# 需求追踪

| 需求 | 模块 | 证据 |
|---|---|---|
| SM3 已知答案、SM2 签验 | `crypto.py` | `tests/unit/test_crypto.py`、`results/` |
| JCS Manifest 与最终字节摘要 | `manifest.py`、`service.py` | `tests/unit/test_manifest.py` |
| 随机 ContentID 与原子登记 | `service.py`、`registry.py` | `tests/unit/test_registry.py` |
| 图片盲水印 | `watermark.py` | `tests/unit/test_watermark.py`、`results/smoke-test/report.json`（scipy + RS(53,21)） |
| PDF 页面范围语义 | `media.py` | `tests/integration/test_pdf.py` |
| 授权派生与父链 | `service.py`、`registry.py` | `tests/integration/test_derivation.py` |
| 状态快照、撤销与最新版本 | `state.py`、`service.py` | `tests/integration/test_derivation.py`、`tests/security/test_state.py` |
| 分层 PASS/FAIL/UNKNOWN | `service.py` | `tests/security/`、`results/attack-matrix/`、`results/validation-installed/pytest-final.xml` |
| G0/G1/G2/G3 评测 | `benchmark.py` | `tests/integration/test_benchmark.py`、`results/benchmark-final/` |
| PDF 页级攻击与资源限制 | `scripts/pdf_qa.py` | `tests/security/test_pdf_attacks.py`、`results/pdf-qa/` |
| 中文 CLI/Web | `cli.py`、`api.py`、`ui/` | `results/demo-final/`、`results/validation-installed/api-report.json` |

未出现实际测试文件或命令输出的条目不能标记完成。
