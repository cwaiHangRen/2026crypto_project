# gm-provenance 最终验收门禁报告

日期：2026-10-18
基线：detached HEAD `416f18e54c68560bb002042f83af0a45af2ad79f`

## A 类 必须现场通过

| 范围 | 结果 | 证据 |
|---|---|---|
| PDF 栅格化单元测试 | PASS 5/5 | `tests/unit/test_pdf_raster.py` |
| 截图模块单元测试 | PASS 3/3 | `tests/unit/test_screenshot.py` |
| PDF/截图合并定向回归 | PASS 9/9 | `submission/logs/pytest-targeted-final2.txt` |
| 核心单元分组 | PASS 182 项 | 现场分组运行；水印 residual 另有 13 项、sync 另有 11 项 |
| 核心集成分组 | PASS 33 项 | PDF/发布服务 12、benchmark 6、development_eval 4、profiles/round7 11 |
| 安全分组 | PASS 24 项 | `tests/security`（排除 loopback service protocol） |
| CLI/环境/compileall | PASS | `cli-help.txt`、`environment-check.txt`、`api-import-startup.txt` |
| 离线登记/验证与负例语义 | PASS | smoke-test.txt，包含发布、hard/full、重编码来源线索和状态语义 |
| 历史 API 逻辑验收 | PASS 9/9 | `api-acceptance-historical-summary.json` |

现场全量 pytest 共收集 301 项。可确认的当前去重现场通过数为 239（unit 182、核心集成 33、安全 24；PDF/截图 9 项包含在 unit 182 中），但全量进程在长时间几何搜索/API 阻塞前没有最终汇总，因此 full pytest 的失败数、跳过数、超时数不能从部分输出推断，均记录为未取得。历史 API 逻辑验收 9/9 单独记录，合并证据数为 248；当前 TestClient 复跑单列为 C 类环境阻塞。

## B 类 可选压力测试

- 1000 行完整复跑未在本轮重新执行；采用已保存 Round9 日志。
- 长时间几何分组已单独设置 20 秒有界超时运行：`tests/unit/test_rotation_geometry.py` 收集并通过 36/36，耗时 17.260 s，未触发 timeout；完整全量套件仍未取得最终汇总。
- 中心 95% 裁剪 S0：48/80，P50/P95 为 936.413/1237.624 ms。
- 任意角度和长时间几何搜索保留原始结果，不因超时改写为通过。

## C 类 环境阻塞

- Windows 受限 loopback：TestClient/localhost 复跑未取得最终汇总；纯函数、离线 ASGI 导入和历史 API 逻辑摘要可用。
- 临时目录权限：部分脚本默认临时目录落入受限 sandbox；已用项目内临时目录重跑可运行部分，失败诊断保留在基线结果目录。
- LibreOffice/soffice 不可用：DOCX 已做 XML、表格、图片、页脚和匿名性审计；同内容 PDF 已用 PDFium 逐页渲染检查。提交电脑需用 Word/LibreOffice 原生复核 DOCX。
- AI 图片 0/20，外部真实 PDF 0/10，保留为覆盖缺口。

## Round9 口径

- 1000 次端到端尝试：800 图片、200 PDF；最终 gate 通过 992/1000。
- 仅在通过 gate 的 992 个发布字节上执行 D0，正确唯一恢复 992/992；G0 无水印控制组误报 0/992。
- 输入质量门支持不超过 16MP；`wikimedia-artemis-earthrise` 与 `wikimedia-earth-from-space` 是超限输入约束拒绝，不生成发布字节。
- 8 个 gate 失败样本均未生成发布字节，因此不进入 D0 分母：
  - `wikimedia-artemis-earthrise`、`wikimedia-earth-from-space`：超过 16MP，输入约束拒绝；
  - `r8-img-0058`、`r8-img-0142`、`r8-img-0178`、`r8-img-0226`、`r8-img-0394`、`r8-img-0520`：最终编码字节未恢复本次 ContentID，发布 gate 拒绝。
- 中心 95% 裁剪 S0 48/80 是独立鲁棒性实验，不是 992/1000 的替代指标。

### 六个内容恢复失败样本诊断

诊断逐样本最多执行既有 `v1+D0`、`v1+S0` 和 `watermark-v2-residual` 三种配置，使用固定诊断 ContentID；由于生产 G3 在发布 gate 前失败，原生产 ContentID 不存在于 Manifest，诊断 CID 只用于复现。`r8-img-0058`（800×624 PNG）与 `r8-img-0226`（800×624 PNG）三配置均未恢复，v1+S0 在既有预算内以 `time_budget_exhausted` 停止；v2 的 RS/ECC 均未解码（分别 24/29 个 bit error，MSE 系数误差 10.581/10.973）。`r8-img-0142`（1440×624）、`r8-img-0178`（800×768）、`r8-img-0394`（800×624）和 `r8-img-0520`（1760×624）在该诊断 CID 下 v1+D0 可恢复，其中 0142、0394、0520 的 v2 也可恢复；这些结果只能说明既有配置对诊断载荷的条件行为，不能改写原 Round9 六条失败。完整候选 ID、强度 34、RS 状态、同步停止原因和 MSE 见 `submission/logs/round9/gate-failure-diagnostics.json` 与 `.md`；未修改生产配置、gate 或统计。

## 报告与包审计

- DOCX XML：193 段落、8 个表格、5 个媒体部件、1 个页脚，无本地用户名路径；本机无 LibreOffice，未声称 DOCX 原生渲染通过。
- 同内容 PDF：7 页，已逐页检查标题、表格、架构图、验证流程图、真实 PDF 页面图、攻击结果表和审批占位。
- 当前提交包：`gm-provenance-submission-20261018.zip`。
- 当前 SHA-256：以最终压缩包旁的 `Get-FileHash` 结果和提交前命令输出为准。
- 之前已知包哈希 `1BDCBF410C2FBB9D13ED4BD07B7C9FD4BE6A30E98AFEECCAE7729AEADDAD7B41` 对应旧包；本轮加入失败样本清单、XML 审计和报告口径修正后哈希发生变化。

## 提交决策

A 类现场逻辑、核心 PDF 流程、六个失败样本诊断、报告审计和提交包审计均已通过；B 类完整 1000 行复跑与 C 类 Windows loopback、临时目录和 LibreOffice 复核仍按环境限制保留。代码核心验收通过，环境复核待完成；本次门禁完成后创建唯一提交，提交信息固定为 `Finalize submission gate, PDF raster validation, and report artifacts`。

人工最后检查：作品编号、提交日期、审批签字；用 Word/LibreOffice 原生打开 DOCX；确认 PDF/截图清晰；确认压缩包 SHA-256；确认不加入私钥、令牌、数据库或本地用户名路径。





