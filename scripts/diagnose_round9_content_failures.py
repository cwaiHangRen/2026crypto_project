"""Bounded diagnosis for the six Round9 content-recovery gate failures.

The source files live in the immutable Round9 evidence tree.  This script only
reads those bytes and runs the three already-existing watermark configurations;
it never changes the benchmark result or publishes a replacement artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET_IDS = [
    "r8-img-0058", "r8-img-0142", "r8-img-0178",
    "r8-img-0226", "r8-img-0394", "r8-img-0520",
]


def _one(config: str, data: bytes, content_id: str) -> dict:
    from gm_provenance import media, watermark

    started = time.perf_counter()
    output_format = "PNG"
    if config == "v1+D0":
        profile_name = "watermark-v1"
        decoder_profile = "dct-difference-v1"
    elif config == "v1+S0":
        profile_name = "watermark-v1"
        decoder_profile = "decoder-v2-sync"
    elif config == "existing-v2-candidate":
        profile_name = "watermark-v2-residual"
        decoder_profile = "dct-difference-v1"
    else:
        raise ValueError(config)

    embedded, embed_profile = media.embed(data, content_id, "image/png", profile_name)
    result = {
        "configuration": config,
        "watermark_profile": profile_name,
        "decoder_profile": decoder_profile,
        "output_format": output_format,
        "final_encoded_bytes": len(embedded),
        "final_sha256": hashlib.sha256(embedded).hexdigest(),
        "embed_profile": embed_profile,
    }
    # The publication gate is always the existing direct D0 gate.  S0 is
    # recorded as a diagnostic only and never substitutes for the gate.
    with watermark.direct_detection():
        direct = media.detect(embedded, "image/png")
    result["direct_detection"] = {
        "status": direct.get("status"),
        "candidate_content_ids": list(direct.get("content_ids", [])),
        "details": direct.get("details", {}),
    }
    result["gate"] = {
        "passed": direct.get("status") == "FOUND" and direct.get("content_ids") == [content_id],
        "expected_content_id": content_id,
        "stage": "final_media_blind_self_check",
    }
    if config == "v1+S0":
        s0 = media.detect(embedded, "image/png", decoder_profile)
        details = s0.get("details") or {}
        result["sync_detection"] = {
            "status": s0.get("status"),
            "candidate_content_ids": list(s0.get("content_ids", [])),
            "stop_reason": s0.get("stop_reason"),
            "search_complete": s0.get("search_complete"),
            "candidate_count": details.get("candidate_count"),
            "decode_attempts": details.get("decode_attempts"),
            "timing_ms": details.get("timing_ms"),
            "budget": details.get("budget"),
        }
    if config == "existing-v2-candidate":
        residual = watermark.diagnose_image_residual(data, content_id, embed_profile, output_format)
        result["residual_diagnostic"] = {
            "pre_encode": residual.get("pre_encode"),
            "post_encode": residual.get("post_encode"),
            "clipping": residual.get("clipping"),
        }
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def diagnose_one(sample_id: str, source: Path, timeout_s: float) -> dict:
    data = source.read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        dimensions = [image.width, image.height]
        mode = image.mode
        source_format = image.format
    # The Round9 G3 gate failure has no published manifest, therefore its
    # random production ContentID is intentionally unavailable.  Use a stable
    # diagnostic CID and label it as such in every result.
    content_id = hashlib.sha256(("round9-diagnostic:" + sample_id).encode()).hexdigest()[:32]
    row = {
        "sample_id": sample_id,
        "source_path": str(source).replace("\\", "/"),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "original_dimensions": dimensions,
        "original_mode": mode,
        "original_format": source_format,
        "original_bytes": len(data),
        "final_encoding": {"format": "PNG", "jpeg_quality": None, "watermark_output_quality": 95},
        "diagnostic_content_id": content_id,
        "content_id_note": "Round9 G3 gate rejected before manifest/publication; production CID is unavailable. This stable CID is used only for bounded reproduction.",
        "configurations": [],
    }
    for config in ("v1+D0", "v1+S0", "existing-v2-candidate"):
        started = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_one, config, data, content_id)
                result = future.result(timeout=timeout_s)
        except TimeoutError:
            result = {
                "configuration": config,
                "status": "timeout",
                "failure_stage": "bounded_diagnostic_timeout",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "timeout_seconds": timeout_s,
            }
        except Exception as exc:  # preserve diagnostic evidence, do not pass
            result = {
                "configuration": config,
                "status": "error",
                "failure_stage": "diagnostic_execution",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        row["configurations"].append(result)
    for result in row["configurations"]:
        if result.get("status") in {"timeout", "error"}:
            continue
        result["failure_stage"] = "passed_gate" if result.get("gate", {}).get("passed") else "final_encoded_byte_blind_recovery"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    rows = []
    for sample_id in TARGET_IDS:
        source = args.source_root / "samples" / sample_id / "source.bin"
        rows.append(diagnose_one(sample_id, source, args.timeout_seconds))
    result = {
        "schema_version": "round9-content-failure-diagnostics-1",
        "source_run": str(args.source_root).replace("\\", "/"),
        "sample_count": len(rows),
        "configurations": ["v1+D0", "v1+S0", "existing-v2-candidate"],
        "timeout_seconds_per_configuration": args.timeout_seconds,
        "gate_definition": "Only direct final encoded-byte recovery equal to the diagnostic ContentID is a gate pass; S0 is evidence only.",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sample_count": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
