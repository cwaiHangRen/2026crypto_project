# v2.1 现场演示脚本

建议时长 8 至 10 分钟。脚本完全离线运行，所有命令都在 v2.1 工作区根目录执行。演示前先确认 Python 3.11+、OpenSSL/SM2-SM3 后端、Pillow、pypdfium2、reportlab 和 pypdf 已安装；演示不需要访问网络，也不需要把私钥复制到报告目录。

## 0. 启动与状态检查（约 1 分钟）

```powershell
$py = ".\.venv-v2\Scripts\python.exe"
& $py -m gm_provenance.v2_cli --help
& $py scripts\probe_media_codecs.py --output logs\v2.1\codec-capability.json
```

预期：CLI 能列出 key、register、verify、batch、audit 和 attack 命令；能力报告明确显示本机 MP3/FLAC/MP4 为 `BLOCKED_DEPENDENCY` 时，不把它们说成已支持。

## 1. 图片登记、验证和批量证明（约 2 分钟）

```powershell
& $py scripts\run_v2_demo.py
Get-Content logs\v2\demo-summary.json
Get-Content logs\v2\audit-export.json -TotalCount 25
```

预期：10 个固定 PNG 完成登记和验证，摘要显示 10 叶 Merkle root；审计摘要显示事件链有效并包含 checkpoint。演示界面可用 `uvicorn gm_provenance.v2_api:app --host 127.0.0.1 --port 8001` 启动后打开 `http://127.0.0.1:8001/`。

## 2. 真实 PDF 120 DPI 栅格化（约 1.5 分钟）

```powershell
& $py scripts\run_v2_pdf_demo.py
Get-Content logs\v2\pdf-demo\pdf-raster-log.json
```

预期：日志同时列出原始 PDF 摘要、120 DPI/RGB 页面尺寸、rendered PNG、最终编码 PNG、恢复出的 ContentID 和 gate。备用路径是直接查看 `logs/v2/pdf-demo/pages/page-001-rendered.png` 与 `page-001-final.png`。

## 3. 攻击矩阵与可解释结果（约 1 分钟）

图片攻击矩阵已经由演示脚本生成；若现场需要单独重跑，使用已有发布字节作为输入：

```powershell
& $py scripts\run_v2_attack_matrix.py <published-image> --content-id <content-id> --output results\v2-attack
```

展示 `attack-matrix.json` 或 `attack-matrix.csv` 中的 `watermark_recovered`、摘要/签名/版本链/撤销字段，以及 `REJECTED_OR_NOT_FOUND` 的失败阶段。备用路径是直接展示提交包中的 `logs/v2/attack-matrix.json`。

## 4. 密钥轮换、撤销和旧版本（约 1.5 分钟）

```powershell
& $py -m gm_provenance.v2_cli --root results\v2-live keygen --publisher demo --passphrase <local-passphrase>
& $py -m gm_provenance.v2_cli --root results\v2-live key-status
& $py -m gm_provenance.v2_cli --root results\v2-live key-rotate --publisher demo --passphrase <local-passphrase>
& $py -m gm_provenance.v2_cli --root results\v2-live key-status
```

现场可说明旧 Manifest 仍按历史公钥验签；吊销后的新验证结果会标记风险，而不会伪造为当前可信发布。口令只在本地输入，不写入日志。

## 5. 负例和审计断链（约 1.5 分钟）

准备内容替换、错误 key_id、错误 proof、旧版本回滚和超限输入，观察验证结果的失败阶段。审计链可用：

```powershell
& $py -m gm_provenance.v2_cli --root results\v2-live audit-verify --json
```

备用路径是使用提交包中已经生成的 `logs/v2/audit-export.json`，不需要现场重新创建数据库。任何修改 `event_hash`、`previous_event_hash` 或时间顺序的实验都必须保留副本和失败输出。

## 6. 收尾（约 30 秒）

展示 `submission-v2.1/SUBMISSION_MANIFEST.txt`、`logs/v2.1/codec-capability.json` 和 `docs/FINAL_V2_ACCEPTANCE.md`。说明完整 1000 行压力测试、长时间几何搜索、真实 FFmpeg 编解码和 DOCX 原生渲染如果未在提交电脑完成，均按 BLOCKED/LIMITED 展示，不把环境阻塞改写成 PASS。
