import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from diagnose_round7_geometry import diagnose, load_rows

def test_diagnose_counts_unique_capacity_and_geometry(tmp_path):
    rows=[
      {"key":"a","role":"paired","transform_range":"in_scope","transform":"crop_center","pair_cell":"d0_success_sync_failed","sync":{"observed":{"status":"FAILED","geometry_hypotheses":[{"geometry":"native-work-domain"}]}}},
      {"key":"a","role":"paired","transform_range":"in_scope","transform":"crop_center","pair_cell":"d0_success_sync_failed","sync":{"observed":{"status":"FAILED"}}},
      {"key":"b","role":"paired","transform_range":"out_of_scope","transform":"crop_offset","pair_cell":"both_success","sync":{"observed":{"status":"FOUND"}},"sync_diagnostics":{"geometry_hypotheses":[{"geometry":"offset-grid"}]}},
    ]
    p=tmp_path/"paired.jsonl"; p.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
    result=diagnose(load_rows(p),target=3)
    assert result["unique_task_keys"]==2
    assert result["capacity_gap"]==1
    assert result["transform_range_counts"]=={"in_scope":2,"out_of_scope":1}
    assert result["geometry_hypothesis_counts"]=={"native-work-domain":1,"offset-grid":1}
    assert result["sync_failure_rows"]==2

def test_empty_input_reports_full_gap():
    result=diagnose([],target=882)
    assert result["capacity_gap"]==882
    assert result["target_reached"] is False
