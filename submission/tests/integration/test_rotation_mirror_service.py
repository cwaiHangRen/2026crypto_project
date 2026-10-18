"""旋转镜像后的真实发布/签验闭环及现有上传 API。"""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from gm_provenance import api, watermark
from gm_provenance.service import ProvenanceService


@pytest.fixture(scope="module", params=["PNG", "JPEG"])
def published(request, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("rotation-service")
    source = tmp_path / "source.bin"
    image = Image.new("RGB", (896, 640), (90, 140, 180))
    ImageDraw.Draw(image).text((80, 120), "Service carrier", fill="white")
    image.save(source, request.param, quality=95)
    service = ProvenanceService(tmp_path / "registry")
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    service.keygen(private, public, "rotation-test-pass-123")
    service.trust_key(public, "publisher-demo-001")
    return service, service.register(source, private, "rotation-test-pass-123")


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("rotation", [None, Image.Transpose.ROTATE_90,
                                      Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_270])
def test_service_recovers_without_sidecar_and_preserves_hash_semantics(published, tmp_path, mirror, rotation):
    service, registered = published
    original = Path(registered["media_path"]).read_bytes()
    image = Image.open(io.BytesIO(original))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if rotation is not None:
        image = image.transpose(rotation)
    path = tmp_path / "opaque-input.bin"
    if not mirror and rotation is None:
        path.write_bytes(original)
    else:
        image.save(path, "PNG")
    result = service.verify(path, None, "recover")
    exact = path.read_bytes() == original
    assert result["watermark_status"] == "FOUND"
    assert result["recovered_content_ids"] == [registered["content_id"]]
    assert result["signature_valid"] == result["issuer_trusted"] == "PASS"
    assert result["content_hash_match"] == ("PASS" if exact else "FAIL")
    assert result["conclusion"] == ("VERIFIED_BINDING_ONLY" if exact else "ORIGIN_HINT_ONLY")
    assert result["state_freshness"] == "NOT_CHECKED"
    if not exact:
        hard = service.verify(path, registered["manifest_path"], "hard")
        assert hard["content_hash_match"] == "FAIL"
        assert hard["conclusion"] == "INDETERMINATE"


def test_api_upload_recovers_mirrored_rotated_image(published, monkeypatch):
    service, registered = published
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    image = Image.open(registered["media_path"]).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    image = image.transpose(Image.Transpose.ROTATE_270)
    out = io.BytesIO()
    image.save(out, "PNG")
    response = client.post("/api/verify", files={"file": ("opaque.bin", out.getvalue(), "image/png")})
    assert response.status_code == 200
    result = response.json()
    assert result["verification_mode"] == "full"
    assert result["recovered_content_ids"] == [registered["content_id"]]
    assert result["signature_valid"] == "PASS"
    assert result["content_hash_match"] == "FAIL"
    assert result["conclusion"] == "ORIGIN_HINT_ONLY"
    assert result["state_freshness"] == "UNKNOWN"


def test_service_rejects_real_conflicting_orientation_ids(published, tmp_path):
    service, registered = published
    marked = Path(registered["media_path"]).read_bytes()
    other_id = "fedcba9876543210fedcba9876543210"
    opposite, _ = watermark.embed_image(marked, other_id)
    image = Image.open(io.BytesIO(marked))
    opposite_image = Image.open(io.BytesIO(opposite)).transpose(Image.Transpose.ROTATE_180)
    width, height = image.size
    image.paste(opposite_image.crop((0, height // 2, width, height)), (0, height // 2))
    path = tmp_path / "opaque-conflict.bin"
    image.save(path, "PNG")
    result = service.verify(path, None, "recover")
    assert result["watermark_status"] == "CONFLICT"
    assert result["recovered_content_ids"] == sorted([registered["content_id"], other_id])
    assert result["conclusion"] == "REJECTED"
