import io
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from gm_provenance import media
from gm_provenance.service import ProvenanceService
from tests.helpers import provision_publisher


CID = "0123456789abcdef0123456789abcdef"


def _source(path: Path) -> None:
    Image.new("RGB", (768, 768), (230, 230, 230)).save(path, "PNG")


def _pdf() -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(360, 240))
    c.drawString(30, 180, "publish self-check")
    c.showPage()
    c.save()
    return out.getvalue()


def _service(tmp_path: Path):
    service = ProvenanceService(tmp_path / "data")
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    provision_publisher(service, private, public, "self-check-pass")
    return service, private


def test_register_self_check_matches_the_stored_final_bytes(tmp_path: Path):
    source = tmp_path / "source.png"
    _source(source)
    service, private = _service(tmp_path)

    result = service.register(source, private, "self-check-pass")
    stored = Path(result["media_path"]).read_bytes()
    observed = media.detect(stored, result["media_type"])

    assert observed["status"] == "FOUND"
    assert observed["content_ids"] == [result["content_id"]]
    assert result["manifest"]["body"]["watermark_profile"]["publish_self_check"]["status"] == "PASS"


def test_self_check_rejects_no_id_wrong_id_and_conflict(monkeypatch):
    cases = [
        {"status": "NOT_FOUND", "content_ids": []},
        {"status": "FOUND", "content_ids": ["fedcba9876543210fedcba9876543210"]},
        {"status": "CONFLICT", "content_ids": [CID, "fedcba9876543210fedcba9876543210"]},
    ]
    for observed in cases:
        monkeypatch.setattr(media, "detect", lambda *_args, observed=observed: observed)
        with pytest.raises(media.WatermarkSelfCheckError) as exc:
            media.assert_publishable(b"\x89PNG\r\n\x1a\n", CID, "image/png")
        assert exc.value.diagnostics["recovered_content_ids"] == observed["content_ids"]


def test_register_self_check_failure_has_no_registry_side_effect(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.png"
    _source(source)
    service, private = _service(tmp_path)
    before = service.registry.status()

    def reject(*_args, **_kwargs):
        raise media.WatermarkSelfCheckError("injected self-check failure", {"stage": "test"})

    monkeypatch.setattr(media, "assert_publishable", reject)
    with pytest.raises(media.WatermarkSelfCheckError):
        service.register(source, private, "self-check-pass")

    after = service.registry.status()
    assert after["sequence"] == before["sequence"]
    assert service.registry.list_contents() == []


def test_derive_self_check_failure_has_no_new_version(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.png"
    _source(source)
    service, private = _service(tmp_path)
    root = service.register(source, private, "self-check-pass")
    before = service.registry.status()

    def reject(*_args, **_kwargs):
        raise media.WatermarkSelfCheckError("injected self-check failure", {"stage": "test"})

    monkeypatch.setattr(media, "assert_publishable", reject)
    with pytest.raises(media.WatermarkSelfCheckError):
        service.derive(root["content_id"], private, "self-check-pass", "resize")

    after = service.registry.status()
    assert after["sequence"] == before["sequence"]
    assert len(service.registry.history(root["content_id"])) == 1


def test_pdf_self_check_uses_current_page_scope(tmp_path: Path):
    marked, _profile = media.embed(_pdf(), CID, "application/pdf")
    result = media.assert_publishable(marked, CID, "application/pdf")
    assert result["status"] == "PASS"
    assert result["page_count"] == 1
