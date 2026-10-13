# Round 6 盲几何同步最终验收

日期：2026-10-09。本文记录 `decoder-v2-sync` 的冻结参数、原始检查点审计、可恢复续跑、统计修正、失败归因、性能实测和回归结果。同步检测仍是手动开启的实验功能；它只恢复水印来源线索，不放宽发布 gate，也不把裁剪文件变成原始文件的完整性通过。

## A. 中断与修复

原始运行目录 `results/round6-sync/20261008T-round6-sync-final/` 的 `run_manifest.json` 写着 `running`，但审计时没有发现匹配 `run_id`、启动信息和命令的评测进程，也没有退出记录，因此修正为 `interrupted`，停止原因写为“原因未知”。`paired.jsonl` 的 405 行均为完整 JSON，末尾有换行，SHA-256 为 `74ed1ca1a56c6125cb3e1f1452c11a0376985a7ca25f5f3df532b0e2527632dd`，没有半行、损坏行或重复键。原文件保持不变。

审计证据在 `results/round6-sync/20261008T-round6-sync-final-audit/`。审计计算出 4,654 条配对任务中可复用 405 条、缺失 4,249 条；前五个样本各有 78 条，`img-synth-001` 有 15 条，其余待补。

## B. 续跑、任务清单和版本

恢复运行目录为 `results/round6-sync/20261008T-round6-sync-final-r2/`。405 条记录按输入、Round 5 不可变产物和 execution fingerprint 复核后复用，4,249 条缺失配对任务补跑；最终得到 4,654/4,654 配对和 248/248 负例，`missing_task_count=0`，状态为 `completed`。

运行器现在保存确定性 `task_id`、`task_manifest.jsonl`、`task_states.json`、每次尝试的 `task_attempts.jsonl`，结果 JSONL 在 checkpoint 前先 `fsync`。恢复时会拒绝不属于冻结清单的键、execution/config 不一致的结果和矛盾重复记录；相同重复只保留一个分母项并报告重复，矛盾重复从正式汇总中排除并报告。旧 r2 清单缺少负例键，恢复时只追加缺失的冻结负例任务，不改变已有结果。

本次最终执行 fingerprint 为 `c1af3f7882e3413a496d82bd8aab7964763a81e94a6d0e5e72a65d2ec9a9f414`；统计 fingerprint 为 `1773d6d69726e18e759b2481fb51e98062b3e9bfbb1eec3a8c4fe29f9514a94c`；汇总器版本为 `round6-metrics-4`。两者分别记录执行语义和统计口径，不用 Git HEAD 代替。

## C. 统计口径修正

主指标使用“状态为 `FOUND`、返回唯一预期 ContentID 且没有冲突/结果截断”的 `correct_single_output`。`expected_id_hit`、`search_complete`、预算耗尽和冲突单独记录。`oracle_success_sync_failed` 只统计 oracle 在适用几何上找到预期 ID、而 Dsync 没有正确唯一返回的任务；重算为 **882 条**，对应完整 case_id 清单在 `summary.json` 的 `oracle_success_sync_failed_case_ids`。

4,654 条配对的四格为：

| D0 / Dsync | 条数 |
|---|---:|
| 都成功 | 1,441 |
| D0 失败、Dsync 成功 | 1,482 |
| D0 成功、Dsync 失败 | 0 |
| 都失败 | 1,731 |

因此本轮没有观察到 Dsync 对 D0 成功案例的回归，但这不等于证明完整几何空间无回归。

## D. 60 张配对结果

每条有效 profile 结果为 26 个冻结变换；v2 因 Round 5 gate 失败少一个样本，故为 1,534 行，其余 profile 为 1,560 行。括号依次为总行数、有效 detector 行、都成功、D0 失败而 Dsync 成功、Dsync 找到预期总数：

| profile | 结果 |
|---|---|
| `watermark-v1` | 1,560 / 1,440 / 513 / 498 / 1,011 |
| `watermark-v2-residual` | 1,534 / 1,416 / 463 / 492 / 955 |
| `watermark-v2-feedback` | 1,560 / 1,440 / 465 / 492 / 957 |

有效变换分层如下：

| 冻结类别 | 有效行 | D0 成功 | Dsync 找到预期 |
|---|---:|---:|---:|
| 普通非裁剪（7 种） | 1,253 | 1,115 | 1,115 |
| 中心裁剪 0.90/0.95/0.98 | 537 | 0 | 363 |
| 非网格面积比例 | 537 | 0 | 337 |
| 小范围水平/垂直偏移 | 716 | 0 | 445 |
| 裁剪+缩放/JPEG | 358 | 0 | 198 |
| 裁剪+组合非网格 | 179 | 0 | 98 |
| 边界压力 | 716 | 326 | 367 |
| 声明范围外 | 0（358 条变换错误） | 0 | 0 |

“Dsync 找到预期”包含 D0 快速路径；裁剪增益使用四格中的 `D0 失败、Dsync 成功` 读取，不能把普通变换混入裁剪恢复率。

## E. 失败归因

`results/round6-sync/20261008T-round6-sync-final-r2-failures/` 的 3,213 条失败行按记录诊断做互斥归因：

| 归因 | 条数 |
|---|---:|
| 映射/插值差异 | 956 |
| 网格或映射不足 | 1,367 |
| 载荷传播失败或证据不足 | 284 |
| 几何超出声明范围 | 358 |
| 搜索预算耗尽 | 53 |
| oracle 不适用/缺失证据 | 195 |

归因脚本不会把所有失败都归为“裁剪过强”；358 条范围外变换错误单独归为 `geometry_out_of_declared_range`。

## F. 无泄漏、误检和冲突

`detect_image_sync(data, config)` 的公开接口只有媒体字节和公开配置；同步候选不接收 ContentID、原图、真实尺寸、crop box、变换标签、路径、Registry 或 Manifest 答案。隔离进程、改名、清元数据、同尺寸不同内容、无 Registry 和 hard 模式边界测试均在定向套件中通过。

248 条负例分开保存：240 条无水印输入、6 条混合 ID 拼接、1 条畸形输入、1 条超限输入。实际结果为返回 ID 0 条、`CONFLICT` 0 条；无水印组有 18 条软超时，混合组有 4 条软超时。这里报告“未观察到冲突”，不称为真实混合冲突能力已通过。受控聚合单元测试另外验证了同 ID 去重、不同 ID 冲突、返回上限和预算状态。

## G. 预算与性能

冻结配置为最多 128 候选、128 次解码尝试、1,000 ms 候选间软时间预算、256 MB 估算内存预算、最多返回 8 个候选。完整配对同步耗时 P50/P95/最大值为 **781.753/1,141.001/1,418.812 ms**；2,629 条完成声明范围搜索，1,441 条走 D0 快速路径，另有 226 条在候选搜索后遇到时间边界；剩余 358 条是范围外变换错误、没有 detector 搜索结果。结果中明确记录 `search_complete` 和 `stop_reason`。256 MB 是工作数组与缓存的估算，不是进程 RSS 硬上限。

实际性能子集在 `results/round6-performance/20261009T-round6-recover-perf-final/`，计时前固定 8 个案例，每格 1 次预热+10 次正式重复，共 352 条 trial；检测结果缓存关闭，Registry 前后指纹一致。代表性 Dsync 端到端结果如下：

| 案例 / 模式 | P50 / P95 |
|---|---:|
| exact / recover | 218.298 / 258.975 ms |
| 中心裁剪 / recover | 1,093.974 / 1,172.031 ms |
| 裁剪+组合 / full | 1,032.751 / 1,052.080 ms |
| 无水印 / recover | 776.444 / 1,065.082 ms |
| PDF exact / full | 278.879 / 301.031 ms |

中心裁剪 recover 的组件 P50 约为签名 164.570 ms、检测器 927.612 ms、DCT 65.595 ms、RS/CRC 解码 235.215 ms；组件时间用于诊断，不能相加后冒充另一个端到端指标。完整组件分位数见 `summary-rebuilt.json` 与 `trials.jsonl`。

## H. 新挑战素材

仓库当前有 5 个可追溯外部图片（3 个 COCO、1 个 GenImage 官方示例、1 个 DocLayNet 官方示例），均已在 Round 5/6 使用，且许可状态仍为 `review`；55 个 synthetic 样本是 `internal-test-only`。此前未用于调参、许可可追踪的新挑战图片数量为 **0/12**，缺口 12，未将内部合成样本冒充外部泛化证据。证据见 `data/external-pilot-20261006/manifest.json` 和 `data/round3-development-v3/dataset_manifest.jsonl`。

## I. 最终回归

在当前工作区最终全量回归使用仓库内专用临时目录：**131 passed, 2 warnings**。警告是 Starlette/httpx TestClient 弃用提示和 Pillow 对 `I` 模式 PNG 的弃用提示，没有断言失败。定向同步/汇总/API 套件为 **26 passed**。全量测试命令和专用临时目录均保留在本地执行记录中。

## J. 验收决定

Round 6 的统计修正、检查点审计、冻结任务续跑、负例/预算分层、实际 recover/full 性能和全量回归已完成。由于缺少 12 个此前未使用且许可可追踪的新挑战图片，外部泛化验收仍未完成；因此结论为 **主验收完成，外部泛化部分完成**。`decoder-v2-sync` 继续保持手动开启的实验性深度来源恢复，不改默认 v1、PDF 路径或发布 gate。

## K. 复现入口

```powershell
cd <repo-root>
.\.venv\Scripts\python.exe scripts\audit_round6_checkpoint.py --input results\round6-sync\20261008T-round6-sync-final --output results\round6-sync\20261008T-round6-sync-final-audit
.\.venv\Scripts\python.exe scripts\run_round6_sync_eval.py --config configs\round6-sync-evaluation.json --run-id 20261008T-round6-sync-final-r2 --resume
.\.venv\Scripts\python.exe scripts\attribute_round6_failures.py --input results\round6-sync\20261008T-round6-sync-final-r2 --output results\round6-sync\20261008T-round6-sync-final-r2-failures
.\.venv\Scripts\python.exe scripts\run_round6_recover_performance.py --repetitions 10 --run-id 20261009T-round6-recover-perf-final
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-round6-full
```

以上命令会复用已完成任务；性能命令使用新的 run id。所有结果均保留在 `results/`，实验结果目录不记录 Git 提交或推送操作。
