"""Reclassify frozen out-of-scope transform rows without editing history."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def execution_result(row: dict, classification: str) -> str:
    """Return the observed outcome used by the input/result cross table."""
    if classification == "invalid_transform":
        return "invalid_transform"
    statuses = {
        ((row.get("d0") or {}).get("observed") or {}).get("status"),
        ((row.get("sync") or {}).get("observed") or {}).get("status"),
    }
    if "TIME_BUDGET_EXHAUSTED" in statuses:
        return "timeout"
    if "ERROR" in statuses:
        return "execution_exception"
    return str(row.get("pair_cell") or row.get("status") or "unknown")


def detector_outcome(observed: dict | None, expected: str | None) -> str:
    """Normalize one detector's result without changing the frozen record."""
    observed = observed or {}
    status = observed.get("status")
    if status == "TIME_BUDGET_EXHAUSTED":
        return "budget_exhausted"
    if status == "ERROR":
        return "execution_exception"
    ids = sorted(set(map(str, observed.get("content_ids") or observed.get("recovered_content_ids") or [])))
    if status == "CONFLICT" or len(ids) > 1:
        return "conflict"
    if expected and status == "FOUND" and ids == [str(expected)]:
        return "correct_recovery"
    if ids:
        return "wrong_id"
    if status == "NOT_FOUND":
        return "not_found"
    return "unknown"


def run(args) -> Path:
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    old_rows = {row["key"]: row for row in jsonl(ROOT / args.old)}
    new_rows = {row["key"]: row for row in jsonl(ROOT / args.new)}
    task_manifest = {row["key"]: row for row in jsonl(ROOT / args.task_manifest)}
    rows = []
    for key, row in sorted(new_rows.items()):
        old = old_rows.get(key, {})
        task = task_manifest.get(key, {})
        transform_range = row.get("transform_range")
        transform_error = bool(row.get("transform_error"))
        sync_status = ((row.get("sync") or {}).get("observed") or {}).get("status")
        d0_status = ((row.get("d0") or {}).get("observed") or {}).get("status")
        if transform_error and transform_range == "out_of_scope":
            classification = "invalid_transform"
            detector_attempted = False
            denominator = False
        elif sync_status == "TIME_BUDGET_EXHAUSTED" or d0_status == "TIME_BUDGET_EXHAUSTED":
            classification = "resource_exhausted"
            detector_attempted = True
            denominator = True
        elif sync_status == "ERROR" or d0_status == "ERROR":
            classification = "execution_exception"
            detector_attempted = True
            denominator = True
        elif transform_range in {"boundary"}:
            classification = "valid_boundary"
            detector_attempted = True
            denominator = True
        elif transform_range in {"in_scope", "in_scope_offset", "in_scope_unseen_grid", "in_scope_combo", "in_scope_combo_unseen_grid"}:
            classification = "valid_propagation"
            detector_attempted = True
            denominator = True
        else:
            classification = "unclassified"
            detector_attempted = bool(row.get("d0") or row.get("sync"))
            denominator = detector_attempted
        result = execution_result(row, classification)
        expected = row.get("expected_content_id")
        detector_results = {
            "d0": detector_outcome((row.get("d0") or {}).get("observed"), expected),
            "sync": detector_outcome((row.get("sync") or {}).get("observed"), expected),
        }
        rows.append({
            "key": key,
            "sample_id": row.get("sample_id"),
            "profile": row.get("profile"),
            "transform": row.get("transform"),
            "transform_range": transform_range,
            "new_class": classification,
            "execution_result": result,
            "detector_results": detector_results,
            "detector_attempted": detector_attempted,
            "excluded_from_detector_denominator": not denominator,
            "old_status": row.get("status", "TRANSFORM_ERROR"),
            "old_error": row.get("error"),
            "old_pair_cell": row.get("pair_cell"),
            "old_d0_status": ((row.get("d0") or {}).get("observed") or {}).get("status"),
            "old_sync_status": sync_status,
            "round6_task_id": old.get("task_id"),
            "round7_task_id": row.get("task_id") or task.get("task_id"),
            "execution_fingerprint": row.get("execution_fingerprint") or task.get("execution_fingerprint"),
        })
    mapping_path = out / "task_id_mapping.jsonl"
    mapping_path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    summary = {
        "schema_version": "round9-transform-reclassification-2",
        "source_old": str((ROOT / args.old).relative_to(ROOT)).replace("\\", "/"),
        "source_new": str((ROOT / args.new).relative_to(ROOT)).replace("\\", "/"),
        "source_old_sha256": sha(ROOT / args.old),
        "source_new_sha256": sha(ROOT / args.new),
        "task_manifest_sha256": sha(ROOT / args.task_manifest),
        "paired_rows_preserved": len(new_rows),
        "rows_mapped": len(rows),
        "by_class": dict(Counter(row["new_class"] for row in rows)),
        "by_transform": dict(Counter(row["transform"] for row in rows)),
        "by_profile": dict(Counter(row["profile"] for row in rows)),
        "input_type_execution_cross_table": {
            category: dict(Counter(item["execution_result"] for item in rows if item["transform_range"] == category))
            for category in sorted({item["transform_range"] for item in rows})
        },
        "detector_outcome_by_input_type": {
            category: {
                detector: dict(Counter(item["detector_results"][detector] for item in rows if item["transform_range"] == category))
                for detector in ("d0", "sync")
            }
            for category in sorted({item["transform_range"] for item in rows})
        },
        "detector_denominator_by_input_type": {
            category: sum(bool(item["detector_attempted"]) for item in rows if item["transform_range"] == category)
            for category in sorted({item["transform_range"] for item in rows})
        },
        "detector_attempted": sum(bool(row["detector_attempted"]) for row in rows),
        "excluded_from_detector_denominator": sum(bool(row["excluded_from_detector_denominator"]) for row in rows),
        "valid_detector_rows": sum(bool(row["detector_attempted"]) for row in rows),
        "missing_paired_task_rows": len((set(task_manifest) - set(new_rows)) - {key for key, item in task_manifest.items() if item.get("role") == "negative"}),
        "manifest_nonpaired_rows": sum(1 for key, item in task_manifest.items() if key not in new_rows and item.get("role") == "negative"),
        "old_records_unchanged": True,
        "task_id_mapping": str(mapping_path.relative_to(ROOT)).replace("\\", "/"),
        "note": "The frozen JSONL is preserved. Invalid transforms are rejected before valid media exists; valid timeouts and detector exceptions remain in the valid-input denominator.",
    }
    (out / "invalid_transform_reclassification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclassify illegal transform rows")
    parser.add_argument("--old", default="results/round6-sync/20261008T-round6-sync-final-r2/paired.jsonl")
    parser.add_argument("--new", default="results/round7-sync/20261011T-round7-sync-v3-fp2/paired.jsonl")
    parser.add_argument("--task-manifest", default="results/round7-sync/20261011T-round7-sync-v3-fp2/task_manifest.jsonl")
    parser.add_argument("--output", default="results/round9-correction-20261014/reclassification")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
