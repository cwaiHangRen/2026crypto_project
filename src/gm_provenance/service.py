"""发布和验证应用服务；CLI、API 和演示界面共享此层。"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .crypto import OpenSSLCrypto, DEFAULT_USER_ID
from . import manifest, media
from .registry import Registry
from . import state


def _utc_seconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProvenanceService:
    def __init__(self, root: str | Path = "data", crypto: OpenSSLCrypto | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.crypto = crypto or OpenSSLCrypto()
        self.registry = Registry(self.root)

    def doctor(self) -> dict[str, Any]:
        result = self.crypto.doctor()
        result.update({"registry": str(self.registry.db_path), "media_limits": {"bytes": media.MAX_BYTES, "pdf_pages": media.MAX_PAGES}})
        try:
            import numpy, PIL
            result["image_backend"] = {"numpy": numpy.__version__, "pillow": PIL.__version__}
        except Exception as exc:
            result["image_backend_error"] = str(exc)
        return result

    def keygen(self, private_path: str | Path, public_path: str | Path, passphrase: str) -> dict[str, str]:
        key_id = self.crypto.generate_key(private_path, public_path, passphrase)
        public = Path(public_path).read_bytes()
        self.registry.add_key(public, "publisher-demo-001", True, self.crypto)
        return {"key_id": key_id, "private_path": str(Path(private_path).resolve()), "public_path": str(Path(public_path).resolve())}

    def register(self, source_path: str | Path, private_path: str | Path, passphrase: str,
                 publisher_id: str = "publisher-demo-001", asset_id: str | None = None,
                 parent_content_id: str | None = None) -> dict[str, Any]:
        source = Path(source_path)
        original = source.read_bytes()
        media_type = media.sniff(original)
        content_id = secrets.token_hex(16)
        asset_id = asset_id or secrets.token_hex(16)
        final_bytes, profile = media.embed(original, content_id, media_type)
        public = self._public_from_private(private_path, passphrase)
        key_id = self.crypto.key_id(public)
        if key_id in self.registry.status().get("revoked_keys", []):
            raise PermissionError("签名密钥已撤销，不能继续登记")
        self.registry.add_key(public, publisher_id, True, self.crypto)
        version = 1
        parent_hash = None
        if parent_content_id:
            parent = self.registry.get_manifest(parent_content_id)
            if not parent:
                raise ValueError("父 ContentID 未登记")
            pbody = parent["body"]
            asset_id, version = pbody["asset_id"], pbody["version"] + 1
            parent_hash = manifest.manifest_hash(pbody, self.crypto)
        metadata = {"source_name": source.name, "source_size": len(original), "watermark_media_profile": profile}
        metadata_hash = self.crypto.sm3(manifest._canonical(metadata))
        body = {
            "schema_version": "1.0", "asset_id": asset_id, "content_id": content_id,
            "version": version, "publisher_id": publisher_id, "signer_key_id": key_id,
            "generator": {"name": "gm-provenance", "version": "0.1.0", "method": "local-demo"},
            "created_at": _utc_seconds(), "media_type": media_type, "content_size": len(final_bytes),
            "content_hash_alg": "SM3", "content_hash": self.crypto.sm3(final_bytes),
            "metadata": metadata, "metadata_hash": metadata_hash,
            "parent_content_id": parent_content_id, "parent_manifest_hash": parent_hash,
            "transformation": {"operation": "publish"}, "allowed_transformations": ["jpeg-reencode", "resize", "pdf-page-render"],
            "watermark_profile": profile, "signature_suite": "SM2-SM3-DER", "sm2_user_id": DEFAULT_USER_ID,
        }
        envelope = manifest.sign_manifest(body, self.crypto, private_path, passphrase)
        mh = manifest.manifest_hash(body, self.crypto)
        paths = self.registry.publish(envelope, final_bytes, mh)
        return {"content_id": content_id, "asset_id": asset_id, "version": version, "manifest_hash": mh,
                "media_type": media_type, "media_bytes": len(final_bytes), "watermark_profile": profile,
                "manifest": envelope, **paths}

    def _resolve_parent(self, source: str | Path) -> tuple[str, dict[str, Any], bytes]:
        """Resolve a parent ContentID from an id, manifest sidecar, or watermarked file."""
        text = str(source)
        if len(text) == 32:
            try:
                int(text, 16)
            except ValueError:
                pass
            else:
                envelope = self.registry.get_manifest(text)
                if envelope:
                    return text, envelope, self.registry.read_media(text)
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"找不到父版本或文件: {source}")
        if path.suffix.lower() == ".json":
            envelope = manifest.loads(path.read_bytes())
            parent_id = envelope.get("body", {}).get("content_id")
            stored = self.registry.get_manifest(parent_id) if isinstance(parent_id, str) else None
            if not stored or stored.get("body") != envelope.get("body"):
                raise ValueError("Manifest 未在本地登记")
            return parent_id, stored, self.registry.read_media(parent_id)
        raw = path.read_bytes()
        detected = media.detect(raw, media.sniff(raw))
        for parent_id in detected.get("content_ids", []):
            envelope = self.registry.get_manifest(parent_id)
            if envelope:
                return parent_id, envelope, raw
        raise ValueError("文件未恢复出已登记的父 ContentID")

    @staticmethod
    def _operation_allowed(parent_body: dict[str, Any], operation: str) -> bool:
        for rule in parent_body.get("allowed_transformations", []):
            if isinstance(rule, str) and rule == operation:
                return True
            if isinstance(rule, dict) and rule.get("operation") == operation:
                return True
        return False

    def _transform_bytes(self, data: bytes, operation: str, quality: int = 75) -> tuple[bytes, str]:
        from PIL import Image
        import io
        mt = media.sniff(data)
        if mt not in ("image/png", "image/jpeg"):
            raise ValueError("derive 当前支持 PNG/JPEG 图片")
        im = Image.open(io.BytesIO(data)).convert("RGB")
        out = io.BytesIO()
        if operation == "jpeg-reencode":
            im.save(out, "JPEG", quality=max(1, min(100, quality)))
        elif operation == "resize":
            im.resize((max(1, im.width // 2), max(1, im.height // 2))).save(out, "PNG")
        else:
            raise ValueError(f"不支持的变换: {operation}")
        return out.getvalue(), operation

    def derive(self, source: str | Path, private_path: str | Path, passphrase: str,
               operation: str, quality: int = 75) -> dict[str, Any]:
        """Create the authorized next version from a registered parent."""
        parent_id, parent_env, parent_bytes = self._resolve_parent(source)
        if parent_id in self.registry.status().get("revoked_content", []):
            raise PermissionError("父版本已撤销，不能继续派生")
        parent_body = parent_env["body"]
        if not self._operation_allowed(parent_body, operation):
            raise PermissionError(f"父版本未授权变换: {operation}")
        if self.crypto.sm3(parent_bytes) != parent_body["content_hash"]:
            raise ValueError("父文件摘要与父 Manifest 不一致")
        transformed, _ = self._transform_bytes(parent_bytes, operation, quality)
        content_id = secrets.token_hex(16)
        media_type = media.sniff(transformed)
        final_bytes, profile = media.embed(transformed, content_id, media_type)
        public = self._public_from_private(private_path, passphrase)
        key_id = self.crypto.key_id(public)
        if key_id in self.registry.status().get("revoked_keys", []):
            raise PermissionError("派生签名密钥已撤销，不能继续签发")
        existing = self.registry.get_key(key_id)
        publisher_id = parent_body["publisher_id"]
        if existing and existing["publisher_id"] != publisher_id:
            raise PermissionError("派生签名密钥不属于父版本发布者")
        self.registry.add_key(public, publisher_id, existing["trusted"] if existing else True, self.crypto)
        metadata = {"source_name": f"parent:{parent_id}", "source_size": len(parent_bytes), "watermark_media_profile": profile}
        body = {
            "schema_version": "1.0", "asset_id": parent_body["asset_id"], "content_id": content_id,
            "version": parent_body["version"] + 1, "publisher_id": publisher_id, "signer_key_id": key_id,
            "generator": {"name": "gm-provenance", "version": "0.1.0", "method": "authorized-derive"},
            "created_at": _utc_seconds(), "media_type": media_type, "content_size": len(final_bytes),
            "content_hash_alg": "SM3", "content_hash": self.crypto.sm3(final_bytes),
            "metadata": metadata, "metadata_hash": self.crypto.sm3(manifest._canonical(metadata)),
            "parent_content_id": parent_id, "parent_manifest_hash": manifest.manifest_hash(parent_body, self.crypto),
            "transformation": {"operation": operation, "quality": quality} if operation == "jpeg-reencode" else {"operation": operation},
            "allowed_transformations": parent_body["allowed_transformations"], "watermark_profile": profile,
            "signature_suite": "SM2-SM3-DER", "sm2_user_id": DEFAULT_USER_ID,
        }
        envelope = manifest.sign_manifest(body, self.crypto, private_path, passphrase)
        mh = manifest.manifest_hash(body, self.crypto)
        paths = self.registry.publish(envelope, final_bytes, mh)
        return {"content_id": content_id, "asset_id": body["asset_id"], "version": body["version"],
                "parent_content_id": parent_id, "manifest_hash": mh, "transformation": body["transformation"],
                "media_type": media_type, "media_bytes": len(final_bytes), "watermark_profile": profile,
                "manifest": envelope, **paths}

    def state_snapshot(self, private_path: str | Path, passphrase: str,
                       output_path: str | Path | None = None, state_public_path: str | Path | None = None,
                       ttl_seconds: int = 300) -> dict[str, Any]:
        """Sign current registry state with an independent state key."""
        output = Path(output_path) if output_path else self.root / "state-snapshot.json"
        public_path = Path(state_public_path) if state_public_path else self.root / "state-public.pem"
        public_path.write_bytes(self._public_from_private(private_path, passphrase))
        snapshot = state.sign_snapshot(self.registry.status(), self.crypto, str(private_path), passphrase, ttl_seconds)
        output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"snapshot_path": str(output.resolve()), "state_public_path": str(public_path.resolve()),
                "sequence": snapshot["body"]["sequence"], "expires_at": snapshot["body"]["expires_at"], "snapshot": snapshot}

    def verify_state_snapshot(self, snapshot_path: str | Path, state_public_path: str | Path,
                              now=None) -> dict[str, Any]:
        try:
            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            public = Path(state_public_path).read_bytes()
        except Exception as exc:
            return {"status": "UNKNOWN", "reasons": [f"state_unavailable:{exc}"]}
        result = state.verify_snapshot(snapshot, self.crypto, public, now=now)
        if result.get("status") in ("PASS", "UNKNOWN") and isinstance(result.get("sequence"), int):
            if not self.registry.accept_sequence(result["sequence"]):
                result = {**result, "status": "FAIL", "reasons": list(result.get("reasons", [])) + ["sequence rollback"]}
        return result

    def revoke(self, target_type: str, target_id: str, reason: str,
               issuer: str = "registry-demo") -> dict[str, Any]:
        """Record a revocation only for an object known to this registry."""
        if target_type == "content":
            known = self.registry.get_manifest(target_id) is not None
        elif target_type == "key":
            known = self.registry.get_key(target_id) is not None
        else:
            raise ValueError("target_type must be content or key")
        if not known:
            raise KeyError(f"unknown {target_type}: {target_id}")
        return self.registry.revoke(target_type, target_id, reason, issuer)

    def _public_from_private(self, private_path, passphrase) -> bytes:
        return self.crypto._ok(["pkey", "-in", str(Path(private_path).resolve()), "-pubout", "-passin", "__PASSPHRASE__"], passphrase=passphrase)

    def verify(self, media_path: str | Path, manifest_path: str | Path | None = None,
               mode: str = "full", state_snapshot_path: str | Path | None = None,
               state_public_path: str | Path | None = None) -> dict[str, Any]:
        if mode not in ("hard", "recover", "full"):
            raise ValueError("mode must be hard, recover or full")
        started = time.perf_counter(); media_bytes = Path(media_path).read_bytes()
        if len(media_bytes) > media.MAX_BYTES:
            raise ValueError("文件超过 20MB 限制")
        result: dict[str, Any] = {"manifest_presence": "NOT_CHECKED", "manifest_schema": "NOT_CHECKED",
            "signature_valid": "NOT_CHECKED", "issuer_trusted": "NOT_CHECKED", "content_hash_match": "NOT_CHECKED",
            "watermark_status": "NOT_CHECKED", "recovered_content_ids": [], "parent_chain_status": "NOT_CHECKED",
            "authorization_status": "NOT_CHECKED", "content_revocation_status": "NOT_CHECKED", "key_revocation_status": "NOT_CHECKED",
            "state_freshness": "NOT_CHECKED", "latest_version_status": "NOT_CHECKED", "checked_scope": "file",
            "reasons": [], "timings": {}}
        media_type = media.sniff(media_bytes)
        # hard is deliberately limited to an identified credential and its
        # exact byte binding.  Watermark recovery is a separate operation.
        wm = {"status": "NOT_CHECKED", "content_ids": []}
        if mode in ("recover", "full"):
            wm = media.detect(media_bytes, media_type)
            result["watermark_status"] = wm.get("status", "NOT_FOUND")
            result["recovered_content_ids"] = wm.get("content_ids", [])
            if wm.get("status") == "CONFLICT":
                result["reasons"].append("watermark_conflict")
        envelope = None
        if manifest_path is not None:
            if Path(manifest_path).exists():
                try: envelope = manifest.loads(Path(manifest_path).read_bytes())
                except Exception as exc: result["reasons"].append(f"manifest_parse:{exc}")
            else:
                result["reasons"].append("manifest_missing")
        if envelope is None and manifest_path is None and result["recovered_content_ids"] and mode in ("recover", "full") and wm.get("status") == "FOUND":
            envelope = self.registry.get_manifest(result["recovered_content_ids"][0])
            if envelope: result["checked_scope"] = "file via recovered ContentID"
        if envelope is None:
            result["manifest_presence"] = "FAIL" if manifest_path else "NOT_CHECKED"
            if manifest_path is not None:
                result["conclusion"] = "REJECTED"
            else:
                if wm.get("status") == "CONFLICT":
                    result["conclusion"] = "REJECTED"
                else:
                    result["conclusion"] = "ORIGIN_HINT_ONLY" if result["recovered_content_ids"] else "NO_PROVENANCE"
            result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return result
        result["manifest_presence"] = "PASS"
        try:
            manifest.validate_body(envelope["body"]); result["manifest_schema"] = "PASS"
        except Exception as exc:
            result["manifest_schema"] = "FAIL"; result["reasons"].append(f"manifest_schema:{exc}")
            result["conclusion"] = "REJECTED"; return result
        body = envelope["body"]; key = self.registry.get_key(body["signer_key_id"])
        if not key:
            result["issuer_trusted"] = "FAIL"; result["signature_valid"] = "UNKNOWN"; result["reasons"].append("signer_key_not_registered")
        else:
            result["issuer_trusted"] = "PASS" if key["trusted"] else "FAIL"
            if key["publisher_id"] != body["publisher_id"]:
                result["issuer_trusted"] = "FAIL"
                result["reasons"].append("signer_publisher_mismatch")
            try: result["signature_valid"] = "PASS" if manifest.verify_manifest(envelope, self.crypto, key["public_pem"]) else "FAIL"
            except Exception as exc: result["signature_valid"] = "FAIL"; result["reasons"].append(f"signature:{exc}")
        expected = body["content_hash"]
        result["content_hash_match"] = "PASS" if self.crypto.sm3(media_bytes) == expected else "FAIL"
        result["parent_chain_status"] = "PASS" if body["parent_content_id"] is None else self._check_parent(envelope)
        result["authorization_status"] = "PASS" if body["parent_content_id"] is None else self._check_authorization(envelope)
        # Only full validation claims registry state.  The other modes leave
        # state dimensions NOT_CHECKED so callers cannot mistake a fast path
        # for a fresh revocation/latest-version decision.
        if mode == "full":
            snap_path = Path(state_snapshot_path) if state_snapshot_path else self.root / "state-snapshot.json"
            pub_path = Path(state_public_path) if state_public_path else self.root / "state-public.pem"
            if snap_path.exists() and pub_path.exists():
                state_result = self.verify_state_snapshot(snap_path, pub_path)
                result["state_freshness"] = state_result.get("status", "UNKNOWN")
                result["reasons"].extend(f"state:{x}" for x in state_result.get("reasons", []))
                sb = state_result.get("body") or {}
                if state_result.get("status") == "PASS":
                    result["content_revocation_status"] = "FAIL" if body["content_id"] in sb.get("revoked_content", []) else "PASS"
                    result["key_revocation_status"] = "FAIL" if body["signer_key_id"] in sb.get("revoked_keys", []) else "PASS"
                    result["latest_version_status"] = "PASS" if sb.get("latest", {}).get(body["asset_id"]) == body["content_id"] else "FAIL"
                else:
                    result["content_revocation_status"] = result["key_revocation_status"] = result["latest_version_status"] = "UNKNOWN"
            else:
                result["state_freshness"] = "UNKNOWN"
                result["content_revocation_status"] = result["key_revocation_status"] = result["latest_version_status"] = "UNKNOWN"
        hard_failure = result["manifest_schema"] == "FAIL" or result["signature_valid"] == "FAIL" or result["parent_chain_status"] == "FAIL" or result["authorization_status"] == "FAIL"
        revoked = result["content_revocation_status"] == "FAIL" or result["key_revocation_status"] == "FAIL"
        state_complete = mode != "full" or all(result[k] == "PASS" for k in ("state_freshness", "content_revocation_status", "key_revocation_status", "latest_version_status"))
        if hard_failure or revoked or wm.get("status") == "CONFLICT":
            result["conclusion"] = "REJECTED"
        elif result["content_hash_match"] == "PASS" and result["issuer_trusted"] == "PASS" and result["parent_chain_status"] == "PASS" and result["authorization_status"] == "PASS" and state_complete:
            result["conclusion"] = "VERIFIED_DERIVATIVE" if body["parent_content_id"] is not None else "VERIFIED_EXACT"
        elif result["content_hash_match"] == "FAIL" and result["watermark_status"] == "FOUND":
            # A valid old credential plus a recovered ID is a source hint after a propagation transform.
            result["conclusion"] = "ORIGIN_HINT_ONLY"
        else:
            result["conclusion"] = "INDETERMINATE"
        result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result

    def _check_parent(self, envelope):
        body = envelope["body"]
        child = body
        parent_id = body.get("parent_content_id")
        seen: set[str] = set()
        for _ in range(64):
            if not isinstance(parent_id, str) or parent_id in seen:
                return "FAIL"
            seen.add(parent_id)
            parent = self.registry.get_manifest(parent_id)
            if not parent or not isinstance(parent.get("body"), dict):
                return "FAIL"
            pbody = parent["body"]
            # Every edge binds the exact parent body, and every ancestor must
            # carry a cryptographically valid credential from the same issuer.
            if manifest.manifest_hash(pbody, self.crypto) != child.get("parent_manifest_hash"):
                return "FAIL"
            if pbody.get("asset_id") != body.get("asset_id") or pbody.get("publisher_id") != body.get("publisher_id"):
                return "FAIL"
            if pbody.get("version") + 1 != child.get("version"):
                return "FAIL"
            pkey = self.registry.get_key(pbody.get("signer_key_id"))
            if not pkey or pkey.get("publisher_id") != pbody.get("publisher_id"):
                return "FAIL"
            try:
                if not manifest.verify_manifest(parent, self.crypto, pkey["public_pem"]):
                    return "FAIL"
            except Exception:
                return "FAIL"
            parent_id = pbody.get("parent_content_id")
            if parent_id is None:
                return "PASS" if pbody.get("version") == 1 and pbody.get("parent_manifest_hash") is None else "FAIL"
            child = pbody
        return "FAIL"

    def _check_authorization(self, envelope):
        body = envelope["body"]
        parent = self.registry.get_manifest(body["parent_content_id"])
        if not parent:
            return "FAIL"
        operation = body.get("transformation", {}).get("operation")
        pbody = parent["body"]
        key = self.registry.get_key(body.get("signer_key_id"))
        pkey = self.registry.get_key(pbody.get("signer_key_id"))
        if not key or not pkey:
            return "FAIL"
        # A child cannot claim authorization merely by naming an allowed
        # operation: its signer must belong to the parent's publisher, and the
        # parent signer must itself be a trusted registered issuer.
        if key.get("publisher_id") != pbody.get("publisher_id") or key.get("publisher_id") != body.get("publisher_id"):
            return "FAIL"
        if not pkey.get("trusted"):
            return "FAIL"
        return "PASS" if isinstance(operation, str) and self._operation_allowed(pbody, operation) else "FAIL"

    def transform(self, source_path: str | Path, output_path: str | Path, operation: str = "jpeg-reencode", quality: int = 75) -> dict[str, Any]:
        from PIL import Image
        import io
        data = Path(source_path).read_bytes(); transformed, _ = self._transform_bytes(data, operation, quality)
        Path(output_path).write_bytes(transformed); return {"operation": operation, "input": str(source_path), "output": str(output_path), "bytes": len(transformed)}
