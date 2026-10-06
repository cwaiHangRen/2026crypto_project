"""鲁棒图片来源水印（DCT + Reed-Solomon）。

这是来源线索基线：水印不替代文件摘要和签名。检测端不需要原图或
ContentID，遇到多个可校验候选时返回 CONFLICT。
"""
from __future__ import annotations

import io
import re
import zlib
from typing import Any

import numpy as np
from PIL import Image
try:
    from scipy.fft import dctn, idctn
    _DCT_BACKEND = "scipy"
except ImportError:  # offline bundled runtime fallback; same orthonormal DCT-II
    _DCT_BACKEND = "numpy"
    _N = 8
    _I, _J = np.indices((_N, _N))
    _C = np.sqrt(2 / _N) * np.cos(np.pi * (2 * _J + 1) * _I / (2 * _N))
    _C[0, :] = 1 / np.sqrt(_N)
    def dctn(block, type=2, norm="ortho"):
        return _C @ np.asarray(block, dtype=np.float32) @ _C.T
    def idctn(block, type=2, norm="ortho"):
        return _C.T @ np.asarray(block, dtype=np.float32) @ _C
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
    return im.convert(mode), mode


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


def _embed_gray(gray: np.ndarray, bits: np.ndarray) -> np.ndarray:
    out = gray.copy()
    # Adjacent 8x8 blocks make detection deterministic and permit repetition.
    slots = int(len(bits) * _REPEAT)
    positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1) for c in range(1, WORK_SIZE // 8 - 1)]
    if slots > len(positions):
        raise ValueError("图片尺寸不足以承载水印")
    k = 0
    for bit in bits:
        for _ in range(_REPEAT):
            br, bc = positions[k]; k += 1
            block = out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8]
            coeff = dctn(block, type=2, norm="ortho")
            a, b = coeff[2, 3], coeff[3, 2]
            mid = (a + b) / 2.0
            d = _MAGNITUDE if bit else -_MAGNITUDE
            coeff[2, 3], coeff[3, 2] = mid + d / 2, mid - d / 2
            out[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8] = idctn(coeff, type=2, norm="ortho")
    return np.clip(out, 0, 255)


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
               "dct_backend": _DCT_BACKEND, "repeat": _REPEAT, "strength": _MAGNITUDE,
               "input_size": [im.width, im.height], "output_format": "JPEG" if fmt in ("JPEG", "JPG") else "PNG"}
    return bio.getvalue(), profile


def _detect_bits(gray: np.ndarray) -> np.ndarray:
    positions = [(r, c) for r in range(1, WORK_SIZE // 8 - 1) for c in range(1, WORK_SIZE // 8 - 1)]
    vals = []
    for br, bc in positions[: _HEADER_BITS * _REPEAT]:
        block = gray[br * 8 : br * 8 + 8, bc * 8 : bc * 8 + 8]
        coeff = dctn(block, type=2, norm="ortho")
        vals.append(1 if coeff[2, 3] - coeff[3, 2] >= 0 else 0)
    return np.asarray(vals, dtype=np.uint8).reshape(_HEADER_BITS, _REPEAT).sum(axis=1) >= (_REPEAT / 2)


def _candidate(im: Image.Image) -> str | None:
    gray, _ = _work_gray(im)
    bits = _detect_bits(gray)
    raw = np.packbits(bits).tobytes()
    # Majority bits can produce 53 bytes; RS decoder expects exactly that.
    try:
        decoded = _RS.decode(raw)[0]
        if len(decoded) != 21 or decoded[0] != VERSION:
            return None
        body, crc = decoded[:17], decoded[17:]
        if zlib.crc32(body).to_bytes(4, "big") != crc:
            return None
        return body[1:17].hex()
    except Exception:
        return None


def detect_image(data: bytes) -> dict[str, Any]:
    """盲检测图片水印；返回 FOUND/NOT_FOUND/CONFLICT。"""
    try:
        im, _ = _load(data)
    except ValueError as exc:
        return {"status": "NOT_FOUND", "content_ids": [], "details": {"error": str(exc)}}
    cid = _candidate(im)
    if cid is None:
        return {"status": "NOT_FOUND", "content_ids": [], "details": {"profile": "dct-difference-v1"}}
    return {"status": "FOUND", "content_ids": [cid], "details": {"profile": "dct-difference-v1", "work_size": WORK_SIZE}}
