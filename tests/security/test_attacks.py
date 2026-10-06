import json
from pathlib import Path
from PIL import Image
from gm_provenance.service import ProvenanceService


def test_manifest_tamper_is_rejected_and_reencode_is_origin_hint(tmp_path: Path):
    source = tmp_path / "source.png"; Image.new("RGB", (768, 768), "white").save(source)
    svc = ProvenanceService(tmp_path / "data"); private, public = tmp_path / "k", tmp_path / "p"; svc.keygen(private, public, "attack-pass-123")
    published = svc.register(source, private, "attack-pass-123")
    obj = json.loads(Path(published["manifest_path"]).read_text(encoding="utf-8")); obj["body"]["publisher_id"] = "attacker"
    tampered = tmp_path / "tampered.json"; tampered.write_text(json.dumps(obj), encoding="utf-8")
    assert svc.verify(published["media_path"], tampered)["conclusion"] == "REJECTED"
    transformed = tmp_path / "transformed.jpg"; svc.transform(published["media_path"], transformed, "jpeg-reencode", 50)
    recovered = svc.verify(transformed, None, "recover")
    assert recovered["conclusion"] == "ORIGIN_HINT_ONLY"
