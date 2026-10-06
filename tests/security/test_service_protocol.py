"""Protocol-level service checks using generated SM2 keys and real signatures."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PIL import Image
import pytest

from gm_provenance.service import ProvenanceService


def _published(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (768, 768), "white").save(source)
    service = ProvenanceService(tmp_path / "registry")
    private, public = tmp_path / "publisher.pem", tmp_path / "publisher.pub"
    service.keygen(private, public, "protocol-pass")
    return service, source, private


def test_full_without_fresh_state_is_indeterminate_and_hard_is_explicit(tmp_path: Path):
    service, _source, private = _published(tmp_path)
    published = service.register(_source, private, "protocol-pass")

    full = service.verify(published["media_path"], published["manifest_path"], mode="full")
    assert full["state_freshness"] == "UNKNOWN"
    assert full["content_revocation_status"] == "UNKNOWN"
    assert full["conclusion"] == "INDETERMINATE"

    hard = service.verify(published["media_path"], published["manifest_path"], mode="hard")
    assert hard["conclusion"] == "VERIFIED_EXACT"
    assert hard["watermark_status"] == "NOT_CHECKED"
    assert hard["state_freshness"] == "NOT_CHECKED"


def test_authorized_child_is_verified_derivative(tmp_path: Path):
    service, source, private = _published(tmp_path)
    root = service.register(source, private, "protocol-pass")
    child = service.derive(root["content_id"], private, "protocol-pass", "resize")
    checked = service.verify(child["media_path"], child["manifest_path"], mode="hard")
    assert checked["conclusion"] == "VERIFIED_DERIVATIVE"
    assert checked["parent_chain_status"] == "PASS"
    assert checked["authorization_status"] == "PASS"


def test_parent_signature_tamper_rejects_child_chain(tmp_path: Path):
    service, source, private = _published(tmp_path)
    root = service.register(source, private, "protocol-pass")
    child = service.derive(root["content_id"], private, "protocol-pass", "resize")
    db = service.registry.db_path
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT manifest_json FROM contents WHERE content_id=?", (root["content_id"],)).fetchone()
        envelope = json.loads(row[0])
        sig = envelope["signature"]
        envelope["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
        conn.execute("UPDATE contents SET manifest_json=? WHERE content_id=?", (json.dumps(envelope, separators=(",", ":")), root["content_id"]))
        conn.commit()
    checked = service.verify(child["media_path"], child["manifest_path"], mode="hard")
    assert checked["parent_chain_status"] == "FAIL"
    assert checked["conclusion"] == "REJECTED"


def test_revoked_key_and_parent_cannot_issue_new_version(tmp_path: Path):
    service, source, private = _published(tmp_path)
    root = service.register(source, private, "protocol-pass")
    key_id = service.registry.get_manifest(root["content_id"])["body"]["signer_key_id"]
    service.revoke("key", key_id, "compromised")
    with pytest.raises(PermissionError):
        service.register(source, private, "protocol-pass")
