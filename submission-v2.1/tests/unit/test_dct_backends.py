import io

import numpy as np
from PIL import Image

from gm_provenance import watermark


CID = "0123456789abcdef0123456789abcdef"


def _sample(size=(768, 768)):
    y, x = np.indices(size)
    rgb = np.empty((*size, 3), dtype=np.uint8)
    rgb[..., 0] = (x * 3 + y) % 256
    rgb[..., 1] = (y * 2 + 30) % 256
    rgb[..., 2] = ((x + y) * 2) % 256
    out = io.BytesIO()
    Image.fromarray(rgb).save(out, "PNG")
    return out.getvalue()


def test_batch_only_transforms_last_two_axes_and_matches_scalar():
    rng = np.random.default_rng(5)
    blocks = rng.normal(size=(37, 8, 8)).astype(np.float32)
    scalar = np.stack([watermark._dct_blocks(block) for block in blocks])
    batch = watermark._dct_blocks(blocks)
    assert batch.shape == (37, 8, 8)
    np.testing.assert_allclose(batch, scalar, rtol=2e-6, atol=2e-5)


def test_batch_roundtrip_and_decision_equivalence():
    raw = _sample()
    marked_scalar, _ = watermark.embed_image(raw, CID, "PNG")
    with watermark.dct_backend("batch"):
        marked_batch, profile = watermark.embed_image(raw, CID, "PNG")
        detected = watermark.detect_image(marked_batch)
    assert profile["dct_mode"] == "batch"
    assert detected["content_ids"] == [CID]
    assert watermark.detect_image(marked_scalar)["content_ids"] == [CID]
    with watermark.dct_backend("batch"):
        assert watermark.detect_image(marked_scalar)["content_ids"] == [CID]


def test_dct_backend_context_is_scoped():
    assert watermark.get_dct_backend() == "scalar"
    with watermark.dct_backend("batch"):
        assert watermark.get_dct_backend() == "batch"
    assert watermark.get_dct_backend() == "scalar"
