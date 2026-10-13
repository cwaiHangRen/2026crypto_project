"""边界几何恢复，不把攻击角度、源尺寸或真值传给候选生成器。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from gm_provenance import rotation


def carrier(size=(896, 640)):
    image = Image.new("RGB", size, (90, 140, 180))
    draw = ImageDraw.Draw(image)
    draw.rectangle((37, 55, size[0] * 0.55, size[1] * 0.43), fill=(220, 180, 60))
    draw.text((80, 100), "Asymmetric rectangle", fill=(20, 20, 20))
    return image


@pytest.mark.parametrize("size", [(896, 640), (640, 896), (768, 768)])
@pytest.mark.parametrize("angle", [0.1, 13.7, 41.77, 89.9, 127.4, 231.7, 359.9])
def test_expanded_rectangles_recover_native_geometry(size, angle):
    image = carrier(size).rotate(angle, Image.Resampling.BICUBIC, expand=True, fillcolor="black")
    candidates = list(rotation.rotation_candidates(image))
    assert 1 <= len(candidates) <= rotation.MAX_ROTATION_CANDIDATES
    assert all(details["method"] == "expanded-border" for _, _, details in candidates)
    sizes = [rectified.size for rectified, _, _ in candidates]
    assert any(max(abs(a - b) for a, b in zip(candidate, size)) <= 2
               or max(abs(a - b) for a, b in zip(candidate, size[::-1])) <= 2 for candidate in sizes)
    for _, mask, details in candidates:
        assert mask is None
        residual = (details["correction_degrees"] + angle + 45) % 90 - 45
        assert abs(residual) < 0.05


def test_alpha_border_does_not_depend_on_hidden_rgb_values():
    image = carrier().convert("RGBA").rotate(17.35, Image.Resampling.BICUBIC,
                                             expand=True, fillcolor=(255, 0, 255, 0))
    candidates = list(rotation.rotation_candidates(image))
    assert candidates
    assert all(details["border"] == "alpha" for _, _, details in candidates)
    assert any(max(abs(a - b) for a, b in zip(rectified.size, (896, 640))) <= 2
               for rectified, _, _ in candidates)


@pytest.mark.parametrize("size", [(896, 640), (640, 896), (768, 768)])
@pytest.mark.parametrize("angle", [13.7, 41.77, 73.8])
def test_clipped_canvas_has_visibility_mask_and_dimension_hypotheses(size, angle):
    image = carrier(size).rotate(angle, Image.Resampling.BICUBIC, expand=False, fillcolor="black")
    candidates = list(rotation.rotation_candidates(image))
    assert candidates
    assert len(candidates) <= rotation.MAX_ROTATION_CANDIDATES
    assert {rectified.size for rectified, _, _ in candidates} == {size, size[::-1]}
    for rectified, mask, details in candidates:
        assert details["method"] == "clipped-canvas"
        assert mask.mode == "L" and mask.size == rectified.size
        assert np.asarray(mask).min() == 0 and np.asarray(mask).max() == 255
        residual = (details["correction_degrees"] + angle + 45) % 90 - 45
        assert abs(residual) < 0.05


def test_ordinary_axis_aligned_image_does_not_trigger_search():
    assert list(rotation.rotation_candidates(carrier())) == []
    assert list(rotation.rotation_candidates(Image.new("RGB", (512, 512), "black"))) == []


def test_small_clipped_rotation_uses_actual_corner_pixels():
    image = carrier().rotate(0.3, Image.Resampling.BICUBIC, expand=False, fillcolor="black")
    candidates = list(rotation.rotation_candidates(image))
    assert candidates
    assert all(details["method"] == "clipped-canvas" for _, _, details in candidates)


def test_near_white_rectangle_remains_visible_against_white_rotation_border():
    from gm_provenance import watermark
    import io

    content_id = "0123456789abcdef0123456789abcdef"
    image = Image.new("RGB", (768, 768), (245, 248, 252))
    ImageDraw.Draw(image).rounded_rectangle((48, 48, 720, 720), radius=60, fill=(90, 140, 180))
    source = io.BytesIO()
    image.save(source, "PNG")
    marked, _ = watermark.embed_image(source.getvalue(), content_id)
    image = Image.open(io.BytesIO(marked)).rotate(37.5, Image.Resampling.BICUBIC,
                                               expand=True, fillcolor="white")
    candidates = list(rotation.rotation_candidates(image))
    assert candidates
    assert len(candidates) <= rotation.MAX_ROTATION_CANDIDATES
    assert any(details["threshold"] == 4 for _, _, details in candidates)
    output = io.BytesIO()
    image.save(output, "PNG")
    assert watermark.detect_image(output.getvalue())["content_ids"] == [content_id]


def test_missing_scipy_keeps_geometry_optional(monkeypatch):
    monkeypatch.setattr(rotation, "ConvexHull", None)
    assert list(rotation.rotation_candidates(carrier().rotate(17, expand=True))) == []


def test_geometry_rejects_extreme_side_even_under_pixel_limit():
    image = Image.new("RGB", (rotation.MAX_GEOMETRY_SIDE + 1, 64), "black")
    assert list(rotation.rotation_candidates(image)) == []
