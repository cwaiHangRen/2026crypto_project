"""PDFium 实际栅格化与最终编码字节 gate 的回归测试。"""
from __future__ import annotations

import io
import json

import pytest

from gm_provenance.pdf_raster import PdfRasterConfig, iter_rendered_pages, process_pdf


CID = "fedcba9876543210fedcba9876543210"


def _pdf(pages: int = 1) -> bytes:
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(360, 240))
    for index in range(pages):
        c.setFont("Helvetica", 14)
        c.drawString(28, 180, f"PDF raster page {index + 1}")
        c.rect(30, 40, 130, 80, stroke=1, fill=0)
        c.showPage()
    c.save()
    return out.getvalue()


def test_single_page_pdf_raster_gate_and_structured_log(tmp_path):
    result = process_pdf(_pdf(), CID, output_dir=tmp_path)
    assert result.status == "PASS"
    assert result.registration_allowed is True
    assert result.page_count == 1
    page = result.pages[0]
    assert page.gate_pass is True
    assert page.recovered_content_ids == [CID]
    assert page.render_size == (600, 400)  # 360x240 points at fixed 120 DPI
    assert page.artifact_paths["rendered_page"].endswith("page-001-rendered.png")
    log = json.loads((tmp_path / "pdf-raster-log.json").read_text(encoding="utf-8"))
    assert log["original_pdf_sha256"] == result.original_pdf_sha256
    assert log["pages"][0]["final_sha256"] == page.as_dict()["final_sha256"]
    assert log["pages"][0]["verification"]["status"] == "NOT_CHECKED"


def test_pdfium_render_is_deterministic_and_uses_fixed_geometry():
    raw = _pdf()
    first = list(iter_rendered_pages(raw, PdfRasterConfig(dpi=120)))
    second = list(iter_rendered_pages(raw, PdfRasterConfig(dpi=120)))
    assert first[0].expected_size == (600, 400)
    assert first[0].rendered_bytes == second[0].rendered_bytes


def test_common_multi_page_pdf_is_verified_per_page(tmp_path):
    result = process_pdf(_pdf(3), CID, output_dir=tmp_path)
    assert result.status == "PASS"
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert all(p.gate_pass for p in result.pages)
    assert any("多页验证按页执行" in item for item in result.limitations)


def test_final_jpeg_reencode_and_service_verification_callback(tmp_path):
    calls = []

    def verify(final_bytes: bytes, page: int):
        calls.append((page, len(final_bytes)))
        return {"status": "VERIFIED_EXACT", "page": page, "sm3": "test-only"}

    cfg = PdfRasterConfig(final_format="JPEG", jpeg_quality=95)
    result = process_pdf(_pdf(), CID, config=cfg, output_dir=tmp_path, verify_fn=verify)
    assert result.status == "PASS"
    assert calls == [(1, len(result.pages[0].final_bytes))]
    assert result.pages[0].verification["status"] == "VERIFIED_EXACT"
    assert result.pages[0].artifact_paths["final_encoded"].endswith(".jpg")


def test_final_png_resize_keeps_deterministic_size_and_gate(tmp_path):
    cfg = PdfRasterConfig(final_format="PNG", resize=(480, 320))
    result = process_pdf(_pdf(), CID, config=cfg, output_dir=tmp_path)
    assert result.status == "PASS"
    assert result.pages[0].final_size == (480, 320)
    assert result.pages[0].recovered_content_ids == [CID]


def test_gate_failure_is_reported_when_final_detector_does_not_recover(monkeypatch, tmp_path):
    from gm_provenance import watermark

    monkeypatch.setattr(watermark, "detect_image", lambda data: {
        "status": "NOT_FOUND", "content_ids": [], "details": {"test": True}
    })
    result = process_pdf(_pdf(), CID, output_dir=tmp_path)
    assert result.status == "FAILED_GATE"
    assert result.registration_allowed is False
    assert result.pages[0].gate_pass is False
    assert result.pages[0].recovered_content_ids == []

