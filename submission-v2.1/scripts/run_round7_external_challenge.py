#!/usr/bin/env python3
"""Run the frozen detectors on the previously-unseen Round 7 media.

The challenge images are unwatermarked external files.  This runner never
passes a ContentID, geometry truth, filename-derived answer, or registry state
to a detector; it records the raw blind outcomes and keeps this evidence
separate from the paired watermark evaluation.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gm_provenance import media, watermark_sync


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sm3(data: bytes) -> str:
    try:
        return hashlib.new("sm3", data).hexdigest()
    except ValueError as exc:
        raise RuntimeError("当前 Python/OpenSSL 不提供 SM3，拒绝运行挑战结果") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def versions() -> dict[str, str]:
    out = {}
    for name in ("numpy", "scipy", "Pillow", "reedsolo"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "NOT_INSTALLED"
    return out


def source_snapshot() -> dict[str, str]:
    paths = [ROOT / "src/gm_provenance/watermark_sync.py", ROOT / "src/gm_provenance/media.py", ROOT / "configs/round7-development.json"]
    return {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p.read_bytes()) for p in paths}


def timed(fn, data: bytes) -> tuple[dict, float]:
    started = time.perf_counter()
    try:
        value = fn(data)
        if not isinstance(value, dict):
            value = {"status": "ERROR", "content_ids": [], "details": {"result": repr(value)}}
    except Exception as exc:  # a challenge result must remain auditable
        value = {"status": "ERROR", "content_ids": [], "details": {"error_type": type(exc).__name__, "error": str(exc)}}
    return value, round((time.perf_counter() - started) * 1000.0, 3)


def run() -> Path:
    manifest_path = ROOT / "data/round7-challenge-20261011/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = watermark_sync.SyncConfig(
        profile="decoder-v2-sync", max_candidates=128, max_decode_attempts=128,
        max_search_ms=1000, max_memory_mb=256, max_return_candidates=8,
        crop_area_ratios=(1.0, .98, .96, .95, .94, .92, .90),
        offset_fractions=(0.0, -.03, .03),
    )
    cfg_v3 = watermark_sync.SyncConfig(**{**cfg.__dict__, "profile": "decoder-v3-sync"})
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S-round7-external")
    out = ROOT / "results/round7-external" / run_id
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for item in manifest["items"]:
        if not item.get("challenge_included"):
            continue
        path = ROOT / item["acquisition"]["local_path"]
        data = path.read_bytes()
        acquisition = item.get("acquisition", {})
        if len(data) != int(acquisition.get("bytes", -1)) or sha256(data) != acquisition.get("sha256") or sm3(data) != acquisition.get("sm3"):
            raise ValueError(f"挑战文件摘要与清单不一致: {item['sample_id']}")
        d0, d0_ms = timed(media.detect, data)
        s0, s0_ms = timed(lambda b: watermark_sync.detect_image_sync(b, cfg), data)
        candidate, candidate_ms = timed(lambda b: watermark_sync.detect_image_sync(b, cfg_v3), data)
        rows.append({
            "sample_id": item["sample_id"], "source_family_id": item["source_family_id"],
            "category": item["category"], "source": item["source"],
            "input_sha256": sha256(data), "input_bytes": len(data),
            "detectors": {
                "d0": {"result": d0, "elapsed_ms": d0_ms},
                "s0": {"result": s0, "elapsed_ms": s0_ms},
                "candidate_v3": {"result": candidate, "elapsed_ms": candidate_ms},
            },
            "blind": {"expected_content_id_supplied": False, "geometry_supplied": False, "registry_supplied": False},
        })
    summary = {
        "schema": "round7-external-results/v1", "run_id": run_id, "created_at": utc_now(),
        "challenge_manifest": "data/round7-challenge-20261011/manifest.json",
        "challenge_rows": len(rows), "candidate_frozen_before_results": True,
        "candidate_source_snapshot": source_snapshot(),
        "budget": {"max_candidates": 128, "max_decode_attempts": 128, "max_search_ms": 1000, "max_memory_mb": 256, "max_return_candidates": 8},
        "detector_profiles": {"s0": "decoder-v2-sync", "candidate": "decoder-v3-sync"},
        "status_counts": {name: {status: sum(1 for r in rows if r["detectors"][name]["result"].get("status") == status) for status in sorted({r["detectors"][name]["result"].get("status") for r in rows})} for name in ("d0", "s0", "candidate_v3")},
        "false_positive_content_ids": {name: sorted({cid for r in rows for cid in r["detectors"][name]["result"].get("content_ids", [])}) for name in ("d0", "s0", "candidate_v3")},
        "environment": {"python": sys.version, "executable": sys.executable, "packages": versions()},
    }
    (out / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "manifest_sha256.txt").write_text(sha256(manifest_path.read_bytes()) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(run())
