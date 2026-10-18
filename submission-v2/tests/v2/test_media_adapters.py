"""V2 media adapter contract tests; independent from v1 test names."""
import io
import wave
from pathlib import Path

import pytest
from PIL import Image

from gm_provenance.attack_lab import AttackCase, attack_summary, run_attack_matrix
from gm_provenance.media_adapters import ImageAdapter, MediaUnavailableError, PdfAdapter, WavAdapter, adapter_for

CID = "0123456789abcdef0123456789abcdef"


def image_bytes(fmt="PNG"):
    image = Image.new("RGB", (512, 512), (128, 90, 64))
    out = io.BytesIO(); image.save(out, fmt); return out.getvalue()


def pdf_bytes():
    from reportlab.pdfgen import canvas
    out = io.BytesIO(); c = canvas.Canvas(out); c.setPageSize((180, 180)); c.drawString(20, 90, "v2 adapter"); c.showPage(); c.showPage(); c.save(); return out.getvalue()


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        import struct
        frames = [int(10000 * ((i % 40) / 40 - 0.5)) for i in range(4096)]
        w.writeframes(struct.pack("<" + "h" * len(frames), *frames))
    return out.getvalue()


def test_image_adapter_roundtrip_and_attack_matrix():
    marked = ImageAdapter().embed(image_bytes(), CID).final_bytes
    assert marked
    adapter = adapter_for(marked)
    assert adapter.inspect(marked).details["width"] == 512
    result = adapter.verify(marked, CID)
    assert result.watermark_recovered is True
    cases = [AttackCase("jpeg", "image", "jpeg_quality", {"quality": 85}), AttackCase("crop", "image", "crop", {"ratio": .95})]
    rows = run_attack_matrix(marked, CID, cases, adapter=adapter)
    assert len(rows) == 2
    assert attack_summary(rows)["total"] == 2


def test_pdf_adapter_reports_pages_and_raster_gate():
    adapter = PdfAdapter()
    info = adapter.inspect(pdf_bytes())
    assert info.details["page_count"] == 2
    op = adapter.embed(pdf_bytes(), CID)
    assert op.details["pdf_raster"]["page_count"] == 2
    assert op.details["pdf_raster"]["registration_allowed"] is True


def test_wav_adapter_roundtrip_and_attack():
    adapter = WavAdapter(); op = adapter.embed(wav_bytes(), CID)
    assert op.watermark_recovered is True
    attacked = adapter.attack(op.final_bytes, "volume", {"factor": .7})
    detected = adapter.verify(attacked, CID)
    assert detected.media_type == "audio/wav"
    for attack, params in (("resample", {"factor": .8}), ("transcode", {}), ("crop", {"ratio": .8})):
        mutated = adapter.attack(op.final_bytes, attack, params)
        assert adapter.inspect(mutated).available is True


def test_mp4_feature_flag_is_explicit():
    adapter = adapter_for(b"....ftypisom")
    assert adapter.capability == "unavailable"
    assert adapter.inspect(b"....ftypisom").available is False
    with pytest.raises(MediaUnavailableError): adapter.detect(b"....ftypisom")
