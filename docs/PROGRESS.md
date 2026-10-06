# 实施进度

## 已完成并实测

* 阶段 0：OpenSSL 3.6.2 能力探测；SM3 已知答案和 SM2 加密私钥、显式 User ID、DER 签验闭环。
* 阶段 1：随机 ContentID、最终媒体字节 SM3、严格 Manifest/JCS/fixed-domain 签名、SQLite 原子登记、图片 DCT 盲水印和 CLI demo 骨架。
* 运行时适配：应用自带 Python 可运行核心 smoke；在项目 `.venv` 中已实测 NumPy/Pillow/pypdfium2/reportlab/pypdf、scipy、reedsolo、rfc8785、FastAPI 和 pytest。无增强依赖时仍保留 NumPy DCT + CRC-only fallback，profile 会明确记录。
* 真实产物：`results/demo/demo-result.json`（历史发布证据）和 `results/demo-final/demo-result.json`（按 hard/full 状态语义重新生成）、`results/attack-matrix/attack-matrix.json`（重编码、Manifest 篡改、错误公钥、未受信发布者）、`results/pdf-smoke/`（单页 PDF 栅格发布与验证）、`results/evidence/doctor.json`。
* 小型四组 benchmark：`src/gm_provenance/benchmark.py` 已提供 G0/G1/G2/G3 统一接口、逐样本 JSONL、汇总 JSON 和 Markdown；配置化 JPEG/缩放/裁剪/元数据清除及模拟截图已实测一次。当前仅使用合成 PNG fixture，PDF 页面攻击明确标记为 N/A。
* 版本派生：`ProvenanceService.derive` 从已登记 ContentID、Manifest 或带水印媒体解析父版本，校验父摘要、发布者密钥和 `allowed_transformations`，生成版本号递增且带父 Manifest 摘要的新凭证，并通过 Registry 原子登记。
* 状态接入：`state-snapshot` CLI/服务使用独立状态密钥签发快照；验证器在有快照时依据签名状态检查内容/密钥撤销和最新版本，快照缺失或过期时相关状态保持 `UNKNOWN`。
* PDF 安全 QA：`scripts/pdf_qa.py` 对页删除、重排、未标记页、页面替换、多个 ContentID、实际 PDFium 渲染截图、模拟截图和输入限制完成 11/11 通过；证据在 `results/pdf-qa/`。
* 核心 smoke：`scripts/smoke_test.py` 在 `.venv` 中真实运行 crypto、水印、hard/full 状态语义、发布验证和重编码来源线索，全部通过；当前水印 profile 为 scipy DCT + Reed-Solomon RS(53,21)。
* 当前评测证据：`results/benchmark-final/` 为 G0-G3 各 11 条变换样本、4 项攻击均 handled=1 的 `LOCAL_SMOKE`；`results/derive-state-smoke/` 为版本 2 父链、授权和状态快照 PASS 证据。
* 中文演示页已补齐登记发布、文件验证、传播变换、版本链/撤销和四组评测入口；API 在 FastAPI 可用时提供真实服务层输出，不在页面写死指标。
* 完整 pytest：项目 `.venv` 中 `31 passed`；临时目录放在 `results/validation-installed/` 以避开本机系统临时目录权限问题。FastAPI 本地联调覆盖首页、doctor、benchmark、登记、验证、变换、历史、撤销和坏输入，报告在 `results/validation-installed/api-report.json`。
* 外部样本 pilot：`scripts/download_dataset_pilot.py --offline` 已能复用本地 6 个样本并重算 SHA-256/SM3，6/6 解码通过、0 个哈希不匹配；`scripts/test_dataset_pilot.py` 已加入媒体类型、大小限制、hard/recover 结论、PDF 逐页水印和图片传播副本的强断言，6/6 通过。`scripts/dataset_pilot_quality.py` 另行输出 5 个图片样本的 PSNR/全局 SSIM 诊断。
* 完整归档下载：已固定 COCO 2017 train/val/annotations 与 DocLayNet core/extra 的官方 URL 和响应大小，新增 `scripts/download_full_selected.py` 支持断点续传、大小、SHA-256 和 ZIP 样本检查；当前受网络吞吐约 43–64 KB/s 限制，只有 COCO train 的少量部分文件，完整归档尚未下载完成，状态记录在 `docs/FULL_DATASET_DOWNLOAD_STATUS.md`。

## 待真实测试或未完成

* 撤销接口目前是本地 CLI/回环演示服务调用，尚未接入远程认证授权；1000 样本、真实来源数据集和性能/质量报告尚未完成。
* benchmark 的 95%/P95 目标保持未评估，不作达标声明；音视频和 DOCX 原始文件能力尚未实现。

## 下一步

1. 将 PNG smoke fixture 扩展为有来源记录的图片和 PDF 集合，再进行规模化 benchmark；保持目标值与实测值分开。
2. 为远程撤销/状态查询增加明确认证边界，再考虑音视频扩展。
