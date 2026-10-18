"""v2 发布者密钥生命周期管理。

该模块独立于 v1 ``Registry`` 的 ``keys`` 表，保存公钥元数据和撤销状态；
私钥由调用方指定的受保护文件保存，绝不写入 SQLite、日志或返回值。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
ALGORITHM = "SM2-SM3-DER"
ACTIVE = "active"
REVOKED = "revoked"
EXPIRED = "expired"


class KeyManagerError(RuntimeError):
    """密钥生命周期或持久化错误。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(UTC_FORMAT)


def parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("时间必须为字符串")
    try:
        parsed = datetime.strptime(value, UTC_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("时间必须为 UTC 秒精度格式") from exc
    return parsed


def _normal_time(value: str | None, *, default: str | None = None) -> str | None:
    if value is None:
        value = default
    if value is None:
        return None
    return parse_time(value).strftime(UTC_FORMAT)


class KeyManager:
    """在 SQLite 中登记公钥，并管理轮换、过期和吊销。

    ``root`` 只保存 ``key_manager.sqlite``；``keygen`` 生成的私钥路径由
    调用方控制。数据库中的 ``public_pem`` 用于离线验证历史 Manifest。
    """

    def __init__(self, root: Path, crypto: Any | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "key_manager.sqlite"
        self.crypto = crypto
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS publisher_keys (
                    key_id TEXT PRIMARY KEY,
                    algorithm TEXT NOT NULL,
                    publisher_id TEXT NOT NULL,
                    public_pem BLOB NOT NULL,
                    public_key_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active','revoked','expired')),
                    predecessor_key_id TEXT,
                    private_path TEXT,
                    revoked_at TEXT,
                    revocation_reason TEXT,
                    FOREIGN KEY(predecessor_key_id) REFERENCES publisher_keys(key_id)
                );
                CREATE INDEX IF NOT EXISTS idx_publisher_keys_publisher
                    ON publisher_keys(publisher_id, created_at);
                CREATE TABLE IF NOT EXISTS key_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    details TEXT NOT NULL,
                    FOREIGN KEY(key_id) REFERENCES publisher_keys(key_id)
                );
                """
            )
            # Workspaces created by an earlier v2 preview may already have the
            # table.  The path is metadata only; private key bytes never enter
            # SQLite.  Keep this migration idempotent for demo upgrades.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(publisher_keys)").fetchall()}
            if "private_path" not in columns:
                conn.execute("ALTER TABLE publisher_keys ADD COLUMN private_path TEXT")

    def _require_crypto(self):
        if self.crypto is None:
            from .crypto import OpenSSLCrypto

            self.crypto = OpenSSLCrypto()
        return self.crypto

    @staticmethod
    def _public_bytes(public: bytes | bytearray | str | Path) -> bytes:
        if isinstance(public, (str, Path)):
            return Path(public).read_bytes()
        if isinstance(public, (bytes, bytearray)):
            return bytes(public)
        raise TypeError("公钥必须为 PEM 字节或文件路径")

    @staticmethod
    def _record(row: sqlite3.Row, *, now: str | None = None) -> dict[str, Any]:
        now_dt = parse_time(now or utc_now())
        expires = row["expires_at"]
        effective = row["status"]
        if effective == ACTIVE and expires is not None and parse_time(expires) <= now_dt:
            effective = EXPIRED
        return {
            "key_id": row["key_id"],
            "algorithm": row["algorithm"],
            "publisher_id": row["publisher_id"],
            "public_pem": bytes(row["public_pem"]),
            "public_key_fingerprint": row["public_key_fingerprint"],
            "created_at": row["created_at"],
            "valid_from": row["valid_from"],
            "expires_at": expires,
            "status": effective,
            "stored_status": row["status"],
            "predecessor_key_id": row["predecessor_key_id"],
            "private_path": row["private_path"],
            "revoked_at": row["revoked_at"],
            "revocation_reason": row["revocation_reason"],
        }

    def _insert_key(
        self,
        public_pem: bytes,
        publisher_id: str,
        valid_from: str | None,
        expires_at: str | None,
        predecessor_key_id: str | None,
        private_path: Path | None = None,
    ) -> dict[str, Any]:
        if not isinstance(publisher_id, str) or not publisher_id.strip():
            raise ValueError("publisher_id 不能为空")
        crypto = self._require_crypto()
        # public_der performs strict SM2 SPKI validation before key_id.
        crypto.public_der(public_pem)
        key_id = crypto.key_id(public_pem)
        now = utc_now()
        valid = _normal_time(valid_from, default=now)
        expires = _normal_time(expires_at)
        if parse_time(valid) < parse_time(now) - timedelta(days=365 * 100):
            raise ValueError("valid_from 超出支持范围")
        if expires is not None and parse_time(expires) <= parse_time(valid):
            raise ValueError("expires_at 必须晚于 valid_from")
        if predecessor_key_id is not None:
            predecessor_key_id = str(predecessor_key_id)
            with self._connect() as conn:
                if conn.execute("SELECT 1 FROM publisher_keys WHERE key_id=?", (predecessor_key_id,)).fetchone() is None:
                    raise KeyError(predecessor_key_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM publisher_keys WHERE key_id=?", (key_id,)).fetchone()
            if old is not None:
                if bytes(old["public_pem"]) != public_pem or old["publisher_id"] != publisher_id:
                    conn.execute("ROLLBACK")
                    raise KeyManagerError("key_id 已存在且公钥或发布者不一致")
                conn.execute("COMMIT")
                return self._record(old)
            conn.execute(
                "INSERT INTO publisher_keys(key_id,algorithm,publisher_id,public_pem,public_key_fingerprint,created_at,valid_from,expires_at,status,predecessor_key_id,private_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key_id, ALGORITHM, publisher_id, sqlite3.Binary(public_pem), key_id, now, valid, expires, ACTIVE, predecessor_key_id, str(Path(private_path).resolve()) if private_path else None),
            )
            conn.execute("INSERT INTO key_events(key_id,event_type,event_at,details) VALUES(?,?,?,?)", (key_id, "import", now, "public key metadata imported"))
            conn.execute("COMMIT")
            row = conn.execute("SELECT * FROM publisher_keys WHERE key_id=?", (key_id,)).fetchone()
        return self._record(row)

    def import_key(
        self,
        public: bytes | bytearray | str | Path,
        publisher_id: str,
        *,
        valid_from: str | None = None,
        expires_at: str | None = None,
        predecessor_key_id: str | None = None,
        private_path: Path | None = None,
    ) -> dict[str, Any]:
        """导入并登记公钥；不读取或保存对应私钥。"""
        return self._insert_key(self._public_bytes(public), publisher_id, valid_from, expires_at, predecessor_key_id, private_path=private_path)

    def keygen(
        self,
        private_path: Path,
        public_path: Path,
        passphrase: str,
        publisher_id: str,
        *,
        valid_from: str | None = None,
        expires_at: str | None = None,
        predecessor_key_id: str | None = None,
    ) -> dict[str, Any]:
        """生成 SM2 密钥并仅登记公钥。私钥必须由调用方安全保管。"""
        crypto = self._require_crypto()
        private_path, public_path = Path(private_path), Path(public_path)
        key_id = crypto.generate_key(private_path, public_path, passphrase)
        try:
            result = self._insert_key(public_path.read_bytes(), publisher_id, valid_from, expires_at, predecessor_key_id, private_path=private_path)
        except Exception:
            # Do not leave a half-registered public key; generated private key is
            # intentionally left for the caller to handle securely.
            raise
        if result["key_id"] != key_id:
            raise KeyManagerError("生成密钥的 key_id 与公钥登记不一致")
        return result

    def generate(
        self,
        *,
        publisher_id: str,
        passphrase: str,
        key_id: str | None = None,
        private_path: Path | None = None,
        public_path: Path | None = None,
        valid_from: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """CLI 友好的 keygen 封装。

        未指定路径时把密钥放在运行时 ``root/private`` 目录；该目录仅应
        放在本地数据目录，提交打包程序必须排除它。``key_id`` 是派生值，
        如传入则只用于校验，不能由调用方伪造。
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        private_dir = self.root / "private"
        private_dir.mkdir(parents=True, exist_ok=True)
        private_path = Path(private_path) if private_path else private_dir / f"{publisher_id}-{stamp}.private.pem"
        public_path = Path(public_path) if public_path else self.root / f"{publisher_id}-{stamp}.public.pem"
        result = self.keygen(private_path, public_path, passphrase, publisher_id, valid_from=valid_from, expires_at=expires_at)
        if key_id is not None and str(key_id) != result["key_id"]:
            raise ValueError("key_id 是由公钥派生的指纹，不能指定为其他值")
        return result

    def rotate(
        self,
        predecessor_key_id: str | None = None,
        private_path: Path | None = None,
        public_path: Path | None = None,
        passphrase: str | None = None,
        *,
        publisher_id: str | None = None,
        predecessor_key_id_kw: str | None = None,
        valid_from: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        # ``predecessor_key_id_kw`` accommodates callers that pass the readable
        # keyword while preserving the original positional API.
        predecessor_key_id = predecessor_key_id or predecessor_key_id_kw
        if predecessor_key_id is None and publisher_id:
            active = self.active(publisher_id)
            predecessor_key_id = active["key_id"] if active else None
        if predecessor_key_id is None or passphrase is None:
            raise ValueError("轮换需要 predecessor_key_id/publisher_id 和 passphrase")
        old = self.get(predecessor_key_id)
        if old is None:
            raise KeyError(predecessor_key_id)
        if private_path is None or public_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            private_dir = self.root / "private"; private_dir.mkdir(parents=True, exist_ok=True)
            private_path = private_path or private_dir / f"{old['publisher_id']}-{stamp}.private.pem"
            public_path = public_path or self.root / f"{old['publisher_id']}-{stamp}.public.pem"
        return self.keygen(private_path, public_path, passphrase, old["publisher_id"], valid_from=valid_from, expires_at=expires_at, predecessor_key_id=predecessor_key_id)

    def import_public(self, public: bytes | bytearray | str | Path, publisher_id: str, **kwargs: Any) -> dict[str, Any]:
        """语义更明确的导入别名，保留 ``import_key`` 兼容。"""
        return self.import_key(public, publisher_id, **kwargs)

    def active(self, publisher_id: str, *, at: str | None = None) -> dict[str, Any] | None:
        candidates = [row for row in self.list(publisher_id, at=at) if row["status"] == ACTIVE]
        return candidates[-1] if candidates else None

    def active_key(self, publisher_id: str, *, at: str | None = None) -> dict[str, Any] | None:
        return self.active(publisher_id, at=at)

    def sign(self, key_id: str, message: bytes, *, passphrase: str | None = None) -> bytes:
        record = self.get(key_id)
        if record is None:
            raise KeyError(key_id)
        if not self.is_usable(key_id):
            raise KeyManagerError("密钥当前未生效、已过期或已吊销")
        private_path = record.get("private_path")
        if not private_path:
            raise KeyManagerError("该公钥没有可用的私钥路径；请在生成/导入时提供 private_path")
        if not passphrase:
            raise KeyManagerError("签名需要私钥口令")
        return self._require_crypto().sign(message, private_path, passphrase)

    def verify(self, key_id: str, message: bytes, signature: bytes, *, allow_revoked: bool = True) -> bool:
        record = self.get(key_id)
        if record is None or (not allow_revoked and record["status"] != ACTIVE):
            return False
        return bool(self._require_crypto().verify(message, signature, record["public_pem"]))

    def get(self, key_id: str, *, at: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM publisher_keys WHERE key_id=?", (str(key_id),)).fetchone()
        return self._record(row, now=at) if row else None

    def list(self, publisher_id: str | None = None, *, at: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            # Timestamps are deliberately second precision for canonical
            # manifests; ``rowid`` breaks ties so a rotation in the same UTC
            # second is still the newest active key.
            rows = conn.execute("SELECT * FROM publisher_keys WHERE (? IS NULL OR publisher_id=?) ORDER BY created_at,rowid", (publisher_id, publisher_id)).fetchall()
        return [self._record(row, now=at) for row in rows]

    def revoke(self, key_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("吊销原因不能为空")
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM publisher_keys WHERE key_id=?", (str(key_id),)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(key_id)
            if row["status"] != REVOKED:
                conn.execute("UPDATE publisher_keys SET status=?,revoked_at=?,revocation_reason=? WHERE key_id=?", (REVOKED, now, reason.strip(), str(key_id)))
                conn.execute("INSERT INTO key_events(key_id,event_type,event_at,details) VALUES(?,?,?,?)", (str(key_id), "revoke", now, reason.strip()))
            conn.execute("COMMIT")
        return self.get(str(key_id)) or {}

    def status(self, key_id: str | None = None, *, at: str | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self.get(key_id, at=at) if key_id is not None else self.list(at=at)

    @staticmethod
    def sanitized(record: dict[str, Any] | None) -> dict[str, Any] | None:
        """返回可写入日志/API 的状态视图。

        公钥原文和私钥路径都从状态输出移除；验证器仍可通过
        :meth:`public_key` 获取公钥，签名器通过内部记录读取私钥路径。
        """
        if record is None:
            return None
        return {key: value for key, value in record.items() if key not in ("public_pem", "private_path")}

    def public_status(self, key_id: str | None = None, *, at: str | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
        status = self.status(key_id, at=at)
        if isinstance(status, list):
            return [self.sanitized(item) for item in status]
        return self.sanitized(status)

    def is_usable(self, key_id: str, *, at: str | None = None) -> bool:
        record = self.get(key_id, at=at)
        if record is None or record["status"] != ACTIVE:
            return False
        instant = parse_time(at or utc_now())
        return parse_time(record["valid_from"]) <= instant and (record["expires_at"] is None or instant < parse_time(record["expires_at"]))

    def public_key(self, key_id: str, *, require_active: bool = False) -> bytes:
        record = self.get(key_id)
        if record is None or (require_active and not self.is_usable(key_id)):
            raise KeyManagerError("密钥不存在或当前不可用")
        return record["public_pem"]
