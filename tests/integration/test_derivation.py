from pathlib import Path

import pytest
from PIL import Image

from gm_provenance.service import ProvenanceService


def _image(path: Path) -> None:
    Image.new("RGB", (768, 768), "white").save(path)


def test_authorized_derivation_builds_parent_chain(tmp_path: Path):
    source = tmp_path / "source.png"
    _image(source)
    service = ProvenanceService(tmp_path / "registry")
    private, public = tmp_path / "publisher.pem", tmp_path / "publisher.pub"
    service.keygen(private, public, "derive-pass")
    root = service.register(source, private, "derive-pass")

    child = service.derive(root["content_id"], private, "derive-pass", "resize")
    assert child["version"] == 2
    assert child["parent_content_id"] == root["content_id"]
    checked = service.verify(child["media_path"], child["manifest_path"], mode="hard")
    assert checked["conclusion"] == "VERIFIED_DERIVATIVE"
    assert checked["parent_chain_status"] == "PASS"
    assert checked["authorization_status"] == "PASS"


def test_derivation_rejects_unauthorized_operation(tmp_path: Path):
    source = tmp_path / "source.png"
    _image(source)
    service = ProvenanceService(tmp_path / "registry")
    private, public = tmp_path / "publisher.pem", tmp_path / "publisher.pub"
    service.keygen(private, public, "derive-pass")
    root = service.register(source, private, "derive-pass")
    with pytest.raises(PermissionError):
        service.derive(root["content_id"], private, "derive-pass", "unsupported")


def test_state_snapshot_drives_revocation_and_latest_status(tmp_path: Path):
    source = tmp_path / "source.png"
    _image(source)
    service = ProvenanceService(tmp_path / "registry")
    private, public = tmp_path / "publisher.pem", tmp_path / "publisher.pub"
    service.keygen(private, public, "derive-pass")
    root = service.register(source, private, "derive-pass")
    state_private, state_public = tmp_path / "state.pem", tmp_path / "state.pub"
    service.crypto.generate_key(state_private, state_public, "state-pass")
    service.registry.revoke("content", root["content_id"], "test")
    snapshot = service.state_snapshot(state_private, "state-pass")
    checked = service.verify(root["media_path"], root["manifest_path"], state_snapshot_path=snapshot["snapshot_path"], state_public_path=snapshot["state_public_path"])
    assert checked["state_freshness"] == "PASS"
    assert checked["content_revocation_status"] == "FAIL"
    assert checked["conclusion"] == "REJECTED"
