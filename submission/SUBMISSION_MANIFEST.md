# 最终提交目录说明

本目录包含匿名提交版报告、源码、测试、配置、脚本、1000 行样本清单、真实 PDF 页面图、结构化日志和审批签字页占位文件。

- `design_report.docx`：可编辑中文报告。
- `design_report.pdf`：由同一报告内容生成并已用 PDFium 逐页检查的 PDF；本机缺少 LibreOffice，DOCX 原生转换日志记录为阻塞。
- `figures/`：总体架构、验证流程和真实 PDF 渲染/最终编码/确定性 JPEG 页面。
- `tables/`：从 Round9 JSON 日志生成的图片/PDF 分组、变换、延迟、质量和存储指标。
- `logs/`：匿名化 PDF 栅格化验收、Round9 汇总、六个失败样本诊断和报告构建状态；私钥、令牌、SQLite 和运行数据库不在此目录。
- `sample_data/`：真实 PDF 第一页和 1000 行清单副本（800 图片、200 PDF），以及清单审计摘要。
- `approval_page_separate/`：作品编号、日期和审批签字页占位。

Round9 的 1000 行清单是协议规模数据，内部合成样本占多数；AI 可核验样本 0/20、外部真实 PDF 0/10。输入质量门支持不超过 16MP，超限输入拒绝且不生成发布字节。报告保留这些缺口与 PDF 栅格化丢失文字层的限制。

验证状态：PDF 栅格化/截图定向回归 9/9 PASS；CLI、环境检查和 compileall PASS；历史 API 验收摘要 9/9 PASS。当前主机全量 pytest 与 API 现场复跑未完成，原因是部分测试/Starlette TestClient 在受限 Windows loopback 环境挂起，详见匿名化 logs/pytest-* 与 logs/api-acceptance-*。

人工必填：作品编号、提交日期、审批签字；提交电脑需用 Word/LibreOffice 原生复核 DOCX。
