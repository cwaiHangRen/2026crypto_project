# v1 与 v2 结构差异表

| 领域 | v1 稳定基线 | v2 扩展 | 主要入口/证据 |
|---|---|---|---|
| 内容登记 | `service.py`、`registry.py` 的单内容登记与状态 | `v2_service.py`、`v2_store.py` 独立内容库，支持媒体描述、父版本和状态 | `gm-provenance-v2 register`、`/api/v2/register` |
| Manifest | `manifest.py`，v1 字段和摘要 | `manifest_v2.py`、`manifest_service.py` 固定字段顺序、NFC/JCS、长度前缀、key_id | `manifest-inspect`、`tests/v2/test_manifest_v2.py` |
| 密钥 | v1 CLI 管理发布密钥和信任边界 | `key_manager.py` 保存 key_id、有效期、前序关系、撤销状态和公钥指纹 | `keygen`、`key-import`、`key-rotate`、`key-revoke`、`key-status` |
| 水印/媒体 | 图片、PDF 的 v1 watermark 与 PDF 栅格流程 | `media_adapters.py` 统一图片、PDF、WAV，并对 MP3/FLAC/MP4 显式报告能力状态 | `run_v2_pdf_demo.py`、`tests/v2/test_media_adapters.py` |
| 批量凭证 | 无 Merkle 批量凭证 | `batch_proof.py` 固定排序叶子、root、proof、批量签名 | `batch-create`、`batch-proof`、`tests/v2/test_batch_audit.py` |
| 审计 | v1 运行日志和结果文件 | `audit_service.py` SM3 哈希链、checkpoint、断链定位和导出 | `audit-verify`、`logs/v2/audit-export.json` |
| 攻击评测 | 分散的 v1 攻击/回归脚本 | `attack_lab.py` 统一 AttackCase、JSON/CSV 矩阵和媒体级结果字段 | `attack-run`、`logs/v2/attack-matrix.json` |
| API | `gm_provenance.api:app` v1 API | `v2_api.py` 独立 FastAPI，二进制字节不进入 JSON 响应 | `/api/v2/*`、`tests/v2/test_v2_cli_api.py` |
| CLI | `gm-provenance` | `gm-provenance-v2` 增加密钥、Manifest、批量、审计、攻击、导出命令 | `src/gm_provenance/v2_cli.py` |
| 数据库 | v1 registry/state 数据库 | `v2.sqlite`、key manager、batch/audit 数据根与 v1 隔离 | `src/gm_provenance/v2_store.py` |
| 演示/报告 | v1 演示页和 Round 报告 | 10 个固定图片、真实 PDF 页面证据、轻量 v2 dashboard、设计报告和发布清单 | `scripts/run_v2_demo.py`、`scripts/run_v2_pdf_demo.py` |
| 测试 | v1 unit/integration/security 体系 | `tests/v2/` 独立测试；v1 默认路径未改写 | 当前 v2 回归 24 passed |

v2 默认安全 profile 继续复用 v1 watermark-v1、SM3/SM2 和最终编码字节 gate。水印只提供传播后的 ContentID 识别线索，Manifest、摘要、签名、版本链和撤销状态才共同决定验证结论。
