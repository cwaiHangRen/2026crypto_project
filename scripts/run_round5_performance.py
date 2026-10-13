#!/usr/bin/env python3
"""Round 5 paired SM3/DCT performance experiment.

The four cases reuse the published Round 4 v1 media bytes and its registry,
state and public credential files as immutable inputs.  No new ContentID or
credential is generated.  P0--P3 differ by exactly one backend switch each:
CLI/hashlib SM3 and scalar/batch DCT.  This script is deliberately light on
diagnostic instrumentation; each trial records mutually exclusive stage
times and an explicit expected/actual outcome.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gm_provenance.crypto import OpenSSLCrypto  # noqa: E402
from gm_provenance import watermark  # noqa: E402

ROUND4_DEFAULT = ROOT / "results/round4-performance/20261007T-round4-perf-final"
CASES = ("P0_cli_scalar", "P1_hashlib_scalar", "P2_cli_batch", "P3_hashlib_batch")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def source_snapshot() -> dict:
    tracked = [ROOT / "src/gm_provenance/crypto.py", ROOT / "src/gm_provenance/watermark.py", Path(__file__)]
    files = {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in tracked}
    status = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, capture_output=True, check=False).stdout
    # Hash relevant dirty source paths without scanning prior evaluation trees,
    # which can contain intentionally protected directories.
    dirty = files.copy()
    for raw in status.decode("utf-8", errors="replace").splitlines():
        name = raw[3:].strip() if len(raw) >= 3 else ""
        if not name or name.startswith((".eval-", ".git/")):
            continue
        path = (ROOT / name).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            continue
        if path.is_file() and (name.startswith("src/gm_provenance/") or name.startswith("scripts/") or name.startswith("docs/")):
            dirty[name.replace("\\", "/")] = sha256_file(path)
    return {"source_files": files, "source_hash": sha256_bytes(json.dumps(files, sort_keys=True).encode()),
            "dirty_hash": sha256_bytes(json.dumps(dirty, sort_keys=True).encode()),
            "git_status_hash": sha256_bytes(status.strip())}


def dependencies() -> dict:
    names = ("Pillow", "numpy", "scipy", "reedsolo")
    packages = {}
    for name in names:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    value = {"packages": packages, "python": sys.version, "platform": platform.platform(),
             "machine": platform.machine(), "processor": platform.processor()}
    value["dependency_hash"] = sha256_bytes(json.dumps(value, sort_keys=True).encode())
    return value


def load_fixed_inputs(round4_root: Path) -> dict:
    if not (round4_root / "run_manifest.json").is_file():
        raise FileNotFoundError(f"Round4 run_manifest.json 不存在: {round4_root}")
    runtime = round4_root / "runtime" / "img-synth-001-root"
    media_dir = runtime / "registry" / "media"
    media_paths = sorted(media_dir.glob("*.bin"))
    if len(media_paths) != 1:
        raise ValueError("Round4 固定 v1 root media 数量不是 1")
    required = [runtime / "state.json", runtime / "state-public.pem", runtime / "registry" / "registry.sqlite"]
    if any(not p.is_file() for p in required):
        raise FileNotFoundError("Round4 固定凭证/state/registry 文件不完整")
    media = media_paths[0].read_bytes()
    return {"media": media, "media_path": media_paths[0],
            "fixed_files": {str(p.relative_to(round4_root)): sha256_file(p) for p in [*required, media_paths[0]]}}


def run_trial(case: str, media: bytes, crypto: OpenSSLCrypto) -> dict:
    dct_mode = "batch" if case in ("P2_cli_batch", "P3_hashlib_batch") else "scalar"
    expected = "FOUND"
    started = time.perf_counter_ns()
    sm3_started = time.perf_counter_ns()
    digest = crypto.sm3(media)
    sm3_ms = (time.perf_counter_ns() - sm3_started) / 1e6
    wm_started = time.perf_counter_ns()
    with watermark.dct_backend(dct_mode):
        detected = watermark.detect_image(media)
    watermark_ms = (time.perf_counter_ns() - wm_started) / 1e6
    total_ms = (time.perf_counter_ns() - started) / 1e6
    actual = detected.get("status", "ERROR")
    error_class = "success" if actual == expected else "implementation_error"
    return {"case_id": case, "sm3_backend": crypto.sm3_backend, "dct_mode": dct_mode,
            "expected_outcome": expected, "actual_outcome": actual,
            "expected_actual": {"expected": expected, "actual": actual},
            "error_class": error_class, "semantic_match": actual == expected,
            "content_ids_count": len(detected.get("content_ids", [])),
            "sm3_digest_sha256": sha256_bytes(digest.encode()), "sm3_ms": sm3_ms,
            "watermark_ms": watermark_ms, "total_ms": total_ms}


def stats(rows: list[dict], field: str) -> dict:
    values = sorted(float(r[field]) for r in rows)
    return {"n": len(values), "mean": statistics.mean(values), "p50": statistics.median(values),
            "p95": values[0] if len(values) == 1 else statistics.quantiles(values, n=100, method="inclusive")[94],
            "min": values[0], "max": values[-1]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round4-root", default=str(ROUND4_DEFAULT))
    parser.add_argument("--output-root", default="results/round5-performance")
    parser.add_argument("--run-id")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.warmups < 1 or args.repeats < 1:
        raise SystemExit("warmups/repeats 必须至少为 1")
    fixed = load_fixed_inputs(Path(args.round4_root).resolve())
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    output = (ROOT / args.output_root / run_id).resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"run_id": run_id, "schema_version": "round5-performance-1", "status": "STARTED",
                "round4_root": str(Path(args.round4_root).resolve()), "fixed_inputs": fixed["fixed_files"],
                "source": source_snapshot(), "dependencies": dependencies(),
                "cases": CASES, "warmups": args.warmups, "repeats": args.repeats,
                "measurement": "uninstrumented paired serial calls; sm3 and watermark stages are exclusive; no process/cache/state changes"}
    cryptos = {case: OpenSSLCrypto(sm3_backend=("hashlib" if "hashlib" in case else "cli")) for case in CASES}
    manifest["selected_sm3_backends"] = {case: cryptos[case].sm3_backend for case in CASES}
    (output / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    with (output / "trials.jsonl").open("w", encoding="utf-8") as fh:
        for repeat in range(args.warmups + args.repeats):
            for case in CASES:
                row = run_trial(case, fixed["media"], cryptos[case])
                row.update({"repeat": repeat, "warmup": repeat < args.warmups, "input_sha256": sha256_bytes(fixed["media"])})
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    measured = [r for r in rows if not r["warmup"]]
    summary = {case: {field: stats([r for r in measured if r["case_id"] == case], field)
                      for field in ("sm3_ms", "watermark_ms", "total_ms")}
               for case in CASES}
    summary["semantic_mismatches"] = sum(not r["semantic_match"] for r in measured)
    summary["rows_including_warmup"] = len(rows)
    summary["measured_rows"] = len(measured)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest.update({"status": "LOCAL_DIAGNOSTIC", "source_after": source_snapshot()})
    (output / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report = [f"# Round 5 性能配对（{run_id}）", "", "复用 Round 4 v1 root 固定媒体、凭证/state/registry 文件；未生成新 ContentID。",
              "P0=CLI+scalar，P1=hashlib+scalar，P2=CLI+batch，P3=hashlib+batch。正式延迟为无重度插桩调用；预热与正式重复见 summary.json。", "",
              "| case | total mean ms | total p50 ms | total p95 ms | SM3 backend | DCT mode |", "|---|---:|---:|---:|---|---|"]
    for case in CASES:
        item = summary[case]
        sample = next(r for r in measured if r["case_id"] == case)
        report.append(f"| {case} | {item['total_ms']['mean']:.3f} | {item['total_ms']['p50']:.3f} | {item['total_ms']['p95']:.3f} | {sample['sm3_backend']} | {sample['dct_mode']} |")
    report += ["", "stage_ms 为互斥计时，未归类时间为 Python 调度/检测收尾；错误分为 expected_outcome/actual_outcome/semantic_match，不能把拒绝样本计入成功。"]
    (output / "ROUND5_PERFORMANCE.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "status": manifest["status"], "output": str(output), "rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
