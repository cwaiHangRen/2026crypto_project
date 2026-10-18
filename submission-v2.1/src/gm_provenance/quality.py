"""A/B/C media quality diagnostics for one source and published artifact.

The image B control is produced by the exact watermark publisher image path
with only the DCT coefficient modification skipped.  PDF diagnostics compare
same-DPI page renders and separately record semantic PDF feature loss.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import watermark

SSIM_WINDOW = 11
SSIM_K1 = 0.01
SSIM_K2 = 0.03
SSIM_DATA_RANGE = 255.0


def _load_rgb(value: Path | bytes) -> np.ndarray:
    source = io.BytesIO(value) if isinstance(value, (bytes, bytearray)) else value
    with Image.open(source) as image:
        image.load()
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def _local_ssim(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError(f"SSIM expects equal HxWx3 arrays, got {a.shape} and {b.shape}")
    from scipy.ndimage import uniform_filter

    size = (SSIM_WINDOW, SSIM_WINDOW, 1)
    ma = uniform_filter(a, size=size, mode="reflect")
    mb = uniform_filter(b, size=size, mode="reflect")
    va = uniform_filter(a * a, size=size, mode="reflect") - ma * ma
    vb = uniform_filter(b * b, size=size, mode="reflect") - mb * mb
    cov = uniform_filter(a * b, size=size, mode="reflect") - ma * mb
    c1 = (SSIM_K1 * SSIM_DATA_RANGE) ** 2
    c2 = (SSIM_K2 * SSIM_DATA_RANGE) ** 2
    score = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))
    return float(np.mean(score))


def _pair(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    if a.shape != b.shape:
        raise ValueError(f"A/B/C geometry mismatch: {a.shape} vs {b.shape}")
    mse = float(np.mean((a - b) ** 2))
    return {
        "mse": mse,
        "psnr_db": None if mse == 0 else 10.0 * math.log10(SSIM_DATA_RANGE ** 2 / mse),
        "psnr_infinite": mse == 0,
        "ssim_local": _local_ssim(a, b),
        "shape": list(a.shape),
        "ssim_parameters": {
            "colorspace": "RGB", "data_range": SSIM_DATA_RANGE,
            "window": "uniform", "window_size": SSIM_WINDOW,
            "K1": SSIM_K1, "K2": SSIM_K2,
            "use_sample_covariance": False, "edge_mode": "reflect",
        },
    }


def _save_crops(arrays: dict[str, np.ndarray], out: Path, sample_id: str) -> dict[str, Any]:
    h, w = arrays["A"].shape[:2]
    ch, cw = min(256, h), min(256, w)
    boxes = {
        "top_left": (0, 0, cw, ch),
        "center": ((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch),
        "bottom_right": (w - cw, h - ch, w, h),
    }
    result: dict[str, Any] = {}
    for region, (left, top, right, bottom) in boxes.items():
        item = {"box_xyxy": [left, top, right, bottom], "files": {}}
        for label, arr in arrays.items():
            path = out / f"{sample_id}_{label}_{region}.png"
            Image.fromarray(np.clip(arr[top:bottom, left:right], 0, 255).astype(np.uint8), "RGB").save(path, "PNG")
            item["files"][label] = str(path)
        result[region] = item
    return result


def _image(source: Path, published: Path, output_dir: Path, sample_id: str) -> dict[str, Any]:
    source_bytes = source.read_bytes()
    published_bytes = published.read_bytes()
    # B uses the actual published encoding so the control remains comparable
    # even when callers supply a source file in a different container format.
    with Image.open(io.BytesIO(published_bytes)) as im:
        fmt = "JPEG" if im.format and im.format.upper() in {"JPEG", "JPG"} else "PNG"
    b_bytes, b_profile = watermark.encode_without_watermark(source_bytes, fmt)
    a, b, c = _load_rgb(source_bytes), _load_rgb(b_bytes), _load_rgb(published_bytes)
    if a.shape != b.shape or a.shape != c.shape:
        raise ValueError(f"A/B/C geometry mismatch: {a.shape}, {b.shape}, {c.shape}")
    output_dir.mkdir(parents=True, exist_ok=True)
    b_path = output_dir / f"{sample_id}_B.{fmt.lower()}"
    b_path.write_bytes(b_bytes)
    crop_dir = output_dir / f"{sample_id}_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    files = {"A": str(source), "B": str(b_path), "C": str(published)}
    pairs = {"A_B": _pair(a, b), "A_C": _pair(a, c), "B_C": _pair(b, c)}
    return {
        "sample_id": sample_id, "media_type": "image", "status": "PASS",
        "triplet": {"A": "source_original", "B": "same_publisher_chain_no_watermark", "C": "published_full_watermark"},
        "dimensions": list(a.shape), "files": files,
        "file_sizes_bytes": {k: Path(v).stat().st_size for k, v in files.items()},
        "encoding": {"B": b_profile, "C": {"format": fmt, "source_bytes": len(published_bytes)}},
        "pairs": pairs,
        # Keep names aligned with the existing pilot report for consumers that
        # aggregate this API with scripts/dataset_pilot_quality.py.
        "pair_metrics": {"A_vs_B": pairs["A_B"], "A_vs_C": pairs["A_C"], "B_vs_C": pairs["B_C"]},
        "crops": _save_crops({"A": a, "B": b, "C": c}, crop_dir, sample_id),
        "comparison": "decoded RGB at source geometry; no resizing before metrics",
        "work_size": watermark.WORK_SIZE,
    }


def _pdf_page_iter(data: bytes, dpi: int):
    """Yield one detached RGB page at a time and release PDFium resources."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    scale = dpi / 72.0
    try:
        for page in document:
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            try:
                # copy() detaches the ndarray from the PIL/PDFium backing store
                yield np.asarray(pil.convert("RGB"), dtype=np.float64).copy()
            finally:
                pil.close()
                close_bitmap = getattr(bitmap, "close", None)
                if close_bitmap:
                    close_bitmap()
                close_page = getattr(page, "close", None)
                if close_page:
                    close_page()
    finally:
        close_document = getattr(document, "close", None)
        if close_document:
            close_document()


def _outline_count(value: Any) -> int:
    """Count bookmark destinations recursively, excluding child lists."""
    if isinstance(value, (list, tuple)):
        return sum(_outline_count(item) for item in value)
    return 1 if value is not None else 0


def _outline_summary(value: Any, reader: Any = None) -> list[dict[str, str]]:
    """Return stable bookmark title/target summaries, preserving nesting order."""
    if isinstance(value, (list, tuple)):
        result: list[dict[str, str]] = []
        for item in value:
            result.extend(_outline_summary(item, reader))
        return result
    if value is None:
        return []
    title = str(getattr(value, "title", "") or "")
    destination = None
    if reader is not None:
        try:
            destination = f"page:{reader.get_destination_page_number(value)}"
        except Exception:
            destination = None
    if destination is None:
        destination = getattr(value, "page", None)
    if destination is None:
        destination = getattr(value, "dest", None)
    return [{"title": title, "target": str(destination) if destination is not None else ""}]


def _resolve_pdf_object(value: Any) -> Any:
    getter = getattr(value, "get_object", None)
    if getter:
        try:
            return getter()
        except Exception:
            return value
    return value


def _extract_text(page: Any) -> tuple[str, list[str]]:
    captured: list[warnings.WarningMessage] = []
    errors: list[str] = []
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            errors.append(f"{type(exc).__name__}: {exc}")
    messages = [str(item.message) for item in captured]
    messages.extend(errors)
    return text, messages


def _pdf_features(data: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    texts, text_digests, text_warnings, geometries, links, link_summaries = [], [], [], [], [], []
    for page in reader.pages:
        text, extraction_warnings = _extract_text(page)
        texts.append(len(text))
        text_digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None)
        text_warnings.extend(extraction_warnings)
        box = page.mediabox
        geometries.append([float(box.width), float(box.height)])
        annotations = _resolve_pdf_object(page.get("/Annots", []) or [])
        link_count = 0
        page_links: list[dict[str, str]] = []
        for annotation in annotations or []:
            annotation = _resolve_pdf_object(annotation)
            if hasattr(annotation, "get") and str(annotation.get("/Subtype")) == "/Link":
                link_count += 1
                action = _resolve_pdf_object(annotation.get("/A"))
                target = annotation.get("/Dest")
                if hasattr(action, "get"):
                    target = action.get("/URI", action.get("/D", target))
                page_links.append({
                    "target": str(target) if target is not None else "",
                    "rect": str(annotation.get("/Rect", "")),
                })
        links.append(link_count)
        link_summaries.append(page_links)
    try:
        outline = reader.outline
        bookmarks = _outline_count(outline)
        bookmark_summary = _outline_summary(outline, reader)
    except Exception as exc:
        bookmarks = None
        bookmark_summary = None
        text_warnings.append(f"bookmark extraction: {type(exc).__name__}: {exc}")
    root = reader.trailer.get("/Root", {})
    acro = root.get("/AcroForm") if hasattr(root, "get") else None
    forms = 0
    if acro is not None and hasattr(acro, "get_object"):
        try:
            acro = acro.get_object()
        except Exception:
            acro = None
    if acro and hasattr(acro, "get"):
        fields = acro.get("/Fields") or []
        forms = len(_resolve_pdf_object(fields))
    return {"page_count": len(reader.pages), "text_chars": texts, "geometry_points": geometries,
            "text_content_sha256": text_digests, "text_present": any(texts),
            "text_measurement_warnings": text_warnings,
            "text_measurement_reliability": ("WARNING" if text_warnings else "RELIABLE"),
            "link_annotation_counts": links, "links_present": any(links),
            "link_summaries": link_summaries,
            "bookmarks": bookmarks, "bookmark_summary": bookmark_summary, "bookmarks_present": bool(bookmarks),
            "form_field_count": forms, "forms_present": bool(forms)}


def _feature_status(source_present: bool, published_present: bool, equal: bool) -> str:
    if not source_present:
        return "N/A_NO_SOURCE_FEATURE"
    if not published_present:
        return "LOST"
    return "PRESERVED" if equal else "CHANGED"


def _pdf(source: Path, published: Path, output_dir: Path, sample_id: str, dpi: int = 120) -> dict[str, Any]:
    a_bytes, c_bytes = source.read_bytes(), published.read_bytes()
    af, cf = _pdf_features(a_bytes), _pdf_features(c_bytes)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages, crop_arrays = [], None
    source_iter, published_iter = iter(_pdf_page_iter(a_bytes, dpi)), iter(_pdf_page_iter(c_bytes, dpi))
    source_done = published_done = False
    page_index = 0
    while True:
        try:
            source_page = next(source_iter)
        except StopIteration:
            source_page, source_done = None, True
        try:
            published_page = next(published_iter)
        except StopIteration:
            published_page, published_done = None, True
        if source_page is None and published_page is None:
            break
        page_index += 1
        row: dict[str, Any] = {"page": page_index}
        if source_page is None or published_page is None:
            row.update({"status": "NOT_COMPARABLE", "reason": "page count differs"})
        elif source_page.shape != published_page.shape:
            row.update({"status": "NOT_COMPARABLE", "reason": "same-DPI render geometry differs"})
        else:
            row.update({"status": "PASS", "A_C": _pair(source_page, published_page),
                        "render_shape": list(source_page.shape)})
            if crop_arrays is None:
                crop_arrays = {"A": source_page, "C": published_page}
        pages.append(row)
        del source_page, published_page
    representative_crops = None
    if crop_arrays is not None:
        crop_dir = output_dir / f"{sample_id}_pdf_crops"
        crop_dir.mkdir(parents=True, exist_ok=True)
        representative_crops = _save_crops(crop_arrays, crop_dir, sample_id)
    text_equal = af["text_content_sha256"] == cf["text_content_sha256"]
    bookmark_equal = af["bookmark_summary"] == cf["bookmark_summary"]
    link_equal = af["link_summaries"] == cf["link_summaries"]
    form_equal = af["form_field_count"] == cf["form_field_count"]
    text_status = _feature_status(af["text_present"], cf["text_present"], text_equal)
    feature_status = {
        "text_layer": text_status,
        "bookmarks": _feature_status(af["bookmarks_present"], cf["bookmarks_present"], bookmark_equal),
        "links": _feature_status(af["links_present"], cf["links_present"], link_equal),
        "forms": _feature_status(af["forms_present"], cf["forms_present"], form_equal),
    }
    return {
        "sample_id": sample_id, "media_type": "pdf", "status": "PASS" if all(x["status"] == "PASS" for x in pages) else "NOT_COMPARABLE",
        "dpi": dpi, "triplet": {"A": "source PDF", "B": "NOT_APPLICABLE (no PDF no-watermark encoder)", "C": "published PDF"},
        "pages": pages, "representative_crops": representative_crops,
        "source_features": af, "published_features": cf,
        "feature_changes": {"status": feature_status,
                             "text_layer_measurement_warning": (af["text_measurement_reliability"] == "WARNING" or
                                                                  cf["text_measurement_reliability"] == "WARNING"),
                             "text_layer_preserved": (None if feature_status["text_layer"].startswith("N/A") else feature_status["text_layer"] == "PRESERVED"),
                             "bookmarks_preserved": (None if feature_status["bookmarks"].startswith("N/A") else feature_status["bookmarks"] == "PRESERVED"),
                             "links_preserved": (None if feature_status["links"].startswith("N/A") else feature_status["links"] == "PRESERVED"),
                             "forms_preserved": (None if feature_status["forms"].startswith("N/A") else feature_status["forms"] == "PRESERVED"),
                             "geometry_preserved": af["geometry_points"] == cf["geometry_points"]},
        "file_sizes_bytes": {"A": len(a_bytes), "C": len(c_bytes), "delta": len(c_bytes) - len(a_bytes),
                             "ratio": len(c_bytes) / len(a_bytes) if a_bytes else None},
        "sha256": {"A": hashlib.sha256(a_bytes).hexdigest(), "C": hashlib.sha256(c_bytes).hexdigest()},
        "quality_limits": "PDF B is not constructed; page identity and whole-file integrity are outside this diagnostic.",
    }


def analyze(source: Path, published: Path, output_dir: Path, sample_id: str) -> dict[str, Any]:
    """Analyze one image or PDF source/published pair and save evidence files."""
    source, published, output_dir = Path(source), Path(published), Path(output_dir)
    if not source.exists() or not published.exists():
        raise FileNotFoundError(f"source/published not found: {source}, {published}")
    data = source.read_bytes()
    if data.startswith(b"%PDF-"):
        return _pdf(source, published, output_dir, sample_id)
    return _image(source, published, output_dir, sample_id)


__all__ = ["analyze"]
