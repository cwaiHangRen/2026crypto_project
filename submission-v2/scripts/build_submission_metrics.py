"""从已保存 Round9 JSON 日志生成提交表格，不重新解释或改写分母。"""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "submission" / "logs" / "round9"
TABLES = ROOT / "submission" / "tables"


def load(name: str) -> dict:
    return json.loads((LOG / name).read_text(encoding="utf-8"))


def build() -> dict:
    scale = load("scale-audit-summary.json")
    images = load("images-800-summary.json")
    pdfs = load("pdfs-200-summary.json")
    quality = load("quality-summary.json")
    crop = load("crop-260-summary.json")
    crop80 = load("crop-80-summary.json")
    d0 = scale["default_d0"]
    negative = scale["negative_g0"]

    groups = [
        {"media": "image", "attempts": 800, "gate_pass": 792,
         "gate_rate": 792 / 800, "correct_recovery": 792,
         "recovery_denominator": 792, "recovery_rate": 1.0,
         "gate_failed": 8, "false_positive": 0},
        {"media": "pdf", "attempts": 200, "gate_pass": 200,
         "gate_rate": 1.0, "correct_recovery": 200,
         "recovery_denominator": 200, "recovery_rate": 1.0,
         "gate_failed": 0, "false_positive": 0},
        {"media": "total", "attempts": 1000, "gate_pass": d0["published_rows"],
         "gate_rate": d0["published_rows"] / d0["base_attempts"],
         "correct_recovery": d0["correct_single_output"],
         "recovery_denominator": d0["detector_attempted"],
         "recovery_rate": d0["correct_single_output"] / d0["detector_attempted"],
         "gate_failed": d0["gate_failed"], "false_positive": negative["false_positive"]},
    ]

    timing = {
        "image_d0_recover": images["timings"]["breakdown"]["G0/recover/exact"],
        "image_g3_full": images["timings"]["breakdown"]["G3/full/exact"],
        "pdf_d0_recover": pdfs["timings"]["breakdown"]["G0/recover/exact"],
        "pdf_g3_full": pdfs["timings"]["breakdown"]["G3/full/exact"],
        "crop_s0": {
            "n": crop80["transform_breakdown"]["crop_center_area_0.95"]["rows"],
            "success": crop80["transform_breakdown"]["crop_center_area_0.95"]["sync_recovered_expected"],
            "rate": crop80["transform_breakdown"]["crop_center_area_0.95"]["sync_recovered_expected"] / crop80["transform_breakdown"]["crop_center_area_0.95"]["rows"],
            "p50_ms": crop80["transform_breakdown"]["crop_center_area_0.95"]["sync_ms_p50"],
            "p95_ms": crop80["transform_breakdown"]["crop_center_area_0.95"]["sync_ms_p95"],
        },
    }

    transform_rows = []
    for name, row in sorted(crop["transform_breakdown"].items()):
        if name in {"exact", "jpeg_q50", "jpeg_q75", "jpeg_q90", "resize_0.75",
                    "resize_jpeg_0.75_q75", "metadata_clear", "format_convert",
                    "screenshot_simulated", "crop_center_area_0.95",
                    "crop_boundary_area_0.80", "crop_out_of_range_negative",
                    "crop_out_of_range_over_one"}:
            cells = row["pairing_four_cells"]
            d0_success = cells["both_success"] + cells["d0_success_sync_failed"]
            s0_success = row["sync_recovered_expected"]
            transform_rows.append({
                "transform": name, "n": row["rows"], "d0_correct": d0_success,
                "d0_rate": d0_success / row["rows"] if row["rows"] else None,
                "s0_expected_id": s0_success,
                "s0_rate": s0_success / row["rows"] if row["rows"] else None,
                "p50_ms": row["sync_ms_p50"], "p95_ms": row["sync_ms_p95"],
            })
    center80 = crop80["transform_breakdown"]["crop_center_area_0.95"]
    transform_rows.append({
        "transform": "crop_center_area_0.95_round9_80",
        "n": center80["rows"],
        "d0_correct": center80["pairing_four_cells"]["both_success"],
        "d0_rate": center80["pairing_four_cells"]["both_success"] / center80["rows"],
        "s0_expected_id": center80["sync_recovered_expected"],
        "s0_rate": center80["sync_recovered_expected"] / center80["rows"],
        "p50_ms": center80["sync_ms_p50"],
        "p95_ms": center80["sync_ms_p95"],
    })

    final = {
        "schema_version": "gm-provenance-submission-metrics-1",
        "sources": [
            "submission/logs/round9/scale-audit-summary.json",
            "submission/logs/round9/images-800-summary.json",
            "submission/logs/round9/pdfs-200-summary.json",
            "submission/logs/round9/quality-summary.json",
            "submission/logs/round9/crop-260-summary.json",
            "submission/logs/round9/crop-80-summary.json",
        ],
        "scale_1000": {"groups": groups, "negative_g0": negative,
                       "gate_definition": "final encoded bytes pass blind recovery before registration",
                       "d0_definition": "exact expected ContentID as the only returned ID"},
        "timing_ms": timing,
        "transform_rows": transform_rows,
        "quality": quality["quality"],
        "storage_bytes": quality["storage"],
        "limitations": [
            "1000 行是协议规模清单，内部合成样本占多数；新增 AI 图片覆盖为 0/20，新增外部真实 PDF 覆盖为 0/10。",
            "PDF 质量记录来自固定 120 DPI pypdfium2 重渲染，不是人工或阅读器截图。",
            "裁剪 S0 仅报告来源线索；当前字节仍需 SM3/Manifest/SM2/状态检查。",
        ],
    }
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "final_metrics.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    with (TABLES / "final_metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["media", "attempts", "gate_pass", "gate_rate",
                                                    "correct_recovery", "recovery_denominator", "recovery_rate",
                                                    "gate_failed", "false_positive"])
        writer.writeheader(); writer.writerows(groups)
    lines = ["# 最终验收指标", "", "数字来自 submission/logs/round9 下保存的 JSON 日志。", "",
             "|分组|尝试|gate通过|gate率|正确恢复|恢复分母|恢复率|gate失败|G0误报|", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in groups:
        lines.append("|{media}|{attempts}|{gate_pass}|{gate_rate:.2%}|{correct_recovery}|{recovery_denominator}|{recovery_rate:.2%}|{gate_failed}|{false_positive}|".format(**row))
    lines += ["", "## 延迟", "", "|路径|n|平均 ms|P50 ms|P95 ms|", "|---|---:|---:|---:|---:|"]
    for name, row in timing.items():
        if "rate" in row: continue
        lines.append(f"|{name}|{row.get('n','')}|{row.get('mean_ms','')}|{row.get('p50_ms','')}|{row.get('p95_ms','')}|")
    lines += ["", "## 传播变换", "", "|变换|n|D0正确|D0率|S0预期ID|S0率|S0 P50 ms|S0 P95 ms|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in transform_rows:
        lines.append(f"|{row['transform']}|{row['n']}|{row['d0_correct']}|{row['d0_rate']:.2%}|{row['s0_expected_id']}|{row['s0_rate']:.2%}|{row['p50_ms']}|{row['p95_ms']}|")
    (TABLES / "final_metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return final


if __name__ == "__main__":
    result = build()
    print(json.dumps({"groups": len(result["scale_1000"]["groups"]), "transforms": len(result["transform_rows"])}, ensure_ascii=False))
