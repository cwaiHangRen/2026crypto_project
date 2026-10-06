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

当前配置覆盖 JPEG 质量 90/75/50、缩放 0.5/0.75/1.5、中心裁剪保留面积 0.95/0.90、清除元数据，以及标记为 `simulated` 的截图近似。PDF 删除页面、重排页面、重新渲染 DPI 和截图配置已冻结，但本轮 PNG-only fixture 将 PDF 标为 **N/A**，没有把模拟结果写成 PDF 实测。

该评测是小规模合成机制检查，不是 1000 样本数据集，也不作 95% 识别率或 P95 延迟承诺。汇总中的目标和实测字段分开；未评估的目标保持 `未评估`/`N/A`。

每个样本还会运行四个本地攻击：携带原 Manifest 的内容替换、Manifest 字段篡改、签名字节篡改，以及把合法水印复制到无关 fixture。攻击记录与变换记录分开保存；内容替换与复制水印的结论遵循当前威胁模型，不能把来源线索当成内容完整性证明。
