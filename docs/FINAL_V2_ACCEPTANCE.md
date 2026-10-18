# gm provenance v2.1 最终验收记录

## 版本和隔离

v1 基线为 `a7b8e0783d5d80940028aeb9d33eb6f3538da95d`，v2 基线为 `eee6e62deea796d6a53b855297eaba55120c9da3`。本轮在独立 `v2.1` 分支完成，v1 工作区和 v2 提交历史未回写、未重写。v1-v2 模块差异见 `docs/V1_V2_DIFF.md`。

## 测试矩阵

| 类别 | 命令/证据 | 状态 | 说明 |
|---|---|---|---|
| v2 快速回归 | `pytest -q tests/v2` | PASS | 保留基线 24 passed |
| v2.1 新增测试 | `pytest -q tests/v2_1` | PASS | 15 passed，API、媒体能力、负例和安全边界 |
| 合并回归 | `pytest -q tests/v2 tests/v2_1` | PASS | 39 passed，1 个 Starlette/httpx 弃用警告 |
| 编译检查 | `python -m compileall -q src scripts tests/v2_1` | PASS | 返回码 0 |
| N30 性能 smoke | `scripts/run_v2_1_benchmark.py --count 30 --media image,pdf,wav` | PASS | 三类媒体各 30 登记、30 验证、30 proof |
| MP3/FLAC/MP4 能力 | `scripts/probe_media_codecs.py` | BLOCKED | 本机没有 ffmpeg/ffprobe；离线 fixture 只验证安全拒绝 |
| 完整 1000 行压力 | 未运行 | NOT_RUN | 仍需提交电脑单独执行 |
| 长时间几何搜索/大规模裁剪 | 未运行 | NOT_RUN | 不纳入核心门禁 |
| DOCX 原生渲染 | `render_docx.py --emit_pdf` | BLOCKED | 缺少 `pdf2image`，系统也没有 Word/LibreOffice |
| 同内容 PDF 页面检查 | `logs/v2/report-render/page-01.png`、`page-02.png` | PASS | 当前同内容 PDF 2 页已渲染检查 |
| 提交包审计 | `file_manifest.sha256.json` + zip 扫描 | PASS | 私钥、令牌、数据库、缓存、`.venv` 均排除 |

## N30 实测指标

| 媒体 | 操作 | 平均 ms | P50 ms | P95 ms | P99 ms | 吞吐量/s | 失败 |
|---|---|---:|---:|---:|---:|---:|---:|
| image | register | 623.725 | 601.121 | 696.952 | 799.140 | 1.603 | 0/30 |
| image | verify | 465.442 | 457.428 | 508.720 | 665.108 | 2.148 | 0/30 |
| image | Merkle proof | 0.307 | 0.293 | 0.357 | 0.449 | 3260.834 | 0/30 |
| pdf | register | 1026.920 | 1020.975 | 1097.369 | 1141.067 | 0.974 | 0/30 |
| pdf | verify | 468.820 | 465.028 | 505.148 | 511.030 | 2.133 | 0/30 |
| pdf | Merkle proof | 0.309 | 0.298 | 0.383 | 0.427 | 3236.944 | 0/30 |
| wav | register | 255.642 | 253.227 | 276.596 | 289.383 | 3.912 | 0/30 |
| wav | verify | 208.236 | 206.331 | 229.688 | 231.212 | 4.802 | 0/30 |
| wav | Merkle proof | 0.333 | 0.302 | 0.463 | 0.549 | 3002.582 | 0/30 |

本轮资源记录为 wall `93.257 s`、CPU `61.641 s`、CPU 利用率估计 `66.10%`、峰值 RSS `83.141 MB`。这些实测仍明显高于原方案图片/PDF P95 不超过 100 ms 的优化目标，不能把目标写成已达成。完整指标以 `logs/v2.1/performance-n30.json` 和 `tables/v2.1/performance-n30.csv` 为准。

## 编解码覆盖

图片、PDF、WAV 在离线 smoke 中 PASS。MP3、FLAC、MP4 适配器已经有统一能力边界和元数据接口，但当前机器 `ffmpeg`、`ffprobe` 均不在 PATH，因此真实重采样、转码、音量变化、片段截取、元数据清除、抽帧和变帧率测试均为 `NOT_RUN`，格式状态为 `BLOCKED_DEPENDENCY`，不是通过。

## API 与安全边界

v2.1 API 对上传大小、魔数/MIME、文件名路径穿越、ContentID 格式、批量数量和稳定错误信息进行校验。管理写操作可由 `GM_V2_API_BEARER` 启用 Bearer 鉴权；验证接口保持只读语义，返回值不含媒体二进制、私钥、用户名路径或临时数据库路径。新增测试覆盖重复验证审计、错误 key_id、撤销/旧批次 proof、审计篡改和请求重放。

## 保留的 v1 限制

Round9 总尝试 1000，gate `992/1000`；D0 只在已发布 992 个样本上运行并为 `992/992`；G0 为 `0/992`。中心 95% 裁剪 S0 为 `48/80`，P50/P95 为 `936.413/1237.624 ms`。AI 图片覆盖 `0/20`，外部真实 PDF 覆盖 `0/10`。这些数字与 v2.1 N30 smoke 分开报告，不能互相替代。

## 人工最后检查

提交电脑仍需完成：Word/LibreOffice 原生打开 DOCX 并逐页检查；若原生 PDF 与同内容 PDF 不一致，重新生成报告和清单；安装 FFmpeg 后补充 MP3/FLAC/MP4 真实测试；按机器条件运行完整 1000 行压力、长时间几何搜索和大规模裁剪；填写作品编号、日期和审批签字页。占位签字不得伪造成已审批。
