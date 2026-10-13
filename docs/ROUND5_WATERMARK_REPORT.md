# Round 5 水印严格配对评测

运行器为 `scripts/run_round5_watermark_eval.py`，配置为
`configs/round5-watermark-evaluation.json`。它创建独立的
`results/round5-watermark/<run_id>/`，不会覆盖 Round 4 结果。

## 固定配对

v1、v2-residual 与 `watermark_feedback`（兼容 `r5_feedback` 命名）三种方案对每张图片使用同一输入、同一
Round 4 确定性 ContentID（`round4|seed|sample_id|slot`）、同一编码格式、同一
`development_eval._image_transform` 变换和同一盲检测器。无水印 control 走已有
v1/v2 control helper；反馈方案不会改变 control。

## 发布 gate 与分母

每个最终编码字节先调用 `media.assert_publishable(final_bytes, cid,
media_type=...)`。通过 gate 才记为 `published`；异常对象的 diagnostics 和
`final_bytes` 摘要仍离线保存。`summary.json` 同时保存 attempted、gate passed、
gate failed、published recovery 与包含 gate 失败的端到端恢复率，拒绝样本不从分母删除。

## 裁剪诊断

`crop_diagnostics.jsonl` 包含 D0 盲检测和离线 `D_oracle_geometry`。Round 4 的
`crop=0.95` 定义为保留面积 95%，宽高分别乘 `sqrt(0.95)`，四舍五入后中心整点裁剪，
不再缩放；每行保存矩形和输出尺寸。本轮另测 0.90 与轻微水平偏移。oracle 只把
保留像素贴回原坐标的固定灰色填充画布，不复制原图丢失像素，也不把真实矩形传入
盲检测器；其恢复率明确排除在生产恢复统计之外，同时保留可用的位/字节误码诊断。

## PDF 回归

启用时按 manifest 排序串行处理现有 40 个 PDF，使用 v1 页面水印、最终字节盲检测和
相同发布 gate；失败行仍写入 `pdf_regression.jsonl`，不只抽测单个文件。

## 命令

```text
.\.venv\Scripts\python.exe scripts/run_round5_watermark_eval.py --config configs/round5-watermark-evaluation.json --pilot
.\.venv\Scripts\python.exe scripts/run_round5_watermark_eval.py --config configs/round5-watermark-evaluation.json
```

`--pilot` 只运行配置中的 6 张分层小子集（跳过 PDF 以避免在冻结参数时占用回归资源）；正式运行固定 60 张图片和
40 个 PDF。反馈模块不可用时，该 profile 的失败会保留为 blocked/failed 记录，不会
悄悄替换为 v1 或 v2。

## 正式运行结果

运行目录为
`results/round5-watermark/20261007T-round5-watermark-final/`，状态为
`completed`。三种 profile 的发布 gate 结果如下；`passed` 是最终编码字节经过盲检测并恢复当前
ContentID 后才计入的数量，失败仍保留在 attempted 分母中：

| profile | attempted | gate passed | gate failed | exact recovery | exact published recovery |
|---|---:|---:|---:|---:|---:|
| watermark-v1 | 60 | 60 | 0 | 60/60 | 60/60 |
| watermark-v2-residual | 60 | 59 | 1 | 59/60 | 59/60 |
| watermark-v2-feedback | 60 | 60 | 0 | 60/60 | 60/60 |

变换后的盲恢复率（每个 profile 均为 60 张）为：

| 变换 | v1 | v2-residual | v2-feedback |
|---|---:|---:|---:|
| JPEG q90 | 57/60 | 50/60 | 50/60 |
| JPEG q75 | 56/60 | 48/60 | 48/60 |
| JPEG q50 | 54/60 | 48/60 | 48/60 |
| resize 0.75 | 54/60 | 47/60 | 47/60 |
| resize+JPEG | 49/60 | 47/60 | 47/60 |
| format convert | 57/60 | 50/60 | 50/60 |
| metadata clear | 60/60 | 59/60 | 60/60 |
| screenshot simulated | 57/60 | 47/60 | 47/60 |
| center crop 95% area | 0/60 | 0/60 | 0/60 |

180 个负例检测中误报为 0。6 张分层样本上的 payload sensitivity 共 162 次，恢复 137 次。
反馈候选最多 3 轮、固定步长 4.0；正式 60 张中观测到的迭代次数为 59 次 1 轮、1 次 2 轮，
未超过上限；最终裁剪比例峰值为 0.0467。反馈与 v2 的图像质量几乎相同：v1 平均 PSNR/SSIM
为 25.5768 dB / 0.848258，v2 为 39.1707 dB / 0.971877，feedback 为 39.1693 dB /
0.971875。

裁剪诊断共 72 行。D0 使用真实裁剪后字节盲检测，三个 profile 均为 0/24；离线
`D_oracle_geometry` 在三个 profile 均为 24/24。oracle 结果已排除在生产恢复率之外，说明当前
主要问题是几何同步，而不是简单的载荷或 RS 解码失败。

PDF 回归固定处理 40 个文件，发布 gate 通过 40/40，最终文件盲恢复 40/40。完整明细见
`pdf_regression.jsonl`；旧的 9 页真实 PDF 也包含在这组回归中。

正式结果是本地诊断证据，不代表跨机器或跨编码器的稳定性。当前默认 profile 仍为 v1；
v2-residual 和 feedback 保留为实验候选，feedback 没有改善本轮变换恢复率，只修复了 exact
发布 gate 中 v2 的 1 个失败样本。
