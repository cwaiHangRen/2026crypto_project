from pathlib import Path
from datetime import datetime, timedelta

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


def test_snapshot_time_boundaries(keypair):
    crypto, private, public = keypair
    snap = sign_snapshot({"sequence": 3}, crypto, str(private), "state-pass", ttl_seconds=60)
    issued = datetime.fromisoformat(snap["body"]["issued_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(snap["body"]["expires_at"].replace("Z", "+00:00"))
    assert verify_snapshot(snap, crypto, public.read_bytes(), now=issued)["status"] == "PASS"
    assert verify_snapshot(snap, crypto, public.read_bytes(), now=expires - timedelta(microseconds=1))["status"] == "PASS"
    expired = verify_snapshot(snap, crypto, public.read_bytes(), now=expires)
    assert expired["status"] == "UNKNOWN"
    assert expired["reasons"] == ["snapshot expired"]
    future = verify_snapshot(snap, crypto, public.read_bytes(), now=issued - timedelta(seconds=1))
    assert future["status"] == "FAIL"
    assert future["reasons"] == ["issued_at in future"]


def test_snapshot_wrong_key_and_malformed_signature(keypair, tmp_path):
    crypto, private, public = keypair
    snap = sign_snapshot({"sequence": 1}, crypto, str(private), "state-pass")
    other_private, other_public = tmp_path / "other.key", tmp_path / "other.pub"
    crypto.generate_key(other_private, other_public, "other-pass")
    assert verify_snapshot(snap, crypto, other_public.read_bytes())["reasons"] == ["signature invalid"]
    snap["signature"] = "not-base64!"
    assert verify_snapshot(snap, crypto, public.read_bytes())["reasons"] == ["invalid signature encoding"]
