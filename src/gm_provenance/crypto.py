"""真实 OpenSSL SM2/SM3 适配器；传入消息而非预先计算的摘要。"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

DEFAULT_USER_ID = "1234567812345678"
SM2_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a811ccf5501822d03420004")


class CryptoError(RuntimeError):
    """密码操作失败，不包含口令或底层命令内容。"""


class CryptoEnvironmentError(CryptoError):
    """密码后端不可用或外部调用异常。"""


class InvalidKeyError(CryptoError, ValueError):
    """输入不是本协议支持的有效 SM2 SPKI 公钥。"""


class OpenSSLCrypto:
    def __init__(self, binary=None):
        candidate = binary or os.environ.get("GM_OPENSSL") or shutil.which("openssl")
        if candidate is None and Path("C:/msys64/ucrt64/bin/openssl.exe").is_file():
            candidate = "C:/msys64/ucrt64/bin/openssl.exe"
        if candidate is None:
            raise CryptoEnvironmentError("未找到 OpenSSL，请安装或设置 GM_OPENSSL")
        self.binary = str(candidate)

    def _run(self, args, data=None, passphrase=None):
        env = os.environ.copy()
        if passphrase is not None:
            if not isinstance(passphrase, str) or not passphrase or any(c in passphrase for c in "\x00\r\n"):
                raise CryptoError("口令不能为空或包含 NUL/换行")
            # 口令仅位于子进程环境；不进入 argv、shell 或临时文件。
            variable = "GM_PASS_" + secrets.token_hex(12)
            env[variable] = passphrase
            args = ["env:" + variable if x == "__PASSPHRASE__" else x for x in args]
        try:
            return subprocess.run([self.binary, *args], input=data, capture_output=True,
                                  env=env, timeout=30, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CryptoEnvironmentError("OpenSSL 不可执行或操作超时") from None

    def _ok(self, args, data=None, passphrase=None):
        result = self._run(args, data, passphrase)
        if result.returncode != 0:
            raise CryptoError("OpenSSL 操作失败（密钥、口令、算法或环境错误）")
        return result.stdout

    def sm3(self, data: bytes) -> str:
        if not isinstance(data, bytes):
            raise TypeError("SM3 输入必须是 bytes")
        digest = self._ok(["dgst", "-sm3", "-binary"], data)
        if len(digest) != 32:
            raise CryptoEnvironmentError("OpenSSL 返回异常 SM3 长度")
        return digest.hex()

    def public_der(self, public_pem: bytes) -> bytes:
        if not isinstance(public_pem, bytes) or len(public_pem) > 4096:
            raise InvalidKeyError("公钥必须是有限大小的 SPKI PEM")
        if not public_pem.strip().startswith(b"-----BEGIN PUBLIC KEY-----"):
            raise InvalidKeyError("公钥必须是 SPKI PEM")
        result = self._run(["pkey", "-pubin", "-pubout", "-outform", "DER"], public_pem)
        if result.returncode != 0:
            raise InvalidKeyError("公钥无法解析")
        der = result.stdout
        if len(der) != 91 or not der.startswith(SM2_SPKI_PREFIX):
            raise InvalidKeyError("公钥不是 SM2 命名曲线的未压缩 SPKI")
        check = self._run(["pkey", "-pubin", "-pubcheck", "-noout"], public_pem)
        if check.returncode != 0:
            raise InvalidKeyError("SM2 公钥点无效")
        return der

    def key_id(self, public_pem: bytes) -> str:
        return self.sm3(self.public_der(public_pem))

    def generate_key(self, private_path, public_path, passphrase: str) -> str:
        private_path, public_path = Path(private_path), Path(public_path)
        if private_path.resolve() == public_path.resolve() or private_path.exists() or public_path.exists():
            raise CryptoError("密钥输出必须是两个不存在的新文件")
        encrypted = self._ok(["genpkey", "-algorithm", "SM2", "-aes-256-cbc", "-pass", "__PASSPHRASE__"], passphrase=passphrase)
        public = self._ok(["pkey", "-pubout", "-passin", "__PASSPHRASE__"], encrypted, passphrase)
        key_id = self.key_id(public)
        created = []
        try:
            for path, contents in ((private_path, encrypted), (public_path, public)):
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                with os.fdopen(fd, "wb") as file:
                    file.write(contents)
        except OSError:
            for path in created:
                path.unlink(missing_ok=True)
            raise CryptoError("密钥文件写入失败") from None
        return key_id

    @staticmethod
    def _user_id(user_id):
        if not isinstance(user_id, str) or not user_id or not user_id.isascii() or any(ord(c) < 32 for c in user_id) or len(user_id) > 8191:
            raise CryptoError("SM2 用户标识须为 1 至 8191 字节可打印 ASCII")
        return "distid:" + user_id

    def sign(self, message: bytes, private_path, passphrase: str, user_id=DEFAULT_USER_ID) -> bytes:
        public = self._ok(["pkey", "-in", str(Path(private_path).resolve()), "-pubout", "-passin", "__PASSPHRASE__"], passphrase=passphrase)
        self.public_der(public)
        return self._ok(["pkeyutl", "-sign", "-rawin", "-digest", "sm3", "-pkeyopt", self._user_id(user_id),
                         "-inkey", str(Path(private_path).resolve()), "-passin", "__PASSPHRASE__"], message, passphrase)

    def verify(self, message: bytes, signature: bytes, public_pem: bytes, user_id=DEFAULT_USER_ID) -> bool:
        self.public_der(public_pem)
        if not isinstance(signature, bytes) or not (8 <= len(signature) <= 80):
            return False
        temp_root = Path.cwd() / "data" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gm-verify-", dir=temp_root) as directory:
            pub, sig = Path(directory) / "public.pem", Path(directory) / "signature.der"
            pub.write_bytes(public_pem)
            sig.write_bytes(signature)
            result = self._run(["pkeyutl", "-verify", "-rawin", "-digest", "sm3", "-pkeyopt", self._user_id(user_id),
                                "-pubin", "-inkey", str(pub.resolve()), "-sigfile", str(sig.resolve())], message)
        if result.returncode == 0:
            return True
        # OpenSSL 验证不匹配及畸形 DER 均输出 Verification failure；初始化错误抛异常。
        if result.returncode == 1 and b"Signature Verification Failure" in result.stdout:
            return False
        raise CryptoEnvironmentError("OpenSSL 验证操作未正常完成")

    def doctor(self) -> dict:
        version = self._ok(["version"]).decode("utf-8", errors="replace").strip()
        sm3_ok = self.sm3(b"abc") == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
        temp_root = Path.cwd() / "data" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gm-doctor-", dir=temp_root) as directory:
            private, public = Path(directory) / "key.pem", Path(directory) / "public.pem"
            password = secrets.token_urlsafe(32)
            self.generate_key(private, public, password)
            signature = self.sign(b"gm-provenance doctor", private, password)
            sm2_ok = self.verify(b"gm-provenance doctor", signature, public.read_bytes())
        return {"backend": "OpenSSL CLI", "version": version, "sm3_known_answer": sm3_ok,
                "sm2_roundtrip": sm2_ok, "sm2_user_id": DEFAULT_USER_ID,
                "signature_encoding": "DER", "process_startup_included": True}
