#!/usr/bin/env python3
"""Evidence-based geometry diagnosis for the frozen Round 6 gap set."""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    rows = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at line {n}: {exc}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _geometry_labels(row: dict[str, Any]) -> list[str]:
    out = []
    for root in (row.get("sync_diagnostics"), (row.get("sync") or {}).get("observed"), row.get("oracle")):
        if not isinstance(root, dict):
            continue
        for item in root.get("geometry_hypotheses", []) or []:
            out.append(str(item.get("geometry", "unknown") if isinstance(item, dict) else item))
    return sorted(set(out))


def diagnose(rows: Iterable[dict[str, Any]], target: int = 882) -> dict[str, Any]:
    """Inventory-only summary; no claim that row count proves a target set."""
    rows = list(rows)
    paired = [r for r in rows if r.get("role", "paired") == "paired"]
    ranges = collections.Counter(str(r.get("transform_range", "unknown")) for r in paired)
    transforms = collections.Counter(str(r.get("transform", "unknown")) for r in paired)
    geometries = collections.Counter(g for r in paired for g in _geometry_labels(r))
    failures = [r for r in paired if str(r.get("pair_cell")) in {"d0_success_sync_failed", "both_failed"}]
    unique_keys = {str(r.get("key")) for r in paired if r.get("key")}
    return {
        "observed_rows": len(paired), "unique_task_keys": len(unique_keys), "target_rows": target,
        "capacity_gap": max(0, target - len(unique_keys)), "target_reached": False,
        "inventory_capacity_reached": len(unique_keys) >= target,
        "transform_range_counts": dict(ranges), "transform_counts": dict(transforms),
        "geometry_hypothesis_counts": dict(geometries), "sync_failure_rows": len(failures),
        "failure_keys": [str(r.get("key")) for r in failures[:50]],
        "diagnostic": "inventory only; actual geometry evidence requires --actual and a frozen paired.jsonl",
    }


def _old_oracle_gray(transformed: bytes, geometry: dict[str, Any]):
    from PIL import Image
    from gm_provenance import watermark as wm
    source = Image.open(io.BytesIO(transformed)).convert("RGB")
    canvas = Image.new("RGB", tuple(int(x) for x in geometry["source_size"]), (128, 128, 128))
    x0, y0, x1, y1 = [int(x) for x in geometry["rect_xyxy"]]
    canvas.paste(source.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), (x0, y0))
    return wm._work_gray(canvas)[0]


def _normalized_geometry(geometry: dict[str, Any]) -> dict[str, float]:
    sw, sh = [float(x) for x in geometry["source_size"]]
    x0, y0, x1, y1 = [float(x) for x in geometry["rect_xyxy"]]
    cw, ch = x1 - x0, y1 - y0
    return {"crop_area_ratio": (cw * ch) / (sw * sh), "offset_x": (x0 - (sw - cw) / 2.0) / sw, "offset_y": (y0 - (sh - ch) / 2.0) / sh}


def _dct_signature(gray):
    import numpy as np
    from gm_provenance import watermark as wm
    positions = [(r, c) for r in range(1, wm.WORK_SIZE // 8 - 1) for c in range(1, wm.WORK_SIZE // 8 - 1)]
    blocks = np.stack([gray[br * 8:br * 8 + 8, bc * 8:bc * 8 + 8] for br, bc in positions[: wm._CODEWORD_BYTES * 8 * wm._REPEAT]])
    coeff = wm._dct_blocks(blocks)
    return np.asarray(coeff[:, (2, 3), (3, 2)], dtype=np.float32)


def _metrics(old, candidate, expected: str | None, *, cache: dict[int, Any] | None = None) -> dict[str, Any]:
    import numpy as np
    from gm_provenance import watermark as wm
    from gm_provenance.watermark_sync import decode_work_gray
    old = np.asarray(old, dtype=np.float32); candidate = np.asarray(candidate, dtype=np.float32)
    cache = cache if cache is not None else {}
    def prepared(value):
        key = id(value)
        if key not in cache:
            cache[key] = (_dct_signature(value), wm._detect_bits(value).astype(np.uint8), decode_work_gray(value))
        return cache[key]
    dct_old, bits_old, decoded_old = prepared(old)
    dct_new, bits_new, decoded_new = prepared(candidate)
    expected_bits = np.unpackbits(np.frombuffer(wm._RS.encode(wm._payload(expected)), dtype=np.uint8)) if expected else None
    raw_old = np.packbits(bits_old).tobytes(); raw_new = np.packbits(bits_new).tobytes()
    return {
        "mse": round(float(np.mean((old - candidate) ** 2)), 6),
        "max_abs_pixel": int(np.max(np.abs(old - candidate))),
        "mean_abs_dct_pair": round(float(np.mean(np.abs((dct_old[:, 0] - dct_old[:, 1]) - (dct_new[:, 0] - dct_new[:, 1])))), 6),
        "dct_sign_disagreements": int(np.count_nonzero(np.signbit(dct_old[:, 0] - dct_old[:, 1]) != np.signbit(dct_new[:, 0] - dct_new[:, 1]))),
        "repeated_bit_disagreements": int(np.count_nonzero(bits_old != bits_new)),
        "old_ecc_input_bit_errors": int(np.count_nonzero(np.unpackbits(np.frombuffer(raw_old, dtype=np.uint8)) != expected_bits)) if expected_bits is not None else None,
        "candidate_ecc_input_bit_errors": int(np.count_nonzero(np.unpackbits(np.frombuffer(raw_new, dtype=np.uint8)) != expected_bits)) if expected_bits is not None else None,
        "old_decoded": decoded_old, "candidate_decoded": decoded_new,
        "old_expected": bool(expected and decoded_old == expected),
        "candidate_expected": bool(expected and decoded_new == expected),
    }


def _transform_spec(name: str) -> dict[str, Any]:
    cfg = json.loads((ROOT / "configs/round6-sync-evaluation.json").read_text(encoding="utf-8"))
    for spec in cfg["transforms"]:
        if spec["name"] == name:
            return spec
    raise KeyError(name)


def actual_diagnosis(path: str | Path, *, limit: int | None = None) -> dict[str, Any]:
    import numpy as np
    from gm_provenance import watermark as wm
    from gm_provenance.watermark_sync import _candidate_gray, _resize_observed, reconstruct_work_gray
    from run_round6_sync_eval import transform

    rows, seen = [], set()
    for row in load_rows(path):
        if row.get("role", "paired") != "paired" or row.get("key") in seen:
            continue
        seen.add(row.get("key"))
        oracle = row.get("oracle") or {}; oracle_obs = oracle.get("observed") if isinstance(oracle, dict) else None
        sync_rec = (row.get("sync") or {}).get("recovery") or {}
        if not (isinstance(oracle_obs, dict) and oracle_obs.get("status") == "FOUND" and oracle_obs.get("content_ids")):
            continue
        if sync_rec.get("found_expected"):
            continue
        geometry = row.get("evaluator_only_geometry_truth")
        if isinstance(geometry, dict) and "rect_xyxy" in geometry:
            rows.append(row)
    if limit is not None:
        rows = rows[:int(limit)]
    details = []
    for row in rows:
        try:
            final = ROOT / row["final_source"]
            transformed, geometry = transform(final.read_bytes(), _transform_spec(row["transform"]))
            normalized = _normalized_geometry(geometry)
            image, _mode = wm._load(transformed)
            pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
            old = _old_oracle_gray(transformed, geometry)
            observed = _resize_observed(pixels, normalized["crop_area_ratio"], profile="decoder-v2-sync")
            legacy = _candidate_gray(pixels, normalized["crop_area_ratio"], normalized["offset_x"], normalized["offset_y"], observed=observed, profile="decoder-v2-sync")
            unified = reconstruct_work_gray(transformed, normalized)
            cache = {}
            details.append({"key": row["key"], "profile": row.get("profile"), "transform": row.get("transform"), "transform_range": row.get("transform_range"), "normalized_geometry": normalized, "legacy_vs_oracle": _metrics(old, legacy, row.get("expected_content_id"), cache=cache), "unified_vs_oracle": _metrics(old, unified, row.get("expected_content_id"), cache=cache)})
        except Exception as exc:
            details.append({"key": row.get("key"), "profile": row.get("profile"), "transform": row.get("transform"), "error": f"{type(exc).__name__}: {exc}"})
    good = [x for x in details if "error" not in x]
    return {
        "schema": "round7-geometry-diagnostic/v2", "source": str(path).replace("\\", "/"),
        "target_definition": "stored oracle FOUND with stored sync expected failure",
        "target_rows_selected": len(rows), "completed_rows": len(good), "error_rows": len(details) - len(good), "rows": details,
        "evidence_summary": {
            "old_oracle_expected": sum(bool(x["legacy_vs_oracle"].get("old_expected")) for x in good),
            "legacy_true_geometry_expected": sum(bool(x["legacy_vs_oracle"].get("candidate_expected")) for x in good),
            "unified_true_geometry_expected": sum(bool(x["unified_vs_oracle"].get("candidate_expected")) for x in good),
            "legacy_mean_mse": _mean([x["legacy_vs_oracle"] for x in good], "mse"),
            "unified_mean_mse": _mean([x["unified_vs_oracle"] for x in good], "mse"),
            "legacy_mean_ecc_input_bit_errors": _mean([x["legacy_vs_oracle"] for x in good], "candidate_ecc_input_bit_errors"),
            "unified_mean_ecc_input_bit_errors": _mean([x["unified_vs_oracle"] for x in good], "candidate_ecc_input_bit_errors"),
        },
    }


def _mean(rows, key):
    vals = [float(x[key]) for x in rows if isinstance(x.get(key), (int, float))]
    return round(sum(vals) / len(vals), 6) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("input", nargs="+", help="paired.jsonl"); ap.add_argument("--target", type=int, default=882); ap.add_argument("--actual", action="store_true"); ap.add_argument("--limit", type=int); ap.add_argument("--output")
    args = ap.parse_args()
    result = actual_diagnosis(args.input[0], limit=args.limit) if args.actual else diagnose([r for p in args.input for r in load_rows(p)], args.target)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output: Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text); return 0


if __name__ == "__main__":
    raise SystemExit(main())
