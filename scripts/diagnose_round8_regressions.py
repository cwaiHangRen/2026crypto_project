"""Bounded, repeated diagnosis of representative Round 7 v3 regressions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_round6_sync_eval as evaluator  # noqa: E402
from gm_provenance import media, watermark_sync  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def detector(config, blob: bytes) -> dict:
    started = time.perf_counter()
    result = evaluator._detector(watermark_sync, blob, config)
    result["wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    details = result.get("details") or {}
    result["diagnostic"] = {
        "status": result.get("status"),
        "content_ids": result.get("content_ids", []),
        "candidate_count": details.get("candidate_count"),
        "decode_attempts": details.get("decode_attempts"),
        "search_complete": details.get("search_complete"),
        "stop_reason": details.get("stop_reason"),
        "elapsed_ms": details.get("elapsed_ms"),
        "budget": details.get("budget"),
        "decoder_profile": details.get("decoder_profile"),
    }
    result.pop("details", None)
    return result


def select_cases(rows: list[dict], limit: int) -> list[dict]:
    preferred = [
        "crop_center_area_0.90", "crop_center_area_0.95", "crop_offset_h_pos",
        "crop_offset_v_pos", "crop_nongrid_area_0.943", "crop_nongrid_area_0.957",
        "crop_resize_jpeg_0.957", "crop_boundary_area_0.99",
    ]
    picked: list[dict] = []
    used: set[tuple[str, str]] = set()
    for transform in preferred:
        for profile in ("v1", "v2", "feedback"):
            choices = [x for x in rows if x.get("transform") == transform and x.get("profile") == profile]
            if choices:
                row = sorted(choices, key=lambda x: (x.get("sample_id", ""), x.get("key", "")))[0]
                picked.append(row)
                used.add((profile, transform))
    for row in sorted(rows, key=lambda x: x.get("key", "")):
        key = (row.get("profile"), row.get("transform"))
        if key not in used:
            picked.append(row)
            used.add(key)
        if len(picked) >= limit:
            break
    return picked[:limit]


def run(args) -> Path:
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    regressions = read_json(ROOT / args.regressions)
    old_rows = {x["key"]: x for x in read_jsonl(ROOT / args.old)}
    config = evaluator.load_config(args.config)
    specs = {str(x["name"]): x for x in config.get("transforms", [])}
    cases = select_cases(regressions, args.cases)
    records = []
    for row in cases:
        old = old_rows[row["key"]]
        final = (ROOT / str(row["final_source"])).read_bytes()
        transformed, geometry = evaluator.transform(final, specs[row["transform"]])
        d0 = evaluator._d0(media, transformed)
        rec = {
            "key": row["key"], "sample_id": row.get("sample_id"),
            "profile": row.get("profile"), "transform": row.get("transform"),
            "transform_range": row.get("transform_range"),
            "final_sha256": row.get("final_sha256"),
            "transformed_sha256": row.get("transformed_sha256"),
            "source_size": geometry.get("source_size"),
            "transformed_size": geometry.get("output_size"),
            "d0": {"status": d0.get("status"), "content_ids": d0.get("content_ids", [])},
            "expected_old_success": old.get("pair_cell") in {"both_success", "d0_failed_sync_success"},
            "repetitions": [],
        }
        for repeat in range(1, args.repetitions + 1):
            # Interleave versions on the same immutable bytes for each repeat.
            old_result = detector({**config, "decoder": {**config["decoder"], "profile": "decoder-v2-sync"}}, transformed)
            new_result = detector({**config, "decoder": {**config["decoder"], "profile": "decoder-v3-sync"}}, transformed)
            rec["repetitions"].append({"repeat": repeat, "s0": old_result["diagnostic"], "v3": new_result["diagnostic"]})
        records.append(rec)
    (out / "representative_regressions.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema_version": "round8-regression-diagnostic-1",
        "cases": len(records), "repetitions": args.repetitions,
        "source_regression_count": len(regressions),
        "candidate_sequence_exposed": False,
        "candidate_sequence_note": "The production detector exposes candidate count and decode attempts, not raw candidate coordinates; no truth is passed to detection.",
        "case_keys": [x["key"] for x in records],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Repeat representative v3 regression cases")
    parser.add_argument("--regressions", default="results/round8-audit/strict_regressions_244.json")
    parser.add_argument("--old", default="results/round6-sync/20261008T-round6-sync-final-r2/paired.jsonl")
    parser.add_argument("--config", default="configs/round6-sync-evaluation.json")
    parser.add_argument("--cases", type=int, default=24)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", default="results/round8-regression-diagnostic")
    args = parser.parse_args()
    print(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
