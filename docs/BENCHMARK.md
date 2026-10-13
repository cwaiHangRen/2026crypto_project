# 小型四组评测

`gm_provenance.benchmark.run_benchmark` 提供一个固定、可复现的 smoke benchmark。它使用合成 PNG fixture 和真实的 `ProvenanceService`、Manifest/JCS/SM2 签名及 DCT 水印实现，统一输出四组：

| 组 | 能力 |
|---|---|
| G0 | 无凭证、无水印 |
| G1 | 仅签名凭证（sidecar Manifest，不嵌入水印） |
| G2 | 仅水印（无 Manifest） |
| G3 | 水印、签名凭证和 Registry 查询；版本链/撤销状态本轮 N/A |

示例运行：

```text
python -c "from gm_provenance.benchmark import run_benchmark; run_benchmark('results/benchmark-smoke', 'configs/attacks.yaml', sample_count=1)"
```

输出目录包含变换逐样本 `samples.jsonl`、攻击记录 `attacks.jsonl`、汇总 `summary.json` 和可读的 `BENCHMARK.md`。每条记录的 `truth` 由评测器保存，调用验证器时只传入媒体和该组真实可获得的 sidecar；真值、攻击名称和原始文件路径不会传给检测器。

当前配置覆盖 JPEG 质量 90/75/50、缩放 0.5/0.75/1.5、中心裁剪保留面积 0.95/0.90、90/180/270 度旋转、左右镜像后 0/90/180/270 度旋转、清除元数据，以及标记为 `simulated` 的截图近似。正交变换使用像素转置并保存为 PNG，避免混入额外 JPEG 损失。默认每组 18 条变换样本。PDF 删除页面、重排页面、重新渲染 DPI 和截图配置已冻结，但本轮 PNG-only fixture 将 PDF 标为 **N/A**，没有把模拟结果写成 PDF 实测。

G2 和 G3 的水印恢复用例必须同时满足最终结论正确、`watermark_status=FOUND`、恢复列表恰好等于期望编号列表；仅有 `ORIGIN_HINT_ONLY` 不计为恢复正确。独立旋转镜像评测由 `scripts/validate_rotation_mirror.py` 提供，它保存合成载体来源信息、逐输入记录、旧实现对照、负例、JPEG 额外重编码及真实服务验证字段，见 [ROTATION_MIRROR.md](ROTATION_MIRROR.md)。

评分同时依据实际字节是否改变，记录于 `truth.bytes_changed`。例如没有元数据的 PNG 在清除元数据后编码字节完全不变，仍应精确验证通过；不会仅因操作标签就要求摘要不匹配。G3 此类恢复调用仍须恢复出正确编号。

该评测是小规模合成机制检查，不是 1000 样本数据集，也不作 95% 识别率或 P95 延迟承诺。汇总中的目标和实测字段分开；未评估的目标保持 `未评估`/`N/A`。

每个样本还会运行四个本地攻击：携带原 Manifest 的内容替换、Manifest 字段篡改、签名字节篡改，以及把合法水印复制到无关 fixture。攻击记录与变换记录分开保存；内容替换与复制水印的结论遵循当前威胁模型，不能把来源线索当成内容完整性证明。
