import json
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=spec_from_file_location("round7_sets", ROOT/"scripts"/"round7_sets.py")
d=module_from_spec(spec); spec.loader.exec_module(d)

def test_task_id_and_target_selection():
    assert d.task_id("abcdef0123456789", "paired|a") == "r7:abcdef0123456789:paired|a"
    assert d.select_targets(["paired|a|v1|exact","paired|b|v2|jpeg"], "paired|a") == ["paired|a|v1|exact"]

def test_audit_baseline_dynamic(tmp_path):
    p=tmp_path/"summary.json"; p.write_text(json.dumps({"paired_rows":4654,"pairing_four_cells":{"both_success":1441,"d0_failed_sync_success":1482,"d0_success_sync_failed":0,"both_failed":1731},"oracle_success_sync_failed":882}),encoding="utf-8")
    a=d.audit_baseline(p)
    assert a["attribution_total"] == 3213
    assert a["baseline_contract"]["both_success"]

def test_candidate_config_uses_v3():
    c=d.candidate_config({"decoder":{},"profiles":{"v1":"watermark-v1"}})
    assert c["decoder"]["profile"] == "decoder-v3-sync"

def test_derive_sets_uses_task_rows_and_separates_oracle_gap(tmp_path):
    rows = [
        {"key": "a", "task_id": "ta", "sample_id": "s", "profile": "v1", "transform": "exact", "d0": {"observed": {"status": "FOUND", "content_ids": ["x"]}}, "sync": {"observed": {"status": "FOUND", "content_ids": ["x"]}}, "oracle": {"observed": {"status": "FOUND", "content_ids": ["x"]}} , "expected_content_id": "x"},
        {"key": "b", "task_id": "tb", "sample_id": "s", "profile": "v1", "transform": "crop", "d0": {"observed": {"status": "NOT_FOUND", "content_ids": []}}, "sync": {"observed": {"status": "NOT_FOUND", "content_ids": []}}, "oracle": {"observed": {"status": "FOUND", "content_ids": ["x"]}} , "expected_content_id": "x"},
        {"key": "c", "task_id": "tc", "sample_id": "s", "profile": "v1", "transform": "bad", "transform_error": True, "d0": {"observed": {"status": "ERROR", "content_ids": []}}, "sync": {"observed": {"status": "ERROR", "content_ids": []}}, "oracle": None, "expected_content_id": "x"},
    ]
    p = tmp_path / "paired.jsonl"; p.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    result = d.derive_sets(p)
    assert result["counts"]["d0_success"] == 1
    assert result["counts"]["dsync_not_success"] == 2
    assert result["counts"]["oracle_success_sync_failed"] == 1
    assert result["counts"]["input_or_execution_error"] == 1
