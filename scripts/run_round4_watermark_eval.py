#!/usr/bin/env python3
"""Round-4 paired watermark evaluation (A/B1/C1/B2/C2).

This runner is intentionally self contained and writes a fresh run directory.
It does not change the round-3 runner, source modules, or existing result
directories.  B1/C1 use watermark-v1 and B2/C2 use the exported v2 residual
helper through ``media.embed(..., watermark_profile=...)``.  If the v2 helper
or its no-watermark control is not available, the run is recorded as blocked
with a precise error instead of silently substituting another encoder.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import platform
import subprocess
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": "round4-watermark-evaluation-1",
    "allow_nan": False,
    "seed": 20261007,
    "sample_count": 60,
    "dataset_manifest": "data/round3-development-v3/dataset_manifest.jsonl",
    "profiles": {
        "v1": {"watermark_profile": "watermark-v1"},
        "v2": {"watermark_profile": "watermark-v2-residual"},
    },
    "transforms": [
        {"name": "exact", "operation": "identity"},
        {"name": "jpeg_q90", "operation": "jpeg", "quality": 90},
        {"name": "jpeg_q75", "operation": "jpeg", "quality": 75},
        {"name": "jpeg_q50", "operation": "jpeg", "quality": 50},
        {"name": "resize_0.75", "operation": "resize", "ratio": 0.75},
        {"name": "resize_jpeg_0.75_q75", "operation": "resize_jpeg", "ratio": 0.75, "quality": 75},
        {"name": "crop_0.95", "operation": "crop", "keep": 0.95},
        {"name": "metadata_clear", "operation": "metadata_clear"},
        {"name": "format_convert", "operation": "format_convert", "quality": 90},
        {"name": "screenshot_simulated", "operation": "screenshot_simulated", "simulated": True},
    ],
    "payload_sensitivity": {"sample_count": 6, "ids_per_sample": 3, "sample_ids": ["img-real-coco-139", "img-synth-001", "img-synth-002", "img-synth-004", "img-synth-005", "img-synth-006"], "transforms": ["exact", "jpeg_q75", "resize_0.75"]},
    "pdf_regression": {"enabled": True, "max_samples": 1, "dpi": 120},
    "retain_artifacts": {"exact": True, "payload_sensitivity": True},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    value = json.loads(path.read_text(encoding="utf-8"))
    return merge(DEFAULT_CONFIG, value if isinstance(value, dict) else {})


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_dataset(path: Path, count: int) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    images = [r for r in rows if str(r.get("media_type", "")).startswith("image/")]
    images.sort(key=lambda r: str(r.get("sample_id", "")))
    selected = images[:count]
    for row in selected:
        p = ROOT / str(row["local_path"])
        if not p.exists():
            raise FileNotFoundError(f"dataset file missing: {p}")
        actual = sha256_file(p)
        if actual != row.get("sha256"):
            raise ValueError(f"sha256 mismatch for {row.get('sample_id')}: {actual} != {row.get('sha256')}")
    return selected


def git_snapshot() -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.splitlines()
        return {"head": head, "dirty": bool(status), "status_short": status}
    except Exception as exc:
        return {"head": None, "dirty": None, "error": f"{type(exc).__name__}: {exc}"}


def dependency_snapshot() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in ("numpy", "scipy", "Pillow", "pypdf", "pypdfium2", "reedsolo", "reportlab"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "packages": packages}


def _helper(module: Any, names: tuple[str, ...]) -> tuple[Callable[..., Any] | None, str | None]:
    for name in names:
        value = getattr(module, name, None)
        if callable(value):
            return value, f"{module.__name__}.{name}"
    return None, None


def _call_encoder(fn: Callable[..., Any], data: bytes, cid: str, fmt: str) -> tuple[bytes, dict[str, Any]]:
    """Call an exported helper while tolerating v2's small API variants."""
    attempts = ((data, cid, fmt), (data, cid), (data, fmt))
    errors: list[str] = []
    for args in attempts:
        try:
            result = fn(*args)
            if isinstance(result, tuple) and len(result) == 2:
                raw, profile = result
                return bytes(raw), profile if isinstance(profile, dict) else {"profile": profile}
            return bytes(result), {}
        except TypeError as exc:
            errors.append(str(exc))
    raise TypeError(f"helper call failed ({getattr(fn, '__name__', fn)}): {' | '.join(errors)}")


def _call_control(fn: Callable[..., Any], data: bytes, fmt: str) -> tuple[bytes, dict[str, Any]]:
    errors: list[str] = []
    for args in ((data, fmt), (data,)):
        try:
            result = fn(*args)
            if isinstance(result, tuple) and len(result) == 2:
                raw, profile = result
                return bytes(raw), profile if isinstance(profile, dict) else {"profile": profile}
            return bytes(result), {}
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    raise TypeError(f"control helper call failed ({getattr(fn, '__name__', fn)}): {' | '.join(errors)}")


def _call_detector(fn: Callable[..., Any], data: bytes) -> dict[str, Any]:
    result = fn(data)
    return result if isinstance(result, dict) else {"status": "ERROR", "details": {"raw": str(result)}}


def resolve_profile(profile: str, watermark: Any, media: Any) -> dict[str, Any]:
    if profile == "v1":
        embed, embed_name = _helper(watermark, ("embed_image",))
        control, control_name = _helper(watermark, ("encode_without_watermark", "embed_image_without_watermark", "embed_image_no_watermark"))
        detect, detect_name = _helper(watermark, ("detect_image",))
    else:
        embed = None; embed_name = None
        # v2 is exported by media as a named profile; direct helper names are
        # accepted for diagnostics and older in-progress implementations.
        control, control_name = _helper(watermark, ("encode_without_watermark_residual", "encode_without_watermark_v2", "encode_without_watermark_v2_residual", "embed_image_residual_without_watermark"))
        # v2 deliberately shares the payload decoder with v1.  A dedicated
        # detector is accepted when exported, otherwise detect_image is the
        # documented blind decoder for both embedding profiles.
        detect, detect_name = _helper(watermark, ("detect_image_residual", "detect_image_v2", "detect_image_v2_residual", "detect_image"))
    diagnose, diagnose_name = _helper(watermark, ("diagnose_image_residual", "diagnose_image_v2", "diagnose_residual")) if profile == "v2" else (None, None)
    if profile == "v1" and (embed is None or control is None or detect is None):
        raise RuntimeError("watermark-v1 exports are incomplete")
    if profile == "v2" and (control is None or detect is None):
        missing = [label for value, label in ((control, "no-watermark control"), (detect, "detector")) if value is None]
        raise RuntimeError("watermark-v2 helper exports missing: " + ", ".join(missing))
    return {"profile": profile, "embed": embed, "embed_name": embed_name, "control": control, "control_name": control_name, "detect": detect, "detect_name": detect_name, "diagnose": diagnose, "diagnose_name": diagnose_name, "media": media}


def encode(profile: dict[str, Any], data: bytes, cid: str, media_type: str, fmt: str, watermark_module: Any) -> tuple[bytes, dict[str, Any]]:
    if profile["profile"] == "v1":
        return _call_encoder(profile["embed"], data, cid, fmt)
    # The v2 production path is intentionally reached through media.embed so
    # that the exact profile selected by the service is tested.
    result = profile["media"].embed(data, cid, media_type, "watermark-v2-residual")
    return bytes(result[0]), result[1] if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict) else {}


def control(profile: dict[str, Any], data: bytes, cid: str, fmt: str) -> tuple[bytes, dict[str, Any]]:
    return _call_control(profile["control"], data, fmt)


def detect(profile: dict[str, Any], data: bytes, media_module: Any) -> dict[str, Any]:
    if profile["profile"] == "v2":
        return _call_detector(profile["detect"], data)
    return _call_detector(profile["detect"], data)


def transform(data: bytes, spec: dict[str, Any]) -> bytes:
    """Use the frozen round3 transformation implementation without reimplementing it."""
    from gm_provenance.development_eval import _image_transform
    op = str(spec["operation"])
    value = {"jpeg": spec.get("quality"), "resize": spec.get("ratio"),
             "crop": spec.get("keep"), "resize_jpeg": [spec.get("ratio"), spec.get("quality")]}.get(op)
    return _image_transform(data, op, value)


def image_metrics(left: bytes, right: bytes) -> dict[str, Any]:
    from PIL import Image
    import io
    import numpy as np
    with Image.open(io.BytesIO(left)) as a_im, Image.open(io.BytesIO(right)) as b_im:
        a, b = np.asarray(a_im.convert("RGB"), dtype=np.float64), np.asarray(b_im.convert("RGB"), dtype=np.float64)
    if a.shape != b.shape:
        return {"status": "NOT_COMPARABLE", "shape_a": list(a.shape), "shape_b": list(b.shape)}
    from gm_provenance.quality import _pair
    return {"status": "PASS", **_pair(a, b)}


def deterministic_cid(seed: int, sample_id: str, slot: int) -> str:
    return hashlib.sha256(f"round4|{seed}|{sample_id}|{slot}".encode()).hexdigest()[:32]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1], 3)


def timing_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        if isinstance(row.get("elapsed_ms"), (int, float)):
            key = f"{row.get('profile')}/{row.get('role')}/{row.get('transform')}"
            groups.setdefault(key, []).append(float(row["elapsed_ms"]))
    return {key: {"n": len(v), "mean_ms": round(statistics.mean(v), 3), "p50_ms": percentile(v, 50), "p95_ms": percentile(v, 95)} for key, v in sorted(groups.items())}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 6)
    return round(statistics.quantiles(values, n=100, method="inclusive")[max(0, int(p) - 1)], 6)


def quality_summary(rows: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    meta = {str(r.get("sample_id")): r for r in selected}
    exact = [r for r in rows if r.get("transform") == "exact" and r.get("metrics")]
    out: dict[str, Any] = {"rows": len(exact), "profiles": {}, "grouped": {"category": {}, "resolution": {}, "source_family": {}}, "quality_deltas": {}}
    for profile, b, c in (("v1", "B1", "C1"), ("v2", "B2", "C2")):
        metrics: dict[str, dict[str, list[float]]] = {}
        for r in exact:
            if r.get("profile") != profile:
                continue
            for key, m in r.get("metrics", {}).items():
                if not isinstance(m, dict) or m.get("status") != "PASS":
                    continue
                for name in ("mse", "psnr_db", "ssim_local"):
                    value = m.get(name)
                    if isinstance(value, (int, float)) and math.isfinite(float(value)):
                        metrics.setdefault(key, {}).setdefault(name, []).append(float(value))
        prof: dict[str, Any] = {}
        for key, vals in metrics.items():
            mse = vals.get("mse", []); psnr = vals.get("psnr_db", []); ssim = vals.get("ssim_local", [])
            mean_mse = statistics.mean(mse) if mse else None
            prof[key] = {"n_mse": len(mse), "finite_denominator": bool(mse), "zero_mse_count": sum(v == 0 for v in mse), "meanMSE": round(mean_mse, 6) if mean_mse is not None else None, "mean_perPSNR": round(statistics.mean(psnr), 6) if psnr else None, "PSNRfrommeanMSE": (None if not mean_mse else round(10 * math.log10(255.0 ** 2 / mean_mse), 6)), "p10SSIM": _percentile(ssim, 10), "medianSSIM": _percentile(ssim, 50)}
        out["profiles"][profile] = prof
    # Category/resolution/family summaries use the exact B/C metrics only.
    for r in exact:
        mdata = meta.get(str(r.get("sample_id")), {}); cat = str(mdata.get("categories", "unknown")); dims = mdata.get("dimensions") or []
        resolution = "unknown"
        if len(dims) == 2 and all(isinstance(x, (int, float)) for x in dims):
            pixels = int(dims[0] * dims[1]); resolution = "low" if pixels < 800 * 600 else ("high" if pixels >= 1600 * 1200 else "mid")
        family = str(mdata.get("source_family_id", "unknown"))
        for group_name, group_key in (("category", cat), ("resolution", resolution), ("source_family", family)):
            bucket = out["grouped"][group_name].setdefault(group_key, {"rows": 0, "v1": [], "v2": []}); bucket["rows"] += 1
            for profile, pair in (("v1", "B1_C1"), ("v2", "B2_C2")):
                metric = r.get("metrics", {}).get(pair, {}); value = metric.get("psnr_db")
                if isinstance(value, (int, float)) and math.isfinite(float(value)): bucket[profile].append(float(value))
    for group_name, groups in out["grouped"].items():
        for bucket in groups.values():
            for profile in ("v1", "v2"):
                values = bucket[profile]; bucket[profile] = {"n": len(values), "mean_psnr_db": round(statistics.mean(values), 6) if values else None}
    for r in exact:
        sid = str(r.get("sample_id"));
        if r.get("profile") == "v1":
            other = next((x for x in exact if x.get("sample_id") == sid and x.get("profile") == "v2"), None)
            if other:
                signal_v1 = r.get("metrics", {}).get("B1_C1", {}).get("psnr_db"); signal_v2 = other.get("metrics", {}).get("B2_C2", {}).get("psnr_db")
                endpoint_v1 = r.get("metrics", {}).get("A_C1", {}).get("psnr_db"); endpoint_v2 = other.get("metrics", {}).get("A_C2", {}).get("psnr_db")
                signal_delta = (float(signal_v2) - float(signal_v1)) if isinstance(signal_v1, (int, float)) and isinstance(signal_v2, (int, float)) else None
                endpoint_delta = (float(endpoint_v2) - float(endpoint_v1)) if isinstance(endpoint_v1, (int, float)) and isinstance(endpoint_v2, (int, float)) else None
                out["quality_deltas"][sid] = {"signal_delta_BC_psnr_v2_minus_v1_db": round(signal_delta, 6) if signal_delta is not None else None, "endpoint_delta_AC_psnr_v2_minus_v1_db": round(endpoint_delta, 6) if endpoint_delta is not None else None, "improved": endpoint_delta > 0 if endpoint_delta is not None else False, "regressed": endpoint_delta < 0 if endpoint_delta is not None else False}
    deltas = list(out["quality_deltas"].values()); out["quality_deltas_summary"] = {"n": len(deltas), "improved_count": sum(d["improved"] for d in deltas), "regressed_count": sum(d["regressed"] for d in deltas), "equal_count": sum(not d["improved"] and not d["regressed"] for d in deltas)}
    return out


def audit_findings() -> dict[str, Any]:
    return {
        "quality_aggregation": {"finding": "round3 quality_summary reports descriptive aggregates over PASS rows", "round4_control": "pair metrics are keyed by sample_id, transform and profile; mean per-sample PSNR and PSNR derived from mean MSE are both retained, with finite denominators and zero-MSE flags"},
        "g2_g3_causal_pairing": {"finding": "round3 development_eval creates independent random G2 and G3 ContentIDs", "round4_control": "one deterministic ContentID is reused by C1/C2 for each sample and payload slot; transformations are byte-identical operations per cell"},
        "timing_group_composition": {"finding": "round3 timing_summary keys group/mode/transform while sidecar/full/recover rows have unequal denominators", "round4_control": "timing rows carry profile, role, transform, sample_id and stage; summary reports n per exact cell and never mixes controls with detectors"},
    }


def report_text(manifest: dict[str, Any], summary: dict[str, Any], blocked: list[dict[str, Any]]) -> str:
    status = manifest.get("status", "unknown")
    return f"""# Round 4 水印配对评测报告

状态：`{status}`；run_id：`{manifest.get('run_id')}`；样本：{manifest.get('sample_count')}（目标 60 张图片）。

## 评测设计

每个样本固定同一输入、同一 ContentID、同一输出格式/编码参数和同一变换，形成 A（原图）、B1/C1（v1 无水印/有水印）、B2/C2（v2 无水印/有水印）配对。C1/C2 复用每个样本的确定性 ID；payload sensitivity 子集固定 3 个样本、每样本 3 个 ID。

## 审计结论

- 质量聚合：round4 按样本、变换、profile 保存六对指标；`mean_perPSNR` 与 `PSNRfrommeanMSE` 都是有效且并列报告的聚合视角，并记录有限分母与零 MSE。质量差异同时报告 signal delta（B/C）与 endpoint delta（A/C），改善/退步计数按 endpoint delta。
- G2/G3 因果性：round3 的 G2/G3 ID 独立随机生成；本轮固定 ID 并记录每条变换的字节摘要。
- 计时组成：本轮按 `profile/role/transform` 严格分组，保留 n、均值、P50、P95，避免 sidecar/mode 混合。

## 结果

- 配对质量：`paired_quality.jsonl`；检测记录：`detections.jsonl`；配对恢复：`paired_recovery.jsonl`；计时：`timings.jsonl`；payload：`payload_sensitivity.jsonl`；失败分类：`new_failures.jsonl`。
- 汇总：`summary.json`。每个 profile/变换的 robust 状态同时要求检测到预期 ContentID；NOT_FOUND、CONFLICT、错误和缺少 helper 都保留在分母或 blocked 记录中。
- PDF 回归（若依赖可用）：`pdf_regression.json`；PDF v2 若接口明确不支持则记为 N/A，不冒充通过。

## 阻塞与限制

{json.dumps(blocked, ensure_ascii=False, indent=2) if blocked else '无阻塞记录。'}

复现命令：

```text
.\\.venv\\Scripts\\python.exe scripts/run_round4_watermark_eval.py --config configs/round4-watermark-evaluation.json
```
"""


def classify_error(stage: str, error: str) -> str:
    text = f"{stage} {error}".lower()
    if "missing" in text or "modulenotfound" in text or "dependency" in text: return "dependency"
    if "dataset" in text or "sha256" in text: return "dataset_integrity"
    if "helper" in text or "watermark" in text: return "watermark_api"
    if "transform" in text: return "transform"
    if "pdf" in text: return "pdf_regression"
    if "quality" in text or "nan" in text: return "quality_metric"
    return "runner"


def run(args: argparse.Namespace) -> int:
    config = load_config(ROOT / args.config)
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    out = ROOT / "results" / "round4-watermark" / run_id
    out.mkdir(parents=True, exist_ok=False)
    for name in ("paired_quality.jsonl", "detections.jsonl", "paired_recovery.jsonl", "timings.jsonl", "payload_sensitivity.jsonl", "failures.jsonl", "new_failures.jsonl"):
        (out / name).touch()
    manifest: dict[str, Any] = {"schema_version": config["schema_version"], "run_id": run_id, "started_at": utc_now(), "status": "initializing", "config": config, "config_path": str(args.config).replace("\\", "/"), "config_sha256": sha256_bytes(json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")), "output_root": str(out.relative_to(ROOT)).replace("\\", "/"), "git": git_snapshot(), "dependency_snapshot": dependency_snapshot()}
    write_json(out / "run_manifest.json", manifest)
    blocked: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []; timings: list[dict[str, Any]] = []; quality_rows: list[dict[str, Any]] = []; detections: list[dict[str, Any]] = []; recovery_rows: list[dict[str, Any]] = []; payload_rows: list[dict[str, Any]] = []
    pdf_result: dict[str, Any] = {"status": "NOT_RUN"}
    try:
        from gm_provenance import media, watermark
        if str(getattr(watermark, "_ECC_BACKEND", "")) != "reedsolo":
            raise RuntimeError(f"strict round4 evaluation requires reedsolo, detected {getattr(watermark, '_ECC_BACKEND', 'unknown')}")
        dataset_path = ROOT / str(config["dataset_manifest"])
        selected = read_dataset(dataset_path, int(config.get("sample_count", 60)))
        manifest.update({"dataset_manifest": str(dataset_path.relative_to(ROOT)).replace("\\", "/"), "dataset_manifest_sha256": sha256_file(dataset_path), "sample_count": len(selected), "sample_ids": [r.get("sample_id") for r in selected], "source_hashes": {str(r.get("sample_id")): str(r.get("sha256")) for r in selected}, "audit": audit_findings()})
        write_json(out / "run_manifest.json", manifest)
        if len(selected) != int(config.get("sample_count", 60)):
            blocked.append({"stage": "dataset", "error": f"requested {config.get('sample_count')} images, selected {len(selected)}"})
        profiles = {name: resolve_profile(name, watermark, media) for name in ("v1", "v2")}
        manifest["helpers"] = {name: {k: value for k, value in prof.items() if k.endswith("_name")} for name, prof in profiles.items()}
        manifest["watermark_profiles"] = {name: config["profiles"][name].get("watermark_profile") for name in profiles}
        payload_cfg = config.get("payload_sensitivity", {}); payload_ids = int(payload_cfg.get("ids_per_sample", 3)); payload_samples = int(payload_cfg.get("sample_count", 6))
        configured_payload_ids = [str(x) for x in payload_cfg.get("sample_ids", [])]
        payload_sample_ids = configured_payload_ids or [str(r["sample_id"]) for r in selected[:payload_samples]]
        missing_payload = [sid for sid in payload_sample_ids if sid not in {str(r["sample_id"]) for r in selected}]
        if missing_payload:
            raise ValueError(f"payload sensitivity sample_ids not selected: {missing_payload}")
        manifest["payload_sensitivity"] = {"sample_ids": payload_sample_ids, "ids_per_sample": payload_ids, "ids": {sid: [deterministic_cid(int(config["seed"]), sid, slot) for slot in range(payload_ids)] for sid in payload_sample_ids}}
        transform_specs = {str(s["name"]): s for s in config.get("transforms", [])}
        for index, sample in enumerate(selected, 1):
            sid = str(sample["sample_id"]); source_path = ROOT / str(sample["local_path"]); original = source_path.read_bytes(); media_type = media.sniff(original); fmt = "JPEG" if media_type == "image/jpeg" else "PNG"; cid = deterministic_cid(int(config["seed"]), sid, 0); sample_dir = out / "samples" / sid
            sample_dir.mkdir(parents=True, exist_ok=True)
            bases: dict[str, bytes] = {"A": original}; profiles_out: dict[str, tuple[bytes, bytes]] = {}
            for name, profile in profiles.items():
                t0 = time.perf_counter(); b, bprof = control(profile, original, cid, fmt); t1 = time.perf_counter(); c, cprof = encode(profile, original, cid, media_type, fmt, watermark); t2 = time.perf_counter(); profiles_out[name] = (b, c); bases[f"B{name[-1]}"] = b; bases[f"C{name[-1]}"] = c
                timings.extend([{"sample_id": sid, "profile": name, "role": "control_encode", "transform": "exact", "elapsed_ms": round((t1 - t0) * 1000, 3)}, {"sample_id": sid, "profile": name, "role": "watermark_encode", "transform": "exact", "elapsed_ms": round((t2 - t1) * 1000, 3)}])
                write_json(sample_dir / f"profile-{name}.json", {"control_profile": bprof, "watermark_profile": cprof, "content_id": cid})
                if profile.get("diagnose") is not None:
                    try:
                        write_json(sample_dir / f"diagnostics-{name}.json", profile["diagnose"](original, cid, fmt))
                    except Exception as exc:
                        blocked.append({"sample_id": sid, "profile": name, "stage": "diagnostics", "error": f"{type(exc).__name__}: {exc}"})
            # Exact outputs are retained as the audit anchor.
            for label, data in bases.items(): (sample_dir / f"{label}.bin").write_bytes(data)
            append_jsonl(out / "timings.jsonl", [r for r in timings if r.get("sample_id") == sid and r.get("transform") == "exact" and str(r.get("role", "")).endswith("encode")])
            for spec in config.get("transforms", []):
                name = str(spec["name"]); transformed: dict[str, bytes] = {}; transform_ms = 0.0
                timing_batch: list[dict[str, Any]] = []
                for label, data in bases.items():
                    t0 = time.perf_counter(); transformed[label] = transform(data, spec); transform_ms += (time.perf_counter() - t0) * 1000
                for label, data in transformed.items():
                    retain = bool(config.get("retain_artifacts", {}).get("exact")) and name == "exact"
                    if retain or (sid in payload_sample_ids and bool(config.get("retain_artifacts", {}).get("payload_sensitivity"))):
                        p = sample_dir / name; p.mkdir(parents=True, exist_ok=True); (p / f"{label}.bin").write_bytes(data)
                if name == "exact" and sid in payload_sample_ids:
                    try:
                        from PIL import Image
                        import io
                        crop_root = out / "representative_crops" / sid; crop_root.mkdir(parents=True, exist_ok=True)
                        crop_meta = {}
                        for label, data in transformed.items():
                            with Image.open(io.BytesIO(data)) as im:
                                rgb = im.convert("RGB"); cw, ch = min(256, rgb.width), min(256, rgb.height); left, top = max(0, (rgb.width - cw) // 2), max(0, (rgb.height - ch) // 2); rgb.crop((left, top, left + cw, top + ch)).save(crop_root / f"{label}_center.png", "PNG"); crop_meta[label] = {"box_xyxy": [left, top, left + cw, top + ch], "source_dimensions": [rgb.width, rgb.height]}
                        write_json(crop_root / "metadata.json", {"sample_id": sid, "category": next((str(x.get("categories")) for x in selected if str(x.get("sample_id")) == sid), "unknown"), "crops": crop_meta})
                    except Exception as exc:
                        blocked.append({"sample_id": sid, "stage": "representative_crops", "category": classify_error("representative_crops", str(exc)), "error": f"{type(exc).__name__}: {exc}"})
                for profile_name, b_label, c_label in (("v1", "B1", "C1"), ("v2", "B2", "C2")):
                    metrics = {}
                    if name == "exact":
                        metrics = {f"A_{b_label}": image_metrics(transformed["A"], transformed[b_label]), f"A_{c_label}": image_metrics(transformed["A"], transformed[c_label]), f"{b_label}_{c_label}": image_metrics(transformed[b_label], transformed[c_label])}
                    pair = {"sample_id": sid, "transform": name, "profile": profile_name, "content_id": cid, "bytes": {k: len(transformed[k]) for k in ("A", b_label, c_label)}, "sha256": {k: sha256_bytes(transformed[k]) for k in ("A", b_label, c_label)}, "metrics": metrics, "simulated": bool(spec.get("simulated", False))}
                    quality_rows.append(pair); append_jsonl(out / "paired_quality.jsonl", [pair])
                    for role, label in (("control", b_label), ("watermark", c_label)):
                        t0 = time.perf_counter(); observed = detect(profiles[profile_name], transformed[label], media); elapsed = round((time.perf_counter() - t0) * 1000, 3); ids = observed.get("content_ids") or observed.get("recovered_content_ids") or []; robust = bool(cid in ids and str(observed.get("status", "")).upper() == "FOUND") if role == "watermark" else bool(str(observed.get("status", "")).upper() == "NOT_FOUND" and cid not in ids)
                        row = {"sample_id": sid, "profile": profile_name, "role": role, "transform": name, "content_id": cid, "observed": observed, "robust": robust, "simulated": bool(spec.get("simulated", False))}; detections.append(row); timing_row = {"sample_id": sid, "profile": profile_name, "role": f"{role}_detect", "transform": name, "elapsed_ms": elapsed}; timings.append(timing_row); timing_batch.append(timing_row); append_jsonl(out / "detections.jsonl", [row])
                append_jsonl(out / "timings.jsonl", timing_batch)
                for profile_name in ("v1", "v2"):
                    cells = [r for r in detections if r.get("sample_id") == sid and r.get("profile") == profile_name and r.get("transform") == name]
                    control_cell = next((r for r in cells if r.get("role") == "control"), None)
                    watermark_cell = next((r for r in cells if r.get("role") == "watermark"), None)
                    recovery = {"sample_id": sid, "profile": profile_name, "transform": name, "content_id": cid, "control_status": (control_cell or {}).get("observed", {}).get("status"), "watermark_status": (watermark_cell or {}).get("observed", {}).get("status"), "control_false_positive": not bool((control_cell or {}).get("robust")), "watermark_recovered_expected": bool((watermark_cell or {}).get("robust")), "status": "PASS" if (control_cell and watermark_cell and control_cell.get("robust") and watermark_cell.get("robust")) else "FAIL"}
                    recovery_rows.append(recovery); append_jsonl(out / "paired_recovery.jsonl", [recovery])
            sample_rows.append({"sample_id": sid, "media_type": media_type, "content_id": cid, "transforms": len(config.get("transforms", []))})
            if index % 10 == 0: print(f"[{index}/{len(selected)}] {sid}", flush=True)
        if bool(config.get("pdf_regression", {}).get("enabled", True)):
            try:
                all_rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                pdf_sample = next((r for r in all_rows if r.get("media_type") == "application/pdf"), None)
                if pdf_sample is None:
                    pdf_result = {"status": "NOT_AVAILABLE", "reason": "dataset has no PDF row"}
                else:
                    pdf_path = ROOT / str(pdf_sample["local_path"]); raw_pdf = pdf_path.read_bytes(); pdf_cid = deterministic_cid(int(config["seed"]), str(pdf_sample["sample_id"]), 0); pdf_dir = out / "pdf-regression"; pdf_dir.mkdir(parents=True, exist_ok=True)
                    marked, profile = media.embed(raw_pdf, pdf_cid, "application/pdf", "watermark-v1"); marked_path = pdf_dir / "v1-marked.pdf"; marked_path.write_bytes(marked); observed = media.detect(marked, "application/pdf")
                    observed_ids = observed.get("content_ids") or observed.get("recovered_content_ids") or []
                    pdf_ok = str(observed.get("status", "")).upper() == "FOUND" and pdf_cid in observed_ids
                    pdf_result = {"status": "PASS" if pdf_ok else "FAIL", "sample_id": pdf_sample["sample_id"], "content_id": pdf_cid, "v1_profile": profile, "v1_detect": observed, "correct_found_and_content_id": pdf_ok, "v2": "N/A_PDF_PROFILE_UNSUPPORTED"}
                    try:
                        from gm_provenance import quality
                        pdf_result["quality"] = quality.analyze(pdf_path, marked_path, pdf_dir / "quality", str(pdf_sample["sample_id"]))
                    except Exception as exc:
                        pdf_result["quality_error"] = f"{type(exc).__name__}: {exc}"
                    try:
                        media.embed(raw_pdf, pdf_cid, "application/pdf", "watermark-v2-residual")
                        pdf_result["v2"] = "UNEXPECTEDLY_SUPPORTED"
                    except Exception as exc:
                        pdf_result["v2_error"] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                pdf_result = {"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"}
        write_json(out / "pdf_regression.json", pdf_result)
        for sid in payload_sample_ids:
            sample = next(r for r in selected if str(r["sample_id"]) == sid); original = (ROOT / str(sample["local_path"])).read_bytes(); mt = media.sniff(original); fmt = "JPEG" if mt == "image/jpeg" else "PNG"
            for slot in range(payload_ids):
                pid = deterministic_cid(int(config["seed"]), sid, slot); for_spec = [transform_specs[n] for n in payload_cfg.get("transforms", []) if n in transform_specs]
                for name in ("v1", "v2"):
                    for spec in for_spec:
                        b, _ = control(profiles[name], original, pid, fmt); c, _ = encode(profiles[name], original, pid, mt, fmt, watermark); transformed = transform(c, spec); observed = detect(profiles[name], transformed, media); ids = observed.get("content_ids") or observed.get("recovered_content_ids") or []; row = {"sample_id": sid, "payload_slot": slot, "profile": name, "transform": spec["name"], "content_id": pid, "observed": observed, "recovered_expected": pid in ids, "simulated": bool(spec.get("simulated", False))}; payload_rows.append(row); append_jsonl(out / "payload_sensitivity.jsonl", [row])
        manifest["status"] = "completed" if not blocked else "completed_with_blockers"
    except Exception as exc:
        failure = {"category": classify_error("initialization_or_evaluation", str(exc)), "stage": "initialization_or_evaluation", "error": f"{type(exc).__name__}: {exc}"}; blocked.append(failure); manifest["status"] = "blocked"; append_jsonl(out / "failures.jsonl", [failure]); append_jsonl(out / "new_failures.jsonl", [failure])
    summary = {"status": manifest.get("status"), "samples": {"n": len(sample_rows), "target": config.get("sample_count"), "by_media_type": {"image": len(sample_rows)}}, "quality": {"rows": len(quality_rows), "paired_b_c_pass": sum(1 for r in quality_rows if r["metrics"].get(f"B{r['profile'][-1]}_C{r['profile'][-1]}", {}).get("status") == "PASS"), "by_profile": {p: {"rows": sum(r["profile"] == p for r in quality_rows), "paired_b_c_pass": sum(1 for r in quality_rows if r["profile"] == p and r["metrics"].get(f"B{p[-1]}_C{p[-1]}", {}).get("status") == "PASS")} for p in ("v1", "v2")}}, "detection": {"rows": len(detections), "robust_rows": sum(bool(r.get("robust")) for r in detections), "by_profile_transform": {}}, "paired_recovery": {"rows": len(recovery_rows), "pass": sum(r.get("status") == "PASS" for r in recovery_rows)}, "timings": timing_summary(timings), "payload_sensitivity": {"rows": len(payload_rows), "recovered_expected": sum(bool(r.get("recovered_expected")) for r in payload_rows)}, "pdf_regression": pdf_result, "quality_summary": quality_summary(quality_rows, selected if "selected" in locals() else []), "blocked": blocked, "audit": audit_findings()}
    for p in ("v1", "v2"):
        for role in ("control", "watermark"):
            for t in sorted({str(r.get("transform")) for r in detections if r.get("profile") == p}):
                subset = [r for r in detections if r.get("profile") == p and r.get("role") == role and r.get("transform") == t]; key = f"{p}/{role}/{t}"; summary["detection"]["by_profile_transform"][key] = {"n": len(subset), "robust": sum(bool(r.get("robust")) for r in subset), "rate": (sum(bool(r.get("robust")) for r in subset) / len(subset) if subset else None)}
    delta_items = list(summary.get("quality_summary", {}).get("quality_deltas", {}).items())
    crop_candidates = {"worst_improvement": max(delta_items, key=lambda item: item[1].get("endpoint_delta_AC_psnr_v2_minus_v1_db") if item[1].get("endpoint_delta_AC_psnr_v2_minus_v1_db") is not None else float("-inf"))[0] if delta_items else None, "worst_regression": min(delta_items, key=lambda item: item[1].get("endpoint_delta_AC_psnr_v2_minus_v1_db") if item[1].get("endpoint_delta_AC_psnr_v2_minus_v1_db") is not None else float("inf"))[0] if delta_items else None}
    summary["quality_summary"]["representative_crop_candidates"] = crop_candidates
    try:
        from PIL import Image
        for role, sid in crop_candidates.items():
            if not sid: continue
            root = out / "samples" / sid / "exact"; dest = out / "representative_crops" / role / sid; dest.mkdir(parents=True, exist_ok=True)
            for label in ("A", "B1", "C1", "B2", "C2"):
                src = root / f"{label}.bin"
                if not src.exists(): continue
                with Image.open(src) as im:
                    rgb = im.convert("RGB"); cw, ch = min(256, rgb.width), min(256, rgb.height); left, top = max(0, (rgb.width-cw)//2), max(0, (rgb.height-ch)//2); rgb.crop((left, top, left+cw, top+ch)).save(dest / f"{label}_center.png", "PNG")
    except Exception as exc:
        blocked.append({"stage": "worst_quality_crops", "category": classify_error("worst_quality_crops", str(exc)), "error": f"{type(exc).__name__}: {exc}"})
    manifest["finished_at"] = utc_now(); write_json(out / "run_manifest.json", manifest); write_json(out / "summary.json", summary); (out / "report.md").write_text(report_text(manifest, summary, blocked), encoding="utf-8")
    latest = ROOT / "docs" / "ROUND4_WATERMARK_REPORT.md"; latest.write_text(report_text(manifest, summary, blocked) + f"\n\n本报告对应结果目录：`{out.relative_to(ROOT).as_posix()}`。\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "run": str(out.relative_to(ROOT)), "summary": str((out / "summary.json").relative_to(ROOT))}, ensure_ascii=False))
    return 0 if manifest["status"] in ("completed", "completed_with_blockers") else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/round4-watermark-evaluation.json")
    parser.add_argument("--run-id", default=None)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
