"""Audit Round 8 evidence from frozen Round 7 records.

This script only reads existing result/data files. It does not rerun a
detector, mutate a registry, or retry a failed publication with another ID.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def correct(recovery: dict[str, Any] | None) -> bool:
    return bool(recovery and recovery.get("correct_single_output"))


def sync_success(row: dict[str, Any]) -> bool:
    """Use the frozen pairing cell so older rows with missing recovery fields remain auditable."""
    if row.get("pair_cell") in {"both_success", "d0_failed_sync_success"}:
        return True
    return correct(((row.get("sync") or {}).get("recovery")))


def detector_fields(row: dict[str, Any], key: str) -> dict[str, Any]:
    observed = ((row.get(key) or {}).get("observed") or {})
    details = observed.get("details") or {}
    return {
        "status": observed.get("status"),
        "content_ids": observed.get("content_ids", []),
        "search_complete": row.get("search_complete") if key == "sync" else None,
        "stop_reason": row.get("stop_reason") if key == "sync" else None,
        "candidate_count": row.get("candidate_count") if key == "sync" else None,
        "decode_attempts": row.get("decode_attempts") if key == "sync" else None,
        "elapsed_ms": row.get("sync_ms") if key == "sync" else row.get("d0_ms"),
        "budget": details.get("budget") if key == "sync" else None,
    }


def compare_sync(old_path: Path, new_path: Path, out: Path) -> dict[str, Any]:
    old = {row["key"]: row for row in read_jsonl(old_path)}
    new = {row["key"]: row for row in read_jsonl(new_path)}
    if set(old) != set(new):
        raise SystemExit(f"paired task sets differ: old={len(old)} new={len(new)}")
    regressions: list[dict[str, Any]] = []
    rescues: list[dict[str, Any]] = []
    both_success = both_failed = 0
    for key in sorted(old):
        a, b = old[key], new[key]
        s0 = sync_success(a)
        v3 = sync_success(b)
        record = {
            "key": key,
            "sample_id": b.get("sample_id"),
            "source_family_id": b.get("source_family_id"),
            "profile": b.get("profile"),
            "transform": b.get("transform"),
            "transform_range": b.get("transform_range"),
            "final_source": b.get("final_source"),
            "final_sha256": b.get("final_sha256"),
            "transformed_sha256": b.get("transformed_sha256"),
            "expected_content_id": b.get("expected_content_id"),
            "old_detector": detector_fields(a, "sync"),
            "new_detector": detector_fields(b, "sync"),
            "old_pair_cell": a.get("pair_cell"),
            "new_pair_cell": b.get("pair_cell"),
            "old_execution_fingerprint": a.get("execution_fingerprint"),
            "new_execution_fingerprint": b.get("execution_fingerprint"),
        }
        if s0 and v3:
            both_success += 1
        elif not s0 and not v3:
            both_failed += 1
        elif s0 and not v3:
            regressions.append(record)
        else:
            rescues.append(record)
    write_json(out / "strict_regressions_244.json", regressions)
    write_json(out / "new_successes_545.json", rescues)
    summary = {
        "schema_version": "round8-sync-audit-1",
        "old_rows": len(old),
        "new_rows": len(new),
        "s0_success": both_success + len(regressions),
        "v3_success": both_success + len(rescues),
        "only_s0_success": len(regressions),
        "only_v3_success": len(rescues),
        "both_success": both_success,
        "both_failed": both_failed,
        "regression_by_profile": dict(Counter(x["profile"] for x in regressions)),
        "regression_by_transform": dict(Counter(x["transform"] for x in regressions)),
        "rescue_by_profile": dict(Counter(x["profile"] for x in rescues)),
        "rescue_by_transform": dict(Counter(x["transform"] for x in rescues)),
        "old_run": str(old_path.relative_to(ROOT)).replace("\\", "/"),
        "new_run": str(new_path.relative_to(ROOT)).replace("\\", "/"),
    }
    write_json(out / "sync_comparison_summary.json", summary)
    return summary


def classify_failure(error: str | None, attrs: dict[str, Any]) -> tuple[str, str]:
    text = error or ""
    if "16MP" in text:
        return "A", "declared maximum pixel count exceeded"
    if "不支持" in text or "unsupported" in text.lower() or "quality budget" in text:
        return "B", "preprocess, mode, or quality-budget guard rejected input"
    if "encode" in text.lower() or "media" in text.lower():
        return "C", "encoding or media processing error"
    if attrs.get("mode") not in {"RGB", "L"}:
        return "B", "input mode outside the profile implementation"
    return "F", "failure evidence is insufficient for a narrower category"


def audit_external(attempts_path: Path, manifest_path: Path, out: Path) -> dict[str, Any]:
    manifest = {x["sample_id"]: x for x in read_json(manifest_path)["items"]}
    failures: list[dict[str, Any]] = []
    all_rows = read_jsonl(attempts_path)
    for row in all_rows:
        if row.get("published"):
            continue
        sample = manifest.get(row.get("sample_id"), {})
        attrs = sample.get("attrs", {})
        category, reason = classify_failure(row.get("error"), attrs)
        failures.append(
            {
                "sample_id": row.get("sample_id"),
                "source_family_id": sample.get("source_family_id"),
                "profile": row.get("profile"),
                "declared_support": "image/JPEG or PNG; profile-specific guards apply",
                "format": attrs.get("format"),
                "mode": attrs.get("mode"),
                "exif_orientation": attrs.get("exif_orientation", 1),
                "width": attrs.get("width"),
                "height": attrs.get("height"),
                "pixels": (attrs.get("width") or 0) * (attrs.get("height") or 0),
                "source_bytes": row.get("source_bytes", row.get("bytes")),
                "embed_executed": bool(row.get("attempted")),
                "encoded": bool(row.get("final_sha256")),
                "gate_status": ((row.get("gate") or {}).get("result") or {}).get("status"),
                "gate_detector_status": ((row.get("gate") or {}).get("result") or {}).get("detector_status"),
                "candidate_ids": ((row.get("gate") or {}).get("result") or {}).get("content_ids", []),
                "expected_content_id": row.get("content_id"),
                "error": row.get("error"),
                "failure_category": category,
                "failure_reason": reason,
                "registry_side_effect": False,
                "version_side_effect": False,
                "state_side_effect": False,
            }
        )
    write_json(out / "external_gate_failures.json", failures)
    summary = {
        "schema_version": "round8-external-audit-1",
        "attempts": len(all_rows),
        "published": sum(bool(x.get("published")) for x in all_rows),
        "gate_failures": len(failures),
        "failure_categories": dict(Counter(x["failure_category"] for x in failures)),
        "failure_by_profile": dict(Counter(f"{x['profile']}|{x['failure_category']}" for x in failures)),
        "attempts_path": str(attempts_path.relative_to(ROOT)).replace("\\", "/"),
        "manifest_path": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
    }
    write_json(out / "external_audit_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Round 8 audit of frozen Round 7 records")
    parser.add_argument("--old", default="results/round6-sync/20261008T-round6-sync-final-r2/paired.jsonl")
    parser.add_argument("--new", default="results/round7-sync/20261011T-round7-sync-v3-fp2/paired.jsonl")
    parser.add_argument("--attempts", default="results/round7-challenge-marked/20261011T-round7-challenge-marked/attempts.jsonl")
    parser.add_argument("--manifest", default="data/round7-challenge-20261011/manifest.json")
    parser.add_argument("--output", default="results/round8-audit")
    args = parser.parse_args()
    out = ROOT / args.output
    sync = compare_sync(ROOT / args.old, ROOT / args.new, out)
    external = audit_external(ROOT / args.attempts, ROOT / args.manifest, out)
    write_json(out / "summary.json", {"schema_version": "round8-audit-1", "sync": sync, "external": external})
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
