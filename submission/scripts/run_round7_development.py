#!/usr/bin/env python3
"""Round 7 decoder-v3 development evaluator.

Generated from the durable Round 6 runner semantics; inputs remain immutable
Round 5 final bytes and the detector receives only bytes plus public config.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from round6_records import (AGGREGATOR_VERSION, METRIC_POLICY, judge, unique_rows,
                            checked_rows, process_identity, atomic_json, exclusive_run_lock)

DEFAULT_CONFIG = json.loads((ROOT / "configs/round7-development.json").read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise FileNotFoundError(p)
    value = json.loads(p.read_text(encoding="utf-8"))
    return merge(json.loads(json.dumps(DEFAULT_CONFIG)), value)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def git_snapshot() -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.splitlines()
        return {"head": head, "dirty": bool(status), "status_short": status}
    except Exception as exc:
        return {"head": None, "dirty": None, "error": f"{type(exc).__name__}: {exc}"}


def environment_snapshot() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in ("numpy", "scipy", "Pillow", "reedsolo"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "processor": platform.processor(), "packages": packages}


def execution_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Hash detector inputs and dependencies, excluding aggregation-only code."""
    paths = [
        ROOT / "configs/round7-development.json",
        ROOT / "configs/decoder-v2-sync.json",
        ROOT / "src/gm_provenance/watermark_sync.py",
        ROOT / "src/gm_provenance/media.py",
        ROOT / "src/gm_provenance/watermark.py",
        ROOT / "src/gm_provenance/development_eval.py",
        ROOT / str(config["dataset_manifest"]),
        ROOT / str(config["round5_run"]) / "run_manifest.json",
    ]
    files: dict[str, str | None] = {}
    for path in paths:
        try:
            name = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            name = str(path)
        files[name] = sha256_file(path) if path.exists() else None
    # This digest intentionally excludes aggregation and scheduler code. The
    # dataset and Round-5 run manifests bind the immutable input inventory;
    # detector/config/backend files and environment bind execution semantics.
    body = {"files": files, "environment": environment_snapshot(), "decoder": config.get("decoder", {})}
    return {"files": files, "environment": body["environment"], "execution_fingerprint": sha256_bytes(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))}


def task_id(execution_fingerprint: str, key: str) -> str:
    return f"r7:{execution_fingerprint[:16]}:{key}"


def _legacy_atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def acquire_run_lock(path: Path):
    """Keep an OS lock for the lifetime of one evaluator process."""
    handle = path.open("a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise RuntimeError("another Round7 evaluator holds this run lock") from exc
    else:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("another Round7 evaluator holds this run lock") from exc
    return handle


def read_manifest(path: str | Path, count: int | None = None) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = sorted((r for r in rows if str(r.get("media_type", "")).startswith("image/")), key=lambda r: str(r.get("sample_id", "")))
    if count is not None:
        rows = rows[:count]
    for row in rows:
        src = ROOT / str(row["local_path"])
        if not src.exists() or sha256_file(src) != row.get("sha256"):
            raise ValueError(f"dataset integrity failure: {row.get('sample_id')}")
    return rows


def load_round5_index(config: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    path = ROOT / str(config["round5_run"]) / "attempts.jsonl"
    index: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("transform") == "exact" and row.get("profile") in config["profiles"] and row.get("published"):
            index[(str(row["sample_id"]), str(row["profile"]))] = row
    return index


def crop_with_geometry(data: bytes, keep: float, offset: tuple[float, float] = (0.0, 0.0)) -> tuple[bytes, dict[str, Any]]:
    from PIL import Image
    if not 0.0 < float(keep) <= 1.0:
        raise ValueError("crop keep must be in (0,1]")
    im = Image.open(io.BytesIO(data)).convert("RGB")
    scale = float(keep) ** 0.5
    width, height = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    cx = (im.width - width) / 2.0 + float(offset[0]) * im.width
    cy = (im.height - height) / 2.0 + float(offset[1]) * im.height
    left = max(0, min(im.width - width, int(cx)))
    top = max(0, min(im.height - height, int(cy)))
    rect = (left, top, left + width, top + height)
    out = io.BytesIO(); im.crop(rect).save(out, "PNG")
    return out.getvalue(), {"keep_area": float(keep), "scale_each_dimension": scale, "source_size": [im.width, im.height], "output_size": [width, height], "rect_xyxy": list(rect), "offset_fraction": [float(offset[0]), float(offset[1])], "rounding": "round dimensions; int-truncated top-left"}


def transform(data: bytes, spec: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    from gm_provenance.development_eval import _image_transform
    op = str(spec["operation"])
    if op == "identity":
        return data, {"operation": op}
    if op in {"crop", "crop_resize", "crop_jpeg", "crop_resize_jpeg"}:
        cropped, geometry = crop_with_geometry(data, float(spec["keep"]), tuple(spec.get("offset", [0.0, 0.0])))
        if op == "crop":
            return cropped, geometry
        if op == "crop_resize":
            return _image_transform(cropped, "resize", float(spec["ratio"])), geometry
        if op == "crop_jpeg":
            return _image_transform(cropped, "jpeg", int(spec["quality"])), geometry
        return _image_transform(_image_transform(cropped, "resize", float(spec["ratio"])), "jpeg", int(spec["quality"])), geometry
    value = {"jpeg": spec.get("quality"), "resize": spec.get("ratio"), "resize_jpeg": [spec.get("ratio"), spec.get("quality")], "metadata_clear": None, "format_convert": spec.get("quality"), "screenshot_simulated": None}.get(op)
    return _image_transform(data, op, value), {"operation": op}


def strict_recovery(observed: dict[str, Any], expected: str | None = None) -> dict[str, Any]:
    result = judge(observed, expected)
    result.update({"expected": expected, "unique": len(result.get("observed_ids", [])) == 1,
                   "found_expected": result["correct_single_output"], "wrong_id": bool(result.get("wrong_ids"))})
    return result


def _detector(module: Any, data: bytes, config: dict[str, Any]) -> dict[str, Any]:
    fn = getattr(module, "detect_image_sync", None)
    if not callable(fn):
        return {"status": "UNAVAILABLE", "content_ids": [], "details": {"error": "detect_image_sync unavailable"}}
    try:
        sync_cls = getattr(module, "SyncConfig", None)
        decoder = dict(config.get("decoder", {}))
        decoder.pop("profile", None)
        cfg = sync_cls(**decoder) if sync_cls is not None else config.get("decoder", {})
        result = fn(data, cfg)
        return result if isinstance(result, dict) else {"status": "ERROR", "content_ids": [], "details": {"result": repr(result)}}
    except Exception as exc:
        return {"status": "ERROR", "content_ids": [], "details": {"error_type": type(exc).__name__, "error": str(exc)}}


def _d0(media: Any, data: bytes) -> dict[str, Any]:
    try:
        result = media.detect(data)
        return result if isinstance(result, dict) else {"status": "ERROR", "content_ids": [], "details": {"result": repr(result)}}
    except Exception as exc:
        return {"status": "ERROR", "content_ids": [], "details": {"error_type": type(exc).__name__, "error": str(exc)}}


def _oracle(media: Any, transformed: bytes, geometry: dict[str, Any]) -> dict[str, Any] | None:
    """Geometry-only diagnostic; no original pixels or expected ID are used."""
    if not geometry or "source_size" not in geometry or "rect_xyxy" not in geometry:
        return None
    from PIL import Image
    source = Image.open(io.BytesIO(transformed)).convert("RGB")
    canvas = Image.new("RGB", tuple(int(x) for x in geometry["source_size"]), (128, 128, 128))
    x0, y0, x1, y1 = [int(x) for x in geometry["rect_xyxy"]]
    canvas.paste(source.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), (x0, y0))
    out = io.BytesIO(); canvas.save(out, "PNG")
    result = _d0(media, out.getvalue())
    return {"observed": result, "checked_scope": "geometry-only", "fill_rule": "RGB(128,128,128), no original pixels"}


def _pair_cell(d0: dict[str, Any], sync: dict[str, Any], expected: str) -> str:
    d0_ok = judge(d0, expected)["correct_single_output"]
    sync_ok = judge(sync, expected)["correct_single_output"]
    return { (True, True): "both_success", (False, True): "d0_failed_sync_success", (True, False): "d0_success_sync_failed", (False, False): "both_failed" }[(d0_ok, sync_ok)]


def _summary(rows: list[dict[str, Any]], negative: list[dict[str, Any]]) -> dict[str, Any]:
    paired_source = [r for r in rows if r.get("role") == "paired"]
    duplicate_conflicts = {}
    try:
        source, duplicate_keys = unique_rows(paired_source)
    except ValueError:
        grouped = {}
        for row in paired_source:
            grouped.setdefault(str(row.get("key")), []).append(row)
        source, duplicate_keys = [], {}
        for key, grouped_rows in grouped.items():
            if all(row == grouped_rows[0] for row in grouped_rows[1:]):
                source.append(grouped_rows[0])
                if len(grouped_rows) > 1:
                    duplicate_keys[key] = len(grouped_rows)
            else:
                duplicate_conflicts[key] = len(grouped_rows)
    try:
        negative, negative_duplicates = unique_rows(negative)
    except ValueError:
        negative, negative_duplicates = [], {}
        for key, group in __import__("itertools").groupby(sorted(negative, key=lambda row: str(row.get("key"))), key=lambda row: str(row.get("key"))):
            group = list(group)
            if all(row == group[0] for row in group[1:]):
                negative.append(group[0])
                if len(group) > 1:
                    negative_duplicates[key] = len(group)
    primary, incomplete = [], []
    for original in source:
        if not original.get("expected_content_id") or not all(isinstance(original.get(k, {}).get("observed"), dict) for k in ("d0", "sync")):
            incomplete.append(original["key"])
            continue
        row = dict(original)
        expected = row["expected_content_id"]
        for name in ("d0", "sync"):
            row[name] = dict(row[name], recovery=strict_recovery(row[name]["observed"], expected))
        row["pair_cell"] = _pair_cell(row["d0"]["observed"], row["sync"]["observed"], expected)
        primary.append(row)
    cells = {k: 0 for k in ("both_success", "d0_failed_sync_success", "d0_success_sync_failed", "both_failed")}
    for row in primary:
        cells[row["pair_cell"]] = cells.get(row["pair_cell"], 0) + 1
    wrong = sum(1 for r in primary if r.get("sync", {}).get("recovery", {}).get("wrong_id") or r.get("d0", {}).get("recovery", {}).get("wrong_id"))
    def expected_hit(observed: Any, expected: str | None) -> bool:
        if not isinstance(observed, dict) or observed.get("status") != "FOUND" or not expected:
            return False
        return judge(observed, expected)["correct_single_output"]

    def sync_correct(row: dict[str, Any]) -> bool:
        return judge(row.get("sync", {}).get("observed"), row.get("expected_content_id"))["correct_single_output"]

    oracle_gap_rows = [r for r in primary if expected_hit(((r.get("oracle") or {}).get("observed")), r.get("expected_content_id")) and not sync_correct(r)]
    oracle_gap = len(oracle_gap_rows)
    statuses: dict[str, int] = {}
    ranges: dict[str, int] = {}
    for r in primary:
        st = str(r.get("sync", {}).get("observed", {}).get("status", "UNKNOWN")); statuses[st] = statuses.get(st, 0) + 1
        scope = str(r.get("transform_range", "unknown")); ranges[scope] = ranges.get(scope, 0) + 1
    neg_wrong = sum(1 for r in negative if r.get("observed", {}).get("content_ids"))
    neg_conflict = sum(1 for r in negative if r.get("observed", {}).get("status") == "CONFLICT")
    mixed = [r for r in negative if r.get("negative_kind") == "mixed_ids_concat"]

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
        return round(float(ordered[index]), 3)

    def group_breakdown(key: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for value in sorted({str(r.get(key, "unknown")) for r in primary}):
            group = [r for r in primary if str(r.get(key, "unknown")) == value]
            group_cells = {name: 0 for name in cells}
            for row in group:
                group_cells[row.get("pair_cell", "both_failed")] = group_cells.get(row.get("pair_cell", "both_failed"), 0) + 1
            times = [float(r["sync_ms"]) for r in group if isinstance(r.get("sync_ms"), (int, float))]
            result[value] = {
                "rows": len(group),
                "case_ids": [r["key"] for r in group],
                "valid_detector_rows": sum(not r.get("transform_error") for r in group),
                "pairing_four_cells": group_cells,
                "sync_recovered_expected": sum(bool(r.get("sync", {}).get("recovery", {}).get("found_expected")) for r in group),
                "sync_status_counts": dict(__import__("collections").Counter(str(r.get("sync", {}).get("observed", {}).get("status", "UNKNOWN")) for r in group)),
                "sync_ms_p50": percentile(times, 0.50),
                "sync_ms_p95": percentile(times, 0.95),
            }
        return result

    all_sync_times = [float(r["sync_ms"]) for r in primary if isinstance(r.get("sync_ms"), (int, float))]
    return {
        "aggregator_version": AGGREGATOR_VERSION,
        "metric_policy": METRIC_POLICY,
        "paired_rows": len(primary), "pairing_four_cells": cells,
        "oracle_success_sync_failed": oracle_gap,
        "oracle_success_sync_failed_case_ids": [str(r.get("key")) for r in oracle_gap_rows],
        "duplicate_task_keys": duplicate_keys,
        "conflicting_duplicate_task_keys": duplicate_conflicts,
        "negative_duplicate_task_keys": negative_duplicates,
        "incomplete_record_case_ids": incomplete,
        "case_sets": {**{k: [r["key"] for r in primary if r["pair_cell"] == k] for k in cells},
            "valid_detector": [r["key"] for r in primary if not r.get("transform_error")],
            "transform_error": [r["key"] for r in primary if r.get("transform_error")],
            "expected_id_hit": [r["key"] for r in primary if r["sync"]["recovery"]["expected_id_hit"]],
            "correct_single_output": [r["key"] for r in primary if sync_correct(r)],
            "partial_correct_single_output": [r["key"] for r in primary if sync_correct(r) and not r["sync"]["recovery"].get("search_complete")],
            "wrong_id": [r["key"] for r in primary if r["sync"]["recovery"]["wrong_id"]]},
        "wrong_id_returned": wrong,
        "sync_status_counts": statuses, "transform_range_counts": ranges,
        "transform_errors": sum(1 for r in primary if r.get("transform_error")),
        "profile_breakdown": group_breakdown("profile"),
        "transform_breakdown": group_breakdown("transform"),
        "range_breakdown": group_breakdown("transform_range"),
        "sync_cost_ms": {"p50": percentile(all_sync_times, 0.50), "p95": percentile(all_sync_times, 0.95), "max": max(all_sync_times) if all_sync_times else None},
        "negative_rows": len(negative), "negative_wrong_id_files": neg_wrong,
        "negative_conflicts": neg_conflict,
        "negative_status_counts": dict(__import__("collections").Counter(str(r.get("observed", {}).get("status", "UNKNOWN")) for r in negative)),
        "negative_mixed_id_rows": len(mixed),
        "negative_mixed_id_conflicts": sum(1 for r in mixed if r.get("observed", {}).get("status") == "CONFLICT"),
        "negative_groups": {kind: {"case_ids": [r["key"] for r in negative if r.get("negative_kind", "unwatermarked") == kind],
            "status_counts": dict(__import__("collections").Counter(r.get("observed", {}).get("status", "UNKNOWN") for r in negative if r.get("negative_kind", "unwatermarked") == kind)),
            "returned_id_count": dict(__import__("collections").Counter(str(len(set(r.get("observed", {}).get("content_ids", [])))) for r in negative if r.get("negative_kind", "unwatermarked") == kind))}
            for kind in sorted({r.get("negative_kind", "unwatermarked") for r in negative})},
        "crypto_acceptance": {"status": "NOT_CHECKED", "accepted": 0, "rejected": 0},
        "note": "ContentID recovery and cryptographic acceptance are intentionally separate metrics.",
    }


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    return checked_rows(path)


def negative_keys(config, manifest, profiles):
    neg = config.get("negative", {})
    if not neg.get("enabled", True):
        return set()
    keys = {f"negative|{sample['sample_id']}|{name}" for sample in manifest[:int(neg.get("max_base_samples", 60))] for name in neg.get("transforms", [])}
    keys.update(f"negative|mixed|{manifest[i]['sample_id']}|{manifest[i+1]['sample_id']}|{profiles[0] if profiles else 'v1'}" for i in range(min(int(neg.get("mixed_ids_pairs", 6)), max(0, len(manifest)-1))))
    for name in ("malformed", "overlimit"):
        if neg.get(f"include_{name}", True):
            keys.add(f"negative|{name}")
    return keys


def run(config: dict[str, Any], *, pilot: bool = False, limit: int | None = None, run_id: str | None = None, resume: bool = False) -> Path:
    rid = run_id or datetime.now().strftime("%Y%m%dT%H%M%S-round7-development")
    out = ROOT / "results" / "round7-development" / rid
    if out.exists() and not resume:
        raise FileExistsError(f"run directory exists: {out}")
    out.mkdir(parents=True, exist_ok=True)
    with exclusive_run_lock(out / "run.lock"):
        try:
            return _run_locked(config, pilot=pilot, limit=limit, run_id=rid, resume=resume)
        except BaseException as exc:
            manifest_path = out / "run_manifest.json"
            if manifest_path.exists():
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
                # A rejected resume must not rewrite a previous completed run.
                if current.get("status") == "running" and current.get("process_identity", {}).get("pid") == os.getpid():
                    current.update(status="interrupted", interrupted_observed_at=utc_now(),
                                   interruption={"type": type(exc).__name__, "reason": str(exc)})
                    atomic_json(manifest_path, current)
                    states_path = out / "task_states.json"
                    states = json.loads(states_path.read_text(encoding="utf-8")) if states_path.exists() else {}
                    for state in states.values():
                        if state.get("state") == "running":
                            state.update(state="interrupted", interrupted_observed_at=utc_now())
                            append_jsonl(out / "task_attempts.jsonl", dict(state, event="interrupted"))
                    atomic_json(states_path, states)
            raise


def _run_locked(config: dict[str, Any], *, pilot: bool = False, limit: int | None = None, run_id: str | None = None, resume: bool = False) -> Path:
    from gm_provenance import media
    try:
        from gm_provenance import watermark_sync
    except Exception as exc:
        watermark_sync = None
        sync_import_error = f"{type(exc).__name__}: {exc}"
    else:
        sync_import_error = None
    cfg_path = ROOT / "configs/round7-development.json"
    rid = run_id or datetime.now().strftime("%Y%m%dT%H%M%S-round7-development")
    out = ROOT / "results" / "round7-development" / rid
    previous_manifest_path = out / "run_manifest.json"
    previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8")) if resume and previous_manifest_path.exists() else None
    pair_path, neg_path = out / "paired.jsonl", out / "negative.jsonl"
    prior, done = _load_rows(pair_path); neg_prior, neg_done = _load_rows(neg_path)
    if (prior or neg_prior) and not previous_manifest:
        raise ValueError("Rows without an execution manifest require a separately audited recovery copy")
    n = int(config.get("pilot_count", 6)) if pilot else int(config.get("sample_count", 60))
    if limit is not None: n = min(n, int(limit))
    manifest = read_manifest(config["dataset_manifest"], n)
    r5 = load_round5_index(config)
    manifest_path = ROOT / str(config["dataset_manifest"])
    r5_manifest_path = ROOT / str(config["round5_run"]) / "run_manifest.json"
    execution = execution_snapshot(config)
    if previous_manifest is not None:
        if previous_manifest.get("execution_fingerprint") != execution["execution_fingerprint"]:
            raise ValueError("execution_fingerprint changed; refuse to reuse previous rows")
        if previous_manifest.get("config") and previous_manifest.get("config") != config:
            raise ValueError("frozen configuration changed; refuse to reuse previous rows")
    aggregation_fingerprint = sha256_file(Path(__file__))
    source_files = [Path(__file__), ROOT / "src/gm_provenance/media.py", ROOT / "src/gm_provenance/watermark.py", ROOT / "src/gm_provenance/development_eval.py", ROOT / "src/gm_provenance/watermark_sync.py"]
    run_manifest = {"schema_version": config["schema_version"], "run_id": rid, "started_at": utc_now(), "process_identity": {"pid": os.getpid(), "python": sys.executable}, "status": "running", "config": config, "config_sha256": sha256_file(cfg_path), "round5_run": config["round5_run"], "round5_run_manifest_sha256": sha256_file(r5_manifest_path) if r5_manifest_path.exists() else None, "execution_fingerprint": execution["execution_fingerprint"], "aggregation_fingerprint": aggregation_fingerprint, "source_snapshot": {"files": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256_file(p) if p.exists() else {"missing": True} for p in source_files}, "execution_files": execution["files"], "git": git_snapshot()}, "environment": execution["environment"], "sync_import_error": sync_import_error, "sample_ids": [str(x["sample_id"]) for x in manifest], "paired_rows": len(prior), "negative_rows": len(neg_prior), "checkpoint_at": utc_now(), "checkpoint_last_key": None}
    transform_specs = {str(x["name"]): x for x in config.get("transforms", [])}
    profiles = list(config.get("profiles", {}).keys())
    expected_keys = [f"paired|{str(sample['sample_id'])}|{profile}|{str(spec['name'])}" for sample in manifest for profile in profiles if (str(sample['sample_id']), profile) in r5 for spec in config.get("transforms", [])]
    expected_negative = negative_keys(config, manifest, profiles)
    required_keys = set(expected_keys) | expected_negative
    if (done | neg_done) - required_keys:
        raise ValueError("Recorded tasks do not belong to the frozen task inventory")
    old_inventory_path = out / "task_manifest.jsonl"
    if old_inventory_path.exists():
        _, old_keys = _load_rows(old_inventory_path)
        if not old_keys <= required_keys:
            raise ValueError("Frozen task inventory changed; use a new run_id")
    run_manifest["process_identity"] = dict(process_identity(), python=sys.executable, argv=sys.argv)
    if previous_manifest:
        append_jsonl(out / "run_attempts.jsonl", dict(previous_manifest, key=f"run:{utc_now()}", event="previous_attempt"))
    run_manifest["started_at"] = (previous_manifest or {}).get("started_at", run_manifest["started_at"])
    atomic_json(out / "run_manifest.json", run_manifest)
    state_path = out / "task_states.json"
    previous_states = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    task_states = {}
    for key in sorted(required_keys):
        state = previous_states.get(key, {"task_id": task_id(execution["execution_fingerprint"], key), "key": key, "state": "planned", "attempts": 0})
        if key in done | neg_done:
            state.update(state="completed", reused=True, recovery_basis="durable result exists")
        elif state.get("state") == "running":
            append_jsonl(out / "task_attempts.jsonl", dict(state, event="interrupted_observed_on_resume"))
            state.update(state="interrupted")
        task_states[key] = state
    atomic_json(out / "task_states.json", task_states)
    inventory_to_append = sorted(required_keys - (old_keys if old_inventory_path.exists() else set()))
    if not old_inventory_path.exists() or inventory_to_append:
        for key in (sorted(task_states) if not old_inventory_path.exists() else inventory_to_append):
            state = task_states[key]
            append_jsonl(old_inventory_path, {"task_id": state["task_id"], "key": key, "role": key.split("|")[0], "execution_fingerprint": execution["execution_fingerprint"]})
    checkpoint_counter = 0

    def start_task(key):
        state = task_states[key]
        state.update(state="running", attempts=state.get("attempts", 0) + 1, started_at=utc_now(), reused=False)
        append_jsonl(out / "task_attempts.jsonl", dict(state, event="started"))
        atomic_json(state_path, task_states)

    def save_result(path, row):
        key = row["key"]
        row.update(task_id=task_states[key]["task_id"], execution_fingerprint=execution["execution_fingerprint"],
                   attempt=task_states[key]["attempts"], cache_hit=False, recorded_at=utc_now())
        append_jsonl(path, row)  # fsync BEFORE the checkpoint; resume trusts results.
        state = task_states[key]
        state.update(state="completed", finished_at=utc_now())
        append_jsonl(out / "task_attempts.jsonl", dict(state, event="completed"))

    def checkpoint(last_key: str | None = None, *, force: bool = False) -> None:
        nonlocal checkpoint_counter
        checkpoint_counter += 1
        if not force and checkpoint_counter % 25:
            return
        current_rows, _ = _load_rows(pair_path)
        current_neg, _ = _load_rows(neg_path)
        run_manifest.update({"status": "running", "checkpoint_at": utc_now(), "checkpoint_last_key": last_key, "paired_rows": len(current_rows), "negative_rows": len(current_neg), "expected_paired_rows": len(expected_keys)})
        atomic_json(out / "run_manifest.json", run_manifest)
        atomic_json(out / "task_states.json", task_states)
        partial = _summary(current_rows, current_neg)
        partial.update({"partial": True, "status": "running", "run_id": rid, "checkpoint_at": run_manifest["checkpoint_at"], "expected_paired_rows": len(expected_keys), "observed_paired_rows": len(current_rows)})
        atomic_json(out / "checkpoint-summary.json", partial)
    for sample in manifest:
        sid, family = str(sample["sample_id"]), str(sample.get("source_family_id", "unknown"))
        for profile in profiles:
            key_art = r5.get((sid, profile))
            if key_art is None: continue
            final_path = ROOT / str(config["round5_run"]) / "samples" / sid / f"{profile}-final.bin"
            if not final_path.exists(): continue
            final = final_path.read_bytes()
            if key_art.get("final_sha256") and sha256_bytes(final) != key_art["final_sha256"]:
                raise ValueError(f"immutable Round 5 artifact hash mismatch: {final_path}")
            for spec in config.get("transforms", []):
                tname = str(spec["name"]); key = f"paired|{sid}|{profile}|{tname}"
                if key in done: continue
                task_states.setdefault(key, {"task_id": task_id(execution["execution_fingerprint"], key), "key": key, "state": "planned", "attempts": 0, "reused": False})
                start_task(key)
                row: dict[str, Any] = {"key": key, "role": "paired", "sample_id": sid, "source_family_id": family, "profile": profile, "transform": tname, "transform_range": spec.get("range"), "final_source": str(final_path.relative_to(ROOT)).replace("\\", "/"), "final_sha256": sha256_bytes(final), "expected_content_id": str(key_art.get("content_id")), "oracle": None}
                started = time.perf_counter()
                try:
                    transformed, geometry = transform(final, spec); row["evaluator_only_geometry_truth"] = geometry if geometry.get("source_size") else None
                except Exception as exc:
                    row.update({"status": "TRANSFORM_ERROR", "error": f"{type(exc).__name__}: {exc}", "search_complete": False, "pair_cell": "both_failed", "transform_error": True, "d0": {"observed": {"status": "ERROR", "content_ids": []}, "recovery": strict_recovery({"status": "ERROR", "content_ids": []}, row["expected_content_id"])}, "sync": {"observed": {"status": "ERROR", "content_ids": []}, "recovery": strict_recovery({"status": "ERROR", "content_ids": []}, row["expected_content_id"])}})
                    save_result(pair_path, row); done.add(key); checkpoint(key); continue
                row["transformed_sha256"] = sha256_bytes(transformed); row["transformed_bytes"] = len(transformed)
                t0 = time.perf_counter(); d0 = _d0(media, transformed); row["d0"] = {"observed": d0, "recovery": strict_recovery(d0, row["expected_content_id"])}; row["d0_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
                t1 = time.perf_counter(); sync = _detector(watermark_sync, transformed, config) if watermark_sync is not None else {"status": "UNAVAILABLE", "content_ids": [], "details": {"error": sync_import_error}}; row["sync"] = {"observed": sync, "recovery": strict_recovery(sync, row["expected_content_id"])}; row["sync_ms"] = round((time.perf_counter() - t1) * 1000.0, 3)
                row["sync_diagnostics"] = sync.get("details", {}) if isinstance(sync, dict) else {}
                row["candidate_count"] = row["sync_diagnostics"].get("candidate_count")
                row["decode_attempts"] = row["sync_diagnostics"].get("decode_attempts")
                row["first_candidate_ms"] = row["sync_diagnostics"].get("first_candidate_ms", "NOT_MEASURED")
                row["search_end_ms"] = row["sync_diagnostics"].get("elapsed_ms", "NOT_MEASURED")
                row["memory_peak_mb"] = row["sync_diagnostics"].get("memory_peak_mb", "NOT_MEASURED")
                try: row["oracle"] = _oracle(media, transformed, geometry)
                except Exception as exc: row["oracle"] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
                row["pair_cell"] = _pair_cell(d0, sync, row["expected_content_id"]); row["search_complete"] = sync.get("search_complete"); row["stop_reason"] = sync.get("stop_reason"); row["total_ms"] = round((time.perf_counter() - started) * 1000.0, 3); row["checked_scope"] = sync.get("details", {}).get("checked_scope", "watermark-only"); row["crypto_acceptance"] = "NOT_CHECKED"
                save_result(pair_path, row); done.add(key); checkpoint(key)
    # Negative corpus intentionally runs once per source family, never once per embedding profile.
    if config.get("negative", {}).get("enabled", True):
        for sample in manifest[: int(config.get("negative", {}).get("max_base_samples", 60))]:
            sid, family = str(sample["sample_id"]), str(sample.get("source_family_id", "unknown")); original = (ROOT / str(sample["local_path"])).read_bytes()
            for tname in config.get("negative", {}).get("transforms", []):
                key = f"negative|{sid}|{tname}"
                if key in neg_done or tname not in transform_specs: continue
                task_states.setdefault(key, {"task_id": task_id(execution["execution_fingerprint"], key), "key": key, "state": "planned", "attempts": 0, "reused": False})
                start_task(key)
                try: transformed, geometry = transform(original, transform_specs[tname]); observed = _detector(watermark_sync, transformed, config) if watermark_sync else {"status": "UNAVAILABLE", "content_ids": []}
                except Exception as exc: transformed, geometry, observed = b"", {}, {"status": "ERROR", "content_ids": [], "details": {"error_type": type(exc).__name__, "error": str(exc)}}
                save_result(neg_path, {"key": key, "role": "negative", "sample_id": sid, "source_family_id": family, "transform": tname, "input_sha256": sha256_bytes(original), "observed": observed, "content_ids": sorted(set(observed.get("content_ids", []))), "search_complete": observed.get("search_complete"), "stop_reason": observed.get("stop_reason")}); neg_done.add(key); checkpoint(key)
        if len(manifest) >= 2:
            from PIL import Image
            mixed_profile = profiles[0] if profiles else "v1"
            for i in range(min(int(config.get("negative", {}).get("mixed_ids_pairs", 6)), len(manifest) - 1)):
                a, b = manifest[i], manifest[i + 1]; key = f"negative|mixed|{a['sample_id']}|{b['sample_id']}|{mixed_profile}"
                if key in neg_done: continue
                ak = bk = None
                try:
                    # Compose two immutable Round 5 marked final artifacts from
                    # different ContentIDs.  The unwatermarked corpus above is
                    # kept separate and is never relabelled as a mixed-ID case.
                    ak, bk = r5.get((str(a["sample_id"]), mixed_profile)), r5.get((str(b["sample_id"]), mixed_profile))
                    if not ak or not bk: continue
                    ap = ROOT / str(config["round5_run"]) / "samples" / str(a["sample_id"]) / f"{mixed_profile}-final.bin"
                    bp = ROOT / str(config["round5_run"]) / "samples" / str(b["sample_id"]) / f"{mixed_profile}-final.bin"
                    aa = Image.open(ap).convert("RGB"); bb = Image.open(bp).convert("RGB"); h = max(aa.height, bb.height); canvas = Image.new("RGB", (aa.width + bb.width, h), (128, 128, 128)); canvas.paste(aa, (0, 0)); canvas.paste(bb, (aa.width, 0)); bio = io.BytesIO(); canvas.save(bio, "PNG"); mixed = bio.getvalue(); observed = _detector(watermark_sync, mixed, config) if watermark_sync else {"status": "UNAVAILABLE", "content_ids": []}
                except Exception as exc: mixed, observed = b"", {"status": "ERROR", "content_ids": [], "details": {"error_type": type(exc).__name__, "error": str(exc)}}
                task_states.setdefault(key, {"task_id": task_id(execution["execution_fingerprint"], key), "key": key, "state": "planned", "attempts": 0, "reused": False})
                start_task(key)
                # Persist the mixed-media result only after the task attempt is visible.
                save_result(neg_path, {"key": key, "role": "negative", "negative_kind": "mixed_ids_concat", "profile": mixed_profile, "source_sample_ids": [str(a["sample_id"]), str(b["sample_id"])], "source_family_ids": [str(a.get("source_family_id")), str(b.get("source_family_id"))], "source_content_ids": [str(ak.get("content_id")) if ak else None, str(bk.get("content_id")) if bk else None], "input_sha256": sha256_bytes(mixed), "observed": observed}); neg_done.add(key); checkpoint(key)
        if config.get("negative", {}).get("include_malformed", True):
            for name, blob in (("malformed", b"not-an-image"), ("overlimit", b"0" * int(config.get("negative", {}).get("max_bytes", 20971520) + 1))):
                key = f"negative|{name}"
                if key in neg_done: continue
                observed = _detector(watermark_sync, blob, config) if watermark_sync else {"status": "UNAVAILABLE", "content_ids": []}
                task_states.setdefault(key, {"task_id": task_id(execution["execution_fingerprint"], key), "key": key, "state": "planned", "attempts": 0, "reused": False})
                start_task(key)
                save_result(neg_path, {"key": key, "role": "negative", "negative_kind": name, "input_bytes": len(blob), "input_sha256": sha256_bytes(blob), "observed": observed}); neg_done.add(key); checkpoint(key)
    all_rows, _ = _load_rows(pair_path); all_neg, _ = _load_rows(neg_path)
    expected_negative = negative_keys(config, manifest, profiles)
    observed_keys = {str(x.get("key")) for x in all_rows + all_neg if x.get("key")}
    required_keys = set(expected_keys) | expected_negative
    complete = required_keys <= observed_keys
    summary = _summary(all_rows, all_neg); summary.update({"run_id": rid, "finished_at": utc_now(), "status": "completed" if complete else "interrupted", "partial": not complete, "sample_count": len(manifest), "profile_count": len(profiles), "transform_count": len(config.get("transforms", [])), "budgets": config.get("decoder", {}), "execution_fingerprint": execution["execution_fingerprint"], "aggregation_fingerprint": sha256_file(Path(__file__)), "expected_paired_rows": len(expected_keys), "observed_paired_rows": len(all_rows), "expected_negative_rows": len(expected_negative), "observed_negative_rows": len(all_neg), "missing_task_keys": sorted(required_keys - observed_keys)})
    run_manifest.update({"status": summary["status"], "finished_at": summary["finished_at"], "paired_rows": len(all_rows), "negative_rows": len(all_neg), "expected_paired_rows": len(expected_keys), "expected_negative_rows": len(expected_negative), "missing_task_count": len(required_keys - observed_keys)})
    atomic_json(out / "run_manifest.json", run_manifest)
    atomic_json(out / "task_states.json", task_states)
    atomic_json(out / "summary.json", summary)
    (out / "report.md").write_text("# Round 7 鐩插悓姝ラ厤瀵硅瘎娴媆n\n" + json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Round 7 bounded blind-sync paired evaluation")
    parser.add_argument("--config", default="configs/round7-development.json"); parser.add_argument("--pilot", action="store_true"); parser.add_argument("--limit", type=int); parser.add_argument("--run-id"); parser.add_argument("--resume", action="store_true"); parser.add_argument("--target-only", help="逗号分隔的 task key 前缀/完整键")
    args = parser.parse_args(); cfg = load_config(args.config)
    # Target-only narrows frozen profiles/transforms before task inventory creation.
    if args.target_only:
        wanted={x.strip() for x in args.target_only.split(',') if x.strip()}
        cfg["transforms"]=[t for t in cfg.get("transforms",[]) if any(x in str(t.get("name")) or str(t.get("name")) in x for x in wanted)] or cfg.get("transforms",[])
        cfg["profiles"]={k:v for k,v in cfg.get("profiles",{}).items() if any(k in x for x in wanted)} or cfg.get("profiles",{})
    out = run(cfg, pilot=args.pilot, limit=args.limit, run_id=args.run_id, resume=args.resume); print(out); return 0


if __name__ == "__main__":
    raise SystemExit(main())
