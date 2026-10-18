from pathlib import Path

import pytest

from gm_provenance.crypto import CryptoEnvironmentError, OpenSSLCrypto


ABC = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"


def test_hashlib_backend_kat_and_chunked_differential():
    crypto = OpenSSLCrypto(sm3_backend="hashlib")
    assert crypto.sm3_backend == "hashlib"
    assert crypto.sm3(b"abc") == ABC
    payload = bytes(range(256)) * 4 + b"round5"
    assert crypto._hashlib_sm3(payload) == crypto._cli_sm3(payload)


def test_cli_backend_is_explicit_and_unchanged():
    crypto = OpenSSLCrypto(sm3_backend="cli")
    assert crypto.sm3_backend == "cli"
    assert crypto.sm3(b"abc") == ABC


def test_invalid_backend_rejected():
    with pytest.raises(ValueError):
        OpenSSLCrypto(sm3_backend="sha256")
