"""Round7 decoder-v3 API and boundary contract tests."""
from __future__ import annotations
import io, inspect, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw
import pytest
from gm_provenance import media
from gm_provenance.service import ProvenanceService
from gm_provenance.watermark import embed_image
from gm_provenance.watermark_sync import SyncConfig, detect_image_sync

CID = "0123456789abcdef0123456789abcdef"

def marked():
    im = Image.new("RGB", (700, 906), (230, 230, 230)); ImageDraw.Draw(im).rectangle((80, 100, 620, 800), fill=(31, 78, 121)); b = io.BytesIO(); im.save(b, "PNG"); return embed_image(b.getvalue(), CID, "PNG")[0]

def crop(data, keep=.90):
    im = Image.open(io.BytesIO(data)).convert("RGB"); s = keep ** .5; w, h = round(im.width*s), round(im.height*s); o = io.BytesIO(); im.crop(((im.width-w)//2, (im.height-h)//2, (im.width-w)//2+w, (im.height-h)//2+h)).save(o, "PNG"); return o.getvalue()

def test_v3_public_api_has_no_registry_or_subprocess_and_filename_metadata_irrelevance(tmp_path):
    assert list(inspect.signature(detect_image_sync).parameters) == ["data", "config"]
    assert "registry" not in inspect.getsource(detect_image_sync).lower()
    first, second = tmp_path / "metadata-a.png", tmp_path / "renamed-metadata-b.png"; first.write_bytes(marked()); second.write_bytes(first.read_bytes())
    assert media.detect(first.read_bytes(), "image/png", decoder_profile="decoder-v3-sync")["content_ids"] == media.detect(second.read_bytes(), "image/png", decoder_profile="decoder-v3-sync")["content_ids"]

def test_v3_hard_mode_never_calls_decoder(monkeypatch, tmp_path):
    monkeypatch.setattr("gm_provenance.media.detect", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("decoder called")))
    p = tmp_path / "x.png"; p.write_bytes(marked()); r = ProvenanceService(tmp_path / "registry").verify(p, mode="hard", decoder_profile="decoder-v3-sync")
    assert r["watermark_status"] == "NOT_CHECKED" and r["decoder_profile"] == "decoder-v3-sync"

def test_v3_gate_remains_d0(monkeypatch):
    seen = []
    original = media.detect
    monkeypatch.setattr(media, "detect", lambda data, mt=None, decoder_profile="dct-difference-v1": (seen.append(decoder_profile) or original(data, mt, decoder_profile)))
    assert media.assert_publishable(marked(), CID, "image/png")["status"] == "PASS"
    assert seen == ["dct-difference-v1"]

def test_v3_sidecar_conflict_is_rejected(tmp_path):
    svc = ProvenanceService(tmp_path / "registry"); p = tmp_path / "x.png"; p.write_bytes(marked()); private, public = tmp_path / "k.pem", tmp_path / "k.pub"; svc.keygen(private, public, "pw"); svc.trust_key(public, "publisher-demo-001"); root = svc.register(p, private, "pw", publisher_id="publisher-demo-001")
    other = tmp_path / "other.png"; other.write_bytes(embed_image(Image.new("RGB", (700, 906), (40, 120, 180)).tobytes() if False else marked(), CID, "PNG")[0])
    # A manifest with a different ContentID is sufficient to exercise the API boundary.
    manifest = Path(root["manifest_path"]); body = json.loads(manifest.read_text())
    body["body"]["content_id"] = "fedcba9876543210fedcba9876543210"; manifest.write_text(json.dumps(body))
    r = svc.verify(p, manifest, mode="full", decoder_profile="decoder-v3-sync")
    assert "sidecar_watermark_conflict" in r["reasons"] and r["conclusion"] == "REJECTED"

def test_v3_soft_budget_and_truncation_semantics():
    data = crop(marked(), .90)
    r = detect_image_sync(data, SyncConfig(profile="decoder-v3-sync", max_candidates=1, max_decode_attempts=1, max_search_ms=1000))
    assert r["search_complete"] is False
    assert r["stop_reason"] in {"candidate_budget_exhausted", "decode_budget_exhausted", "time_budget_exhausted"}
    assert r["status"] in {"CANDIDATE_BUDGET_EXHAUSTED", "DECODE_BUDGET_EXHAUSTED", "TIME_BUDGET_EXHAUSTED"}

def test_v3_pdf_is_explicitly_unsupported():
    assert media.detect(b"%PDF-1.4\n", "application/pdf", decoder_profile="decoder-v3-sync")["status"] == "UNSUPPORTED"

def test_round7_prepare_and_smoke(tmp_path):
    script = Path(__file__).parents[2] / "scripts" / "run_round7_performance.py"
    proc = subprocess.run([sys.executable, str(script), "--prepare", "--smoke"], capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout); assert result["status"] == "smoke_pass"; assert result["case_count"] == 8
