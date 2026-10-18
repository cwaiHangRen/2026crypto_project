"""鲁棒图片来源水印（DCT + Reed-Solomon）。

这是来源线索基线：水印不替代文件摘要和签名。检测端不需要原图或
ContentID，遇到多个可校验候选时返回 CONFLICT。
"""
from __future__ import annotations

import io
import re
import zlib
from contextlib import contextmanager
import contextvars
from typing import Any

import numpy as np
from PIL import Image
from .rotation import GEOMETRY_BACKEND, MAX_ROTATION_CANDIDATES, rotation_candidates
try:
    from scipy.fft import dctn, idctn
    _DCT_BACKEND = "scipy"
except ImportError:  # offline bundled runtime fallback; same orthonormal DCT-II
    _DCT_BACKEND = "numpy"
    _N = 8
    _I, _J = np.indices((_N, _N))
    _C = np.sqrt(2 / _N) * np.cos(np.pi * (2 * _J + 1) * _I / (2 * _N))
    _C[0, :] = 1 / np.sqrt(_N)
    def dctn(block, type=2, norm="ortho", axes=None):
        arr = np.asarray(block, dtype=np.float32)
        if arr.ndim == 2:
            return _C @ arr @ _C.T
        return np.stack([_C @ item @ _C.T for item in arr])
    def idctn(block, type=2, norm="ortho", axes=None):
        arr = np.asarray(block, dtype=np.float32)
        if arr.ndim == 2:
            return _C.T @ arr @ _C
        return np.stack([_C.T @ item @ _C for item in arr])
try:
    from reedsolo import RSCodec
    _ECC_BACKEND = "reedsolo"
except ImportError:  # explicit offline fallback: CRC detects, no correction claim
    _ECC_BACKEND = "crc-only-fallback"
    class RSCodec:
        def __init__(self, nsym): self.nsym = nsym
        def encode(self, payload): return bytes(payload) + bytes(self.nsym)
        def decode(self, codeword):
            if len(codeword) != 21 + self.nsym: raise ValueError("invalid codeword")
            return (bytes(codeword[:21]), bytes(codeword[21:]), bytes())

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
WORK_SIZE = 512
VERSION = 1
_CID_RE = re.compile(r"^[0-9a-f]{32}$")
_RS = RSCodec(32)
_MAGNITUDE = 34.0
_REPEAT = 4
_CODEWORD_BYTES = 21 + 32
_HEADER_BITS = _CODEWORD_BYTES * 8
_V2_PROFILE = "watermark-v2-residual"
_DCT_MODE = contextvars.ContextVar("gm_provenance_dct_mode", default="scalar")
_DIRECT_DETECTION = contextvars.ContextVar("gm_provenance_direct_detection", default=False)


@contextmanager
def direct_detection():
    """Keep publishing self-checks and sync D0 on their original direct decoder.

    Ordinary verification searches every supported direction and geometric
    candidate. Internal callers can retain a strict single-direction check
    without changing the detector signature or bypassing its existing hook.
    The setting is scoped to this context and is restored even on failure.
    """
    token = _DIRECT_DETECTION.set(True)
    try:
        yield
    finally:
        _DIRECT_DETECTION.reset(token)


@contextmanager
def dct_backend(mode: str):
    """Temporarily select the scalar or block-batched DCT implementation.

    Batch mode transforms only the last two (8x8 block) axes; it never mixes
    neighbouring blocks.  The default remains scalar for byte compatibility
    with existing v1/v2 files.
    """
    value = str(mode).lower()
    if value not in ("scalar", "batch"):
        raise ValueError("DCT backend must be scalar or batch")
    token = _DCT_MODE.set(value)
    try:
        yield value
    finally:
        _DCT_MODE.reset(token)


def get_dct_backend() -> str:
    return _DCT_MODE.get()


# Readable alias for callers that distinguish the numerical library backend
# (``_DCT_BACKEND``) from the execution mode used by this process.
dct_mode = dct_backend


def _dct_blocks(blocks: np.ndarray) -> np.ndarray:
    """DCT a stack of 8x8 blocks without crossing block boundaries."""
    arr = np.asarray(blocks, dtype=np.float32)
    if arr.ndim == 2:
        return dctn(arr, type=2, norm="ortho")
    return dctn(arr, axes=(-2, -1), type=2, norm="ortho")


def _idct_blocks(coeffs: np.ndarray) -> np.ndarray:
    arr = np.asarray(coeffs, dtype=np.float32)
    if arr.ndim == 2:
        return idctn(arr, type=2, norm="ortho")
    return idctn(arr, axes=(-2, -1), type=2, norm="ortho")


def _check_cid(content_id: str) -> str:
    if not isinstance(content_id, str) or not _CID_RE.fullmatch(content_id):
        raise ValueError("content_id 必须是 32 位小写十六进制字符串")
    return content_id


def _load(data: bytes) -> tuple[Image.Image, str]:
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
        raise ValueError("输入图片为空、过大或不是字节")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:
        raise ValueError("无法解码图片") from exc
    if im.width * im.height > MAX_PIXELS:
        raise ValueError("图片像素数超过 16MP 限制")
    mode = "L" if im.mode in ("1", "L", "I", "F") else "RGB"
    # Preserve alpha for geometric detection while v1 publishing still emits
    # its historical RGB/L bytes through _make_image.
    has_alpha = "A" in im.getbands() or "transparency" in im.info
    return im.convert("RGBA" if has_alpha else mode), mode


def _payload(cid: str) -> bytes:
    raw = bytes([VERSION]) + bytes.fromhex(cid)
    return raw + zlib.crc32(raw).to_bytes(4, "big")


def _bits(buf: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(buf, dtype=np.uint8)).astype(np.uint8)


def _work_gray(im: Image.Image) -> tuple[np.ndarray, Image.Image]:
    rgb = im.convert("RGB")
    small = rgb.resize((WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS)
    arr = np.asarray(small, dtype=np.float32)
    y = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return y, small


def _embed_gray(gray: np.ndarray, bits: np.ndarray, *, clip_output: bool = True) -> np.ndarray:
    out = gray.copy()
    # Adjacent 8x8 blocks make detection deterministic and permit repetition.
    slots = int(len(bits) * _REPEAT)
    positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1) for c in range(1, WORK_SIZE // 8 - 1)]
    if slots > len(positions):
        raise ValueError("图片尺寸不足以承载水印")
    selected = positions[:slots]
    if _DCT_MODE.get() == "batch":
        blocks = np.stack([out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8] for br, bc in selected])
        coeffs = _dct_blocks(blocks)
        bit_values = np.repeat(np.asarray(bits, dtype=np.uint8), _REPEAT)
        mids = (coeffs[:, 2, 3] + coeffs[:, 3, 2]) / 2.0
        deltas = np.where(bit_values != 0, _MAGNITUDE, -_MAGNITUDE)
        coeffs[:, 2, 3], coeffs[:, 3, 2] = mids + deltas / 2, mids - deltas / 2
        restored = _idct_blocks(coeffs)
        for (br, bc), block in zip(selected, restored):
            out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8] = block
    else:
        k = 0
        for bit in bits:
            for _ in range(_REPEAT):
                br, bc = positions[k]; k += 1
                block = out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8]
                coeff = _dct_blocks(block)
                a, b = coeff[2, 3], coeff[3, 2]
                mid = (a + b) / 2.0
                d = _MAGNITUDE if bit else -_MAGNITUDE
                coeff[2, 3], coeff[3, 2] = mid + d / 2, mid - d / 2
                out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8] = _idct_blocks(coeff)
    # V1 retains its historical clipping. V2 needs the signed, unbounded work
    # signal so clipping happens only after the residual reaches the carrier.
    return np.clip(out, 0, 255) if clip_output else out


def _make_image(im: Image.Image, original_mode: str, modified_y: np.ndarray) -> Image.Image:
    base = im.convert("RGB").resize((WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS)
    rgb = np.asarray(base, dtype=np.float32)
    old_y = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    delta = modified_y - old_y
    rgb = np.clip(rgb + delta[..., None], 0, 255).astype(np.uint8)
    out = Image.fromarray(rgb, "RGB").resize(im.size, Image.Resampling.LANCZOS)
    return out.convert("L") if original_mode == "L" else out


def embed_image(data: bytes, content_id: str, output_format: str = "PNG") -> tuple[bytes, dict[str, Any]]:
    """嵌入水印并返回编码字节与公开 profile。"""
    cid = _check_cid(content_id)
    im, mode = _load(data)
    encoded = _RS.encode(_payload(cid))
    allbits = _bits(bytes(encoded))
    # 每个编码位写入多个块；RS 随后处理传播变换造成的残余位错误。
    modified = _embed_gray(_work_gray(im)[0], allbits)
    outim = _make_image(im, mode, modified)
    fmt = output_format.upper()
    if fmt not in ("PNG", "JPEG", "JPG"):
        raise ValueError("output_format 仅支持 PNG 或 JPEG")
    bio = io.BytesIO()
    outim.save(bio, format="JPEG" if fmt in ("JPEG", "JPG") else "PNG", quality=95, optimize=False)
    profile = {"algorithm": "dct-difference", "version": VERSION, "work_size": WORK_SIZE,
               "ecc": "reed-solomon-rs(53,21)" if _ECC_BACKEND == "reedsolo" else "crc-only-fallback (RS unavailable)",
               "dct_backend": _DCT_BACKEND, "dct_mode": _DCT_MODE.get(), "repeat": _REPEAT, "strength": _MAGNITUDE,
               "input_size": [im.width, im.height], "output_format": "JPEG" if fmt in ("JPEG", "JPG") else "PNG"}
    return bio.getvalue(), profile


def _load_residual(data: bytes) -> tuple[Image.Image, str]:
    """Load the explicitly supported v2 carrier modes.

    The v1 loader intentionally remains unchanged for compatibility.  V2 does
    not silently flatten alpha, palette or CMYK data because doing so would
    change the coordinate system used by the residual.  EXIF orientation is
    likewise rejected unless it is the normal (1) orientation; callers can
    normalize such media explicitly before publishing.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
        raise ValueError("输入图片为空、过大或不是字节")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:
        raise ValueError("无法解码图片") from exc
    if im.width * im.height > MAX_PIXELS:
        raise ValueError("图片像素数超过 16MP 限制")
    if im.mode not in ("L", "RGB"):
        raise ValueError("watermark-v2-residual 仅支持 L 或 RGB 图片，不支持 alpha/调色板/其他模式")
    try:
        orientation = int(im.getexif().get(274, 1))
    except Exception as exc:
        raise ValueError("无法读取 EXIF 方向") from exc
    if orientation != 1:
        raise ValueError("watermark-v2-residual 要求 EXIF Orientation=1，请先显式规范化方向")
    return im.copy(), im.mode


def _validate_output_format(output_format: str) -> str:
    fmt = output_format.upper()
    if fmt not in ("PNG", "JPEG", "JPG"):
        raise ValueError("output_format 仅支持 PNG 或 JPEG")
    return "JPEG" if fmt in ("JPEG", "JPG") else "PNG"


def _encode_image(im: Image.Image, output_format: str) -> bytes:
    """Encode using the same explicit options as the v1 image publisher."""
    fmt = _validate_output_format(output_format)
    bio = io.BytesIO()
    im.save(bio, format=fmt, quality=95, optimize=False)
    return bio.getvalue()


def _residual_marked_image(im: Image.Image, mode: str, bits: np.ndarray) -> tuple[Image.Image, dict[str, Any]]:
    """Apply the v1 work-domain signal as an upsampled float residual."""
    work_gray, _ = _work_gray(im)
    modified = _embed_gray(work_gray, bits, clip_output=False)
    # Keep the residual signed and floating point until it is added to the
    # original-resolution carrier.  Converting this array to uint8 first
    # would discard negative perturbations and wrap positive ones.
    residual = modified.astype(np.float32) - work_gray.astype(np.float32)
    residual_im = Image.fromarray(residual, mode="F").resize(im.size, Image.Resampling.LANCZOS)
    residual_full = np.asarray(residual_im, dtype=np.float32)
    if mode == "L":
        original = np.asarray(im, dtype=np.float32)
        candidate = original + residual_full
        clip_mask = (candidate < 0) | (candidate > 255)
        out_arr = np.clip(candidate, 0, 255).astype(np.uint8)
        out = Image.fromarray(out_arr, mode="L")
    else:
        original = np.asarray(im.convert("RGB"), dtype=np.float32)
        candidate = original + residual_full[..., None]
        clip_mask = np.any((candidate < 0) | (candidate > 255), axis=2)
        out_arr = np.clip(candidate, 0, 255).astype(np.uint8)
        out = Image.fromarray(out_arr, mode="RGB")
    stats = {
        "clipped_pixels": int(np.count_nonzero(clip_mask)),
        "total_pixels": int(im.width * im.height),
        "clipped_fraction": float(np.mean(clip_mask)) if clip_mask.size else 0.0,
        "residual_min": float(np.min(residual_full)) if residual_full.size else 0.0,
        "residual_max": float(np.max(residual_full)) if residual_full.size else 0.0,
        "residual_mean_abs": float(np.mean(np.abs(residual_full))) if residual_full.size else 0.0,
    }
    return out, stats


def _residual_profile(im: Image.Image, output_format: str, *, watermark: str = "enabled") -> dict[str, Any]:
    fmt = _validate_output_format(output_format)
    return {
        "algorithm": "dct-difference",
        "embed_profile": _V2_PROFILE,
        "version": VERSION,
        "payload_version": VERSION,
        "decoder_version": VERSION,
        "work_size": WORK_SIZE,
        "ecc": "reed-solomon-rs(53,21)" if _ECC_BACKEND == "reedsolo" else "crc-only-fallback (RS unavailable)",
        "dct_backend": _DCT_BACKEND,
        "dct_mode": _DCT_MODE.get(),
        "repeat": _REPEAT,
        "strength": _MAGNITUDE,
        "input_size": [im.width, im.height],
        "input_mode": im.mode,
        "output_format": fmt,
        "watermark": watermark,
        "residual_scale": 1.0,
        "residual_source": "unclipped-float-work-luminance",
        "quantization": "clip-0-255-then-uint8-truncate",
        "orientation_policy": "reject-non-1",
    }


def embed_image_residual(data: bytes, content_id: str, output_format: str = "PNG") -> tuple[bytes, dict[str, Any]]:
    """Embed the v1 payload by writing only its work-domain residual.

    Payload layout, Reed--Solomon parameters, DCT positions, repetition and
    strength are shared with :func:`embed_image`; only the reconstruction step
    differs.  The function is deliberately independent of the legacy entry
    point so changing the candidate cannot alter v1 bytes.
    """
    cid = _check_cid(content_id)
    fmt = _validate_output_format(output_format)
    im, mode = _load_residual(data)
    encoded = _RS.encode(_payload(cid))
    bits = _bits(bytes(encoded))
    outim, residual_stats = _residual_marked_image(im, mode, bits)
    encoded_bytes = _encode_image(outim, fmt)
    profile = _residual_profile(im, fmt)
    profile["residual"] = residual_stats
    return encoded_bytes, profile


def encode_without_watermark_residual(data: bytes, output_format: str = "PNG") -> tuple[bytes, dict[str, Any]]:
    """Encode a v2 control without resizing or modifying the source pixels.

    For lossless PNG this preserves every L/RGB pixel after the explicit mode
    and orientation checks.  JPEG follows the same quality/option settings as
    the v1 publisher and therefore measures only normal JPEG encoding loss.
    """
    fmt = _validate_output_format(output_format)
    im, _mode = _load_residual(data)
    encoded = _encode_image(im, fmt)
    profile = _residual_profile(im, fmt, watermark="disabled")
    profile["strength_applied"] = False
    return encoded, profile


def diagnose_image_residual(data: bytes, content_id: str, profile: Any = None,
                            output_format: str = "PNG") -> dict[str, Any]:
    """Return offline residual diagnostics without changing blind detection.

    ``profile`` may be the profile name or returned profile mapping.  For
    compatibility with the initial three-argument candidate API, a third
    positional ``"PNG"``/``"JPEG"`` value is interpreted as output format.
    """
    if isinstance(profile, str) and profile.upper() in ("PNG", "JPEG", "JPG"):
        if output_format != "PNG":
            raise ValueError("profile 与 output_format 参数冲突")
        output_format, profile = profile, None
    profile_name = profile.get("embed_profile") if isinstance(profile, dict) else profile
    if profile_name not in (None, _V2_PROFILE, "v2-residual", "watermark-v2"):
        raise ValueError("diagnose_image_residual 仅支持 watermark-v2-residual profile")
    cid = _check_cid(content_id)
    fmt = _validate_output_format(output_format)
    im, mode = _load_residual(data)
    encoded = _RS.encode(_payload(cid))
    target_bits = _bits(bytes(encoded))
    work_gray, _ = _work_gray(im)
    modified = _embed_gray(work_gray, target_bits, clip_output=False)
    outim, residual_stats = _residual_marked_image(im, mode, target_bits)
    pre_encode_gray, _ = _work_gray(outim)
    final_bytes = _encode_image(outim, fmt)
    final_im, _ = _load_residual(final_bytes)
    final_gray, _ = _work_gray(final_im)
    positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1) for c in range(1, WORK_SIZE // 8 - 1)]
    target_coeffs: list[dict[str, Any]] = []
    observed_bits = []
    pre_encode_bits = []
    coeff_errors = []
    pre_encode_errors = []
    failures: list[dict[str, int]] = []
    index = 0
    for bit_index, bit in enumerate(target_bits):
        repeats = []
        pre_encode_repeats = []
        for repeat_index in range(_REPEAT):
            br, bc = positions[index]; index += 1
            sl = (slice(br * 8, br * 8 + 8), slice(bc * 8, bc * 8 + 8))
            target_coeff = dctn(modified[sl], type=2, norm="ortho")
            observed_coeff = dctn(final_gray[sl], type=2, norm="ortho")
            pre_encode_coeff = dctn(pre_encode_gray[sl], type=2, norm="ortho")
            target_diff = float(target_coeff[2, 3] - target_coeff[3, 2])
            observed_diff = float(observed_coeff[2, 3] - observed_coeff[3, 2])
            pre_encode_diff = float(pre_encode_coeff[2, 3] - pre_encode_coeff[3, 2])
            observed_bit = int(observed_diff >= 0)
            repeats.append(observed_bit)
            pre_encode_repeats.append(int(pre_encode_diff >= 0))
            coeff_errors.append(abs(observed_diff - target_diff))
            pre_encode_errors.append(abs(pre_encode_diff - target_diff))
            target_coeffs.append({"bit_index": bit_index, "repeat_index": repeat_index,
                                 "position": [br, bc], "target_difference": target_diff,
                                 "desired_difference": float(_MAGNITUDE if bit else -_MAGNITUDE),
                                 "pre_encode_difference": pre_encode_diff,
                                 "observed_difference": observed_diff,
                                 "absolute_error": abs(observed_diff - target_diff)})
        observed_bits.append(int(sum(repeats) >= (_REPEAT / 2)))
        pre_encode_bits.append(int(sum(pre_encode_repeats) >= (_REPEAT / 2)))
        if observed_bits[-1] != int(bit):
            failures.append({"bit_index": bit_index, "expected": int(bit), "observed": observed_bits[-1]})
    def stage_report(bits: list[int], errors: list[float]) -> dict[str, Any]:
        codeword = np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()
        failed = [{"bit_index": int(i), "expected": int(target_bits[i]), "observed": int(bit)}
                  for i, bit in enumerate(bits) if bit != int(target_bits[i])]
        decoded = None
        recovered = None
        corrected_symbols = None
        try:
            decoded_result = _RS.decode(codeword)
            decoded = bytes(decoded_result[0])
            corrected_symbols = len(decoded_result[2])
            if (len(decoded) == 21 and decoded[0] == VERSION
                    and zlib.crc32(decoded[:17]).to_bytes(4, "big") == decoded[17:]):
                recovered = decoded[1:17].hex()
        except Exception:
            pass
        return {
            "mean_abs_coefficient_error": float(np.mean(errors)),
            "max_abs_coefficient_error": float(np.max(errors)),
            "bit_errors_before_ecc": len(failed),
            "byte_errors_before_ecc": sum(a != b for a, b in zip(codeword, bytes(encoded))),
            "codeword_exact_before_ecc": codeword == bytes(encoded),
            "ecc_decoded": recovered is not None,
            "ecc_matches_expected": recovered == cid,
            "bit_errors_after_ecc": (int(np.count_nonzero(_bits(decoded) != _bits(_payload(cid))))
                                     if decoded is not None and len(decoded) == 21 else None),
            "corrected_symbols": corrected_symbols,
            "recovered_content_id": recovered,
            "failure_positions": failed,
        }
    before_encode = stage_report(pre_encode_bits, pre_encode_errors)
    after_encode = stage_report(observed_bits, coeff_errors)
    return {
        "embed_profile": _V2_PROFILE,
        "content_id": cid,
        "output_format": fmt,
        "work_size": WORK_SIZE,
        "target_coefficients": target_coeffs,
        "work_domain": {
            "coefficient_count": len(target_coeffs),
            "target_is_unclipped_float": True,
            "modified_min": float(np.min(modified)),
            "modified_max": float(np.max(modified)),
            "mean_abs_residual": float(np.mean(np.abs(modified - work_gray))),
            "max_abs_residual": float(np.max(np.abs(modified - work_gray))),
        },
        "pre_encode": before_encode,
        "post_encode": after_encode,
        "final_reread": after_encode,
        "stage_definitions": {
            "pre_encode": "original-resolution image after clipping/uint8, before codec",
            "post_encode": "final encoded bytes decoded with the same blind work transform",
        },
        "clipping": {k: residual_stats[k] for k in ("clipped_pixels", "total_pixels", "clipped_fraction")},
    }


def encode_without_watermark(data: bytes, output_format: str = "PNG") -> tuple[bytes, dict[str, Any]]:
    """生成 A/B/C 质量对照中的 B（同处理链、无承载系数修改）。

    该入口刻意复用发布路径的解码、512x512 工作平面、恢复原始几何、
    截断和编码参数，仅跳过 ``_embed_gray``。它不会改变生产
    :func:`embed_image` 的行为，也不向检测器泄漏任何真值。
    """
    im, mode = _load(data)
    work_gray, _ = _work_gray(im)
    outim = _make_image(im, mode, work_gray)
    fmt = output_format.upper()
    if fmt not in ("PNG", "JPEG", "JPG"):
        raise ValueError("output_format 仅支持 PNG 或 JPEG")
    bio = io.BytesIO()
    outim.save(bio, format="JPEG" if fmt in ("JPEG", "JPG") else "PNG", quality=95, optimize=False)
    profile = {"algorithm": "dct-difference", "version": VERSION, "work_size": WORK_SIZE,
               "ecc": "not-applicable", "dct_backend": _DCT_BACKEND, "dct_mode": _DCT_MODE.get(), "repeat": _REPEAT,
               "strength": _MAGNITUDE, "strength_applied": False, "watermark": "disabled",
               "input_size": [im.width, im.height],
               "output_format": "JPEG" if fmt in ("JPEG", "JPG") else "PNG"}
    return bio.getvalue(), profile


# Descriptive alias used by quality tooling and callers that prefer the
# control name to the encoding-oriented name.
embed_image_without_watermark = encode_without_watermark
embed_image_no_watermark = encode_without_watermark


def _block_votes(gray: np.ndarray) -> np.ndarray:
    positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1) for c in range(1, WORK_SIZE // 8 - 1)]
    selected = positions[: _HEADER_BITS * _REPEAT]
    if _DCT_MODE.get() == "batch":
        blocks = np.stack([gray[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8] for br, bc in selected])
        coeffs = _dct_blocks(blocks)
        vals = (coeffs[:, 2, 3] - coeffs[:, 3, 2] >= 0).astype(np.uint8).tolist()
    else:
        vals = []
        for br, bc in selected:
            block = gray[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8]
            coeff = _dct_blocks(block)
            vals.append(1 if coeff[2, 3] - coeff[3, 2] >= 0 else 0)
    return np.asarray(vals, dtype=np.uint8).reshape(_HEADER_BITS, _REPEAT).astype(bool)


def _detect_bits(gray: np.ndarray) -> np.ndarray:
    return _block_votes(gray).sum(axis=1) >= (_REPEAT / 2)


def _candidate(im: Image.Image) -> str | None:
    gray, _ = _work_gray(im)
    return _candidate_gray(gray)


def _candidate_gray(gray: np.ndarray, valid_mask: np.ndarray | None = None) -> str | None:
    erased = []
    if valid_mask is None:
        bits = _detect_bits(gray)
    else:
        # A cut-off repeated block contains invented fill pixels. Do not count
        # those as votes; fully missing bits mark their RS byte as an erasure.
        positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1)
                     for c in range(1, WORK_SIZE // 8 - 1)]
        rows, columns = np.asarray(positions[: _HEADER_BITS * _REPEAT]).T
        blocks = valid_mask.reshape(64, 8, 64, 8).transpose(0, 2, 1, 3)
        visible = (blocks[rows, columns].min(axis=(-2, -1)) >= 0.999).reshape(_HEADER_BITS, _REPEAT)
        counts = visible.sum(axis=1)
        bits = (_block_votes(gray) & visible).sum(axis=1) >= (counts / 2)
        erased = np.flatnonzero((counts == 0).reshape(_CODEWORD_BYTES, 8).any(axis=1)).tolist()
        if erased and (_ECC_BACKEND != "reedsolo" or len(erased) > 32):
            return None
    raw = np.packbits(bits).tobytes()
    # Majority bits can produce 53 bytes; RS decoder expects exactly that.
    try:
        decoded = (_RS.decode(raw, erase_pos=erased) if erased else _RS.decode(raw))[0]
        if len(decoded) != 21 or decoded[0] != VERSION:
            return None
        body, crc = decoded[:17], decoded[17:]
        if zlib.crc32(body).to_bytes(4, "big") != crc:
            return None
        return body[1:17].hex()
    except Exception:
        return None


def detect_image(data: bytes) -> dict[str, Any]:
    """盲检测正交方向与任意角度边界校正；汇总所有有效编号。

    方向名称表示检测时尝试的校正操作，不证明图片实际经历的变换。
    不改变嵌入格式，不需要原图、ContentID、真实角度或登记库。
    """
    try:
        im, _ = _load(data)
    except ValueError as exc:
        return {"status": "NOT_FOUND", "content_ids": [], "details": {"error": str(exc)}}
    if _DIRECT_DETECTION.get():
        cid = _candidate(im)
        if cid is None:
            return {"status": "NOT_FOUND", "content_ids": [],
                    "details": {"profile": "dct-difference-v1", "dct_mode": _DCT_MODE.get()}}
        return {"status": "FOUND", "content_ids": [cid],
                "details": {"profile": "dct-difference-v1", "dct_mode": _DCT_MODE.get(), "work_size": WORK_SIZE}}
    gray, _ = _work_gray(im)
    checked = []
    matches = []
    geometry_checked = []

    def inspect(matrix, mask=None, geometry=None):
        for mirrored in (False, True):
            base = np.fliplr(matrix) if mirrored else matrix
            valid = np.fliplr(mask) if mirrored and mask is not None else mask
            for quarter_turns in range(4):
                name = f"{'mirror_' if mirrored else ''}rotate_{quarter_turns * 90}"
                if geometry is None:
                    checked.append(name)
                rotated_mask = np.rot90(valid, quarter_turns) if valid is not None else None
                cid = _candidate_gray(np.rot90(base, quarter_turns), rotated_mask)
                if cid is not None:
                    match = {"orientation": name, "content_id": cid}
                    if geometry is not None:
                        match["geometry"] = geometry
                    matches.append(match)

    inspect(gray)
    # Do not short-circuit after a match: a second valid payload must still
    # produce CONFLICT, including across geometric hypotheses.
    for corrected, valid_mask, geometry in rotation_candidates(im):
        geometry_checked.append(geometry)
        normalized, _ = _work_gray(corrected)
        mask = (np.asarray(valid_mask.resize((WORK_SIZE, WORK_SIZE), Image.Resampling.BOX), dtype=np.float32) / 255
                if valid_mask is not None else None)
        inspect(normalized, mask, geometry)
    ids = sorted({match["content_id"] for match in matches})
    status = "FOUND" if len(ids) == 1 else ("CONFLICT" if len(ids) > 1 else "NOT_FOUND")
    return {"status": status, "content_ids": ids,
            "details": {"profile": "dct-difference-v1", "dct_mode": _DCT_MODE.get(), "work_size": WORK_SIZE,
                        "orientations_checked": checked, "matches": matches,
                        "rotation_recovery": {"backend": GEOMETRY_BACKEND,
                                              "candidate_limit": MAX_ROTATION_CANDIDATES,
                                              "candidates_checked": geometry_checked}}}
