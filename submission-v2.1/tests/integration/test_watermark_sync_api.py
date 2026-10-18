import io
import inspect
import json
import base64
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from gm_provenance import media
from gm_provenance.service import ProvenanceService
from gm_provenance.watermark import embed_image
from tests.helpers import provision_publisher


CID = "0123456789abcdef0123456789abcdef"


def _marked() -> bytes:
    image = Image.new("RGB", (700, 906), (230, 230, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 100, 620, 800), fill=(31, 78, 121))
    source = io.BytesIO()
    image.save(source, "PNG")
    return embed_image(source.getvalue(), CID, "PNG")[0]


def _crop(data: bytes, keep: float = 0.95, offset: tuple[float, float] = (0.0, 0.0)) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    scale = keep ** 0.5
    width, height = round(image.width * scale), round(image.height * scale)
    left = max(0, min(image.width - width, int((image.width - width) / 2 + offset[0] * image.width)))
    top = max(0, min(image.height - height, int((image.height - height) / 2 + offset[1] * image.height)))
    out = io.BytesIO()
    image.crop((left, top, left + width, top + height)).save(out, "PNG")
    return out.getvalue()


def test_sync_is_independent_decoder_and_recovers_bounded_crop():
    marked = _marked()
    cropped = _crop(marked, 0.95, (0.02, 0.0))

    d0 = media.detect(cropped, "image/png")
    sync = media.detect(cropped, "image/png", decoder_profile="decoder-v2-sync")

    assert d0["status"] == "NOT_FOUND"
    assert sync["status"] == "FOUND"
    assert sync["content_ids"] == [CID]
    assert sync["details"]["decoder_profile"] == "decoder-v2-sync"
    assert sync["details"]["search_dct_mode"] == "batch"
    assert sync["details"]["checked_scope"] == "watermark-only"
    assert sync["details"]["reconstruction_rule"]


def test_sync_subprocess_accepts_only_upload_bytes_for_non_grid_crop():
    """The recovery result must come from supplied bytes, without path/registry state."""
    marked = _marked()
    # Area ratio is supported, while both offsets fall between the public grid points.
    cropped = _crop(marked, 0.95, (0.017, -0.011))
    script = (
        "import base64,json,sys; "
        "from gm_provenance.watermark_sync import detect_image_sync; "
        "r=detect_image_sync(base64.b64decode(sys.stdin.buffer.read())); "
        "print(json.dumps({k:r[k] for k in ('status','content_ids')}, sort_keys=True))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        input=base64.b64encode(cropped), capture_output=True, check=True,
        env=env,
    )
    observed = json.loads(proc.stdout)
    assert observed == {"content_ids": [CID], "status": "FOUND"}


def test_sync_does_not_change_publish_gate(monkeypatch):
    marked = _marked()
    called = {"sync": False}

    def forbidden(*_args, **_kwargs):
        called["sync"] = True
        raise AssertionError("发布 gate 不得调用 D_sync")

    monkeypatch.setattr("gm_provenance.watermark_sync.detect_image_sync", forbidden)
    result = media.assert_publishable(marked, CID, "image/png")
    assert result["status"] == "PASS"
    assert called["sync"] is False


def test_sync_api_service_reports_incomplete_search_without_hash_claim(tmp_path: Path):
    marked = _marked()
    cropped = _crop(marked, 0.90)
    path = tmp_path / "renamed-upload.png"
    path.write_bytes(cropped)
    result = ProvenanceService(tmp_path / "data").verify(path, mode="recover", decoder_profile="decoder-v2-sync")

    assert result["decoder_profile"] == "decoder-v2-sync"
    assert result["watermark_status"] == "FOUND"
    assert result["recovered_content_ids"] == [CID]
    assert result["checked_scope"] == "watermark-only"
    assert result["content_hash_match"] == "NOT_CHECKED"
    assert result["conclusion"] == "ORIGIN_HINT_ONLY"


def test_sync_public_interface_has_no_truth_or_path_parameter():
    names = list(inspect.signature(__import__("gm_provenance.watermark_sync", fromlist=["detect_image_sync"]).detect_image_sync).parameters)
    assert names == ["data", "config"]


def test_sync_same_bytes_ignore_filename_and_metadata(tmp_path: Path):
    marked = _marked()
    first = tmp_path / "first-name.png"
    second = tmp_path / "renamed-name.png"
    first.write_bytes(marked)
    second.write_bytes(marked)
    assert media.detect(first.read_bytes(), "image/png") == media.detect(second.read_bytes(), "image/png")

    image = Image.open(io.BytesIO(marked)).convert("RGB")
    without_metadata = io.BytesIO()
    image.save(without_metadata, "PNG", pnginfo=None)
    result = media.detect(without_metadata.getvalue(), "image/png", decoder_profile="decoder-v2-sync")
    assert result["content_ids"] == [CID]


def test_sync_same_size_different_content_does_not_reuse_another_answer():
    first = _marked()
    other_cid = "fedcba9876543210fedcba9876543210"
    source = Image.new("RGB", (700, 906), (40, 180, 70))
    original = io.BytesIO(); source.save(original, "PNG")
    other = embed_image(original.getvalue(), other_cid, "PNG")[0]
    first_result = media.detect(first, "image/png", decoder_profile="decoder-v2-sync")
    other_result = media.detect(other, "image/png", decoder_profile="decoder-v2-sync")
    assert first_result["content_ids"] == [CID]
    assert other_result["content_ids"] == [other_cid]


def test_hard_mode_does_not_enter_sync(monkeypatch, tmp_path: Path):
    marked = _marked()
    path = tmp_path / "marked.png"
    path.write_bytes(marked)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hard verification must not call a decoder")

    monkeypatch.setattr("gm_provenance.media.detect", forbidden)
    result = ProvenanceService(tmp_path / "data").verify(path, mode="hard", decoder_profile="decoder-v2-sync")
    assert result["watermark_status"] == "NOT_CHECKED"
    assert result["decoder_profile"] == "decoder-v2-sync"


def test_sync_full_with_real_signed_manifest_binds_actual_upload_hash(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (700, 906), (230, 230, 230)).save(source)
    service = ProvenanceService(tmp_path / "data")
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    provision_publisher(service, private, public, "sync-pass-123")
    published = service.register(source, private, "sync-pass-123")
    cropped = _crop(Path(published["media_path"]).read_bytes(), 0.90)
    media_path = tmp_path / "cropped-upload.png"
    media_path.write_bytes(cropped)
    result = service.verify(media_path, published["manifest_path"], mode="full", decoder_profile="decoder-v2-sync")
    assert result["signature_valid"] == "PASS"
    assert result["content_hash_match"] == "FAIL"
    assert result["watermark_status"] == "FOUND"
    assert result["conclusion"] == "ORIGIN_HINT_ONLY"
    assert not result["conclusion"].startswith("VERIFIED")


def test_sync_sidecar_watermark_mismatch_is_rejected(tmp_path: Path):
    service = ProvenanceService(tmp_path / "data")
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    provision_publisher(service, private, public, "sync-pass-123")
    first, second = [], []
    for out in (first, second):
        src = tmp_path / f"source-{len(first)+len(second)}.png"
        Image.new("RGB", (700, 906), (40 + len(first) * 40, 120, 180)).save(src)
        out.append(service.register(src, private, "sync-pass-123"))
    wrong_media = Path(second[0]["media_path"])
    result = service.verify(wrong_media, first[0]["manifest_path"], mode="full", decoder_profile="decoder-v2-sync")
    assert result["watermark_status"] == "FOUND"
    assert "sidecar_watermark_conflict" in result["reasons"]
    assert result["conclusion"] == "REJECTED"
