"""确定性的 PDF 页面栅格化、截图重编码与盲恢复流程。

本模块只负责媒体层：PDF 页面通过 PDFium 按固定 DPI 渲染为 RGB/L 图像，
然后使用既有 ``watermark-v1`` 嵌入水印。页面最终编码字节会再次交给盲检测器，
只有检测结果严格等于本次 ContentID 才通过页面 gate。来源凭证（SM3/Manifest/
SM2/版本链/撤销）由调用方通过 ``verify_fn`` 接入；本模块不会把水印当作凭证。

所有写出的文件均为确定性 PNG/JPEG 图像（不依赖 GUI 截图），结构化日志只保存
路径、摘要和验证结果，不把二进制媒体塞进 JSON。PDFium、Pillow 和 watermark
均延迟导入，以保持原有图片接口在最小安装环境中的行为。
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


PDF_RASTER_DPI = 120
PDF_MAX_PAGES = 10
PDF_MAX_PIXELS = 16 * 1024 * 1024
PDF_MAX_BYTES = 20 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode_image(image: Any, fmt: str, *, jpeg_quality: int = 95) -> bytes:
    """Encode without timestamps/metadata so repeated runs have stable bytes."""
    out = io.BytesIO()
    fmt = fmt.upper()
    if fmt in ("JPG", "JPEG"):
        image.save(out, format="JPEG", quality=int(jpeg_quality), optimize=False,
                   progressive=False, subsampling=0)
    elif fmt == "PNG":
        image.save(out, format="PNG", optimize=False)
    else:
        raise ValueError("final_format 仅支持 PNG 或 JPEG")
    return out.getvalue()


def _normalize_image(image: Any, color_space: str, *, background: tuple[int, int, int]) -> Any:
    """Flatten PDFium alpha against a fixed background and select RGB/L."""
    from PIL import Image

    mode = str(color_space).upper()
    if mode not in ("RGB", "L"):
        raise ValueError("color_space 仅支持 RGB 或 L")
    if "A" in image.getbands():
        rgba = image.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, tuple(int(x) for x in background) + (255,))
        image = Image.alpha_composite(bg, rgba)
    rgb = image.convert("RGB")
    return rgb if mode == "RGB" else rgb.convert("L")


@dataclass(frozen=True)
class PdfRasterConfig:
    """固定的 PDF 页面渲染参数。"""

    dpi: int = PDF_RASTER_DPI
    color_space: str = "RGB"
    background: tuple[int, int, int] = (255, 255, 255)
    max_pages: int = PDF_MAX_PAGES
    max_pixels: int = PDF_MAX_PIXELS
    max_bytes: int = PDF_MAX_BYTES
    final_format: str = "PNG"
    jpeg_quality: int = 95
    resize: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if int(self.dpi) <= 0:
            raise ValueError("dpi 必须为正整数")
        if int(self.max_pages) <= 0 or int(self.max_pixels) <= 0:
            raise ValueError("max_pages/max_pixels 必须为正整数")
        if len(tuple(self.background)) != 3 or any(not 0 <= int(x) <= 255 for x in self.background):
            raise ValueError("background 必须是三个 0..255 整数")
        if self.resize is not None and (len(self.resize) != 2 or any(int(x) <= 0 for x in self.resize)):
            raise ValueError("resize 必须为正的 (width, height)")
        if str(self.color_space).upper() not in ("RGB", "L"):
            raise ValueError("color_space 仅支持 RGB 或 L")
        fmt = str(self.final_format).upper()
        if fmt not in ("PNG", "JPEG", "JPG"):
            raise ValueError("final_format 仅支持 PNG 或 JPEG")
        if not 1 <= int(self.jpeg_quality) <= 100:
            raise ValueError("jpeg_quality 必须在 1..100")


@dataclass
class PdfRenderedPage:
    page_number: int
    width_pt: float
    height_pt: float
    expected_size: tuple[int, int]
    image: Any
    rendered_bytes: bytes = b""

    def as_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        out = {
            "page": self.page_number,
            "size_pt": [self.width_pt, self.height_pt],
            "size_px": list(self.expected_size),
            "rendered_sha256": _sha256(self.rendered_bytes),
            "rendered_bytes": len(self.rendered_bytes),
        }
        if include_bytes:
            out["rendered_data"] = self.rendered_bytes
        return out


@dataclass
class PdfPageResult:
    page_number: int
    rendered_bytes: bytes
    final_bytes: bytes
    detector: dict[str, Any]
    recovered_content_ids: list[str]
    gate_pass: bool
    verification: dict[str, Any]
    artifact_paths: dict[str, str] = field(default_factory=dict)
    render_size: tuple[int, int] = (0, 0)
    final_size: tuple[int, int] = (0, 0)

    def as_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "page": self.page_number,
            "rendered_sha256": _sha256(self.rendered_bytes),
            "rendered_bytes": len(self.rendered_bytes),
            "final_sha256": _sha256(self.final_bytes),
            "final_bytes": len(self.final_bytes),
            "render_size": list(self.render_size),
            "final_size": list(self.final_size),
            "detector_status": self.detector.get("status"),
            "recovered_content_ids": list(self.recovered_content_ids),
            "gate_pass": bool(self.gate_pass),
            "verification": self.verification,
            "artifact_paths": dict(self.artifact_paths),
        }
        if include_bytes:
            out["rendered_data"] = self.rendered_bytes
            out["final_data"] = self.final_bytes
        return out


@dataclass
class PdfRasterResult:
    original_pdf_sha256: str
    original_pdf_path: str | None
    page_count: int
    content_id: str
    config: PdfRasterConfig
    pages: list[PdfPageResult]
    status: str
    registration_allowed: bool
    limitations: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    log_path: str | None = None

    def as_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        return {
            "status": self.status,
            "registration_allowed": self.registration_allowed,
            "original_pdf_sha256": self.original_pdf_sha256,
            # Logs are submission material; keep paths relative so local
            # usernames and machine-specific roots cannot leak.
            "original_pdf_path": Path(self.original_pdf_path).name if self.original_pdf_path else None,
            "content_id": self.content_id,
            "page_count": self.page_count,
            "config": {
                "dpi": self.config.dpi,
                "color_space": self.config.color_space,
                "background": list(self.config.background),
                "final_format": self.config.final_format.upper(),
                "jpeg_quality": self.config.jpeg_quality,
                "resize": list(self.config.resize) if self.config.resize else None,
            },
            "pages": [p.as_dict(include_bytes=include_bytes) for p in self.pages],
            "limitations": list(self.limitations),
            "elapsed_ms": self.elapsed_ms,
            "log_path": Path(self.log_path).name if self.log_path else None,
        }


def iter_rendered_pages(data: bytes, config: PdfRasterConfig | None = None) -> Iterator[PdfRenderedPage]:
    """Yield deterministic RGB/L pages from a PDF, closing PDFium resources."""
    config = config or PdfRasterConfig()
    if not isinstance(data, (bytes, bytearray)) or len(data) > config.max_bytes:
        raise ValueError("PDF 为空或超过大小限制")
    if not bytes(data).startswith(b"%PDF-"):
        raise ValueError("输入不是 PDF")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - exercised in minimal envs
        raise RuntimeError("PDF 栅格化需要 pypdfium2") from exc
    document = pdfium.PdfDocument(bytes(data))
    try:
        count = len(document)
        if count > config.max_pages:
            raise ValueError(f"PDF 页数超过 {config.max_pages} 页限制")
        scale = float(config.dpi) / 72.0
        for index in range(count):
            page = document[index]
            bitmap = None
            try:
                width_pt, height_pt = float(page.get_width()), float(page.get_height())
                expected = (max(1, int(round(width_pt * scale))), max(1, int(round(height_pt * scale))))
                if expected[0] * expected[1] > config.max_pixels:
                    raise ValueError(f"PDF 第 {index + 1} 页栅格化像素数超过 {config.max_pixels}")
                bitmap = page.render(scale=scale)
                image = _normalize_image(bitmap.to_pil(), config.color_space, background=config.background)
                if image.size != expected:
                    from PIL import Image
                    image = image.resize(expected, resample=Image.Resampling.LANCZOS)
                rendered = _encode_image(image, "PNG")
                yield PdfRenderedPage(index + 1, width_pt, height_pt, expected, image.copy(), rendered)
                image.close()
            finally:
                close_bitmap = getattr(bitmap, "close", None) if bitmap is not None else None
                if close_bitmap:
                    close_bitmap()
                close_page = getattr(page, "close", None)
                if close_page:
                    close_page()
    finally:
        close_document = getattr(document, "close", None)
        if close_document:
            close_document()


def _final_image(marked_bytes: bytes, config: PdfRasterConfig) -> tuple[bytes, tuple[int, int]]:
    from PIL import Image

    image = Image.open(io.BytesIO(marked_bytes))
    image.load()
    image = _normalize_image(image, config.color_space, background=config.background)
    if config.resize is not None:
        image = image.resize(tuple(map(int, config.resize)), resample=Image.Resampling.LANCZOS)
    result = _encode_image(image, config.final_format, jpeg_quality=config.jpeg_quality)
    size = image.size
    image.close()
    return result, size


def _write_artifacts(root: Path, page: int, rendered: bytes, final: bytes, config: PdfRasterConfig) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    rendered_path = root / f"page-{page:03d}-rendered.png"
    suffix = ".jpg" if config.final_format.upper() in ("JPG", "JPEG") else ".png"
    final_path = root / f"page-{page:03d}-final{suffix}"
    rendered_path.write_bytes(rendered)
    final_path.write_bytes(final)
    # The parent output directory is known to the caller; relative names keep
    # logs portable and prevent a local username from appearing in artifacts.
    return {"rendered_page": str(Path("pages") / rendered_path.name),
            "final_encoded": str(Path("pages") / final_path.name)}


def process_pdf(
    data: bytes,
    content_id: str,
    *,
    config: PdfRasterConfig | None = None,
    output_dir: str | Path | None = None,
    verify_fn: Callable[[bytes, int], dict[str, Any]] | Callable[[bytes], dict[str, Any]] | None = None,
    log_path: str | Path | None = None,
) -> PdfRasterResult:
    """Render, watermark, re-encode, blindly recover and optionally verify pages.

    ``verify_fn`` is intentionally callback based: a service can run the full
    SM3/Manifest/SM2/version-chain/revocation verification on each final image
    without changing this media-only module.  Returning ``registration_allowed``
    is safe because it is gated exclusively by the final encoded bytes.
    """
    from . import watermark

    config = config or PdfRasterConfig()
    started = time.perf_counter()
    if not isinstance(data, (bytes, bytearray)) or len(data) > config.max_bytes:
        raise ValueError("PDF 为空或超过大小限制")
    original = bytes(data)
    if not original.startswith(b"%PDF-"):
        raise ValueError("输入不是 PDF")
    root = Path(output_dir) if output_dir is not None else None
    original_pdf_path: str | None = None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        source_path = root / "source.pdf"
        source_path.write_bytes(original)
        original_pdf_path = str(source_path)
    pages: list[PdfPageResult] = []
    limitations: list[str] = []
    for rendered_page in iter_rendered_pages(original, config):
        # watermark-v1 is always embedded on the deterministic rendered PNG;
        # final_format/resize only describe the propagation encoding afterwards.
        marked, _profile = watermark.embed_image(rendered_page.rendered_bytes, content_id, "PNG")
        final, final_size = _final_image(marked, config)
        try:
            detector = watermark.detect_image(final)
        except Exception as exc:
            # A detector/decoder error is a failed gate, never an implicit pass.
            detector = {
                "status": "NOT_FOUND",
                "content_ids": [],
                "details": {"error_type": type(exc).__name__, "error": str(exc)},
            }
        recovered = list(detector.get("content_ids") or [])
        gate_pass = detector.get("status") == "FOUND" and recovered == [content_id]
        if verify_fn is None:
            verification = {"status": "NOT_CHECKED", "reason": "verify_fn 未提供"}
        else:
            try:
                try:
                    verification = verify_fn(final, rendered_page.page_number)
                except TypeError:
                    verification = verify_fn(final)  # type: ignore[misc]
                if not isinstance(verification, dict):
                    verification = {"status": "RETURNED_NON_OBJECT", "value": str(verification)}
            except Exception as exc:
                verification = {"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)}
        artifact_paths = _write_artifacts(root / "pages", rendered_page.page_number,
                                          rendered_page.rendered_bytes, final, config) if root else {}
        pages.append(PdfPageResult(rendered_page.page_number, rendered_page.rendered_bytes, final,
                                   detector, recovered, gate_pass, verification, artifact_paths,
                                   rendered_page.expected_size, final_size))
    if not pages:
        limitations.append("PDF 不包含页面，未执行水印 gate")
    elif len(pages) > 1:
        limitations.append("多页验证按页执行；未声明原始页身份或整本文档语义")
    registration_allowed = bool(pages) and all(page.gate_pass for page in pages)
    status = "PASS" if registration_allowed else ("FAILED_GATE" if pages else "NOT_CHECKED")
    result = PdfRasterResult(_sha256(original), original_pdf_path, len(pages), content_id, config, pages, status,
                             registration_allowed, limitations,
                             round((time.perf_counter() - started) * 1000, 3))
    if root is not None:
        if log_path is None:
            log_path = root / "pdf-raster-log.json"
        else:
            log_path = Path(log_path)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        result.log_path = str(log_path)
        Path(log_path).write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    elif log_path is not None:
        result.log_path = str(log_path)
        Path(log_path).write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# Explicit aliases make the media operation easy to discover without changing
# the established gm_provenance.media API.
rasterize_pdf = process_pdf
render_pdf_pages = iter_rendered_pages


__all__ = [
    "PDF_RASTER_DPI", "PDF_MAX_PAGES", "PDF_MAX_PIXELS", "PdfRasterConfig",
    "PdfRenderedPage", "PdfPageResult", "PdfRasterResult", "iter_rendered_pages",
    "render_pdf_pages", "process_pdf", "rasterize_pdf",
]
