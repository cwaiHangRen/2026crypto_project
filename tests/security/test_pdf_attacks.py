"""PDF 结构攻击与边界测试。

这些测试只验证媒体层的可观察结果，不把 PDF 的文字层或对象身份当作凭证：发布
流程会把每页栅格化，因此页删除、重排和替换都应通过逐页 watermark 结果反映出来。
截图测试明确区分了 PDFium 的实际渲染和 Pillow 的模拟截图缩放。
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from gm_provenance import media


CID_A = "fedcba9876543210fedcba9876543210"
CID_B = "0123456789abcdef0123456789abcdef"


def _source_pdf(pages: int = 3, pagesize: tuple[int, int] = (360, 240)) -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=pagesize)
    for index in range(pages):
        # Keep source artwork low-frequency enough for the current DCT baseline.
        c.setFont("Helvetica", 10)
        c.drawString(20, pagesize[1] - 35, f"Page {index + 1}")
        c.showPage()
    c.save()
    return out.getvalue()


def _rewrite_pages(data: bytes, order: list[int], replacements: dict[int, bytes] | None = None) -> bytes:
    replacements = replacements or {}
    original = PdfReader(io.BytesIO(data))
    replacement_readers = {index: PdfReader(io.BytesIO(value)) for index, value in replacements.items()}
    writer = PdfWriter()
    for output_index, source_index in enumerate(order):
        reader = replacement_readers.get(output_index, original)
        page = reader.pages[source_index] if reader is original else reader.pages[0]
        writer.add_page(page)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def _assert_page_scope(result: dict, expected_count: int, expected_status: str) -> None:
    assert result["status"] == expected_status
    assert result["details"]["page_count"] == expected_count
    assert [page["page"] for page in result["pages"]] == list(range(1, expected_count + 1))


def test_pdf_page_delete_and_reorder_keep_page_scope(tmp_path: Path):
    raw = _source_pdf(3)
    marked, _ = media.embed(raw, CID_A, "application/pdf")

    deleted = _rewrite_pages(marked, [0, 2])
    deleted_result = media.detect(deleted, "application/pdf")
    _assert_page_scope(deleted_result, 2, "FOUND")
    assert deleted_result["content_ids"] == [CID_A]

    reordered = _rewrite_pages(marked, [2, 0, 1])
    reordered_result = media.detect(reordered, "application/pdf")
    _assert_page_scope(reordered_result, 3, "FOUND")
    assert all(page["status"] == "FOUND" for page in reordered_result["pages"])


def test_pdf_page_replacement_is_not_found_at_replaced_page(tmp_path: Path):
    raw = _source_pdf(3)
    marked, _ = media.embed(raw, CID_A, "application/pdf")
    replaced = _rewrite_pages(marked, [0, 1, 2], {1: raw})
    result = media.detect(replaced, "application/pdf")
    _assert_page_scope(result, 3, "NOT_FOUND")
    assert result["content_ids"] == [CID_A]
    assert result["pages"][1]["status"] == "NOT_FOUND"
    assert result["pages"][0]["status"] == result["pages"][2]["status"] == "FOUND"


def test_pdf_multiple_content_ids_are_conflict_with_page_scope(tmp_path: Path):
    raw = _source_pdf(3)
    marked_a, _ = media.embed(raw, CID_A, "application/pdf")
    marked_b, _ = media.embed(raw, CID_B, "application/pdf")
    mixed = _rewrite_pages(marked_a, [0, 1, 2], {2: marked_b})
    result = media.detect(mixed, "application/pdf")
    _assert_page_scope(result, 3, "CONFLICT")
    assert result["content_ids"] == sorted([CID_A, CID_B])
    assert result["pages"][2]["content_ids"] == [CID_B]


def test_pdf_unmarked_pages_return_not_found_with_page_scope():
    result = media.detect(_source_pdf(2), "application/pdf")
    _assert_page_scope(result, 2, "NOT_FOUND")
    assert result["content_ids"] == []
    assert all(page["status"] == "NOT_FOUND" for page in result["pages"])


def test_pdf_actual_render_screenshot_is_explicitly_image_scope():
    """实际截图：PDFium 渲染的页面像素直接编码为 PNG。"""
    marked, _ = media.embed(_source_pdf(1), CID_A, "application/pdf")
    page_image = pdfium.PdfDocument(marked)[0].render(scale=2).to_pil()
    png = io.BytesIO()
    page_image.save(png, "PNG")
    result = media.detect(png.getvalue(), "image/png")
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID_A]
    # A screenshot is an image artifact: there is intentionally no PDF page scope.
    assert "pages" not in result


def test_pdf_simulated_screenshot_is_explicitly_resized_viewport():
    """模拟截图：对实际渲染像素做 Pillow viewport 缩放，不伪称系统截图。"""
    marked, _ = media.embed(_source_pdf(1), CID_A, "application/pdf")
    rendered = pdfium.PdfDocument(marked)[0].render(scale=2).to_pil()
    simulated = rendered.resize((360, 240), Image.Resampling.LANCZOS)
    png = io.BytesIO()
    simulated.save(png, "PNG")
    result = media.detect(png.getvalue(), "image/png")
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID_A]
    assert "pages" not in result


def test_pdf_limits_reject_oversized_page_and_page_count():
    too_many = _source_pdf(media.MAX_PAGES + 1)
    try:
        media.inspect_media(too_many)
    except ValueError as exc:
        assert "页数" in str(exc)
    else:
        raise AssertionError("超过页数上限的 PDF 未被拒绝")

    oversized_page = _source_pdf(1, pagesize=(3000, 3000))
    try:
        media.inspect_media(oversized_page)
    except ValueError as exc:
        assert "像素" in str(exc)
    else:
        raise AssertionError("inspect_media 未拒绝超过栅格化像素上限的页面")
    try:
        media.embed(oversized_page, CID_A, "application/pdf")
    except ValueError as exc:
        assert "像素" in str(exc)
    else:
        raise AssertionError("超过像素上限的页面未被拒绝")
    assert media.detect(oversized_page, "application/pdf")["status"] == "NOT_FOUND"


def test_pdf_limit_rejects_over_20mb_before_parsing():
    oversized = b"%PDF-1.7\n" + b"0" * (media.MAX_BYTES + 1)
    try:
        media.inspect_media(oversized)
    except ValueError as exc:
        assert "20MB" in str(exc)
    else:
        raise AssertionError("超过 20MB 的输入未被拒绝")
    result = media.detect(oversized, "application/pdf")
    assert result["status"] == "NOT_FOUND"
    assert result["pages"] == []
