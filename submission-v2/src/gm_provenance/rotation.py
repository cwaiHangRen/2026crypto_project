"""v1 盲水印检测的有限几何候选，不修改水印载荷。

利用旋转后留下的透明或近似纯色边框估计矩形；裁切模式仅尝试原画布
尺寸及其宽高交换，不声称恢复任意裁剪。所有候选均需水印校验确认。
"""
from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import numpy as np
from PIL import Image

try:
    from scipy.spatial import ConvexHull, QhullError
    GEOMETRY_BACKEND = "scipy-convex-hull"
except ImportError:
    ConvexHull = None
    QhullError = ValueError
    GEOMETRY_BACKEND = "unavailable"

MAX_ROTATION_CANDIDATES = 8
MAX_GEOMETRY_PIXELS = 16 * 1024 * 1024
MAX_GEOMETRY_SIDE = 8192
_THRESHOLDS = (4, 12, 24, 48)
_OFFSETS = (0, -1)
_MIN_ANGLE = math.radians(0.02)


def _foreground(image: Image.Image, threshold: int) -> tuple[np.ndarray, str] | None:
    """边框必须由输入自身提供；不猜测原图或 ContentID。"""
    if image.mode == "RGBA":
        alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        # PNG 的透明旋转边框不受 RGB 隐藏值影响。
        if np.count_nonzero(alpha < 128) >= image.width * image.height * 0.002:
            return alpha > threshold, "alpha"
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    # 小数角只留下 1..2 像素的角落；5x5 统计会把真实内容误当边框。
    corners = np.asarray([arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]])
    background = np.median(corners, axis=0)
    # JPEG 轻微噪声允许；照片自身不同颜色的四角不触发几何搜索。
    if np.quantile(np.max(np.abs(corners.astype(np.int16) - background), axis=1), 0.9) > 18:
        return None
    distance = np.zeros(arr.shape[:2], dtype=np.uint8)
    for channel in range(3):
        delta = np.abs(arr[..., channel].astype(np.int16) - int(round(background[channel])))
        np.maximum(distance, delta, out=distance, casting="unsafe")
    return distance > threshold, "uniform-border"


def _hull(mask: np.ndarray) -> np.ndarray | None:
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if len(rows) < 32 or len(columns) < 32:
        return None
    points = np.concatenate([
        np.column_stack([mask[rows].argmax(axis=1), rows]),
        np.column_stack([mask.shape[1] - 1 - mask[rows, ::-1].argmax(axis=1), rows]),
        np.column_stack([columns, mask[:, columns].argmax(axis=0)]),
        np.column_stack([columns, mask.shape[0] - 1 - mask[::-1, columns].argmax(axis=0)]),
    ]).astype(np.float64) + 0.5
    try:
        return points[ConvexHull(points).vertices]
    except (QhullError, ValueError):
        return None


def _rectangle(hull: np.ndarray) -> tuple[float, np.ndarray, np.ndarray, float]:
    edges = np.roll(hull, -1, axis=0) - hull
    angles = np.unique((np.arctan2(edges[:, 1], edges[:, 0]) + math.pi / 4) %
                       (math.pi / 2) - math.pi / 4)
    best = None
    for theta in angles:
        c, s = math.cos(theta), math.sin(theta)
        uv = hull @ np.array([[c, -s], [s, c]])
        low, high = uv.min(axis=0), uv.max(axis=0)
        size = high - low + 1.0
        area = float(size.prod())
        if best is None or area < best[3]:
            center = ((high + low) / 2) @ np.array([[c, s], [-s, c]])
            best = (float(theta), size, center, area)
    return best


def _clipped_angle(hull: np.ndarray, size: tuple[int, int]) -> float | None:
    """排除画布四边后，原矩形残留斜边仍有共同的模 90 度方向。"""
    edges = np.roll(hull, -1, axis=0) - hull
    lengths = np.linalg.norm(edges, axis=1)
    angles = (np.arctan2(edges[:, 1], edges[:, 0]) + math.pi / 4) % (math.pi / 2) - math.pi / 4
    eligible = (lengths > min(size) * 0.12) & (np.abs(angles) > _MIN_ANGLE)
    if np.count_nonzero(eligible) < 2:
        return None
    angles, lengths = angles[eligible], lengths[eligible]
    reference = angles[np.argmax(lengths)]
    distance = np.abs((angles - reference + math.pi / 4) % (math.pi / 2) - math.pi / 4)
    consistent = distance < math.radians(0.5)
    if np.count_nonzero(consistent) < 2:
        return None
    angles, weights = angles[consistent], lengths[consistent] ** 2
    theta = math.atan2(float(np.sum(weights * np.sin(4 * angles))),
                       float(np.sum(weights * np.cos(4 * angles)))) / 4
    return theta if abs(theta) > _MIN_ANGLE else None


def _rectify(image: Image.Image, theta: float, size: tuple[int, int],
             center: np.ndarray | tuple[float, float]) -> Image.Image:
    w, h = size
    c, s = math.cos(theta), math.sin(theta)
    cx, cy = center
    matrix = (c, -s, cx - c * w / 2 + s * h / 2,
              s, c, cy - s * w / 2 - c * h / 2)
    return image.transform(size, Image.Transform.AFFINE, matrix, Image.Resampling.BICUBIC)


def rotation_candidates(image: Image.Image) -> Iterator[tuple[Image.Image, Image.Image | None, dict[str, Any]]]:
    """生成最多 8 个有限候选；mask 为候选原生尺寸的 L 模式 0..255 可见度。

    检测端仍须逐候选汇总全部八个正交方向。依赖不可用或边界不可识别时
    返回空迭代，让已有正交检测继续工作。
    """
    if (ConvexHull is None or min(image.size) < 64 or max(image.size) > MAX_GEOMETRY_SIDE
            or image.width * image.height > MAX_GEOMETRY_PIXELS):
        return
    produced = 0
    seen = set()
    for threshold in _THRESHOLDS:
        foreground = _foreground(image, threshold)
        if foreground is None:
            continue
        mask, border = foreground
        density = float(mask.mean())
        if not 0.15 < density < 0.9998:
            continue
        hull = _hull(mask)
        if hull is None:
            continue
        theta, estimated_size, center, area = _rectangle(hull)
        coverage = float(mask.sum() / area)
        # 完整 expand 画布：最小矩形保留斜边，不接触裁切边界。
        if abs(theta) > _MIN_ANGLE and coverage >= 0.5:
            specifications = [("expanded-border", theta,
                               tuple(max(1, round(v + offset)) for v in estimated_size),
                               center, offset) for offset in _OFFSETS]
        else:
            theta = _clipped_angle(hull, image.size)
            if theta is None:
                continue
            # expand=False 的源尺寸=画布尺寸仅是候选假设，宽高方向可能交换。
            center = (image.width / 2, image.height / 2)
            specifications = [("clipped-canvas", theta, size, center, 0)
                              for size in dict.fromkeys((image.size, image.size[::-1]))]
        for method, theta, size, center, offset in specifications:
            if size[0] * size[1] > MAX_GEOMETRY_PIXELS or min(size) < 64:
                continue
            key = (method, round(theta, 6), size, tuple(round(float(v), 2) for v in center))
            if key in seen:
                continue
            seen.add(key)
            rectified = _rectify(image, theta, size, center)
            valid = None
            if method == "clipped-canvas":
                valid = _rectify(Image.new("L", image.size, 255), theta, size, center)
            details = {"method": method, "correction_degrees": round(math.degrees(theta), 6),
                       "estimated_size": list(size), "threshold": threshold,
                       "size_offset": offset, "border": border,
                       "foreground_coverage": round(coverage, 6),
                       "center": [round(float(v), 4) for v in center]}
            yield rectified, valid, details
            produced += 1
            if produced >= MAX_ROTATION_CANDIDATES:
                return
