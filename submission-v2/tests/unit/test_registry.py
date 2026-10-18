from pathlib import Path

import pytest

from gm_provenance.crypto import OpenSSLCrypto
from gm_provenance.registry import Registry


@pytest.fixture()
def crypto():
    try:
        return OpenSSLCrypto()
    except Exception as exc:
        pytest.skip(f"OpenSSL unavailable: {exc}")


def test_publish_parent_history_and_media(tmp_path: Path, crypto):
    reg = Registry(tmp_path / "registry")
    private, public = tmp_path / "key.pem", tmp_path / "pub.pem"
    crypto.generate_key(private, public, "test-pass")
    reg.add_key(public.read_bytes(), "publisher-demo-001", True, crypto)
    first = {"body": {"content_id": "1" * 32, "asset_id": "asset-a", "version": 1,
                       "publisher_id": "publisher-demo-001", "parent_content_id": None,
                       "parent_manifest_hash": None}, "signature": b"sig"}
    reg.publish(first, b"one", "2" * 64)
    second = {"body": {"content_id": "3" * 32, "asset_id": "asset-a", "version": 2,
                        "publisher_id": "publisher-demo-001", "parent_content_id": "1" * 32,
                        "parent_manifest_hash": "2" * 64}, "signature": b"sig2"}
    reg.publish(second, b"two", "4" * 64)
    assert reg.current("asset-a") == "3" * 32
    assert [x["body"]["content_id"] for x in reg.history("3" * 32)] == ["3" * 32, "1" * 32]
    assert reg.read_media("3" * 32) == b"two"


def test_publish_rejects_parent_fork(tmp_path: Path):
    reg = Registry(tmp_path / "registry")
    root = {"body": {"content_id": "a" * 32, "asset_id": "asset", "version": 1,
                      "publisher_id": "pub", "parent_content_id": None, "parent_manifest_hash": None}}
    reg.publish(root, b"x", "b" * 64)
    child = {"body": {"content_id": "c" * 32, "asset_id": "asset", "version": 3,
                       "publisher_id": "pub", "parent_content_id": "a" * 32,
                       "parent_manifest_hash": "b" * 64}}
    with pytest.raises(ValueError):
        reg.publish(child, b"y", "d" * 64)
