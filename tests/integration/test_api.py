from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from gm_provenance import api
from gm_provenance.service import ProvenanceService


def test_api_register_verify_transform_and_revoke(tmp_path: Path, monkeypatch):
    service = ProvenanceService(tmp_path / "data")
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)

    image = tmp_path / "source.png"
    # The production PNG watermark profile needs a sufficiently large carrier.
    Image.new("RGB", (768, 768), "white").save(image)
    response = client.post(
        "/api/register-demo",
        files={"file": ("source.png", image.read_bytes(), "image/png")},
    )
    assert response.status_code == 200
    published = response.json()
    assert published["content_id"]
    assert "manifest" not in published
    assert "media_bytes" not in published

    verify = client.post(
        "/api/verify",
        files={"file": ("published.png", Path(published["media_path"]).read_bytes(), "image/png")},
    )
    assert verify.status_code == 200
    assert published["content_id"] in verify.json()["recovered_content_ids"]

    transformed = client.post(
        "/api/transform-preview?operation=resize",
        files={"file": ("published.png", Path(published["media_path"]).read_bytes(), "image/png")},
    )
    assert transformed.status_code == 200
    assert transformed.json()["verification"]["recovered_content_ids"] == [published["content_id"]]

    denied = client.post(
        "/api/revoke",
        json={"target_type": "content", "target_id": published["content_id"], "reason": "test"},
    )
    assert denied.status_code == 403

    revoked = client.post(
        "/api/revoke",
        json={"target_type": "content", "target_id": published["content_id"], "reason": "test", "confirm": True},
    )
    assert revoked.status_code == 200
    assert revoked.json()["target_id"] == published["content_id"]


def test_api_rejects_oversized_verification_upload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(api, "service", ProvenanceService(tmp_path / "data"))
    client = TestClient(api.app)
    response = client.post(
        "/api/verify",
        files={"file": ("large.bin", b"0" * (20 * 1024 * 1024 + 1), "application/octet-stream")},
    )
    assert response.status_code == 413
