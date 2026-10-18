"""Application service for the v2 demo.

The service deliberately composes the v1 media detector and cryptographic
backend through adapters.  It keeps the v1 ``ProvenanceService`` untouched and
stores v2 records in a separate SQLite file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .crypto import OpenSSLCrypto
from . import media
from .v2_models import MediaDescriptor, VerificationResult
from .v2_store import V2Store


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class V2Service:
    def __init__(self, root: str | Path = "data-v2", *, crypto: Any | None = None, key_manager: Any | None = None,
                 store: V2Store | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.crypto = crypto or OpenSSLCrypto()
        self.store = store or V2Store(self.root)
        if key_manager is None:
            from .key_manager import KeyManager
            key_manager = KeyManager(self.root, crypto=self.crypto)
        self.key_manager = key_manager

    def audit_service(self):
        from .audit_service import AuditService
        return AuditService(self.root, crypto=self.crypto)

    def audit_events(self) -> list[dict[str, Any]]:
        return self.audit_service().events()

    def _append_audit(self, event_type: str, *, content_id: str | None, actor: str, result: str, details: dict[str, Any]) -> dict[str, Any]:
        event = self.audit_service().append(event_type, content_id=content_id, actor=actor, result=result, details=details)
        # Keep a compact copy in the v2 content store for one-file exports;
        # audit.sqlite remains the authoritative hash-chain source.
        try:
            self.store.append_audit(event)
        except Exception:
            pass
        return event

    def _adapter(self, media_type: str):
        try:
            from .media_adapters import adapter_for
            return adapter_for(b"", media_type)
        except ImportError:
            return None

    def _key(self, publisher_id: str, key_id: str | None = None) -> dict[str, Any]:
        if key_id:
            record = self.key_manager.get(key_id)
            if record is None:
                raise KeyError(key_id)
            return record
        if hasattr(self.key_manager, "active"):
            record = self.key_manager.active(publisher_id)
            if record:
                return record
        if hasattr(self.key_manager, "active_key"):
            record = self.key_manager.active_key(publisher_id)
            if record:
                return record
        raise ValueError("没有可用的活动发布者密钥，请先执行 v2 keygen/key-rotate")

    def _sign(self, key: dict[str, Any], message: bytes, passphrase: str | None = None) -> bytes:
        if hasattr(self.key_manager, "sign"):
            return self.key_manager.sign(key["key_id"], message, passphrase=passphrase)
        private = key.get("private_path")
        if not private or not passphrase:
            raise ValueError("签名需要密钥管理器或私钥口令")
        return self.crypto.sign(message, private, passphrase)

    def _verify_signature(self, key: dict[str, Any], message: bytes, signature: bytes) -> bool:
        if hasattr(self.key_manager, "verify"):
            return bool(self.key_manager.verify(key["key_id"], message, signature))
        return bool(self.crypto.verify(message, signature, key["public_pem"]))

    def _canonical(self, manifest_body: dict[str, Any]) -> bytes:
        from .manifest_service import canonical_manifest
        return canonical_manifest(manifest_body)

    def _descriptor(self, raw: bytes, final: bytes, media_type: str) -> MediaDescriptor:
        adapter = self._adapter(media_type)
        if adapter is not None:
            info = adapter.inspect(raw)
            details = dict(info.details)
            return MediaDescriptor(media_type=media_type, original_sha256=info.sha256, final_sha256=_sha256(final),
                                   size_bytes=len(final), width=details.get("width"), height=details.get("height"),
                                   duration_seconds=details.get("duration_s"), sample_rate=details.get("sample_rate"),
                                   channels=details.get("channels"), frame_rate=details.get("frame_rate"),
                                   codec=details.get("codec"), page_count=details.get("page_count"),
                                   pages=details.get("pages", []))
        info = media.inspect_media(raw)
        return MediaDescriptor(media_type=media_type, original_sha256=_sha256(raw), final_sha256=_sha256(final),
                               size_bytes=len(final), width=info.get("width"), height=info.get("height"),
                               page_count=info.get("page_count"), pages=info.get("pages", []))

    def _embed(self, raw: bytes, content_id: str, media_type: str) -> tuple[bytes, dict[str, Any]]:
        adapter = self._adapter(media_type)
        if adapter is not None:
            operation = adapter.embed(raw, content_id)
            if operation.final_bytes is not None:
                return operation.final_bytes, operation.details.get("profile", operation.details)
            if media_type == "application/pdf":
                embedded, profile = media.embed(raw, content_id, media_type, "watermark-v1")
                return embedded, profile
            raise ValueError("媒体适配器未生成最终编码字节")
        embedded, profile = media.embed(raw, content_id, media_type, "watermark-v1")
        return embedded, profile

    def register_bytes(self, raw: bytes, *, filename: str, publisher_id: str, passphrase: str | None = None,
                       key_id: str | None = None, parent_content_id: str | None = None,
                       allowed_transformations: list[Any] | None = None) -> dict[str, Any]:
        if not isinstance(raw, bytes) or not raw:
            raise ValueError("输入内容不能为空")
        try:
            from .media_adapters import adapter_for
            adapter_info = adapter_for(raw).inspect(raw)
            media_type = str(adapter_info.details.get("mime", adapter_info.media_type))
        except ImportError:
            media_type = media.sniff(raw)
        cid = secrets.token_hex(16)
        final, watermark_profile = self._embed(raw, cid, media_type)
        key = self._key(publisher_id, key_id)
        parent = self.store.get_content(parent_content_id) if parent_content_id else None
        version = int(parent["version"]) + 1 if parent else 1
        asset_id = parent["manifest"].get("asset_id") if parent else secrets.token_hex(16)
        descriptor = self._descriptor(raw, final, media_type)
        metadata = {"filename": filename, "descriptor": descriptor.as_dict()}
        body = {
            "schema_version": "2.0",
            "asset_id": asset_id,
            "content_id": cid,
            "version": version,
            "parent_content_id": parent_content_id,
            "parent_manifest_hash": parent["manifest_hash"] if parent else None,
            "publisher_id": publisher_id,
            "key_id": key["key_id"],
            "signature_algorithm": "SM2-SM3-DER",
            "created_at": _utc(),
            "media_type": media_type,
            "content_size": len(final),
            "content_hash_alg": "SM3",
            "content_hash": self.crypto.sm3(final),
            "metadata": metadata,
            "metadata_hash": self.crypto.sm3(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            "watermark_profile": watermark_profile,
            "allowed_transformations": allowed_transformations or ["jpeg-reencode", "resize", "pdf-page-render"],
        }
        from . import manifest_v2
        canonical = manifest_v2.canonical_bytes(body)
        signing_bytes = manifest_v2.DOMAIN + canonical
        manifest_hash = self.crypto.sm3(canonical)
        signature = self._sign(key, signing_bytes, passphrase)
        row = {
            "content_id": cid, "media_type": media_type, "original_sha256": descriptor.original_sha256,
            "final_sha256": descriptor.final_sha256, "manifest_hash": manifest_hash, "key_id": key["key_id"],
            "version": version, "parent_content_id": parent_content_id, "status": "active", "created_at": body["created_at"],
            "descriptor_json": json.dumps(descriptor.as_dict(), ensure_ascii=False, sort_keys=True),
            "manifest_json": json.dumps(body, ensure_ascii=False, sort_keys=True),
            "signature_b64": base64.b64encode(signature).decode("ascii"), "media": final,
            "manifest": body,
        }
        self.store.put_content(row)
        try:
            self._append_audit("register", content_id=cid, actor=publisher_id, result="PASS", details={"manifest_hash": manifest_hash})
        except ImportError:
            pass
        return {"content_id": cid, "asset_id": asset_id, "version": version, "key_id": key["key_id"],
                "media_type": media_type, "media_bytes": len(final), "original_sha256": descriptor.original_sha256,
                "final_sha256": descriptor.final_sha256, "manifest_hash": manifest_hash,
                "manifest": body, "signature_b64": base64.b64encode(signature).decode("ascii"),
                "media_bytes_value": final}

    def verify_bytes(self, raw: bytes, *, content_id: str | None = None, batch_id: str | None = None,
                     proof: list[str] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        record = self.store.get_content(content_id) if content_id else None
        if record is None and content_id:
            return VerificationResult(content_id, {}, {"status": "NOT_FOUND"}, {"status": "NOT_FOUND"}, {}, {}, {}, "REJECTED", ["ContentID 不存在"]).as_dict()
        if record is None:
            try:
                adapter = self._adapter("application/pdf" if raw.startswith(b"%PDF-") else "")
                if adapter is None:
                    from .media_adapters import adapter_for
                    adapter = adapter_for(raw)
                observed = adapter.detect(raw)
            except Exception as exc:
                observed = {"status": "UNAVAILABLE", "content_ids": [], "details": {"error": str(exc)}}
            ids = observed.get("content_ids", []) if hasattr(observed, "get") else getattr(observed, "recovered_content_ids", [])
            record = self.store.get_content(ids[0]) if len(ids) == 1 else None
            content_id = ids[0] if len(ids) == 1 else None
        if record is None:
            return VerificationResult(content_id, {"status": "NOT_FOUND"}, {"status": "NOT_CHECKED"}, {"status": "NOT_FOUND"}, {}, {}, {}, "REJECTED", ["未找到唯一 ContentID"]).as_dict()
        expected = record["manifest"]["content_hash"]
        observed_sha = self.crypto.sm3(raw)
        sm3_result = {"status": "PASS" if observed_sha == expected else "FAIL", "expected": expected, "observed": observed_sha, "algorithm": "SM3"}
        adapter = self._adapter(record["media_type"])
        watermark = adapter.detect(raw, content_id) if adapter is not None else media.detect(raw, record["media_type"])
        key = self._key(record["manifest"]["publisher_id"], record["key_id"])
        from . import manifest_v2
        canonical = manifest_v2.canonical_bytes(record["manifest"])
        signature = base64.b64decode(record["signature_b64"])
        signature_ok = self._verify_signature(key, manifest_v2.DOMAIN + canonical, signature)
        manifest_result = {"status": "PASS", "manifest_hash": record["manifest_hash"], "canonical_bytes": len(canonical), "key_id": record["key_id"]}
        merkle_result: dict[str, Any] = {"status": "NOT_CHECKED"}
        if batch_id and proof is not None:
            from .batch_proof import verify_proof
            batch = self.store.get_batch(batch_id)
            merkle_result = verify_proof(record["manifest_hash"], proof, batch["merkle_root"] if batch else None)
        key_status = str(key.get("status", "active"))
        cryptographic_ok = sm3_result["status"] == "PASS" and signature_ok and watermark.get("status") == "FOUND"
        if cryptographic_ok and key_status == "active":
            decision = "VERIFIED"
        elif cryptographic_ok and key_status in {"revoked", "expired"}:
            decision = "RISK_REVOKED_KEY"
        elif watermark.get("status") == "FOUND":
            decision = "ORIGIN_HINT_ONLY"
        else:
            decision = "REJECTED"
        result = VerificationResult(content_id, watermark, sm3_result, manifest_result,
                                     {"status": "PASS" if signature_ok else "FAIL", "algorithm": "SM2-SM3-DER-v2"},
                                     merkle_result,
                                     {"status": "PASS" if record["version"] >= 1 else "FAIL", "version": record["version"], "parent_content_id": record["manifest"].get("parent_content_id")},
                                     {"status": "PASS" if key_status == "active" else "FAIL", "key_status": key_status}, decision,
                                     [] if decision == "VERIFIED" else (["签名密钥已吊销或过期，历史签名保留但当前凭证标记风险"] if key_status != "active" else ["字节摘要、签名或水印校验未全部通过"]),
                                     round((time.perf_counter() - started) * 1000, 3))
        try:
            self._append_audit("verify", content_id=content_id, actor="verifier", result=decision, details=result.as_dict())
        except ImportError:
            pass
        return result.as_dict()

    def create_batch(self, content_ids: list[str], *, publisher_id: str, passphrase: str | None = None,
                     key_id: str | None = None) -> dict[str, Any]:
        from .batch_proof import BatchProofService, BATCH_DOMAIN, canonical_bytes
        records = [self.store.get_content(cid) for cid in content_ids]
        if any(x is None for x in records):
            raise KeyError("批量中存在未知 ContentID")
        key = self._key(publisher_id, key_id)
        batch = BatchProofService(crypto=self.crypto).create_batch([x["manifest_hash"] for x in records if x], publisher_id)
        signing_descriptor = {"leaf_count": batch["leaf_count"], "merkle_root": batch["merkle_root"], "created_at": batch["created_at"], "publisher_id": publisher_id, "signer_key_id": key["key_id"], "protocol": batch.get("protocol", "GM-PROVENANCE-BATCH-v2")}
        signature = self._sign(key, BATCH_DOMAIN + canonical_bytes(signing_descriptor), passphrase)
        leaves = [x["manifest_hash"] for x in records if x]
        signature_b64 = base64.b64encode(signature).decode("ascii")
        row = {**batch, "key_id": key["key_id"], "signer_key_id": key["key_id"], "signature": signature_b64, "created_at": batch["created_at"], "signature_b64": signature_b64, "manifest": {"content_ids": content_ids, "leaves": leaves, "merkle_root": batch["merkle_root"]}, "tree_height": batch["tree_height"]}
        self.store.put_batch(row)
        return {k: v for k, v in row.items() if k != "signing_bytes"}
