import io
import math

import numpy as np
from PIL import Image

from gm_provenance import watermark
from gm_provenance.watermark_sync import _inferred_native_geometry, reconstruct_work_gray


def _rgb_pattern(size):
    """A deterministic pattern that exposes translation and resampling."""
    width, height = size
    yy, xx = np.indices((height, width))
    return np.stack(
        [
            (3 * xx + 5 * yy) % 256,
            (11 * xx + 7 * yy + 19) % 256,
            (13 * xx + 17 * yy + 31) % 256,
        ],
        axis=-1,
    ).astype(np.uint8)


def _expected_geometry(observed_size, area_ratio, offset_x, offset_y, max_side=2048):
    """Independent geometry oracle based on the documented crop transform."""
    obs_w, obs_h = observed_size
    width_fraction = math.sqrt(area_ratio)
    full_w = max(obs_w, round(obs_w / width_fraction))
    full_h = max(obs_h, round(obs_h / width_fraction))
    scale = min(1.0, max_side / max(full_w, full_h))
    native_w = max(8, round(full_w * scale))
    native_h = max(8, round(full_h * scale))
    crop_w = max(8, round(obs_w * scale))
    crop_h = max(8, round(obs_h * scale))
    left = int((native_w - crop_w) / 2 + offset_x * native_w)
    top = int((native_h - crop_h) / 2 + offset_y * native_h)
    left = min(max(left, 0), max(0, native_w - crop_w))
    top = min(max(top, 0), max(0, native_h - crop_h))
    return {
        "native_width": native_w,
        "native_height": native_h,
        "crop_width": crop_w,
        "crop_height": crop_h,
        "left": left,
        "top": top,
        "scale": scale,
    }


def _expected_work(pixels, area_ratio, offset_x, offset_y, *, max_side=2048):
    """Build the expected native canvas independently, then convert once."""
    source = Image.fromarray(np.asarray(pixels, dtype=np.uint8), mode="RGB")
    geometry = _expected_geometry(
        source.size, area_ratio, offset_x, offset_y, max_side=max_side
    )
    crop = source.resize(
        (geometry["crop_width"], geometry["crop_height"]), Image.Resampling.LANCZOS
    )
    canvas = Image.new(
        "RGB", (geometry["native_width"], geometry["native_height"]), (128, 128, 128)
    )
    canvas.paste(crop, (geometry["left"], geometry["top"]))
    return watermark._work_gray(canvas)[0], geometry


def test_known_points_and_identity_for_non_square_canvas():
    # ratio 1 is an identity geometry even when the native image is not square.
    pixels = _rgb_pattern((37, 23))
    actual = reconstruct_work_gray(
        pixels, {"crop_area_ratio": 1.0, "offset_x": 0.0, "offset_y": 0.0}
    )
    expected = watermark._work_gray(Image.fromarray(pixels, mode="RGB"))[0]
    np.testing.assert_array_equal(actual, expected)

    geometry = _inferred_native_geometry((37, 23), 1.0, 0.0, 0.0)
    assert {
        key: geometry[key]
        for key in ("native_width", "native_height", "crop_width", "crop_height", "left", "top")
    } == {"native_width": 37, "native_height": 23, "crop_width": 37, "crop_height": 23, "left": 0, "top": 0}


def test_center_and_boundary_offsets_use_clamp_and_floor():
    observed_size = (101, 73)
    ratio = 0.81
    center = _inferred_native_geometry(observed_size, ratio, 0.0, 0.0)
    expected_center = _expected_geometry(observed_size, ratio, 0.0, 0.0)
    assert center == expected_center

    # Large offsets must clamp to the final legal paste rectangle.
    boundary = _inferred_native_geometry(observed_size, ratio, 1.0, -1.0)
    assert boundary["left"] == boundary["native_width"] - boundary["crop_width"]
    assert boundary["top"] == 0

    # The transform truncates toward zero after adding the normalized offset.
    fractional = _inferred_native_geometry(observed_size, ratio, 0.002, -0.002)
    assert fractional["left"] == int(
        (fractional["native_width"] - fractional["crop_width"]) / 2
        + 0.002 * fractional["native_width"]
    )
    assert fractional["top"] == int(
        (fractional["native_height"] - fractional["crop_height"]) / 2
        - 0.002 * fractional["native_height"]
    )
    assert fractional["left"] != round(
        (fractional["native_width"] - fractional["crop_width"]) / 2
        + 0.002 * fractional["native_width"]
    )


def test_known_translation_and_scaling_match_independent_pil_oracle():
    pixels = _rgb_pattern((113, 79))
    ratio = 0.64
    offset_x, offset_y = 0.03, -0.02
    actual = reconstruct_work_gray(
        pixels,
        {"crop_area_ratio": ratio, "offset_x": offset_x, "offset_y": offset_y},
    )
    expected, geometry = _expected_work(pixels, ratio, offset_x, offset_y)
    np.testing.assert_array_equal(actual, expected)
    assert geometry["native_width"] == 141
    assert geometry["native_height"] == 99
    assert geometry["crop_width"] == 113
    assert geometry["crop_height"] == 79


def test_equivalent_normalized_rectangle_representation_is_stable():
    pixels = _rgb_pattern((113, 79))
    source_w, source_h = pixels.shape[1], pixels.shape[0]
    crop_w, crop_h = 90, 63
    x0, y0 = 7, 5
    ratio = (crop_w * crop_h) / (source_w * source_h)
    ox = (x0 - (source_w - crop_w) / 2) / source_w
    oy = (y0 - (source_h - crop_h) / 2) / source_h

    direct = reconstruct_work_gray(
        pixels, {"crop_area_ratio": ratio, "offset_x": ox, "offset_y": oy}
    )
    # Recomputing normalized values from the same rectangle is a separate
    # representation of the same geometry and must produce identical pixels.
    normalized_again = {
        "crop_area_ratio": float(crop_w * crop_h) / float(source_w * source_h),
        "offset_x": float(2 * x0 - source_w + crop_w) / (2 * source_w),
        "offset_y": float(2 * y0 - source_h + crop_h) / (2 * source_h),
    }
    equivalent = reconstruct_work_gray(pixels, normalized_again)
    np.testing.assert_array_equal(direct, equivalent)


def test_2048_cap_leaves_measurable_geometry_error():
    pixels = _rgb_pattern((4096, 3072))
    ratio = 0.95
    actual = reconstruct_work_gray(
        pixels, {"crop_area_ratio": ratio, "offset_x": 0.0, "offset_y": 0.0}
    )
    capped = _inferred_native_geometry(pixels.shape[1::-1], ratio, 0.0, 0.0)
    assert capped["scale"] < 1.0
    assert max(capped["native_width"], capped["native_height"]) == 2048

    # Construct the uncapped mathematical canvas separately.  Its dimensions
    # cannot be represented exactly after the cap, so the capped work domain
    # must retain a nonzero pixel error against this ideal reference.
    ideal, ideal_geometry = _expected_work(
        pixels, ratio, 0.0, 0.0, max_side=10000
    )
    assert ideal_geometry["scale"] == 1.0  # uncapped mathematical reference
    assert int(np.max(np.abs(actual.astype(np.int16) - ideal.astype(np.int16)))) > 0
