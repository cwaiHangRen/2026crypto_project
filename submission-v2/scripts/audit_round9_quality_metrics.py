"""Aggregate fixed Round9 quality records and audit raw-input negatives.

This script only reads the frozen manifests and existing final bytes.  It does
not publish, sign, re-embed, or mutate a registry.  The raw negative pass is
separate from published-byte D0: it covers every in-scope original input,
including the eight inputs whose publish gate rejected the encoded output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gm_provenance import media  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stats(values: list[float], *, lower_is_worse: bool = True) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "worst": None}
    ordered = sorted(values, reverse=not lower_is_worse)
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
        "worst": round(ordered[0], 6),
    }


def quality_summary(path: Path) -> dict:
    records = load_jsonl(path)
    image_metrics: dict[str, list[dict]] = defaultdict(list)
    pdf_page_metrics: list[dict] = []
    pdf_feature_counts: dict[str, Counter] = defaultdict(Counter)
    pdf_sizes: list[float] = []
    pdf_page_counts: list[int] = []
    for record in records:
        quality = record.get("quality") or {}
        if record.get("kind") == "image":
            for pair_name, value in (quality.get("pairs") or {}).items():
                if isinstance(value, dict):
                    image_metrics[pair_name].append(value)
            continue
        for page in quality.get("pages") or []:
            if page.get("status") == "PASS" and isinstance(page.get("A_C"), dict):
                pdf_page_metrics.append(page["A_C"])
        features = (quality.get("feature_changes") or {}).get("status") or {}
        for name, status in features.items():
            pdf_feature_counts[name][str(status)] += 1
        sizes = quality.get("file_sizes_bytes") or {}
        if sizes.get("ratio") is not None:
            pdf_sizes.append(float(sizes["ratio"]))
        if quality.get("source_features", {}).get("page_count") is not None:
            pdf_page_counts.append(int(quality["source_features"]["page_count"]))

    def metric_block(items: list[dict]) -> dict:
        mse = [float(item["mse"]) for item in items if item.get("mse") is not None]
        psnr = [float(item["psnr_db"]) for item in items if item.get("psnr_db") is not None]
        ssim = [float(item["ssim_local"]) for item in items if item.get("ssim_local") is not None]
        return {
            "n": len(items),
            "mse": stats(mse, lower_is_worse=False),
            "psnr_db": stats(psnr, lower_is_worse=True),
            "ssim_local": stats(ssim, lower_is_worse=True),
            "zero_mse": sum(item.get("psnr_infinite") is True for item in items),
            "parameters": (items[0].get("ssim_parameters") if items else None),
        }

    return {
        "schema_version": "round9-quality-metrics-1",
        "source": str(path.relative_to(ROOT)).replace("\\", "/"),
        "selected_records": len(records),
        "image": {name: metric_block(items) for name, items in sorted(image_metrics.items())},
        "pdf": {
            "rendered_pages": metric_block(pdf_page_metrics),
            "feature_status": {name: dict(counts) for name, counts in sorted(pdf_feature_counts.items())},
            "source_page_count": stats([float(value) for value in pdf_page_counts]),
            "published_to_source_size_ratio": stats(pdf_sizes, lower_is_worse=False),
            "B_control": "NOT_APPLICABLE: the current PDF path has no no-watermark PDF encoder",
            "render_scope": "same-DPI pypdfium2 render at 120 DPI; not a real reader screenshot",
        },
        "interpretation": {
            "A_B_A_C_B_C": "Image metrics are decoded RGB at source geometry; MSE is 0 only when arrays are identical and PSNR is then reported as null with psnr_infinite=true.",
            "thresholds": "No quality threshold was declared before this diagnostic; values are distributions, not pass/fail claims.",
        },
    }


def negative_summary(manifest_paths: list[str], output: Path) -> dict:
    rows: list[dict] = []
    for manifest_name in manifest_paths:
        rows.extend(load_jsonl(resolve(manifest_name)))
    results: list[dict] = []
    for sample in rows:
        path = resolve(sample["local_path"])
        row = {
            "sample_id": sample["sample_id"],
            "media_type": sample.get("media_type"),
            "input_compatibility": sample.get("input_compatibility", sample.get("support_status", "unknown")),
            "source_sha256": sample.get("sha256") or sha256(path),
            "source_exists": path.exists(),
            "detector_attempted": False,
        }
        if not path.exists():
            row.update({"status": "MISSING_INPUT", "error_type": "FileNotFoundError"})
        elif row["input_compatibility"] != "in_scope":
            row.update({"status": "OUT_OF_SCOPE", "error_type": "predeclared_input_limit"})
        else:
            try:
                observed = media.detect(path.read_bytes(), sample.get("media_type"))
                row.update({
                    "status": observed.get("status"),
                    "detector_attempted": True,
                    "returned_ids": sorted(set(observed.get("recovered_content_ids") or observed.get("content_ids") or [])),
                    "watermark_status": observed.get("watermark_status", observed.get("status")),
                })
            except Exception as exc:
                row.update({"status": "EXECUTION_ERROR", "error_type": type(exc).__name__})
        results.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    (output.parent / "raw_input_negative.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in results), encoding="utf-8"
    )
    in_scope = [row for row in results if row["input_compatibility"] == "in_scope"]
    return {
        "schema_version": "round9-raw-negative-1",
        "manifest_rows": len(results),
        "in_scope_denominator": len(in_scope),
        "out_of_scope_not_attempted": sum(row["status"] == "OUT_OF_SCOPE" for row in results),
        "missing_input": sum(row["status"] == "MISSING_INPUT" for row in results),
        "detector_attempted": sum(bool(row["detector_attempted"]) for row in results),
        "status_counts_all": dict(Counter(row["status"] for row in results)),
        "status_counts_in_scope": dict(Counter(row["status"] for row in in_scope)),
        "false_positive_in_scope": sum(bool(row.get("returned_ids")) for row in in_scope),
        "gate_failed_inputs_in_scope": [
            row["sample_id"] for row in in_scope
            if row["sample_id"] in {"r8-img-0058", "r8-img-0142", "r8-img-0178", "r8-img-0226", "r8-img-0394", "r8-img-0520"}
        ],
        "raw_records": "raw_input_negative.jsonl",
        "note": "The in-scope denominator includes original inputs whose later publish gate failed. Out-of-scope inputs are reported separately and are not treated as decoder attempts.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", default="results/round9-scale-audit-r2/quality_fixed_subset.jsonl")
    parser.add_argument("--images-manifest", default="data/round8-scale-1000-final2/images_manifest.jsonl")
    parser.add_argument("--pdfs-manifest", default="data/round8-scale-1000-final2/pdfs_manifest.jsonl")
    parser.add_argument("--output", default="results/round9-correction-20261014-v2")
    args = parser.parse_args()
    output = resolve(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "quality": quality_summary(resolve(args.quality)),
        "raw_negative": negative_summary([args.images_manifest, args.pdfs_manifest], output / "summary.json"),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
