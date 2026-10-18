from __future__ import annotations


def test_v2_api_public_result_drops_binary_payload():
    from gm_provenance.v2_api import _public_result
    result = _public_result({"content_id": "cid", "media_bytes_value": b"private-bytes"})
    assert result == {"content_id": "cid"}
