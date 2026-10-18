import json

from gm_provenance.codec_capabilities import build_capability_report
from gm_provenance.media_adapters import MediaUnavailableError, adapter_for


def test_capability_report_is_privacy_safe_and_classifies_codecs():
    report = build_capability_report()
    assert report["schema_version"].startswith("v2.1-")
    assert set(report["formats"]) == {"audio/mpeg", "audio/flac", "video/mp4"}
    text = json.dumps(report, ensure_ascii=False)
    assert "C:\\Users" not in text
    assert "private_keys" in report["privacy"]


def test_mp3_flac_mp4_offline_fixtures_never_fake_success():
    fixtures = [(b"ID3" + b"\0" * 32, "audio/mpeg"), (b"fLaC" + b"\0" * 32, "audio/flac"), (b"\0\0\0\x18ftypisom" + b"\0" * 16, "video/mp4")]
    for payload, media_type in fixtures:
        adapter = adapter_for(payload, media_type)
        info = adapter.inspect(payload)
        assert info.available is False
        assert info.reason
        try:
            adapter.detect(payload)
        except MediaUnavailableError:
            pass
        else:
            raise AssertionError("codec fixture must not be reported as verified")

