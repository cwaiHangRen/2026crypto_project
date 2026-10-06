"""Measure image quality before/after local watermark publication.

This is a small diagnostic for the downloaded pilot only. It reports global
RGB PSNR and a global SSIM-style score for image samples. PDF pages are listed
as non-comparable because publication rasterizes them into a new PDF.
"""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data" / "external-pilot-20261006"
TEST = ROOT / "results" / "dataset-pilot-20261006"
REPORT = TEST / "report.json"
OUT = TEST / "quality.json"


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def global_ssim(a: np.ndarray, b: np.ndarray) -> float:
    # A global SSIM diagnostic, not a replacement for a windowed image-quality
    # benchmark. Constants follow the standard L=255 normalization.
    x = a.reshape(-1)
    y = b.reshape(-1)
    mu_x, mu_y = float(x.mean()), float(y.mean())
    var_x, var_y = float(x.var()), float(y.var())
    cov = float(((x - mu_x) * (y - mu_y)).mean())
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return ((2 * mu_x * mu_y + c1) * (2 * cov + c2)) / ((mu_x**2 + mu_y**2 + c1) * (var_x + var_y + c2))


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = []
    for row in report["rows"]:
        item = {"dataset": row["dataset"], "input": row["input"], "content_id": row.get("content_id")}
        if row.get("media_type") not in ("image/png", "image/jpeg"):
            item.update({"status": "NOT_COMPARABLE", "reason": "PDF publication rasterizes pages into a new PDF"})
            rows.append(item)
            continue
        original = ROOT / row["input"]
        published = TEST / "registry" / "media" / f"{row['content_id']}.bin"
        a, b = load_rgb(original), load_rgb(published)
        if a.shape != b.shape:
            item.update({"status": "FAIL", "reason": f"shape mismatch: {a.shape} vs {b.shape}"})
            rows.append(item)
            continue
        mse = float(np.mean((a - b) ** 2))
        psnr = float("inf") if mse == 0 else 10 * math.log10((255.0**2) / mse)
        item.update({"status": "PASS", "shape": list(a.shape), "mse": mse, "psnr_db": psnr, "global_ssim": global_ssim(a, b)})
        rows.append(item)
    output = {
        "status": "PASS" if all(item["status"] in ("PASS", "NOT_COMPARABLE") for item in rows) else "FAIL",
        "scope": "downloaded six-file pilot; image publication only",
        "rows": rows,
        "note": "Global SSIM is diagnostic only; no project acceptance threshold is asserted.",
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
