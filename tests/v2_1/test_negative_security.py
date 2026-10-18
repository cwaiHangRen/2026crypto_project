from __future__ import annotations

import io
import sqlite3

import pytest
from PIL import Image


def _image_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (20, 90, 130)).save(out, "PNG")
    return out.getvalue()


def test_content_id_and_mime_boundaries_are_rejected():
    from gm_provenance.v2_api import _declared_mime_ok, _validate_content_id

    assert _declared_mime_ok(b"%PDF-1.7", "application/pdf")
    assert not _declared_mime_ok(b"%PDF-1.7", "image/png")
    with pytest.raises(Exception):
        _validate_content_id("../secret")


def test_audit_duplicate_and_tamper_are_detected(tmp_path):
    from gm_provenance.audit_service import AuditService

    audit = AuditService(tmp_path)
    audit.append("test", actor="v2.1", result="PASS", event_id="fixed-event")
    with pytest.raises(ValueError):
        audit.append("test", actor="v2.1", result="PASS", event_id="fixed-event")
    with sqlite3.connect(tmp_path / "audit.sqlite") as db:
        db.execute("UPDATE audit_events SET result='FAIL' WHERE event_id='fixed-event'")
    result = audit.verify_chain()
    assert result["valid"] is False
    assert result["first_bad_sequence"] == 1


def test_wrong_key_id_is_rejected_without_fallback(tmp_path):
    from gm_provenance.crypto import OpenSSLCrypto
    from gm_provenance.key_manager import KeyManager
    from gm_provenance.v2_service import V2Service

    crypto = OpenSSLCrypto()
    keys = KeyManager(tmp_path, crypto=crypto)
    keys.generate(publisher_id="security", passphrase="security-passphrase")
    service = V2Service(tmp_path, crypto=crypto, key_manager=keys)
    with pytest.raises(KeyError):
        service.register_bytes(_image_bytes(), filename="safe.png", publisher_id="security", key_id="bad" * 16, passphrase="security-passphrase")


def test_repeated_verify_is_audited_and_cross_batch_proof_fails(tmp_path):
    from gm_provenance.batch_proof import proof_for
    from gm_provenance.crypto import OpenSSLCrypto
    from gm_provenance.key_manager import KeyManager
    from gm_provenance.v2_service import V2Service

    crypto = OpenSSLCrypto()
    keys = KeyManager(tmp_path, crypto=crypto)
    keys.generate(publisher_id="security", passphrase="security-passphrase")
    service = V2Service(tmp_path, crypto=crypto, key_manager=keys)
    first = service.register_bytes(_image_bytes(), filename="a.png", publisher_id="security", passphrase="security-passphrase")
    second = service.register_bytes(_image_bytes(), filename="b.png", publisher_id="security", passphrase="security-passphrase")
    one = service.create_batch([first["content_id"]], publisher_id="security", passphrase="security-passphrase")
    two = service.create_batch([second["content_id"]], publisher_id="security", passphrase="security-passphrase")
    proof = proof_for(service.store.get_batch(one["batch_id"]), first["manifest_hash"])
    wrong = service.verify_bytes(first["media_bytes_value"], content_id=first["content_id"], batch_id=two["batch_id"], proof=proof)
    assert wrong["merkle"]["valid"] is False
    service.verify_bytes(first["media_bytes_value"], content_id=first["content_id"])
    service.verify_bytes(first["media_bytes_value"], content_id=first["content_id"])
    assert len(service.audit_events()) >= 5
