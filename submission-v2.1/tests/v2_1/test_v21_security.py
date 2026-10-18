from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from gm_provenance.audit_service import AuditService
from gm_provenance.batch_proof import proof_for, verify_proof
from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import KeyManager
from gm_provenance.v2_service import V2Service


def _image() -> bytes:
    out = io.BytesIO(); Image.new("RGB", (600, 400), (22, 91, 131)).save(out, "PNG"); return out.getvalue()


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    from gm_provenance import v2_api
    crypto = OpenSSLCrypto(); km = KeyManager(tmp_path, crypto=crypto); km.generate(publisher_id="demo", passphrase="api-passphrase")
    monkeypatch.setattr(v2_api, "service", V2Service(tmp_path, crypto=crypto, key_manager=km))
    monkeypatch.setattr(v2_api, "MAX_UPLOAD_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(v2_api, "_BEARER_TOKEN", "")
    return v2_api, TestClient(v2_api.app)


def test_api_rejects_oversize_mime_and_path_traversal(api_env, monkeypatch):
    v2_api, client = api_env
    monkeypatch.setattr(v2_api, "MAX_UPLOAD_BYTES", 64)
    response = client.post("/api/v2/register", files={"file": ("x.png", _image(), "image/png")}, data={"publisher_id": "demo"})
    assert response.status_code == 413
    monkeypatch.setattr(v2_api, "MAX_UPLOAD_BYTES", 32 * 1024 * 1024)
    response = client.post("/api/v2/register", files={"file": ("x.png", _image(), "application/pdf")}, data={"publisher_id": "demo"})
    assert response.status_code == 415
    response = client.post("/api/v2/register", files={"file": ("../x.png", _image(), "image/png")}, data={"publisher_id": "demo"})
    assert response.status_code == 400
    assert "C:\\Users" not in response.text and "v2.sqlite" not in response.text


def test_api_register_verify_replay_and_json_boundary(api_env):
    _v2_api, client = api_env
    response = client.post("/api/v2/register", files={"file": ("fixture.png", _image(), "image/png")}, data={"publisher_id": "demo", "passphrase": "api-passphrase"})
    assert response.status_code == 200, response.text
    body = response.json(); cid = body["content_id"]
    assert "media_bytes_value" not in body and "private" not in response.text.lower()
    result = client.post("/api/v2/verify", files={"file": ("published.png", _v2_api.service.store.get_content(cid)["media"], "image/png")}, data={"content_id": cid})
    replay = client.post("/api/v2/verify", files={"file": ("published.png", _v2_api.service.store.get_content(cid)["media"], "image/png")}, data={"content_id": cid})
    assert result.status_code == replay.status_code == 200
    assert result.json()["decision"] == replay.json()["decision"] == "VERIFIED"
    bad = client.post("/api/v2/verify", files={"file": ("published.png", _image(), "image/png")}, data={"content_id": "bad"})
    assert bad.status_code == 400


def test_management_bearer_and_batch_input_limits(api_env, monkeypatch):
    v2_api, client = api_env
    monkeypatch.setattr(v2_api, "_BEARER_TOKEN", "test-token")
    assert client.post("/api/v2/keygen", json={"publisher_id": "x", "passphrase": "long-passphrase"}).status_code == 401
    assert client.post("/api/v2/keygen", json={"publisher_id": "x", "passphrase": "long-passphrase"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    good = client.post("/api/v2/keygen", json={"publisher_id": "x", "passphrase": "long-passphrase"}, headers={"Authorization": "Bearer test-token"})
    assert good.status_code == 200
    too_many = client.post("/api/v2/batches", json={"content_ids": ["0" * 32] * 257}, headers={"Authorization": "Bearer test-token"})
    assert too_many.status_code == 400


def test_merkle_replay_and_audit_tamper_are_rejected(tmp_path):
    crypto = OpenSSLCrypto(); km = KeyManager(tmp_path, crypto=crypto); km.generate(publisher_id="demo", passphrase="passphrase")
    service = V2Service(tmp_path, crypto=crypto, key_manager=km)
    rows = [service.register_bytes(_image(), filename=f"{i}.png", publisher_id="demo", passphrase="passphrase") for i in range(2)]
    batch = service.create_batch([r["content_id"] for r in rows], publisher_id="demo", passphrase="passphrase")
    stored = service.store.get_batch(batch["batch_id"]); leaf = stored["manifest"]["leaves"][0]; proof = proof_for(stored, leaf)
    assert verify_proof(stored, proof)["valid"]
    proof["batch_id"] = "wrong-batch"
    assert not verify_proof(stored, proof)["valid"]
    proof = proof_for(stored, leaf); proof["leaf"] = "0" * 64
    assert not verify_proof(stored, proof)["valid"]
    audit = AuditService(tmp_path, crypto=crypto); event = audit.events()[0]
    with sqlite3.connect(audit.db_path) as db:
        db.execute("UPDATE audit_events SET result=? WHERE sequence=?", ("TAMPERED", event["sequence"])); db.commit()
    report = audit.verify_chain()
    assert report["valid"] is False and report["first_bad_sequence"] == event["sequence"]
