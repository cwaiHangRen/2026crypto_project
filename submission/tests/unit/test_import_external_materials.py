import importlib.util
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[2] / "scripts" / "import_external_materials.py"
SPEC = importlib.util.spec_from_file_location("import_external_materials", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_local_import_validates_media_and_keeps_review_pending(tmp_path: Path):
    media_path = tmp_path / "sample.png"
    Image.new("RGB", (32, 24), (12, 34, 56)).save(media_path, "PNG")
    row = {
        "source_id": "local-1",
        "relative_path": "sample.png",
        "source_url": "https://example.invalid/source",
        "license_evidence_url": "https://example.invalid/license",
        "license_status": "review",
        "source_family_id": "family-1",
    }
    result = MODULE._record(row, media_path, {}, {})
    assert result["import_status"] == "validated"
    assert result["input_compatibility"] == "supported"
    assert result["media_type"] == "image/png"
    assert result["source_status"] == "pending"
    assert result["review_status"] == "pending"
    assert result["duplicate_status"] == "new"


def test_local_import_marks_duplicate_hash_without_side_effects(tmp_path: Path):
    media_path = tmp_path / "sample.png"
    Image.new("RGB", (16, 16), (1, 2, 3)).save(media_path, "PNG")
    data = media_path.read_bytes()
    digest = MODULE.hashlib.sha256(data).hexdigest()
    result = MODULE._record({"relative_path": "sample.png"}, media_path, {digest: ["history.json"]}, {})
    assert result["duplicate_status"] == "duplicate"
    assert result["duplicate_references"] == ["history.json"]
