"""Round 5 反馈候选小子集探针。

固定选择 6 张开发集图片，其中优先包含 Round 4 exact 失败样本；同一
ContentID 下比较 v2-residual、反馈候选和既有强制系数差规则控制。脚本只
保存本地 JSON 与失败字节，不写入登记库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gm_provenance.watermark import detect_image, embed_image_residual  # noqa: E402
from gm_provenance.watermark_feedback import (  # noqa: E402
    PROFILE,
    WatermarkFeedbackError,
    diagnose_image_feedback,
    embed_image_feedback,
)
from gm_provenance.development_eval import _image_transform  # noqa: E402

ROUND4_FAILURES = {"synth-image-004.png", "synth-image-010.png", "synth-image-016.png"}


def _selected_images() -> list[Path]:
    images = sorted((ROOT / "data" / "round3-development-v3" / "images").glob("*.png"))
    preferred = [p for p in images if p.name in ROUND4_FAILURES]
    rest = [p for p in images if p not in preferred]
    return (preferred + rest)[:6]


def _one(path: Path) -> dict:
    raw = path.read_bytes()
    sample_id = path.stem
    cid = hashlib.sha256(f"round4|20261007|img-{sample_id}|0".encode()).hexdigest()[:32]
    row = {"sample": path.name, "content_id": cid, "profiles": {}, "round4_reproduction": {}}
    # Round 4 exact baseline: no feedback and the same v1 blind detector.
    baseline, baseline_profile = embed_image_residual(raw, cid, "PNG")
    baseline_detect = detect_image(baseline)
    row["profiles"]["v2-residual"] = {
        "status": baseline_detect.get("status"),
        "content_ids": baseline_detect.get("content_ids", []),
        "exact_recovered": cid in baseline_detect.get("content_ids", []),
        "profile": baseline_profile,
    }
    # The existing residual encoder is also the coefficient-rule-only control:
    # it force-sets target differences once and performs no post-encode feedback.
    row["profiles"]["coefficient-rule-only"] = {
        **row["profiles"]["v2-residual"],
        "ablation": "existing forced target coefficient difference; feedback disabled",
    }
    try:
        marked, profile = embed_image_feedback(raw, cid, "PNG")
        observed = detect_image(marked)
        row["profiles"][PROFILE] = {
            "status": observed.get("status"),
            "content_ids": observed.get("content_ids", []),
            "exact_recovered": cid in observed.get("content_ids", []),
            "profile": profile,
            "diagnostics": diagnose_image_feedback(raw, cid, "PNG"),
        }
    except WatermarkFeedbackError as exc:
        row["profiles"][PROFILE] = {
            "status": "FEEDBACK_REJECTED",
            "content_ids": [],
            "exact_recovered": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
            "final_bytes_length": len(exc.final_bytes),
        }
    if path.name == "synth-image-010.png":
        transformed_base = _image_transform(baseline, "format_convert")
        transformed_detect = detect_image(transformed_base)
        row["round4_reproduction"] = {
            "transform": "format_convert",
            "baseline_status": transformed_detect.get("status"),
            "baseline_content_ids": transformed_detect.get("content_ids", []),
            "baseline_recovered_expected": cid in transformed_detect.get("content_ids", []),
        }
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "round5-feedback" / "probe.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = [_one(path) for path in _selected_images()]
    summary = {
        "run_id": "20261007T-round5-feedback-probe",
        "profile": PROFILE,
        "content_id_policy": "sha256(round4|20261007|img-{sample_id}|0)[:32]",
        "sample_count": len(rows),
        "samples": rows,
        "ablation": {
            "feedback": "post-encode signed-margin updates, max 3 rounds, fixed step",
            "coefficient_rule_only": "v2-residual forced target difference, no feedback",
            "interpretation": "The two controls share payload, RS, DCT positions and strength 34.",
        },
    }
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sample_count": len(rows),
        "v2_exact": sum(r["profiles"]["v2-residual"]["exact_recovered"] for r in rows),
        "feedback_exact": sum(r["profiles"][PROFILE]["exact_recovered"] for r in rows),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
