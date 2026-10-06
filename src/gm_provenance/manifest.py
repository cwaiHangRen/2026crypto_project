"""GM Provenance 1.0 严格结构及 RFC 8785 序列化。"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import re

try:
    import rfc8785  # optional maintained implementation
except ImportError:  # pragma: no cover - offline runtime fallback
    import re as _re

    def _fallback_number(value):
        if isinstance(value, int):
            return str(value)
        if not math.isfinite(value):
            raise ValueError("non-finite")
        if value == 0:
            return "0"
        # Python's shortest-roundtrip spelling is close to ECMAScript; normalize
        # exponent padding and integral decimal values for JCS common cases.
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        magnitude = abs(value)
        if "e" in text.lower() and 1e-6 <= magnitude < 1e21:
            text = format(Decimal(str(value)), "f").rstrip("0").rstrip(".")
            if text in ("", "-0"):
                text = "0"
        if "e" not in text and "E" not in text and text.endswith(".0"):
            text = text[:-2]
        text = _re.sub(r"([eE][+-])0+(\d+)$", r"\1\2", text)
        text = text.replace("E", "e")
        return text

    def _fallback_jcs(value):
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _fallback_number(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, list):
            return "[" + ",".join(_fallback_jcs(item) for item in value) + "]"
        if isinstance(value, dict):
            # RFC 8785 sorts by UTF-16 code units, unlike Python's code points.
            keys = sorted(value, key=lambda key: key.encode("utf-16-be", "surrogatepass"))
            return "{" + ",".join(_fallback_jcs(str(key)) + ":" + _fallback_jcs(value[key]) for key in keys) + "}"
        raise TypeError("unsupported JSON value")

    class _RFC8785:
        @staticmethod
        def dumps(value):
            return _fallback_jcs(value).encode("utf-8")
    rfc8785 = _RFC8785()

from .crypto import DEFAULT_USER_ID

DOMAIN = b"GM-PROVENANCE-MANIFEST-v1\x00"
MAX_BYTES = 1_048_576
MAX_SAFE_INTEGER = 2**53 - 1
REQUIRED = set("schema_version asset_id content_id version publisher_id signer_key_id generator created_at media_type content_size content_hash_alg content_hash metadata metadata_hash parent_content_id parent_manifest_hash transformation allowed_transformations watermark_profile signature_suite sm2_user_id".split())
OPTIONAL = {"author_claim"}


class ManifestError(ValueError):
    pass


def _resources(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 16 or count > 10000:
            raise ManifestError("JSON 深度或节点数超过限制")
        if isinstance(item, dict):
            if len(item) > 256 or any(not isinstance(k, str) for k in item):
                raise ManifestError("对象字段过多或键不是字符串")
            stack.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif isinstance(item, list):
            if len(item) > 1024:
                raise ManifestError("数组超过限制")
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            try:
                size = len(item.encode("utf-8", errors="strict"))
            except UnicodeError:
                raise ManifestError("非法 Unicode") from None
            if size > 65536:
                raise ManifestError("字符串超过限制")
        elif item is None or isinstance(item, bool):
            pass
        elif isinstance(item, int):
            if abs(item) > MAX_SAFE_INTEGER:
                raise ManifestError("整数超过 IEEE754 安全范围")
        elif isinstance(item, float):
            if not math.isfinite(item) or abs(item) > MAX_SAFE_INTEGER:
                raise ManifestError("浮点值非有限或超过协议范围")
        else:
            raise ManifestError("不是受支持的 JSON 值")


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ManifestError("重复 JSON 键")
        out[key] = value
    return out


def _constant(value):
    raise ManifestError("JSON 不允许 NaN 或 Infinity")


def loads(data: bytes | str) -> dict:
    try:
        if isinstance(data, bytes):
            if len(data) > MAX_BYTES:
                raise ManifestError("Manifest 超过 1 MiB")
            data = data.decode("utf-8", errors="strict")
        if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_BYTES:
            raise ManifestError("Manifest 必须是有限大小 UTF-8 JSON")
        obj = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
        _resources(obj)
        if not isinstance(obj, dict):
            raise ManifestError("JSON 根必须是对象")
        return obj
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise ManifestError("无法解析严格 JSON") from None


def _canonical(value):
    _resources(value)
    try:
        encoded = rfc8785.dumps(value)
    except (ValueError, TypeError, RecursionError):
        raise ManifestError("JCS 规范化失败") from None
    if len(encoded) > MAX_BYTES:
        raise ManifestError("Manifest 超过 1 MiB")
    return encoded


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def validate_body(body) -> None:
    _resources(body)
    if not isinstance(body, dict) or not REQUIRED <= body.keys() or body.keys() - REQUIRED - OPTIONAL:
        raise ManifestError("Manifest 字段缺失或包含未知字段")
    for key, expected in (("schema_version", "1.0"), ("content_hash_alg", "SM3"), ("signature_suite", "SM2-SM3-DER"), ("sm2_user_id", DEFAULT_USER_ID)):
        if body[key] != expected:
            raise ManifestError("不支持的协议参数：" + key)
    for key in ("asset_id", "content_id"):
        if not _hex(body[key], 32):
            raise ManifestError(key + " 必须是 128 位小写十六进制")
    for key in ("content_hash", "metadata_hash", "signer_key_id"):
        if not _hex(body[key], 64):
            raise ManifestError(key + " 必须是 SM3 十六进制")
    for key, minimum in (("version", 1), ("content_size", 0)):
        if type(body[key]) is not int or not minimum <= body[key] <= MAX_SAFE_INTEGER:
            raise ManifestError(key + " 超出整数范围")
    for key in ("publisher_id", "media_type"):
        if not isinstance(body[key], str) or not body[key] or len(body[key]) > 256 or any(ord(c) < 32 for c in body[key]):
            raise ManifestError(key + " 字符串无效")
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", body["media_type"]):
        raise ManifestError("media_type 必须为无参数的小写 MIME")
    if "author_claim" in body and (not isinstance(body["author_claim"], str) or len(body["author_claim"]) > 1024):
        raise ManifestError("author_claim 必须是有限大小字符串")
    timestamp = body["created_at"]
    if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        raise ManifestError("created_at 必须是 UTC 秒精度格式")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ManifestError("created_at 日期无效") from None
    for key in ("generator", "metadata", "transformation", "watermark_profile"):
        if not isinstance(body[key], dict):
            raise ManifestError(key + " 必须是对象")
    if not isinstance(body["allowed_transformations"], list):
        raise ManifestError("allowed_transformations 必须是数组")
    # 授权规则仅允许操作名或具名参数对象，解释由服务层负责。
    for rule in body["allowed_transformations"]:
        if not isinstance(rule, (str, dict)) or not rule:
            raise ManifestError("变换规则必须是非空操作名或对象")
    parent, parent_hash = body["parent_content_id"], body["parent_manifest_hash"]
    if parent is None or parent_hash is None:
        if parent is not None or parent_hash is not None or body["version"] != 1:
            raise ManifestError("根版本必须成对 null 且版本号为 1")
    elif not _hex(parent, 32) or not _hex(parent_hash, 64) or body["version"] < 2 or parent == body["content_id"]:
        raise ManifestError("父版本引用不合法")


def canonical_body(body) -> bytes:
    validate_body(body)
    return _canonical(body)


def manifest_hash(body, crypto) -> str:
    return crypto.sm3(DOMAIN + canonical_body(body))


def _envelope(envelope):
    _resources(envelope)
    if not isinstance(envelope, dict) or set(envelope) != {"body", "signature", "signature_encoding"}:
        raise ManifestError("签名 envelope 字段无效")
    validate_body(envelope["body"])
    if envelope["signature_encoding"] != "base64-DER" or not isinstance(envelope["signature"], str):
        raise ManifestError("不支持的签名编码")
    try:
        sig = base64.b64decode(envelope["signature"], validate=True)
    except (ValueError, binascii.Error):
        raise ManifestError("签名不是规范 base64") from None
    if not 8 <= len(sig) <= 80 or base64.b64encode(sig).decode("ascii") != envelope["signature"]:
        raise ManifestError("签名长度或 base64 编码不规范")
    return sig


def dumps(envelope) -> bytes:
    _envelope(envelope)
    return _canonical(envelope)


def sign_manifest(body, crypto, private_path, passphrase) -> dict:
    canonical = canonical_body(body)
    if crypto.sm3(_canonical(body["metadata"])) != body["metadata_hash"]:
        raise ManifestError("metadata_hash 与元数据不符")
    # 签名者 ID 与实际私钥对应的公钥一致；防止生成不可验证凭证。
    public = crypto._ok(["pkey", "-in", str(Path(private_path).resolve()), "-pubout", "-passin", "__PASSPHRASE__"], passphrase=passphrase)
    if crypto.key_id(public) != body["signer_key_id"]:
        raise ManifestError("signer_key_id 与签名密钥不符")
    sig = crypto.sign(DOMAIN + canonical, private_path, passphrase, body["sm2_user_id"])
    return {"body": loads(canonical), "signature": base64.b64encode(sig).decode("ascii"), "signature_encoding": "base64-DER"}


def verify_manifest(envelope, crypto, public_pem) -> bool:
    signature = _envelope(envelope)
    body = envelope["body"]
    if crypto.key_id(public_pem) != body["signer_key_id"] or crypto.sm3(_canonical(body["metadata"])) != body["metadata_hash"]:
        return False
    return crypto.verify(DOMAIN + canonical_body(body), signature, public_pem, body["sm2_user_id"])
