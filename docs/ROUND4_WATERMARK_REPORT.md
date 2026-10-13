# Round 4 水印配对评测报告

状态：`completed`；run_id：`20261007T-round4-v2-final`；样本：60（目标 60 张图片）。

## 评测设计

每个样本固定同一输入、同一 ContentID、同一输出格式/编码参数和同一变换，形成 A（原图）、B1/C1（v1 无水印/有水印）、B2/C2（v2 无水印/有水印）配对。C1/C2 复用每个样本的确定性 ID；payload sensitivity 子集固定 3 个样本、每样本 3 个 ID。

## 审计结论

- 质量聚合：round4 按样本、变换、profile 保存六对指标；`mean_perPSNR` 与 `PSNRfrommeanMSE` 都是有效且并列报告的聚合视角，并记录有限分母与零 MSE。质量差异同时报告 signal delta（B/C）与 endpoint delta（A/C），改善/退步计数按 endpoint delta。
- G2/G3 因果性：round3 的 G2/G3 ID 独立随机生成；本轮固定 ID 并记录每条变换的字节摘要。
- 计时组成：本轮按 `profile/role/transform` 严格分组，保留 n、均值、P50、P95，避免 sidecar/mode 混合。

## 结果

- 配对质量：`paired_quality.jsonl`；检测记录：`detections.jsonl`；配对恢复：`paired_recovery.jsonl`；计时：`timings.jsonl`；payload：`payload_sensitivity.jsonl`；失败分类：`new_failures.jsonl`。
- 汇总：`summary.json`。每个 profile/变换的 robust 状态同时要求检测到预期 ContentID；NOT_FOUND、CONFLICT、错误和缺少 helper 都保留在分母或 blocked 记录中。
- PDF 回归（若依赖可用）：`pdf_regression.json`；PDF v2 若接口明确不支持则记为 N/A，不冒充通过。

### Exact 配对质量

下表使用同一输入的 exact 单元；`mean PSNR` 是逐样本 PSNR 的算术平均，
`PSNR(MSE)` 是先平均 MSE 再换算，二者都保留。v2 的 A/B2 有 57/60 个
PNG 单元在最终编码后与原图逐像素相同，因此该组的零 MSE 数量单独列出。

| 配对 | v1 mean MSE | v2 mean MSE | v1 mean PSNR | v2 mean PSNR | v1 PSNR(MSE) | v2 PSNR(MSE) | v1 median SSIM | v2 median SSIM | v2 zero MSE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A-B（无水印控制） | 552.050759 | 1.035750 | 28.996697 | 35.263179 | 20.711013 | 47.978252 | 0.912190 | 1.000000 | 57/60 |
| A-C（发布结果） | 562.327613 | 10.009685 | 25.576814 | 39.170702 | 20.630910 | 38.126599 | 0.905208 | 0.973478 | 0/60 |
| B-C（水印信号） | 8.666748 | 8.999710 | 39.543952 | 39.462993 | 38.752242 | 38.588518 | 0.976171 | 0.973719 | 0/60 |

按 A-C endpoint PSNR，v2 在 60/60 个样本上高于 v1；但 B-C signal PSNR
在高纹理、细线和小文字类别出现轻微下降，说明“保留原图细节”与“信号
可见度/鲁棒性”仍有权衡，不能把 endpoint 改善解释为所有类别都更适合。

### 变换鲁棒性

`robust` 要求检测到预期 ContentID；每一格分母均为 60，控制组两种 profile
在全部变换上均为 60/60 `NOT_FOUND`。

| 变换 | v1 | v2 |
|---|---:|---:|
| exact | 60/60 | 59/60 |
| JPEG Q90 | 57/60 | 50/60 |
| JPEG Q75 | 56/60 | 48/60 |
| JPEG Q50 | 54/60 | 48/60 |
| resize 0.75 | 54/60 | 47/60 |
| resize + JPEG Q75 | 49/60 | 47/60 |
| crop 0.95 | 0/60 | 0/60 |
| 清除元数据 | 60/60 | 59/60 |
| 格式转换 | 57/60 | 50/60 |
| 模拟截图 | 57/60 | 47/60 |

payload sensitivity 子集为 6 个样本、3 个 ContentID、3 种变换，共 108 个
单元：v1 恢复 49/54，v2 恢复 44/54；完整记录仍保留在
`payload_sensitivity.jsonl`。相对 v1，v2 共出现 49 个新的预期 ID 解码失败：
exact 1、metadata_clear 1、format_convert 7、JPEG Q90/Q75/Q50 分别
7/8/6、resize 7、resize+JPEG 2、模拟截图 10；crop 不计为新的失败，
因为 v1 同样为 0/60。

### PDF 回归与运行状态

使用真实的 9 页 PDF 回归，v1 在 9/9 页检测到预期 ContentID，
`correct_found_and_content_id=true`；PDF 发布接口继续固定使用 v1，v2 明确
记录为 `N/A_PDF_PROFILE_UNSUPPORTED`。完整回归为 87 passed、2 个依赖版本
相关弃用警告，未出现失败或 blocked 项。

## 阻塞与限制

无阻塞记录。

复现命令：

```text
.\.venv\Scripts\python.exe scripts/run_round4_watermark_eval.py --config configs/round4-watermark-evaluation.json
```


本报告对应结果目录：`results/round4-watermark/20261007T-round4-v2-final`。
