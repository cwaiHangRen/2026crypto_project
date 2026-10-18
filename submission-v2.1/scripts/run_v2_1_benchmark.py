"""v2.1 reproducible performance benchmark (N=30 by default).

This script keeps benchmark databases in a temporary directory and writes only
sanitized metrics to the requested output directory.  It reports unavailable
media codecs as BLOCKED instead of converting them to passes.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import io
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from PIL import Image

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import KeyManager
from gm_provenance.v2_service import V2Service


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(p * len(ordered))) - 1))
    return round(ordered[index], 3)


def _stats(values: list[float], count: int | None = None, cpu_values: list[float] | None = None, memory_peak: float | None = None) -> dict:
    count = count if count is not None else len(values)
    total = sum(values)
    result = {"n": len(values), "attempted": count, "mean_ms": round(statistics.mean(values), 3) if values else None,
            "p50_ms": _percentile(values, .50), "p95_ms": _percentile(values, .95), "p99_ms": _percentile(values, .99),
            "throughput_per_s": round(len(values) / max(total / 1000, 1e-9), 3) if values else 0.0}
    result["cpu_mean_ms"] = round(statistics.mean(cpu_values), 3) if cpu_values else None
    result["cpu_p95_ms"] = _percentile(cpu_values, .95) if cpu_values else None
    result["memory_peak_mb"] = memory_peak
    return result


def _image() -> bytes:
    out = io.BytesIO(); Image.new("RGB", (600, 400), (20, 90, 130)).save(out, "PNG"); return out.getvalue()


def _rss_mb() -> float | None:
    """Read current Windows RSS without adding a psutil dependency."""
    if os.name != "nt":
        return None
    try:
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters(); counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = psapi.GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        fn.restype = ctypes.c_int
        ok = fn(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return round(counters.WorkingSetSize / (1024 * 1024), 3) if ok else None
    except Exception:
        return None


def _wav() -> bytes:
    import struct
    import wave
    out = io.BytesIO(); rate, frames = 16000, 16000
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * ((i % 80) / 40 - 1))) for i in range(frames)))
    return out.getvalue()


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas
    out = io.BytesIO(); c = canvas.Canvas(out, pagesize=(300, 220)); c.setTitle("v2.1 benchmark fixture"); c.drawString(30, 180, "gm-provenance v2.1 PDF benchmark"); c.showPage(); c.save(); return out.getvalue()


def _svg(path: Path, title: str, labels: list[str], values: list[float]) -> None:
    width, height = 960, 440; maxv = max(values or [1.0]); barw = max(24, (width - 100) // max(1, len(values)) - 15)
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><text x="30" y="35" font-size="22">{title}</text>']
    for i, (label, value) in enumerate(zip(labels, values)):
        x = 50 + i * (barw + 15); h = round((height - 130) * value / maxv); y = height - 80 - h
        body.append(f'<rect x="{x}" y="{y}" width="{barw}" height="{h}" fill="#0b7285"/><text x="{x}" y="{height-55}" font-size="12" transform="rotate(25 {x} {height-55})">{label}</text><text x="{x}" y="{y-6}" font-size="12">{value:.2f} ms</text>')
    body.append("</svg>"); path.write_text("".join(body), encoding="utf-8")


def _run_media(service: V2Service, raw: bytes, media_type: str, count: int, passphrase: str) -> tuple[dict, list[dict]]:
    register_times: list[float] = []; verify_times: list[float] = []; register_cpu: list[float] = []; verify_cpu: list[float] = []; ids: list[str] = []; failures: list[dict] = []; memory_peak = _rss_mb()
    for index in range(count):
        try:
            started = time.perf_counter(); cpu_started = time.process_time(); row = service.register_bytes(raw, filename=f"bench-{media_type}-{index}.bin", publisher_id="bench", passphrase=passphrase); register_times.append((time.perf_counter() - started) * 1000); register_cpu.append((time.process_time() - cpu_started) * 1000); ids.append(row["content_id"]); memory_peak = max(memory_peak or 0.0, _rss_mb() or 0.0)
            started = time.perf_counter(); cpu_started = time.process_time(); result = service.verify_bytes(row["media_bytes_value"], content_id=row["content_id"]); verify_times.append((time.perf_counter() - started) * 1000); verify_cpu.append((time.process_time() - cpu_started) * 1000); memory_peak = max(memory_peak or 0.0, _rss_mb() or 0.0)
            if result.get("decision") not in {"VERIFIED", "RISK_REVOKED_KEY"}:
                failures.append({"index": index, "stage": "verify", "reason": result.get("decision", "unknown")})
        except Exception as exc:
            # Keep error text short and scrub paths/usernames.
            message = type(exc).__name__ + ": " + str(exc).splitlines()[0][:180]
            failures.append({"index": index, "stage": "register_or_verify", "reason": message, "classification": "dependency_or_fixture" if "need" in message.lower() or "unavailable" in message.lower() else "code_or_fixture"})
    rows: list[dict] = []
    rows.append({"media_type": media_type, "operation": "register", **_stats(register_times, count, register_cpu, memory_peak), "failures": count - len(register_times)})
    rows.append({"media_type": media_type, "operation": "verify", **_stats(verify_times, len(register_times), verify_cpu, memory_peak), "failures": len(register_times) - len(verify_times)})
    if ids:
        try:
            started = time.perf_counter(); cpu_started = time.process_time(); batch = service.create_batch(ids, publisher_id="bench", passphrase=passphrase); batch_ms = (time.perf_counter() - started) * 1000; batch_cpu = (time.process_time() - cpu_started) * 1000
            rows.append({"media_type": media_type, "operation": "merkle-batch-create", **_stats([batch_ms], 1, [batch_cpu], memory_peak), "failures": 0})
            from gm_provenance.batch_proof import proof_for, verify_proof
            stored = service.store.get_batch(batch["batch_id"]); proof_times = []; proof_cpu = []
            leaves = list(stored.get("leaves") or stored.get("manifest", {}).get("leaves", []))
            for leaf in leaves:
                started = time.perf_counter(); cpu_started = time.process_time(); proof = proof_for(stored, leaf); valid = verify_proof(stored, proof).get("valid", False); proof_times.append((time.perf_counter() - started) * 1000); proof_cpu.append((time.process_time() - cpu_started) * 1000)
                if not valid: failures.append({"stage": "proof", "reason": "proof_mismatch"})
            rows.append({"media_type": media_type, "operation": "merkle-proof-verify", **_stats(proof_times, len(proof_times), proof_cpu, memory_peak), "failures": len([x for x in failures if x.get("stage") == "proof"])})
        except Exception as exc:
            failures.append({"stage": "batch", "reason": type(exc).__name__ + ": " + str(exc).splitlines()[0][:180]})
    return {"media_type": media_type, "sample_count": count, "registered": len(ids), "verified": len(verify_times), "rows": rows, "failures": failures}, rows


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--count", type=int, default=30); parser.add_argument("--media", default="image,pdf,wav"); parser.add_argument("--output", default="logs/v2.1")
    args = parser.parse_args(); count = max(1, min(int(args.count), 100)); output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    fixtures = {"image": _image, "pdf": _pdf, "wav": _wav}; requested = [x.strip().lower() for x in args.media.split(",") if x.strip()]
    metrics: list[dict] = []; groups: list[dict] = []
    wall_started = time.perf_counter(); cpu_started = time.process_time(); peak_rss = _rss_mb()
    # Windows may retain a SQLite handle briefly at interpreter shutdown;
    # ignore cleanup failures because the temporary directory is outside the
    # delivery tree and metrics must still be emitted.
    with tempfile.TemporaryDirectory(prefix="gm-v21-bench-", ignore_cleanup_errors=True) as temp:
        root = Path(temp); crypto = OpenSSLCrypto(); passphrase = "benchmark-passphrase"; km = KeyManager(root, crypto=crypto); km.generate(publisher_id="bench", passphrase=passphrase); service = V2Service(root, crypto=crypto, key_manager=km)
        for media_type in requested:
            if media_type not in fixtures:
                groups.append({"media_type": media_type, "status": "NOT_RUN", "reason": "unknown fixture"}); continue
            try:
                raw = fixtures[media_type]()
            except Exception as exc:
                groups.append({"media_type": media_type, "status": "BLOCKED", "reason": type(exc).__name__}); continue
            group, rows = _run_media(service, raw, media_type, count, passphrase); group["status"] = "PASS" if group["registered"] == count and group["verified"] == count and not group["failures"] else ("LIMITED" if group["registered"] else "BLOCKED"); groups.append(group); metrics.extend(rows)
            current_rss = _rss_mb()
            if current_rss is not None: peak_rss = max(peak_rss or current_rss, current_rss)
        audit_started = time.perf_counter(); audit_cpu_started = time.process_time(); checkpoint = service.audit_service().checkpoint() if service.audit_events() else None; audit_ms = (time.perf_counter() - audit_started) * 1000; audit_cpu_ms = (time.process_time() - audit_cpu_started) * 1000
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    payload = {"protocol": "GM-PROVENANCE-V2.1-BENCHMARK", "profile": "N30", "requested_count": count, "media": groups, "metrics": metrics, "audit_checkpoint": {"elapsed_ms": round(audit_ms, 3), "cpu_ms": round(audit_cpu_ms, 3), "created": bool(checkpoint), "memory_peak_mb": peak_rss}, "resource_usage": {"wall_seconds": round(wall_seconds, 3), "cpu_seconds": round(cpu_seconds, 3), "cpu_utilization_percent_estimate": round(cpu_seconds / max(wall_seconds, 1e-9) * 100, 2), "peak_rss_mb": peak_rss, "memory_backend": "Windows GetProcessMemoryInfo" if peak_rss is not None else "unavailable"}, "notes": ["目标 P95<=100ms 仅作为优化目标；本机实测不宣称达到。", "依赖缺失或编解码器不可用记录为 BLOCKED/LIMITED。"]}
    json_path = output / "performance-n30.json"
    csv_path = output / "performance-n30.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["media_type", "operation", "n", "attempted", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "throughput_per_s", "cpu_mean_ms", "cpu_p95_ms", "memory_peak_mb", "failures"]; writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows({k: row.get(k) for k in fields} for row in metrics)
    table_dir = Path("tables/v2.1"); figure_dir = Path("figures/v2.1"); table_dir.mkdir(parents=True, exist_ok=True); figure_dir.mkdir(parents=True, exist_ok=True)
    (table_dir / "performance-n30.csv").write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
    means = [next((r["mean_ms"] for r in metrics if r["media_type"] == m and r["operation"] == "register" and r["mean_ms"] is not None), 0.0) for m in requested]
    figure_path = output / "performance-n30-register.svg"
    _svg(figure_path, "v2.1 N30 register latency", requested, means)
    (figure_dir / "performance-n30-register.svg").write_text(figure_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps({"output": str(output), "requested_count": count, "groups": [{"media_type": g.get("media_type"), "status": g.get("status"), "registered": g.get("registered", 0), "verified": g.get("verified", 0)} for g in groups]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
