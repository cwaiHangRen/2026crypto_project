"""实际 v1 字节的盲检测；任意角度真值不传给检测器。"""
import io

import pytest
from PIL import Image, ImageDraw

from gm_provenance import watermark

ARBITRARY_CID_A = "0123456789abcdef0123456789abcdef"
ARBITRARY_CID_B = "fedcba9876543210fedcba9876543210"


def arbitrary_source(size=(896, 640), mode="RGB", fmt="PNG"):
    image = Image.new(mode, size, 150 if mode == "L" else (90, 140, 180))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 60, size[0] // 2, size[1] // 2), outline="white", width=3)
    draw.text((80, 100), "Asymmetric arbitrary carrier", fill="black")
    out = io.BytesIO()
    image.save(out, fmt, quality=95)
    return out.getvalue()


def arbitrary_rotated(data, angle, mirror=False, border="black"):
    image = Image.open(io.BytesIO(data))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if border == "transparent":
        image = image.convert("RGBA")
        fill = (0, 0, 0, 0)
    else:
        fill = 0 if border == "black" else 255
        if image.mode != "L":
            image = image.convert("RGB")
            fill = (fill, fill, fill)
    image = image.rotate(angle, Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize("fmt,mode,size,angle,mirror,border", [
    ("PNG", "RGB", (896, 640), 13.7, False, "black"),
    ("JPEG", "RGB", (640, 896), 127.4, True, "white"),
    ("PNG", "L", (768, 768), 231.7, True, "transparent"),
    ("PNG", "RGB", (896, 640), 359.2, False, "white"),
    ("PNG", "L", (768, 768), 44.65, False, "black"),
])
def test_arbitrary_expanded_v1_payload_and_matching_negative(fmt, mode, size, angle, mirror, border):
    raw = arbitrary_source(size, mode, fmt)
    marked, profile = watermark.embed_image(raw, ARBITRARY_CID_A, fmt)
    assert profile["version"] == 1
    for data, expected in ((marked, ARBITRARY_CID_A), (raw, None)):
        result = watermark.detect_image(arbitrary_rotated(data, angle, mirror, border))
        assert result["status"] == ("FOUND" if expected else "NOT_FOUND")
        assert result["content_ids"] == ([expected] if expected else [])


def arbitrary_two_id_image():
    raw = arbitrary_source((512, 512), "L")
    marked_a, _ = watermark.embed_image(raw, ARBITRARY_CID_A)
    marked_b, _ = watermark.embed_image(raw, ARBITRARY_CID_B)
    image = Image.open(io.BytesIO(marked_a))
    opposite = Image.open(io.BytesIO(marked_b)).transpose(Image.Transpose.ROTATE_180)
    # v1 两份载荷位于各自朝向的上半区，合并时不互相覆盖。
    image.paste(opposite.crop((0, 256, 512, 512)), (0, 256))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_arbitrary_rotation_preserves_real_two_id_conflict():
    changed = arbitrary_rotated(arbitrary_two_id_image(), 33.3, mirror=True, border="black")
    result = watermark.detect_image(changed)
    assert result["status"] == "CONFLICT"
    assert result["content_ids"] == sorted([ARBITRARY_CID_A, ARBITRARY_CID_B])
    assert {match["content_id"] for match in result["details"]["matches"]} == {ARBITRARY_CID_A, ARBITRARY_CID_B}
