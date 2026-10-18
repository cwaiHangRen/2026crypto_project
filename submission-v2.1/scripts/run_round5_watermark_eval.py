#!/usr/bin/env python3
"""Round 5 strict paired watermark evaluation.

This runner is deliberately a new entry point.  It reuses the Round 4
deterministic ContentID rule, the development transformation implementation,
and the quality metric implementation; it does not alter the production
watermark modules or prior result directories.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import statistics
import subprocess
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
    "schema_version": "round5-watermark-evaluation-1",
    "seed": 20261007,
    "sample_count": 60,
    "pilot_count": 6,
    "dataset_manifest": "data/round3-development-v3/dataset_manifest.jsonl",
    "profiles": {"v1": "watermark-v1", "v2": "watermark-v2-residual", "feedback": "watermark-v2-feedback"},
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
    "pdf_regression": {"enabled": True, "max_samples": 40},
    "quality_budget": {"max_feedback_iterations": 3, "max_feedback_clipped_fraction": 0.25},
    "crop_diagnostic": {"enabled": True, "subset_count": 6, "keep": [0.95, 0.90], "offsets": [[0.0, 0.0], [0.01, 0.0]]},
    "retain_artifacts": {"exact": True, "representative": True, "diagnostics": True},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    p = ROOT / path if not Path(path).is_absolute() else Path(path)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    value = json.loads(p.read_text(encoding="utf-8"))
    return _merge(DEFAULT_CONFIG, value if isinstance(value, dict) else {})


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def deterministic_cid(seed: int, sample_id: str, slot: int = 0) -> str:
    """The exact Round 4 ContentID derivation, retained for causal pairing."""
    return hashlib.sha256(f"round4|{seed}|{sample_id}|{slot}".encode()).hexdigest()[:32]


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
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "processor": platform.processor(), "packages": packages}


def source_snapshot(config_path: Path) -> dict[str, Any]:
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    names = ["scripts/run_round5_watermark_eval.py", str(config_path.relative_to(ROOT)).replace("\\", "/"),
             "src/gm_provenance/media.py", "src/gm_provenance/watermark.py", "src/gm_provenance/development_eval.py", "src/gm_provenance/quality.py"]
    for feedback_name in ("src/gm_provenance/r5_feedback.py", "src/gm_provenance/watermark_feedback.py"):
        if (ROOT / feedback_name).exists():
            names.append(feedback_name)
    files: dict[str, Any] = {}
    for name in names:
        p = ROOT / name
        if p.exists():
            files[name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
        else:
            files[name] = {"missing": True}
    return {"files": files, "git": git_snapshot()}


def read_dataset(path: Path, count: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    images = sorted([r for r in rows if str(r.get("media_type", "")).startswith("image/")], key=lambda r: str(r.get("sample_id", "")))[:count]
    pdfs = sorted([r for r in rows if r.get("media_type") == "application/pdf"], key=lambda r: str(r.get("sample_id", "")))
    for row in images + pdfs:
        p = ROOT / str(row["local_path"])
        if not p.exists() or sha256_file(p) != row.get("sha256"):
            raise ValueError(f"dataset integrity failure: {row.get('sample_id')}")
    return images, pdfs


def _call_encoder(fn: Callable[..., Any], data: bytes, cid: str, fmt: str) -> tuple[bytes, dict[str, Any]]:
    errors: list[str] = []
    for args in ((data, cid, fmt), (data, cid), (data, fmt)):
        try:
            result = fn(*args)
            if isinstance(result, tuple) and len(result) == 2:
                raw, profile = result
                return bytes(raw), profile if isinstance(profile, dict) else {"profile": profile}
            return bytes(result), {}
        except TypeError as exc:
            errors.append(str(exc))
    raise TypeError("encoder call failed: " + " | ".join(errors))


def _call_control(fn: Callable[..., Any], data: bytes, fmt: str) -> tuple[bytes, dict[str, Any]]:
    for args in ((data, fmt), (data,)):
        try:
            result = fn(*args)
            if isinstance(result, tuple) and len(result) == 2:
                return bytes(result[0]), result[1] if isinstance(result[1], dict) else {"profile": result[1]}
            return bytes(result), {}
        except TypeError:
            continue
    raise TypeError("control encoder call failed")


def transform(data: bytes, spec: dict[str, Any]) -> bytes:
    """Use the frozen Round 4/development transform implementation."""
    from gm_provenance.development_eval import _image_transform
    op = str(spec["operation"])
    value = {"jpeg": spec.get("quality"), "resize": spec.get("ratio"), "crop": spec.get("keep"), "resize_jpeg": [spec.get("ratio"), spec.get("quality")]}.get(op)
    return _image_transform(data, op, value)


def crop_with_geometry(data: bytes, keep: float, offset: tuple[float, float] = (0.0, 0.0)) -> tuple[bytes, dict[str, Any]]:
    """Round 4 crop semantics plus an optional bounded fractional offset.

    ``keep`` is retained *area*; width and height are each multiplied by
    sqrt(keep), rounded, then center coordinates are integer-truncated.  The
    geometry is returned as diagnostic truth and is never passed to D0.
    """
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    scale = max(0.01, min(1.0, float(keep))) ** 0.5
    width, height = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    cx = (im.width - width) / 2.0 + float(offset[0]) * im.width
    cy = (im.height - height) / 2.0 + float(offset[1]) * im.height
    left, top = max(0, min(im.width - width, int(cx))), max(0, min(im.height - height, int(cy)))
    rect = (left, top, left + width, top + height)
    out = io.BytesIO(); im.crop(rect).save(out, "PNG")
    return out.getvalue(), {"keep_area": float(keep), "scale_each_dimension": scale, "source_size": [im.width, im.height], "output_size": [width, height], "rect_xyxy": list(rect), "offset_fraction": [float(offset[0]), float(offset[1])]}


def oracle_geometry_fill(cropped: bytes, geometry: dict[str, Any]) -> bytes:
    """Offline geometry oracle: paste only retained pixels into a fixed gray fill."""
    from PIL import Image
    source = Image.open(io.BytesIO(cropped)).convert("RGB")
    canvas = Image.new("RGB", tuple(int(x) for x in geometry["source_size"]), (128, 128, 128))
    x0, y0, x1, y1 = [int(x) for x in geometry["rect_xyxy"]]
    canvas.paste(source.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), (x0, y0))
    out = io.BytesIO(); canvas.save(out, "PNG"); return out.getvalue()


def run_crop_diagnostics(images: list[dict[str, Any]], config: dict[str, Any], media: Any, watermark: Any, feedback: Any | None, out: Path) -> dict[str, Any]:
    """Run D0 and offline D_oracle_geometry; oracle rows never affect recovery rates."""
    cfg = config.get("crop_diagnostic", {})
    if not cfg.get("enabled", True):
        return {"status": "DISABLED", "rows": []}
    rows: list[dict[str, Any]] = []
    subset = images[: int(cfg.get("subset_count", 6))]
    offsets = [tuple(x) for x in cfg.get("offsets", [[0.0, 0.0], [0.01, 0.0]])]
    for sample in subset:
        sid = str(sample["sample_id"]); original = (ROOT / str(sample["local_path"])).read_bytes(); mt = media.sniff(original)
        if not mt.startswith("image/"):
            continue
        fmt = "JPEG" if mt == "image/jpeg" else "PNG"; cid = deterministic_cid(int(config["seed"]), sid, 0)
        for profile in ("v1", "v2", "feedback"):
            try:
                marked, _ = embed_profile(profile, original, cid, mt, fmt, media, watermark, feedback)
            except Exception as exc:
                rows.append({"sample_id": sid, "profile": profile, "content_id": cid, "error": f"{type(exc).__name__}: {exc}", "oracle_not_production": True})
                continue
            for keep in cfg.get("keep", [0.95, 0.90]):
                for offset in offsets:
                    geometry: dict[str, Any] = {}
                    try:
                        cropped, geometry = crop_with_geometry(marked, float(keep), offset)
                        # D0 is the production blind detector on the actual crop.
                        d0 = media.detect(cropped)
                        d0_rec = strict_recovery(d0, cid)
                        oracle_bytes = oracle_geometry_fill(cropped, geometry)
                        oracle = media.detect(oracle_bytes)
                        oracle_rec = strict_recovery(oracle, cid)
                        diagnostic: dict[str, Any] = {}
                        # v2's offline diagnostic accepts truth only for analysis;
                        # it is never used by media.detect or counted as D0.
                        diag_module = feedback if profile == "feedback" else watermark
                        diag_fn = getattr(diag_module, "diagnose_image_feedback", None) if profile == "feedback" else getattr(watermark, "diagnose_image_residual", None)
                        if callable(diag_fn):
                            try:
                                diagnostic = diag_fn(cropped, cid, "PNG")
                            except Exception as exc:
                                diagnostic = {"error": f"{type(exc).__name__}: {exc}"}
                        rows.append({"sample_id": sid, "profile": profile, "content_id": cid, "keep": float(keep), "geometry": geometry, "d0": {"observed": d0, "recovery": d0_rec}, "d_oracle_geometry": {"observed": oracle, "recovery": oracle_rec, "fill_rule": "RGB(128,128,128), no original pixels"}, "oracle_not_production": True, "diagnostics": {"bit_errors": diagnostic.get("post_encode", {}).get("bit_errors_before_ecc"), "byte_errors": diagnostic.get("post_encode", {}).get("byte_errors_before_ecc"), "failure_positions": diagnostic.get("post_encode", {}).get("failure_positions", [])} if diagnostic else {}})
                    except Exception as exc:
                        rows.append({"sample_id": sid, "profile": profile, "content_id": cid, "keep": float(keep), "geometry": geometry, "d0": {"recovery": {"found_expected": False}}, "d_oracle_geometry": {"recovery": {"found_expected": False}}, "oracle_not_production": True, "error": f"{type(exc).__name__}: {exc}"})
    path = out / "crop_diagnostics.jsonl"
    for row in rows:
        append_jsonl(path, row)
    by_profile: dict[str, Any] = {}
    for profile in ("v1", "v2", "feedback"):
        subset_rows = [r for r in rows if r.get("profile") == profile]
        by_profile[profile] = {"rows": len(subset_rows), "d0_recovered": sum(bool(r.get("d0", {}).get("recovery", {}).get("found_expected")) for r in subset_rows), "oracle_recovered": sum(bool(r.get("d_oracle_geometry", {}).get("recovery", {}).get("found_expected")) for r in subset_rows)}
    return {"status": "COMPLETED", "rows": len(rows), "by_profile": by_profile, "oracle_excluded_from_recovery": True, "rows_path": str(path.relative_to(ROOT)).replace("\\", "/")}


def image_metrics(left: bytes, right: bytes) -> dict[str, Any]:
    from PIL import Image
    import numpy as np
    with Image.open(io.BytesIO(left)) as a_im, Image.open(io.BytesIO(right)) as b_im:
        a, b = np.asarray(a_im.convert("RGB"), dtype=np.float64), np.asarray(b_im.convert("RGB"), dtype=np.float64)
    if a.shape != b.shape:
        return {"status": "NOT_COMPARABLE", "shape_a": list(a.shape), "shape_b": list(b.shape)}
    from gm_provenance.quality import _pair
    return {"status": "PASS", **_pair(a, b)}


def strict_recovery(observed: dict[str, Any], expected: str) -> dict[str, Any]:
    ids = observed.get("content_ids") or observed.get("recovered_content_ids") or []
    unique = sorted({str(x) for x in ids})
    return {"expected": expected, "observed_ids": unique, "unique": len(unique) == 1, "found_expected": unique == [expected], "status": str(observed.get("status", "UNKNOWN"))}


def publish_gate(media: Any, final_bytes: bytes, cid: str, media_type: str | None) -> dict[str, Any]:
    """Invoke the production blind self-check without exposing the expected ID."""
    checker = getattr(media, "assert_publishable", None)
    if not callable(checker):
        return {"passed": False, "error": "media.assert_publishable unavailable", "diagnostics": {}}
    try:
        result = checker(final_bytes, cid, media_type=media_type)
        details = result if isinstance(result, dict) else {"result": str(result)}
        passed = bool(details.get("passed", details.get("publishable", details.get("status") in ("PASS", "PUBLISHED", "FOUND"))))
        return {"passed": passed, "result": details, "diagnostics": details.get("diagnostics", {})}
    except Exception as exc:
        diagnostics = getattr(exc, "diagnostics", {})
        final = getattr(exc, "final_bytes", None)
        out = {"passed": False, "error": f"{type(exc).__name__}: {exc}", "diagnostics": diagnostics if isinstance(diagnostics, dict) else {"raw": str(diagnostics)}}
        if isinstance(final, (bytes, bytearray)):
            out["exception_final_bytes_sha256"] = sha256_bytes(bytes(final))
        return out


def embed_profile(profile: str, data: bytes, cid: str, media_type: str, fmt: str, media: Any, watermark: Any, feedback: Any | None) -> tuple[bytes, dict[str, Any]]:
    if profile == "v1":
        return _call_encoder(watermark.embed_image, data, cid, fmt)
    if profile == "v2":
        return _call_encoder(watermark.embed_image_residual, data, cid, fmt)
    if feedback is None or not callable(getattr(feedback, "embed_image_feedback", None)):
        raise RuntimeError("r5_feedback.embed_image_feedback unavailable")
    # Exercise the production named profile route.  The direct helper remains
    # a compatibility fallback for isolated unit doubles only.
    try:
        result = media.embed(data, cid, media_type, "watermark-v2-feedback")
        if isinstance(result, tuple) and len(result) == 2:
            return bytes(result[0]), result[1] if isinstance(result[1], dict) else {"profile": result[1], "route": "media.embed"}
        return bytes(result), {"route": "media.embed"}
    except (AttributeError, TypeError):
        raw, info = _call_encoder(feedback.embed_image_feedback, data, cid, fmt)
        info.setdefault("route", "r5_feedback.embed_image_feedback")
        return raw, info


def control_profile(profile: str, data: bytes, fmt: str, watermark: Any) -> tuple[bytes, dict[str, Any]]:
    if profile == "v1":
        return _call_control(watermark.encode_without_watermark, data, fmt)
    return _call_control(watermark.encode_without_watermark_residual, data, fmt)


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 6)
    return round(statistics.quantiles(values, n=100, method="inclusive")[max(0, min(99, int(q) - 1))], 6)


def quality_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for profile in ("v1", "v2", "feedback"):
        vals = [r.get("metrics", {}).get("A_C", {}) for r in rows if r.get("profile") == profile]
        psnr = [float(v["psnr_db"]) for v in vals if isinstance(v.get("psnr_db"), (int, float)) and math.isfinite(float(v["psnr_db"]))]
        ssim = [float(v["ssim_local"]) for v in vals if isinstance(v.get("ssim_local"), (int, float)) and math.isfinite(float(v["ssim_local"]))]
        out[profile] = {"n": len(vals), "mean_psnr_db": round(statistics.mean(psnr), 6) if psnr else None, "median_psnr_db": quantile(psnr, 50), "p10_psnr_db": quantile(psnr, 10), "mean_ssim": round(statistics.mean(ssim), 6) if ssim else None, "median_ssim": quantile(ssim, 50), "p10_ssim": quantile(ssim, 10)}
    return out


def report_text(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    return f"""# Round 5 水印严格配对评测\n\n状态：`{manifest.get('status')}`；run_id：`{manifest.get('run_id')}`；图片：{manifest.get('sample_count')}；PDF 回归：{summary.get('pdf', {}).get('attempted', 0)}。\n\n本轮固定 Round 4 的 `round4|seed|sample_id|slot` ContentID、媒体编码和 development_eval 变换。每个有水印最终字节先经过 `media.assert_publishable` 盲检测 gate；gate 失败的字节仍保留用于离线诊断，但不计入 published。\n\n## 结果文件\n\n- `attempts.jsonl`：逐 profile/变换尝试、gate、恢复和诊断；\n- `quality.jsonl`：A/B/C 配对质量与逐图指标；\n- `payload_sensitivity.jsonl`：6×3 ContentID 子集；\n- `pdf_regression.jsonl`：40 个 PDF 的串行 v1 最终盲检测；\n- `summary.json`：原分母统计、new success/failure、质量和预算；\n- `run_manifest.json`：dirty 源码哈希、配置/数据摘要和硬件软件版本。\n\n复现：\n\n```text\n.\\.venv\\Scripts\\python.exe scripts/run_round5_watermark_eval.py --config configs/round5-watermark-evaluation.json\n```\n"""


def run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-round5-{uuid.uuid4().hex[:8]}"
    out = ROOT / "results" / "round5-watermark" / run_id
    out.mkdir(parents=True, exist_ok=False)
    for name in ("attempts.jsonl", "quality.jsonl", "payload_sensitivity.jsonl", "pdf_regression.jsonl", "crop_diagnostics.jsonl", "failures.jsonl"):
        (out / name).touch()
    dataset_path = ROOT / str(config["dataset_manifest"])
    manifest: dict[str, Any] = {"schema_version": config["schema_version"], "run_id": run_id, "started_at": utc_now(), "status": "initializing", "config": config, "config_path": str(config_path.relative_to(ROOT)).replace("\\", "/"), "config_sha256": sha256_bytes(json.dumps(config, ensure_ascii=False, sort_keys=True).encode()), "output_root": str(out.relative_to(ROOT)).replace("\\", "/"), "source_snapshot": source_snapshot(config_path), "dependency_snapshot": dependency_snapshot(), "dataset_manifest_sha256": sha256_file(dataset_path) if dataset_path.exists() else None}
    write_json(out / "run_manifest.json", manifest)
    attempts: list[dict[str, Any]] = []; quality_rows: list[dict[str, Any]] = []; payload_rows: list[dict[str, Any]] = []; pdf_rows: list[dict[str, Any]] = []; failures: list[dict[str, Any]] = []
    try:
        from gm_provenance import media, watermark
        try:
            from gm_provenance import r5_feedback
        except Exception:
            try:
                from gm_provenance import watermark_feedback as r5_feedback
            except Exception:
                r5_feedback = None
        images, pdfs = read_dataset(dataset_path, int(args.limit or (config["pilot_count"] if args.pilot else config["sample_count"])))
        if args.pilot and not args.limit:
            # Freeze the same six Round 4 payload-sensitivity strata for the
            # pilot; fill from the deterministic image order only if a custom
            # config names fewer than pilot_count samples.
            all_images, _ = read_dataset(dataset_path, int(config.get("sample_count", 60)))
            wanted = [str(x) for x in config.get("payload_sensitivity", {}).get("sample_ids", [])]
            by_id = {str(row.get("sample_id")): row for row in all_images}
            images = [by_id[sid] for sid in wanted if sid in by_id][: int(config.get("pilot_count", 6))]
            if len(images) < int(config.get("pilot_count", 6)):
                selected_ids = {str(row.get("sample_id")) for row in images}
                images.extend(row for row in all_images if str(row.get("sample_id")) not in selected_ids)
                images = images[: int(config.get("pilot_count", 6))]
        manifest.update({"sample_count": len(images), "sample_ids": [r.get("sample_id") for r in images], "source_hashes": {str(r.get("sample_id")): r.get("sha256") for r in images}, "feedback_module": bool(r5_feedback)})
        write_json(out / "run_manifest.json", manifest)
        specs = {str(s["name"]): s for s in config.get("transforms", [])}
        profiles = ("v1", "v2", "feedback")
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for sample in images:
            sid = str(sample["sample_id"]); original = (ROOT / str(sample["local_path"])).read_bytes(); mt = media.sniff(original); fmt = "JPEG" if mt == "image/jpeg" else "PNG"; cid = deterministic_cid(int(config["seed"]), sid, 0); sample_dir = out / "samples" / sid; sample_dir.mkdir(parents=True, exist_ok=True)
            encoded: dict[str, bytes] = {}; controls: dict[str, bytes] = {}
            for profile in profiles:
                started = time.perf_counter_ns()
                try:
                    controls[profile], _ = control_profile(profile, original, fmt, watermark)
                    final, profile_info = embed_profile(profile, original, cid, mt, fmt, media, watermark, r5_feedback)
                    encoded[profile] = final
                    gate = publish_gate(media, final, cid, mt)
                    elapsed = (time.perf_counter_ns() - started) / 1_000_000
                    diagnostic = profile_info.get("diagnostics", {}) if isinstance(profile_info, dict) else {}
                    if config.get("retain_artifacts", {}).get("exact"):
                        (sample_dir / f"{profile}-final.bin").write_bytes(final)
                    if config.get("retain_artifacts", {}).get("representative"):
                        try:
                            from PIL import Image
                            rep_dir = sample_dir / "representative"; rep_dir.mkdir(parents=True, exist_ok=True)
                            with Image.open(io.BytesIO(final)) as rep:
                                rep.convert("RGB").save(rep_dir / f"{profile}.png", "PNG")
                        except Exception:
                            pass
                    exact_obs = media.detect(final, mt)
                    recover = strict_recovery(exact_obs, cid)
                    row = {"sample_id": sid, "profile": profile, "transform": "exact", "content_id": cid, "attempted": True, "gate_passed": bool(gate.get("passed")), "published": bool(gate.get("passed")), "final_sha256": sha256_bytes(final), "bytes": len(final), "gate": gate, "observed": exact_obs, "recovery": recover, "elapsed_ms": round(elapsed, 3), "embed_profile": profile_info, "diagnostics": diagnostic}
                    attempts.append(row); by_key[(sid, profile)] = row; append_jsonl(out / "attempts.jsonl", row)
                    if config.get("retain_artifacts", {}).get("diagnostics") and diagnostic:
                        write_json(sample_dir / f"{profile}-diagnostics.json", diagnostic)
                    metric = {"A_C": image_metrics(original, final), "B_C": image_metrics(controls[profile], final), "A_B": image_metrics(original, controls[profile])}
                    qrow = {"sample_id": sid, "profile": profile, "content_id": cid, "metrics": metric, "category": sample.get("categories", "unknown"), "high_texture": "texture" in str(sample.get("categories", "")).lower(), "smalltext": "small text" in str(sample.get("categories", "")).lower()}
                    quality_rows.append(qrow); append_jsonl(out / "quality.jsonl", qrow)
                    # Negative control: an unwatermarked carrier must not be
                    # counted as an expected-ID recovery or a publish.
                    negative_observed = media.detect(controls[profile])
                    negative = strict_recovery(negative_observed, cid)
                    negative_row = {"sample_id": sid, "profile": profile, "transform": "negative_exact", "role": "negative_control", "content_id": cid, "attempted": True, "published": False, "observed": negative_observed, "recovery": negative, "false_positive": bool(negative.get("found_expected"))}
                    attempts.append(negative_row); append_jsonl(out / "attempts.jsonl", negative_row)
                except Exception as exc:
                    failure = {"sample_id": sid, "profile": profile, "transform": "exact", "attempted": True, "published": False, "error": f"{type(exc).__name__}: {exc}"}
                    diagnostics = getattr(exc, "diagnostics", None)
                    final_failed = getattr(exc, "final_bytes", None)
                    if isinstance(diagnostics, dict):
                        failure["diagnostics"] = diagnostics
                        if config.get("retain_artifacts", {}).get("diagnostics"):
                            write_json(sample_dir / f"{profile}-failed-diagnostics.json", diagnostics)
                    if isinstance(final_failed, (bytes, bytearray)):
                        failure["final_bytes_sha256"] = sha256_bytes(bytes(final_failed))
                        failure["final_bytes_length"] = len(final_failed)
                        if config.get("retain_artifacts", {}).get("exact"):
                            (sample_dir / f"{profile}-failed-final.bin").write_bytes(bytes(final_failed))
                    failures.append(failure); append_jsonl(out / "failures.jsonl", failure)
            for profile in profiles:
                source_row = by_key.get((sid, profile), {})
                final = encoded.get(profile)
                for spec in config.get("transforms", []):
                    name = str(spec["name"])
                    if name == "exact" or final is None:
                        continue
                    started = time.perf_counter_ns()
                    try:
                        transformed = transform(final, spec); observed = media.detect(transformed); rec = strict_recovery(observed, cid); elapsed = (time.perf_counter_ns() - started) / 1_000_000
                        row = {"sample_id": sid, "profile": profile, "transform": name, "content_id": cid, "attempted": True, "published": bool(source_row.get("published", False)), "source_gate_passed": bool(source_row.get("gate_passed", False)), "final_sha256": sha256_bytes(transformed), "observed": observed, "recovery": rec, "simulated": bool(spec.get("simulated", False)), "elapsed_ms": round(elapsed, 3)}
                        attempts.append(row); append_jsonl(out / "attempts.jsonl", row)
                    except Exception as exc:
                        row = {"sample_id": sid, "profile": profile, "transform": name, "content_id": cid, "attempted": True, "published": bool(source_row.get("published", False)), "recovery": {"status": "ERROR", "found_expected": False}, "error": f"{type(exc).__name__}: {exc}"}; attempts.append(row); failures.append(row); append_jsonl(out / "attempts.jsonl", row); append_jsonl(out / "failures.jsonl", row)
        payload_cfg = config.get("payload_sensitivity", {}); payload_names = [str(x) for x in payload_cfg.get("transforms", [])]; payload_ids = int(payload_cfg.get("ids_per_sample", 3)); payload_samples = {str(x) for x in payload_cfg.get("sample_ids", [])}
        if not payload_samples:
            payload_samples = {str(r["sample_id"]) for r in images[: int(payload_cfg.get("sample_count", 6))]}
        for sample in images:
            sid = str(sample["sample_id"])
            if sid not in payload_samples:
                continue
            original = (ROOT / str(sample["local_path"])).read_bytes(); mt = media.sniff(original); fmt = "JPEG" if mt == "image/jpeg" else "PNG"
            for slot in range(payload_ids):
                pid = deterministic_cid(int(config["seed"]), sid, slot)
                for profile in ("v1", "v2", "feedback"):
                    try:
                        final, _ = embed_profile(profile, original, pid, mt, fmt, media, watermark, r5_feedback)
                        gate = publish_gate(media, final, pid, mt)
                        for name in payload_names:
                            spec = specs.get(name)
                            if spec is None:
                                continue
                            transformed = transform(final, spec); observed = media.detect(transformed); rec = strict_recovery(observed, pid)
                            row = {"sample_id": sid, "payload_slot": slot, "profile": profile, "transform": name, "content_id": pid, "gate_passed": bool(gate.get("passed")), "recovery": rec, "observed": observed}
                            payload_rows.append(row); append_jsonl(out / "payload_sensitivity.jsonl", row)
                    except Exception as exc:
                        row = {"sample_id": sid, "payload_slot": slot, "profile": profile, "transform": "error", "content_id": pid, "gate_passed": False, "error": f"{type(exc).__name__}: {exc}"}; payload_rows.append(row); append_jsonl(out / "payload_sensitivity.jsonl", row)
        crop_summary = run_crop_diagnostics(images, config, media, watermark, r5_feedback, out)
        if config.get("pdf_regression", {}).get("enabled", True) and not args.pilot:
            pdf_dir = out / "pdf"; pdf_dir.mkdir(parents=True, exist_ok=True)
            for sample in pdfs[: int(config.get("pdf_regression", {}).get("max_samples", 40))]:
                sid = str(sample["sample_id"]); raw = (ROOT / str(sample["local_path"])).read_bytes(); pid = deterministic_cid(int(config["seed"]), sid, 0); started = time.perf_counter_ns()
                try:
                    marked, info = media.embed(raw, pid, "application/pdf", "watermark-v1"); gate = publish_gate(media, marked, pid, "application/pdf"); observed = media.detect(marked, "application/pdf"); rec = strict_recovery(observed, pid)
                    row = {"sample_id": sid, "content_id": pid, "attempted": True, "gate_passed": bool(gate.get("passed")), "published": bool(gate.get("passed")), "final_sha256": sha256_bytes(marked), "observed": observed, "recovery": rec, "embed_profile": info, "elapsed_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3)}
                    (pdf_dir / f"{sid}.pdf").write_bytes(marked)
                except Exception as exc:
                    row = {"sample_id": sid, "content_id": pid, "attempted": True, "gate_passed": False, "published": False, "recovery": {"found_expected": False}, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3)}
                pdf_rows.append(row); append_jsonl(out / "pdf_regression.jsonl", row)
        manifest["status"] = "completed" if not failures else "completed_with_blockers"
    except Exception as exc:
        failure = {"stage": "runner", "error": f"{type(exc).__name__}: {exc}"}; failures.append(failure); append_jsonl(out / "failures.jsonl", failure); manifest["status"] = "blocked"
    selfcheck = {p: {"attempted": sum(1 for r in attempts if r.get("profile") == p and r.get("transform") == "exact"), "passed": sum(1 for r in attempts if r.get("profile") == p and r.get("transform") == "exact" and r.get("gate_passed")), "failed": sum(1 for r in attempts if r.get("profile") == p and r.get("transform") == "exact" and not r.get("gate_passed")), "published_recovery": sum(1 for r in attempts if r.get("profile") == p and r.get("transform") == "exact" and r.get("published") and r.get("recovery", {}).get("found_expected")), "e2e_recovery": sum(1 for r in attempts if r.get("profile") == p and r.get("transform") == "exact" and r.get("recovery", {}).get("found_expected"))} for p in ("v1", "v2", "feedback")}
    transforms_summary: dict[str, Any] = {}
    for name in sorted({str(r.get("transform")) for r in attempts if r.get("role") != "negative_control"}):
        transforms_summary[name] = {}
        for p in ("v1", "v2", "feedback"):
            rows = [r for r in attempts if r.get("profile") == p and r.get("transform") == name]; transforms_summary[name][p] = {"attempted": len(rows), "recovered": sum(bool(r.get("recovery", {}).get("found_expected")) for r in rows), "published_recovered": sum(bool(r.get("published") and r.get("recovery", {}).get("found_expected")) for r in rows)}
    timing: dict[str, Any] = {}
    for profile in ("v1", "v2", "feedback"):
        for transform_name in sorted({str(r.get("transform")) for r in attempts if r.get("profile") == profile and r.get("role") != "negative_control"}):
            values = [float(r["elapsed_ms"]) for r in attempts if r.get("profile") == profile and r.get("transform") == transform_name and isinstance(r.get("elapsed_ms"), (int, float))]
            if values:
                timing[f"{profile}/{transform_name}"] = {"n": len(values), "mean_ms": round(statistics.mean(values), 3), "p50_ms": quantile(values, 50), "p95_ms": quantile(values, 95)}
    negative_rows = [r for r in attempts if r.get("role") == "negative_control"]
    feedback_profiles = [r.get("embed_profile", {}) for r in attempts if r.get("profile") == "feedback" and r.get("transform") == "exact"]
    feedback_iterations = []
    feedback_clipping: list[float] = []
    feedback_budget_failures: list[str] = []
    for profile_info in feedback_profiles:
        nested = profile_info.get("feedback", profile_info) if isinstance(profile_info, dict) else {}
        if isinstance(nested, dict) and nested.get("budget_failure"):
            feedback_budget_failures.append(str(nested["budget_failure"]))
        value = nested.get("rounds_used") or nested.get("iterations") or nested.get("iteration_count")
        if isinstance(value, (int, float)):
            feedback_iterations.append(value)
        for round_info in nested.get("rounds", []) if isinstance(nested, dict) else []:
            if isinstance(round_info, dict) and isinstance(round_info.get("clipped_fraction"), (int, float)):
                feedback_clipping.append(float(round_info["clipped_fraction"]))
    quality_agg = quality_aggregate(quality_rows)
    quality_deltas = {p: {metric: (quality_agg.get(p, {}).get(metric) - quality_agg.get("v1", {}).get(metric) if isinstance(quality_agg.get(p, {}).get(metric), (int, float)) and isinstance(quality_agg.get("v1", {}).get(metric), (int, float)) else None) for metric in ("mean_psnr_db", "median_psnr_db", "p10_psnr_db", "mean_ssim", "median_ssim", "p10_ssim")} for p in ("v2", "feedback")}
    by_sample_quality: dict[str, dict[str, Any]] = {}
    for row in quality_rows:
        sid = str(row.get("sample_id")); by_sample_quality.setdefault(sid, {})[str(row.get("profile"))] = row.get("metrics", {}).get("A_C", {})
    for sid, profiles_for_sample in by_sample_quality.items():
        base = profiles_for_sample.get("v1", {})
        base_psnr = base.get("psnr_db")
        for profile_name in ("v2", "feedback"):
            value = profiles_for_sample.get(profile_name, {}).get("psnr_db")
            profiles_for_sample.setdefault(profile_name, {})["psnr_delta_vs_v1_db"] = round(float(value) - float(base_psnr), 6) if isinstance(value, (int, float)) and isinstance(base_psnr, (int, float)) else None
    summary = {"status": manifest.get("status"), "images": {"attempted": len({r.get("sample_id") for r in attempts}), "target": config.get("sample_count")}, "self_check": selfcheck, "transforms": transforms_summary, "new_success_failure": {name: {p: {"new_success_vs_v1": transforms_summary[name][p]["recovered"] - transforms_summary[name].get("v1", {}).get("recovered", 0), "new_failure_vs_v1": transforms_summary[name].get("v1", {}).get("recovered", 0) - transforms_summary[name][p]["recovered"]} for p in ("v2", "feedback")} for name in transforms_summary}, "negative": {"attempted": len(negative_rows), "false_positive": sum(bool(r.get("false_positive")) for r in negative_rows)}, "quality": {"rows": len(quality_rows), "aggregate": quality_agg, "deltas_vs_v1": quality_deltas, "per_sample_deltas": by_sample_quality, "category": {c: quality_aggregate([r for r in quality_rows if r.get("category") == c]) for c in sorted({str(r.get("category")) for r in quality_rows})}, "high_texture": quality_aggregate([r for r in quality_rows if r.get("high_texture")]), "small_text": quality_aggregate([r for r in quality_rows if r.get("smalltext")])}, "timing": timing, "feedback_budget": {"max_iterations": config.get("quality_budget", {}).get("max_feedback_iterations", 3), "observed_iteration_values": feedback_iterations, "clipped_fraction_max": max(feedback_clipping) if feedback_clipping else None, "budget_failures": feedback_budget_failures}, "payload_sensitivity": {"attempted": len(payload_rows), "recovered": sum(bool(r.get("recovery", {}).get("found_expected")) for r in payload_rows)}, "crop_diagnostic": locals().get("crop_summary", {"status": "NOT_RUN"}), "pdf": {"attempted": len(pdf_rows), "gate_passed": sum(bool(r.get("gate_passed")) for r in pdf_rows), "recovered": sum(bool(r.get("recovery", {}).get("found_expected")) for r in pdf_rows), "rows": pdf_rows}, "failures": failures}
    manifest["finished_at"] = utc_now(); write_json(out / "run_manifest.json", manifest); write_json(out / "summary.json", summary); (out / "report.md").write_text(report_text(manifest, summary), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "run": str(out.relative_to(ROOT)), "summary": str((out / "summary.json").relative_to(ROOT))}, ensure_ascii=False))
    return 0 if manifest["status"] in ("completed", "completed_with_blockers") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Round 5 v1/v2/feedback strict paired evaluation")
    parser.add_argument("--config", default="configs/round5-watermark-evaluation.json")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--pilot", action="store_true", help="run the configured stratified pilot subset")
    parser.add_argument("--limit", type=int, default=None, help="override image count; PDF regression remains serial")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
