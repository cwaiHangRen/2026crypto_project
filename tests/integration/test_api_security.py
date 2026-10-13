"""FastAPI authorization and state-linked full verification checks.

These tests intentionally use a real temporary registry and SM2/SM3 signing
operations.  They do not mock the service's authentication or verification
decisions.
"""
from pathlib import Path
import secrets

from fastapi.testclient import TestClient
from PIL import Image

from gm_provenance import api
from gm_provenance import media
from gm_provenance.service import ProvenanceService


def _image(path: Path) -> None:
    Image.new("RGB", (768, 768), (245, 248, 252)).save(path, format="PNG")


def test_api_requires_admin_and_full_revoke_roundtrip(tmp_path: Path, monkeypatch):
    service = ProvenanceService(tmp_path / "registry")
    token = service.initialize_admin()
    monkeypatch.setattr(api, "service", service)
    state_private = tmp_path / "state-private.pem"
    state_public = tmp_path / "state-public.pem"
    state_passphrase = secrets.token_urlsafe(24)
    service.keygen(state_private, state_public, state_passphrase)
    source = tmp_path / "source.png"
    _image(source)
    client = TestClient(api.app)

    upload = {"file": ("source.png", source.read_bytes(), "image/png")}
    denied = client.post("/api/register-demo", files=upload)
    assert denied.status_code == 401
    wrong = client.post("/api/register-demo", files=upload, headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401
    # confirm=true is an anti-mistake guard and does not replace authentication.
    no_auth_revoke = client.post(
        "/api/revoke",
        json={"target_type": "content", "target_id": "0" * 32, "reason": "test", "confirm": True},
    )
    assert no_auth_revoke.status_code == 401
    assert service.registry.status()["sequence"] == 0

    auth = {"Authorization": f"Bearer {token}"}
    registered = client.post("/api/register-demo", files=upload, headers=auth)
    assert registered.status_code == 200
    published = registered.json()
    assert token not in registered.text
    assert "private" not in registered.text.lower()
    assert "passphrase" not in registered.text.lower()

    # The snapshot is deliberately signed with a separate state identity.
    service.state_snapshot(state_private, state_passphrase)
    media_bytes = Path(published["media_path"]).read_bytes()
    checked = client.post("/api/verify", files={"file": ("published.png", media_bytes, "image/png")})
    assert checked.status_code == 200
    result = checked.json()
    assert result["verification_mode"] == "full"
    assert result["signature_valid"] == "PASS"
    assert result["issuer_trusted"] == "PASS"
    assert result["content_hash_match"] == "PASS"
    assert result["state_freshness"] == "PASS"
    assert result["content_revocation_status"] == "PASS"
    assert result["key_revocation_status"] == "PASS"
    assert result["latest_version_status"] == "PASS"
    assert result["conclusion"] in {"VERIFIED_EXACT", "VERIFIED_DERIVATIVE"}

    revoked = client.post(
        "/api/revoke",
        headers=auth,
        json={"target_type": "content", "target_id": published["content_id"], "reason": "test", "confirm": True},
    )
    assert revoked.status_code == 200
    service.state_snapshot(state_private, state_passphrase)
    after = client.post("/api/verify", files={"file": ("published.png", media_bytes, "image/png")})
    assert after.status_code == 200
    revoked_result = after.json()
    assert revoked_result["content_revocation_status"] == "FAIL"
    assert revoked_result["conclusion"] == "REJECTED"


def test_api_upload_limits_are_consistent(tmp_path: Path, monkeypatch):
    service = ProvenanceService(tmp_path / "registry")
    token = service.initialize_admin()
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    oversized = b"x" * (media.MAX_BYTES + 1)
    files = {"file": ("oversized.bin", oversized, "application/octet-stream")}
    assert client.post("/api/verify", files=files).status_code == 413
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/register-demo", files=files, headers=auth).status_code == 413
    assert client.post("/api/transform-preview", files=files, headers=auth).status_code == 413
    assert service.registry.status()["sequence"] == 0
