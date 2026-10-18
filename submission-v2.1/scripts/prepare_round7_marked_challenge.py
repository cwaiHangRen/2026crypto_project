#!/usr/bin/env python3
"""Prepare the immutable Round 7 marked challenge fixtures.

This is an adapter around the Round 5 embedding and publish gate.  It does
not run a detector experiment and it never edits the challenge manifest.  The
output directory intentionally has the Round 5 layout consumed by Round 6.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import run_round5_watermark_eval as r5


PROFILES = {"v1": "watermark-v1", "v2": "watermark-v2-residual", "feedback": "watermark-v2-feedback"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_challenge(path: Path) -> list[dict[str, Any]]:
    """Flatten the object manifest to the JSONL shape used by Round 6."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    items = [x for x in doc.get("items", []) if x.get("challenge_included") is True]
    if len(items) != 12:
        raise ValueError(f"expected exactly 12 included challenge items, got {len(items)}")
    rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: str(x["sample_id"])):
        acq = item["acquisition"]
        src = ROOT / str(acq["local_path"])
        if not src.exists() or _sha(src) != str(acq.get("sha256")):
            raise ValueError(f"challenge integrity failure: {item.get('sample_id')}")
        fmt = str(item.get("attrs", {}).get("format", "")).upper()
        media_type = "image/jpeg" if fmt in {"JPG", "JPEG"} else "image/png" if fmt == "PNG" else None
        if media_type is None:
            raise ValueError(f"unsupported challenge format: {item.get('sample_id')} / {fmt}")
        rows.append({"sample_id": str(item["sample_id"]), "source_family_id": item.get("source_family_id"),
                     "local_path": str(acq["local_path"]), "sha256": str(acq["sha256"]),
                     "media_type": media_type, "category": item.get("category"),
                     "license": item.get("license"), "license_reference": item.get("license_reference")})
    return rows


def prepare(manifest_path: Path, out: Path, seed: int, run_id: str) -> Path:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing fixture run: {out}")
    out.mkdir(parents=True, exist_ok=False)
    rows = load_challenge(manifest_path)
    challenge_jsonl = out / "challenge_dataset.jsonl"
    with challenge_jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    from gm_provenance import media, watermark
    try:
        from gm_provenance import r5_feedback
    except Exception:
        try:
            from gm_provenance import watermark_feedback as r5_feedback
        except Exception:
            r5_feedback = None

    attempts: list[dict[str, Any]] = []
    rejects = 0
    for sample in rows:
        sid = str(sample["sample_id"])
        original = (ROOT / sample["local_path"]).read_bytes()
        mt = str(sample["media_type"])
        fmt = "JPEG" if mt == "image/jpeg" else "PNG"
        # One immutable CID per source fixture, shared by all three profiles.
        cid = r5.deterministic_cid(seed, sid, 0)
        sample_dir = out / "samples" / sid
        sample_dir.mkdir(parents=True, exist_ok=True)
        for profile in PROFILES:
            row: dict[str, Any] = {"sample_id": sid, "profile": profile, "transform": "exact",
                                   "content_id": cid, "attempted": True, "published": False,
                                   "gate_passed": False, "source_sha256": sample["sha256"]}
            try:
                marked, info = r5.embed_profile(profile, original, cid, mt, fmt, media, watermark, r5_feedback)
                gate = r5.publish_gate(media, marked, cid, mt)
                passed = bool(gate.get("passed"))
                row.update({"gate_passed": passed, "published": passed,
                            "final_sha256": r5.sha256_bytes(marked), "bytes": len(marked),
                            "gate": gate, "embed_profile": info})
                if passed:
                    (sample_dir / f"{profile}-final.bin").write_bytes(marked)
                else:
                    rejects += 1
                    (sample_dir / f"{profile}-rejected-final.bin").write_bytes(marked)
            except Exception as exc:
                rejects += 1
                row.update({"error": f"{type(exc).__name__}: {exc}"})
            with (out / "attempts.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            attempts.append(row)

    summary = {"status": "completed", "challenge_id": "round7-challenge-20261011",
               "sample_count": len(rows), "profile_count": len(PROFILES),
               "expected_gate_attempts": 36, "gate_passed": sum(bool(x.get("gate_passed")) for x in attempts),
               "gate_rejected": rejects, "published": sum(bool(x.get("published")) for x in attempts),
               "negative_blindtests_included": False, "cid_rule": "round4|seed|sample_id|slot=0",
               "seed": seed, "run_id": run_id, "finished_at": _now()}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_manifest = {"schema_version": "round7-marked-challenge-1", "run_id": run_id,
                    "status": "completed", "started_at": _now(), "finished_at": _now(),
                    "dataset_manifest": str(challenge_jsonl.relative_to(ROOT)).replace("\\", "/"),
                    "sample_ids": [x["sample_id"] for x in rows], "profiles": PROFILES,
                    "expected_gate_attempts": 36, "gate_rejected": rejects,
                    "negative_blindtests_included": False,
                    "source_manifest_sha256": _sha(manifest_path), "seed": seed}
    (out / "run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare Round 7 marked challenge fixtures for Round 6")
    ap.add_argument("--manifest", default="data/round7-challenge-20261011/manifest.json")
    ap.add_argument("--out", default="results/round7-challenge-marked/20261011T-round7-challenge-marked")
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--run-id", default="20261011T-round7-challenge-marked")
    args = ap.parse_args()
    print(prepare(ROOT / args.manifest if not Path(args.manifest).is_absolute() else Path(args.manifest),
                  ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out), args.seed, args.run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
