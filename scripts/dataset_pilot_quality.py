"""Measure the pilot quality with an explicit A/B/C baseline.

The image comparison has three decoded RGB arrays:

* A: the downloaded source image;
* B: the same source decoded and encoded with the output format used by the
  publisher, without a watermark (codec-only baseline);
* C: the published, watermarked image.

Metrics are calculated after decoding. A->B is codec distortion, B->C is the
increment attributable to the watermark and A->C is total publication
distortion. Global SSIM is retained only as a historical diagnostic; the
acceptance-facing SSIM is a standard local-window RGB score.

PDF is reported separately. It compares source and published page renders at
the same DPI and records page geometry and text-layer observations. A PDF page
watermark is a hint for the currently observed rendered page; it does not
establish original page identity or whole-file integrity.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "results" / "dataset-pilot-20261006"
REPORT = TEST / "report.json"
OUT = TEST / "quality.json"
OUT_JSONL = TEST / "quality.jsonl"
SSIM_WINDOW = 11
SSIM_K1 = 0.01
SSIM_K2 = 0.03
SSIM_DATA_RANGE = 255.0


def load_rgb(path: Path | bytes) -> np.ndarray:
    """Decode an image into an HxWx3 RGB float array."""
    source = io.BytesIO(path) if isinstance(path, (bytes, bytearray)) else path
    with Image.open(source) as image:
        image.load()
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def global_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Historical global SSIM diagnostic retained for old pilot reports."""
    x = a.reshape(-1)
    y = b.reshape(-1)
    mu_x, mu_y = float(x.mean()), float(y.mean())
    var_x, var_y = float(x.var()), float(y.var())
    cov = float(((x - mu_x) * (y - mu_y)).mean())
    c1, c2 = (SSIM_K1 * SSIM_DATA_RANGE) ** 2, (SSIM_K2 * SSIM_DATA_RANGE) ** 2
    return ((2 * mu_x * mu_y + c1) * (2 * cov + c2)) / (
        (mu_x**2 + mu_y**2 + c1) * (var_x + var_y + c2)
    )


def local_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Return standard local-window SSIM averaged over RGB channels.

    The score uses an 11x11 uniform window, ``L=255``, K1=.01 and K2=.03,
    with population local moments (``use_sample_covariance=False``).
    """
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError(f"SSIM expects equal HxWx3 arrays, got {a.shape} and {b.shape}")
    from scipy.ndimage import uniform_filter

    size = (SSIM_WINDOW, SSIM_WINDOW, 1)
    mu_a = uniform_filter(a, size=size, mode="reflect")
    mu_b = uniform_filter(b, size=size, mode="reflect")
    sigma_a = uniform_filter(a * a, size=size, mode="reflect") - mu_a * mu_a
    sigma_b = uniform_filter(b * b, size=size, mode="reflect") - mu_b * mu_b
    covariance = uniform_filter(a * b, size=size, mode="reflect") - mu_a * mu_b
    c1 = (SSIM_K1 * SSIM_DATA_RANGE) ** 2
    c2 = (SSIM_K2 * SSIM_DATA_RANGE) ** 2
    numerator = (2 * mu_a * mu_b + c1) * (2 * covariance + c2)
    denominator = (mu_a * mu_a + mu_b * mu_b + c1) * (sigma_a + sigma_b + c2)
    return float(np.mean(numerator / np.maximum(denominator, np.finfo(float).eps)))


def pair_metrics(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    mse = float(np.mean((a - b) ** 2))
    return {
        "shape": list(a.shape),
        "mse": mse,
        "psnr_db": None if mse == 0 else 10 * math.log10((SSIM_DATA_RANGE**2) / mse),
        "psnr_infinite": mse == 0,
        "ssim_local": local_ssim(a, b),
        "ssim_global_legacy": global_ssim(a, b),
        "parameters": {
            "colorspace": "RGB",
            "data_range": SSIM_DATA_RANGE,
            "window": "uniform",
            "window_size": SSIM_WINDOW,
            "K1": SSIM_K1,
            "K2": SSIM_K2,
            "use_sample_covariance": False,
        },
    }


def encode_without_watermark(source: Path, media_type: str) -> bytes:
    """Encode the codec-only B baseline with publisher output settings."""
    with Image.open(source) as image:
        image.load()
        out = io.BytesIO()
        if media_type == "image/jpeg":
            image.convert("RGB").save(out, "JPEG", quality=95, optimize=False)
        elif media_type == "image/png":
            image.save(out, "PNG")
        else:
            raise ValueError(f"unsupported image media type: {media_type}")
        return out.getvalue()


def _crop_boxes(height: int, width: int) -> dict[str, tuple[int, int, int, int]]:
    crop_h, crop_w = min(256, height), min(256, width)
    return {
        "top_left": (0, 0, crop_w, crop_h),
        "center": ((width - crop_w) // 2, (height - crop_h) // 2,
                   (width - crop_w) // 2 + crop_w, (height - crop_h) // 2 + crop_h),
        "bottom_right": (width - crop_w, height - crop_h, width, height),
    }


def representative_crops(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> dict[str, Any]:
    """Report metrics for fixed crops without selecting based on quality."""
    result: dict[str, Any] = {"crop_size_max": 256, "regions": {}}
    for name, (left, top, right, bottom) in _crop_boxes(a.shape[0], a.shape[1]).items():
        aa, bb, cc = a[top:bottom, left:right], b[top:bottom, left:right], c[top:bottom, left:right]
        result["regions"][name] = {
            "box_xyxy": [left, top, right, bottom],
            "A_vs_B": pair_metrics(aa, bb),
            "A_vs_C": pair_metrics(aa, cc),
            "B_vs_C": pair_metrics(bb, cc),
        }
    return result


def _image_triplet(row: dict[str, Any], test_root: Path) -> dict[str, Any]:
    original = ROOT / row["input"]
    published = test_root / "registry" / "media" / f"{row['content_id']}.bin"
    if not original.exists() or not published.exists():
        raise FileNotFoundError(f"missing A or C: {original} / {published}")
    media_type = row["media_type"]
    a = load_rgb(original)
    b_bytes = encode_without_watermark(original, media_type)
    b = load_rgb(b_bytes)
    c = load_rgb(published)
    if a.shape != b.shape or a.shape != c.shape:
        raise ValueError(f"A/B/C shape mismatch: {a.shape}, {b.shape}, {c.shape}")
    baseline_dir = test_root / "quality-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if media_type == "image/jpeg" else ".png"
    baseline_path = baseline_dir / f"{row['content_id']}{suffix}"
    baseline_path.write_bytes(b_bytes)
    source_h, source_w = a.shape[:2]
    return {
        "dataset": row["dataset"],
        "input": row["input"],
        "content_id": row.get("content_id"),
        "status": "PASS",
        "media_type": media_type,
        "triplet": {
            "A": {"role": "source_original", "path": row["input"]},
            "B": {"role": "codec_only_no_watermark", "path": str(baseline_path.relative_to(ROOT)).replace("\\", "/")},
            "C": {"role": "published_full_watermark", "path": str(published.relative_to(ROOT)).replace("\\", "/")},
        },
        "shape": list(a.shape),
        "colorspace": "RGB",
        "normalization": {
            "watermark_work_size": [512, 512],
            "resized_to_work_size": [source_w, source_h] != [512, 512],
            "source_to_work_resize": ("downsample" if source_w >= 512 and source_h >= 512
                                       else "upsample_or_mixed"),
            "restored_to_source_size": [source_w, source_h] != [512, 512],
            "upsampled_to_source_size": [source_w != 512, source_h != 512],
            "source_dimensions": [source_w, source_h],
            "comparison_dimensions": [source_w, source_h],
            "comparison_rule": "decode A/B/C to RGB; no resize before metrics",
        },
        "pair_metrics": {
            "A_vs_B": pair_metrics(a, b),
            "A_vs_C": pair_metrics(a, c),
            "B_vs_C": pair_metrics(b, c),
        },
        "representative_crops": representative_crops(a, b, c),
        "legacy_global_ssim_scope": "diagnostic only; use ssim_local for quality reporting",
    }


def _pdf_text_and_geometry(data: bytes) -> tuple[list[int], list[list[float]]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    texts = [len((page.extract_text() or "")) for page in reader.pages]
    geometry = []
    for page in reader.pages:
        box = page.mediabox
        geometry.append([float(box.width), float(box.height)])
    return texts, geometry


def _render_pdf(data: bytes, dpi: int) -> list[np.ndarray]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    scale = dpi / 72.0
    return [np.asarray(page.render(scale=scale).to_pil().convert("RGB"), dtype=np.float64)
            for page in document]


def _pdf_quality(row: dict[str, Any], test_root: Path, dpi: int = 120) -> dict[str, Any]:
    source = ROOT / row["input"]
    published = test_root / "registry" / "media" / f"{row['content_id']}.bin"
    source_bytes, published_bytes = source.read_bytes(), published.read_bytes()
    source_pages = _render_pdf(source_bytes, dpi)
    published_pages = _render_pdf(published_bytes, dpi)
    source_text, source_geometry = _pdf_text_and_geometry(source_bytes)
    published_text, published_geometry = _pdf_text_and_geometry(published_bytes)
    pages: list[dict[str, Any]] = []
    for index in range(max(len(source_pages), len(published_pages))):
        item: dict[str, Any] = {
            "page": index + 1,
            "page_source_hint": "watermark detector only; current rendered page hint",
            "original_page_identity": "NOT_CHECKED",
            "whole_file_digest": "NOT_CHECKED_BY_QUALITY_DIAGNOSTIC",
        }
        if index >= len(source_pages) or index >= len(published_pages):
            item.update({"status": "NOT_COMPARABLE", "reason": "source/published page count differs"})
        elif source_pages[index].shape != published_pages[index].shape:
            item.update({"status": "NOT_COMPARABLE", "reason": "same-DPI render pixel geometry differs"})
        else:
            item.update({"status": "PASS", "render_shape": list(source_pages[index].shape),
                        "A_render_vs_C_render": pair_metrics(source_pages[index], published_pages[index])})
        item["source_geometry_points"] = source_geometry[index] if index < len(source_geometry) else None
        item["published_geometry_points"] = published_geometry[index] if index < len(published_geometry) else None
        item["source_text_chars"] = source_text[index] if index < len(source_text) else None
        item["published_text_chars"] = published_text[index] if index < len(published_text) else None
        pages.append(item)
    return {
        "dataset": row["dataset"],
        "input": row["input"],
        "content_id": row.get("content_id"),
        "status": "PASS" if len(source_pages) == len(published_pages) and all(p["status"] == "PASS" for p in pages) else "NOT_COMPARABLE",
        "comparison": "source page render A vs published page render C at identical DPI",
        "dpi": dpi,
        "source_page_count": len(source_pages),
        "published_current_page_count": len(published_pages),
        "page_identity": "NOT_CHECKED",
        "whole_file_digest": {
            "algorithm": "SHA-256",
            "source": hashlib.sha256(source_bytes).hexdigest(),
            "published": hashlib.sha256(published_bytes).hexdigest(),
            "scope": "diagnostic file digests; not a Manifest verification result",
        },
        "text_layer": {
            "source_chars": source_text,
            "published_chars": published_text,
            "preserved": source_text == published_text,
            "expected_behavior": "published PDF is rasterized and normally loses original text layer",
        },
        "geometry": {"source_points": source_geometry, "published_points": published_geometry,
                     "all_equal": source_geometry == published_geometry},
        "triplet": {
            "A": "source PDF page objects rendered at the comparison DPI",
            "B": "NOT_APPLICABLE: no equivalent unwatermarked PDF page encoding was constructed",
            "C": "published PDF page objects rendered at the comparison DPI",
        },
        "pages": pages,
        "semantic_limit": "page watermark recovery is a current-page source hint; it does not prove original page set/order or whole-file integrity",
    }


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    pdf_rows: list[dict[str, Any]] = []
    for row in report["rows"]:
        try:
            if row.get("media_type") in ("image/png", "image/jpeg"):
                rows.append(_image_triplet(row, TEST))
            elif row.get("media_type") == "application/pdf":
                pdf_rows.append(_pdf_quality(row, TEST))
            else:
                rows.append({"dataset": row.get("dataset"), "status": "NOT_COMPARABLE",
                             "reason": f"unsupported media type {row.get('media_type')!r}"})
        except Exception as exc:
            target = rows if row.get("media_type") in ("image/png", "image/jpeg") else pdf_rows
            target.append({"dataset": row.get("dataset"), "input": row.get("input"),
                           "content_id": row.get("content_id"), "status": "FAIL",
                           "error": f"{type(exc).__name__}: {exc}"})
    all_rows = rows + pdf_rows
    output = {
        "status": "PASS" if all(item["status"] in ("PASS", "NOT_COMPARABLE") for item in all_rows) else "FAIL",
        "scope": "downloaded six-file pilot; real local samples only",
        "image_baseline": {"A": "original", "B": "same output encoding without watermark", "C": "full watermark publication"},
        "image_rows": rows,
        "pdf_rows": pdf_rows,
        "artifacts": {
            "quality_json": str(OUT.relative_to(ROOT)).replace("\\", "/"),
            "quality_jsonl": str(OUT_JSONL.relative_to(ROOT)).replace("\\", "/"),
        },
        "note": "PSNR and local-window SSIM are descriptive for this pilot. Global SSIM is retained only as a historical diagnostic; no acceptance threshold is asserted.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    # One record per sample makes the diagnostic consumable by the round-2
    # evaluation aggregator without requiring it to parse the nested report.
    records = rows + pdf_rows
    OUT_JSONL.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
