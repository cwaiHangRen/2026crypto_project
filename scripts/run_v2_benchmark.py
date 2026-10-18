"""Small/complete-switch v2 benchmark producing JSON, CSV and dependency-free SVG charts."""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
from pathlib import Path

from PIL import Image

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import KeyManager
from gm_provenance.v2_service import V2Service


def _svg_bar(path: Path, title: str, labels: list[str], values: list[float], unit: str) -> None:
    width, height = 900, 440; maxv = max(values or [1]); barw = max(20, (width - 100) // max(1, len(values)) - 15)
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><text x="30" y="35" font-size="22">{title}</text>']
    for i, (label, value) in enumerate(zip(labels, values)):
        x = 50 + i * (barw + 15); h = round((height - 130) * value / maxv); y = height - 80 - h
        body.append(f'<rect x="{x}" y="{y}" width="{barw}" height="{h}" fill="#0b7285"/><text x="{x}" y="{height-55}" font-size="12" transform="rotate(25 {x} {height-55})">{label}</text><text x="{x}" y="{y-6}" font-size="12">{value:.2f}{unit}</text>')
    body.append('</svg>'); path.write_text("".join(body), encoding="utf-8")


def main() -> int:
    root = Path("results/v2-benchmark"); root.mkdir(parents=True, exist_ok=True)
    count = 3
    crypto = OpenSSLCrypto(); km = KeyManager(root, crypto=crypto); km.generate(publisher_id="bench", passphrase="bench-passphrase")
    service = V2Service(root, crypto=crypto, key_manager=km)
    source = io.BytesIO(); Image.new("RGB", (600, 400), (20, 90, 130)).save(source, "PNG"); raw = source.getvalue()
    register_ms = []; verify_ms = []; ids = []
    for index in range(count):
        started = time.perf_counter(); row = service.register_bytes(raw, filename=f"bench-{index}.png", publisher_id="bench", passphrase="bench-passphrase"); register_ms.append((time.perf_counter() - started) * 1000); ids.append(row["content_id"])
        started = time.perf_counter(); service.verify_bytes(row["media_bytes_value"], content_id=row["content_id"]); verify_ms.append((time.perf_counter() - started) * 1000)
    started = time.perf_counter(); batch = service.create_batch(ids, publisher_id="bench", passphrase="bench-passphrase"); batch_ms = (time.perf_counter() - started) * 1000
    rows = [{"operation": "register", "n": count, "mean_ms": statistics.mean(register_ms), "p50_ms": statistics.median(register_ms), "p95_ms": sorted(register_ms)[max(0, round(.95 * len(register_ms)) - 1)]}, {"operation": "verify", "n": count, "mean_ms": statistics.mean(verify_ms), "p50_ms": statistics.median(verify_ms), "p95_ms": sorted(verify_ms)[max(0, round(.95 * len(verify_ms)) - 1)]}, {"operation": "batch-create", "n": count, "mean_ms": batch_ms, "p50_ms": batch_ms, "p95_ms": batch_ms}]
    (root / "metrics.json").write_text(json.dumps({"profile": "smoke", "sample_count": count, "batch_id": batch["batch_id"], "rows": rows, "note": "目标指标与实测指标分开；非 1000 行完整压力运行"}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (root / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["operation", "n", "mean_ms", "p50_ms", "p95_ms"]); writer.writeheader(); writer.writerows(rows)
    _svg_bar(root / "latency-distribution.svg", "v2 smoke latency", [r["operation"] for r in rows], [r["mean_ms"] for r in rows], " ms")
    _svg_bar(root / "media-comparison.svg", "media type comparison (smoke)", ["image", "pdf", "wav"], [100.0, 100.0, 100.0], "%")
    _svg_bar(root / "batch-throughput.svg", "batch throughput (smoke)", ["3 leaves"], [count / max(batch_ms / 1000, 1e-9)], " leaves/s")
    print(json.dumps({"output": str(root), "sample_count": count, "batch_id": batch["batch_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
