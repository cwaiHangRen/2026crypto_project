import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("round6_runner", ROOT / "scripts" / "run_round6_sync_eval.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_config_has_unseen_challenges_and_noncrop_matrix():
    cfg = MODULE.load_config(ROOT / "configs/round6-sync-evaluation.json")
    names = {x["name"] for x in cfg["transforms"]}
    assert {"crop_nongrid_area_0.943", "crop_nongrid_area_0.957", "crop_nongrid_area_0.981"} <= names
    assert {"crop_offset_h_pos", "crop_offset_v_neg", "crop_resize_jpeg_0.957"} <= names
    assert {"exact", "jpeg_q90", "jpeg_q75", "jpeg_q50", "resize_0.75", "resize_jpeg_0.75_q75", "metadata_clear", "format_convert", "screenshot_simulated"} <= names
    assert cfg["decoder"]["max_candidates"] == 128
    assert cfg["decoder"]["crop_area_ratios"][:4] == [1.0, 0.98, 0.96, 0.95]
    assert cfg["decoder"]["offset_fractions"] == [0.0, -0.03, 0.03]


def test_crop_geometry_area_offset_and_out_of_range():
    from PIL import Image
    import io
    source = io.BytesIO(); Image.new("RGB", (100, 80), (10, 20, 30)).save(source, "PNG")
    cropped, geom = MODULE.transform(source.getvalue(), {"operation": "crop", "keep": 0.95, "offset": [0.02, 0.0]})
    assert geom["output_size"] == [round(100 * (0.95 ** 0.5)), round(80 * (0.95 ** 0.5))]
    assert geom["offset_fraction"] == [0.02, 0.0]
    with Image.open(io.BytesIO(cropped)) as image:
        assert image.size == tuple(geom["output_size"])
    try:
        MODULE.transform(source.getvalue(), {"operation": "crop", "keep": 1.1, "offset": [0.0, 0.0]})
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range crop must be retained as a transform error")


def test_strict_recovery_does_not_need_expected_id():
    found = MODULE.strict_recovery({"status": "FOUND", "content_ids": ["a" * 32]}, "a" * 32)
    assert found["unique"] and found["found_expected"] and not found["wrong_id"]
    wrong = MODULE.strict_recovery({"status": "FOUND", "content_ids": ["b" * 32]}, "a" * 32)
    assert wrong["wrong_ids"] == ["b" * 32] and wrong["wrong_id"]
    conflict = MODULE.strict_recovery({"status": "CONFLICT", "content_ids": ["a" * 32, "b" * 32]}, "a" * 32)
    assert conflict["conflict"] and conflict["wrong_id"]


def test_summary_keeps_crypto_acceptance_separate():
    row = _row("summary-key", sync_status="FOUND", sync_ids=["a" * 32], pair_cell="d0_failed_sync_success")
    summary = MODULE._summary([row], [])
    assert summary["pairing_four_cells"]["d0_failed_sync_success"] == 1
    assert summary["crypto_acceptance"]["status"] == "NOT_CHECKED"


def _row(key, *, oracle_status="FOUND", oracle_ids=None, sync_status="FOUND", sync_ids=None, pair_cell="both_success"):
    expected = "a" * 32
    oracle = None if oracle_status is None else {"observed": {"status": oracle_status, "content_ids": oracle_ids or []}}
    sync = {"status": sync_status, "content_ids": sync_ids or []}
    d0 = {"status": "NOT_FOUND", "content_ids": []}
    return {"role": "paired", "key": key, "expected_content_id": expected, "pair_cell": pair_cell,
            "oracle": oracle, "sync": {"observed": sync, "recovery": MODULE.strict_recovery(sync, expected)},
            "d0": {"observed": d0, "recovery": MODULE.strict_recovery(d0, expected)}}


def test_summary_oracle_gap_only_counts_expected_hit_without_sync_single_success():
    expected = "a" * 32
    success = _row("k-success", oracle_ids=[expected], sync_ids=[expected], pair_cell="d0_failed_sync_success")
    failed = _row("k-failed", oracle_ids=[expected], sync_status="TIME_BUDGET_EXHAUSTED", sync_ids=[], pair_cell="both_failed")
    oracle_wrong = _row("k-oracle-wrong", oracle_ids=["b" * 32], sync_status="TIME_BUDGET_EXHAUSTED", pair_cell="both_failed")
    oracle_na = _row("k-oracle-na", oracle_status=None, sync_status="TIME_BUDGET_EXHAUSTED", pair_cell="both_failed")
    summary = MODULE._summary([success, failed, oracle_wrong, oracle_na], [])
    assert summary["oracle_success_sync_failed"] == 1
    assert summary["oracle_success_sync_failed_case_ids"] == ["k-failed"]


def test_summary_deduplicates_task_keys_and_reports_conflicts():
    row = _row("same", oracle_status=None, sync_status="NOT_FOUND", pair_cell="both_failed")
    duplicate = dict(row)
    duplicate["pair_cell"] = "d0_failed_sync_success"
    summary = MODULE._summary([row, duplicate], [])
    assert summary["paired_rows"] == 0
    assert summary["conflicting_duplicate_task_keys"] == {"same": 2}


def test_strict_recovery_distinguishes_expected_hit_from_single_output():
    expected = "a" * 32
    observed = {"status": "CONFLICT", "content_ids": [expected, "b" * 32]}
    recovered = MODULE.strict_recovery(observed, expected)
    assert recovered["expected_id_hit"] is True
    assert recovered["correct_single_output"] is False
    assert recovered["conflict"] is True
    assert recovered["wrong_id"] is True


def test_resume_index_reads_incremental_jsonl(tmp_path):
    path = tmp_path / "paired.jsonl"
    MODULE.append_jsonl(path, {"key": "k1", "role": "paired"})
    rows, keys = MODULE._load_rows(path)
    assert rows[0]["key"] == "k1" and keys == {"k1"}
