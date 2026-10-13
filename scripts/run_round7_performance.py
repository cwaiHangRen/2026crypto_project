#!/usr/bin/env python3
"""Round7 decoder-v3 synchronization performance runner.

The runner keeps Round6's fixture construction and mutually-exclusive timing
instrumentation, adding v3, D0/S0 labels, and explicit prepare/smoke modes.
"""
from __future__ import annotations
import argparse, hashlib, json, secrets, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from gm_provenance.service import ProvenanceService
from gm_provenance import media
from gm_provenance.manifest import dumps

# Reuse Round6's actual fixture/transform/timing primitives.
from run_round6_recover_performance import crop, timed_verify, quant  # noqa: E402
from run_round6_recover_performance import _image_transform  # noqa: E402

PROFILES = ("dct-difference-v1", "decoder-v2-sync", "decoder-v3-sync")
MODES = ("hard", "recover", "full")


def oracle_crop(data: bytes, source_data: bytes, keep: float, offset=(0.0, 0.0)) -> tuple[bytes, dict]:
    """Offline geometry adapter used only to classify a fixed perf case."""
    from PIL import Image
    import io
    source = Image.open(io.BytesIO(source_data)).convert("RGB")
    observed = Image.open(io.BytesIO(data)).convert("RGB")
    scale = keep ** 0.5
    width, height = round(source.width * scale), round(source.height * scale)
    left = max(0, min(source.width - width, int((source.width - width) / 2 + offset[0] * source.width)))
    top = max(0, min(source.height - height, int((source.height - height) / 2 + offset[1] * source.height)))
    canvas = Image.new("RGB", source.size, (128, 128, 128)); canvas.paste(observed.resize((width, height), Image.Resampling.LANCZOS), (left, top))
    out = io.BytesIO(); canvas.save(out, "PNG")
    return out.getvalue(), {"source_size": [source.width, source.height], "rect_xyxy": [left, top, left + width, top + height]}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provision(service, private, public, passphrase, publisher):
    service.keygen(private, public, passphrase)
    service.trust_key(public, publisher)


def prepare(run_id: str | None = None) -> Path:
    """Create the fixed Round6-compatible fixture and case manifest only."""
    rid = run_id or datetime.now().strftime("%Y%m%dT%H%M%S-round7-prepare")
    out = ROOT / "results" / "round7-performance" / rid
    out.mkdir(parents=True, exist_ok=False)
    work = out / "work"; work.mkdir()
    rows = [json.loads(x) for x in (ROOT / "data/round3-development-v3/dataset_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    idx = {x["sample_id"]: x for x in rows}
    src1, src2, pdfsrc = (ROOT / idx[k]["local_path"] for k in ("img-real-coco-139", "img-real-coco-285", "pdf-synth-001"))
    svc = ProvenanceService(work / "registry")
    priv, pub = work / "publisher.pem", work / "publisher.pub"; ppass = secrets.token_urlsafe(32)
    _provision(svc, priv, pub, ppass, "round7-perf")
    r1 = svc.register(src1, priv, ppass, publisher_id="round7-perf")
    r2 = svc.register(src2, priv, ppass, publisher_id="round7-perf")
    rp = svc.register(pdfsrc, priv, ppass, publisher_id="round7-perf")
    mp, pmp = work / "manifest.json", work / "pdf-manifest.json"
    mp.write_bytes(dumps(r1["manifest"])); pmp.write_bytes(dumps(rp["manifest"]))
    spriv, spub, snap = work / "state.pem", work / "state.pub", work / "state-snapshot.json"
    spass = secrets.token_urlsafe(32); svc.keygen(spriv, spub, spass); svc.state_snapshot(spriv, spass, snap, spub)
    a, b = Path(r1["media_path"]).read_bytes(), Path(r2["media_path"]).read_bytes()
    from PIL import Image
    import io
    ia, ib = Image.open(io.BytesIO(a)).convert("RGB"), Image.open(io.BytesIO(b)).convert("RGB")
    mm = Image.new("RGB", (ia.width + ib.width, max(ia.height, ib.height)), (127, 127, 127)); mm.paste(ia, (0, 0)); mm.paste(ib, (ia.width, 0)); mo = io.BytesIO(); mm.save(mo, "PNG")
    cases = [
        ("exact", a, True, mp), ("resize", _image_transform(a, "resize", .75), False, None),
        ("crop_center", crop(a, .90), False, None), ("crop_offset", crop(a, .95, (.02, 0)), False, None),
        ("crop_combo", _image_transform(crop(a, .957, (.01, -.01)), "resize_jpeg", [.75, 75]), False, None),
        ("negative", src1.read_bytes(), False, None), ("mixed", mo.getvalue(), False, None),
        ("pdf_exact", Path(rp["media_path"]).read_bytes(), True, pmp),
    ]
    manifest_cases = []
    for name, data, retained, _side in cases:
        expected = rp["manifest"]["body"]["content_id"] if name == "pdf_exact" else r1["manifest"]["body"]["content_id"]
        pre = {}
        for profile in PROFILES:
            try:
                mt = media.sniff(data); pre[profile] = media.detect(data, mt, decoder_profile=profile) if mt.startswith(("image/", "application/pdf")) else {"status": "UNSUPPORTED"}
            except Exception as exc:
                pre[profile] = {"status": "ERROR", "error": str(exc)}
        v3 = pre["decoder-v3-sync"]
        d0 = pre["dct-difference-v1"]
        oracle_result = None
        if name in {"crop_center", "crop_offset", "crop_combo"}:
            keep, offset = {"crop_center": (.90, (0.0, 0.0)), "crop_offset": (.95, (.02, 0.0)), "crop_combo": (.957, (.01, -.01))}[name]
            oracle_bytes, oracle_geometry = oracle_crop(data, a, keep, offset)
            oracle_result = media.detect(oracle_bytes, decoder_profile="dct-difference-v1")
        oracle_hit = bool(oracle_result and oracle_result.get("status") == "FOUND" and expected in oracle_result.get("content_ids", []))
        classification = "oracle_gap" if oracle_hit and not (d0.get("status") == "FOUND" and expected in d0.get("content_ids", [])) else ("sync_success" if v3.get("status") == "FOUND" and expected in v3.get("content_ids", []) else ("d0_success" if d0.get("status") == "FOUND" and expected in d0.get("content_ids", []) else "sync_fail"))
        manifest_cases.append({"case": name, "expected_content_id": expected, "sidecar": "retained" if retained else "lost", "preflight": pre, "oracle": oracle_result, "oracle_expected_hit": oracle_hit, "classification": classification, "sha256": hashlib.sha256(data).hexdigest()})
        (work / f"{name}.bin").write_bytes(data)
    (out / "case_manifest.json").write_text(json.dumps({"schema_version": "round7-case-manifest-1", "source": "round6-compatible-fixed-fixture", "cases": manifest_cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "fixture.json").write_text(json.dumps({"media": {k: _sha(work / f"{k}.bin") for k, *_ in cases}, "sidecars": {"image": _sha(mp), "pdf": _sha(pmp), "state": _sha(snap), "registry": _sha(work / "registry" / "registry.sqlite")}, "registry_status": svc.registry.status()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def smoke(out: Path) -> dict:
    cm = json.loads((out / "case_manifest.json").read_text(encoding="utf-8"))
    assert len(cm["cases"]) == 8
    assert {x["case"] for x in cm["cases"]} == {"exact", "resize", "crop_center", "crop_offset", "crop_combo", "negative", "mixed", "pdf_exact"}
    return {"status": "smoke_pass", "case_count": 8, "profiles": list(PROFILES), "modes": list(MODES)}


def run(repetitions=10, run_id=None) -> Path:
    out = prepare(run_id)
    work = out / "work"; cm = json.loads((out / "case_manifest.json").read_text(encoding="utf-8")); case_meta = {x["case"]: x for x in cm["cases"]}
    svc = ProvenanceService(work / "registry"); priv, pub, snap = work / "publisher.pem", work / "publisher.pub", work / "state-snapshot.json"
    # Registry is fixed before timing; all calls use identical bytes and sidecars.
    frozen = _sha(work / "registry" / "registry.sqlite")
    frozen_registry_status = svc.registry.status()
    sidecars = {"exact": work / "manifest.json", "pdf_exact": work / "pdf-manifest.json"}
    rec = []
    for name in case_meta:
        path = work / f"{name}.bin"; side = sidecars.get(name)
        for mode in MODES:
            for profile in PROFILES:
                for n in range(repetitions + 1):
                    result, timing, error = timed_verify(svc, path, side, mode, profile, snap, work / "state.pub")
                    rec.append({"case": name, "category": case_meta[name]["classification"], "sidecar": case_meta[name]["sidecar"], "mode": mode, "decoder_profile": profile, "d0_s0": "D0" if profile == "dct-difference-v1" else ("S0" if profile == "decoder-v2-sync" else "candidate-v3"), "repeat": n, "warmup": n == 0, "timing_ms": {k: round(v, 3) for k, v in timing.items()}, "conclusion": result.get("conclusion"), "watermark_status": result.get("watermark_status"), "recovered_content_ids": result.get("recovered_content_ids"), "search_complete": result.get("search_complete"), "search_stop_reason": result.get("search_stop_reason"), "state_freshness": result.get("state_freshness"), "error": error})
    (out / "trials.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rec), encoding="utf-8")
    summaries = []
    keys = ("signature_ms", "hash_ms", "parent_ms", "state_ms", "detector_ms", "geometry_ms", "dct_ms", "ecc_ms", "total_ms")
    for name in case_meta:
        for mode in MODES:
            for profile in PROFILES:
                rs = [x for x in rec if x["case"] == name and x["mode"] == mode and x["decoder_profile"] == profile and not x["warmup"]]
                ts = [x["timing_ms"]["total_ms"] for x in rs]
                summaries.append({"case": name, "category": case_meta[name]["classification"], "sidecar": case_meta[name]["sidecar"], "mode": mode, "decoder_profile": profile, "d0_s0": "D0" if profile == "dct-difference-v1" else ("S0" if profile == "decoder-v2-sync" else "candidate-v3"), "repetitions": len(rs), "p50_ms": quant(ts, .5), "p95_ms": quant(ts, .95), "max_ms": round(max(ts), 3), "timing_p50_ms": {k: quant([x["timing_ms"][k] for x in rs], .5) for k in keys}, "timing_semantics": {"signature_ms": "exclusive", "hash_ms": "exclusive", "parent_ms": "exclusive", "state_ms": "exclusive", "detector_ms": "inclusive", "total_ms": "inclusive"}, "outcomes": {c: sum(x["conclusion"] == c for x in rs) for c in sorted({x["conclusion"] for x in rs})}})
    after = _sha(work / "registry" / "registry.sqlite")
    after_registry_status = svc.registry.status()
    summary = {"schema_version": "round7-recover-performance-1", "status": "completed", "run_id": out.name, "repetitions_per_cell": repetitions, "warmups_per_cell": 1, "fixed_case_count": 8, "profiles": list(PROFILES), "modes": list(MODES), "cache_enabled": False, "freeze_before_sha256": frozen, "freeze_after_sha256": after, "registry_file_bytes_unchanged": frozen == after, "registry_unchanged": frozen_registry_status == after_registry_status, "registry_compare_basis": "canonical registry status; SQLite file bytes may change from storage housekeeping", "summary": summaries}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--repetitions", type=int, default=10); ap.add_argument("--run-id"); ap.add_argument("--prepare", action="store_true"); ap.add_argument("--smoke", action="store_true"); ap.add_argument("--input")
    a = ap.parse_args()
    if a.input:
        target = Path(a.input)
        result = smoke(target) if a.smoke else {"status": "prepared", "path": str(target)}
    elif a.prepare or a.smoke:
        target = prepare(a.run_id); result = smoke(target) if a.smoke else {"status": "prepared", "path": str(target)}
    else:
        result = {"status": "completed", "path": str(run(a.repetitions, a.run_id))}
    print(json.dumps(result, ensure_ascii=False))
