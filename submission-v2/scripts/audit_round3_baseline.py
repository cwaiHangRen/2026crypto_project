#!/usr/bin/env python3
"""Recompute round-3 aggregation and causal/timing audits without rerunning it."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def metric(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(r["pairs"][key]["mse"]) for r in rows]
    psnr = [float(r["pairs"][key]["psnr_db"]) for r in rows if r["pairs"][key].get("psnr_db") is not None]
    ssim = [float(r["pairs"][key]["ssim_local"]) for r in rows]
    zero = sum(float(r["pairs"][key]["mse"]) == 0.0 for r in rows)
    return {
        "n": len(rows), "mean_mse": statistics.fmean(values) if values else None,
        "mean_per_image_psnr": statistics.fmean(psnr) if psnr else None,
        "psnr_from_mean_mse": None if not values or statistics.fmean(values) == 0 else 10 * math.log10(255.0**2 / statistics.fmean(values)),
        "median_psnr": statistics.median(psnr) if psnr else None,
        "p10_psnr": percentile(psnr, 0.10), "median_ssim": statistics.median(ssim) if ssim else None,
        "p10_ssim": percentile(ssim, 0.10), "mse_zero_count": zero,
        "psnr_infinite_count": sum(bool(r["pairs"][key].get("psnr_infinite")) for r in rows),
    }


def group_quality(rows: list[dict[str, Any]], key: str, label: str, value_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(value_fn(row))].append(row)
    return {f"{label}={name}": metric(group, key) for name, group in sorted(groups.items())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="results/pilot-round3/20261007T-round3-v3-final")
    ap.add_argument("--output", default="results/round4-baseline-audit.json")
    args = ap.parse_args()
    root = Path(args.run)
    quality_rows = [json.loads(line) for line in (root / "quality.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    image_rows = [r for r in quality_rows if r.get("media_type") == "image"]
    aggregate = {key: metric(image_rows, key) for key in ("A_B", "A_C", "B_C")}
    by_category = {key: group_quality(image_rows, key, "origin_category", lambda r: r.get("source_sample", {}).get("origin_category", "unknown")) for key in ("A_B", "A_C", "B_C")}
    by_family = {key: group_quality(image_rows, key, "source_family", lambda r: r.get("source_sample", {}).get("source_family_id", "unknown")) for key in ("A_B", "A_C", "B_C")}
    by_resolution = {key: group_quality(image_rows, key, "resolution", lambda r: f"{r['dimensions'][1]}x{r['dimensions'][0]}") for key in ("A_B", "A_C", "B_C")}
    samples = [json.loads(line) for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    g23 = [r for r in samples if r.get("group") in ("G2", "G3")]
    paired_cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in g23:
        paired_cells[(str(row.get("sample_id")), str(row.get("transform")), str(row.get("group")))].append(row)
    g23_summary = {
        "rows": len(g23), "by_group": Counter(str(r.get("group")) for r in g23),
        "same_observed_media_path_pairs": 0,
        "content_ids": sorted({str(r.get("truth", {}).get("expected_content_id")) for r in g23 if r.get("truth", {}).get("expected_content_id")}),
        "note": "G2/G3 rows are not a causal pair in round3: development_eval creates separate random IDs and independently embeds each group before transforms.",
    }
    timing_rows = [json.loads(line) for line in (root / "timings.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    timing_groups = Counter(f"{r.get('group')}/{r.get('mode', r.get('phase', 'unknown'))}" for r in timing_rows)
    timing_results = Counter()
    for row in samples:
        g = row.get("group"); mode = (row.get("detector_input") or {}).get("mode", "unknown")
        timing_results[f"{g}/{mode}/{row.get('observed', {}).get('conclusion')}"] += 1
    output = {
        "schema_version": "round4-baseline-audit-1", "source_run": str(root),
        "quality": {"overall_image": aggregate, "by_origin_category": by_category, "by_source_family": by_family, "by_resolution": by_resolution,
                    "interpretation": "mean_per_image_psnr is the arithmetic mean of finite per-image PSNR; psnr_from_mean_mse is computed after averaging MSE. Both are valid summaries with different weighting and are not expected to match.",
                    "zero_error_policy": "MSE=0 is counted explicitly; no epsilon or Infinity is written."},
        "g2_g3_audit": g23_summary,
        "timing_audit": {"rows": len(timing_rows), "group_mode_counts": dict(timing_groups), "outcome_counts": dict(timing_results),
                          "interpretation": "hard/recover/full contain different groups, sidecar states and early-return outcomes in round3; their aggregate means are retained but are not a same-workload comparison."},
    }
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "quality_rows": len(image_rows), "timing_rows": len(timing_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
