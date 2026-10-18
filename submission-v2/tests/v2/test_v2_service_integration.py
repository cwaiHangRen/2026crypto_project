from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import KeyManager
from gm_provenance.v2_service import V2Service


def _service(tmp_path: Path) -> tuple[V2Service, KeyManager]:
    crypto = OpenSSLCrypto()
    km = KeyManager(tmp_path, crypto=crypto)
    km.generate(publisher_id="demo", passphrase="passphrase")
    return V2Service(tmp_path, crypto=crypto, key_manager=km), km


def _image() -> bytes:
    import io
    out = io.BytesIO()
    Image.new("RGB", (600, 400), (35, 80, 120)).save(out, "PNG")
    return out.getvalue()


def test_register_verify_and_audit_chain(tmp_path):
    service, _km = _service(tmp_path)
    published = service.register_bytes(_image(), filename="demo.png", publisher_id="demo", passphrase="passphrase")
    result = service.verify_bytes(published["media_bytes_value"], content_id=published["content_id"])
    assert result["decision"] == "VERIFIED"
    assert result["signature"]["status"] == "PASS"
    assert result["sm3"]["status"] == "PASS"
    assert service.store.get_content(published["content_id"])["manifest"]["key_id"] == published["key_id"]
    audit = service.audit_events()
    assert len(audit) >= 2


def test_batch_proof_and_tamper_rejection(tmp_path):
    service, _km = _service(tmp_path)
    rows = [service.register_bytes(_image(), filename=f"demo-{i}.png", publisher_id="demo", passphrase="passphrase") for i in range(2)]
    batch = service.create_batch([r["content_id"] for r in rows], publisher_id="demo", passphrase="passphrase")
    from gm_provenance.batch_proof import proof_for, verify_proof
    stored = service.store.get_batch(batch["batch_id"])
    leaf = rows[0]["manifest_hash"]
    proof = proof_for(stored, leaf)
    assert verify_proof(stored, proof)["valid"]
    proof["siblings"][0] = "0" * 64
    assert not verify_proof(stored, proof)["valid"]


def test_revoked_signing_key_is_explicit_risk(tmp_path):
    service, km = _service(tmp_path)
    published = service.register_bytes(_image(), filename="revoked.png", publisher_id="demo", passphrase="passphrase")
    km.revoke(published["key_id"], "v2 test revoke")
    result = service.verify_bytes(published["media_bytes_value"], content_id=published["content_id"])
    assert result["signature"]["status"] == "PASS"
    assert result["revocation"]["status"] == "FAIL"
    assert result["decision"] == "RISK_REVOKED_KEY"
