# Round 4 性能诊断

运行器是 `scripts/run_round4_performance.py`。它读取固定的
`data/round3-development-v3/dataset_manifest.jsonl` 子集：PNG、JPEG、单页
PDF、多页 PDF；图片包含 root、`jpeg-reencode`、`resize`，每个目标版本放在
独立 Registry 中，因此 full 校验时目标始终是 latest。

主实验 `paired_sidecar` 对每个版本运行 hard/recover/full 三种模式，三者
收到相同 sidecar、状态快照、公钥、信任库和父链。`lost_recovery` 单独测试
recover/full 无 sidecar；`no_provenance` 使用未发布原始文件；`invalid_input`
使用不支持的字节。后两组不与主实验混合解释。

每个输入/模式先预热 1 次，再测 3 次。未包装 baseline 与 instrumented 调用
交替执行，`summary.json.overhead_pairs` 直接报告观测开销、输入指纹和结果
一致性。诊断包装器不改变业务实现，也不作缓存或优化。

计时同时保存 `inclusive_ms` 和 `exclusive_ms`。前者包含嵌套子调用，不能把
各项相加；后者扣除已观测子调用，并与 `unattributed_ms` 一起解释总时长。
覆盖读取、媒体检查/解码、水印、PDF 渲染、SM3、JCS、SM2 验签、公钥转换、
父链、SQLite、状态和 doctor/version。每行还记录 OpenSSL 分类、SM3 输入摘要及重复
次数、公钥输入摘要、DB 方法/SQL 类别、每次 full 调用的状态完整重读次数。

最终诊断运行 `20261007T-round4-perf-final` 共写入 512 行（每个 cell 含 1 次预热），
正式测量 384 行，其中 instrumented 执行结果为 success 120、early_return
36、error 36；语义比较为 0 个 mismatch。正式 instrumented 行的 OpenSSL
分类累计为：SM3 540 次、SM2 验签 228 次、公钥转换 408 次、公钥检查 408
次；异常和非零返回均为 0。每个主实验 cell 的正式重复数为 3。

代表性正式测量（均为 instrumented、均值，单位 ms）如下：

| 场景 | 总耗时 | 水印 | PDF 渲染 | SM3 | SM2 验签 | 公钥转换 | 状态重建 |
|---|---:|---:|---:|---:|---:|---:|---:|
| PNG/root hard | 235.446 | 0 | 0 | 77.110 | 97.991 | 104.449 | 0 |
| PNG/root recover | 413.369 | 170.935 | 0 | 80.260 | 100.564 | 108.596 | 0 |
| PNG/root full | 516.490 | 159.374 | 0 | 80.988 | 208.093 | 167.016 | 109.626 |
| 多页 PDF/root recover | 595.798 | 149.398 | 54.051 | 80.671 | 105.528 | 116.437 | 0 |
| 多页 PDF/root full | 678.509 | 145.080 | 54.278 | 81.302 | 196.704 | 161.224 | 101.292 |

另外，未发布原始文件的 recover 走 early return（PNG 均值 117.014 ms），
不支持输入的 error 约 0.286 ms；这些诊断组没有与主实验成功路径混合汇报。

建议串行运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_round4_performance.py
```

每次运行生成新的 `run_id`、`config.json`、`run_manifest.json`、`trials.jsonl`
和 `summary.json`。manifest 保存 v3 数据哈希、源代码/脏工作区哈希、依赖、
doctor/version 结果和状态快照摘要。私钥只在运行时存在，结束后删除。
