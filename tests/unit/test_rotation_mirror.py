"""真实载荷正例、无水印负例与双编号冲突；不向检测器传入真值。"""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gm_provenance import watermark

CID_A = "0123456789abcdef0123456789abcdef"
CID_B = "fedcba9876543210fedcba9876543210"
OPERATIONS = [None, Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180,
              Image.Transpose.ROTATE_270, Image.Transpose.FLIP_LEFT_RIGHT,
              Image.Transpose.FLIP_TOP_BOTTOM, Image.Transpose.TRANSPOSE,
              Image.Transpose.TRANSVERSE]


def source(size=(896, 640), mode="RGB", fmt="PNG"):
    image = Image.new(mode, size, 150 if mode == "L" else (90, 140, 180))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 60, size[0] // 2, size[1] // 2), outline="white", width=3)
    draw.text((80, 100), "Asymmetric carrier", fill="black")
    out = io.BytesIO()
    image.save(out, fmt, quality=95)
    return out.getvalue()


@pytest.fixture(scope="module", params=[("PNG", "RGB", (896, 640)),
                                        ("JPEG", "RGB", (640, 896)),
                                        ("PNG", "L", (768, 768))])
def carrier(request):
    fmt, mode, size = request.param
    raw = source(size, mode, fmt)
    marked, _ = watermark.embed_image(raw, CID_A, fmt)
    return raw, marked


@pytest.mark.parametrize("operation", OPERATIONS)
def test_eight_directions_recover_exact_id_and_reject_unmarked(carrier, operation):
    raw, marked = carrier
    for data, expected in ((marked, CID_A), (raw, None)):
        image = Image.open(io.BytesIO(data))
        if operation is not None:
            image = image.transpose(operation)
        out = io.BytesIO()
        image.save(out, "PNG")
        result = watermark.detect_image(out.getvalue())
        assert result["status"] == ("FOUND" if expected else "NOT_FOUND")
        assert result["content_ids"] == ([expected] if expected else [])
        assert len(result["details"]["orientations_checked"]) == 8


@pytest.mark.parametrize("quality", [95, 85])
def test_combined_mirror_rotation_and_jpeg_reencoding(quality):
    marked, _ = watermark.embed_image(source(), CID_A)
    image = Image.open(io.BytesIO(marked)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    image = image.transpose(Image.Transpose.ROTATE_90)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    result = watermark.detect_image(out.getvalue())
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID_A]


def dual_id_image(second_id=CID_B):
    raw = source((512, 512), "L")
    marked_a, _ = watermark.embed_image(raw, CID_A)
    marked_b, _ = watermark.embed_image(raw, second_id)
    image = Image.open(io.BytesIO(marked_a))
    opposite = Image.open(io.BytesIO(marked_b)).transpose(Image.Transpose.ROTATE_180)
    # v1 payload occupies the upper half. Opposite payloads occupy disjoint halves.
    image.paste(opposite.crop((0, 256, 512, 512)), (0, 256))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_real_two_ids_in_different_orientations_are_conflict():
    result = watermark.detect_image(dual_id_image())
    assert result["status"] == "CONFLICT"
    assert result["content_ids"] == sorted([CID_A, CID_B])
    assert {match["content_id"] for match in result["details"]["matches"]} == {CID_A, CID_B}


def test_same_real_id_in_two_orientations_is_deduplicated():
    result = watermark.detect_image(dual_id_image(CID_A))
    assert result["status"] == "FOUND"
    assert result["content_ids"] == [CID_A]
    assert len(result["details"]["matches"]) >= 2


def test_numpy_dct_backend_handles_batched_blocks(monkeypatch):
    marked, _ = watermark.embed_image(source(), CID_A)
    i, j = np.indices((8, 8))
    matrix = np.sqrt(2 / 8) * np.cos(np.pi * (2 * j + 1) * i / 16)
    matrix[0] = 1 / np.sqrt(8)

    def numpy_dct(block, type=2, norm="ortho", axes=(-2, -1)):
        return matrix @ np.asarray(block, dtype=np.float32) @ matrix.T

    monkeypatch.setattr(watermark, "dctn", numpy_dct)
    assert watermark.detect_image(marked)["content_ids"] == [CID_A]


@pytest.mark.parametrize("data", [b"", b"not an image"])
def test_invalid_image_stays_not_found(data):
    result = watermark.detect_image(data)
    assert result["status"] == "NOT_FOUND"
    assert result["content_ids"] == []
