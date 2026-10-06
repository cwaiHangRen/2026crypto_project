from pathlib import Path

import pytest

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.state import sign_snapshot, verify_snapshot


@pytest.fixture()
def keypair(tmp_path: Path):
    try:
        crypto = OpenSSLCrypto()
    except Exception as exc:
        pytest.skip(f"OpenSSL unavailable: {exc}")
    private, public = tmp_path / "state.key", tmp_path / "state.pub"
    crypto.generate_key(private, public, "state-pass")
    return crypto, private, public


def test_snapshot_roundtrip_and_tamper(keypair):
    crypto, private, public = keypair
    snap = sign_snapshot({"sequence": 7, "latest": {"a": "c"}, "revoked_content": [],
                          "revoked_keys": [], "revocations": []}, crypto, str(private), "state-pass")
    assert verify_snapshot(snap, crypto, public.read_bytes())["status"] == "PASS"
    snap["body"]["sequence"] = 8
    assert verify_snapshot(snap, crypto, public.read_bytes())["status"] == "FAIL"


def test_snapshot_rollback_high_water(keypair):
    crypto, private, public = keypair
    snap = sign_snapshot({"sequence": 2, "latest": {}, "revoked_content": [], "revoked_keys": [], "revocations": []}, crypto, str(private), "state-pass")
    out = verify_snapshot(snap, crypto, public.read_bytes(), high_water=3)
    assert out["status"] == "FAIL" and "rollback" in out["reasons"][0]
