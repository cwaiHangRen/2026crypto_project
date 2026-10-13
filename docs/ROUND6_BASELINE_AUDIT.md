# Round 6 A：Oracle、payload sensitivity 与 clipping 审计

审计脚本为 `scripts/audit_round6_baseline.py`，默认读取 Round 5 正式运行
`results/round5-watermark/20261007T-round5-watermark-final/`，每次写入新的
`results/round6-audit/<run_id>/`，不覆盖历史结果。

## Oracle 结论

Round 5 的 `D_oracle_geometry` 是 geometry-only：`oracle_geometry_fill` 只接收
裁剪字节与几何矩形，使用固定 `RGB(128,128,128)` 画布并贴入保留像素，不读取
缺失原图像素、source path、Registry 或 Manifest。D0 为 `media.detect(cropped)`，
oracle 为 `media.detect(oracle_bytes)`；预期 ContentID 在检测返回后由评测器
`strict_recovery` 比较。离线 `diagnose_image_*` 虽接收 CID，但标记
`oracle_not_production`，不进入恢复统计。

72 条 Round5 裁剪诊断中三个 profile 均为 D0=0/24、D_oracle=24/24；诊断 bit
error 分布为 v1：0（22 条）/1（2 条），v2 与 feedback：0（18 条）/1（4 条）/
2（1 条）/5（1 条）。这支持“主要瓶颈是几何不同步”的结论，同时保留少量
传播/解码误差作为独立现象。

## Payload 分母

逐行数据为 162 条：6 个 sample × 3 个固定 payload ID × 3 个 embedding profile ×
3 个变换。审计结果是 137/162（84.5679%），payload 子集 gate 失败 0 条；exact
发布载荷三种 profile 均为 18/18。传播恢复率分别为：v1 的 JPEG q75 为 16/18、
resize 0.75 为 15/18；v2-residual 与 v2-feedback 的 JPEG q75 均为 14/18、
resize 0.75 均为 12/18。相对 v1 的配对新增失败为 JPEG q75 各 2 条、resize
0.75 各 3 条，均集中于 `img-synth-004`（以及 JPEG 的 `img-synth-005`
payload 2）；比较按同一 sample/payload 配对，不用登记库过滤，也不删除 gate 失败分母。

## Clipping 语义

`clipped_fraction` 是载波加残差后，候选像素任一通道越过 `[0,255]` 的像素数除以
图像宽×高，在 `np.clip` 和 uint8 量化前统计。它是像素饱和比例，不是空间裁剪比例，
不能把它解释为保留面积。反馈诊断 61 个 round 中最大值为 0.046665，0 个超过
`max_clipped_fraction=0.08`；Round 5 的 crop 0.95 则是保留面积比例，两者分母和语义独立。

## 复现

```text
python scripts/audit_round6_baseline.py --round5-dir results/round5-watermark/20261007T-round5-watermark-final
```

本次结果目录：`results/round6-audit/20261007T-round6-baseline-audit-r2/`。
