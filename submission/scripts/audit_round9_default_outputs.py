#!/usr/bin/env python3
"""Audit the frozen Round 9 default runs without re-publishing media.

The scale runner records full verification.  This companion reads those
durable final bytes and performs an explicit D0 detector pass.  It also runs
the existing A/B/C quality diagnostic on a manifest-first, fixed 10-image /
10-PDF subset; a gate failure remains a skipped quality item rather than a
replacement sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gm_provenance import media, quality  # noqa: E402


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def resolve(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-run", default="results/pilot-round3/20261014T-round9-default-images-800")
    ap.add_argument("--pdfs-run", default="results/pilot-round3/20261014T-round9-default-pdfs-200")
    ap.add_argument("--images-manifest", default="data/round8-scale-1000-final2/images_manifest.jsonl")
    ap.add_argument("--pdfs-manifest", default="data/round8-scale-1000-final2/pdfs_manifest.jsonl")
    ap.add_argument("--output", default="results/round9-scale-audit")
    args = ap.parse_args()
    out = resolve(args.output); out.mkdir(parents=True, exist_ok=True)
    sample_rows = rows(resolve(args.images_run) / "samples.jsonl") + rows(resolve(args.pdfs_run) / "samples.jsonl")
    g0 = [r for r in sample_rows if r.get("group") == "G0" and r.get("transform") == "exact"]
    # The evaluator records one ordinary retained row and one explicit full
    # state row for G3 exact.  Use the latter once per sample for the D0 pass.
    g3 = [r for r in sample_rows if r.get("group") == "G3" and r.get("transform") == "exact" and r.get("sidecar") == "retained" and (r.get("truth") or {}).get("expected_outcome") == "VERIFIED_EXACT"]
    d0_rows: list[dict] = []
    for row in g3:
        path = resolve(row.get("media_path", ""))
        expected = (row.get("truth") or {}).get("expected_content_id")
        if not path.exists() or str(row.get("media_path")) == "not retained":
            d0_rows.append({"sample_id": row.get("sample_id"), "status": "PUBLISH_GATE_FAILED", "expected_content_id": expected, "detector_attempted": False})
            continue
        data = path.read_bytes(); observed = media.detect(data, row.get("media_type"))
        ids = sorted(set(observed.get("recovered_content_ids") or observed.get("content_ids") or []))
        d0_rows.append({"sample_id": row.get("sample_id"), "media_type": row.get("media_type"), "final_sha256": sha(path), "expected_content_id": expected, "observed": observed, "expected_id_hit": expected in ids, "correct_single_output": observed.get("status") == "FOUND" and ids == [expected], "detector_attempted": True, "detector": "dct-difference-v1"})
    d0_path = out / "default_d0.jsonl"
    d0_path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in d0_rows), encoding="utf-8")
    negative = [{"sample_id": r.get("sample_id"), "observed": r.get("observed"), "expected_no_watermark": True, "false_positive": bool((r.get("observed") or {}).get("recovered_content_ids"))} for r in g0]
    (out / "default_negative_g0.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in negative), encoding="utf-8")

    manifests = []
    for kind, manifest_path, run_path in (("image", args.images_manifest, args.images_run), ("pdf", args.pdfs_manifest, args.pdfs_run)):
        selected = rows(resolve(manifest_path))[:10]
        by_id = {str(r.get("sample_id")): r for r in sample_rows}
        for sample in selected:
            sid = str(sample["sample_id"]); exact = next((r for r in g3 if r.get("sample_id") == sid), None)
            item = {"sample_id": sid, "kind": kind, "selected_by": f"first-10-in-{manifest_path}", "source_path": sample.get("local_path"), "quality_attempted": False}
            if not exact or str(exact.get("media_path")) == "not retained" or not Path(str(exact.get("media_path", ""))).exists():
                item["status"] = "PUBLISH_GATE_FAILED"; manifests.append(item); continue
            source = ROOT / str(sample["local_path"]); published = Path(str(exact["media_path"]))
            try:
                item["quality"] = quality.analyze(source, published, out / "quality" / sid, sid); item["quality_attempted"] = True; item["status"] = "PASS"
            except Exception as exc:
                item.update({"status": "QUALITY_ERROR", "error_type": type(exc).__name__})
            manifests.append(item)
    (out / "quality_fixed_subset.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n" for r in manifests), encoding="utf-8")
    summary = {
        "schema_version": "round9-default-output-audit-1",
        "runs": {"images": args.images_run, "pdfs": args.pdfs_run},
        "default_d0": {"base_attempts": len(rows(resolve(args.images_manifest))) + len(rows(resolve(args.pdfs_manifest))), "published_rows": len(d0_rows), "detector_attempted": sum(bool(r.get("detector_attempted")) for r in d0_rows), "gate_failed": len(rows(resolve(args.images_manifest))) + len(rows(resolve(args.pdfs_manifest))) - len(d0_rows), "expected_id_hit": sum(bool(r.get("expected_id_hit")) for r in d0_rows), "correct_single_output": sum(bool(r.get("correct_single_output")) for r in d0_rows), "status_counts": dict(Counter((r.get("observed") or {}).get("status", r.get("status", "UNKNOWN")) for r in d0_rows))},
        "negative_g0": {"rows": len(negative), "false_positive": sum(bool(r.get("false_positive")) for r in negative), "status_counts": dict(Counter((r.get("observed") or {}).get("watermark_status", "UNKNOWN") for r in negative))},
        "quality_fixed_subset": {"selected": len(manifests), "attempted": sum(bool(r.get("quality_attempted")) for r in manifests), "passed": sum(r.get("status") == "PASS" for r in manifests), "status_counts": dict(Counter(r.get("status") for r in manifests)), "selection_rule": "first 10 rows of each frozen manifest before observing outcomes"},
        "note": "D0 is measured on the final published bytes. Gate failures have no media and therefore no decoder record; they remain in the end-to-end denominator. Quality is a fixed 20-row diagnostic subset, not a whole-corpus quality claim.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
