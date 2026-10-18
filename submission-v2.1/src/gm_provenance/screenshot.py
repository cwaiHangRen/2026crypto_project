"""真实截图分析核心：页面定位、有限几何校正及区域级来源证据。"""
from __future__ import annotations

import io
import csv
import json
import threading
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import media
from .crypto import OpenSSLCrypto


@dataclass(frozen=True)
class ScreenshotBudget:
    max_regions: int = 4
    max_candidates: int = 16
    max_pixels_per_candidate: int = 16 * 1024 * 1024
    time_budget_ms: int = 1500
    max_verify_calls: int = 16


@dataclass(frozen=True)
class PageRegion:
    region_id: str
    bbox: tuple[int, int, int, int]
    confidence: float
    source: str = "background-segmentation"
    corners: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class GeometryCandidate:
    candidate_id: str
    region_id: str
    operation: str
    angle: int = 0
    perspective: bool = False


@dataclass
class RegionEvidence:
    candidate: GeometryCandidate
    region: PageRegion
    detector: dict[str, Any]
    credential: dict[str, Any] | None = None
    image_sm3: str | None = None
    pixels: tuple[int, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreenshotAnalysis:
    original_bytes: bytes
    original_sm3: str
    media_type: str
    image_size: tuple[int, int]
    regions: list[PageRegion] = field(default_factory=list)
    evidence: list[RegionEvidence] = field(default_factory=list)
    budget: ScreenshotBudget = field(default_factory=ScreenshotBudget)
    stop_reason: str = "COMPLETE"
    elapsed_ms: float = 0.0

    @property
    def status(self) -> str:
        ids = {cid for e in self.evidence for cid in e.detector.get("content_ids", [])}
        if ids:
            return "FOUND" if len(ids) == 1 else "CONFLICT"
        return "INDETERMINATE" if self.stop_reason != "COMPLETE" else "NOT_FOUND"

    def as_dict(self, include_bytes: bool = False) -> dict[str, Any]:
        out = {"status": self.status, "original_sm3": self.original_sm3,
               "media_type": self.media_type, "image_size": list(self.image_size),
               "regions": [asdict(x) for x in self.regions],
               "evidence": [x.as_dict() for x in self.evidence],
               "budget": asdict(self.budget), "stop_reason": self.stop_reason,
               "elapsed_ms": self.elapsed_ms}
        if include_bytes:
            out["original_bytes"] = self.original_bytes
        return out


def _sm3(data: bytes) -> str:
    return OpenSSLCrypto().sm3(data)


def _load_image(data: bytes) -> Image.Image:
    mt = media.sniff(data)
    if mt not in ("image/png", "image/jpeg"):
        raise ValueError("真实截图必须是 PNG 或 JPEG 图片")
    image = Image.open(io.BytesIO(data)); image.load()
    return image.convert("RGB")


def locate_page_regions(image: Image.Image, *, max_regions: int = 4) -> list[PageRegion]:
    """按背景差异定位页面矩形；无明显差异时退化为全图。"""
    if max_regions <= 0:
        return []
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    gray = np.mean(rgb, axis=2)
    threshold = min(245.0, max(150.0, float(np.percentile(gray, 98)) - 10.0))
    mask = gray < threshold; h, w = mask.shape
    row = mask.sum(axis=1) > max(2, int(w * .004)); col = mask.sum(axis=0) > max(2, int(h * .004))
    if not row.any() or not col.any():
        return [PageRegion("r0", (0, 0, w, h), .2, "full-image-fallback", ((0., 0.), (w, 0.), (w, h), (0., h)))]
    ys, xs = np.flatnonzero(row), np.flatnonzero(col)
    x0, x1, y0, y1 = int(xs[0]), int(xs[-1] + 1), int(ys[0]), int(ys[-1] + 1)
    margin = max(2, int(min(w, h) * .01)); x0, y0 = max(0, x0-margin), max(0, y0-margin)
    x1, y1 = min(w, x1+margin), min(h, y1+margin)
    confidence = float(min(.99, max(.25, ((x1-x0)*(y1-y0))/(w*h) + .25)))
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return [PageRegion("r0", (x0, y0, x1, y1), confidence, "background-segmentation", corners)]


def _perspective(image: Image.Image, corners: tuple[tuple[float, float], ...]) -> Image.Image:
    if len(corners) != 4:
        return image.copy()
    xs, ys = zip(*corners)
    width = max(1, int(max(xs) - min(xs)))
    height = max(1, int(max(ys) - min(ys)))
    # QUAD maps an output rectangle to the finite source quadrilateral.  The
    # detector therefore sees a rectified working image while original bytes
    # remain untouched.  No unbounded homography search is attempted.
    return image.transform((width, height), Image.Transform.QUAD,
                           tuple(v for point in corners for v in point),
                           Image.Resampling.BICUBIC)


def _encode(image: Image.Image) -> bytes:
    out = io.BytesIO(); image.save(out, format="PNG"); return out.getvalue()


def analyze_screenshot(data: bytes, *, service: Any | None = None,
                      budget: ScreenshotBudget | None = None,
                      verify_mode: str = "recover",
                      verify_fn: Callable[[bytes], dict[str, Any]] | None = None) -> ScreenshotAnalysis:
    """返回保留原始截图字节/SM3 的区域级检测和凭证结果。"""
    started = time.perf_counter(); budget = budget or ScreenshotBudget()
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("截图必须是 bytes")
    original = bytes(data); image = _load_image(original)
    result = ScreenshotAnalysis(original, _sm3(original), media.sniff(original),
                                (image.width, image.height), budget=budget)
    result.regions = locate_page_regions(image, max_regions=budget.max_regions)
    candidates = [(r, op, a, p) for r in result.regions
                  for op, a, p in (("crop", 0, False), ("rotate", 90, False),
                                   ("rotate", 270, False), ("perspective", 0, True))]
    calls = 0
    for i, (region, op, angle, persp) in enumerate(candidates[:budget.max_candidates]):
        if (time.perf_counter()-started)*1000 >= budget.time_budget_ms:
            result.stop_reason = "TIME_BUDGET_EXHAUSTED"; break
        if calls >= budget.max_verify_calls:
            result.stop_reason = "VERIFY_BUDGET_EXHAUSTED"; break
        x0, y0, x1, y1 = region.bbox; work = image.crop((x0, y0, x1, y1))
        # A full-image fallback is still a valid region, but encoding that
        # exact image would make the first "crop" candidate byte-identical to
        # the uploaded screenshot.  Remove a one-pixel border in that case so
        # the candidate represents an actual derived viewport while retaining
        # the original bytes and digest in ScreenshotAnalysis.
        if op == "crop" and work.size == image.size and work.width > 2 and work.height > 2:
            work = work.crop((1, 1, work.width - 1, work.height - 1))
        if op == "rotate": work = work.rotate(angle, expand=True)
        elif persp and region.corners: work = _perspective(image, region.corners)
        if work.width * work.height > budget.max_pixels_per_candidate:
            result.stop_reason = "PIXEL_BUDGET_EXHAUSTED"; break
        payload = _encode(work); detector = media.detect(payload, "image/png"); credential = None
        if verify_fn is not None:
            credential = verify_fn(payload); calls += 1
        elif service is not None:
            with tempfile.NamedTemporaryFile(suffix=".png") as fh:
                fh.write(payload); fh.flush(); credential = service.verify(fh.name, None, verify_mode); calls += 1
        result.evidence.append(RegionEvidence(GeometryCandidate(f"{region.region_id}-c{i}", region.region_id, op, angle, persp),
                                               region, detector, credential, _sm3(payload), (work.width, work.height)))
    else:
        if len(candidates) > budget.max_candidates:
            result.stop_reason = "CANDIDATE_BUDGET_EXHAUSTED"
    result.elapsed_ms = round((time.perf_counter()-started)*1000, 3)
    return result


class ScreenshotTaskManager:
    """小型本地任务管理器，供 API/CLI 复用；原始截图落盘后再分析。"""
    def __init__(self, root: str | Any, verifier: Callable[..., dict[str, Any]] | None = None):
        self.root = __import__("pathlib").Path(root) / "screenshots"
        self.root.mkdir(parents=True, exist_ok=True)
        self.verifier = verifier
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock(); self._executor = ThreadPoolExecutor(max_workers=2)

    def submit(self, data: bytes, filename: str, source_kind: str, *, mode: str = "recover") -> dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("截图必须是 bytes")
        task_id = uuid.uuid4().hex; path = self.root / f"{task_id}.bin"; path.write_bytes(bytes(data))
        task = {"task_id": task_id, "status": "queued", "filename": filename,
                "source_kind": source_kind, "mode": mode, "path": str(path), "created_at": time.time()}
        with self._lock: self._tasks[task_id] = task
        self._executor.submit(self._run, task_id)
        return dict(task)

    def _run(self, task_id: str) -> None:
        with self._lock: task = self._tasks.get(task_id); task["status"] = "running" if task else "missing"
        if not task: return
        try:
            raw = __import__("pathlib").Path(task["path"]).read_bytes()
            vf = None
            if self.verifier:
                def vf(payload):
                    with tempfile.NamedTemporaryFile(suffix=".png") as f:
                        f.write(payload); f.flush(); return self.verifier(f.name, None, task["mode"])
            result = analyze_screenshot(raw, verify_fn=vf)
            with self._lock: task.update(result=result.as_dict(), original_sm3=result.original_sm3, status="complete")
        except Exception as exc:
            with self._lock: task.update(status="failed", error=f"{type(exc).__name__}: {exc}")

    def list(self) -> list[dict[str, Any]]:
        with self._lock: return [dict(x) for x in self._tasks.values()]

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks: raise KeyError(task_id)
            return dict(self._tasks[task_id])

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task: raise KeyError(task_id)
            if task["status"] in ("queued", "running"): task["status"] = "cancelled"
            return dict(task)

    def delete(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task: raise KeyError(task_id)
            if task["status"] in ("queued", "running"): raise ValueError("任务仍在运行")
            self._tasks.pop(task_id); __import__("pathlib").Path(task["path"]).unlink(missing_ok=True)
            return {"task_id": task_id, "deleted": True}

    def report(self, task_ids: list[str] | None = None) -> list[dict[str, Any]]:
        rows = self.list(); return [x for x in rows if not task_ids or x["task_id"] in task_ids]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def report_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)


def report_csv(rows: list[dict[str, Any]]) -> str:
    fields = ["task_id", "status", "source_kind", "filename", "original_sm3"]
    out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
    for row in rows: writer.writerow({f: row.get(f, "") for f in fields})
    return out.getvalue()


def report_html(rows: list[dict[str, Any]]) -> str:
    return "<table><tr><th>task_id</th><th>status</th><th>source_kind</th><th>original_sm3</th></tr>" + "".join(
        f"<tr><td>{r.get('task_id','')}</td><td>{r.get('status','')}</td><td>{r.get('source_kind','')}</td><td>{r.get('original_sm3','')}</td></tr>" for r in rows) + "</table>"
