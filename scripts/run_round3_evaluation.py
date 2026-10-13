#!/usr/bin/env python3
"""Run the frozen round-3 development evaluation.

The runner records a new immutable run directory for every invocation.  It
does not modify the development dataset or prior result directories and can
be stopped after a small stage before a larger stage is started.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gm_provenance import quality, watermark  # noqa: E402
from gm_provenance.development_eval import evaluate_sample, load_config  # noqa: E402
from gm_provenance.crypto import OpenSSLCrypto  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run_text(args: list[str]) -> str:
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return f"UNAVAILABLE: {type(exc).__name__}: {exc}"
    text = (p.stdout or p.stderr or "").strip()
    return text[-4000:]


def git_snapshot() -> dict[str, Any]:
    status = run_text(["git", "status", "--short"])
    return {
        "head": run_text(["git", "rev-parse", "HEAD"]),
        "status_short": status.splitlines() if status else [],
        "diff_stat": run_text(["git", "diff", "--stat"]),
        "dirty": bool(status),
    }


def memory_info() -> dict[str, Any]:
    # Keep this optional and truthful on Windows hosts without psutil.
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return {"total_bytes": vm.total, "available_bytes": vm.available, "source": "psutil"}
    except Exception:
        text = run_text(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        try:
            total = int(text.splitlines()[-1].strip())
            free_text = run_text(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1024"])
            free = int(free_text.splitlines()[-1].strip()) if free_text else None
            return {"total_bytes": total, "available_bytes": free, "source": "Win32_ComputerSystem/Win32_OperatingSystem"}
        except Exception:
            return {"total_bytes": None, "available_bytes": None, "source": "unavailable"}


def dependency_snapshot() -> dict[str, Any]:
    names = ["numpy", "scipy", "Pillow", "pypdf", "pypdfium2", "reportlab", "reedsolo", "fastapi", "starlette", "httpx"]
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "NOT_INSTALLED"
    return {
        "python": sys.version,
        "executable": Path(sys.executable).name,
        "runtime_kind": "project_venv" if ".venv" in str(Path(sys.executable)).lower() else "alternate_python_runtime",
        "platform": platform.platform(),
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count_logical": os.cpu_count(),
        "cpu_model": run_text(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"]),
        "memory": memory_info(),
        "openssl": OpenSSLCrypto()._ok(["version"]).decode().strip(),
        "packages": versions,
        "pip_check": run_text([sys.executable, "-m", "pip", "check"]),
    }


def select_rows(rows: list[dict[str, Any]], stage: int) -> list[dict[str, Any]]:
    if stage >= len(rows):
        return rows
    if stage == 6:
        return rows[:6]
    # Stage 20 keeps the six pilot rows and adds a deterministic balanced slice.
    pilot = rows[:6]
    need = stage - len(pilot)
    images = [r for r in rows[6:] if str(r.get("media_type", "")).startswith("image/")]
    pdfs = [r for r in rows[6:] if r.get("media_type") == "application/pdf"]
    target_images = round(stage * 0.6) - sum(str(r.get("media_type", "")).startswith("image/") for r in pilot)
    target_images = max(0, min(target_images, len(images)))
    target_pdfs = need - target_images
    return pilot + images[:target_images] + pdfs[:max(0, target_pdfs)]


def load_dataset(path: Path, stage: int) -> tuple[list[dict[str, Any]], str]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 100:
        raise ValueError(f"development manifest must contain 100 rows, got {len(rows)}")
    seen: set[str] = set()
    for row in rows:
        sid = str(row.get("sample_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in seen:
            raise ValueError(f"duplicate or empty sample_id: {sid!r}")
        seen.add(sid)
        p = ROOT / str(row["local_path"])
        if not p.exists():
            raise FileNotFoundError(f"dataset file missing: {p}")
        actual = sha256(p)
        if actual != row.get("sha256"):
            raise ValueError(f"sha256 mismatch for {sid}: {actual} != {row.get('sha256')}")
    return select_rows(rows, stage), sha256(path)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1], 3)


def timing_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        key = f"{row.get('group', 'unknown')}/{row.get('mode', 'unknown')}/{row.get('transform', 'unknown')}"
        if isinstance(row.get("total_ms"), (int, float)):
            groups.setdefault(key, []).append(float(row["total_ms"]))
    result: dict[str, Any] = {}
    for key, values in sorted(groups.items()):
        result[key] = {"n": len(values), "mean_ms": round(statistics.mean(values), 3), "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95)}
    return {"breakdown": result, "note": "read/verify/transform are measured separately; internal hash/signature stages are not instrumented in this run."}


def quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    image: dict[str, list[float]] = {}
    pdf: dict[str, list[bool]] = {}
    for row in rows:
        if row.get("status") != "PASS":
            continue
        if row.get("media_type") == "image":
            for pair, metrics in row.get("pairs", {}).items():
                for name in ("mse", "psnr_db", "ssim_local"):
                    value = metrics.get(name)
                    if isinstance(value, (int, float)):
                        image.setdefault(f"{pair}.{name}", []).append(float(value))
        elif row.get("media_type") == "pdf":
            changes = row.get("feature_changes", {})
            for name, value in changes.items():
                if name.endswith("measurement_warning"):
                    continue
                if isinstance(value, bool):
                    pdf.setdefault(name, []).append(value)
    return {
        "image": {k: {"n": len(v), "mean": round(statistics.mean(v), 6), "min": round(min(v), 6), "max": round(max(v), 6)} for k, v in sorted(image.items())},
        "pdf": {k: {"n": len(v), "preserved": sum(v), "rate": round(sum(v) / len(v), 6)} for k, v in sorted(pdf.items())},
        "limits": "Metrics are descriptive for this development set; synthetic rows are not real-world prevalence estimates.",
    }


def summarize(sample_rows: list[dict[str, Any]], attacks: list[dict[str, Any]], timings: list[dict[str, Any]], quality_rows: list[dict[str, Any]], failures: list[dict[str, Any]], storage: list[dict[str, Any]]) -> dict[str, Any]:
    denominator = len(sample_rows)
    handled = sum(bool(row.get("handled")) for row in sample_rows)
    attack_denominator = sum(bool(row.get("applicable", True)) for row in attacks)
    attack_handled = sum(bool(row.get("applicable", True) and row.get("handled") is True) for row in attacks)
    recovery_rows = [row for row in sample_rows if row.get("detector_input", {}).get("mode") == "recover"]
    recovery_by_group: dict[str, dict[str, Any]] = {}
    for group in ("G0", "G2", "G3"):
        group_rows = [row for row in recovery_rows if row.get("group") == group]
        recovered = sum(bool(row.get("recognition", {}).get("watermark_recovered")) for row in group_rows)
        found = sum(row.get("observed", {}).get("watermark_status") == "FOUND" for row in group_rows)
        nonempty = sum(bool(row.get("observed", {}).get("recovered_content_ids")) for row in group_rows)
        recovery_by_group[group] = {"n": len(group_rows), "watermark_found": found, "content_ids_nonempty": nonempty, "expected_content_id_recovered": recovered, "not_found": sum(row.get("observed", {}).get("watermark_status") == "NOT_FOUND" for row in group_rows), "partial_page_recovery": sum(row.get("observed", {}).get("watermark_status") == "NOT_FOUND" and bool(row.get("observed", {}).get("recovered_content_ids")) for row in group_rows), "false_positive": found if group == "G0" else None}
    return {
        "samples": {"rows": denominator, "handled": handled, "rate": handled / denominator if denominator else None, "by_group": {g: sum(row.get("group") == g for row in sample_rows) for g in ("G0", "G1", "G2", "G3")}},
        "attacks": {"rows": len(attacks), "applicable_rows": attack_denominator, "handled": attack_handled, "rate": attack_handled / attack_denominator if attack_denominator else None, "by_attack": {name: {"n": sum(row.get("attack") == name and bool(row.get("applicable", True)) for row in attacks), "handled": sum(bool(row.get("attack") == name and row.get("applicable", True) and row.get("handled") is True) for row in attacks), "not_applicable": sum(row.get("attack") == name and not bool(row.get("applicable", True)) for row in attacks)} for name in sorted({str(row.get("attack")) for row in attacks})}},
        "failures": {"rows": len(failures), "by_stage": {stage: sum(row.get("stage") == stage for row in failures) for stage in sorted({str(row.get("stage")) for row in failures})}},
        "watermark_recovery": {"rows": len(recovery_rows), "by_group": recovery_by_group, "note": "G0 FOUND counts are false positives; G2/G3 misses are detector false negatives or unsupported propagation cases and retain their denominator."},
        "storage": {"bytes": sum(int(row.get("bytes", 0)) for row in storage), "samples": len(storage)},
        "timings": timing_summary(timings),
        "quality": quality_summary(quality_rows),
    }


def report_text(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    s = summary
    q_image = s.get("quality", {}).get("image", {})
    q_pdf = s.get("quality", {}).get("pdf", {})
    recovery = s.get("watermark_recovery", {}).get("by_group", {})
    storage_bytes = s.get("storage", {}).get("bytes")
    return f"""# Round 3 实验基线报告

## A. 基线与范围

- run_id：`{manifest['run_id']}`；阶段：`{manifest['stage']}`；样本：{manifest['sample_count']}（图片 {manifest['media_counts'].get('image', 0)}，PDF {manifest['media_counts'].get('pdf', 0)}）。
- 基线：`watermark-v1`；ECC：`{manifest['watermark_backend']['ecc']}`；DCT：`{manifest['watermark_backend']['dct']}`；固定随机种子：`{manifest['seed']}`。
- 开发集含真实/来源未知的既有 pilot 与明确标注的程序合成样本；合成样本不能代表真实世界分布。

## B. 回归与运行前提

- 本轮运行器要求 Reed–Solomon 实际后端为 `reedsolo`；缺少时会在正式评测前失败。
- Git、解释器、OpenSSL、依赖和机器信息见 `run_manifest.json`。

## C. 数据集与许可

- 详细样本、哈希、source_family_id、来源和 license_status 见 `dataset_manifest.jsonl`。
- license_status 为 `review` 的样本不计入许可已确认结论；合成样本标为 internal-test-only。

## D. A/B/C 质量

质量结果见 `quality.jsonl` 和 `visual_comparisons/`。B 使用发布链但跳过系数修改，未使用 strength=0；图片记录 MSE、PSNR、11x11 局部 SSIM 及裁剪证据，PDF 使用相同 DPI 渲染并记录文字层、书签、链接、表单、几何和体积变化。本轮图片 A/B 平均 PSNR 为 `{q_image.get('A_B.psnr_db', {}).get('mean')}` dB，A/C 为 `{q_image.get('A_C.psnr_db', {}).get('mean')}` dB，B/C 为 `{q_image.get('B_C.psnr_db', {}).get('mean')}` dB；PDF 文字层保持率为 `{q_pdf.get('text_layer_preserved', {}).get('rate')}`。

## E. G0-G3 与攻击

- G0 无凭证/无水印，G1 仅签名，G2 仅水印，G3 水印+签名+状态。
- 业务行：{s['samples']['rows']}，处理符合预期：{s['samples']['handled']}（分母包含所有业务行）。
- 攻击记录：{s['attacks']['rows']}，其中适用攻击分母 {s['attacks'].get('applicable_rows', s['attacks']['rows'])}，处理符合预期：{s['attacks']['handled']}；N/A 不计入分母，逐攻击计数见 `summary.json` 与 `attacks.jsonl`。
- 水印恢复单独统计在 `summary.json.watermark_recovery`：G0 的 FOUND 是误报观察，G2/G3 的 NOT_FOUND 保留为检测能力分母，不被宽松的协议 handled 率掩盖。
- 本轮恢复分母与观察：G0 `{recovery.get('G0', {})}`，G2 `{recovery.get('G2', {})}`，G3 `{recovery.get('G3', {})}`。

## F. 性能与存储

每行保留变换和验证总耗时；当前服务没有独立的文件读取子计时，因此 `read_ms` 明确记为 `null`，不会把总耗时拆分后伪装成文件读取、SM3 或验签耗时。`summary.json` 给出各组/模式/变换的均值、P50、P95。样本工作目录体积见存储汇总。
- 最小保留产物占用约 `{round((storage_bytes or 0) / 1024 / 1024, 2)}` MiB；每样本原始执行体积和裁剪策略见 `samples.jsonl`、`summary.json`。

## G. 结论与限制

- 这是开发基线和协议回归证据，不是 100 条真实数据的鲁棒性证明。
- 截图攻击为明确标注的模拟变换；未连接浏览器或真实阅读器截图链路。
- PDF 水印发布会栅格化并丢弃原文字层，质量报告会单独显示该语义损失。
- `failures.jsonl` 为空才表示该阶段没有记录到执行错误；业务处理率和攻击处理率仍需结合 truth 与分母解释。

## H. 复现与产物

运行命令、配置、HEAD、脏工作区状态、样本哈希和依赖快照见 `run_manifest.json`。可用相同解释器重新运行：

```text
.\\.venv\\Scripts\\python.exe scripts/run_round3_evaluation.py --stage {manifest['stage']}
```
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=(6, 20, 100), default=6)
    ap.add_argument("--dataset-manifest", default="data/round3-development-v3/dataset_manifest.jsonl")
    ap.add_argument("--config", default="configs/round3-evaluation.json")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--workers", type=int, default=1, help="并行样本数；PDFium 长批处理默认串行以避免原生后端资源问题")
    args = ap.parse_args()

    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    out = ROOT / "results" / "pilot-round3" / run_id
    out.mkdir(parents=True, exist_ok=False)
    started = utc_now()
    manifest: dict[str, Any] = {"schema_version": "round3-run-1", "run_id": run_id, "stage": args.stage, "started_at": started, "seed": 20261007, "root_relative": "results/pilot-round3/" + run_id}
    try:
        selected, dataset_hash = load_dataset(ROOT / args.dataset_manifest, args.stage)
        if watermark._ECC_BACKEND != "reedsolo":
            raise RuntimeError(f"formal evaluation requires reedsolo, detected {watermark._ECC_BACKEND}")
        cfg = load_config(ROOT / args.config)
        source_files = ("pyproject.toml", "requirements.txt", "configs/round3-evaluation.json", "configs/round3-dataset.json", "configs/round3-dataset-strata.jsonl", "src/gm_provenance/watermark.py", "src/gm_provenance/service.py", "src/gm_provenance/media.py", "src/gm_provenance/development_eval.py", "src/gm_provenance/quality.py", "scripts/run_round3_evaluation.py")
        manifest.update({"dataset_manifest": args.dataset_manifest.replace("\\", "/"), "dataset_manifest_sha256": dataset_hash, "config": cfg, "sample_count": len(selected), "sample_ids": [r["sample_id"] for r in selected], "media_counts": {"image": sum(str(r.get("media_type", "")).startswith("image/") for r in selected), "pdf": sum(r.get("media_type") == "application/pdf" for r in selected)}, "watermark_backend": {"ecc": watermark._ECC_BACKEND, "dct": watermark._DCT_BACKEND, "version": watermark.VERSION, "work_size": watermark.WORK_SIZE, "repeat": watermark._REPEAT, "strength": watermark._MAGNITUDE}, "git": git_snapshot(), "environment": dependency_snapshot(), "source_hashes": {name: sha256(ROOT / name) for name in source_files}})
        write_json(out / "run_manifest.json", manifest)
        (out / "dataset_manifest.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in selected) + "\n", encoding="utf-8")
        for empty_name in ("samples.jsonl", "attacks.jsonl", "timings.jsonl", "quality.jsonl", "failures.jsonl"):
            (out / empty_name).touch()
        sample_rows: list[dict[str, Any]] = []; attack_rows: list[dict[str, Any]] = []; timing_rows: list[dict[str, Any]] = []; quality_rows: list[dict[str, Any]] = []; failure_rows: list[dict[str, Any]] = []; storage_rows: list[dict[str, Any]] = []
        workers = max(1, min(int(args.workers), len(selected)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="round3") as pool:
            futures = [pool.submit(evaluate_sample, sample, out, copy.deepcopy(cfg)) for sample in selected]
            for index, (sample, future) in enumerate(zip(selected, futures), 1):
                result = future.result()
                sample_rows.extend(result.get("samples", [])); attack_rows.extend(result.get("attacks", [])); timing_rows.extend(result.get("timings", [])); failure_rows.extend(result.get("failures", [])); storage_rows.append(result.get("storage", {}))
                append_jsonl(out / "samples.jsonl", result.get("samples", [])); append_jsonl(out / "attacks.jsonl", result.get("attacks", [])); append_jsonl(out / "timings.jsonl", result.get("timings", [])); append_jsonl(out / "failures.jsonl", result.get("failures", []))
                exact = next((r for r in result.get("samples", []) if r.get("group") == "G3" and r.get("transform") == "exact" and r.get("sidecar") == "retained"), None)
                if exact:
                    try:
                        source = ROOT / str(sample["local_path"])
                        published = Path(str(exact["media_path"]))
                        q = quality.analyze(source, published, out / "visual_comparisons" / str(sample["sample_id"]), str(sample["sample_id"]))
                        q["source_sample"] = {k: sample.get(k) for k in ("sample_id", "source_family_id", "origin_category", "license_status", "generator_claim")}
                        quality_rows.append(q); append_jsonl(out / "quality.jsonl", [q])
                    except Exception as exc:
                        failure = {"sample_id": sample["sample_id"], "stage": "quality", "error": f"{type(exc).__name__}: {exc}"}; failure_rows.append(failure); append_jsonl(out / "failures.jsonl", [failure])
                print(f"[{index}/{len(selected)}] {sample['sample_id']} rows={len(result.get('samples', []))} failures={len(result.get('failures', []))}", flush=True)
        summary = summarize(sample_rows, attack_rows, timing_rows, quality_rows, failure_rows, storage_rows)
        write_json(out / "summary.json", summary)
        (out / "report.md").write_text(report_text(manifest, summary), encoding="utf-8")
        manifest.update({"finished_at": utc_now(), "status": "completed", "output_files": sorted(p.name for p in out.iterdir() if p.is_file())})
        write_json(out / "run_manifest.json", manifest)
        return 0
    except Exception as exc:
        manifest.update({"finished_at": utc_now(), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        write_json(out / "run_manifest.json", manifest)
        (out / "failures.jsonl").write_text(json.dumps({"stage": "initialization", "error": manifest["error"]}, ensure_ascii=False) + "\n", encoding="utf-8")
        print(manifest["error"], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
