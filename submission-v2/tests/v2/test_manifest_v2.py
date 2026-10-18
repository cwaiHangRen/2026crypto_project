from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.manifest_v2 import ManifestV2Error, canonical_bytes, inspect_manifest, manifest_hash, normalize_body, sign_manifest, verify_manifest


def _body() -> dict:
    metadata = {"caption": "演示", "license": "demo", "tags": ["v2", "proof"]}
    metadata_hash = hashlib.new("sm3", json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": "2.0", "asset_id": "a" * 32, "content_id": "b" * 32, "version": 1,
        "publisher_id": "publisher-demo-001", "key_id": "c" * 64,
        "signature_algorithm": "SM2-SM3-DER", "created_at": "2026-10-18T00:00:00Z",
        "media_type": "image/png", "content_size": 123, "content_hash_alg": "SM3",
        "content_hash": "d" * 64, "metadata": metadata, "metadata_hash": metadata_hash,
        "parent_content_id": None, "parent_manifest_hash": None,
        "allowed_transformations": ["jpeg-reencode", {"name": "resize", "max": 4096}],
        "watermark_profile": {"algorithm": "watermark-v1", "dpi": 120},
        "generator": {"name": "v2-test"}, "transformation": {},
    }


def test_fixed_field_order_and_length_prefix_is_deterministic():
    body = _body()
    first = canonical_bytes(body)
    reordered = {key: body[key] for key in reversed(list(body))}
    assert first == canonical_bytes(reordered)
    assert first.startswith(b"GM-PROVENANCE-MANIFEST-v2\x00")
    assert len(first) > 2
    assert manifest_hash(body, type("C", (), {"sm3": staticmethod(lambda value: hashlib.new("sm3", value).hexdigest())})())


def test_unicode_normalization_and_metadata_tamper_rejected():
    body = _body()
    body["publisher_id"] = "pub-e\u0301"
    normalized = normalize_body(body)
    assert normalized["publisher_id"] == "pub-é"
    body["metadata"]["tamper"] = True
    try:
        normalize_body(body)
    except ManifestV2Error:
        pass
    else:
        raise AssertionError("metadata tamper was accepted")


def test_sign_verify_and_inspect(tmp_path: Path):
    crypto = OpenSSLCrypto()
    private, public = tmp_path / "key.pem", tmp_path / "pub.pem"
    generated_key = crypto.generate_key(private, public, "manifest-v2-pass")
    body = _body(); body["key_id"] = generated_key
    envelope = sign_manifest(body, crypto, private, "manifest-v2-pass")
    checked = verify_manifest(envelope, crypto, public.read_bytes())
    assert checked["valid"] and checked["signature_valid"] and checked["hash_valid"]
    inspected = inspect_manifest(envelope, crypto)
    assert inspected["manifest_hash"] == envelope["manifest_hash"]
    assert base64.b64decode(envelope["manifest_canonical_bytes"])
    envelope["body"]["publisher_id"] = "attacker"
    assert not verify_manifest(envelope, crypto, public.read_bytes())["valid"]

