import io
from pathlib import Path
from PIL import Image
from gm_provenance.service import ProvenanceService


def test_publish_verify_and_recover(tmp_path: Path):
    image = Image.new("RGB", (768, 768), "white"); source = tmp_path / "source.png"; image.save(source)
    service = ProvenanceService(tmp_path / "data"); private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    service.keygen(private, public, "service-pass-123")
    published = service.register(source, private, "service-pass-123")
    exact = service.verify(published["media_path"], published["manifest_path"], "hard")
    full = service.verify(published["media_path"], published["manifest_path"], "full")
    recover = service.verify(published["media_path"], None, "recover")
    assert exact["conclusion"] == "VERIFIED_EXACT"; assert recover["recovered_content_ids"] == [published["content_id"]]
    assert full["state_freshness"] == "UNKNOWN"; assert full["conclusion"] == "INDETERMINATE"
