from __future__ import annotations


def test_v2_cli_exposes_lifecycle_and_batch_commands():
    from gm_provenance.v2_cli import build_parser
    parser = build_parser()
    assert parser.parse_args(["key-status"]).command == "key-status"
    assert parser.parse_args(["batch-proof", "batch", "a" * 64]).command == "batch-proof"


def test_v2_api_imports_without_changing_v1_app():
    from gm_provenance import api, v2_api
    assert api.app is not None
    assert v2_api.app is not None
