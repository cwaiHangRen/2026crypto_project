import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("round5_runner", ROOT / "scripts" / "run_round5_watermark_eval.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_round4_content_id_rule_is_stable():
    assert MODULE.deterministic_cid(20261007, "img-synth-001", 0) == MODULE.deterministic_cid(20261007, "img-synth-001", 0)
    assert MODULE.deterministic_cid(20261007, "img-synth-001", 0) != MODULE.deterministic_cid(20261007, "img-synth-001", 1)


def test_strict_recovery_rejects_missing_or_conflict():
    assert MODULE.strict_recovery({"status": "FOUND", "content_ids": ["a" * 32]}, "a" * 32)["found_expected"]
    assert not MODULE.strict_recovery({"status": "FOUND", "content_ids": []}, "a" * 32)["found_expected"]
    assert not MODULE.strict_recovery({"status": "CONFLICT", "content_ids": ["a" * 32, "b" * 32]}, "a" * 32)["unique"]


def test_crop_geometry_uses_area_keep_and_fixed_oracle_fill():
    from PIL import Image
    import io

    source = io.BytesIO()
    Image.new("RGB", (100, 80), (10, 20, 30)).save(source, "PNG")
    cropped, geometry = MODULE.crop_with_geometry(source.getvalue(), 0.95)
    assert geometry["output_size"] == [round(100 * (0.95 ** 0.5)), round(80 * (0.95 ** 0.5))]
    restored = MODULE.oracle_geometry_fill(cropped, geometry)
    with Image.open(io.BytesIO(restored)) as image:
        assert image.size == (100, 80)
        assert image.getpixel((0, 0)) == (128, 128, 128)
