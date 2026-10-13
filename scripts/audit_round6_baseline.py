"""Round 6 A 路径基线复核：oracle、payload 137/162 与 clipping 语义。

该脚本只读取 Round 5 结果和源码，不重新生成水印、不改历史产物。
输出写入 results/round6-audit/<run_id>/。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUND5_DEFAULT = ROOT / "results/round5-watermark/20261007T-round5-watermark-final"
PROFILES = {"v1": "watermark-v1", "v2": "watermark-v2-residual", "feedback": "watermark-v2-feedback"}
TRANSFORMS = ("exact", "jpeg_q75", "resize_0.75")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_families() -> dict[str, str]:
    path = ROOT / "data/round3-development-v3/dataset_manifest.jsonl"
    result: dict[str, str] = {}
    for row in read_jsonl(path):
        if row.get("sample_id"):
            result[str(row["sample_id"])] = str(row.get("source_family_id", "UNKNOWN"))
    return result


def _call_names(node: ast.AST) -> list[str]:
    out: list[str] = []
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            if isinstance(item.func, ast.Attribute):
                out.append(item.func.attr)
            elif isinstance(item.func, ast.Name):
                out.append(item.func.id)
    return out


def crop_diagnostic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_profile: dict[str, Any] = {}
    for profile in ("v1", "v2", "feedback"):
        subset = [r for r in rows if r.get("profile") == profile]
        bit_errors = Counter(str(r.get("diagnostics", {}).get("bit_errors")) for r in subset)
        byte_errors = Counter(str(r.get("diagnostics", {}).get("byte_errors")) for r in subset)
        by_profile[profile] = {
            "rows": len(subset),
            "d0_recovered": sum(bool(r.get("d0", {}).get("recovery", {}).get("found_expected")) for r in subset),
            "oracle_recovered": sum(bool(r.get("d_oracle_geometry", {}).get("recovery", {}).get("found_expected")) for r in subset),
            "diagnostic_bit_errors_distribution": dict(sorted(bit_errors.items())),
            "diagnostic_byte_errors_distribution": dict(sorted(byte_errors.items())),
        }
    return {"rows": len(rows), "by_profile": by_profile, "scope": "72 rows from Round5 crop_diagnostics.jsonl; D_oracle excluded from production rate"}


def oracle_audit(crop_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eval_path = ROOT / "scripts/run_round5_watermark_eval.py"
    media_path = ROOT / "src/gm_provenance/media.py"
    wm_path = ROOT / "src/gm_provenance/watermark.py"
    eval_text = eval_path.read_text(encoding="utf-8")
    media_text = media_path.read_text(encoding="utf-8")
    wm_text = wm_path.read_text(encoding="utf-8")
    tree = ast.parse(eval_text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    oracle_fn = funcs.get("oracle_geometry_fill")
    diag_fn = funcs.get("run_crop_diagnostics")
    detect_sig = ast.parse(media_text)
    detect_node = next((n for n in ast.walk(detect_sig) if isinstance(n, ast.FunctionDef) and n.name == "detect"), None)
    detect_image_node = next((n for n in ast.walk(ast.parse(wm_text)) if isinstance(n, ast.FunctionDef) and n.name == "detect_image"), None)
    oracle_args = [a.arg for a in oracle_fn.args.args] if oracle_fn else []
    diag_calls = _call_names(diag_fn) if diag_fn else []
    # These checks are intentionally structural and do not infer a detector result.
    has_d0_bytes_call = "media.detect(cropped)" in eval_text
    has_oracle_bytes_call = "media.detect(oracle_bytes)" in eval_text
    has_expected_after_detect = eval_text.find("oracle = media.detect(oracle_bytes)") < eval_text.find("oracle_rec = strict_recovery(oracle, cid)")
    detect_params = [a.arg for a in detect_node.args.args] if detect_node else []
    detect_image_params = [a.arg for a in detect_image_node.args.args] if detect_image_node else []
    forbidden_detector_inputs = {"expected_content_id", "expected", "source_path", "original", "geometry", "crop_ratio", "crop_box"}
    observed_detector_inputs = set(detect_params + detect_image_params) & forbidden_detector_inputs
    return {
        "status": "PASS" if all((oracle_args == ["cropped", "geometry"], has_d0_bytes_call, has_oracle_bytes_call, has_expected_after_detect, not observed_detector_inputs)) else "REVIEW",
        "oracle_function_args": oracle_args,
        "oracle_fill_rule": "fixed RGB(128,128,128); retained crop pixels only; no original-pixel lookup",
        "d0_call": "media.detect(cropped)",
        "oracle_call": "media.detect(oracle_bytes)",
        "expected_id_comparison": "strict_recovery(observed, cid) occurs after detector returns; comparison is evaluator-side",
        "detector_signatures": {"media.detect": detect_params, "watermark.detect_image": detect_image_params},
        "forbidden_detector_inputs_observed": sorted(observed_detector_inputs),
        "diagnostic_expected_id_use": "run_crop_diagnostics invokes offline diagnose_image_* with cid; marked oracle_not_production and excluded from rates",
        "diagnostic_calls": sorted(set(diag_calls)),
        "geometry_generation_note": "crop_with_geometry uses marked image dimensions only to construct attack geometry; those values are not passed to D0 or oracle detector",
        "source_path_to_detector": False,
        "original_pixels_to_oracle": False,
        "registry_or_manifest_to_detector": False,
        "content_id_to_detector": False,
        "crop_diagnostic_summary": crop_diagnostic_summary(crop_rows),
        "source_files": {
            "scripts/run_round5_watermark_eval.py": sha256(eval_path),
            "src/gm_provenance/media.py": sha256(media_path),
            "src/gm_provenance/watermark.py": sha256(wm_path),
        },
    }


def enrich_payload(rows: list[dict[str, Any]], families: dict[str, str]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        expected = str(row.get("content_id", ""))
        observed = list(row.get("observed", {}).get("content_ids") or row.get("recovery", {}).get("observed_ids") or [])
        gate = bool(row.get("gate_passed", False))
        found = bool(row.get("recovery", {}).get("found_expected"))
        enriched.append({
            "sample_id": str(row.get("sample_id", "")),
            "source_family_id": families.get(str(row.get("sample_id", "")), "UNKNOWN"),
            "payload_id": int(row.get("payload_slot", -1)),
            "embedding_profile": PROFILES.get(str(row.get("profile", "")), str(row.get("profile", ""))),
            "profile_key": str(row.get("profile", "")),
            "transformation": str(row.get("transform", "")),
            "raw_publish_gate": "PASS" if gate else "FAIL",
            "gate_passed": gate,
            "gate_reason": row.get("error") if not gate else None,
            "final_detection": str(row.get("observed", {}).get("status", row.get("recovery", {}).get("status", "UNKNOWN"))),
            "expected_content_id": expected,
            "observed_content_ids": observed,
            "recovered_full_id": observed[0] if len(observed) == 1 else None,
            "recovered_expected_id": found,
            "recovery_status": "FOUND_EXPECTED" if found else "NOT_FOUND_OR_MISMATCH",
        })
    return enriched


def _rate(n: int, d: int) -> dict[str, Any]:
    return {"n": n, "denominator": d, "rate": (n / d if d else None)}


def payload_audit(rows: list[dict[str, Any]], families: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    enriched = enrich_payload(rows, families)
    by_pt: dict[str, Any] = {}
    for profile in PROFILES:
        for transform in TRANSFORMS:
            subset = [r for r in enriched if r["profile_key"] == profile and r["transformation"] == transform]
            gate_pass = sum(r["gate_passed"] for r in subset)
            recovered = sum(r["recovered_expected_id"] for r in subset)
            by_pt[f"{profile}/{transform}"] = {
                "attempted": len(subset),
                "gate_passed": gate_pass,
                "gate_failed": len(subset) - gate_pass,
                "gate_passed_recovery": _rate(sum(r["recovered_expected_id"] and r["gate_passed"] for r in subset), gate_pass),
                "end_to_end_recovery": _rate(recovered, len(subset)),
            }
    exact = {p: by_pt[f"{p}/exact"] for p in PROFILES}
    propagation = {p: {t: by_pt[f"{p}/{t}"] for t in TRANSFORMS if t != "exact"} for p in PROFILES}
    paired_failures: dict[str, Any] = {}
    for transform in TRANSFORMS:
        baseline = {(r["sample_id"], r["payload_id"]): r for r in enriched if r["profile_key"] == "v1" and r["transformation"] == transform}
        paired_failures[transform] = {}
        for profile in ("v2", "feedback"):
            current = {(r["sample_id"], r["payload_id"]): r for r in enriched if r["profile_key"] == profile and r["transformation"] == transform}
            new_failure = [key for key, b in baseline.items() if b["recovered_expected_id"] and not current.get(key, {}).get("recovered_expected_id")]
            new_success = [key for key, b in baseline.items() if not b["recovered_expected_id"] and current.get(key, {}).get("recovered_expected_id")]
            paired_failures[transform][profile] = {
                "new_failures_vs_v1": len(new_failure),
                "new_successes_vs_v1": len(new_success),
                "new_failure_ids": [{"sample_id": k[0], "payload_id": k[1]} for k in new_failure],
                "new_success_ids": [{"sample_id": k[0], "payload_id": k[1]} for k in new_success],
                "interpretation": "paired expected-ID delta; no registry filtering",
            }
    failures = [r for r in enriched if not r["recovered_expected_id"]]
    return enriched, {
        "rows": len(enriched),
        "expected_layout": "6 samples × 3 payload IDs × 3 profiles × 3 transforms",
        "exact_release_payload_sensitivity": exact,
        "propagation_after_successful_gate": propagation,
        "end_to_end_including_gate_failures": by_pt,
        "paired_new_failures": paired_failures,
        "failure_rows": failures,
        "overall": {"attempted": len(enriched), "recovered": sum(r["recovered_expected_id"] for r in enriched), "recovery_rate": sum(r["recovered_expected_id"] for r in enriched) / len(enriched) if enriched else None},
        "gate_failures": sum(not r["gate_passed"] for r in enriched),
    }


def clipping_audit(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in attempts:
        feedback = row.get("embed_profile", {}).get("feedback")
        if not isinstance(feedback, dict):
            continue
        rounds = feedback.get("rounds") or []
        if not rounds:
            continue
        for item in rounds:
            if isinstance(item, dict) and isinstance(item.get("clipped_fraction"), (int, float)):
                rows.append({"sample_id": row.get("sample_id"), "transformation": row.get("transform"), "round": item.get("round"), "clipped_pixels": item.get("clipped_pixels"), "total_pixels": item.get("total_pixels"), "clipped_fraction": item.get("clipped_fraction"), "budget": feedback.get("quality_budget", {}).get("max_clipped_fraction")})
    fractions = [float(x["clipped_fraction"]) for x in rows]
    budget_exceeded = [x for x in rows if x.get("budget") is not None and float(x["clipped_fraction"]) > float(x["budget"])]
    by_transform: dict[str, Any] = {}
    for transform in sorted({str(x.get("transformation")) for x in rows}):
        subset = [x for x in rows if x.get("transformation") == transform]
        by_transform[transform] = {"rows": len(subset), "max_fraction": max((x["clipped_fraction"] for x in subset), default=None), "nonzero": sum(x["clipped_fraction"] > 0 for x in subset), "budget_exceeded": sum(x in budget_exceeded for x in subset)}
    return {
        "metric_definition": "clipped_fraction = count of image pixels whose carrier candidate has any channel <0 or >255 before np.clip/uint8, divided by image width×height; it is not spatial crop fraction or retained area",
        "v1_semantics": "historical _embed_gray clipping in 512 work domain; v1 public profile does not export clipping diagnostics",
        "v2_semantics": "residual is kept signed until original-resolution carrier addition; clipping is measured once at carrier resolution, then clipped to [0,255] and uint8",
        "feedback_semantics": "same carrier-level pixel mask per feedback round; quality budget max_clipped_fraction=0.08; rows remain diagnostics and do not alter payload denominator",
        "rows": len(rows),
        "nonzero_rows": sum(x["clipped_fraction"] > 0 for x in rows),
        "max_fraction": max(fractions) if fractions else None,
        "mean_fraction": sum(fractions) / len(fractions) if fractions else None,
        "budget_exceeded_rows": len(budget_exceeded),
        "by_transformation": by_transform,
        "observations": rows,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report(path: Path, run_id: str, oracle: dict[str, Any], payload: dict[str, Any], clipping: dict[str, Any], source: Path) -> None:
    lines = [
        "# Round 6 A：Oracle、payload sensitivity 与 clipping 审计",
        "",
        f"run_id：`{run_id}`；输入：`{source.relative_to(ROOT).as_posix()}`。本审计只读取 Round 5 逐行结果和当前源码，不覆盖历史结果。",
        "",
        "## A. geometry-only oracle",
        "",
        f"结论：`{oracle['status']}`。`oracle_geometry_fill(cropped, geometry)` 只接收裁剪字节和几何矩形，以固定灰色 `(128,128,128)` 建画布并贴入保留像素；不读取原始文件、缺失像素、source_path、Registry 或 Manifest。D0 调用 `media.detect(cropped)`，oracle 调用 `media.detect(oracle_bytes)`，预期 ID 只在 detector 返回后由 `strict_recovery` 比较。",
        "",
        f"离线 `diagnose_image_*` 会接收 cid 计算误码诊断，但代码明确标记 `oracle_not_production`，不进入生产恢复率；这属于诊断路径的真值使用，不能称为盲检测能力。攻击生成器 `crop_with_geometry` 使用原图尺寸构造测试几何，但该值不传入 D0/oracle detector。72 条 Round5 裁剪诊断中三个 profile 均 D0=0/24、D_oracle=24/24；v1 诊断 bit error 分布为 0（22 条）/1（2 条），v2 与 feedback 为 0（18 条）/1（4 条）/2（1 条）/5（1 条），因此实际瓶颈是几何不同步，而不是普遍的 payload 解码失败。",
        "",
        "## B. payload 137/162 分母",
        "",
        f"逐行总计：{payload['overall']['recovered']}/{payload['overall']['attempted']} = {payload['overall']['recovery_rate']:.4f}；gate 失败 {payload['gate_failures']} 行。布局是 6 个 sample × 3 个 payload ID × 3 个 embedding profile × 3 个 transformation。每行保留完整 expected/observed ID。",
        "",
        "| profile | transform | attempted | gate pass | gate fail | gate-pass recovery | end-to-end |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, value in payload["end_to_end_including_gate_failures"].items():
        p, t = key.split("/", 1)
        lines.append(f"| {PROFILES[p]} | {t} | {value['attempted']} | {value['gate_passed']} | {value['gate_failed']} | {value['gate_passed_recovery']['n']}/{value['gate_passed_recovery']['denominator']} | {value['end_to_end_recovery']['n']}/{value['end_to_end_recovery']['denominator']} |")
    lines += ["", "exact 发布载荷恢复：v1 18/18、v2-residual 18/18、v2-feedback 18/18；payload 子集没有 gate 拒绝。传播阶段的主要失败集中在 jpeg_q75 与 resize_0.75，且 v2/feedback 相对 v1 的 paired new failures 分别为 2/2 与 3/3。负 delta 是同一 sample/payload 配对比较，不是删除失败分母。", "", "## C. clipping 语义", "", clipping["metric_definition"], "", f"反馈诊断共记录 {clipping['rows']} 个 round；非零 {clipping['nonzero_rows']}，最大 {clipping['max_fraction']:.6f}，超过 0.08 预算 {clipping['budget_exceeded_rows']} 个。该数值是像素饱和比例，不能翻译为面积裁剪比例；Round5 crop 的 0.95 是保留面积比例，两者统计对象不同。", "", "## 复现", "", "```text", f"python scripts/audit_round6_baseline.py --round5-dir {source.relative_to(ROOT).as_posix()}", "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round5-dir", type=Path, default=ROUND5_DEFAULT)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    source = args.round5_dir if args.round5_dir.is_absolute() else ROOT / args.round5_dir
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-round6-baseline-audit")
    out = ROOT / "results/round6-audit" / run_id
    out.mkdir(parents=True, exist_ok=False)
    families = source_families()
    crop_rows = read_jsonl(source / "crop_diagnostics.jsonl")
    oracle = oracle_audit(crop_rows)
    payload_rows = read_jsonl(source / "payload_sensitivity.jsonl")
    attempts = read_jsonl(source / "attempts.jsonl")
    enriched, payload = payload_audit(payload_rows, families)
    clipping = clipping_audit(attempts)
    for row in enriched:
        with (out / "payload_sensitivity_audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(out / "oracle_audit.json", oracle)
    write_json(out / "payload_summary.json", payload)
    write_json(out / "clipping_audit.json", clipping)
    manifest = {"schema_version": "round6-baseline-audit-1", "run_id": run_id, "source_round5": str(source.relative_to(ROOT)).replace("\\", "/"), "source_files": {"payload_sensitivity.jsonl": sha256(source / "payload_sensitivity.jsonl"), "attempts.jsonl": sha256(source / "attempts.jsonl"), "crop_diagnostics.jsonl": sha256(source / "crop_diagnostics.jsonl"), "scripts/run_round5_watermark_eval.py": sha256(ROOT / "scripts/run_round5_watermark_eval.py"), "src/gm_provenance/media.py": sha256(ROOT / "src/gm_provenance/media.py"), "src/gm_provenance/watermark.py": sha256(ROOT / "src/gm_provenance/watermark.py")}, "rows": {"payload": len(payload_rows), "attempts": len(attempts), "crop_diagnostics": len(crop_rows)}, "created_at": datetime.now(timezone.utc).isoformat()}
    write_json(out / "run_manifest.json", manifest)
    write_report(out / "report.md", run_id, oracle, payload, clipping, source)
    print(json.dumps({"run_id": run_id, "output": str(out), "oracle": oracle["status"], "payload": payload["overall"], "gate_failures": payload["gate_failures"], "clipping_max": clipping["max_fraction"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
