"""任意角度演示使用真实发布字节与恢复器；角度只用于图片变换。"""
import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from gm_provenance import api, media
from gm_provenance.benchmark import _bytes_image
from gm_provenance.service import ProvenanceService


@pytest.fixture(scope="module")
def demo_published(tmp_path_factory):
    root = tmp_path_factory.mktemp("arbitrary-demo")
    source = root / "source.png"
    image = Image.new("RGB", (896, 640), (92, 145, 188))
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 90, 650, 510), outline="white", width=6)
    draw.text((200, 250), "Arbitrary rotation demo", fill="white")
    image.save(source, "PNG")
    service = ProvenanceService(root / "registry")
    private, public = root / "private.pem", root / "public.pem"
    password = "arbitrary-demo-test-pass-2026"
    service.keygen(private, public, password)
    service.trust_key(public, "publisher-demo-001")
    registered = service.register(source, private, password)
    private.unlink()
    return service, registered, Path(registered["media_path"]).read_bytes()


@pytest.mark.parametrize("angle,mirror", [(37.5, False), (-22.75, True), (213.25, False), (398.125, True)])
def test_service_arbitrary_rotation_preserves_origin_and_hash_semantics(demo_published, tmp_path, angle, mirror):
    service, registered, original = demo_published
    output = tmp_path / "opaque.bin"
    transformed = service.transform(registered["media_path"], output, "rotate", angle=angle, mirror=mirror)
    assert transformed["angle"] == angle % 360.0
    assert transformed["mirror"] is mirror
    assert transformed["expand"] is True
    assert (transformed["width"], transformed["height"]) != (896, 640)
    assert Path(registered["media_path"]).read_bytes() == original
    result = service.verify(output, None, "recover")
    assert result["watermark_status"] == "FOUND"
    assert result["recovered_content_ids"] == [registered["content_id"]]
    assert result["signature_valid"] == result["issuer_trusted"] == "PASS"
    assert result["content_hash_match"] == "FAIL"
    assert result["conclusion"] == "ORIGIN_HINT_ONLY"
    assert result["state_freshness"] == "NOT_CHECKED"
    hard = service.verify(output, registered["manifest_path"], "hard")
    assert hard["watermark_status"] == "NOT_CHECKED"
    assert hard["content_hash_match"] == "FAIL"
    assert hard["conclusion"] == "INDETERMINATE"
    (tmp_path / "verification.json").write_text(json.dumps({"transform": transformed, "recover": result, "hard": hard}, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("angle,mirror", [(37.5, False), (123.875, True)])
def test_api_arbitrary_rotation_and_unhinted_upload(demo_published, monkeypatch, tmp_path, angle, mirror):
    service, registered, original = demo_published
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    auth = {"Authorization": f"Bearer {service.initialize_admin()}"}
    response = client.post("/api/transform-preview", params={"operation": "rotate", "angle": angle, "mirror": str(mirror).lower()},
                           files={"file": ("opaque.bin", original, "image/png")}, headers=auth)
    assert response.status_code == 200
    result = response.json()
    assert result["transform"]["angle"] == angle
    assert result["transform"]["mirror"] is mirror
    assert result["transform"]["expand"] is True
    assert result["verification"]["recovered_content_ids"] == [registered["content_id"]]
    assert result["verification"]["content_hash_match"] == "FAIL"
    assert result["verification"]["conclusion"] == "ORIGIN_HINT_ONLY"
    assert result["verification"]["verification_mode"] == "full"
    assert result["verification"]["state_freshness"] == "UNKNOWN"
    for key in ("before_image", "after_image"):
        assert result[key].startswith("data:image/png;base64,")
        preview = Image.open(io.BytesIO(base64.b64decode(result[key].split(",", 1)[1])))
        assert max(preview.size) <= 512
    # The upload verification endpoint gets bytes alone, with no angle or transformation name.
    rotated = _bytes_image(original, "mirror_rotate" if mirror else "rotate", angle)
    upload = client.post("/api/verify", files={"file": ("input.bin", rotated, "image/png")})
    assert upload.status_code == 200
    assert upload.json()["recovered_content_ids"] == [registered["content_id"]]
    assert upload.json()["conclusion"] == "ORIGIN_HINT_ONLY"
    assert upload.json()["verification_mode"] == "full"
    assert upload.json()["state_freshness"] == "UNKNOWN"
    assert Path(registered["media_path"]).read_bytes() == original
    (tmp_path / "preview-result.json").write_text(json.dumps({k: v for k, v in result.items() if not k.endswith("_image")}, indent=2), encoding="utf-8")


def test_unmarked_rotation_demo_does_not_invent_provenance(demo_published, monkeypatch):
    service, _, _ = demo_published
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    auth = {"Authorization": f"Bearer {service.initialize_admin()}"}
    out = io.BytesIO()
    Image.new("RGB", (896, 640), (90, 140, 180)).save(out, "PNG")
    response = client.post("/api/transform-preview", params={"operation": "rotate", "angle": 72.375, "mirror": "true"},
                           files={"file": ("input.bin", out.getvalue(), "image/png")}, headers=auth)
    assert response.status_code == 200
    assert response.json()["verification"]["watermark_status"] == "NOT_FOUND"
    assert response.json()["verification"]["recovered_content_ids"] == []
    assert response.json()["verification"]["conclusion"] == "NO_PROVENANCE"


def test_arbitrary_rotation_preview_requires_admin(demo_published, monkeypatch):
    service, _, original = demo_published
    monkeypatch.setattr(api, "service", service)
    service.initialize_admin()
    client = TestClient(api.app)
    files = {"file": ("input.bin", original, "image/png")}
    params = {"operation": "rotate", "angle": 37.5, "mirror": "true"}
    assert client.post("/api/transform-preview", params=params, files=files).status_code == 401
    assert client.post("/api/transform-preview", params=params, files=files,
                       headers={"Authorization": "Bearer incorrect-test-token"}).status_code == 401


@pytest.mark.parametrize("angle", [True, False, float("nan"), float("inf"), float("-inf"), "37.5"])
def test_service_rejects_invalid_angle_without_writing(demo_published, tmp_path, angle):
    service, registered, _ = demo_published
    output = tmp_path / "invalid.bin"
    with pytest.raises(ValueError, match="有限数值"):
        service.transform(registered["media_path"], output, "rotate", angle=angle)
    assert not output.exists()


@pytest.mark.parametrize("angle", ["nan", "inf", "-inf", "true", ""])
def test_api_rejects_invalid_angle(demo_published, monkeypatch, angle):
    service, _, original = demo_published
    monkeypatch.setattr(api, "service", service)
    auth = {"Authorization": f"Bearer {service.initialize_admin()}"}
    response = TestClient(api.app).post("/api/transform-preview", params={"operation": "rotate", "angle": angle},
                                         files={"file": ("input.bin", original, "image/png")}, headers=auth)
    assert response.status_code in (400, 422)


def test_service_rejects_non_boolean_mirror(demo_published, tmp_path):
    service, registered, _ = demo_published
    with pytest.raises(ValueError, match="布尔值"):
        service.transform(registered["media_path"], tmp_path / "invalid.bin", "rotate", angle=13.5, mirror=1)


def test_transform_cannot_overwrite_published_bytes_or_hardlink(demo_published, tmp_path):
    service, registered, original = demo_published
    published = Path(registered["media_path"])
    with pytest.raises(ValueError, match="独立文件"):
        service.transform(published, published, "rotate", angle=37.5)
    assert published.read_bytes() == original
    alias = tmp_path / "published-hardlink.png"
    alias.hardlink_to(published)
    with pytest.raises(ValueError, match="独立文件"):
        service.transform(published, alias, "rotate", angle=37.5, mirror=True)
    assert published.read_bytes() == alias.read_bytes() == original
    hard = service.verify(published, registered["manifest_path"], "hard")
    assert hard["content_hash_match"] == "PASS"
    assert hard["conclusion"] == "VERIFIED_BINDING_ONLY"


def test_rotation_preserves_alpha_and_expands_complete_canvas(demo_published, tmp_path):
    service, _, _ = demo_published
    source = tmp_path / "transparent.png"
    image = Image.new("RGBA", (800, 600), (30, 90, 130, 255))
    image.putpixel((100, 100), (255, 0, 0, 128))
    image.save(source)
    output = tmp_path / "rotated.png"
    result = service.transform(source, output, "rotate", angle=37.5)
    with Image.open(output) as rotated:
        assert rotated.mode == "RGBA"
        assert rotated.getpixel((0, 0))[3] == 0
        assert rotated.width > 800 and rotated.height > 600
        assert rotated.size == (result["width"], result["height"])


def test_rotation_size_and_byte_limits(demo_published, tmp_path, monkeypatch):
    service, registered, original = demo_published
    monkeypatch.setattr(media, "MAX_PIXELS", 896 * 640)
    with pytest.raises(ValueError, match="展开后"):
        service.transform(registered["media_path"], tmp_path / "too-big.bin", "rotate", angle=45.25)
    monkeypatch.setattr(media, "MAX_PIXELS", 16 * 1024 * 1024)
    monkeypatch.setattr(media, "MAX_BYTES", len(original) - 1)
    with pytest.raises(ValueError, match="20MB"):
        service.transform(registered["media_path"], tmp_path / "too-big.bin", "rotate", angle=45.25)
    response = TestClient(api.app)
    monkeypatch.setattr(api, "service", service)
    auth = {"Authorization": f"Bearer {service.initialize_admin()}"}
    assert response.post("/api/transform-preview", params={"operation": "rotate", "angle": 45.25},
                         files={"file": ("input.bin", original, "image/png")}, headers=auth).status_code == 413


def test_existing_jpeg_and_resize_encoding_is_preserved(demo_published, tmp_path):
    service, registered, original = demo_published
    image = Image.open(io.BytesIO(original)).convert("RGB")
    for operation in ("jpeg-reencode", "resize"):
        expected = io.BytesIO()
        if operation == "jpeg-reencode":
            image.save(expected, "JPEG", quality=75)
        else:
            image.resize((image.width // 2, image.height // 2)).save(expected, "PNG")
        output = tmp_path / (operation + ".bin")
        service.transform(registered["media_path"], output, operation)
        assert output.read_bytes() == expected.getvalue()
