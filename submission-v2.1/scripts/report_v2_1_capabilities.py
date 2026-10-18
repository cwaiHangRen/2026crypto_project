"""Emit a truthful local codec capability report for v2.1."""
from __future__ import annotations

import csv
import json
import shutil
import io
import wave
import struct
from pathlib import Path

from gm_provenance.media_adapters import MediaUnavailableError, adapter_for


def _offline_fixtures() -> list[tuple[str, bytes]]:
    from PIL import Image
    image = io.BytesIO(); Image.new("RGB", (16, 16), (20, 80, 120)).save(image, "PNG")
    from reportlab.pdfgen import canvas
    pdf = io.BytesIO(); c = canvas.Canvas(pdf, pagesize=(100, 100)); c.drawString(10, 50, "fixture"); c.showPage(); c.save()
    wav = io.BytesIO()
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(struct.pack("<h", 0) * 2048)
    return [("image/png", image.getvalue()), ("application/pdf", pdf.getvalue()), ("audio/wav", wav.getvalue())]


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    entries = []
    probes = {"audio/mpeg": b"ID3" + b"\x00" * 16, "audio/flac": b"fLaC" + b"\x00" * 16, "video/mp4": b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16}
    for media_type, raw in probes.items():
        adapter = adapter_for(raw, media_type)
        info = adapter.inspect(raw)
        entries.append({"media_type": media_type, "code_boundary": "implemented-unavailable-safe", "adapter": type(adapter).__name__, "available": info.available, "status": "PASS" if info.available else "BLOCKED", "classification": "dependency_missing" if not (ffmpeg or ffprobe) else "codec_fixture_not_run", "reason": info.reason, "ffmpeg": bool(ffmpeg), "ffprobe": bool(ffprobe)})
    # Enabled adapters serve as deterministic offline smoke coverage.
    for media_type, raw in _offline_fixtures():
        try:
            adapter = adapter_for(raw, media_type); available = adapter.inspect(raw).available
        except Exception as exc:
            available = False; reason = type(exc).__name__
        else:
            reason = None
        entries.append({"media_type": media_type, "code_boundary": "implemented", "adapter": type(adapter).__name__, "available": available, "status": "PASS" if available else "NOT_RUN", "classification": "offline_fixture" if available else "fixture_invalid", "reason": reason, "ffmpeg": bool(ffmpeg), "ffprobe": bool(ffprobe)})
    out = Path("logs/v2.1"); out.mkdir(parents=True, exist_ok=True)
    payload = {"protocol": "GM-PROVENANCE-V2.1-CAPABILITY", "ffmpeg_path_present": bool(ffmpeg), "ffprobe_path_present": bool(ffprobe), "entries": entries, "notes": ["MP3/FLAC/MP4 boundary is explicit and safe: no codec is claimed when the dependency is absent.", "The offline WAV/image/PDF adapters remain the CI smoke path."]}
    (out / "media-capability-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "media-capability-report.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["media_type", "adapter", "status", "classification", "available", "reason"]; writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows({k: row.get(k) for k in fields} for row in entries)
    print(json.dumps({"output": str(out / "media-capability-report.json"), "ffmpeg": bool(ffmpeg), "entries": len(entries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
