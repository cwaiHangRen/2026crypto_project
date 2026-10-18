from __future__ import annotations

from gm_provenance.media_adapters import MediaUnavailableError, adapter_for


def test_codec_boundaries_are_explicit_and_offline_safe():
    for media_type, raw in (("audio/mpeg", b"ID3" + b"\0" * 16), ("audio/flac", b"fLaC" + b"\0" * 16), ("video/mp4", b"\0\0\0\x18ftypisom" + b"\0" * 16)):
        adapter = adapter_for(raw, media_type)
        info = adapter.inspect(raw)
        assert info.available is False
        assert info.reason
        try:
            adapter.detect(raw)
        except MediaUnavailableError:
            pass
        else:
            raise AssertionError(f"{media_type} unexpectedly used an unavailable codec")


def test_enabled_offline_adapters_have_stable_media_boundary():
    assert adapter_for(b"RIFF\0\0\0\0WAVE", "audio/wav").media_type == "audio/wav"
    assert adapter_for(b"%PDF-1.7\n", "application/pdf").media_type == "pdf"
    assert adapter_for(b"\x89PNG\r\n\x1a\n", "image/png").media_type == "image"
