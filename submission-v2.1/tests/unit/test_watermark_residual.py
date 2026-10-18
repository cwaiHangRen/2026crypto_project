import io

import numpy as np
import pytest
from PIL import Image
from gm_provenance import watermark as wm

from gm_provenance.watermark import (
    diagnose_image_residual,
    detect_image,
    embed_image,
    embed_image_residual,
    encode_without_watermark_residual,
)


CID = "0123456789abcdef0123456789abcdef"


def _bytes(image: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    stream = io.BytesIO()
    image.save(stream, fmt, **kwargs)
    return stream.getvalue()


def _rgb() -> tuple[bytes, np.ndarray]:
    y, x = np.indices((640, 768))
    arr = np.empty((640, 768, 3), dtype=np.uint8)
    arr[..., 0] = (x * 3 + y) % 256
    arr[..., 1] = (y * 2 + 17) % 256
    arr[..., 2] = ((x + y) * 2) % 256
    return _bytes(Image.fromarray(arr, "RGB")), arr


def test_residual_profile_and_blind_v1_decoder():
    raw, _ = _rgb()
    marked, profile = embed_image_residual(raw, CID, "PNG")
    assert profile["embed_profile"] == "watermark-v2-residual"
    assert profile["payload_version"] == profile["decoder_version"] == 1
    assert profile["work_size"] == 512
    assert detect_image(marked)["content_ids"] == [CID]


def test_residual_png_control_is_pixel_identity_for_rgb_and_l():
    raw, rgb = _rgb()
    control, profile = encode_without_watermark_residual(raw, "PNG")
    assert profile["watermark"] == "disabled"
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(control))), rgb)

    gray = np.arange(768 * 640, dtype=np.uint8).reshape(640, 768)
    gray_raw = _bytes(Image.fromarray(gray, "L"))
    gray_control, gray_profile = encode_without_watermark_residual(gray_raw, "PNG")
    assert gray_profile["input_mode"] == "L"
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(gray_control))), gray)


@pytest.mark.parametrize("mode", ["RGBA", "P", "CMYK", "I"])
def test_residual_rejects_unsupported_modes(mode):
    if mode == "RGBA":
        image = Image.new(mode, (768, 640), (10, 20, 30, 255))
    elif mode == "P":
        image = Image.new(mode, (768, 640))
    elif mode == "CMYK":
        image = Image.new(mode, (768, 640), (10, 20, 30, 40))
    else:
        image = Image.new(mode, (768, 640), 10)
    # Pillow cannot write CMYK PNG; JPEG still preserves the unsupported mode
    # marker needed to verify the explicit v2 rejection.
    raw = _bytes(image, "JPEG", quality=95) if mode == "CMYK" else _bytes(image)
    with pytest.raises(ValueError, match="仅支持 L 或 RGB"):
        embed_image_residual(raw, CID, "PNG")
    with pytest.raises(ValueError, match="仅支持 L 或 RGB"):
        encode_without_watermark_residual(raw, "PNG")


def test_residual_rejects_non_normalized_exif_orientation():
    image = Image.new("RGB", (768, 640), (10, 20, 30))
    exif = image.getexif()
    exif[274] = 6
    raw = _bytes(image, exif=exif.tobytes())
    with pytest.raises(ValueError, match="EXIF Orientation=1"):
        embed_image_residual(raw, CID, "PNG")


def test_residual_diagnostic_contains_coeff_errors_ecc_and_clipping():
    raw, _ = _rgb()
    result = diagnose_image_residual(raw, CID, "PNG")
    assert result["embed_profile"] == "watermark-v2-residual"
    assert result["work_domain"]["coefficient_count"] > 0
    assert result["final_reread"]["ecc_decoded"] is True
    assert result["final_reread"]["recovered_content_id"] == CID
    assert set(result["clipping"]) >= {"clipped_pixels", "clipped_fraction"}


@pytest.mark.parametrize("output_format", ["PNG", "JPEG"])
def test_legacy_v1_output_matches_frozen_clipped_reference(monkeypatch, output_format):
    raw, _ = _rgb()
    actual, profile = embed_image(raw, CID, output_format)

    # Literal pre-v2 block modification, independent of the new clip flag.
    def legacy_embed(gray, bits):
        out = gray.copy()
        positions = [(r, c) for r in range(1, 63) for c in range(1, 63)]
        k = 0
        for bit in bits:
            for _ in range(4):
                br, bc = positions[k]
                k += 1
                block = out[br * 8:br * 8 + 8, bc * 8:bc * 8 + 8]
                coeff = wm.dctn(block, type=2, norm="ortho")
                a, b = coeff[2, 3], coeff[3, 2]
                mid = (a + b) / 2.0
                d = 34.0 if bit else -34.0
                coeff[2, 3], coeff[3, 2] = mid + d / 2, mid - d / 2
                out[br * 8:br * 8 + 8, bc * 8:bc * 8 + 8] = wm.idctn(coeff, type=2, norm="ortho")
        return np.clip(out, 0, 255)

    monkeypatch.setattr(wm, "_embed_gray", legacy_embed)
    expected, expected_profile = embed_image(raw, CID, output_format)
    assert actual == expected
    assert profile == expected_profile
    assert profile["version"] == 1
    assert "embed_profile" not in profile


def test_residual_uses_unclipped_float_before_upsampling():
    im = Image.new("L", (769, 637), 0)
    bits = wm._bits(bytes(wm._RS.encode(wm._payload(CID))))
    gray, _ = wm._work_gray(im)
    target = wm._embed_gray(gray, bits, clip_output=False)
    assert target.dtype == np.float32
    assert target.min() < 0 < target.max()
    assert np.array_equal(wm._embed_gray(gray, bits), np.clip(target, 0, 255))
    delta = target - gray
    delta_full = np.asarray(Image.fromarray(delta, "F").resize(im.size, Image.Resampling.LANCZOS))
    expected = np.clip(delta_full, 0, 255).astype(np.uint8)
    clipped_delta = np.clip(target, 0, 255) - gray
    wrong = np.clip(np.asarray(Image.fromarray(clipped_delta, "F").resize(im.size, Image.Resampling.LANCZOS)), 0, 255).astype(np.uint8)
    assert not np.array_equal(expected, wrong), "fixture must detect early work-domain clipping"
    output, profile = embed_image_residual(_bytes(im), CID)
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(output))), expected)
    assert profile["residual"]["residual_min"] < 0
    assert profile["residual"]["clipped_pixels"] > 0


@pytest.mark.parametrize("output_format", ["PNG", "JPEG"])
def test_diagnostics_separate_pre_and_post_codec_and_float_targets(output_format):
    raw = _bytes(Image.new("L", (769, 637), 0))
    result = diagnose_image_residual(raw, CID, output_format)
    assert result["work_domain"]["modified_min"] < 0
    assert result["work_domain"]["target_is_unclipped_float"]
    for entry in result["target_coefficients"]:
        assert entry["target_difference"] == pytest.approx(entry["desired_difference"], abs=0.001)
        assert "pre_encode_difference" in entry
    for stage in ("pre_encode", "post_encode"):
        assert 0 <= result[stage]["bit_errors_before_ecc"] <= 424
        assert "bit_errors_after_ecc" in result[stage]
        assert "ecc_matches_expected" in result[stage]
        assert "corrected_symbols" in result[stage]
    if output_format == "PNG":
        assert result["pre_encode"] == result["post_encode"]
    marked, _ = embed_image_residual(raw, CID, output_format)
    blind = detect_image(marked)
    assert result["post_encode"]["ecc_decoded"] == (blind["status"] == "FOUND")
    if blind["status"] == "FOUND":
        assert result["post_encode"]["recovered_content_id"] == blind["content_ids"][0]
