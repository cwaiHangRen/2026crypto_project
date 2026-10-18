from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.key_manager import EXPIRED, REVOKED, KeyManager


def _utc(days: int = 0) -> str:
    value = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=days)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_keygen_status_rotation_revoke_and_secure_storage(tmp_path: Path):
    crypto = OpenSSLCrypto(); manager = KeyManager(tmp_path / "keys", crypto=crypto)
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    first = manager.keygen(private, public, "manager-pass", "publisher-v2")
    assert first["status"] == "active" and first["private_path"] == str(private.resolve())
    assert not (tmp_path / "keys" / "private.pem").exists()
    second_private, second_public = tmp_path / "private2.pem", tmp_path / "public2.pem"
    second = manager.rotate(first["key_id"], second_private, second_public, "manager-pass")
    assert second["predecessor_key_id"] == first["key_id"]
    assert manager.active("publisher-v2")["key_id"] == second["key_id"]
    revoked = manager.revoke(first["key_id"], "retired after rotation")
    assert revoked["status"] == REVOKED
    assert manager.is_usable(second["key_id"])
    assert not manager.is_usable(first["key_id"])


def test_expired_and_wrong_key_handling(tmp_path: Path):
    crypto = OpenSSLCrypto(); manager = KeyManager(tmp_path / "keys", crypto=crypto)
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    record = manager.keygen(private, public, "manager-pass", "publisher-v2", valid_from=_utc(-1), expires_at=_utc(0))
    assert manager.get(record["key_id"])["status"] == EXPIRED
    assert not manager.is_usable(record["key_id"])
    assert manager.get("f" * 64) is None


def test_sign_and_verify_respect_revocation(tmp_path: Path):
    crypto = OpenSSLCrypto(); manager = KeyManager(tmp_path / "keys", crypto=crypto)
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    record = manager.keygen(private, public, "manager-pass", "publisher-v2")
    signature = manager.sign(record["key_id"], b"v2-key-message", passphrase="manager-pass")
    assert manager.verify(record["key_id"], b"v2-key-message", signature)
    manager.revoke(record["key_id"], "compromised")
    assert manager.verify(record["key_id"], b"v2-key-message", signature)
    assert not manager.verify(record["key_id"], b"v2-key-message", signature, allow_revoked=False)
    with pytest.raises(Exception):
        manager.sign(record["key_id"], b"v2-key-message", passphrase="manager-pass")

