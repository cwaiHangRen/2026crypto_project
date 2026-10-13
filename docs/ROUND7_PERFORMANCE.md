# Round 7 性能复测

本轮使用 `scripts/run_round7_performance.py` 复用 Round6 的服务、固定 fixture 和计时插桩，覆盖 exact、resize、中心裁剪、偏移裁剪、组合变换、无水印、混合 ID 和 PDF exact 八类输入；每类分别执行 hard/recover/full，D0、`decoder-v2-sync` 和 `decoder-v3-sync`，每格 1 次预热加 10 次正式重复，关闭结果缓存。

最终结果位于 `results/round7-performance/20261011T-round7-perf-final/summary.json`。计时记录签名、SM3、父链、状态、检测器、几何、DCT、ECC 和总耗时，并标明互斥项与包含项。逻辑 Registry 状态前后相同；SQLite 文件字节变化单独记录为存储维护现象，不能当作业务状态变化。

每次性能运行先生成一次 Round6 兼容的固定 fixture，随后所有 profile、模式和重复都复用同一批输入、sidecar、信任、父链、状态快照和后端配置；它不是对 Round6 历史性能目录的字节级重放。正确性收益/退步的主比较仍直接复用冻结 Round5 final bytes 和 Round6 变换记录。

代表性恢复耗时（P50/P95，毫秒）如下：

| 场景 | S0 `decoder-v2-sync` | v3 `decoder-v3-sync` |
|---|---:|---:|
| exact / recover | 222.577 / 249.814 | 223.610 / 238.562 |
| 中心裁剪 / recover | 1118.387 / 1152.334 | 1252.204 / 1303.739 |
| 无水印 / recover | 919.262 / 1094.565 | 1066.316 / 1077.631 |
| PDF exact / full | 305.435 / 338.493 | 312.670 / 334.163 |

100 ms 是目标值，深度同步在裁剪、负例路径上仍为秒级或接近秒级；本轮没有把 recover/full 改成更浅模式，也没有引入验证结果缓存或 SM2 后端迁移。v3 继续作为显式实验 profile，不改变默认 v1、PDF v1 或 D0 发布 gate。
