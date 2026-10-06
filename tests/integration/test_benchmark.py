import json
from pathlib import Path

from gm_provenance.benchmark import GROUPS, run_benchmark


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
