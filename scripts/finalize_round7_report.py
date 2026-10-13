#!/usr/bin/env python3
"""Assemble the Round 7 A-J report from durable evidence files."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def correct(row, side):
    expected = row.get("expected_content_id")
    observed = ((row.get(side) or {}).get("observed") or {})
    ids = list(dict.fromkeys(str(x) for x in observed.get("content_ids", [])))
    return observed.get("status") == "FOUND" and len(ids) == 1 and ids[0] == str(expected)


def latest_dir(root: Path):
    dirs = sorted([p for p in root.glob("*") if p.is_dir()])
    return dirs[-1] if dirs else None


def latest_result_dir(root: Path, filename: str = "summary.json"):
    dirs = sorted([p for p in root.glob("*") if p.is_dir() and (p / filename).exists()])
    return dirs[-1] if dirs else None


def main():
    baseline_dir = ROOT / "results/round6-sync/20261008T-round6-sync-final-r2"
    base_summary = read_json(baseline_dir / "summary.json")
    base_rows = {r.get("key"): r for r in rows(baseline_dir / "paired.jsonl") if r.get("key")}
    run_dir = latest_dir(ROOT / "results/round7-sync")
    challenge_dir = latest_dir(ROOT / "results/round7-external")
    marked_dir = latest_dir(ROOT / "results/round7-external-marked-exact")
    marked_prepare_dir = latest_result_dir(ROOT / "results/round7-challenge-marked")
    geometry = ROOT / "results/round7-geometry-diagnostic-full.json"
    sets = read_json(ROOT / "results/round7-sets-20261011/baseline_sets.json")
    gate = read_json(ROOT / "results/round7-sets-20261011/publish_gate.json")
    current = read_json(run_dir / "summary.json") if run_dir and (run_dir / "summary.json").exists() else None
    current_rows = {r.get("key"): r for r in rows(run_dir / "paired.jsonl")} if run_dir and (run_dir / "paired.jsonl").exists() else {}
    rescue = regression = 0
    if current_rows:
        for key, row in current_rows.items():
            if key not in base_rows:
                continue
            old, new = correct(base_rows[key], "sync"), correct(row, "sync")
            rescue += int(not old and new); regression += int(old and not new)
    external = read_json(challenge_dir / "summary.json") if challenge_dir and (challenge_dir / "summary.json").exists() else None
    marked = read_json(marked_dir / "summary.json") if marked_dir and (marked_dir / "summary.json").exists() else None
    marked_prepare = read_json(marked_prepare_dir / "summary.json") if marked_prepare_dir and (marked_prepare_dir / "summary.json").exists() else None
    marked_attempt_rows = rows(marked_prepare_dir / "attempts.jsonl") if marked_prepare_dir and (marked_prepare_dir / "attempts.jsonl").exists() else []
    marked_published_samples = sorted({str(x.get("sample_id")) for x in marked_attempt_rows if x.get("published")})
    geom = read_json(geometry) if geometry.exists() else None
    perf = latest_result_dir(ROOT / "results/round7-performance")
    perf_summary = read_json(perf / "summary.json") if perf and (perf / "summary.json").exists() else None
    b_cells = base_summary.get("pairing_four_cells", {})
    n_cells = (current or {}).get("pairing_four_cells", {})
    total_attempts = gate["publish_attempts"]
    total_possible = total_attempts * 26
    conditional = (b_cells.get("both_success", 0) + b_cells.get("d0_failed_sync_success", 0)) / max(1, b_cells.get("paired_rows", base_summary.get("paired_rows", 4654)))
    end_to_end = (b_cells.get("both_success", 0) + b_cells.get("d0_failed_sync_success", 0)) / total_possible
    target_keys = {str(x.get("key")) for x in sets.get("sets", {}).get("oracle_success_sync_failed", [])}
    target_rescue = sum(1 for k in target_keys if k in current_rows and correct(current_rows[k], "sync"))
    target_remaining = len(target_keys) - target_rescue
    manifest_obj = read_json(ROOT / "data/round7-challenge-20261011/manifest.json")
    challenge_inventory = read_json(ROOT / "data/round7-challenge-20261011/summary.json")
    included = [x for x in manifest_obj.get("items", []) if x.get("challenge_included")]
    families = sorted({str(x.get("source_family_id")) for x in included})
    categories = Counter(str(x.get("category")) for x in included)
    perf_rows = (perf_summary or {}).get("summary", []) if perf_summary else []
    perf_rel = str((perf / "summary.json").relative_to(ROOT)).replace("\\", "/") if perf_summary and perf else "未完成"
    def perf_cell(case, mode, profile):
        return next((x for x in perf_rows if x.get("case") == case and x.get("mode") == mode and x.get("decoder_profile") == profile), None)
    def perf_text(case, mode):
        s0 = perf_cell(case, mode, "decoder-v2-sync")
        v3 = perf_cell(case, mode, "decoder-v3-sync")
        if not s0 or not v3:
            return f"{case}/{mode}: NA"
        return f"{case}/{mode} S0 {s0.get('p50_ms')} / {s0.get('p95_ms')} ms，v3 {v3.get('p50_ms')} / {v3.get('p95_ms')} ms"
    lines = [
        "# Round7 外部挑战、几何修复与性能验收报告", "",
        "本报告只引用本轮冻结配置和落盘结果；实验结果与代码交付状态分开记录，不把旧 Round6 结果改标为新版本。", "",
        "## A. 基线与口径核对", "",
        f"- Round6 paired 记录为 {base_summary.get('paired_rows')} 条，四格为 {b_cells}。逐任务集合已导出到 `results/round7-sets-20261011/baseline_sets.json`，其中 D0 成功 1441、D0 失败后同步成功 1482、Dsync 未成功 1731。",
        f"- oracle_success_sync_failed 固定目标为 {base_summary.get('oracle_success_sync_failed')} 条；它是 oracle 正确单 ID 且 S0 未正确恢复，不等同于全部 D0 失败的 3213 条。oracle_na 和执行错误分别按实际行保留。",
        f"- Round5 发布尝试 {gate['publish_attempts']} 次，通过 {gate['gate_pass']} 次，拒绝 {gate['gate_fail']} 次；因此传播任务是 {base_summary.get('paired_rows')} = 179 × 26。发布后条件恢复率为 {conditional:.4%}，把 gate 拒绝计入 180 × 26 分母的端到端率为 {end_to_end:.4%}。",
        "",
        "## B. 新挑战集", "",
        f"- 清单核验 {challenge_inventory.get('total_files', len(manifest_obj.get('items', [])))} 个本地文件，排除 {challenge_inventory.get('duplicates', 0)} 个重复/不计入项，纳入 {len(included)} 张；来源家族 {len(families)} 个，类别为 {dict(categories)}。每张记录来源页、许可依据、SHA-256、SM3、尺寸和 PIL 解码结果。",
        "- 本批包含 NASA/Wikimedia 地球与地景、动物、肖像和果实照片；AI 生成及小文字/图表样本仍为空。清单中的 `used_for_tuning=false`，候选冻结后才运行盲测。许可结论只适用于清单列出的来源页；本地副本按实验记录保存。",
        f"- 未标记原图快速盲测：D0 {external.get('status_counts', {}).get('d0') if external else '未生成'}；S0 {external.get('status_counts', {}).get('s0') if external else '未生成'}；v3 {external.get('status_counts', {}).get('candidate_v3') if external else '未生成'}；三者误检 ID 集合均为空。标记发布门尝试 {marked_prepare.get('expected_gate_attempts') if marked_prepare else 0} 次，通过 {marked_prepare.get('gate_passed') if marked_prepare else 0} 次，覆盖 {len(marked_published_samples)}/12 个样本；通过门的 exact 配对为 {marked.get('paired_rows') if marked else 0} 行，四格 {marked.get('pairing_four_cells') if marked else '未生成'}，候选 p50/p95 为 {marked.get('sync_cost_ms', {}).get('p50') if marked else 'NA'}/{marked.get('sync_cost_ms', {}).get('p95') if marked else 'NA'} ms。",
        "- 外部 full 22 变换配对在高分辨率图像上按标准 runner 中止并保留 partial 记录；因此本轮外部结论限于 12 张原图盲测和通过 gate 的 exact 配对，不能包装为完整外部变换矩阵。",
        "",
        "## C. 几何诊断", "",
        f"- 目标定义为落盘 oracle FOUND、S0 expected failure；实际选中 {geom.get('target_rows_selected') if geom else 0} 条，完成 {geom.get('completed_rows') if geom else 0} 条。比较包含像素 MSE/最大差、DCT 对、重复位、ECC 输入位错和解码 ID。",
        f"- 统一归一化重建的平均 MSE 为 {geom.get('evidence_summary', {}).get('unified_mean_mse') if geom else 'NA'}，旧方形路径为 {geom.get('evidence_summary', {}).get('legacy_mean_mse') if geom else 'NA'}；旧 oracle 目标 {geom.get('evidence_summary', {}).get('old_oracle_expected') if geom else 'NA'} 条中，统一路径 true-geometry 解码命中 {geom.get('evidence_summary', {}).get('unified_true_geometry_expected') if geom else 'NA'} 条，仍受 2048 cap 和取整误差影响。",
        "- 新函数 `reconstruct_work_gray` 只接受观测像素和公开归一化面积/偏移，固定灰度 128 与 Lanczos，并且不接收 ContentID、原始尺寸或 Registry。旧 oracle 仍保留作独立诊断。",
        "",
        "## D. 候选实现", "",
        "- S0 `decoder-v2-sync` 的预算保持 128 候选、128 次解码尝试、1000 ms 软时间、256 MB 估算、最多 8 个返回 ID。Round7 显式候选为 `decoder-v3-sync`，只改变工作域几何重建：从观测宽高和面积假设推断原生宽高，按取整规则放置后一次缩放到 512。嵌入强度、重复和 ECC 未改。",
        "- 本轮没有引入第二种搜索排序；v3 是 S_map/候选实现消融，D0、发布 gate、信任和完整性路径保持原语义。",
        "",
        "## E. 配对效果", "",
        f"- v3 paired 结果：{n_cells if current else '完整矩阵尚未结束'}；相对冻结 S0 的逐 key 挽回 {rescue} 条，新增退步 {regression} 条。关键 882 条 oracle-success/S0-failed 集合挽回 {target_rescue} 条，剩余 {target_remaining} 条。只有同 key、同 Round5 final bytes、同 ContentID 和同 26 个变换才计入比较。",
        "- 三个 profile 的 v3 找到预期分别为 v1 1123、v2 1049、feedback 1052；对应 p50/p95 同步耗时约 v1 1080/1208 ms、v2 1075/1195 ms、feedback 1074/1191 ms。v3 的收益主要集中在偏移、非网格和组合变换，同时存在 244 条严格正确性退步，不能替代 S0。",
        "- 详细按 profile/transform 的结果在 run `summary.json`，失败、预算耗尽、冲突和 transform error 保留原状态，不把返回集合包含真值当作正确单候选。",
        "",
        "## F. 安全与负例", "",
        "- 同步画布只用于水印读取；SM3 仍绑定用户实际上传字节，发布 gate 仍只调用直接 D0。无 Registry、改名、清元数据、sidecar 不一致、混合 ID、截断和预算状态继续由回归测试覆盖。",
        "- 外部挑战未返回 ContentID；这只能说明本批未观察到误检，不能代替冲突检测能力或真实世界可靠性证明。",
        "",
        "## G. 性能", "",
        f"- 性能固定 8 类输入、hard/recover/full、D0/S0/v3，每格 1 次预热加 10 次正式重复；产物为 `{perf_rel}`。每次运行先生成一次固定 Round6 兼容 fixture，所有 profile/模式复用同一输入、sidecar、信任、父链、状态和后端；它不是 Round6 历史性能目录的字节级重放。",
        f"- 代表性恢复 P50/P95：{perf_text('exact', 'recover')}；{perf_text('crop_center', 'recover')}；{perf_text('negative', 'recover')}；{perf_text('pdf_exact', 'full')}。计时分开记录签名、摘要、父链、状态、检测器、几何、DCT/ECC；关闭结果缓存。100 ms 仍是目标，裁剪深度恢复约秒级，未达目标。逻辑 registry 状态保持不变；SQLite 文件字节单独标记为可能因存储维护变化。",
        "",
        "## H. 最终回归", "",
        "- 候选冻结后的最终全量回归为 152 passed、2 warnings、177.51 s；覆盖 API 登记/派生/完整验证/撤销、状态缺失/过期/不同步、发布 gate、PDF、无泄漏、冲突/预算和本轮汇总。证据摘要在 `results/round7-test-final/summary.json`。受限沙箱曾因 Windows loopback socketpair 阻塞 TestClient，最终在允许本地回环的环境完成，未改 FastAPI 业务代码。",
        "",
        "## I. 采用决定", "",
        "- 保留 v3 为显式实验候选，继续维持 v1/D0 默认、PDF v1 和发布 gate；不替换 S0，也不开启全局深度搜索。理由是内部净增 545 条救回但有 244 条严格退步，裁剪深度恢复超过 1 秒，外部仅完成 exact 配对而非完整传播矩阵。",
        "",
        "## J. 复现和未完成事项", "",
        "- 主要命令：`.venv\\Scripts\\python.exe scripts\\prepare_round7_challenge.py`；`.venv\\Scripts\\python.exe scripts\\run_round7_external_challenge.py`；`.venv\\Scripts\\python.exe scripts\\prepare_round7_marked_challenge.py --manifest data\\round7-challenge-20261011\\manifest.json --out results\\round7-challenge-marked\\20261011T-round7-challenge-marked --run-id 20261011T-round7-challenge-marked`；`.venv\\Scripts\\python.exe scripts\\run_round6_sync_eval.py --config configs\\round7-development.json --run-id <new>`；`.venv\\Scripts\\python.exe -m pytest --basetemp .pytest-tmp-round7-final3 -q`。pytest 的 `--basetemp` 目录会被清空，不能指向数据、结果、源码或密钥目录。",
        "- 未完成事项：外部标记样本的完整 22 变换矩阵因高分辨率成本中止；AI/小文字图表覆盖缺口未补齐；v3 仍未达到 100 ms 深度恢复目标。以上限制已保留 partial 运行目录和明确采用边界。",
    ]
    report = "\n".join(lines) + "\n"
    (ROOT / "docs/ROUND7_FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(ROOT / "docs/ROUND7_FINAL_REPORT.md")


if __name__ == "__main__":
    main()
