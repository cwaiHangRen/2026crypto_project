"""Separate internal mixed-ID/sidecar diagnostic using two normal publications.

No source dataset is required. This does not close the original external task
inventory. Private material stays in the ignored output directory and secrets
are never serialized. Each input is published exactly once, without gate retries.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import secrets
import sys
import time
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gm_provenance import media  # noqa: E402
from gm_provenance.service import ProvenanceService  # noqa: E402
from round6_records import atomic_json, digest  # noqa: E402
import run_round6_sync_eval as evaluator  # noqa: E402

DECODERS = ("dct-difference-v1", "decoder-v2-sync", "decoder-v3-sync")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative(path: str | Path) -> str:
    return Path(path).resolve().relative_to(ROOT).as_posix()


def run(output: Path) -> Path:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("use a fresh output directory; existing evidence is preserved")
    output.mkdir(parents=True, exist_ok=True)
    private_dir = output / "private"
    private_dir.mkdir()
    service = ProvenanceService(output / "registry")
    password = secrets.token_urlsafe(32)
    private, public = private_dir / "publisher.pem", output / "publisher-public.pem"
    service.keygen(private, public, password)
    key_id = service.trust_key(public, "crop-handoff-internal")
    published = []
    for index, color in enumerate(((245, 248, 252), (230, 240, 245))):
        source = output / f"source-{index}.png"
        image = Image.new("RGB", (768, 768), color)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((64, 64, 704, 704), 24, fill=(31 + index * 20, 78, 121))
        draw.text((210, 350), f"Internal crop handoff {index}", fill="white")
        image.save(source, "PNG")
        # Normal service chooses one random ID and enforces final-byte gate.
        result = service.register(source, private, password, "crop-handoff-internal")
        published.append(result)
    assert published[0]["content_id"] != published[1]["content_id"]
    state_password = secrets.token_urlsafe(32)
    state_private = private_dir / "state.pem"
    state_public = output / "state-public.pem"
    service.keygen(state_private, state_public, state_password)
    service.state_snapshot(state_private, state_password, state_public_path=state_public)
    originals = [service.verify(p["media_path"], p["manifest_path"], "full",
                                state_snapshot_path=output / "registry/state-snapshot.json",
                                state_public_path=output / "state-public.pem") for p in published]
    assert all(x["conclusion"] == "VERIFIED_EXACT" and x["signature_valid"] == "PASS" for x in originals)
    baseline = {"status": service.registry.status(), "contents": service.registry.list_contents(),
                "trusted_key": {k: v for k, v in service.registry.get_key(key_id).items() if k != "public_pem"}}
    db_before = sha(service.registry.db_path.read_bytes())
    source_refs = [{"content_id": p["content_id"], "media_path": relative(p["media_path"]),
                    "manifest_path": relative(p["manifest_path"]),
                    "media_sha256": sha(Path(p["media_path"]).read_bytes()),
                    "manifest_sha256": sha(Path(p["manifest_path"]).read_bytes()),
                    "publish_self_check": p["watermark_profile"]["publish_self_check"],
                    "original_full": v, "origin": "self-created internal fixture; not external challenge"}
                   for p, v in zip(published, originals)]
    a = Image.open(published[0]["media_path"]).convert("RGB")
    b = Image.open(published[1]["media_path"]).convert("RGB")
    records = []
    for name in ("horizontal-a-b", "horizontal-b-a", "vertical-a-b"):
        vertical = name.startswith("vertical")
        left, right = (b, a) if name == "horizontal-b-a" else (a, b)
        canvas = Image.new("RGB", (768, 1536) if vertical else (1536, 768), (128, 128, 128))
        canvas.paste(left, (0, 0))
        canvas.paste(right, (0, 768) if vertical else (768, 0))
        path = output / f"{name}.png"
        canvas.save(path, "PNG")
        raw = path.read_bytes()
        record = {"name": name, "scope": "internal supplemental; original external missing tasks remain blocked",
                  "sources": source_refs, "combination": name + ": RGB decode, no resizing, fixed gray fill, PNG encoding",
                  "path": relative(path), "sha256": sha(raw), "sm3": service.crypto.sm3(raw),
                  "bytes": len(raw), "size": list(canvas.size), "detectors": {}, "sidecar_validation": {}}
        for decoder in DECODERS:
            started = time.perf_counter()
            observed = media.detect(raw, decoder_profile=decoder)
            record["detectors"][decoder] = {**observed, "wall_ms": round((time.perf_counter() - started) * 1000, 3)}
            # Separate validation calls and their times are retained, not fused.
            validations = []
            for p in published:
                validation = service.verify(path, p["manifest_path"], "full", decoder_profile=decoder,
                                            state_snapshot_path=output / "registry/state-snapshot.json",
                                            state_public_path=output / "state-public.pem")
                assert validation["content_hash_match"] == "FAIL"
                assert validation["conclusion"] not in ("VERIFIED_EXACT", "VERIFIED_DERIVATIVE", "VERIFIED_BINDING_ONLY")
                validations.append({"original_content_id": p["content_id"], "result": validation})
            record["sidecar_validation"][decoder] = validations
        records.append(record)
        atomic_json(output / "mixed_ids.json", records)
    final = {"status": service.registry.status(), "contents": service.registry.list_contents(),
             "trusted_key": {k: v for k, v in service.registry.get_key(key_id).items() if k != "public_pem"}}
    assert digest(baseline) == digest(final), "verification changed business registry state"
    # Reusable demo artifacts: exact success, crop source hint and unmarked failure.
    crop, _ = evaluator.transform(Path(published[0]["media_path"]).read_bytes(),
                                  {"operation": "crop", "keep": 0.95, "offset": [0.0, 0.0]})
    crop_path = output / "demo-crop.png"
    crop_path.write_bytes(crop)
    demo = {"exact": {"media": source_refs[0]["media_path"], "manifest": source_refs[0]["manifest_path"]},
            "crop": {"media": relative(crop_path), "results": {
                decoder: service.verify(crop_path, None, "recover", decoder_profile=decoder) for decoder in DECODERS}},
            "unmarked": {"media": relative(output / "source-0.png"),
                         "result": service.verify(output / "source-0.png", None, "recover")},
            "registry_root": relative(service.root), "label": "selected internal demos, not benchmark evidence"}
    atomic_json(output / "demo.json", demo)
    summary = {"schema_version": "crop-mixed-id-internal-2", "status": "completed", "cases": len(records),
               "external_tasks_closed": 0, "normal_signed_publications": len(published),
               "source_content_ids": [p["content_id"] for p in published], "sources": source_refs,
               "status_counts": {decoder: dict(Counter(r["detectors"][decoder]["status"] for r in records)) for decoder in DECODERS},
               "returned_id_counts": {decoder: dict(Counter(str(len(set(r["detectors"][decoder].get("content_ids", [])))) for r in records)) for decoder in DECODERS},
               "sidecar_precise_binding_acceptances": 0, "sidecar_checks": len(records) * len(DECODERS) * len(published),
               "registry_logic_unchanged": digest(baseline) == digest(final), "registry_logic_sha256": digest(final),
               "sqlite_bytes_unchanged": db_before == sha(service.registry.db_path.read_bytes()),
               "code_snapshot": {f: sha((ROOT / f).read_bytes()) for f in (
                   "src/gm_provenance/watermark_sync.py", "src/gm_provenance/watermark.py",
                   "src/gm_provenance/media.py", "src/gm_provenance/service.py", "scripts/diagnose_round8_mixed_ids.py")},
               "demo": relative(output / "demo.json")}
    atomic_json(output / "summary.json", summary)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal signed mixed-ID diagnostic, three cases")
    parser.add_argument("--output", default="results/crop-handoff-closeout/internal-mixed-20261014")
    args = parser.parse_args()
    path = (ROOT / args.output).resolve()
    if not path.is_relative_to(ROOT / "results"):
        raise ValueError("output must stay in the project's ignored results directory")
    print(run(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
