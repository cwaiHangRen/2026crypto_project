import io

import numpy as np
import pytest
from PIL import Image

from gm_provenance import watermark as watermark_module
from gm_provenance.watermark import embed_image
from gm_provenance.watermark_sync import (
    SyncConfig,
    _candidate_gray,
    _inferred_native_geometry,
    _resize_observed,
    decode_work_gray,
    detect_image_sync,
    reconstruct_work_gray,
)


CID = "0123456789abcdef0123456789abcdef"


def sample(size=(768, 768)):
    y, x = np.indices(size)
    arr = np.stack([(x * 3 + y) % 256, (y * 2 + 30) % 256, ((x + y) * 2) % 256], axis=-1).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(arr, "RGB").save(out, "PNG")
    return out.getvalue()


def test_sync_d0_fast_path_and_schema():
    marked, _ = embed_image(sample(), CID, "PNG")
    result = detect_image_sync(marked)
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID]
    details = result["details"]
    assert details["decoder_profile"] == "decoder-v2-sync"
    assert details["search_complete"] is False
    assert details["stop_reason"] == "d0_found"
    assert details["geometry_hypotheses"][0]["source"] == "d0"


def test_sync_requires_bytes_and_reports_budget():
    result = detect_image_sync(bytearray(sample()))
    assert result["status"] == "ERROR"
    assert result["details"]["stop_reason"] == "input_type"

    result = detect_image_sync(sample(), SyncConfig(max_candidates=1, max_search_ms=10000))
    assert result["status"] == "CANDIDATE_BUDGET_EXHAUSTED"
    assert result["details"]["search_complete"] is False
    assert result["details"]["stop_reason"] == "candidate_budget_exhausted"

    result = detect_image_sync(sample(), SyncConfig(max_search_ms=1, max_candidates=128))
    assert result["details"]["search_complete"] is False
    assert result["details"]["stop_reason"] == "time_budget_exhausted"


def test_sync_rejects_nonfinite_public_grid_values():
    with pytest.raises(ValueError):
        SyncConfig(crop_area_ratios=(float("nan"),))
    with pytest.raises(ValueError):
        SyncConfig(offset_fractions=(float("inf"),))


def test_sync_batch_scalar_decode_differential():
    marked, _ = embed_image(sample(), CID, "PNG")
    image, _ = watermark_module._load(marked)
    work, _ = watermark_module._work_gray(image)
    with watermark_module.dct_backend("scalar"):
        scalar = decode_work_gray(work)
    with watermark_module.dct_backend("batch"):
        batch = decode_work_gray(work)
    assert scalar == batch == CID

    control = np.zeros_like(work)
    with watermark_module.dct_backend("scalar"):
        scalar_control = decode_work_gray(control)
    with watermark_module.dct_backend("batch"):
        batch_control = decode_work_gray(control)
    assert scalar_control == batch_control is None


def test_sync_cached_resize_is_byte_equivalent():
    """Offset hypotheses may cache the ratio resize without changing work data."""
    pixels = np.random.default_rng(7).integers(0, 256, (768, 768, 3), dtype=np.uint8)
    for ratio in (1.0, 0.98, 0.95, 0.90):
        uncached = _candidate_gray(pixels, ratio, 0.03, -0.03)
        cached = _candidate_gray(
            pixels,
            ratio,
            0.03,
            -0.03,
            observed=_resize_observed(pixels, ratio),
        )
        np.testing.assert_array_equal(uncached, cached)


def test_v3_inferred_native_geometry_uses_floor_offset_and_aspect_ratio():
    geometry = _inferred_native_geometry((1996, 1497), 0.95, 0.0, 0.0)
    assert (geometry["native_width"], geometry["native_height"]) == (2048, 1536)
    assert (geometry["crop_width"], geometry["crop_height"]) == (1996, 1497)
    # The transform uses int((full-crop)/2 + offset*full), not round().
    shifted = _inferred_native_geometry((1996, 1497), 0.95, 0.002, -0.002)
    assert shifted["left"] == int((2048 - 1996) / 2 + 0.002 * 2048)
    assert shifted["top"] == int((1536 - 1497) / 2 - 0.002 * 1536)


def test_v3_profile_preserves_observed_aspect_and_reports_new_rule():
    result = detect_image_sync(sample((768, 1024)), SyncConfig(profile="decoder-v3-sync"))
    details = result["details"]
    assert details["decoder_profile"] == "decoder-v3-sync"
    assert details["reconstruction_mode"] == "inferred-native-aspect"
    assert details["reconstruction_max_side"] == 2048


def test_reusable_normalized_reconstruction_has_fixed_work_shape_and_validates_inputs():
    pixels = np.zeros((1497, 1996, 3), dtype=np.uint8)
    work = reconstruct_work_gray(
        pixels, {"crop_area_ratio": 0.95, "offset_x": 0.0, "offset_y": 0.0}
    )
    assert work.shape == (512, 512)
    with pytest.raises(ValueError):
        reconstruct_work_gray(pixels, {"crop_area_ratio": 0.95}, work_size=256)
    with pytest.raises(ValueError):
        reconstruct_work_gray(pixels, {"crop_area_ratio": 0.95, "offset_x": 2.0, "offset_y": 0.0})


def _stub_sync_search(monkeypatch, decoded, *, max_return_candidates=8):
    """Run aggregation against controlled decoder answers, without registry or image truth."""
    import gm_provenance.watermark_sync as sync

    monkeypatch.setattr(sync._wm, "detect_image", lambda _data: {"status": "NOT_FOUND", "content_ids": []})
    monkeypatch.setattr(sync, "_image_to_pixels", lambda _data: (None, np.zeros((8, 8, 3), dtype=np.uint8)))
    monkeypatch.setattr(sync, "_candidate_gray", lambda *args, **kwargs: np.zeros((512, 512), dtype=np.float32))
    answers = iter(decoded) if not isinstance(decoded, str) else None
    def next_answer(_gray):
        if answers is None:
            return decoded
        try:
            return next(answers)
        except StopIteration:
            return decoded[-1]
    monkeypatch.setattr(sync, "_decode_work_gray", next_answer)
    return sync.detect_image_sync(
        b"upload-bytes-only",
        SyncConfig(max_candidates=9, max_decode_attempts=9, max_search_ms=10000,
                   max_return_candidates=max_return_candidates,
                   crop_area_ratios=(1.0,), offset_fractions=(0.0, -0.03, 0.03)),
    )


def test_sync_aggregation_deduplicates_same_decoder_answer(monkeypatch):
    result = _stub_sync_search(monkeypatch, CID)
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID]
    assert result["search_complete"] is True
    assert len(result["details"]["geometry_hypotheses"][0]["geometries"]) > 1


def test_sync_aggregation_reports_multiple_ids_as_conflict(monkeypatch):
    other = "fedcba9876543210fedcba9876543210"
    result = _stub_sync_search(monkeypatch, [CID, other, CID, other, CID])
    assert result["status"] == "CONFLICT"
    assert result["content_ids"] == [CID, other]
    assert result["details"]["service_conclusion"] == "CONFLICT"


def test_sync_aggregation_return_limit_is_bounded_not_conflict_proof(monkeypatch):
    other = "fedcba9876543210fedcba9876543210"
    result = _stub_sync_search(monkeypatch, [CID, other, other], max_return_candidates=1)
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID]
    assert result["search_complete"] is False
    assert result["stop_reason"] == "return_budget_exhausted"
