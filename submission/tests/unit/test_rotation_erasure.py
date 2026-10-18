"""真实 v1 载荷的可见重复块投票与 RS 擦除上限。"""
import io

import numpy as np
import pytest
from PIL import Image

from gm_provenance import watermark

CID = "0123456789abcdef0123456789abcdef"


def evidence():
    source = io.BytesIO()
    Image.new("L", (768, 768), 140).save(source, "PNG")
    marked, _ = watermark.embed_image(source.getvalue(), CID)
    return watermark._work_gray(Image.open(io.BytesIO(marked)))[0]


def erase_blocks(gray, block_indices):
    mask = np.ones_like(gray)
    positions = [(r, c) for r in range(1, 63) for c in range(1, 63)]
    for index in block_indices:
        r, c = positions[index]
        gray[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = 0
        mask[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = 0
    return mask


def test_one_visible_repeat_per_bit_suffices():
    gray = evidence()
    mask = erase_blocks(gray, [i for i in range(424 * 4) if i % 4 != 0])
    assert watermark._candidate_gray(gray, mask) == CID


@pytest.mark.parametrize("erased_bytes", [16, 32, 33])
def test_real_rs_erasure_budget(erased_bytes):
    gray = evidence()
    mask = erase_blocks(gray, range(erased_bytes * 8 * 4))
    assert watermark._candidate_gray(gray, mask) == (CID if erased_bytes <= 32 else None)


def test_crc_only_backend_does_not_claim_erasure_correction(monkeypatch):
    gray = evidence()
    mask = erase_blocks(gray, range(8 * 4))
    monkeypatch.setattr(watermark, "_ECC_BACKEND", "crc-only-fallback")
    assert watermark._candidate_gray(gray, mask) is None


def test_masked_votes_respect_dct_mode_and_match_decisions(monkeypatch):
    gray = evidence()
    mask = erase_blocks(gray, range(16 * 8 * 4))
    original_dct = watermark._dct_blocks
    calls = []

    def observed_dct(blocks):
        calls.append(blocks.ndim)
        return original_dct(blocks)

    monkeypatch.setattr(watermark, "_dct_blocks", observed_dct)
    for mode, expected_ndim, expected_calls in (("scalar", 2, 424 * 4), ("batch", 3, 1)):
        calls.clear()
        with watermark.dct_backend(mode):
            assert watermark._candidate_gray(gray, mask) == CID
        assert calls == [expected_ndim] * expected_calls
