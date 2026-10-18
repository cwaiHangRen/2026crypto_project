import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from gm_provenance import media
from gm_provenance.development_eval import evaluate_sample, load_config


def test_development_eval_real_groups_and_truth_is_separate(tmp_path: Path):
    source = tmp_path / "input.png"
    y, x = np.indices((768, 1024))
    array = np.stack([(x * 3 + y) % 256, (y * 5 + 2 * x) % 256, (x + 2 * y) % 256], axis=-1).astype("uint8")
    Image.fromarray(array, "RGB").save(source, "PNG")
    result = evaluate_sample({"sample_id": "dev-01", "source_family_id": "f-01", "local_path": str(source)}, tmp_path / "run", {"images": {"jpeg_quality": [90], "resize": [], "crop_keep": [], "metadata_clear": False, "format_convert": False, "screenshot_simulated": False}})
    assert set(result) == {"samples", "attacks", "timings", "failures", "storage", "metadata"}
    assert {row["group"] for row in result["samples"]} == {"G0", "G1", "G2", "G3"}
    assert all("truth" in row and "expected_content_id" in row["truth"] for row in result["samples"])
    assert all("truth" not in row["detector_input"] for row in result["samples"])
    assert result["metadata"]["truth_is_evaluator_only"] is True
    assert all(row["sidecar"] == "not_applicable" for row in result["samples"] if row["group"] in ("G0", "G2"))
    assert {a["attack"] for a in result["attacks"]} >= {"signature_tamper", "untrusted_key", "copied_watermark_unrelated", "state_expired", "state_mismatch"}
    sample_dir = tmp_path / "run" / "samples" / "dev-01"
    g1_manifest = next((sample_dir / "g1-registry" / "manifests").glob("*.json"))
    g3_manifest = next((sample_dir / "registry" / "manifests").glob("*.json"))
    assert json.loads(g1_manifest.read_text(encoding="utf-8"))["body"]["watermark_profile"]["algorithm"] == "none"
    assert json.loads(g3_manifest.read_text(encoding="utf-8"))["body"]["watermark_profile"]["algorithm"] == "dct-difference"


def test_round9_g3_gate_failure_does_not_create_g1_artifact(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.png"
    Image.new("RGB", (768, 768), (90, 120, 160)).save(source, "PNG")

    def reject(*_args, **_kwargs):
        raise media.WatermarkSelfCheckError("injected gate failure", {"stage": "test"})

    monkeypatch.setattr(media, "assert_publishable", reject)
    result = evaluate_sample(
        {"sample_id": "g3-fail", "local_path": str(source)},
        tmp_path / "round9",
        {
            "groups": ["G0", "G3"],
            "images": {"jpeg_quality": [], "resize": [], "crop_keep": [], "metadata_clear": False, "format_convert": False, "screenshot_simulated": False},
            "scale_evaluation": {"attacks_enabled": False, "derive_enabled": False, "quality_enabled": False},
        },
    )
    assert result["failures"] and result["failures"][0]["stage"] == "publish"
    sample_dir = tmp_path / "round9" / "samples" / "g3-fail"
    assert not (sample_dir / "g1-registry" / "manifests").exists()
    assert not list((sample_dir / "registry" / "manifests").glob("*.json"))


def test_config_has_fixed_matrix_and_performance_section():
    cfg = load_config(Path(__file__).parents[2] / "configs" / "round3-evaluation.json")
    assert cfg["groups"] == ["G0", "G1", "G2", "G3"]
    assert cfg["images"]["jpeg_quality"] == [90, 75, 50]
    assert cfg["pdf"]["screenshot_simulated"] is True
    assert cfg["performance"]["repeats"] >= 1


def test_development_eval_state_full_and_legal_replacement(tmp_path: Path):
    y, x = np.indices((512, 512))
    array = np.stack([(x * 3 + y) % 256, (y * 5 + 2 * x) % 256, (x + 2 * y) % 256], axis=-1).astype("uint8")
    source = tmp_path / "textured.png"
    Image.fromarray(array, "RGB").save(source)
    result = evaluate_sample(
        {"sample_id": "state-01", "local_path": str(source)},
        tmp_path / "run",
        {"images": {"jpeg_quality": [], "resize": [], "crop_keep": [], "metadata_clear": False, "format_convert": False, "screenshot_simulated": False}},
    )
    full = next(row for row in result["samples"] if row["group"] == "G3" and row["transform"] == "exact" and row["detector_input"]["mode"] == "full")
    hard = next(row for row in result["samples"] if row["group"] == "G3" and row["transform"] == "exact" and row["detector_input"]["mode"] == "hard")
    lost = next(row for row in result["samples"] if row["group"] == "G3" and row["transform"] == "exact" and row["sidecar"] == "lost")
    assert full["observed"]["conclusion"] == "VERIFIED_EXACT"
    assert hard["observed"]["conclusion"] == "VERIFIED_BINDING_ONLY" and hard["handled"]
    assert lost["observed"]["conclusion"] == "VERIFIED_BINDING_ONLY"
    replacement = next(row for row in result["attacks"] if row["attack"] == "content_replacement")
    assert replacement["observed"]["content_hash_match"] == "FAIL" and replacement["handled"]
    assert not (tmp_path / "run" / "samples" / "state-01" / "state-private.pem").exists()
