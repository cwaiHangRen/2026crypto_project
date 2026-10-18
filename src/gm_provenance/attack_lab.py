"""Deterministic, auditable attack matrix for v2 media adapters."""
from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .media_adapters import MediaAdapter, MediaAdapterError, MediaOperation, MediaUnavailableError, adapter_for


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes): return {"__bytes__": value.hex()}
    if isinstance(value, dict): return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True, init=False)
class AttackCase:
    attack_id: str
    media_type: str
    attack_type: str
    params: dict[str, Any]
    expected_result: str = "detect_or_reject"
    input_file: str | None = None

    def __init__(self, attack_id: str, media_type: str, attack_type: str,
                 params: dict[str, Any] | None = None, expected_result: str = "detect_or_reject",
                 input_file: str | None = None, *, parameters: dict[str, Any] | None = None):
        object.__setattr__(self, "attack_id", attack_id)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "attack_type", attack_type)
        object.__setattr__(self, "params", dict(params if params is not None else (parameters or {})))
        object.__setattr__(self, "expected_result", expected_result)
        object.__setattr__(self, "input_file", input_file)

    @property
    def parameters(self) -> dict[str, Any]:
        """Alias used by the serialisable v2 domain model."""
        return self.params

    def as_dict(self) -> dict[str, Any]:
        out = _jsonable(asdict(self)); out["parameters"] = out["params"]; return out


@dataclass
class AttackResult:
    attack_id: str
    media_type: str
    attack_type: str
    params: dict[str, Any]
    input_file: str | None
    expected_result: str
    actual_result: str
    watermark_recovered: bool | None
    recovered_content_ids: list[str]
    summary: dict[str, Any]
    elapsed_ms: float
    log_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = _jsonable(asdict(self))
        summary = self.summary if isinstance(self.summary, dict) else {}
        out["manifest_status"] = summary.get("manifest_status", "NOT_CHECKED")
        out["signature_status"] = summary.get("signature_status", "NOT_CHECKED")
        out["version_chain_status"] = summary.get("version_chain_status", "NOT_CHECKED")
        out["revocation_status"] = summary.get("revocation_status", "NOT_CHECKED")
        return out


def default_attack_cases(media_type: str) -> list[AttackCase]:
    mt = media_type.lower()
    if mt in {"image", "image/png", "image/jpeg"}:
        return [
            AttackCase("img-jpeg-70", "image", "jpeg_quality", {"quality": 70}),
            AttackCase("img-png-jpeg", "image", "png_to_jpeg", {"quality": 85}),
            AttackCase("img-resize-75", "image", "resize", {"scale": 0.75}),
            AttackCase("img-crop-90", "image", "crop", {"ratio": 0.90}),
            AttackCase("img-rotate-2", "image", "rotate", {"angle": 2.0}),
            AttackCase("img-mirror", "image", "mirror", {}),
            AttackCase("img-screenshot", "image", "screenshot", {}),
            AttackCase("img-metadata-clear", "image", "metadata_clear", {}),
            AttackCase("img-local-replace", "image", "local_replace", {"ratio": 0.12}),
            AttackCase("img-watermark-splice", "image", "watermark_copy", {}),
        ]
    if mt in {"pdf", "application/pdf"}:
        return [
            AttackCase("pdf-rasterize", "pdf", "rasterize", {}),
            AttackCase("pdf-delete-page", "pdf", "page_delete", {"page": 0}),
            AttackCase("pdf-reorder-page", "pdf", "page_reorder", {}),
            AttackCase("pdf-content-replace", "pdf", "content_replace", {"page": 0}),
            AttackCase("pdf-metadata-clear", "pdf", "metadata_clear", {}),
            AttackCase("pdf-old-version", "pdf", "rollback", {}),
        ]
    if mt in {"audio/wav", "wav", "audio"}:
        return [
            AttackCase("wav-resample", "audio/wav", "resample", {"factor": 0.8}),
            AttackCase("wav-transcode", "audio/wav", "transcode", {}),
            AttackCase("wav-volume", "audio/wav", "volume", {"factor": 0.7}),
            AttackCase("wav-crop", "audio/wav", "crop", {"ratio": 0.8}),
        ]
    return []


def run_attack_case(adapter: MediaAdapter, marked_data: bytes, content_id: str, case: AttackCase, *, output_dir: str | Path | None = None) -> AttackResult:
    started = time.perf_counter(); result_path = None
    try:
        attacked = adapter.attack(marked_data, case.attack_type, case.params)
        operation = adapter.verify(attacked, content_id)
        recovered = list(operation.recovered_content_ids)
        actual = "WATERMARK_RECOVERED" if operation.watermark_recovered else "REJECTED_OR_NOT_FOUND"
        summary = operation.as_dict()
        if output_dir is not None:
            root = Path(output_dir); root.mkdir(parents=True, exist_ok=True); result_path = str(root / f"{case.attack_id}.bin")
            Path(result_path).write_bytes(attacked)
    except MediaUnavailableError as exc:
        actual, recovered, summary = "UNAVAILABLE", [], {"error_type": type(exc).__name__, "error": str(exc)}
    except Exception as exc:
        actual, recovered, summary = "ERROR", [], {"error_type": type(exc).__name__, "error": str(exc)}
    return AttackResult(case.attack_id, case.media_type, case.attack_type, case.params, case.input_file, case.expected_result,
                        actual, (bool(recovered) if actual not in {"UNAVAILABLE", "ERROR"} else None), recovered,
                        summary, round((time.perf_counter() - started) * 1000, 3), result_path)


def run_attack_matrix(marked_data: bytes, content_id: str, cases: Iterable[AttackCase] | None = None, *, adapter: MediaAdapter | None = None, output_dir: str | Path | None = None, log_path: str | Path | None = None) -> list[AttackResult]:
    adapter = adapter or adapter_for(marked_data)
    cases = list(cases or default_attack_cases(adapter.media_type))
    results = [run_attack_case(adapter, marked_data, content_id, case, output_dir=output_dir) for case in cases]
    if log_path is not None:
        path = Path(log_path); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def write_attack_matrix(results: Iterable[AttackResult], path: str | Path) -> dict[str, Any]:
    rows = [r.as_dict() for r in results]; path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fields = ["attack_id", "media_type", "attack_type", "expected_result", "actual_result", "watermark_recovered", "recovered_content_ids", "manifest_status", "signature_status", "version_chain_status", "revocation_status", "elapsed_ms"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            for row in rows: writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k) for k in fields})
    else:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"count": len(rows), "path": str(path), "results": rows}


def attack_summary(results: Iterable[AttackResult]) -> dict[str, Any]:
    rows = list(results); by_type: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_type.setdefault(row.attack_type, {"total": 0, "recovered": 0, "rejected": 0, "unavailable": 0, "error": 0}); bucket["total"] += 1
        if row.actual_result == "WATERMARK_RECOVERED": bucket["recovered"] += 1
        elif row.actual_result == "UNAVAILABLE": bucket["unavailable"] += 1
        elif row.actual_result == "ERROR": bucket["error"] += 1
        else: bucket["rejected"] += 1
    return {"total": len(rows), "by_attack_type": by_type, "mean_latency_ms": (sum(r.elapsed_ms for r in rows) / len(rows) if rows else 0.0)}


__all__ = ["AttackCase", "AttackResult", "default_attack_cases", "run_attack_case", "run_attack_matrix", "write_attack_matrix", "attack_summary"]
