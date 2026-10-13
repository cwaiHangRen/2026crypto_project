# 实施进度

## Round 7：外部挑战、几何候选与性能验收（2026-10-11）

* 已完成外部挑战清单：15 个本地文件中排除 3 个重复或不计入项，保留 12 张此前未用于调参、来源页和许可依据可追踪的图片；覆盖 12 个来源家族，含地球/地景、动物、肖像和果实照片。AI 生成及小文字/图表样本仍为空，不能据此宣称真实世界覆盖完整。
* 候选代码冻结后完成原图盲测：12 张未标记图片的 D0 均为 `NOT_FOUND`，S0/v3 只产生 `ERROR`、`NOT_FOUND` 或 `TIME_BUDGET_EXHAUSTED`，没有返回误检 ContentID。标记发布门 36 次尝试中 27 次通过，覆盖 10/12 个样本；exact 配对 27 行均由 D0/v3 正确恢复，0 冲突、0 错 ID。
* 基线集合已按逐行 task ID 导出：D0 成功 1,441、D0 失败而同步成功 1,482、Dsync 未成功 1,731、oracle 成功而同步未正确成功 882、oracle 不适用或无证据 2,265、输入/执行错误 358；task ID 4,654 个且无缺失/重复。Round5 发布尝试 180 次、通过 179 次、拒绝 1 次，传播任务仍为 `179 × 26 = 4,654`。
* 针对 882 条目标完成完整几何诊断：统一原生宽高/画布重建后平均 MSE 从 619.068876 降至 56.781891，true-geometry 离线命中 701/882；保留旧 oracle 作为独立证据，并补充非方形、边界、中心、平移、取整和 2048 cap 的独立测试。
* 新增显式 `decoder-v3-sync` 候选：只改变观测图像到工作画布的几何重建，S0 的 128 候选、128 次解码、1000 ms 软预算、256 MB 估算和返回上限保持不变；默认 v1、PDF v1、D0 发布 gate、信任/撤销/完整性语义均未改变。完整 4,654 条同 key 配对中，v3 挽回 545 条、严格退步 244 条；v1/v2/feedback 找到预期分别为 1,123/1,049/1,052。
* 性能矩阵完成 8 类输入 × hard/recover/full × D0/S0/v3，每格 1 次预热和 10 次重复；逻辑 registry 状态前后一致，缓存关闭。exact recover v3 P50/P95 为 223.610/238.562 ms；中心裁剪 recover 为 1,252.204/1,303.739 ms，100 ms 目标仍未达到。
* 最终 `.venv` 全量回归为 `152 passed, 2 warnings`（177.51 s）。受限沙箱中的 FastAPI TestClient 曾被 Windows loopback socketpair 阻塞，最终在允许本地回环的环境完成，未修改 API 业务代码。采用决定：v3 仅保留为手动显式实验候选，不替换 S0 或默认路径；外部完整 22 变换矩阵因高分辨率成本中止并保留 partial 记录。

## Round 6：盲几何同步评测修复与最终验收（2026-10-09）

* 已审计原始中断运行：405 条完整配对记录、无损坏/重复，旧 `running` 状态修正为 `interrupted`，停止原因保留为未知；原 JSONL 未截断。审计和复用清单在 `results/round6-sync/20261008T-round6-sync-final-audit/`。
* 已在冻结 execution fingerprint 下完成可恢复续跑：复用 405 条、补跑 4,249 条，最终 4,654/4,654 配对和 248/248 负例完成；运行器新增确定性 task ID、任务清单、尝试状态、原子 checkpoint、锁和矛盾重复拒绝。最终目录为 `results/round6-sync/20261008T-round6-sync-final-r2/`。
* 已修正 `oracle_success_sync_failed`：仅计算 oracle 命中预期 ID 且 Dsync 未正确唯一返回的任务，最终为 882 条，并保存可回溯 case ID。配对四格为都成功 1,441、D0 失败而 Dsync 成功 1,482、D0 成功而 Dsync 失败 0、都失败 1,731。
* profile 结果：v1 1,011 条找到预期、v2-residual 955、v2-feedback 957；三者均未出现 D0 成功而 Dsync 失败。中心裁剪 D0=0/537、Dsync=363/537；非网格 337/537；偏移 445/716；裁剪组合 198/358；非网格组合 98/179。详细分层和失败归因在 `docs/ROUND6_SYNC_FINAL_REPORT.md`。
* 失败归因按诊断独立分为映射/插值 956、网格/映射不足 1,367、载荷传播或证据不足 284、范围外 358、搜索预算 53、oracle 不适用/缺证据 195；实际混合 ID 负例未观察到 `CONFLICT`，不把它称作冲突能力通过。
* 已完成实际性能测量：8 个固定案例，recover/full × D0/sync，每格 1 次预热+10 次重复，共 352 条 trial；裁剪同步路径约 1.09–1.24 秒 P50，exact sync recover 218.298 ms P50，PDF full 278.879 ms P50。结果在 `results/round6-performance/20261009T-round6-recover-perf-final/`。
* 当前全量回归为 `131 passed, 2 warnings`；同步/汇总/API 定向套件为 `26 passed`。新挑战素材检查得到 0 个此前未使用且许可可追踪的外部图片，距离 12 个目标缺 12，外部泛化不宣称完成。`decoder-v2-sync` 继续保持手动开启的实验 profile，默认 v1、PDF 路径和发布 gate 不变。

## Round 5：发布自检、反馈候选与性能基线（2026-10-07）

* `media.assert_publishable` 已接入 `register` 和 `derive` 的登记边界：最终编码字节必须经盲检测恢复本次 ContentID，之后才计算摘要、签名并写入 Registry；失败只返回诊断，不产生登记或版本副作用。PDF 仍使用当前页范围的 v1 自检。
* 新增 `watermark-v2-feedback` 实验候选：复用 v1 payload、RS、DCT、repeat 和 strength，在原始分辨率保留浮点 residual，每轮重新编码并从最终字节读取系数，最多 3 轮、步长 4.0，并记录裁剪/残差质量预算。v1 仍为默认 profile，PDF 不自动切换到实验 profile。
* Round 5 固定配对评测已完成 60 张图片、三种 profile、9 类传播变换、180 个负例和 40 个 PDF。正式结果在 `results/round5-watermark/20261007T-round5-watermark-final/summary.json`；v1 gate 60/60、v2-residual 59/60、feedback 60/60，负例误报 0/180，PDF gate 与恢复均 40/40。中心 95% 裁剪的 D0 盲检测为 0/24，几何 oracle 为 24/24，当前主要瓶颈是同步。
* 新增 SM3 `auto/hashlib/cli` 探测和 scalar/batch DCT 消融；Round 5 性能结果在 `results/round5-performance/20261007T-round5-performance-final/summary.json`，四种配置语义一致，P0/P1/P2/P3 正式总耗时均值为 218.025/211.054/191.488/188.050 ms。本机结果属于诊断基线，不作跨平台承诺。
* 本轮完整 pytest 使用仓库内临时目录成功为 `105 passed, 2 warnings`；直接使用系统临时目录曾因 Windows 临时目录权限产生 setup errors，属于环境问题而非断言失败。

## Round 3：开发基线冻结（2026-10-07）

* 开发集已固定为 100 条：60 图片、40 PDF；其中 6 条为已有 pilot，94 条为明确标注的程序合成样本。当前 v3 清单 `data/round3-development-v3/summary.json` 记录 `unique_sha256=100`、重复行 0、13 个 source family；外部 pilot 的许可仍为 `review`，合成样本为 `internal-test-only`。
* 新增 `scripts/run_round3_evaluation.py`、`src/gm_provenance/development_eval.py` 和 `src/gm_provenance/quality.py`。统一评测使用真实服务层 G0/G1/G2/G3、hard/recover/full、图片/PDF 传播矩阵、sidecar 丢失、跨 ContentID、内容/密钥撤销、状态缺失和版本派生攻击；评测真值独立于检测器。
* v3 最终串行基线在 `results/pilot-round3/20261007T-round3-v3-final/`：100/100 基础样本、5200 条业务行、1360 条攻击记录（1320 条适用，40 条 PDF 派生 N/A）、0 failures；适用攻击 1320/1320 处理符合预期。水印使用 `reedsolo` + `scipy` 的 `watermark-v1`（512 工作平面、repeat=4、strength=34）。并发 PDFium 试跑出现原生后端错误，交付基线改用串行策略。
* A/B/C 质量证据覆盖 60 张图片和 40 个 PDF，图片保存 MSE、PSNR、11×11 局部 SSIM 和代表性裁剪；PDF 使用同 120 DPI 渲染并记录文字层、书签、链接、表单、几何和体积变化。质量与恢复统计保留真实分母，不将 handled 率写成鲁棒率；PDF 文字层与书签等栅格发布损失按实际内容单列。
* 本轮项目 `.venv` 完整回归为 `70 passed, 1 warning`；`results/round3-api-acceptance-project-venv/summary.json` 的 FastAPI/服务验收为 `9/9 PASS`。唯一警告是 Starlette/httpx 弃用提示。

## 已完成并实测

* 阶段 0：OpenSSL 3.6.2 能力探测；SM3 已知答案和 SM2 加密私钥、显式 User ID、DER 签验闭环。
* 阶段 1：随机 ContentID、最终媒体字节 SM3、严格 Manifest/JCS/fixed-domain 签名、SQLite 原子登记、图片 DCT 盲水印和 CLI demo 骨架。
* 运行时适配：应用自带 Python 可运行核心 smoke；在项目 `.venv` 中已实测 NumPy/Pillow/pypdfium2/reportlab/pypdf、scipy、reedsolo、rfc8785、FastAPI 和 pytest。无增强依赖时仍保留 NumPy DCT + CRC-only fallback，profile 会明确记录。
* 真实产物：`results/demo/demo-result.json`（历史发布证据）和 `results/demo-final/demo-result.json`（按 hard/full 状态语义重新生成）、`results/attack-matrix/attack-matrix.json`（重编码、Manifest 篡改、错误公钥、未受信发布者）、`results/pdf-smoke/`（单页 PDF 栅格发布与验证）、`results/evidence/doctor.json`。
* 小型四组 benchmark：`src/gm_provenance/benchmark.py` 已提供 G0/G1/G2/G3 统一接口、逐样本 JSONL、汇总 JSON 和 Markdown；配置化 JPEG/缩放/裁剪/元数据清除及模拟截图已实测一次。当前仅使用合成 PNG fixture，PDF 页面攻击明确标记为 N/A。
* 版本派生：`ProvenanceService.derive` 从已登记 ContentID、Manifest 或带水印媒体解析父版本，校验父摘要、发布者密钥和 `allowed_transformations`，生成版本号递增且带父 Manifest 摘要的新凭证，并通过 Registry 原子登记。
* 状态接入：`state-snapshot` CLI/服务使用独立状态密钥签发快照；验证器在有快照时依据签名状态检查内容/密钥撤销和最新版本，快照缺失或过期时相关状态保持 `UNKNOWN`。
* PDF 安全 QA：`scripts/pdf_qa.py` 对页删除、重排、未标记页、页面替换、多个 ContentID、实际 PDFium 渲染截图、模拟截图和输入限制完成 11/11 通过；证据在 `results/pdf-qa/`。
* 核心 smoke：`scripts/smoke_test.py` 在 `.venv` 中真实运行 crypto、水印、hard/full 状态语义、发布验证和重编码来源线索，全部通过；当前水印 profile 为 scipy DCT + Reed-Solomon RS(53,21)。
* 当前评测证据：`results/benchmark-final/` 为 G0-G3 各 11 条变换样本、4 项攻击均 handled=1 的 `LOCAL_SMOKE`；`results/derive-state-smoke/` 为版本 2 父链、授权和状态快照 PASS 证据。
* 中文演示页已补齐登记发布、文件验证、传播变换、版本链/撤销和四组评测入口；API 在 FastAPI 可用时提供真实服务层输出，不在页面写死指标。
* 完整 pytest：项目 `.venv` 中上一轮为 `60 passed, 1 warning`；Round 3 新增数据集、质量、评测和安全覆盖后为 `70 passed, 1 warning`。FastAPI 本地联调覆盖首页、doctor、benchmark、登记、验证、变换、历史、撤销、认证和统一上传限制。
* 外部样本 pilot：`scripts/download_dataset_pilot.py --offline` 已能复用本地 6 个样本并重算 SHA-256/SM3，6/6 解码通过、0 个哈希不匹配；`scripts/test_dataset_pilot.py` 已加入媒体类型、大小限制、hard/recover 结论、PDF 逐页水印和图片传播副本的强断言，6/6 通过。`scripts/dataset_pilot_quality.py` 另行输出 5 个图片样本的 A/B/C（原图、同编码无水印、完整水印）PSNR/11×11 RGB 局部 SSIM、固定代表性裁剪、512×512 工作尺寸和上采样记录，并对 PDF 做同 120 DPI 的逐页 A/C 渲染对照；这些都是本地 pilot 描述性指标，不构成阈值达标声明。
* 完整归档下载：已固定 COCO 2017 train/val/annotations 与 DocLayNet core/extra 的官方 URL 和响应大小，新增 `scripts/download_full_selected.py` 支持断点续传、大小、SHA-256 和 ZIP 样本检查；当前受网络吞吐约 43–64 KB/s 限制，只有 COCO train 的少量部分文件，完整归档尚未下载完成，状态记录在 `docs/FULL_DATASET_DOWNLOAD_STATUS.md`。
* Round 2 安全验收：`scripts/run_round2_acceptance.py` 使用隔离临时登记库、真实 SM2/SM3、独立状态密钥和 FastAPI TestClient，当前 `9/9 PASS`，结果在 `results/round2-api-delivery/summary.json`；覆盖无/错 token、无副作用、登记、派生、父链、当前/历史版本、内容/密钥撤销、状态刷新失败、缺失状态和 hard/recover/full 范围差异。

## 待真实测试或未完成

* 撤销接口目前是本地 CLI/回环演示服务调用，尚未接入远程认证授权；1000 样本和更大规模真实来源数据集仍未完成。Round 3 的100条开发集性能/质量报告已完成，但合成比例较高，不作真实世界鲁棒性声明。
* benchmark 的 95%/P95 目标保持未评估，不作达标声明；音视频和 DOCX 原始文件能力尚未实现。

## 下一步

1. 将 PNG smoke fixture 扩展为有来源记录的图片和 PDF 集合，再进行规模化 benchmark；保持目标值与实测值分开。
2. 为远程撤销/状态查询增加明确认证边界，再考虑音视频扩展。
