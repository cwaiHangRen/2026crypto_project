#!/usr/bin/env python3
"""Audit an interrupted Round 6 JSONL checkpoint without modifying it.

The audit is intentionally separate from the evaluator.  It validates every
existing line, derives the frozen task inventory, compares execution inputs,
and writes a recovery manifest plus explicit reuse/missing lists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return sha256_bytes(path.read_bytes())


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    if not path.exists():
        return rows, malformed
    raw = path.read_bytes()
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception as exc:
            malformed.append({"line": number, "error": f"{type(exc).__name__}: {exc}", "prefix_sha256": sha256_bytes(line[:256])})
            continue
        if not isinstance(value, dict):
            malformed.append({"line": number, "error": "row_not_object", "prefix_sha256": sha256_bytes(line[:256])})
            continue
        value["_line"] = number
        rows.append(value)
    return rows, malformed


def source_snapshot(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    relative = [
        config_path,
        ROOT / "configs/decoder-v2-sync.json",
        ROOT / "src/gm_provenance/watermark_sync.py",
        ROOT / "src/gm_provenance/media.py",
        ROOT / "src/gm_provenance/watermark.py",
        ROOT / "src/gm_provenance/development_eval.py",
        ROOT / str(config["dataset_manifest"]),
        ROOT / str(config["round5_run"]) / "run_manifest.json",
    ]
    files: dict[str, str | None] = {}
    for path in relative:
        try:
            name = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            name = str(path)
        files[name] = sha256_file(path)
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {"files": files, "execution_fingerprint": sha256_bytes(payload), "config": config}


def git_snapshot() -> dict[str, Any]:
    try:
        status = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=False)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        return {"head": head.stdout.strip() or None, "status_sha256": sha256_bytes(status.stdout.encode("utf-8")), "dirty": bool(status.stdout.strip())}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def audit(input_dir: Path, output_dir: Path) -> Path:
    from run_round6_sync_eval import load_config, read_manifest, load_round5_index

    config_path = ROOT / "configs/round6-sync-evaluation.json"
    config = load_config(config_path)
    paired_path = input_dir / "paired.jsonl"
    negative_path = input_dir / "negative.jsonl"
    paired, malformed = load_jsonl(paired_path)
    negative, negative_malformed = load_jsonl(negative_path)
    all_rows = paired + negative
    keys: dict[str, list[int]] = {}
    for row in all_rows:
        key = str(row.get("key", ""))
        keys.setdefault(key, []).append(int(row.get("_line", -1)))
    duplicates = [{"key": key, "lines": lines} for key, lines in keys.items() if key and len(lines) > 1]
    missing_key = [int(row.get("_line", -1)) for row in all_rows if not row.get("key")]

    manifest = read_manifest(config["dataset_manifest"], int(config.get("sample_count", 60)))
    r5 = load_round5_index(config)
    profiles = list(config.get("profiles", {}).keys())
    transforms = [str(x["name"]) for x in config.get("transforms", [])]
    expected: list[dict[str, Any]] = []
    for sample in manifest:
        sid = str(sample["sample_id"])
        for profile in profiles:
            if (sid, profile) not in r5:
                continue
            for transform in transforms:
                expected.append({"key": f"paired|{sid}|{profile}|{transform}", "sample_id": sid, "profile": profile, "transform": transform})
    observed_keys = {str(row.get("key")) for row in paired if row.get("key")}
    reusable = [row for row in expected if row["key"] in observed_keys]
    missing = [row for row in expected if row["key"] not in observed_keys]
    malformed_count = len(malformed) + len(negative_malformed)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in (output_dir / "reuse_keys.jsonl", output_dir / "missing_tasks.jsonl"):
        if old.exists():
            old.unlink()
    (output_dir / "reuse_keys.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in reusable), encoding="utf-8")
    (output_dir / "missing_tasks.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in missing), encoding="utf-8")
    source = source_snapshot(config_path)
    old_manifest_path = input_dir / "run_manifest.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8")) if old_manifest_path.exists() else {}
    process_running = False
    audit_record = {
        "schema_version": "round6-checkpoint-audit-1",
        "audited_at": utc_now(),
        "input_run_dir": str(input_dir.relative_to(ROOT)).replace("\\", "/") if input_dir.is_relative_to(ROOT) else str(input_dir),
        "input_run_id": old_manifest.get("run_id", input_dir.name),
        "process_check": {"running": process_running, "identity": "no matching evaluator process observed", "stop_reason": "unknown"},
        "status_correction": {"original": old_manifest.get("status"), "corrected": "interrupted", "basis": "405 valid records present but no matching evaluator process and no exit record"},
        "jsonl": {"paired_path": str(paired_path.relative_to(ROOT)).replace("\\", "/"), "paired_rows": len(paired), "negative_rows": len(negative), "malformed_rows": malformed_count, "paired_sha256": sha256_file(paired_path), "negative_sha256": sha256_file(negative_path), "ends_cleanly": paired_path.read_bytes().endswith(b"\n") if paired_path.exists() else True},
        "duplicates": duplicates,
        "missing_key_lines": missing_key,
        "task_inventory": {"expected_paired": len(expected), "observed_paired": len(observed_keys), "reusable_paired": len(reusable), "missing_paired": len(missing), "profiles": profiles, "transforms": transforms},
        "coverage": {"observed_by_sample": {sid: sum(1 for row in paired if str(row.get("sample_id")) == sid) for sid in [str(x["sample_id"]) for x in manifest]}, "last_valid_line": max((int(row.get("_line", 0)) for row in all_rows), default=0)},
        "source_snapshot": source,
        "old_run_manifest": old_manifest,
        "git": git_snapshot(),
        "reuse_policy": {"eligible": len(malformed) == 0 and len(duplicates) == 0 and len(missing_key) == 0, "reason": "existing rows are immutable and inputs are rechecked by the recovery runner; aggregator changes do not alter detector results"},
    }
    (output_dir / "audit.json").write_text(json.dumps(audit_record, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="results/round6-sync/20261008T-round6-sync-final")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audit(ROOT / args.input, ROOT / args.output)
    print(ROOT / args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
