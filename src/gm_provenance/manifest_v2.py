"""v2 确定性 Manifest、SM3 摘要和 SM2 签名。

v1 的 ``manifest.py`` 保持原样。本模块使用固定字段顺序、NFC 字符串
规范化及字段名/字段值长度前缀，避免简单字符串拼接的歧义，并保留
``key_id`` 以便验证器通过密钥生命周期模块查找历史公钥。
"""
from __future__ import annotations

import base64
import binascii
import json
import struct
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

from . import manifest as v1_manifest


DOMAIN = b"GM-PROVENANCE-MANIFEST-v2\x00"
SCHEMA_VERSION = "2.0"
SIGNATURE_ALGORITHM = "SM2-SM3-DER"
FIELD_ORDER = (
    "schema_version", "asset_id", "content_id", "version", "publisher_id", "key_id",
    "signature_algorithm", "created_at", "media_type", "content_size", "content_hash_alg",
    "content_hash", "metadata", "metadata_hash", "parent_content_id", "parent_manifest_hash",
    "allowed_transformations", "watermark_profile", "generator", "transformation", "author_claim",
)
REQUIRED_FIELDS = set(FIELD_ORDER[:18])


class ManifestV2Error(ValueError):
    pass


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", str(key)): _normalize(item) for key, item in value.items()}
    return value


def _jcs(value: Any) -> bytes:
    try:
        return v1_manifest._canonical(_normalize(value))
    except Exception as exc:
        raise ManifestV2Error("Manifest JCS 规范化失败") from exc


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ManifestV2Error("created_at 必须是 UTC 秒精度格式") from exc
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_body(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ManifestV2Error("Manifest body 必须是对象")
    normalized = _normalize(dict(body))
    # Accept signer_key_id from v1 callers while emitting the unambiguous v2 key_id.
    if "key_id" not in normalized and "signer_key_id" in normalized:
        normalized["key_id"] = normalized.pop("signer_key_id")
    normalized.setdefault("schema_version", SCHEMA_VERSION)
    normalized.setdefault("signature_algorithm", SIGNATURE_ALGORITHM)
    normalized.setdefault("content_hash_alg", "SM3")
    normalized.setdefault("generator", {})
    normalized.setdefault("transformation", {})
    normalized.setdefault("author_claim", None)
    normalized.setdefault("allowed_transformations", [])
    normalized.setdefault("watermark_profile", {})
    if "metadata_hash" not in normalized:
        raise ManifestV2Error("metadata_hash 必须由调用方按 SM3 计算")
    unknown = set(normalized) - set(FIELD_ORDER)
    if unknown:
        raise ManifestV2Error("Manifest 含未知字段: " + ",".join(sorted(unknown)))
    missing = REQUIRED_FIELDS - set(normalized)
    if missing:
        raise ManifestV2Error("Manifest 缺少字段: " + ",".join(sorted(missing)))
    normalized["created_at"] = _utc_timestamp(normalized["created_at"])
    if normalized["media_type"] != str(normalized["media_type"]).lower():
        raise ManifestV2Error("media_type 必须使用小写")
    if normalized["schema_version"] != SCHEMA_VERSION or normalized["signature_algorithm"] != SIGNATURE_ALGORITHM:
        raise ManifestV2Error("不支持的 v2 协议版本")
    if normalized["content_hash_alg"] != "SM3":
        raise ManifestV2Error("content_hash_alg 必须为 SM3")
    for field, length in (("asset_id", 32), ("content_id", 32), ("key_id", 64), ("content_hash", 64), ("metadata_hash", 64)):
        value = normalized.get(field)
        if not isinstance(value, str) or len(value) != length:
            raise ManifestV2Error(f"{field} 必须为 {length} 位十六进制")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ManifestV2Error(f"{field} 必须为十六进制") from exc
        if value != value.lower():
            raise ManifestV2Error(f"{field} 必须为小写十六进制")
    if type(normalized["version"]) is not int or normalized["version"] < 1:
        raise ManifestV2Error("version 必须为正整数")
    if type(normalized["content_size"]) is not int or normalized["content_size"] < 0:
        raise ManifestV2Error("content_size 必须为非负整数")
    if not isinstance(normalized["publisher_id"], str) or not normalized["publisher_id"]:
        raise ManifestV2Error("publisher_id 不能为空")
    if not isinstance(normalized["metadata"], dict) or not isinstance(normalized["watermark_profile"], dict):
        raise ManifestV2Error("metadata 和 watermark_profile 必须为对象")
    expected_metadata_hash = _sm3_hex(normalized["metadata"], None)
    if expected_metadata_hash != normalized["metadata_hash"]:
        raise ManifestV2Error("metadata_hash 与 metadata 不一致")
    parent, parent_hash = normalized["parent_content_id"], normalized["parent_manifest_hash"]
    if (parent is None) != (parent_hash is None):
        raise ManifestV2Error("父版本字段必须同时为空或同时存在")
    if parent is None and normalized["version"] != 1:
        raise ManifestV2Error("根版本必须为 version=1")
    if parent is not None and (not isinstance(parent, str) or len(parent) != 32 or not isinstance(parent_hash, str) or len(parent_hash) != 64):
        raise ManifestV2Error("父版本引用格式无效")
    return {key: normalized[key] for key in FIELD_ORDER if key in normalized and not (key == "author_claim" and normalized[key] is None)}


def canonical_bytes(body: Mapping[str, Any]) -> bytes:
    """按固定字段顺序编码，每项含名称长度和值长度前缀。"""
    normalized = normalize_body(body)
    fields = [(key, normalized[key]) for key in FIELD_ORDER if key in normalized]
    encoded = bytearray(DOMAIN + struct.pack(">H", len(fields)))
    for key, value in fields:
        name = key.encode("utf-8")
        value_bytes = _jcs(value)
        if len(name) > 0xFFFF:
            raise ManifestV2Error("字段名过长")
        encoded.extend(struct.pack(">H", len(name)))
        encoded.extend(name)
        encoded.extend(struct.pack(">Q", len(value_bytes)))
        encoded.extend(value_bytes)
    return bytes(encoded)


def _sm3_hex(value: Any, crypto: Any | None) -> str:
    raw = _jcs(value)
    if crypto is not None:
        return crypto.sm3(raw)
    # metadata hash is protocol SM3 even when body normalization is being validated
    import hashlib

    try:
        return hashlib.new("sm3", raw).hexdigest()
    except (ValueError, TypeError) as exc:
        raise ManifestV2Error("当前环境缺少 SM3") from exc


def manifest_hash(body: Mapping[str, Any], crypto: Any) -> str:
    return crypto.sm3(canonical_bytes(body))


def sign_manifest(body: Mapping[str, Any], crypto: Any, private_path, passphrase: str) -> dict[str, Any]:
    normalized = normalize_body(body)
    canonical = canonical_bytes(normalized)
    public = crypto._ok(["pkey", "-in", str(private_path), "-pubout", "-passin", "__PASSPHRASE__"], passphrase=passphrase)
    key_id = crypto.key_id(public)
    if key_id != normalized["key_id"]:
        raise ManifestV2Error("key_id 与签名私钥不匹配")
    signature = crypto.sign(DOMAIN + canonical, private_path, passphrase)
    digest = crypto.sm3(canonical)
    return {
        "body": normalized,
        "manifest_hash": digest,
        "manifest_canonical_bytes": base64.b64encode(canonical).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
        "signature_encoding": "base64-DER",
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "public_key_fingerprint": key_id,
    }


def verify_manifest(envelope: Mapping[str, Any], crypto: Any, public_pem: bytes, *, require_active: bool = False) -> dict[str, Any]:
    try:
        body = normalize_body(envelope["body"])
        canonical = canonical_bytes(body)
        supplied_hash = envelope.get("manifest_hash")
        digest = crypto.sm3(canonical)
        signature = base64.b64decode(envelope["signature"], validate=True)
        fingerprint = crypto.key_id(public_pem)
        hash_ok = supplied_hash == digest
        key_ok = fingerprint == body["key_id"] and envelope.get("public_key_fingerprint") == fingerprint
        signature_ok = key_ok and crypto.verify(DOMAIN + canonical, signature, public_pem)
        return {"manifest_hash": digest, "canonical_bytes_length": len(canonical), "hash_valid": hash_ok, "key_valid": key_ok, "signature_valid": signature_ok, "valid": bool(hash_ok and signature_ok)}
    except (KeyError, TypeError, ValueError, binascii.Error, ManifestV2Error):
        return {"manifest_hash": None, "canonical_bytes_length": None, "hash_valid": False, "key_valid": False, "signature_valid": False, "valid": False}


def inspect_manifest(envelope_or_body: Mapping[str, Any], crypto: Any | None = None) -> dict[str, Any]:
    body = envelope_or_body.get("body", envelope_or_body) if isinstance(envelope_or_body, Mapping) else envelope_or_body
    normalized = normalize_body(body)
    canonical = canonical_bytes(normalized)
    digest = crypto.sm3(canonical) if crypto is not None else _sm3_hex(canonical, None)
    return {"fields": normalized, "manifest_canonical_bytes": base64.b64encode(canonical).decode("ascii"), "canonical_bytes_length": len(canonical), "manifest_hash": digest, "signature_present": bool(isinstance(envelope_or_body, Mapping) and envelope_or_body.get("signature")), "public_key_fingerprint": (envelope_or_body.get("public_key_fingerprint") if isinstance(envelope_or_body, Mapping) else normalized.get("key_id"))}
