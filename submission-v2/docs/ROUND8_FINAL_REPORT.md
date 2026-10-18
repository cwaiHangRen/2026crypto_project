# Round8 收尾验收报告

本轮冻结现有 watermark-v1、v2-residual、v2-feedback、S0 `decoder-v2-sync` 和实验性 `decoder-v3-sync`。没有新增嵌入 profile，没有改变强度、ECC、重复次数、反馈轮数、发布 gate 或签名/信任/撤销语义。

## 1. 配对口径与退步诊断

审计脚本 `scripts/audit_round8_external.py` 对 Round6 S0 与 Round7 v3 的 4,654 个相同 task key 逐条比较，结果为：

| 口径 | 数量 |
|---|---:|
| S0 正确单候选成功 | 2,923 |
| v3 正确单候选成功 | 3,224 |
| 两者都成功 | 2,679 |
| 仅 S0 成功（严格退步） | 244 |
| 仅 v3 成功（新增挽回） | 545 |
| 两者都失败 | 1,186 |

244 条退步和 545 条挽回分别导出到 `results/round8-audit/strict_regressions_244.json` 与 `new_successes_545.json`。退步按 profile 为 feedback 80、v1 87、v2 77；主要集中在 `crop_resize_jpeg_0.957`（79 条）、垂直偏移（109 条合计）和水平偏移（35 条合计）。

从退步清单中选取 24 条，覆盖三个 profile、中心裁剪、偏移、非网格、组合和边界变换，在相同最终字节上交错运行 S0/v3，各重复 3 次。S0 能找到的案例在三次复现中保持 `FOUND`；v3 对这些案例稳定出现 `TIME_BUDGET_EXHAUSTED`。v3 的观测时间约 1.1–1.6 秒，超过 1,000 ms 软预算；D0 输入和预期字节保持一致。诊断产物在 `results/round8-regression-diagnostic/representative_regressions.json`。检测器接口只输出候选数量、解码尝试和停止原因，不暴露候选坐标序列，也没有向检测器传入真值。

证据指向 v3 几何重建成本在固定预算下挤占候选搜索时间。本轮没有足够证据安全修改插值或候选网格，因此保留 v3 为显式实验选项，S0 继续作为默认同步版本。

## 2. 外部 36 次发布尝试

外部清单原有 15 个文件，按预先记录的重复、同一作品衍生和非自然测试图规则排除 3 个，纳入 12 个；排除决定不依赖本轮识别结果。36 次尝试中 v1 通过 10/12、v2 通过 9/12、feedback 通过 8/12，共发布 27 次，失败 9 次。16MP 总像素上限和实验 profile 对 alpha/调色板模式的限制在实验前已经写入 [`docs/WATERMARK.md`](WATERMARK.md) 并由单元测试覆盖，因此按声明的输入范围排除 8 次后，支持范围内为 28 次，其中 27 次通过；feedback 的 clipping quality budget 拒绝仍计入这 28 次，不能改称“不适用”。逐项记录和分类见 `results/round8-audit/external_gate_failures.json`。

失败分类如下：

| 类别 | 数量 | 证据 |
|---|---:|---|
| A 事先声明的输入范围 | 6 | Artemis 20,668,416 像素、Earth-from-space 16,838,656 像素，均超过 16MP；三个 profile 各 2 次 |
| B 预处理/模式/质量预算 | 3 | RGBA testimage1 的 v2/feedback 不支持 alpha；earthrise-cutout 的 feedback clipping fraction 超过质量预算 |
| C 编码/媒体错误 | 0 | 未观察到 |
| D 嵌入后盲检测失败 | 0 | 未观察到 |
| E 资源执行异常 | 0 | 未观察到 |
| F 证据不足 | 0 | 未观察到 |

9 次失败都在最终编码和发布 gate 之前结束，没有 Registry、版本或状态副作用。未对失败样本更换 ContentID、静默切换 profile 或删除样本。未发布的 9 个组合没有伪造变换媒体。

## 3. 默认组合与外部 22 变换矩阵

使用已有 10 个通过 gate 的外部 v1 最终发布字节，按同一冻结配置单独生成新的 S0 fingerprint，补测 10 × 26 = 260 行；没有重新嵌入、换载荷或选取其他 profile。默认组合结果为：

| 默认组合 | 完成 | 正确单候选 | 口径 |
|---|---:|---:|---|
| watermark-v1 + D0 | 260/260 | 93/260 | 普通精确检测 |
| watermark-v1 + S0 | 260/260 | 200/260 | 用户主动选择的裁剪/偏移恢复 |

其中 S0 的 `in_scope` 项为 96/100，未见网格为 28/30，偏移为 25/40，已见组合为 19/20，未见组合为 7/10，boundary 为 25/40，out-of-scope 为 0/20；合计支持范围内为 200/240。out-of-scope 的 20 行按输入错误保留，不计作支持范围成功率。补测目录为 `results/crop-handoff-default-s0/20261014T-crop-handoff-s0/`。

原计划的 22 个变换是配置中的 exact、JPEG/resize/metadata/format/screenshot 和 13 个裁剪/组合项；本次实际配置为 26 个，新增 4 个显式边界/范围检查：`crop_boundary_area_0.99`、`crop_boundary_area_0.80`、`crop_out_of_range_negative`、`crop_out_of_range_over_one`。新增项没有从分母中删除，实际外部矩阵为 27 × 26 = 702；原 22 项的结果与新增 4 项分别按 `transform_range` 保留。

原 partial 运行使用相同配置、输入、代码和 execution fingerprint 从 50 行续跑，最终生成 702/702 个基础 paired rows（27 个已发布组合 × 26 个变换）。矩阵任务本身已完成；完整目录为 `results/round7-external-marked/20261011T-round7-external-marked/`。

该目录整体仍标记 `partial=true`，因为 53/56 个负例完成，3 个原始 mixed-ID 负例缺少指定组合中的双份已发布媒体：`apollo17-blue-marble + artemis-earthrise` 缺 Artemis 发布字节，`artemis-earthrise + earth-from-space` 两份均受 16MP 限制，`earth-from-space + earthrise-cutout` 缺 Earth-from-space 发布字节。不能用其他组合覆盖原任务。三条原任务已有结果分别是 `ERROR`（输入超限）、`TIME_BUDGET_EXHAUSTED`、`TIME_BUDGET_EXHAUSTED`，均没有候选 ID，没有 `CONFLICT`。

外部正向矩阵统计为：both_success 248、D0 failed/S0-v3 success 216、both_failed 238、wrong ID 0、conflict 0；FOUND 464、TIME_BUDGET_EXHAUSTED 184、ERROR 54。按已发布组合的 702 行计算，v3 正确单候选为 464/702（66.10%）；普通变换、裁剪、组合和压力边界按 `transform_range` 分开记录，未把未发布的 9 个组合伪造成变换记录。端到端外部发布成功率仍为 27/36（75.00%），支持范围内发布成功率为 27/28（96.43%）。

v3 的 profile 分母分别为 v1 260 行、v2 234 行、feedback 208 行，正确单候选分别为 177、155、132。按实际变换范围统计：普通 `in_scope` 246/270，未见网格 69/81，偏移 34/108，已见组合 43/54，未见组合 5/27，boundary 67/108，out-of-scope 0/54；这些结果是已发布组合上的条件结果。

为核对混合媒体和 sidecar 语义，另用两张自制图片经正常签名发布流程生成两个不同 ContentID，构造 3 个内部拼接样本并分别运行 D0/S0/v3。D0 三次均为 `NOT_FOUND`，S0 三次均为 `NOT_FOUND`，v3 三次均为 `TIME_BUDGET_EXHAUSTED`；3 个 detector × 2 个原始 sidecar 的 18 次完整验证均保持 `content_hash_match=FAIL`、精确绑定通过数为 0，Registry 逻辑和 SQLite 字节均未改变。该补充结果保存在 `results/crop-handoff-closeout/internal-mixed-20261014-r2/`，只验证模块行为，不关闭原三条外部 missing 任务。

## 4. 性能与采用决定

完整矩阵的 v3 同步时间 P50/P95 为：v1 1,115.113/3,138.702 ms，v2 1,108.280/2,475.495 ms，feedback 1,115.113/3,138.702 ms（以目录 `summary.json` 的 profile 分组为准）。裁剪和组合路径仍未达到 100 ms 目标；预算耗尽是可见结果，不能按提前失败计作成功延迟。

本轮不实施算法小补丁。保留默认 v1、PDF v1、发布 gate 和 S0；v2-residual、v2-feedback、v3 继续由调用方显式选择。几何重建只服务水印读取，摘要仍绑定上传原始字节；裁剪恢复结果不能升级为原文件精确绑定通过。

当前仍未达到的目标：开发基线不是 1,000 条基础样本；没有证据支持 95% 的通用识别率；裁剪/组合恢复仍为秒级，未达到 100 ms 目标；外部集仍缺 AI 生成图和小文字/图表覆盖。以上目标不在本次封版中通过改口径解决。

## 5. 复现与交接

```powershell
.\.venv\Scripts\python.exe scripts\audit_round8_external.py --output results\round8-audit
.\.venv\Scripts\python.exe scripts\diagnose_round8_regressions.py --cases 24 --repetitions 3 --output results\round8-regression-diagnostic
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-round8-final
```

本轮实际验证结果：允许 Windows 本机回环后全量 pytest 为 `152 passed, 2 warnings`（189.25 秒）；`scripts/run_round2_acceptance.py --output results/round8-api-acceptance` 为 `9/9 PASS`；环境检查、`pip check` 和 `git diff --check` 均通过。警告仍是 Starlette/httpx TestClient 弃用提示及 Pillow 对 `I` 模式 PNG 的弃用提示，未因此升级依赖。

队友可在现有 API/CLI 中分别选择直接 D0、S0 深度同步和显式 v3 实验同步。界面应展示实际 decoder profile、候选状态、搜索完成标志、停止原因和耗时；v3 的结果不能解释为必然优于 S0。外部清单仍缺 AI 生成图和小文字/图表样本，3 个 mixed-ID 负例因没有两份可合法发布媒体而保留为未完成项。
