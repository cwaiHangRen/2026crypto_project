import io

import numpy as np
from PIL import Image

from gm_provenance.watermark import detect_image, embed_image


def sample(size=(768, 768)):
    y, x = np.indices(size)
    a = np.empty((*size, 3), dtype=np.uint8)
    a[..., 0] = (x * 3 + y) % 256
    a[..., 1] = (y * 2 + 30) % 256
    a[..., 2] = ((x + y) * 2) % 256
    b = io.BytesIO(); Image.fromarray(a).save(b, "PNG"); return b.getvalue()


CID = "0123456789abcdef0123456789abcdef"


def test_png_roundtrip_and_no_watermark():
    raw = sample()
    marked, profile = embed_image(raw, CID, "PNG")
    assert profile["work_size"] == 512
    result = detect_image(marked)
    assert result["status"] == "FOUND" and result["content_ids"] == [CID]
    assert detect_image(raw)["status"] == "NOT_FOUND"


def test_profile_reports_loaded_dct_and_ecc_backends():
    marked, profile = embed_image(sample(), CID, "PNG")
    assert profile["dct_backend"] == "scipy"
    assert profile["ecc"].startswith("reed-solomon-rs(")
    assert detect_image(marked)["content_ids"] == [CID]


def test_jpeg_reencode_and_resize():
    raw = sample()
    marked, _ = embed_image(raw, CID, "JPEG")
    result = detect_image(marked)
    assert CID in result["content_ids"]
    im = Image.open(io.BytesIO(marked)).resize((384, 384), Image.Resampling.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=85)
    assert CID in detect_image(b.getvalue())["content_ids"]


def test_cid_is_strict():
    import pytest
    with pytest.raises(ValueError): embed_image(sample(), "ABC", "PNG")
