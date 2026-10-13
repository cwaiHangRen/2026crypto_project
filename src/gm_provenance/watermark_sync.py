"""有界盲几何同步检测器（decoder-v2-sync / decoder-v3-sync）。

该模块只接收实际上传的图片字节和公开 :class:`SyncConfig`。它先执行
原有 D0 检测；D0 没有候选时，按公开、固定的面积比例和偏移网格把当前
图片放回 512 工作域，再使用同一 RS/CRC 载荷规则解码。重建画布仅是解码
工作表示，绝不用于文件完整性摘要或签名验证。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from PIL import Image

from . import watermark as _wm


# The inferred-native candidate is deliberately capped.  This is a resource
# bound for the reconstruction workspace, not a new search-budget dimension.
# It keeps a very large upload from turning one geometry hypothesis into an
# unbounded allocation while preserving the original image aspect ratio.
RECONSTRUCTION_MAX_SIDE = 2048


@dataclass(frozen=True)
class SyncConfig:
    """公开且可序列化的同步搜索预算。

    ``crop_area_ratios`` 表示保留面积比例，宽高各使用其平方根；偏移值
    是相对估计完整原生画布宽高的有符号比例（再映射到 512 工作域）。
    枚举顺序固定，禁止按答案或登记库排序。
    """

    max_candidates: int = 128
    max_decode_attempts: int = 128
    max_search_ms: int = 1000
    max_memory_mb: int = 256
    max_return_candidates: int = 8
    crop_area_ratios: tuple[float, ...] = (1.0, 0.98, 0.96, 0.95, 0.94, 0.92, 0.90)
    # Offsets are fractions of the estimated complete native canvas. +/-0.03
    # covers the historical ~1% full-width crop displacement.
    offset_fractions: tuple[float, ...] = (0.0, -0.03, 0.03)
    profile: str = "decoder-v2-sync"
    fill_mode: str = "fixed-gray-128"

    def __post_init__(self) -> None:
        for name in ("max_candidates", "max_decode_attempts", "max_search_ms", "max_memory_mb", "max_return_candidates"):
            try:
                value = int(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 必须为正整数") from exc
            if value <= 0:
                raise ValueError(f"{name} 必须为正数")
            object.__setattr__(self, name, value)
        if self.profile not in ("decoder-v2-sync", "decoder-v3-sync"):
            raise ValueError("仅支持 decoder-v2-sync 或 decoder-v3-sync")
        if self.fill_mode != "fixed-gray-128":
            raise ValueError("fill_mode 仅支持固定灰度 128")
        ratios = tuple(float(x) for x in self.crop_area_ratios)
        offsets = tuple(float(x) for x in self.offset_fractions)
        if not ratios or any(not math.isfinite(x) or not (0.05 <= x <= 1.0) for x in ratios):
            raise ValueError("crop_area_ratios 必须在 [0.05,1] 范围")
        if not offsets or any(not math.isfinite(x) or abs(x) > 1.0 for x in offsets):
            raise ValueError("offset_fractions 必须在 [-1,1] 范围")
        object.__setattr__(self, "crop_area_ratios", ratios)
        object.__setattr__(self, "offset_fractions", offsets)


def _as_config(config: SyncConfig | dict[str, Any] | None) -> SyncConfig:
    if config is None:
        return SyncConfig()
    if isinstance(config, SyncConfig):
        return config
    if isinstance(config, dict):
        allowed = {f.name for f in SyncConfig.__dataclass_fields__.values()}
        return SyncConfig(**{k: v for k, v in config.items() if k in allowed})
    raise TypeError("config 必须是 SyncConfig、字典或 None")


def _decode_work_gray(gray: np.ndarray) -> str | None:
    """在已构造的 512 灰度工作域上解码，不再次 resize/归一化。"""
    arr = np.asarray(gray, dtype=np.float32)
    if arr.shape != (_wm.WORK_SIZE, _wm.WORK_SIZE):
        raise ValueError("工作域必须为 512x512")
    bits = _wm._detect_bits(arr)
    raw = np.packbits(bits.astype(np.uint8)).tobytes()
    try:
        decoded = _wm._RS.decode(raw)[0]
        if len(decoded) != 21 or decoded[0] != _wm.VERSION:
            return None
        body, crc = decoded[:17], decoded[17:]
        if __import__("zlib").crc32(body).to_bytes(4, "big") != crc:
            return None
        return body[1:17].hex()
    except Exception:
        return None


def decode_work_gray(gray: np.ndarray) -> str | None:
    """Decode a pre-built 512x512 work domain without another resize."""
    return _decode_work_gray(gray)


def _image_to_pixels(data: bytes) -> tuple[Image.Image, np.ndarray]:
    im, _ = _wm._load(data)
    # Retain RGB until the reconstructed native canvas is resized. This is the
    # same ordering as the legacy work-domain conversion and avoids making
    # geometry hypotheses depend on a different grayscale interpolation path.
    rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    return im, rgb


def _resize_observed(
    pixels: np.ndarray,
    area_ratio: float,
    *,
    profile: str = "decoder-v2-sync",
) -> Image.Image:
    """Resize one observed image for an area hypothesis.

    Offset hypotheses for the same area ratio share this image.  Keeping this
    operation separate lets the bounded search cache the expensive Lanczos
    resize without changing the public geometry order or reconstruction rule.
    """
    source_arr = np.asarray(pixels)
    if source_arr.ndim not in (2, 3):
        raise ValueError("输入像素必须是二维灰度或三维 RGB")
    side = float(np.sqrt(area_ratio))
    crop_w = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
    crop_h = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
    source = np.clip(source_arr, 0, 255).astype(np.uint8)
    if source_arr.ndim == 3:
        if source_arr.shape[2] != 3:
            raise ValueError("输入像素通道必须是 RGB")
        source_image = Image.fromarray(source, mode="RGB")
    else:
        source_image = Image.fromarray(source, mode="L")
    if profile == "decoder-v3-sync":
        # v3 infers the complete native canvas from the observed crop shape.
        # Preserve those pixels here; the only resize happens after the
        # geometry hypothesis has been placed in the inferred canvas.
        geometry = _inferred_native_geometry(source_image.size, area_ratio, 0.0, 0.0)
        target = (int(geometry["crop_width"]), int(geometry["crop_height"]))
        return source_image if source_image.size == target else source_image.resize(target, Image.Resampling.LANCZOS)
    return source_image.resize((crop_w, crop_h), Image.Resampling.LANCZOS)


def _inferred_native_geometry(
    observed_size: tuple[int, int],
    area_ratio: float,
    ox: float,
    oy: float,
    *,
    max_side: int = RECONSTRUCTION_MAX_SIDE,
) -> dict[str, int | float]:
    """Infer the native crop canvas using only observed shape and area.

    The Round6 oracle creates a native canvas, places the transformed crop at
    ``int((full-crop)/2 + offset*full)`` and then performs one work-domain
    resize.  v3 reproduces that order.  ``round`` recovers the dimensions
    generated by the deterministic transform while ``int`` preserves its
    offset truncation rule.  No expected ID, registry state, or filename is
    consulted.
    """
    obs_w, obs_h = (int(observed_size[0]), int(observed_size[1]))
    if obs_w < 1 or obs_h < 1:
        raise ValueError("observed_size 必须为正数")
    side = float(np.sqrt(float(area_ratio)))
    full_w = max(obs_w, int(round(obs_w / side)))
    full_h = max(obs_h, int(round(obs_h / side)))
    cap = max(8, int(max_side))
    scale = min(1.0, cap / float(max(full_w, full_h)))
    native_w = max(8, int(round(full_w * scale)))
    native_h = max(8, int(round(full_h * scale)))
    crop_w = max(8, int(round(obs_w * scale)))
    crop_h = max(8, int(round(obs_h * scale)))
    left = int((native_w - crop_w) / 2.0 + float(ox) * native_w)
    top = int((native_h - crop_h) / 2.0 + float(oy) * native_h)
    left = max(0, min(max(0, native_w - crop_w), left))
    top = max(0, min(max(0, native_h - crop_h), top))
    return {
        "native_width": native_w,
        "native_height": native_h,
        "crop_width": crop_w,
        "crop_height": crop_h,
        "left": left,
        "top": top,
        "scale": float(scale),
    }


def _candidate_gray(
    pixels: np.ndarray,
    area_ratio: float,
    ox: float,
    oy: float,
    *,
    observed: Image.Image | None = None,
    profile: str = "decoder-v2-sync",
) -> np.ndarray:
    """Map an observed crop into a hypothesized work canvas.

    v2 retains the Round6 fixed-512 reconstruction.  v3 infers a native-size
    canvas first, pastes the observed pixels there, and lets the watermark
    work-domain conversion perform the single final resize.  Both paths use
    only upload pixels and public geometry hypotheses.
    """
    source_arr = np.asarray(pixels)
    if source_arr.ndim not in (2, 3):
        raise ValueError("输入像素必须是二维灰度或三维 RGB")
    if profile not in ("decoder-v2-sync", "decoder-v3-sync"):
        raise ValueError("不支持的同步重建 profile")
    side = float(np.sqrt(area_ratio))
    crop_w = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
    crop_h = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
    # The fill is public and independent of image content.  It matches the
    # geometry-only diagnostic oracle and prevents a crop's observed median
    # from becoming an image-dependent hidden ranking signal.
    if source_arr.ndim == 3:
        if source_arr.shape[2] != 3:
            raise ValueError("输入像素通道必须是 RGB")
        mode = "RGB"
    else:
        mode = "L"
    if observed is None:
        observed = _resize_observed(source_arr, area_ratio, profile=profile)
    if profile == "decoder-v3-sync":
        return _reconstruct_inferred_native(source_arr, area_ratio, ox, oy, observed=observed)
    fill = (128, 128, 128) if mode == "RGB" else 128
    canvas = Image.new(mode, (_wm.WORK_SIZE, _wm.WORK_SIZE), fill)
    if observed.size != (crop_w, crop_h):
        raise ValueError("预缩放候选尺寸与面积假设不一致")
    margin_x = _wm.WORK_SIZE - crop_w
    margin_y = _wm.WORK_SIZE - crop_h
    # Offset is relative to the complete 512 work canvas.
    left = int(round(margin_x / 2.0 + float(ox) * _wm.WORK_SIZE))
    top = int(round(margin_y / 2.0 + float(oy) * _wm.WORK_SIZE))
    left = max(0, min(margin_x, left))
    top = max(0, min(margin_y, top))
    canvas.paste(observed, (left, top))
    return _wm._work_gray(canvas)[0]


def _reconstruct_inferred_native(
    pixels: np.ndarray,
    area_ratio: float,
    ox: float,
    oy: float,
    *,
    observed: Image.Image | None = None,
) -> np.ndarray:
    """Build the v3 native-aspect canvas and convert it once to work gray."""
    source_arr = np.asarray(pixels)
    if source_arr.ndim not in (2, 3):
        raise ValueError("输入像素必须是二维灰度或三维 RGB")
    mode = "RGB" if source_arr.ndim == 3 else "L"
    if mode == "RGB" and source_arr.shape[2] != 3:
        raise ValueError("输入像素通道必须是 RGB")
    geometry = _inferred_native_geometry(
        (int(source_arr.shape[1]), int(source_arr.shape[0])), area_ratio, ox, oy,
    )
    crop_w = int(geometry["crop_width"])
    crop_h = int(geometry["crop_height"])
    if observed is None:
        observed = _resize_observed(source_arr, area_ratio, profile="decoder-v3-sync")
    if observed.size != (crop_w, crop_h):
        observed = observed.resize((crop_w, crop_h), Image.Resampling.LANCZOS)
    fill = (128, 128, 128) if mode == "RGB" else 128
    canvas = Image.new(mode, (int(geometry["native_width"]), int(geometry["native_height"])), fill)
    canvas.paste(observed, (int(geometry["left"]), int(geometry["top"])))
    return _wm._work_gray(canvas)[0]


def reconstruct_work_gray(
    observed: bytes | Image.Image | np.ndarray,
    geometry: dict[str, float],
    *,
    work_size: int = _wm.WORK_SIZE,
    fill_mode: str = "fixed-gray-128",
    interpolation: str = "lanczos",
) -> np.ndarray:
    """Reconstruct one blind, normalized geometry hypothesis.

    ``geometry`` contains only ``crop_area_ratio``, ``offset_x`` and
    ``offset_y``.  This public helper intentionally accepts no source size,
    crop rectangle, ContentID, or registry object.  The adapter that knows a
    transform's truth may convert its rectangle to these normalized values
    for offline comparison; the blind detector uses the same helper through
    its v3 path.
    """
    if int(work_size) != _wm.WORK_SIZE:
        raise ValueError("当前水印工作域固定为 512x512")
    if fill_mode != "fixed-gray-128":
        raise ValueError("仅支持固定灰度 128 填充")
    if interpolation != "lanczos":
        raise ValueError("仅支持 Lanczos 插值")
    try:
        ratio = float(geometry["crop_area_ratio"])
        ox = float(geometry.get("offset_x", 0.0))
        oy = float(geometry.get("offset_y", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("geometry 必须包含 crop_area_ratio、offset_x、offset_y") from exc
    if not math.isfinite(ratio) or not 0.05 <= ratio <= 1.0:
        raise ValueError("crop_area_ratio 必须在 [0.05,1] 范围")
    if not all(math.isfinite(v) and abs(v) <= 1.0 for v in (ox, oy)):
        raise ValueError("offset_x/offset_y 必须在 [-1,1] 范围")
    if isinstance(observed, (bytes, bytearray)):
        _im, pixels = _image_to_pixels(bytes(observed))
    elif isinstance(observed, Image.Image):
        pixels = np.asarray(observed.convert("RGB"), dtype=np.uint8)
    else:
        pixels = np.asarray(observed)
    return _reconstruct_inferred_native(pixels, ratio, ox, oy)


def _geometry_candidates(config: SyncConfig) -> Iterable[dict[str, float]]:
    # Search low-dimensional center hypotheses first, then one-axis offsets,
    # then diagonal offsets. This keeps center 0.90 within the small budget
    # without using labels or expected IDs to rank candidates.
    ratios = tuple(float(x) for x in config.crop_area_ratios)
    offsets = tuple(float(x) for x in config.offset_fractions)
    zero = 0.0 if 0.0 in offsets else offsets[0]
    seen: set[tuple[float, float, float]] = set()

    def emit(ratio: float, ox: float, oy: float) -> dict[str, float] | None:
        key = (ratio, ox, oy)
        if key in seen:
            return None
        seen.add(key)
        return {"crop_area_ratio": ratio, "width_ratio": float(np.sqrt(ratio)),
                "height_ratio": float(np.sqrt(ratio)), "offset_x": ox, "offset_y": oy}

    for ratio in ratios:
        item = emit(ratio, zero, zero)
        if item is not None:
            yield item
    nonzero = tuple(x for x in offsets if x != zero)
    for axis in ("x", "y"):
        for ratio in ratios:
            for delta in nonzero:
                item = emit(ratio, delta if axis == "x" else zero,
                            delta if axis == "y" else zero)
                if item is not None:
                    yield item
    for ratio in ratios:
        for ox in nonzero:
            for oy in nonzero:
                item = emit(ratio, ox, oy)
                if item is not None:
                    yield item


def _base_details(config: SyncConfig) -> dict[str, Any]:
    reconstruction_rule = (
        "infer_native_canvas_from_observed_dimensions_and_area_then_floor_offset_then_work_resize"
        if config.profile == "decoder-v3-sync"
        else "estimate_full_native_from_crop_area_then_fixed_gray_128_fill_then_work_resize"
    )
    return {
        "decoder_profile": config.profile,
        "checked_scope": "watermark-only",
        "search_dct_mode": "batch",
        "reconstruction_rule": reconstruction_rule,
        "reconstruction_mode": "inferred-native-aspect" if config.profile == "decoder-v3-sync" else "legacy-square-work",
        "reconstruction_max_side": RECONSTRUCTION_MAX_SIDE if config.profile == "decoder-v3-sync" else None,
        "search_complete": False,
        "stop_reason": None,
        "geometry_hypotheses": [],
        "budget": {
            "max_candidates": config.max_candidates,
            "max_decode_attempts": config.max_decode_attempts,
            "max_search_ms": config.max_search_ms,
            "max_memory_mb": config.max_memory_mb,
            "max_return_candidates": config.max_return_candidates,
        },
    }


def detect_image_sync(data: bytes, config: SyncConfig | dict[str, Any] | None = None) -> dict[str, Any]:
    """执行 D0 + 有界同步搜索。

    返回状态为 ``FOUND``、``CONFLICT``、``NOT_FOUND`` 或预算/输入错误状态。
    ``search_complete`` 为 False 时，调用方不得将结果解释为已穷尽允许范围。
    """
    cfg = _as_config(config)
    details = _base_details(cfg)
    started = time.perf_counter()
    if not isinstance(data, bytes):
        details.update({"stop_reason": "input_type", "elapsed_ms": 0.0,
                        "error": "detect_image_sync 仅接受 bytes"})
        return {"status": "ERROR", "content_ids": [], "search_complete": False,
                "stop_reason": "input_type", "details": details}
    # D0 is always the first decoder operation. A successful quick path is
    # deliberately incomplete: it has not searched alternative geometries.
    d0_started = time.perf_counter()
    d0 = _wm.detect_image(data)
    details["timing_ms"] = {"d0": round((time.perf_counter() - d0_started) * 1000, 3)}
    if d0.get("status") == "FOUND" and d0.get("content_ids"):
        details.update({"search_complete": False, "stop_reason": "d0_found",
                        "geometry_hypotheses": [{"source": "d0", "geometry": "native-work-domain"}],
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "candidate_count": 0, "decode_attempts": 0})
        return {"status": "FOUND", "content_ids": list(d0["content_ids"]),
                "search_complete": False, "stop_reason": "d0_found", "details": details}
    # Memory preflight precedes synchronization allocations. D0 itself has
    # already run as the mandated quick path.
    estimated_mb = (_wm.WORK_SIZE * _wm.WORK_SIZE * 4 * 3) / (1024 * 1024)
    details["memory_estimate_mb"] = round(estimated_mb, 3)
    if cfg.max_memory_mb * 1024 * 1024 < _wm.WORK_SIZE * _wm.WORK_SIZE * 4 * 2:
        details.update({"stop_reason": "memory_budget", "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return {"status": "ERROR", "content_ids": [], "search_complete": False,
                "stop_reason": "memory_budget", "details": details}
    # Parse once for synchronization. D0 may have returned NOT_FOUND for a
    # malformed image; expose that as an input error rather than a silent miss.
    try:
        _im, gray_source = _image_to_pixels(data)
    except Exception as exc:
        details.update({"stop_reason": "input_error", "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": str(exc)})
        return {"status": "ERROR", "content_ids": [], "search_complete": False,
                "stop_reason": "input_error", "details": details}

    # This is a conservative accounting estimate for the retained input RGB
    # array, the complete ratio-resize cache (offset hypotheses reuse these
    # images), and two 512x512 float work arrays. It is deliberately reported
    # as an estimate rather than a process-wide RSS claim, while still making
    # the configured memory budget enforceable.
    cached_observed_bytes = 0
    max_reconstruction_bytes = 0
    for ratio in cfg.crop_area_ratios:
        if cfg.profile == "decoder-v3-sync":
            geometry = _inferred_native_geometry(
                (int(gray_source.shape[1]), int(gray_source.shape[0])),
                ratio, 0.0, 0.0,
            )
            cached_observed_bytes += int(geometry["crop_width"]) * int(geometry["crop_height"]) * 3
            max_reconstruction_bytes = max(
                max_reconstruction_bytes,
                int(geometry["native_width"]) * int(geometry["native_height"]) * 3,
            )
        else:
            side = float(np.sqrt(ratio))
            crop_w = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
            crop_h = max(8, min(_wm.WORK_SIZE, round(_wm.WORK_SIZE * side)))
            cached_observed_bytes += crop_w * crop_h * 3
    if cfg.profile != "decoder-v3-sync":
        max_reconstruction_bytes = _wm.WORK_SIZE * _wm.WORK_SIZE * 3
    memory_peak_estimate = (
        int(gray_source.nbytes)
        + (_wm.WORK_SIZE * _wm.WORK_SIZE * 4 * 2)
        + cached_observed_bytes
        + max_reconstruction_bytes
    ) / (1024 * 1024)
    details["memory_peak_mb"] = round(memory_peak_estimate, 3)
    details["memory_cache_ratio_count"] = len(cfg.crop_area_ratios)
    details["memory_peak_is_estimate"] = True
    if memory_peak_estimate > cfg.max_memory_mb:
        details.update({"stop_reason": "memory_budget", "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return {"status": "ERROR", "content_ids": [], "search_complete": False,
                "stop_reason": "memory_budget", "details": details}

    candidate_count = 0
    decode_attempts = 0
    hypotheses: dict[str, list[dict[str, float]]] = {}
    seen_geometry: set[tuple[float, float, float]] = set()
    first_candidate_ms: float | None = None
    # The Lanczos resize depends only on the area ratio, not on offsets.  Keep
    # one immutable PIL image per ratio so axis and diagonal hypotheses reuse
    # it under the same bounded search budget.
    resized_observed: dict[float, Image.Image] = {}
    stop_reason = "search_exhausted"
    search_complete = True
    timing = details.setdefault("timing_ms", {})
    timing.update({"geometry_generation": 0.0, "reconstruction": 0.0, "dct_ecc": 0.0})
    sync_started = time.perf_counter()
    for geometry in _geometry_candidates(cfg):
        if candidate_count >= cfg.max_candidates:
            search_complete, stop_reason = False, "candidate_budget_exhausted"
            break
        elapsed_ms = (time.perf_counter() - sync_started) * 1000
        if elapsed_ms >= cfg.max_search_ms:
            search_complete, stop_reason = False, "time_budget_exhausted"
            break
        if decode_attempts >= cfg.max_decode_attempts:
            search_complete, stop_reason = False, "decode_budget_exhausted"
            break
        key = (geometry["crop_area_ratio"], geometry["offset_x"], geometry["offset_y"])
        if key in seen_geometry:
            continue
        seen_geometry.add(key)
        candidate_count += 1
        reconstruct_started = time.perf_counter()
        ratio = geometry["crop_area_ratio"]
        observed = resized_observed.get(ratio)
        if observed is None:
            observed = _resize_observed(gray_source, ratio, profile=cfg.profile)
            resized_observed[ratio] = observed
        work = _candidate_gray(
            gray_source,
            ratio,
            geometry["offset_x"],
            geometry["offset_y"],
            observed=observed,
            profile=cfg.profile,
        )
        timing["reconstruction"] += (time.perf_counter() - reconstruct_started) * 1000
        decode_attempts += 1
        decode_started = time.perf_counter()
        # D0 keeps the legacy scalar backend. Sync candidates use the existing
        # block-batched DCT only after differential testing against scalar;
        # blocks never cross boundaries and payload rules are unchanged.
        with _wm.dct_backend("batch"):
            cid = _decode_work_gray(work)
        timing["dct_ecc"] += (time.perf_counter() - decode_started) * 1000
        if cid is not None:
            hypotheses.setdefault(cid, []).append(geometry)
            if first_candidate_ms is None:
                first_candidate_ms = (time.perf_counter() - started) * 1000
            if len(hypotheses) >= cfg.max_return_candidates:
                # Keep searching only until the configured return bound; this
                # is an explicit bounded result rather than a conflict proof.
                search_complete, stop_reason = False, "return_budget_exhausted"
                break
        # A reconstruction/decode operation is not interruptible, so one
        # candidate can cross the wall-clock boundary. Check immediately after
        # it and report the run as incomplete instead of letting the for/else
        # path claim exhaustive search.
        if (time.perf_counter() - sync_started) * 1000 >= cfg.max_search_ms:
            search_complete, stop_reason = False, "time_budget_exhausted"
            break
    else:
        search_complete, stop_reason = True, "search_exhausted"
    timing["sync_search"] = round((time.perf_counter() - sync_started) * 1000, 3)
    for key in ("geometry_generation", "reconstruction", "dct_ecc"):
        timing[key] = round(float(timing[key]), 3)
    details.update({"search_complete": search_complete, "stop_reason": stop_reason,
                   "candidate_count": candidate_count, "decode_attempts": decode_attempts,
                   "first_candidate_ms": round(first_candidate_ms, 3) if first_candidate_ms is not None else None,
                   "geometry_hypotheses": [{"content_id": cid, "geometries": gs} for cid, gs in hypotheses.items()],
                   "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
    ids = list(hypotheses)[: cfg.max_return_candidates]
    if len(ids) > 1:
        status = "CONFLICT"
    elif len(ids) == 1:
        status = "FOUND"
    elif not search_complete:
        if stop_reason == "time_budget_exhausted":
            status = "TIME_BUDGET_EXHAUSTED"
        elif stop_reason == "decode_budget_exhausted":
            status = "DECODE_BUDGET_EXHAUSTED"
        else:
            status = "CANDIDATE_BUDGET_EXHAUSTED"
    else:
        status = "NOT_FOUND"
    details["service_conclusion"] = "INDETERMINATE" if (not ids and not search_complete) else status
    return {"status": status, "content_ids": ids, "search_complete": search_complete,
            "stop_reason": stop_reason, "details": details}


__all__ = ["SyncConfig", "decode_work_gray", "detect_image_sync", "reconstruct_work_gray"]
