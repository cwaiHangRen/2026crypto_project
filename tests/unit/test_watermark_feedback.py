import io

import numpy as np
import pytest
from PIL import Image

from gm_provenance import watermark as wm
from gm_provenance.watermark import detect_image
from gm_provenance.watermark_feedback import (
    PROFILE,
    WatermarkFeedbackError,
    diagnose_image_feedback,
    embed_image_feedback,
)


CID = "0123456789abcdef0123456789abcdef"


def _raw() -> bytes:
    y, x = np.indices((640, 768))
    arr = np.empty((640, 768, 3), dtype=np.uint8)
    arr[..., 0] = (x * 3 + y) % 256
    arr[..., 1] = (y * 2 + 17) % 256
    arr[..., 2] = ((x + y) * 2) % 256
    stream = io.BytesIO()
    Image.fromarray(arr, "RGB").save(stream, "PNG")
    return stream.getvalue()


def test_feedback_success_uses_shared_blind_detector_and_public_budget():
    marked, profile = embed_image_feedback(_raw(), CID, "PNG")
    assert profile["embed_profile"] == PROFILE
    assert profile["payload_version"] == profile["decoder_version"] == 1
    assert profile["strength"] == 34.0
    assert profile["feedback"]["max_rounds"] == 3
    assert profile["feedback"]["jpeg_reencoding_accumulation"] is False
    assert detect_image(marked)["content_ids"] == [CID]
    # The public profile has compact per-round summaries, not coefficient dumps.
    assert len(profile["feedback"]["rounds"]) <= 4
    assert all("coefficients" not in row for row in profile["feedback"]["rounds"])


def test_feedback_diagnostic_contains_signed_margins_positions_and_ecc():
    result = diagnose_image_feedback(_raw(), CID, "PNG")
    assert result["embed_profile"] == PROFILE
    assert result["target_payload"]["codeword_bytes"] == 53
    assert result["rounds"]
    final_round = result["rounds"][-1]
    assert len(final_round["coefficients"]) == 424 * 4
    entry = final_round["coefficients"][0]
    assert {"target_bit", "position", "observed_difference", "signed_margin"} <= set(entry)
    assert "failure_positions" in final_round["ecc"]
    assert "byte_error_positions" in final_round["ecc"]
    assert final_round["ecc"]["ecc_matches_expected"] is True
    assert result["budget_failure"] is None


def test_feedback_failure_preserves_diagnostics_and_final_bytes(monkeypatch):
    def always_missing(_data):
        return {"status": "NOT_FOUND", "content_ids": [], "details": {"injected": True}}

    monkeypatch.setattr(wm, "detect_image", always_missing)
    with pytest.raises(WatermarkFeedbackError) as raised:
        embed_image_feedback(_raw(), CID, "PNG")
    exc = raised.value
    assert exc.final_bytes.startswith(b"\x89PNG")
    assert exc.diagnostics["embed_profile"] == PROFILE
    assert len(exc.diagnostics["rounds"]) <= 4
    assert exc.diagnostics["final"]["failure_positions"] is not None


def test_feedback_rejects_invalid_content_id_before_encoding():
    with pytest.raises(ValueError, match="32 位小写十六进制"):
        embed_image_feedback(_raw(), "not-a-content-id")
