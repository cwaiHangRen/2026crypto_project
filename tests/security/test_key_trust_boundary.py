from pathlib import Path
import pytest
from PIL import Image
from gm_provenance.service import ProvenanceService


def test_keygen_does_not_create_trust_and_register_rejects(tmp_path: Path):
    src = tmp_path / "x.png"; Image.new("RGB", (32, 32), "white").save(src)
    svc = ProvenanceService(tmp_path / "db")
    private, public = tmp_path / "k.pem", tmp_path / "k.pub"
    svc.keygen(private, public, "pass-123")
    assert svc.registry.list_contents() == []
    assert svc.registry.get_key(svc.crypto.key_id(public.read_bytes())) is None
    with pytest.raises(PermissionError):
        svc.register(src, private, "pass-123")
    assert svc.registry.list_contents() == []


def test_explicit_trust_and_ownership_are_required(tmp_path: Path):
    svc = ProvenanceService(tmp_path / "db")
    private, public = tmp_path / "k.pem", tmp_path / "k.pub"
    svc.keygen(private, public, "pass-123")
    key_id = svc.trust_key(public, "publisher-a")
    assert svc.registry.get_key(key_id)["trusted"] is True
    with pytest.raises(PermissionError):
        svc.trust_key(public, "publisher-b")


def test_derive_quality_bounds_reject_before_side_effects(tmp_path: Path):
    svc = ProvenanceService(tmp_path / "db")
    private, public = tmp_path / "k.pem", tmp_path / "k.pub"
    svc.keygen(private, public, "pass-123"); svc.trust_key(public, "publisher-demo-001")
    with pytest.raises(ValueError):
        svc.derive("0" * 32, private, "pass-123", "resize", 101)


@pytest.mark.parametrize("quality", [1, 100])
def test_derive_quality_integer_boundaries_are_accepted(tmp_path: Path, quality: int):
    src = tmp_path / "x.png"; Image.new("RGB", (768, 768), "white").save(src)
    svc = ProvenanceService(tmp_path / "db")
    private, public = tmp_path / "k.pem", tmp_path / "k.pub"
    svc.keygen(private, public, "pass-123"); svc.trust_key(public, "publisher-demo-001")
    root = svc.register(src, private, "pass-123")
    child = svc.derive(root["content_id"], private, "pass-123", "resize", quality)
    assert child["version"] == 2


@pytest.mark.parametrize("quality", [0, 101, 75.5, True, None, "75"])
def test_derive_quality_rejects_invalid_types_and_ranges(tmp_path: Path, quality):
    svc = ProvenanceService(tmp_path / "db")
    private, public = tmp_path / "k.pem", tmp_path / "k.pub"
    svc.keygen(private, public, "pass-123"); svc.trust_key(public, "publisher-demo-001")
    before = svc.registry.status()
    with pytest.raises(ValueError):
        svc.derive("0" * 32, private, "pass-123", "resize", quality)
    after = svc.registry.status()
    assert after["sequence"] == before["sequence"]
