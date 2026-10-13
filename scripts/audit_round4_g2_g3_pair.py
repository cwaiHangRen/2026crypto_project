#!/usr/bin/env python3
"""Strict G2/G3 causal pairing: one encoded copy, external credential only."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from gm_provenance import manifest, media
from gm_provenance.crypto import DEFAULT_USER_ID
from gm_provenance.service import ProvenanceService

ROOT = Path(__file__).resolve().parents[1]
IDS = ["img-real-coco-139", "img-synth-001", "img-synth-002", "img-synth-004", "img-synth-005", "img-synth-006"]


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cid_for(sample_id: str) -> str:
    return hashlib.sha256(f"round4-g2-g3|{sample_id}".encode()).hexdigest()[:32]


def make_signed_manifest(service: ProvenanceService, source_name: str, marked: bytes, media_type: str,
                         cid: str, private: Path, password: str, publisher: str, profile: dict) -> dict:
    public = service._public_from_private(private, password)
    key_id = service.crypto.key_id(public)
    metadata = {"source_name": source_name, "source_size": len(marked), "watermark_media_profile": profile}
    body = {
        "schema_version": "1.0", "asset_id": secrets.token_hex(16), "content_id": cid,
        "version": 1, "publisher_id": publisher, "signer_key_id": key_id,
        "generator": {"name": "gm-provenance", "version": "0.1.0", "method": "round4-g2-g3-pair"},
        "created_at": utc(), "media_type": media_type, "content_size": len(marked),
        "content_hash_alg": "SM3", "content_hash": service.crypto.sm3(marked),
        "metadata": metadata, "metadata_hash": service.crypto.sm3(manifest._canonical(metadata)),
        "parent_content_id": None, "parent_manifest_hash": None,
        "transformation": {"operation": "publish"},
        "allowed_transformations": ["jpeg-reencode", "resize", "pdf-page-render"],
        "watermark_profile": profile, "signature_suite": "SM2-SM3-DER", "sm2_user_id": DEFAULT_USER_ID,
    }
    envelope = manifest.sign_manifest(body, service.crypto, private, password)
    mh = manifest.manifest_hash(body, service.crypto)
    service.registry.publish(envelope, marked, mh)
    return envelope


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="results/round4-g2-g3-pair")
    args = ap.parse_args()
    out = ROOT / args.output; out.mkdir(parents=True, exist_ok=False)
    rows = []
    dataset = [json.loads(line) for line in (ROOT / "data/round3-development-v3/dataset_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {str(row["sample_id"]): row for row in dataset}
    for sample_id in IDS:
        row = by_id[sample_id]; source = ROOT / str(row["local_path"]); raw = source.read_bytes(); mt = media.sniff(raw)
        cid = cid_for(sample_id); marked, profile = media.embed(raw, cid, mt, "watermark-v1")
        sample_dir = out / sample_id; sample_dir.mkdir(parents=True, exist_ok=True); marked_path = sample_dir / "same-media.bin"; marked_path.write_bytes(marked)
        g2 = media.detect(marked, mt)
        registry_root = sample_dir / "g3-registry"; service = ProvenanceService(registry_root)
        private, public = sample_dir / "publisher-private.pem", sample_dir / "publisher-public.pem"; password = "round4-g2-g3-local-pass"
        service.keygen(private, public, password); service.trust_key(public, "round4-publisher")
        envelope = make_signed_manifest(service, source.name, marked, mt, cid, private, password, "round4-publisher", profile)
        manifest_path = sample_dir / "g3-manifest.json"; manifest_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        g3 = media.detect(marked, mt)
        hard = service.verify(marked_path, manifest_path, "hard")
        row_out = {"sample_id": sample_id, "content_id": cid, "media_type": mt, "same_media_sha256": hashlib.sha256(marked).hexdigest(),
                   "same_media_bytes": True, "g2": g2, "g3_detector_on_same_bytes": g3,
                   "detector_equal": g2 == g3, "g2_found_expected": cid in g2.get("content_ids", []),
                   "g3_found_expected": cid in g3.get("content_ids", []),
                   "g3_hard_conclusion": hard.get("conclusion"),
                   "credential_created_after_media": True}
        rows.append(row_out)
        (sample_dir / "row.json").write_text(json.dumps(row_out, ensure_ascii=False, indent=2), encoding="utf-8")
        private.unlink(missing_ok=True)
    summary = {"schema_version": "round4-g2-g3-pair-1", "samples": len(rows), "same_media_sha256_all": all(r["same_media_bytes"] for r in rows), "detector_equal_all": all(r["detector_equal"] for r in rows), "g2_expected_found": sum(r["g2_found_expected"] for r in rows), "g3_expected_found": sum(r["g3_found_expected"] for r in rows), "hard_verified_binding_only": sum(r["g3_hard_conclusion"] == "VERIFIED_BINDING_ONLY" for r in rows), "rows": rows, "interpretation": "G2 and G3 use one pre-encoded media copy. G3 adds a signed registry credential after that copy exists; it does not re-embed, re-encode, or alter detector input bytes."}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "samples": len(rows), "same_media_sha256_all": summary["same_media_sha256_all"], "detector_equal_all": summary["detector_equal_all"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
