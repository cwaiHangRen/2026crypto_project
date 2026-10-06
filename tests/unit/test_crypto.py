from pathlib import Path
from gm_provenance.crypto import OpenSSLCrypto


def test_sm3_known_answer_and_sm2_roundtrip(tmp_path: Path):
    c = OpenSSLCrypto(); assert c.sm3(b"abc") == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    key_id = c.generate_key(private, public, "unit-pass-123")
    assert key_id == c.key_id(public.read_bytes())
    sig = c.sign(b"protocol-message", private, "unit-pass-123")
    assert c.verify(b"protocol-message", sig, public.read_bytes())
    assert not c.verify(b"modified-message", sig, public.read_bytes())


def test_wrong_user_id_rejected(tmp_path: Path):
    c = OpenSSLCrypto(); private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    c.generate_key(private, public, "unit-pass-123"); sig = c.sign(b"x", private, "unit-pass-123", "user-a")
    assert not c.verify(b"x", sig, public.read_bytes(), "user-b")
