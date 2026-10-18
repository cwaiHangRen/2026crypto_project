# Round9：默认方案 1000 样本规模评测与封版记录

本轮冻结现有密码协议、发布 gate、`watermark-v1`、`watermark-v2-residual`、`watermark-v2-feedback`、S0 `decoder-v2-sync` 和显式实验 v3 `decoder-v3-sync`。没有新增 profile，没有调整水印强度、纠错、反馈轮数、同步搜索预算、信任/签名/父链/撤销语义，也没有修改旋转/镜像或 PDF 截图模块。本轮只补充规模基线、统计分类和交接材料。

## 1. 样本清单和可复现性

规模清单位于 `data/round8-scale-1000-final2/`，包含 800 张图片和 200 份 PDF，逐行保存 `sample_id`、媒体类型、来源家族、来源引用、许可状态、原始路径、SHA-256、尺寸/页数、是否曾用于调参、兼容性和预处理信息。该目录以及结果目录均被 `.gitignore` 排除，仓库不携带数据本体。

清单摘要：

| 项目 | 实际值 |
|---|---:|
| 总行数 | 1000 |
| 图片 / PDF | 800 / 200 |
| 新增行 / 曾使用行 | 888 / 112 |
| source family | 35 |
| 精确重复组 / 缩略图重复组 | 0 / 0 |
| 输入兼容范围内 / 范围外 | 997 / 3 |
| 可审查许可 / 仅内部测试 | 18 / 982 |

逐行核查确认 1000 行对应 1000 个存在的本地文件路径，1000 个 SHA-256 唯一值，缺失文件为 0；`source_family_id` 共 35 个。`new_rows=888` 表示此前未出现在项目既有清单中的记录，`previously_used=112` 表示曾被项目使用；“新”不等于现实来源独立，也不等于新的分布。内部自制模板按 `source_family_id` 分组，属于受控协议样本，不能当成 982 个独立现实来源。

网络限制期间官方 COCO、Wikimedia、HuggingFace 端点 DNS 不可用，本轮没有绕过 TLS、登录或许可限制。结果因此是协议规模基线，不是 1000 条真实世界来源分布：图片中程序合成样本占大多数，AI 生成可核验样本为 0，小文字/图表样本为 783 条内部合成，外部真实 PDF 仅 1 条。缺口和来源分类见 `data/round8-scale-1000-final2/summary.json`；后续补充样本必须更新清单和许可证据，不能改写本报告的实际组成。

### 1.1 真实内容补证结果

本轮按“少量、可追踪、不下载大型归档”的要求尝试补充 20 张有生成来源记录的 AI 图片和 10 份外部真实 PDF，实际新增均为 0，因而没有对新增素材执行补充评测，也没有把数量写入原 1000 行分母。诊断分别覆盖 Windows 主机 PowerShell 和项目 `.venv` Python：系统代理开关为启用，代理为 `127.0.0.1:17890`，但该端口没有 TCP 监听；系统代理路径在本机代理 socket 处以 WinError 10013 失败，当前会话的直连路径又在 DNS（WinError 11001）处失败。没有修改全局代理、证书或 TLS 校验，也没有产生下载字节。详细记录在 `results/round9-correction-20261014/network_diagnostics.json` 和 `source_attempts.json`。候选入口只作为后续人工恢复网络后的来源线索： [DiffusionDB 官方数据集](https://huggingface.co/datasets/poloclub/diffusiondb) 和 [arXiv 官方入口](https://arxiv.org/)。已有本地 6 条 pilot 素材继续按原清单使用，但它们既不计入新增 AI 20 张，也不计入外部真实 PDF 10 份目标；其许可状态仍是 `review`。

为应对网络不可用，新增可直接运行的离线入口 `scripts/import_external_materials.py`。它读取 incoming 目录中的 `manifest.json`/`manifest.jsonl`，对实际图片/PDF 执行项目媒体检查，计算 SHA-256/SM3，检查与既有清单的重复，并将缺失的来源、许可或 source family 证据标为 `pending`；它不下载、不签名、不写 Registry。用现有 `data/external-pilot-20261006/manifest.json` 实测 6/6 文件校验通过、6/6 为既有哈希重复、6/6 仍待人工许可/来源审查，输出在 `results/round9-correction-20261014/local-import-pilot-20261015/import_report.json`。随后用同一 3 张 COCO 图片、2 张官方示例图和 1 份 arXiv PDF 执行现有 `scripts/test_dataset_pilot.py`，服务登记、hard/recover、图片 JPEG 传播和 PDF 逐页检查为 6/6 PASS；这只是小型 pilot 证据，不替代新增 AI/PDF 目标，也不把旧 pilot 重新计入 1000 行分母。

## 2. 默认方案 1000 行基线

配置为 `configs/round9-default-scale.json`：图片和 PDF 均使用默认 `watermark-v1`，发布使用 D0 gate，G0 保留无水印负例，G3 保留最终字节和 `full` 验证。为让规模运行保持可复现和可完成，本轮主层只测 `exact`，没有把 v2/v3、历史攻击矩阵、派生矩阵或 26 项传播矩阵冒充 1000 行主层结果。原有传播证据仍以 Round7/Round8 目录为准。

实际运行：

| 介质 | 基础尝试 | 发布通过 | gate 失败 | 结果目录 |
|---|---:|---:|---:|---|
| 图片 | 800 | 792 | 8 | `results/pilot-round3/20261014T-round9-default-images-800/` |
| PDF | 200 | 200 | 0 | `results/pilot-round3/20261014T-round9-default-pdfs-200/` |
| 合计 | 1000 | 992 | 8 | `results/round9-scale-audit-r2/` |

8 条图片失败均发生在发布 gate：2 条输入超过 16MP，6 条最终编码自检未恢复本次 ContentID。没有重试、换 ContentID、换 profile 或按成功样本替换分母；失败记录保留在图片运行的 `failures.jsonl`。

失败归属审计按签名正文中的 watermark profile 分类，而不是按目录名猜测：8/8 个 manifest 都是 `watermark_profile=none` 的合法 G1 signed-only 对照；2 个残留 SQLite 中的 2 条 `contents` 和 2 条 `assets` 也全部对应 G1，G3 Registry `contents` 为 0，版本均为 1，撤销记录为 0，没有 G3 最终发布媒体或状态快照。历史评测器的调用顺序是 `_signed_only()`（G1）先执行，再调用 `ProvenanceService.register()`（G3）；而且旧代码即使配置只请求 G0/G3 也会无条件创建 G1。因而这些记录不是失败 G3 提前签发，而是评测器把合法 G1 对照放进了 G3 共用 Registry，并在最小保留清理后留下了部分 G1 痕迹。生产 `register()` 的实际顺序仍是输入/授权检查→嵌入→最终字节 D0 自检→签名→`Registry.publish`，未发现真实 G3 gate 顺序违规。逐条证据见 `results/round9-correction-20261014-v2/gate_failure_artifact_audit.json`，审计脚本为 `scripts/audit_round9_gate_failures.py`。

本轮已做最小评测隔离修复：只有配置明确请求 G1 时才生成 G1；G1 使用独立的 `g1-registry`，G3 保持 `registry`；G3 gate 失败时不会生成 G1 产物，也不会产生 G3 签名或 Registry 记录。该修改属于“评测归属/隔离修复”，不改变生产密码协议或发布语义。

在全新目录 `results/round9-boundary-repro-20261015/` 对原 8 个失败输入逐一执行了同一冻结配置：8/8 均实际运行；两张超 16MP 图片仍输入拒绝，`r8-img-0142` 和 `r8-img-0226` 仍出现真实编码后的 gate 自检失败；另外 4 个原先自检失败样本在新的随机 ContentID/全新隔离环境中本次通过，说明该类失败具有输入/随机载荷相关性，不能把旧 6 条简单当成每次必现。新目录的 4 条失败均无签名 manifest、无 G3 `contents/assets`，只看到最多空的初始化 SQLite 表；审计结果见该目录下的 `gate_failure_artifact_audit.json`。

这里要区分几个分母：`992/1000` 是 1000 次端到端发布尝试中通过最终发布 gate 的次数；`992/992` 是对这 992 个最终发布字节执行 D0 `dct-difference-v1` 时预期 ContentID 命中的次数，正确单输出也是 992/992。G0 的 `0/992` 是独立 G0 无水印编码控制组的误报结果，不是对已发布 G3 最终字节再次做的负例测试；其 992 行均为 `NOT_FOUND`。原始输入无水印负例另有 `0/997`。8 个 gate 失败没有最终发布媒体，故没有对应发布字节 detector 记录，但仍留在 1000 次端到端尝试分母。逐行结果见 `results/round9-scale-audit-r2/default_d0.jsonl`、`default_negative_g0.jsonl` 和 `summary.json`。

本轮进一步对原始输入执行独立 D0 负例审计：997 条事先声明为 `in_scope` 的原始输入全部实际执行，`NOT_FOUND` 997/997、返回 ID 0、错误 0、未完成 0；其中 6 条后来因最终编码自检失败的图片也包含在这 997 条中。另有 3 条事先标为 `out_of_scope`（两张超过 16MP、一个 RGBA 输入），按输入校验统计保留为未尝试，不改写成 detector 失败。逐行记录见 `results/round9-correction-20261014-v2/raw_input_negative.jsonl`。

G3 完整验证也保留在原运行结果中。992 条成功发布行均有最终字节记录和 full 记录；`signature_valid`、`content_hash_match`、`issuer_trusted`、`parent_chain_status`、`authorization_status`、`content_revocation_status`、`key_revocation_status`、`latest_version_status`、`state_freshness` 九类字段均为 `PASS`，结论为 `VERIFIED_EXACT`（992/992）。图片成功行的 D0 recover P50/P95 为 540.827/791.749 ms，G3 full P50/P95 为 1156.362/1646.974 ms；PDF 对应 D0 recover 为 196.872/379.059 ms，G3 full 为 579.003/963.331 ms。这里的 G3 full 是发布后服务层完整检查，不能与 D0 的水印识别率混用。

固定质量诊断预先选择两个清单各前 10 行，共 20 行，流程均完成（20/20）。这是固定诊断子集，不是 1000 行全体质量结论。实际数值如下，MSE 越低越好，PSNR/SSIM 越高越好；`worst` 是同一指标的最差样本，未事后设置阈值：

| 图片对照 | MSE 均值 / 中位数 / 最差 | PSNR 均值 / 最差 dB | SSIM 均值 / 最差 |
|---|---:|---:|---:|
| A-B（无水印同编码控制） | 289.731890 / 79.438710 / 1096.404868 | 30.398887 / 17.731094 | 0.918936 / 0.778625 |
| A-C（原图/发布图） | 299.617593 / 87.651094 / 1116.740165 | 27.889160 / 17.651282 | 0.892481 / 0.764515 |
| B-C（控制/发布图） | 9.030087 / 7.915602 / 18.433801 | 39.259469 / 35.474655 | 0.971418 / 0.943757 |

10 份 PDF 共比较 27 个 120 DPI 页面：MSE 均值/中位数/最差为 537.942194/451.631509/894.058053，PSNR 为 20.963666/18.617146 dB（均值/最差），SSIM 为 0.878221/0.815305（均值/最差）。源文档平均 2.7 页，中位数 2 页；发布/源文件体积比均值 212.007 倍、中位数 243.995 倍、最大 267.114 倍。PDF 文字层 10/10 丢失，书签 10/10 丢失，链接只有 1 份检测到丢失、其余 9 份源文件没有链接，表单均为源文件无该功能。PDF 的 B 控制不适用，因为当前 PDF 路径没有无水印 PDF 编码器；渲染图不是阅读器真实截图。质量原始记录和参数见 `results/round9-correction-20261014-v2/summary.json`。

规模运行产物按类别核查：992 个 retained 最终发布字节共 824,071,188 bytes；隔离评测 Registry 的 JSON 记录 4,200,469 bytes、SQLite 425,943,040 bytes，状态快照 616,028 bytes。SQLite、状态和每样本复制文件属于评测隔离开销，不是产品部署 footprint；完整运行目录大小也不作为产品开销指标。

PDF 运行曾在多进程 PDFium 试跑中出现原生 `bad_variant_access`，最终以串行 workers=1 完成 200 条，业务代码没有为绕过该问题改变安全语义。日志中还保留 pypdfium2/fontTools 对 CFF 字体的提示，属于环境警告，不作为通过依据。

## 3. 裁剪同步冻结子集

配置 `configs/round9-crop-subset.json` 只复用 Round9 已发布的前 80 张图片最终字节，不重新嵌入、不重新登记、不挑选成功样本；每张执行 `exact` 和 `crop_center_area_0.95`，实际 profile 为 v1 + S0。结果在 `results/round9-scale-crop/20261014T-round9-crop-80/summary.json`：

| 变换 | D0/S0 配对结果 | S0 预期 ID |
|---|---:|---:|
| exact | 都成功 80/80 | 80/80 |
| 中心裁剪 95% | D0 失败/S0 成功 48，均失败 32 | 48/80 |

裁剪 S0 的 5 条为 `TIME_BUDGET_EXHAUSTED`、27 条为 `NOT_FOUND`，没有 D0 成功而 S0 失败的行。同步耗时 P50/P95/最大值为 936.413/1237.624/1329.612 ms，仍是秒级搜索，不满足 100 ms 目标。裁剪找回 ID 只提供来源线索；上传字节的 SM3、签名、父链、撤销和状态检查仍按原规则执行。

JPEG、缩放和组合传播没有混入 1000 行主层。已冻结的外部默认组合目录 `results/crop-handoff-default-s0/20261014T-crop-handoff-s0/` 另有 10 个媒体 × 26 个变换 = 260 条配对记录，其中包含 `jpeg_q50/q75/q90`、`resize_0.75`、`resize_jpeg_0.75_q75`、裁剪 JPEG、裁剪缩放和模拟截图等；其 v1+D0 为 93/260、v1+S0 为 200/260。Round9 新增的中心 95% 裁剪固定子集是另外 80 张图片，不能把这些覆盖量相加后称为 1000 张传播矩阵。PDF 质量诊断覆盖 10 份文档、27 个 120 DPI 重渲染页面，不是阅读器真实截图。

## 4. 传播统计和非法变换重分类

Round7 外部正向矩阵的 702/702 已发布组合任务保持完成，普通变换、裁剪、组合和压力/边界范围仍按原 `transform_range` 分开；其目录整体仍因 3 条原始 mixed-ID 负例缺少合法的两份发布媒体而为 `partial`。v3 的 464/702 是已发布组合上的条件结果，不能当作 1000 行默认 D0 成绩。

本轮新增 `scripts/reclassify_invalid_transforms.py`，读取 Round6/Round7 冻结 JSONL，生成 `results/round9-correction-20261014/reclassification/`。原始 paired JSONL 和 Round8 旧报告不被改写，映射保留 Round6/Round7 task ID 和 fingerprint。4654 条 paired row 的新分类为：

| 分类 | 数量 | 分母处理 |
|---|---:|---|
| `valid_propagation` | 2862 | 保留有效输入 detector 分母 |
| `valid_boundary` | 363 | 保留边界输入 detector 分母 |
| `resource_exhausted` | 1071 | 保留有效输入分母，显示超时 |
| `invalid_transform` | 358 | 参数在有效媒体前被拒绝，排除 detector 分母 |

无 paired task 缺失；task manifest 另有 248 条预期 negative 行，不把它们误报成 paired 缺失。详见 `invalid_transform_reclassification.json` 和 `task_id_mapping.jsonl`。

按输入类型和执行结果分开计算（Round7 的 `sync` 列是 v3 实验检测器，不能当作默认 S0）：声明支持范围 `in_scope + in_scope_offset + in_scope_combo` 为 2864 条，v3 正确恢复 2445/2864（85.37%），预算耗尽 419 条；边界/压力范围 `boundary + in_scope_unseen_grid + in_scope_combo_unseen_grid` 为 1432 条，正确恢复 779/1432（54.40%），预算耗尽 652 条、未找到 1 条；全部有效输入合计 4296 条，正确恢复 3224/4296（75.05%），预算耗尽 1071 条、未找到 1 条。D0 在有效输入上正确单输出 1441/4296、未找到 2855/4296；错误 ID、冲突和有效执行异常均为 0。358 条非法参数均正确拒绝 358/358，未进入 decoder 分母。逐 detector、逐输入类型的交叉表保存在 `results/round9-correction-20261014/reclassification/invalid_transform_reclassification.json`。

## 5. 完成状态和未完成目标

| 子集 | 状态 | 说明 |
|---|---|---|
| 内部配对审计 | complete | 4654 条逐条记录和重分类映射已保存 |
| 外部发布审计 | complete with limits | 27/36；支持范围前提下 27/28，限制在实验前已声明 |
| 真实内容补证 | blocked / 0 acquired | AI 0/20、外部真实 PDF 0/10；网络代理拒绝，未执行新增素材评测 |
| 外部正向矩阵 | complete / overall partial | 702/702 正向任务；整体仍缺 3 条原始 mixed-ID 负例 |
| 无水印负例 | complete for Round9 in-scope inputs | 原始输入 D0 0/997 误报；独立 G0 无水印编码控制组 0/992 误报；3 条越界输入单列未尝试 |
| mixed-ID | missing | 原外部三条仍缺合法双媒体；内部补充不能替代原任务 |
| 性能 | partial | 精确 D0/full、固定裁剪已测；S0 裁剪仍约秒级 |
| 全量回归 | complete | 本轮最终代码状态 `.venv` 全量 pytest 为 157 passed、2 warnings；隔离 FastAPI 验收 9/9 PASS |
| 整体 Round9 | partial | 真实内容补证、外部 mixed-ID、全体质量和 100 ms/95%目标仍未完成 |

因此不能宣称已达到 1000 条独立真实样本、95% 通用识别率或 100 ms 同步。裁剪模块可以按当前接口冻结用于团队集成，但应以 `partial` 状态交付，并明确 D0 默认、S0 主动开启、v3 显式实验的区别。

## 6. 复现命令和交接入口

```powershell
cd <project-root>
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe scripts\reclassify_invalid_transforms.py
.\.venv\Scripts\python.exe scripts\audit_round9_default_outputs.py --output results\round9-scale-audit-r2
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-round9-<唯一标识>
.\.venv\Scripts\python.exe scripts\run_round2_acceptance.py --output results\round9-api-acceptance
```

本轮调整了评测器的 G1/G3 产物隔离，但没有改变生产 API 的发布顺序或安全语义；最终代码状态 `.venv` 全量 pytest 为 `157 passed, 2 warnings`（158.00 秒），FastAPI 隔离验收为 `9/9 PASS`。新增导入入口和评测隔离的定向测试为 `6 passed`，并另行执行了 `py_compile`、`pip check` 和环境检查。警告为 Starlette/httpx TestClient 弃用提示和 Pillow 对 `I` 模式 PNG 的弃用提示，未进行依赖升级；受限沙箱中的 TestClient 会阻塞，完整回归和 API 验收均在允许本地回环的环境完成。

队友接口、字段、文件边界、演示成功/失败案例和独立数据库/密钥/token/结果目录要求见 [`docs/ROUND8_HANDOFF.md`](ROUND8_HANDOFF.md)。
