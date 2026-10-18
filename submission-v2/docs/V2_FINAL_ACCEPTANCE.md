# v2 最终验收记录

## 基线与隔离

v1 稳定基线为提交 `a7b8e0783d5d80940028aeb9d33eb6f3538da95d`。v2 在独立 `v2` 分支和工作区 `gm-provenance-v2-20261018` 开发，未修改 v1 工作区、v1 提交包、v1 日志或 v1 统计定义。

## 核心必测

| 项目 | 命令/证据 | 结果 |
|---|---|---|
| Python 编译检查 | `python -m compileall -q src` | PASS，返回码 0 |
| v2 单元/集成/API/CLI/安全负例 | `pytest -q --basetemp tests/.tmp/v2-final tests/v2` | 24 passed，0 failed，约 26.86 s |
| 固定图片演示 | `scripts/run_v2_demo.py` | 10 登记、10 验证、10 叶 Merkle、10 类攻击矩阵、审计链有效 |
| 真实 PDF 演示 | `scripts/run_v2_pdf_demo.py` | 1 页、120 DPI、RGB、1020×1320，渲染页和最终 PNG 均恢复 ContentID，registration_allowed=true |
| 报告 PDF 视觉检查 | `logs/v2/report-render/page-01.png`、`page-02.png` | 2 页 PNG 已生成并检查 |

## 可选压力与环境项

完整 1000 行 v2 压力运行、长时间几何搜索和大规模裁剪没有冒充核心通过；它们必须在提交电脑上单独运行并保留超时日志。MP3、FLAC、MP4 适配器在没有可选系统编解码器时明确返回 `UNAVAILABLE`。DOCX 原生渲染需在提交电脑使用 Word 或 LibreOffice 复核；本机提交包只保证 OOXML 可读性和同内容 PDF 的页面检查。

## 数字口径

Round9 的固定事实仍按 v1 定义报告：总尝试 1000，gate 通过 992；仅对已发布的 992 个样本运行 D0，正确恢复 992/992，G0 误报 0/992。中心 95% 裁剪 S0 为 48/80，P50/P95 为 936.413/1237.624 ms。AI 图片覆盖 0/20，外部真实 PDF 覆盖 0/10。上述结果与 v2 小样本 smoke 指标分开保存，不能互相替代。

## 交付与人工检查

`submission-v2/` 含源代码、测试、配置、脚本、固定样本、PDF 页面证据、攻击矩阵、性能表、图表、匿名 Markdown/DOCX/PDF 报告和 `SUBMISSION_MANIFEST.txt`。提交前人工检查作品编号、日期、审批签字页，确认 Word/LibreOffice 原生渲染、外部媒体编解码器和完整压力测试环境；不得把占位签字写成已审批。
