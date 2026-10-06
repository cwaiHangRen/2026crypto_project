"""统一图片/PDF媒体适配。PDF会逐页栅格化，水印发布后不保留原文字层。"""
from __future__ import annotations

import io
from typing import Any

from PIL import Image

from . import watermark

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 10
MAX_PIXELS = 16 * 1024 * 1024
PDF_DPI = 120


def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF-"): return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:2] == b"\xff\xd8": return "image/jpeg"
    raise ValueError("不支持的媒体格式")


def inspect_media(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES: raise ValueError("文件超过 20MB 限制")
    mt = sniff(data)
    if mt == "application/pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        if len(pdf) > MAX_PAGES: raise ValueError("PDF 页数超过 10 页限制")
        pages = []
        for i in range(len(pdf)):
            p = pdf[i]
            width, height = float(p.get_width()), float(p.get_height())
            scale = PDF_DPI / 72.0
            if width * scale * height * scale > MAX_PIXELS:
                raise ValueError("PDF 页面栅格化像素数超过 16MP 限制")
            pages.append({"width": int(width), "height": int(height)})
        return {"media_type": mt, "page_count": len(pdf), "pages": pages}
    im = Image.open(io.BytesIO(data)); im.load()
    return {"media_type": mt, "width": im.width, "height": im.height, "mode": im.mode}


def _pdf_pages(data: bytes):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    if len(pdf) > MAX_PAGES: raise ValueError("PDF 页数超过 10 页限制")
    for i in range(len(pdf)):
        page = pdf[i]
        scale = PDF_DPI / 72.0
        if page.get_width() * scale * page.get_height() * scale > MAX_PIXELS:
            raise ValueError("PDF 页面栅格化像素数超过 16MP 限制")
        bitmap = page.render(scale=scale)
        yield page, bitmap.to_pil()


def embed(data: bytes, content_id: str, media_type: str | None = None) -> tuple[bytes, dict[str, Any]]:
    detected = sniff(data)
    if media_type is not None and media_type != detected:
        raise ValueError(f"media_type 与文件头不一致: {media_type} != {detected}")
    mt = detected
    if mt in ("image/png", "image/jpeg"):
        return watermark.embed_image(data, content_id, "PNG" if mt.endswith("png") else "JPEG")
    if mt != "application/pdf": raise ValueError("不支持的 media_type")
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    import pypdfium2 as pdfium
    info = inspect_media(data)
    out = io.BytesIO(); c = canvas.Canvas(out)
    pages = 0
    watermark_profile = None
    for page, pil in _pdf_pages(data):
        bio, page_profile = watermark.embed_image(_pil_bytes(pil), content_id, "PNG")
        if watermark_profile is None:
            watermark_profile = page_profile
        pimg = Image.open(io.BytesIO(bio)); w, h = page.get_width(), page.get_height()
        c.setPageSize((w, h)); c.drawImage(ImageReader(pimg), 0, 0, width=w, height=h); c.showPage(); pages += 1
    c.save()
    return out.getvalue(), {"algorithm": "pdf-raster-pages", "page_count": pages, "dpi": PDF_DPI,
                            "text_layer": "discarded", "source": info,
                            "watermark_profile": watermark_profile or {}}


def _pil_bytes(im: Image.Image) -> bytes:
    b = io.BytesIO(); im.save(b, format="PNG"); return b.getvalue()


def detect(data: bytes, media_type: str | None = None) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
        return {"status": "NOT_FOUND", "content_ids": [], "pages": [],
                "details": {"error": "文件超过 20MB 限制"}}
    detected = sniff(data)
    if media_type is not None and media_type != detected:
        raise ValueError(f"media_type 与文件头不一致: {media_type} != {detected}")
    mt = detected
    if mt in ("image/png", "image/jpeg"): return watermark.detect_image(data)
    if mt != "application/pdf": raise ValueError("不支持的 media_type")
    ids: list[str] = []; details = []; errors = []
    try:
        for index, (_page, pil) in enumerate(_pdf_pages(data), 1):
            result = watermark.detect_image(_pil_bytes(pil)); details.append({"page": index, **result})
            ids.extend(result.get("content_ids", []))
    except Exception as exc:
        return {"status": "NOT_FOUND", "content_ids": [], "pages": details, "details": {"error": str(exc)}}
    unique = sorted(set(ids))
    status = "FOUND" if len(unique) == 1 and all(d.get("status") != "NOT_FOUND" for d in details) else ("CONFLICT" if len(unique) > 1 else "NOT_FOUND")
    return {"status": status, "content_ids": unique, "pages": details, "details": {"page_count": len(details), "dpi": PDF_DPI}}
