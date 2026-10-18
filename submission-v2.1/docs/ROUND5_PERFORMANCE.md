# Round 5 性能模块

本轮性能消融由 `scripts/run_round5_performance.py` 执行，固定复用
`results/round4-performance/20261007T-round4-perf-final` 的 v1 root 媒体、
凭证、state 和 registry 文件。脚本不会生成新的 ContentID，也不会写入
固定 Round 4 目录。

四个互斥配置如下：

| 配置 | SM3 | DCT |
|---|---|---|
| P0 | OpenSSL CLI | scalar |
| P1 | 进程内 `hashlib.new("sm3")` | scalar |
| P2 | OpenSSL CLI | batch（仅 axes=(-2,-1)） |
| P3 | 进程内 hashlib | batch |

`OpenSSLCrypto(sm3_backend="auto")` 初始化时先检查 hashlib SM3，执行已知答案、
分块更新和与 CLI 的相同输入差分；只有全部通过才选 hashlib，否则回退 CLI。
显式 `hashlib`/`cli` 可用于配对实验。未使用 `usedforsecurity=False`。SM2
仍保持 OpenSSL CLI、User ID、ZA 和签名消息语义。

水印 DCT 默认仍为 scalar，以保持旧文件和边界判决兼容。`watermark.dct_backend`
上下文可切换到 batch；batch 将 8×8 块堆叠后只沿最后两个轴做 DCT/IDCT，块间
不发生变换。profile 会记录 `dct_mode`，便于核对实际配置。

建议在 CPU 空闲时运行（至少 1 次预热、3 次正式重复）：

```powershell
python scripts/run_round5_performance.py --run-id 20261007T-round5-perf-final
```

正式运行前应告知主评测负责人，避免与 60 图评测争用 CPU。结果目录包含
`run_manifest.json`（脏源码摘要、硬件和包版本、固定输入哈希）、`trials.jsonl`、
`summary.json` 和可读报告。`sm3_ms`、`watermark_ms`、`total_ms` 是互斥的无重度
插桩计时；`expected_outcome`、`actual_outcome` 和 `semantic_match` 分开记录，
不会把预期拒绝或实现错误混入成功耗时。
四个密码对象在计时循环前初始化并完成后端探测，探测和进程初始化不混入单次
`total_ms`；CLI SM3 的实际 OpenSSL 进程调用仍包含在 `sm3_ms` 内。

## 本轮实测结果

运行目录为 `results/round5-performance/20261007T-round5-performance-final/`，包含 1 次预热和
3 次正式重复，共 16 行（正式 12 行）。四个配置的语义结果均为 `FOUND`，
`semantic_mismatches=0`。正式 `total_ms` 如下：

| case | total mean ms | total p50 ms | total p95 ms |
|---|---:|---:|---:|
| P0 CLI + scalar | 218.025 | 219.978 | 234.070 |
| P1 hashlib + scalar | 211.054 | 211.744 | 215.278 |
| P2 CLI + batch | 191.488 | 190.465 | 194.263 |
| P3 hashlib + batch | 188.050 | 188.604 | 196.459 |

其中 P3 相对 P0 的本组均值下降约 13.7%，但正式重复数只有 3 次，结果仍属于本机诊断，
不能直接作为跨平台性能承诺。SM3 和水印检测分别计时，CLI 进程开销包含在 `sm3_ms` 内，
探测和对象初始化不计入单次 `total_ms`。
