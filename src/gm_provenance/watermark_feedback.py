"""编码后反馈水印候选。

``watermark-v2-feedback`` 保持 v2-residual 的载荷和盲检测器，只在最终编码
重新读取后修正判决裕量不足的系数。每轮都从原始图像和累计浮点扰动重建，
不会把上一轮 JPEG 作为下一轮原图。该模块是实验候选，不改变默认 profile。
"""
from __future__ import annotations

import io
import zlib
from typing import Any

import numpy as np
from PIL import Image

from . import watermark as _wm


PROFILE = "watermark-v2-feedback"
MAX_FEEDBACK_ROUNDS = 3
FEEDBACK_STEP = 4.0
MIN_SIGNED_MARGIN = 8.0
MAX_MEAN_ABS_RESIDUAL = 8.0
# The v2 residual itself can reach about 121 on dark carriers after the
# original-resolution Lanczos mapping; the budget leaves bounded headroom for
# three fixed feedback steps without permitting unbounded edits.
MAX_PEAK_ABS_RESIDUAL = 160.0
MAX_CLIPPED_FRACTION = 0.08


class WatermarkFeedbackError(ValueError):
    """最终编码无法盲恢复时携带离线诊断和最后字节。"""

    def __init__(self, message: str, diagnostics: dict[str, Any], final_bytes: bytes):
        super().__init__(message)
        self.diagnostics = diagnostics
        self.final_bytes = final_bytes


def _positions() -> list[tuple[int, int]]:
    return [(r, c) for r in range(1, _wm.WORK_SIZE // 8 - 1)
            for c in range(1, _wm.WORK_SIZE // 8 - 1)]


def _carrier_from_delta(im: Image.Image, mode: str, delta_work: np.ndarray) -> tuple[Image.Image, dict[str, float]]:
    """将累计工作域浮点扰动映射回原分辨率，再执行唯一一次量化。"""
    full = np.asarray(
        Image.fromarray(delta_work.astype(np.float32), mode="F").resize(
            im.size, Image.Resampling.LANCZOS
        ),
        dtype=np.float32,
    )
    if mode == "L":
        original = np.asarray(im, dtype=np.float32)
        candidate = original + full
        clip_mask = (candidate < 0) | (candidate > 255)
        out = Image.fromarray(np.clip(candidate, 0, 255).astype(np.uint8), mode="L")
    else:
        original = np.asarray(im.convert("RGB"), dtype=np.float32)
        candidate = original + full[..., None]
        clip_mask = np.any((candidate < 0) | (candidate > 255), axis=2)
        out = Image.fromarray(np.clip(candidate, 0, 255).astype(np.uint8), mode="RGB")
    return out, {
        "clipped_pixels": int(np.count_nonzero(clip_mask)),
        "total_pixels": int(clip_mask.size),
        "clipped_fraction": float(np.mean(clip_mask)) if clip_mask.size else 0.0,
        "cumulative_mean_abs_residual": float(np.mean(np.abs(full))) if full.size else 0.0,
        "cumulative_peak_abs_residual": float(np.max(np.abs(full))) if full.size else 0.0,
    }


def _decode_bits(bits: np.ndarray, target_encoded: bytes, cid: str) -> dict[str, Any]:
    codeword = np.packbits(bits.astype(np.uint8)).tobytes()
    failed = [{"bit_index": int(i), "expected": int(target_encoded[i // 8] >> (7 - i % 8) & 1),
               "observed": int(b)}
              for i, b in enumerate(bits)
              if int(b) != int(target_encoded[i // 8] >> (7 - i % 8) & 1)]
    result: dict[str, Any] = {
        "bit_errors_before_ecc": len(failed),
        "byte_errors_before_ecc": sum(a != b for a, b in zip(codeword, target_encoded)),
        "byte_error_positions": [int(i) for i, (a, b) in enumerate(zip(codeword, target_encoded)) if a != b],
        "codeword_exact_before_ecc": codeword == target_encoded,
        "failure_positions": failed,
        "ecc_decoded": False,
        "ecc_matches_expected": False,
        "recovered_content_id": None,
        "corrected_symbols": None,
        "crc_valid": False,
        "decoded_payload_hex": None,
    }
    try:
        decoded_result = _wm._RS.decode(codeword)
        decoded = bytes(decoded_result[0])
        result["corrected_symbols"] = len(decoded_result[2])
        result["decoded_payload_hex"] = decoded.hex()
        if len(decoded) == 21 and decoded[0] == _wm.VERSION:
            body, crc = decoded[:17], decoded[17:]
            result["crc_valid"] = zlib.crc32(body).to_bytes(4, "big") == crc
            if result["crc_valid"]:
                recovered = body[1:17].hex()
                result["recovered_content_id"] = recovered
                result["ecc_decoded"] = True
                result["ecc_matches_expected"] = recovered == cid
                result["bit_errors_after_ecc"] = int(
                    np.count_nonzero(_wm._bits(decoded) != _wm._bits(_wm._payload(cid)))
                )
    except Exception as exc:  # decoder failures are part of diagnostics, not exceptions
        result["ecc_error"] = type(exc).__name__
    return result


def _observe(encoded_bytes: bytes, target_bits: np.ndarray, target_encoded: bytes,
             cid: str, fmt: str, *, include_coefficients: bool) -> dict[str, Any]:
    final_im, _ = _wm._load_residual(encoded_bytes)
    gray, _ = _wm._work_gray(final_im)
    positions = _positions()
    observations: list[dict[str, Any]] = []
    bit_values: list[int] = []
    signed_margins: list[float] = []
    idx = 0
    for bit_index, target in enumerate(target_bits):
        repeats: list[int] = []
        margins: list[float] = []
        for repeat_index in range(_wm._REPEAT):
            br, bc = positions[idx]
            idx += 1
            block = gray[br * 8:br * 8 + 8, bc * 8:bc * 8 + 8]
            coeff = _wm.dctn(block, type=2, norm="ortho")
            difference = float(coeff[2, 3] - coeff[3, 2])
            signed = difference if int(target) else -difference
            repeats.append(int(difference >= 0))
            margins.append(signed)
            signed_margins.append(signed)
            if include_coefficients:
                observations.append({
                    "bit_index": bit_index, "repeat_index": repeat_index,
                    "position": [br, bc], "target_bit": int(target),
                    "observed_difference": difference, "signed_margin": signed,
                })
        bit_values.append(int(sum(repeats) >= (_wm._REPEAT / 2)))
    ecc = _decode_bits(np.asarray(bit_values, dtype=np.uint8), target_encoded, cid)
    observed = _wm.detect_image(encoded_bytes)
    return {
        "output_format": fmt,
        "bit_values": bit_values,
        "signed_margins": signed_margins,
        "coefficients": observations,
        "ecc": ecc,
        "detector": observed,
        "mean_signed_margin": float(np.mean(signed_margins)),
        "minimum_signed_margin": float(np.min(signed_margins)),
    }


def _profile(im: Image.Image, fmt: str) -> dict[str, Any]:
    return {
        "algorithm": "dct-difference-feedback",
        "embed_profile": PROFILE,
        "payload_version": _wm.VERSION,
        "decoder_version": _wm.VERSION,
        "work_size": _wm.WORK_SIZE,
        "ecc": "reed-solomon-rs(53,21)" if _wm._ECC_BACKEND == "reedsolo" else "crc-only-fallback (RS unavailable)",
        "dct_backend": _wm._DCT_BACKEND,
        "repeat": _wm._REPEAT,
        "strength": _wm._MAGNITUDE,
        "input_size": [im.width, im.height],
        "input_mode": im.mode,
        "output_format": fmt,
        "feedback": {
            "max_rounds": MAX_FEEDBACK_ROUNDS,
            "fixed_step": FEEDBACK_STEP,
            "minimum_signed_margin": MIN_SIGNED_MARGIN,
            "quality_budget": {
                "max_mean_abs_residual": MAX_MEAN_ABS_RESIDUAL,
                "max_peak_abs_residual": MAX_PEAK_ABS_RESIDUAL,
                "max_clipped_fraction": MAX_CLIPPED_FRACTION,
            },
            "update_rule": "only coefficients below signed margin; cumulative float residual",
            "jpeg_reencoding_accumulation": False,
        },
    }


def _run_feedback(data: bytes, content_id: str, output_format: str, *, detail: bool) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    cid = _wm._check_cid(content_id)
    fmt = _wm._validate_output_format(output_format)
    im, mode = _wm._load_residual(data)
    encoded_payload = bytes(_wm._RS.encode(_wm._payload(cid)))
    target_bits = _wm._bits(encoded_payload)
    work_gray, _ = _wm._work_gray(im)
    initial = _wm._embed_gray(work_gray, target_bits, clip_output=False)
    delta_work = initial.astype(np.float32) - work_gray.astype(np.float32)
    rounds: list[dict[str, Any]] = []
    detailed_rounds: list[dict[str, Any]] = []
    final_bytes = b""
    final_obs: dict[str, Any] = {}
    budget_failure: str | None = None
    for round_index in range(MAX_FEEDBACK_ROUNDS + 1):
        outim, residual_stats = _carrier_from_delta(im, mode, delta_work)
        if residual_stats["cumulative_mean_abs_residual"] > MAX_MEAN_ABS_RESIDUAL:
            budget_failure = "quality budget exceeded: mean absolute residual"
        if residual_stats["cumulative_peak_abs_residual"] > MAX_PEAK_ABS_RESIDUAL:
            budget_failure = "quality budget exceeded: peak residual"
        if residual_stats["clipped_fraction"] > MAX_CLIPPED_FRACTION:
            budget_failure = "quality budget exceeded: clipping fraction"
        final_bytes = _wm._encode_image(outim, fmt)
        obs = _observe(final_bytes, target_bits, encoded_payload, cid, fmt, include_coefficients=detail)
        final_obs = obs
        detector = obs["detector"]
        recovered = list(detector.get("content_ids") or [])
        success = detector.get("status") == "FOUND" and recovered == [cid]
        summary = {
            "round": round_index,
            "detector_status": detector.get("status"),
            "recovered_content_ids": recovered,
            "ecc_matches_expected": bool(obs["ecc"]["ecc_matches_expected"]),
            "bit_errors_before_ecc": int(obs["ecc"]["bit_errors_before_ecc"]),
            "byte_errors_before_ecc": int(obs["ecc"]["byte_errors_before_ecc"]),
            "minimum_signed_margin": obs["minimum_signed_margin"],
            "mean_signed_margin": obs["mean_signed_margin"],
            "updated_coefficients": 0,
            **residual_stats,
        }
        rounds.append(summary)
        if detail:
            detailed_rounds.append({**summary, "ecc": obs["ecc"], "coefficients": obs["coefficients"]})
        if success and budget_failure is None:
            break
        if budget_failure:
            break
        if round_index >= MAX_FEEDBACK_ROUNDS:
            break
        correction = np.zeros_like(delta_work, dtype=np.float32)
        idx = 0
        changed = 0
        # Use post-encoding coefficients. Each block is updated at most once in
        # this round, and already adequate signed margins remain untouched.
        for bit_index, target in enumerate(target_bits):
            for repeat_index in range(_wm._REPEAT):
                br, bc = _positions()[idx]; idx += 1
                block = final_obs["coefficients"][idx - 1] if detail else None
                # Recompute the observed coefficient for the compact path.
                if block is None:
                    gray, _ = _wm._work_gray(_wm._load_residual(final_bytes)[0])
                    sl = (slice(br * 8, br * 8 + 8), slice(bc * 8, bc * 8 + 8))
                    coeff = _wm.dctn(gray[sl], type=2, norm="ortho")
                    difference = float(coeff[2, 3] - coeff[3, 2])
                else:
                    difference = float(block["observed_difference"])
                signed = difference if int(target) else -difference
                if signed >= MIN_SIGNED_MARGIN:
                    continue
                corr_coeff = np.zeros((8, 8), dtype=np.float32)
                signed_step = FEEDBACK_STEP if int(target) else -FEEDBACK_STEP
                corr_coeff[2, 3] = signed_step / 2.0
                corr_coeff[3, 2] = -signed_step / 2.0
                correction[br * 8:br * 8 + 8, bc * 8:bc * 8 + 8] += _wm.idctn(
                    corr_coeff, type=2, norm="ortho"
                )
                changed += 1
        rounds[-1]["updated_coefficients"] = changed
        delta_work += correction
        if changed == 0:
            break
    diagnostics: dict[str, Any] = {
        "embed_profile": PROFILE,
        "content_id": cid,
        "output_format": fmt,
        "rounds": detailed_rounds if detail else rounds,
        "final": final_obs["ecc"] if final_obs else {},
        "budget_failure": budget_failure,
        "final_bytes_length": len(final_bytes),
        "target_payload": {
            "version": _wm.VERSION,
            "ecc": "RS(53,21)",
            "content_id": cid,
            "codeword_bytes": len(encoded_payload),
        },
    }
    public = _profile(im, fmt)
    public["feedback"]["rounds"] = rounds
    public["feedback"]["rounds_used"] = len(rounds)
    public["feedback"]["final_status"] = final_obs.get("detector", {}).get("status")
    public["feedback"]["final_recovered_content_ids"] = list(final_obs.get("detector", {}).get("content_ids") or [])
    public["feedback"]["budget_failure"] = budget_failure
    if budget_failure or not (final_obs.get("detector", {}).get("status") == "FOUND"
                              and list(final_obs.get("detector", {}).get("content_ids") or []) == [cid]):
        reason = budget_failure or "最终编码的盲检测未恢复唯一 ContentID"
        raise WatermarkFeedbackError(reason, diagnostics, final_bytes)
    return final_bytes, public, diagnostics


def embed_image_feedback(data: bytes, content_id: str, output_format: str = "PNG") -> tuple[bytes, dict[str, Any]]:
    """编码后反馈嵌入，成功才返回最终编码字节和公开 profile。"""
    encoded, profile, _ = _run_feedback(data, content_id, output_format, detail=False)
    return encoded, profile


def diagnose_image_feedback(data: bytes, content_id: str, profile: Any = None,
                            output_format: str = "PNG") -> dict[str, Any]:
    """返回离线详细反馈诊断；失败时仍返回异常中保留的最终字节信息。

    第三个位置参数兼容 residual 诊断约定：传 ``"PNG"``/``"JPEG"``
    时按输出格式解释，传 profile 字典时只校验 profile 名称。
    """
    if isinstance(profile, str) and profile.upper() in ("PNG", "JPEG", "JPG"):
        if output_format != "PNG":
            raise ValueError("profile 与 output_format 参数冲突")
        output_format, profile = profile, None
    profile_name = profile.get("embed_profile") if isinstance(profile, dict) else profile
    if profile_name not in (None, PROFILE):
        raise ValueError("diagnose_image_feedback 仅支持 watermark-v2-feedback profile")
    try:
        _encoded, _profile_out, diagnostics = _run_feedback(data, content_id, output_format, detail=True)
        return diagnostics
    except WatermarkFeedbackError as exc:
        diagnostics = dict(exc.diagnostics)
        diagnostics["failed"] = True
        diagnostics["final_bytes"] = exc.final_bytes
        return diagnostics


__all__ = ["PROFILE", "WatermarkFeedbackError", "embed_image_feedback", "diagnose_image_feedback"]
