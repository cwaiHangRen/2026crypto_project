#!/usr/bin/env python3
"""Index already-published Round 9 image bytes for the frozen S0 subset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/round8-scale-1000-final2/images_manifest.jsonl")
    ap.add_argument("--samples", default="results/pilot-round3/20261014T-round9-default-images-800/samples.jsonl")
    ap.add_argument("--round-run", default="results/round9-scale-audit/retained-publications")
    ap.add_argument("--count", type=int, default=80)
    args = ap.parse_args()
    manifest = sorted(jsonl(ROOT / args.manifest), key=lambda r: str(r.get("sample_id", "")))[: args.count]
    source_rows = {str(r.get("sample_id")): r for r in jsonl(ROOT / args.samples) if r.get("group") == "G3" and r.get("transform") == "exact" and r.get("sidecar") == "retained" and (r.get("truth") or {}).get("expected_outcome") == "VERIFIED_EXACT"}
    out = ROOT / args.round_run; out.mkdir(parents=True, exist_ok=True)
    attempts = []
    for sample in manifest:
        sid = str(sample["sample_id"]); row = source_rows.get(sid)
        item = {"sample_id": sid, "profile": "v1", "transform": "exact", "published": False, "source_manifest": args.samples}
        if row:
            media_path = Path(str(row["media_path"])); cid = str((row.get("truth") or {}).get("expected_content_id")); manifest_path = media_path.parent / "registry" / "manifests" / f"{cid}.json"
            if media_path.exists() and manifest_path.exists():
                item.update({"published": True, "content_id": cid, "final_path": str(media_path.relative_to(ROOT)).replace("\\", "/"), "manifest_path": str(manifest_path.relative_to(ROOT)).replace("\\", "/"), "final_sha256": sha(media_path), "manifest_sha256": sha(manifest_path)})
        attempts.append(item)
    (out / "attempts.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in attempts), encoding="utf-8")
    (out / "run_manifest.json").write_text(json.dumps({"schema_version": "round9-retained-publications-1", "source_run": args.samples, "selection": f"first {args.count} rows from frozen image manifest before observing outcomes", "attempts": len(attempts), "published": sum(bool(x["published"]) for x in attempts)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "attempts": len(attempts), "published": sum(bool(x["published"]) for x in attempts), "gate_failed": sum(not bool(x["published"]) for x in attempts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
