#!/usr/bin/env python3
"""Derive v2-new failure rows from a completed paired round-4 run."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="completed round4 watermark run directory")
    args = ap.parse_args()
    root = Path(args.run)
    rows = [json.loads(line) for line in (root / "paired_recovery.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_cell = {(str(row.get("sample_id")), str(row.get("transform")), str(row.get("profile"))): row for row in rows}
    failures = []
    for (sample_id, transform, profile), v2 in sorted(by_cell.items()):
        if profile != "v2" or v2.get("watermark_recovered_expected"):
            continue
        v1 = by_cell.get((sample_id, transform, "v1"), {})
        if v1.get("watermark_recovered_expected"):
            failures.append({"sample_id": sample_id, "transform": transform, "profile": "v2", "category": "v2_new_decode_failure", "v1_recovered_expected": True, "v2_recovered_expected": False, "v1_watermark_status": v1.get("watermark_status"), "v2_watermark_status": v2.get("watermark_status"), "v1_control_status": v1.get("control_status"), "v2_control_status": v2.get("control_status")})
    out = root / "new_failures.jsonl"
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in failures), encoding="utf-8")
    summary = {"count": len(failures), "by_transform": dict(Counter(str(row["transform"]) for row in failures)), "by_category": dict(Counter(str(row["category"]) for row in failures)), "definition": "v1 recovered the expected ContentID in the same sample/transform cell while v2 did not; controls are retained separately and are not treated as watermark success."}
    (root / "new_failure_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = root / "summary.json"
    if summary_path.exists():
        full = json.loads(summary_path.read_text(encoding="utf-8")); full["new_failures"] = summary; summary_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"new_failures": len(failures), "by_transform": summary["by_transform"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
