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


class WatermarkSelfCheckError(ValueError):
    """最终发布字节未恢复本次 ContentID。"""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


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
        try:
            if len(pdf) > MAX_PAGES: raise ValueError("PDF 页数超过 10 页限制")
            pages = []
            for i in range(len(pdf)):
                p = pdf[i]
                try:
                    width, height = float(p.get_width()), float(p.get_height())
                    scale = PDF_DPI / 72.0
                    if width * scale * height * scale > MAX_PIXELS:
                        raise ValueError("PDF 页面栅格化像素数超过 16MP 限制")
                    pages.append({"width": int(width), "height": int(height)})
                finally:
                    close_page = getattr(p, "close", None)
                    if close_page:
                        close_page()
            return {"media_type": mt, "page_count": len(pdf), "pages": pages}
        finally:
            close_pdf = getattr(pdf, "close", None)
            if close_pdf:
                close_pdf()
    im = Image.open(io.BytesIO(data)); im.load()
    return {"media_type": mt, "width": im.width, "height": im.height, "mode": im.mode}


def _pdf_pages(data: bytes):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > MAX_PAGES: raise ValueError("PDF 页数超过 10 页限制")
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = None
            pil = None
            try:
                scale = PDF_DPI / 72.0
                if page.get_width() * scale * page.get_height() * scale > MAX_PIXELS:
                    raise ValueError("PDF 页面栅格化像素数超过 16MP 限制")
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
                yield page, pil
            finally:
                if pil is not None:
                    pil.close()
                close_bitmap = getattr(bitmap, "close", None) if bitmap is not None else None
                if close_bitmap:
                    close_bitmap()
                close_page = getattr(page, "close", None)
                if close_page:
                    close_page()
    finally:
        close_pdf = getattr(pdf, "close", None)
        if close_pdf:
            close_pdf()


def embed(data: bytes, content_id: str, media_type: str | None = None,
          watermark_profile: str = "watermark-v1") -> tuple[bytes, dict[str, Any]]:
    """Publish a media object with an explicit watermark embedding profile.

    Existing callers retain the v1 default.  The residual candidate is only
    enabled for raster images; PDF publishing deliberately remains on v1 so a
    helper default change cannot silently alter the established PDF path.
    """
    if not isinstance(watermark_profile, str):
        raise ValueError("watermark_profile 必须是字符串")
    if watermark_profile in ("watermark-v1", "dct-difference-v1", "v1"):
        profile_name = "watermark-v1"
    elif watermark_profile in ("watermark-v2-residual", "v2-residual", "watermark-v2"):
        profile_name = "watermark-v2-residual"
    elif watermark_profile in ("watermark-v2-feedback", "v2-feedback", "feedback"):
        profile_name = "watermark-v2-feedback"
    else:
        raise ValueError("不支持的 watermark_profile")
    detected = sniff(data)
    if media_type is not None and media_type != detected:
        raise ValueError(f"media_type 与文件头不一致: {media_type} != {detected}")
    mt = detected
    if mt in ("image/png", "image/jpeg"):
        fmt = "PNG" if mt.endswith("png") else "JPEG"
        if profile_name == "watermark-v2-residual":
            return watermark.embed_image_residual(data, content_id, fmt)
        if profile_name == "watermark-v2-feedback":
            from . import watermark_feedback
            return watermark_feedback.embed_image_feedback(data, content_id, fmt)
        return watermark.embed_image(data, content_id, fmt)
    if mt != "application/pdf": raise ValueError("不支持的 media_type")
    if profile_name != "watermark-v1":
        raise ValueError("PDF 发布当前固定使用 watermark-v1")
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    import pypdfium2 as pdfium
    info = inspect_media(data)
    out = io.BytesIO(); c = canvas.Canvas(out)
    pages = 0
    watermark_profile = None
    for page, pil in _pdf_pages(data):
        try:
            # Keep the PDF baseline explicit even if the image helper gains a
            # different default in a future release.
            bio, page_profile = watermark.embed_image(_pil_bytes(pil), content_id, "PNG")
            if watermark_profile is None:
                watermark_profile = page_profile
            pimg = Image.open(io.BytesIO(bio)); w, h = page.get_width(), page.get_height()
            try:
                c.setPageSize((w, h)); c.drawImage(ImageReader(pimg), 0, 0, width=w, height=h); c.showPage(); pages += 1
            finally:
                pimg.close()
        finally:
            # _pdf_pages also closes these after the generator resumes; close
            # here keeps resources bounded even if the consumer raises.
            pil.close()
    c.save()
    return out.getvalue(), {"algorithm": "pdf-raster-pages", "page_count": pages, "dpi": PDF_DPI,
                            "text_layer": "discarded", "source": info,
                            "watermark_profile": watermark_profile or {},
                            "embed_profile": "watermark-v1"}


def _pil_bytes(im: Image.Image) -> bytes:
    b = io.BytesIO(); im.save(b, format="PNG"); return b.getvalue()


def detect(data: bytes, media_type: str | None = None,
           decoder_profile: str = "dct-difference-v1") -> dict[str, Any]:
    """盲检测媒体水印。

    ``dct-difference-v1`` 的普通验证包含旋转/镜像与边框几何恢复；
    ``decoder-v2-sync`` 与 ``decoder-v3-sync`` 只对图片启用有界裁剪同步。
    发布 gate 在 direct_detection 上下文中保持历史单方向 D0，
    不接受传播校正或同步搜索结果作为最终文件自检依据。
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
        return {"status": "NOT_FOUND", "content_ids": [], "pages": [],
                "details": {"error": "文件超过 20MB 限制"}}
    aliases = {
        "watermark-v1": "dct-difference-v1",
        "v1": "dct-difference-v1",
        "decoder-v2-sync": "decoder-v2-sync",
        "decoder-v3-sync": "decoder-v3-sync",
    }
    profile = aliases.get(decoder_profile, decoder_profile)
    if profile not in ("dct-difference-v1", "decoder-v2-sync", "decoder-v3-sync"):
        raise ValueError("不支持的 decoder_profile")
    detected = sniff(data)
    if media_type is not None and media_type != detected:
        raise ValueError(f"media_type 与文件头不一致: {media_type} != {detected}")
    mt = detected
    if mt in ("image/png", "image/jpeg"):
        if profile in ("decoder-v2-sync", "decoder-v3-sync"):
            from . import watermark_sync
            return watermark_sync.detect_image_sync(data, {"profile": profile})
        return watermark.detect_image(data)
    if mt != "application/pdf": raise ValueError("不支持的 media_type")
    if profile != "dct-difference-v1":
        return {"status": "UNSUPPORTED", "content_ids": [], "pages": [],
                "details": {"decoder_profile": profile, "checked_scope": "pdf", "reason": "同步原型当前只支持图片"}}
    ids: list[str] = []; details = []; errors = []
    try:
        for index, (_page, pil) in enumerate(_pdf_pages(data), 1):
            try:
                result = watermark.detect_image(_pil_bytes(pil)); details.append({"page": index, **result})
                ids.extend(result.get("content_ids", []))
            finally:
                pil.close()
    except Exception as exc:
        return {"status": "NOT_FOUND", "content_ids": [], "pages": details, "details": {
            "error": str(exc),
            "current_page_count": len(details),
            "page_source_hint": "NOT_CHECKED",
            "whole_file_digest": "NOT_CHECKED",
            "original_page_identity": "NOT_CHECKED",
        }}
    unique = sorted(set(ids))
    status = "FOUND" if len(unique) == 1 and all(d.get("status") != "NOT_FOUND" for d in details) else ("CONFLICT" if len(unique) > 1 else "NOT_FOUND")
    return {"status": status, "content_ids": unique, "pages": details, "details": {
        "page_count": len(details), "current_page_count": len(details), "dpi": PDF_DPI,
        # Detection observes the current rendered pages only.  It has no
        # original page manifest, file digest, or stable page identity.
        "page_source_hint": "watermark ContentID is a source hint for current rendered pages",
        "whole_file_digest": "NOT_CHECKED",
        "original_page_identity": "NOT_CHECKED",
    }}


def assert_publishable(data: bytes, content_id: str, media_type: str | None = None) -> dict[str, Any]:
    """Gate publication on blind recovery from the exact final bytes.

    The detector receives only the encoded media and public decoder settings;
    the expected ContentID is compared here, outside the detector.  This
    function never writes registry state or changes ``data``.
    """
    if not isinstance(content_id, str) or len(content_id) != 32:
        raise ValueError("content_id 必须是 32 位小写十六进制字符串")
    try:
        int(content_id, 16)
    except ValueError as exc:
        raise ValueError("content_id 必须是 32 位小写十六进制字符串") from exc
    if content_id != content_id.lower():
        raise ValueError("content_id 必须是 32 位小写十六进制字符串")
    try:
        detected_type = sniff(data)
        if media_type is not None and media_type != detected_type:
            raise ValueError(f"media_type 与文件头不一致: {media_type} != {detected_type}")
        # Publishing must keep the legacy direct D0 gate. Rotation recovery
        # is for propagation copies, not a way to relax final-byte self-check.
        with watermark.direct_detection():
            observed = detect(data, detected_type)
    except Exception as exc:
        diagnostics = {
            "stage": "final_media_decode_or_detect",
            "content_id": content_id,
            "media_type": media_type,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        raise WatermarkSelfCheckError("最终编码文件无法完成水印自检", diagnostics) from exc
    recovered = observed.get("content_ids") or []
    diagnostics = {
        "stage": "final_media_blind_self_check",
        "content_id": content_id,
        "media_type": detected_type,
        "detector_status": observed.get("status"),
        "recovered_content_ids": list(recovered),
        "page_count": observed.get("details", {}).get("current_page_count", observed.get("details", {}).get("page_count")),
        "detector_details": observed.get("details", {}),
    }
    if observed.get("status") != "FOUND" or recovered != [content_id]:
        raise WatermarkSelfCheckError("最终编码文件未恢复本次 ContentID，拒绝发布", diagnostics)
    return {"status": "PASS", "recovered_content_id": content_id, "media_type": detected_type,
            "detector_status": observed.get("status"), "page_count": diagnostics.get("page_count")}
