from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


_PATH = Path(__file__).parents[2] / "scripts" / "reclassify_invalid_transforms.py"
_SPEC = spec_from_file_location("reclassify_invalid_transforms", _PATH)
_MODULE = module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


def test_execution_result_keeps_timeout_separate_from_pair_cell():
    row = {
        "pair_cell": "both_failed",
        "d0": {"observed": {"status": "NOT_FOUND"}},
        "sync": {"observed": {"status": "TIME_BUDGET_EXHAUSTED"}},
    }
    assert _MODULE.execution_result(row, "resource_exhausted") == "timeout"


def test_invalid_transform_is_its_own_result():
    row = {"pair_cell": "both_failed"}
    assert _MODULE.execution_result(row, "invalid_transform") == "invalid_transform"
