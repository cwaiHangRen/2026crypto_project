# Round7 外部挑战、几何修复与性能验收报告

本报告只引用本轮冻结配置和落盘结果；实验结果与代码交付状态分开记录，不把旧 Round6 结果改标为新版本。

## A. 基线与口径核对

- Round6 paired 记录为 4654 条，四格为 {'both_success': 1441, 'd0_failed_sync_success': 1482, 'd0_success_sync_failed': 0, 'both_failed': 1731}。逐任务集合已导出到 `results/round7-sets-20261011/baseline_sets.json`，其中 D0 成功 1441、D0 失败后同步成功 1482、Dsync 未成功 1731。
- oracle_success_sync_failed 固定目标为 882 条；它是 oracle 正确单 ID 且 S0 未正确恢复，不等同于全部 D0 失败的 3213 条。oracle_na 和执行错误分别按实际行保留。
- Round5 发布尝试 180 次，通过 179 次，拒绝 1 次；因此传播任务是 4654 = 179 × 26。发布后条件恢复率为 62.8062%，把 gate 拒绝计入 180 × 26 分母的端到端率为 62.4573%。

## B. 新挑战集

- 清单核验 15 个本地文件，排除 3 个重复/不计入项，纳入 12 张；来源家族 12 个，类别为 {'earthrise': 3, 'earth': 2, 'landscape': 2, 'animal': 3, 'portrait': 1, 'fruit-photo': 1}。每张记录来源页、许可依据、SHA-256、SM3、尺寸和 PIL 解码结果。
- 本批包含 NASA/Wikimedia 地球与地景、动物、肖像和果实照片；AI 生成及小文字/图表样本仍为空。清单中的 `used_for_tuning=false`，候选冻结后才运行盲测。许可结论只适用于清单列出的来源页；本地副本按实验记录保存。
- 未标记原图快速盲测：D0 {'NOT_FOUND': 12}；S0 {'ERROR': 2, 'NOT_FOUND': 3, 'TIME_BUDGET_EXHAUSTED': 7}；v3 {'ERROR': 2, 'TIME_BUDGET_EXHAUSTED': 10}；三者误检 ID 集合均为空。标记发布门尝试 36 次，通过 27 次，覆盖 10/12 个样本；通过门的 exact 配对为 27 行，四格 {'both_success': 27, 'd0_failed_sync_success': 0, 'd0_success_sync_failed': 0, 'both_failed': 0}，候选 p50/p95 为 131.571/425.807 ms。
- 外部 full 22 变换配对在高分辨率图像上按标准 runner 中止并保留 partial 记录；因此本轮外部结论限于 12 张原图盲测和通过 gate 的 exact 配对，不能包装为完整外部变换矩阵。

## C. 几何诊断

- 目标定义为落盘 oracle FOUND、S0 expected failure；实际选中 882 条，完成 882 条。比较包含像素 MSE/最大差、DCT 对、重复位、ECC 输入位错和解码 ID。
- 统一归一化重建的平均 MSE 为 56.781891，旧方形路径为 619.068876；旧 oracle 目标 882 条中，统一路径 true-geometry 解码命中 701 条，仍受 2048 cap 和取整误差影响。
- 新函数 `reconstruct_work_gray` 只接受观测像素和公开归一化面积/偏移，固定灰度 128 与 Lanczos，并且不接收 ContentID、原始尺寸或 Registry。旧 oracle 仍保留作独立诊断。

## D. 候选实现

- S0 `decoder-v2-sync` 的预算保持 128 候选、128 次解码尝试、1000 ms 软时间、256 MB 估算、最多 8 个返回 ID。Round7 显式候选为 `decoder-v3-sync`，只改变工作域几何重建：从观测宽高和面积假设推断原生宽高，按取整规则放置后一次缩放到 512。嵌入强度、重复和 ECC 未改。
- 本轮没有引入第二种搜索排序；v3 是 S_map/候选实现消融，D0、发布 gate、信任和完整性路径保持原语义。

## E. 配对效果

- v3 paired 结果：{'both_success': 1441, 'd0_failed_sync_success': 1783, 'd0_success_sync_failed': 0, 'both_failed': 1430}；相对冻结 S0 的逐 key 挽回 545 条，新增退步 244 条。关键 882 条 oracle-success/S0-failed 集合挽回 545 条，剩余 337 条。只有同 key、同 Round5 final bytes、同 ContentID 和同 26 个变换才计入比较。
- 三个 profile 的 v3 找到预期分别为 v1 1123、v2 1049、feedback 1052；对应 p50/p95 同步耗时约 v1 1080/1208 ms、v2 1075/1195 ms、feedback 1074/1191 ms。v3 的收益主要集中在偏移、非网格和组合变换，同时存在 244 条严格正确性退步，不能替代 S0。
- 详细按 profile/transform 的结果在 run `summary.json`，失败、预算耗尽、冲突和 transform error 保留原状态，不把返回集合包含真值当作正确单候选。

## F. 安全与负例

- 同步画布只用于水印读取；SM3 仍绑定用户实际上传字节，发布 gate 仍只调用直接 D0。无 Registry、改名、清元数据、sidecar 不一致、混合 ID、截断和预算状态继续由回归测试覆盖。
- 外部挑战未返回 ContentID；这只能说明本批未观察到误检，不能代替冲突检测能力或真实世界可靠性证明。

## G. 性能

- 性能固定 8 类输入、hard/recover/full、D0/S0/v3，每格 1 次预热加 10 次正式重复；产物为 `results/round7-performance/20261011T-round7-perf-final/summary.json`。每次运行先生成一次固定 Round6 兼容 fixture，所有 profile/模式复用同一输入、sidecar、信任、父链、状态和后端；它不是 Round6 历史性能目录的字节级重放。
- 代表性恢复 P50/P95：exact/recover S0 222.577 / 249.814 ms，v3 223.61 / 238.562 ms；crop_center/recover S0 1118.387 / 1152.334 ms，v3 1252.204 / 1303.739 ms；negative/recover S0 919.262 / 1094.565 ms，v3 1066.316 / 1077.631 ms；pdf_exact/full S0 305.435 / 338.493 ms，v3 312.67 / 334.163 ms。计时分开记录签名、摘要、父链、状态、检测器、几何、DCT/ECC；关闭结果缓存。100 ms 仍是目标，裁剪深度恢复约秒级，未达目标。逻辑 registry 状态保持不变；SQLite 文件字节单独标记为可能因存储维护变化。

## H. 最终回归

- 候选冻结后的最终全量回归为 152 passed、2 warnings、177.51 s；覆盖 API 登记/派生/完整验证/撤销、状态缺失/过期/不同步、发布 gate、PDF、无泄漏、冲突/预算和本轮汇总。证据摘要在 `results/round7-test-final/summary.json`。受限沙箱曾因 Windows loopback socketpair 阻塞 TestClient，最终在允许本地回环的环境完成，未改 FastAPI 业务代码。

## I. 采用决定

- 保留 v3 为显式实验候选，继续维持 v1/D0 默认、PDF v1 和发布 gate；不替换 S0，也不开启全局深度搜索。理由是内部净增 545 条救回但有 244 条严格退步，裁剪深度恢复超过 1 秒，外部仅完成 exact 配对而非完整传播矩阵。

## J. 复现和未完成事项

- 主要命令：`.venv\Scripts\python.exe scripts\prepare_round7_challenge.py`；`.venv\Scripts\python.exe scripts\run_round7_external_challenge.py`；`.venv\Scripts\python.exe scripts\prepare_round7_marked_challenge.py --manifest data\round7-challenge-20261011\manifest.json --out results\round7-challenge-marked\20261011T-round7-challenge-marked --run-id 20261011T-round7-challenge-marked`；`.venv\Scripts\python.exe scripts\run_round6_sync_eval.py --config configs\round7-development.json --run-id <new>`；`.venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-round7-final3 -q`。pytest 的 `--basetemp` 目录会被清空，不能指向数据、结果、源码或密钥目录。
- 未完成事项：外部标记样本的完整 22 变换矩阵因高分辨率成本中止；AI/小文字图表覆盖缺口未补齐；v3 仍未达到 100 ms 深度恢复目标。以上限制已保留 partial 运行目录和明确采用边界。
