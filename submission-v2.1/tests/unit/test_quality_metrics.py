"""Regression tests for the A/B/C pilot quality diagnostics."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dataset_pilot_quality", ROOT / "scripts" / "dataset_pilot_quality.py")
assert SPEC and SPEC.loader
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


def test_local_ssim_uses_rgb_window_and_detects_change():
    original = np.full((32, 32, 3), 128.0)
    same = original.copy()
    changed = original.copy()
    changed[10:20, 10:20, 0] = 0

    assert quality.local_ssim(original, same) == 1.0
    assert quality.local_ssim(original, changed) < 1.0
    metrics = quality.pair_metrics(original, changed)
    assert metrics["parameters"]["colorspace"] == "RGB"
    assert metrics["parameters"]["data_range"] == 255.0
    assert metrics["parameters"]["window_size"] == 11
    assert metrics["parameters"]["use_sample_covariance"] is False


def test_representative_crops_are_fixed_and_include_all_pairs():
    a = np.zeros((300, 400, 3), dtype=np.float64)
    result = quality.representative_crops(a, a.copy(), a.copy())
    assert set(result["regions"]) == {"top_left", "center", "bottom_right"}
    for row in result["regions"].values():
        left, top, right, bottom = row["box_xyxy"]
        assert 0 <= left < right <= 400 and 0 <= top < bottom <= 300
        assert set(row) >= {"A_vs_B", "A_vs_C", "B_vs_C"}


def test_codec_only_baseline_uses_publisher_format(tmp_path: Path):
    image = Image.new("RGB", (32, 24), (10, 20, 30))
    source = tmp_path / "source.png"
    image.save(source, "PNG")
    baseline = quality.encode_without_watermark(source, "image/png")
    assert baseline.startswith(b"\x89PNG\r\n\x1a\n")
    assert quality.load_rgb(source).shape == quality.load_rgb(baseline).shape
