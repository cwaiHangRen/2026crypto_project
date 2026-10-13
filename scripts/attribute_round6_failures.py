#!/usr/bin/env python3
"""Classify Round 6 failures using only recorded diagnostics."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def oracle_hit(row: dict) -> bool:
    oracle = row.get("oracle")
    observed = oracle.get("observed") if isinstance(oracle, dict) else None
    expected = row.get("expected_content_id")
    return bool(isinstance(observed, dict) and observed.get("status") == "FOUND" and expected in (observed.get("content_ids") or []))


def classify(row: dict) -> str | None:
    if row.get("role") != "paired":
        return None
    if row.get("pair_cell") in {"both_success"}:
        return None
    transform_range = str(row.get("transform_range", "unknown"))
    sync = row.get("sync", {}).get("observed", {})
    if transform_range == "out_of_scope":
        return "geometry_out_of_declared_range"
    if row.get("transform_error"):
        return "input_or_backend_error"
    if sync.get("status") in {"TIME_BUDGET_EXHAUSTED", "CANDIDATE_BUDGET_EXHAUSTED", "DECODE_BUDGET_EXHAUSTED"}:
        return "search_budget_exhausted"
    if sync.get("status") in {"ERROR", "UNAVAILABLE", "UNSUPPORTED"}:
        return "input_or_backend_error"
    if oracle_hit(row):
        if transform_range in {"in_scope_unseen_grid", "in_scope_offset", "in_scope_combo_unseen_grid"}:
            return "grid_or_mapping_insufficient"
        return "mapping_or_interpolation_difference"
    oracle = row.get("oracle")
    if oracle is None or not isinstance(oracle, dict) or "observed" not in oracle:
        return "evidence_insufficient_oracle_na"
    if sync.get("status") == "NOT_FOUND":
        return "payload_or_propagation_failure_or_evidence_insufficient"
    return "evidence_insufficient"


def run(input_dir: Path, output_dir: Path) -> Path:
    paired = input_dir / "paired.jsonl"
    rows = [json.loads(line) for line in paired.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = []
    counts = collections.Counter()
    for row in rows:
        reason = classify(row)
        if reason is None:
            continue
        record = {"key": row.get("key"), "sample_id": row.get("sample_id"), "profile": row.get("profile"), "transform": row.get("transform"), "transform_range": row.get("transform_range"), "pair_cell": row.get("pair_cell"), "sync_status": row.get("sync", {}).get("observed", {}).get("status"), "stop_reason": row.get("stop_reason"), "oracle_expected_hit": oracle_hit(row), "reason": reason}
        records.append(record); counts[reason] += 1
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "failure_attribution.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
    summary = {"schema_version": "round6-failure-attribution-1", "input": str(input_dir), "failed_rows": len(records), "counts": dict(counts), "records": len(records)}
    (output_dir / "failure_attribution_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); parser.add_argument("--output", required=True); args = parser.parse_args(); print(run(ROOT / args.input, ROOT / args.output))
