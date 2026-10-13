import json
from pathlib import Path

from gm_provenance.benchmark import GROUPS, load_attack_config, run_benchmark, _bytes_image, _fixture_bytes, _transform_cases, _transform_outcome


def test_small_benchmark_writes_four_groups_and_separates_truth(tmp_path: Path):
    output = tmp_path / "benchmark"
    summary = run_benchmark(output, sample_count=1)
    assert summary["status"] == "LOCAL_SMOKE"
    assert summary["groups"] == list(GROUPS)
    assert summary["dataset"]["documents"] == "N/A"
    assert "95%" in summary["targets"]["content_id_accuracy"]["target"]
    rows = [json.loads(line) for line in (output / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["group"] for row in rows} == set(GROUPS)
    assert (output / "summary.json").exists()
    assert (output / "BENCHMARK.md").exists()
    # The verifier receives only a manifest-presence flag; evaluator truth is retained separately.
    assert all("truth" in row and "expected_content_id" in row["truth"] for row in rows)
    assert all("truth" not in row["detector_input"] for row in rows)
    assert any(row["simulated"] for row in rows)
    geometry = [row for row in rows if row["transform"].startswith(("rotate_", "mirror_rotate_"))]
    assert len(geometry) == 7 * len(GROUPS)
    assert all(row["handled"] for row in geometry)
    for row in geometry:
        if row["group"] in ("G2", "G3"):
            assert row["observed"]["recovered_content_ids"] == [row["truth"]["expected_content_id"]]


def test_recovery_benchmark_does_not_accept_a_wrong_or_extra_id():
    expected = "0123456789abcdef0123456789abcdef"
    for group in ("G2", "G3"):
        for ids in ([], ["fedcba9876543210fedcba9876543210"], [expected, "fedcba9876543210fedcba9876543210"]):
            observed = {"conclusion": "ORIGIN_HINT_ONLY", "watermark_status": "FOUND", "recovered_content_ids": ids}
            outcome, handled = _transform_outcome(group, "rotate", observed, expected)
            assert outcome == "ORIGIN_HINT_ONLY"
            assert not handled


def test_geometry_config_is_read_without_pyyaml(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)
    config = tmp_path / "attacks.yaml"
    config.write_text("images:\n  rotation_degrees: [180]\n  mirror_rotation_degrees: [0, 270]\n", encoding="utf-8")
    cases = _transform_cases(load_attack_config(config))
    geometry = [case["name"] for case in cases if case["operation"] in ("rotate", "mirror_rotate")]
    assert geometry == ["rotate_180", "mirror_rotate_0", "mirror_rotate_270"]


def test_unchanged_metadata_clear_is_exact_but_still_requires_correct_recovery_id():
    expected = "0123456789abcdef0123456789abcdef"
    observed = {"conclusion": "VERIFIED_EXACT", "watermark_status": "FOUND", "recovered_content_ids": [expected]}
    for group in ("G1", "G3"):
        outcome, handled = _transform_outcome(group, "metadata_clear", observed, expected, bytes_changed=False)
        assert outcome == "VERIFIED_EXACT"
        assert handled
    observed["recovered_content_ids"] = ["fedcba9876543210fedcba9876543210"]
    assert not _transform_outcome("G3", "metadata_clear", observed, expected, bytes_changed=False)[1]


def test_decimal_rotation_config_and_four_group_recovery(tmp_path):
    config = tmp_path / "decimal-attacks.yaml"
    config.write_text("images:\n  jpeg_reencode: []\n  resize: []\n  crop_keep_area: []\n  rotation_degrees: [37.5]\n  mirror_rotation_degrees: [213.25]\n  metadata_clear: false\n", encoding="utf-8")
    loaded = load_attack_config(config)
    geometry = [case for case in _transform_cases(loaded) if case["operation"] in ("rotate", "mirror_rotate")]
    assert [case["value"] for case in geometry] == [37.5, 213.25]
    output = tmp_path / "decimal-benchmark"
    run_benchmark(output, config, sample_count=1)
    rows = [json.loads(line) for line in (output / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
    rotated = [row for row in rows if row["transform"] in {"rotate_37.5", "mirror_rotate_213.25"}]
    assert len(rotated) == 2 * len(GROUPS)
    assert all(row["handled"] for row in rotated)
    assert all(row["detector_input"] == {"manifest_supplied": row["group"] == "G1"} for row in rotated)
    assert all(row["truth"]["bytes_changed"] for row in rotated)
    for row in rotated:
        if row["group"] in ("G2", "G3"):
            assert row["observed"]["recovered_content_ids"] == [row["truth"]["expected_content_id"]]
            assert row["observed"]["conclusion"] == "ORIGIN_HINT_ONLY"


def test_benchmark_rotation_rejects_nonfinite_and_boolean_angles():
    import pytest
    source = _fixture_bytes(0)
    for operation in ("rotate", "mirror_rotate"):
        for value in (True, False, float("inf"), float("-inf"), float("nan"), "37.5", None):
            with pytest.raises(ValueError, match="有限数值"):
                _bytes_image(source, operation, value)
