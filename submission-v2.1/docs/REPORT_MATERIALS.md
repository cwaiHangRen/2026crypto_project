# 报告素材与提交前盘点（Round6–Round9）

生成日期：2026-10-18（项目工作区盘点）

本文仅汇总仓库中已经落盘的 Round6–Round9 报告和 JSON 摘要，数字均保留原报告口径；不把实验 partial 结果提升为正式通过，也不把本地用户名路径当作可提交内容。

## 可直接引用的真实数字

### Round9 默认规模基线

- 清单：1000 行（800 图片、200 PDF）；1000 个本地文件路径，SHA-256 唯一值 1000，缺失文件 0；来源家族 35 个。
- 发布 gate：1000 次尝试，992 次通过，8 次失败。8 条均为图片：2 条超过 16MP，6 条最终编码自检未恢复本次 ContentID；失败行保留在 `results/round9-scale-audit-r2/`。
- 成功发布字节：D0 盲恢复 992/992；正确单输出 992/992。G3 `full` 验证 992/992 为 `VERIFIED_EXACT`，九类签名/摘要/信任/父链/授权/撤销/版本/新鲜度字段均 PASS。
- 独立无水印控制：G0 编码控制组 0/992 误报；原始输入负例 D0 为 0/997 误报（3 条 out-of-scope 单列未尝试）。
- 延迟：图片 D0 recover P50/P95 = 540.827/791.749 ms；图片 G3 full = 1156.362/1646.974 ms。PDF D0 recover = 196.872/379.059 ms；PDF G3 full = 579.003/963.331 ms。
- PDF 质量诊断：10 份 PDF、27 个页面、120 DPI。MSE 均值/中位数/最差 = 537.942194/451.631509/894.058053；PSNR 均值/最差 = 20.963666/18.617146 dB；SSIM 均值/最差 = 0.878221/0.815305。源文档平均 2.7 页、中位数 2 页；发布/源文件体积比均值 212.007、中位数 243.995、最大 267.114。文字层 10/10 丢失，书签 10/10 丢失，1 份检测到链接丢失；这是栅格化边界，不能宣称保留原 PDF 结构。
- 存储：992 个 retained 发布字节共 824,071,188 bytes；隔离评测 Registry JSON 4,200,469 bytes，SQLite 425,943,040 bytes，状态快照 616,028 bytes。后 3 项为评测隔离开销，不等同产品部署 footprint。
- 中心裁剪 95% 固定子集：80 张图片；D0 失败/S0 成功 48/80，均失败 32/80；S0 P50/P95/最大 = 936.413/1237.624/1329.612 ms。该结果是来源线索，不能升级为当前上传文件的完整性通过。

### Round8/7 外部与传播结果

- Round8 外部发布尝试：36 次，v1 10/12、v2 9/12、feedback 8/12，共 27 次通过；按声明支持范围排除越界后 27/28。
- Round7 外部 22 变换矩阵：27 个已发布组合 × 26 变换 = 702/702 正向任务完成；目录整体仍 partial，3 个 mixed-ID 负例缺合法双媒体。
- Round8 完整矩阵同步时间：v1 P50/P95 1115.113/3138.702 ms，v2 1108.280/2475.495 ms，feedback 1115.113/3138.702 ms；裁剪/组合未达 100 ms 目标。
- Round6 paired：4654 条；四格（D0 与 S0）为都成功 1441、D0 失败/S0 成功 1482、D0 成功/S0 失败 0、都失败 1731。Round7 v3 paired：都成功 1441、D0 失败/v3 成功 1783、D0 成功/v3 失败 0、都失败 1430；净救回与退步需按报告原口径引用，不能替换默认 S0。

### Round8 旋转/PDF 整合演示

- API 隔离验收：9/9 PASS（见 `results/rotation-integration-20261015/api-acceptance/summary.json`）。
- 真实素材 pilot：1 张 JPEG + 1 份 9 页 PDF；图片/PDF 发布与 full 均 `VERIFIED_EXACT`；PDF 逐页 9/9 `FOUND`（页面来源线索）。90/180/270 度、镜像、33.3 度变换为 `ORIGIN_HINT_ONLY`；中心裁剪 95% S0 `FOUND`，带原 manifest 的 full 为 `content_hash_match=FAIL`；撤销后 `REJECTED`；无水印 `NOT_FOUND`。
- 正交样本：8/8 正例、24 条负例 0 误报、SM2/SM3 闭环 16/16。
- 任意角小样本：24/24 正例、24/24 负例、0 detector error；仅为小样本演示，不代表规模识别率。

## 可引用审计路径

- `docs/ROUND6_SYNC_FINAL_REPORT.md`、`docs/ROUND6_BASELINE_AUDIT.md`
- `docs/ROUND7_FINAL_REPORT.md`、`docs/ROUND7_PERFORMANCE.md`
- `docs/ROUND8_FINAL_REPORT.md`、`docs/ROUND8_HANDOFF.md`
- `docs/ROUND9_SCALE_REPORT.md`
- `results/round9-scale-audit-r2/summary.json`
- `results/round9-correction-20261014-v2/summary.json`
- `results/round9-scale-crop/20261014T-round9-crop-80/summary.json`
- `results/rotation-integration-20261015/real-materials/summary.json`
- `results/rotation-integration-20261015/api-acceptance/summary.json`

## 明确缺项与限制

- 当前仓库没有 `.docx`/`.doc` 模板；需要按附件模板由主任务生成 `design_report.docx` 与 `design_report.pdf`。本盘点未伪造模板字段。
- `figures/`、`tables/`、`logs/`、`sample_data/`、审批签字页占位文件和最终提交压缩包尚未形成（现有 `results/` 是实验归档，不宜整目录复制）。
- 真实内容补证仍 blocked：新增 AI 图片 0/20，新增外部真实 PDF 0/10；来源/许可证据不能用内部合成样本替代。
- 外部 mixed-ID 缺口 3 个；外部正向矩阵虽 702/702，但整体报告保持 partial。
- PDF 当前是确定性 120 DPI 栅格化路径；演示已有 PDFium 页面渲染，Round8 明确“没有队友真实阅读器截图代码”。报告应将“渲染页面/编码页面”与“真实阅读器截图”分开标注，避免宣称已完成真实截图闭环。
- 1000 行不是 1000 个独立真实来源；程序合成样本占多数，AI 可核验样本 0，小文字/图表为内部合成 783 条。
- S0/v3 裁剪深度恢复约秒级，未达 100 ms；不能写成性能达标。

## 匿名性扫描结论

仓库正文未发现单位、指导教师、队员姓名或邮箱。发现的身份泄漏仅在历史运行日志中的本地用户名路径 `<local-user-path>...`（如 `results/baseline-20261018-112253/*.txt`、部分报告引用的本地路径）；提交前应将这些日志排除或脱敏。数据集清单和实验结果可能含原始来源 URL、作者/DOI 等内容，需按报告是否公开的要求保留或做许可审查，不应把本地路径带入 docx/PDF/截图元数据。

建议最终报告仅保留作品编号占位符、日期占位符和审批签字页占位符；清理 Word/PDF 作者、创建者、修改者元数据，并检查截图标题栏与文件名。

## 建议提交目录（报告材料侧）

```text
design_report.docx
design_report.pdf
README.md
pyproject.toml
requirements.txt
src/  scripts/  tests/  configs/
sample_data/        # 仅小型可再分发样本或占位说明
figures/            # PDF 页面渲染、攻击流程、验证状态截图
 tables/             # 由最终 logs 生成的 CSV/Markdown 表
logs/               # pytest/API/CLI/环境检查摘要（已脱敏）
APPROVAL_SIGNATURE_PAGE_PLACEHOLDER.md
```

不应打包 `.venv/`、`.pytest_cache/`、私钥、管理员 token、SQLite/运行数据库副本、原始大数据集、临时目录和大体积中间结果。

## 报告章节落点

1. 基本信息：作品题目、类别、300–500 字摘要、关键词；作者/单位留占位符。
2. 功能性能：登记、发布 gate、传播来源线索、full 验证、撤销、攻击演示；严格区分 `FOUND` 与 `VERIFIED_EXACT`。
3. 设计实现：ContentID/Manifest/SM3/SM2/版本链/水印/验证决策；明确水印是识别线索，SM3/SM2 是完整性与来源证据。
4. 运行结果：使用 `real-materials/` 的 JPEG 与 9 页 PDF 演示，并从新 PDF 模块产物补充真实页面截图。
5. 技术指标：引用 Round9 1000 行、图片/PDF 分组、延迟、误报、存储数字；每个数字必须链接到最终日志。
6. 系统测试：157 passed、2 warnings；API 9/9；攻击和撤销使用 `api-acceptance/summary.json` 与 PDF 安全测试。
7. 应用前景与结论：普通电脑部署和来源证明；明确不证明内容真实、合法、无偏见，也不把水印单独当法律作者身份证明。

