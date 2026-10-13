import io

import numpy as np
import pytest
from PIL import Image

from gm_provenance import media, watermark


CID = "0123456789abcdef0123456789abcdef"


def _png(mode="RGB"):
    if mode == "L":
        arr = np.arange(768 * 1024, dtype=np.uint32).reshape(768, 1024) % 256
    else:
        base = np.arange(768 * 1024, dtype=np.uint32).reshape(768, 1024) % 256
        arr = np.stack([base, np.roll(base, 17, 0), np.roll(base, 31, 1)], axis=2)
    out = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), mode).save(out, "PNG")
    return out.getvalue()


def test_v2_profile_is_explicit_and_blind_detector_uses_shared_decoder():
    raw = _png()
    marked, profile = media.embed(raw, CID, "image/png", "watermark-v2-residual")
    assert profile["embed_profile"] == "watermark-v2-residual"
    assert profile["payload_version"] == 1
    assert profile["decoder_version"] == 1
    assert media.detect(marked, "image/png")["content_ids"] == [CID]


def test_v2_lossless_control_preserves_declared_pixels_and_v1_remains_default():
    raw = _png("L")
    control, control_profile = watermark.encode_without_watermark_residual(raw, "PNG")
    with Image.open(io.BytesIO(raw)) as before, Image.open(io.BytesIO(control)) as after:
        assert np.array_equal(np.asarray(before), np.asarray(after))
    assert control_profile["embed_profile"] == "watermark-v2-residual"
    _v1, v1_profile = media.embed(raw, CID, "image/png")
    assert v1_profile["algorithm"] == "dct-difference"
    assert v1_profile.get("embed_profile") != "watermark-v2-residual"


def test_pdf_profile_cannot_be_switched_to_residual():
    with pytest.raises(ValueError, match="PDF"):
        media.embed(b"%PDF-1.7\n", CID, "application/pdf", "watermark-v2-residual")


def test_v2_rejects_alpha_without_silent_background_change():
    raw = io.BytesIO()
    Image.new("RGBA", (768, 768), (255, 0, 0, 0)).save(raw, "PNG")
    with pytest.raises(ValueError, match="alpha"):
        watermark.embed_image_residual(raw.getvalue(), CID, "PNG")
