"""Offline v2 demonstration: ten fixed image samples, key lifecycle,
Merkle batch, attack matrix, audit export and a small performance summary."""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from gm_provenance.attack_lab import attack_summary, run_attack_matrix, write_attack_matrix
from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import KeyManager
from gm_provenance.media_adapters import adapter_for
from gm_provenance.v2_service import V2Service


def main() -> int:
    root = Path("results/v2-demo")
    root.mkdir(parents=True, exist_ok=True)
    crypto = OpenSSLCrypto()
    keys = KeyManager(root, crypto=crypto)
    key = keys.generate(publisher_id="demo-v2", passphrase="demo-passphrase")
    sample_dir = Path("sample_data/v2-demo")
    sample_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for i in range(10):
        path = sample_dir / f"fixed-image-{i+1:02d}.png"
        if not path.exists():
            image = Image.new("RGB", (600, 400), ((31 + i * 17) % 256, (78 + i * 11) % 256, (121 + i * 7) % 256))
            draw = ImageDraw.Draw(image)
            draw.rectangle((40 + i * 3, 40 + i * 2, 240 + i * 3, 180 + i * 2), outline=(240, 240, 240), width=4)
            draw.text((60, 210), f"gm-provenance-v2 sample {i+1:02d}", fill=(245, 245, 245))
            image.save(path, "PNG", optimize=False)
        samples.append(path.read_bytes())
    service = V2Service(root, crypto=crypto, key_manager=keys)
    published = [service.register_bytes(raw, filename=f"fixed-image-{i+1:02d}.png", publisher_id="demo-v2", passphrase="demo-passphrase") for i, raw in enumerate(samples)]
    verified = [service.verify_bytes(row["media_bytes_value"], content_id=row["content_id"]) for row in published]
    batch = service.create_batch([row["content_id"] for row in published], publisher_id="demo-v2", passphrase="demo-passphrase")
    adapter = adapter_for(published[0]["media_bytes_value"])
    started = time.perf_counter()
    attacks = run_attack_matrix(published[0]["media_bytes_value"], published[0]["content_id"], adapter=adapter, output_dir=root / "attacks", log_path=root / "attack-matrix.json")
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    write_attack_matrix(attacks, root / "attack-matrix.csv")
    audit = service.audit_service().export(root / "audit-export.json")
    metrics = {
        "protocol": "gm-provenance-v2",
        "key": keys.sanitized(key),
        "sample_count": len(samples),
        "registered": len(published),
        "verified": sum(x["decision"] == "VERIFIED" for x in verified),
        "decisions": [x["decision"] for x in verified],
        "batch": {k: v for k, v in batch.items() if k not in {"signature_b64"}},
        "attack_summary": attack_summary(attacks),
        "attack_total_ms": elapsed,
        "audit": audit,
    }
    (root / "demo-summary.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with (root / "performance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["operation", "count", "elapsed_ms"]); writer.writeheader()
        writer.writerow({"operation": "register+verify", "count": len(published), "elapsed_ms": sum(x["elapsed_ms"] for x in verified)})
        writer.writerow({"operation": "attack_matrix", "count": len(attacks), "elapsed_ms": elapsed})
    print(json.dumps({"output": str(root), "registered": len(published), "verified": metrics["verified"], "batch_id": batch["batch_id"], "audit_valid": audit["verification"]["valid"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
