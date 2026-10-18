"""用与 DOCX 相同的正式中文内容生成可检查的 PDF 交付版。"""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "submission"
FIG = SUB / "figures"
TABLES = SUB / "tables"


def _register_font():
    for path in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont("ReportChinese", str(path), subfontIndex=0))
                return "ReportChinese"
            except Exception:
                continue
    return "Helvetica"


def _p(text, style):
    return Paragraph(escape(str(text)).replace("\n", "<br/>") , style)


def _table(headers, rows, style):
    data = [[_p(x, style["table_header"]) for x in headers]]
    data.extend([[_p(x, style["table_cell"]) for x in row] for row in rows])
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102A43")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#829AB1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def build():
    font = _register_font()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", fontName=font, fontSize=18, leading=24, alignment=TA_CENTER, textColor=colors.HexColor("#102A43"), spaceAfter=8))
    styles.add(ParagraphStyle(name="ReportSub", fontName=font, fontSize=10, leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#486581"), spaceAfter=14))
    styles.add(ParagraphStyle(name="H1x", fontName=font, fontSize=14, leading=19, textColor=colors.HexColor("#163A5F"), spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="H2x", fontName=font, fontSize=11, leading=15, textColor=colors.HexColor("#243B53"), spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="Bodyx", fontName=font, fontSize=9.3, leading=14, alignment=TA_LEFT, spaceAfter=6))
    styles.add(ParagraphStyle(name="Captionx", fontName=font, fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=colors.HexColor("#486581"), spaceAfter=8))
    styles.add(ParagraphStyle(name="TableHeaderx", fontName=font, fontSize=8.2, leading=10, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="TableCellx", fontName=font, fontSize=7.6, leading=9.5, alignment=TA_LEFT))
    styles_dict = {"body": styles["Bodyx"], "table_header": styles["TableHeaderx"], "table_cell": styles["TableCellx"]}

    out = SUB / "design_report.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=1.55*cm, leftMargin=1.55*cm, topMargin=1.45*cm, bottomMargin=1.45*cm, title="匿名提交版设计报告", author="匿名作品编号")
    story = []
    story += [_p("面向 AI 生成内容的密码水印与可验证溯源系统设计实现", styles["ReportTitle"]), _p("匿名提交版  作品编号：__________  日期：__________", styles["ReportSub"])]
    abstract = "本作品实现一个在普通计算机上运行的内容来源证明原型，将国密 SM3 摘要、SM2 签名、线性版本链、撤销状态与媒体水印组合为可复现的本地验证流程。系统先为发布版本生成随机 ContentID，将其嵌入最终媒体编码，再对最终字节计算 SM3、构造 JCS Manifest 并签名登记。图片支持 PNG、JPEG 和有限几何恢复；PDF 通过 PDFium 固定 120 DPI 渲染页面，在页面图像中嵌入 watermark-v1，并对最终 PNG/JPEG 编码字节执行盲恢复 gate。输入质量门支持不超过 16MP，超限样本安全拒绝且不生成发布字节。验证器分别报告 Manifest、签名、发布者信任、字节摘要、版本链、授权、撤销和状态新鲜度，水印只作为传播后的来源检索线索。Round9 固定清单包含 800 张图片和 200 份 PDF，1000 次发布尝试中 992 次通过最终 gate，成功发布字节的 D0 预期 ID 恢复为 992/992，G0 无水印控制组误报为 0/992。报告同时保留合成样本比例、AI 0/20 与外部真实 PDF 0/10 缺口、裁剪同步耗时和 PDF 栅格化导致文字层丢失等限制。"

    story.append(_p("1 基本信息表", styles["H1x"]))
    story.append(_table(["字段", "内容"], [["作品题目", "面向 AI 生成内容的密码水印与可验证溯源系统设计实现"], ["作品类别", "软件系统 / 密码协议与媒体验证"], ["摘要", abstract], ["关键词", "SM2；SM3；内容凭证；鲁棒水印；PDF 栅格化"]], styles_dict)); story.append(Spacer(1, 8))

    story.append(_p("2 作品功能与性能说明", styles["H1x"]))
    story.append(_p("系统提供内容登记、发布、传播、验证、撤销、版本派生和攻击演示。登记阶段使用本地受信公钥清单，发布阶段固定遵守“随机 ContentID → 水印嵌入 → 最终编码字节 → SM3 → JCS Manifest → SM2 → SQLite 登记”的顺序。验证阶段支持 hard、recover、full 三种模式，未执行的检查保持 NOT_CHECKED，状态快照不可用时返回 UNKNOWN。", styles["Bodyx"]))
    story.append(_table(["功能", "实现与验收口径"], [["登记/发布", "图片与 PDF 均执行最终编码字节盲恢复 gate；通过后才计算摘要和签名。"], ["传播/验证", "JPEG、缩放、元数据清除、截图模拟、旋转/镜像和有限裁剪均保留检测范围与停止原因。"], ["撤销/版本", "父 ContentID、父 Manifest 摘要、版本号、发布者和授权操作参与验证；状态快照区分撤销与最新版本。"], ["PDF 页面流程", "固定 120 DPI、RGB/L、白底 alpha 展平、Lanczos 尺寸重建；逐页写出渲染页、最终编码页、SHA-256、恢复 ID 和回调验证。"], ["性能", "图片 D0 recover 平均/P50/P95 为 540.827/540.924/791.749 ms；PDF D0 recover 为 204.002/196.872/379.059 ms；完整验证延迟另列。"]], styles_dict)); story.append(Spacer(1, 8))

    story.append(PageBreak())
    story.append(_p("3 设计与实现方案", styles["H1x"]))
    story.append(Image(str(FIG / "architecture.png"), width=17.5*cm, height=8.26*cm)); story.append(_p("图 1  总体架构。水印进入媒体像素或页面渲染内容，SM3/SM2 对最终发布字节和 Manifest 提供来源与完整性证据。", styles["Captionx"]))
    story.append(_p("ContentID 是登记发布版本的随机标识。Manifest 正文包括媒体类型、最终字节长度与 SM3、元数据摘要、父版本、允许变换、水印 profile、签名套件和 SM2 用户标识。规范化采用 RFC 8785 JCS，签名消息使用固定协议域分离前缀与 canonical body。发布者信任、签名有效性和状态新鲜度分别保存，避免把有效签名自动等同于受信发布者或最新未撤销状态。", styles["Bodyx"]))
    story.append(Image(str(FIG / "verification-flow.png"), width=17.5*cm, height=8.95*cm)); story.append(_p("图 2  联合验证流程。水印是传播后识别线索，不能替代 SM3/Manifest/SM2 的来源和完整性证据。", styles["Captionx"]))

    story.append(_p("4 运行结果", styles["H1x"]))
    story.append(_p("真实 PDF 验收使用本地已保存的真实论文 PDF 第一页，在隔离 Registry 中完成一次登记与发布。服务层 hard 验证确认 Manifest、SM2 签名、受信发布者、最终 PDF 摘要、根版本链和授权字段；随后 PDF 页面按 120 DPI 渲染，嵌入 watermark-v1，写出 PNG 页面并从最终编码字节盲恢复本次 ContentID。运行的 page_count=1、status=PASS、registration_allowed=true，日志同时记录页面级 SM3 和完整 PDF 凭证验证字段。", styles["Bodyx"]))
    for name, caption in [("pdf-page-001-rendered.png", "图 3  真实 PDF 页面固定 DPI 渲染结果"), ("pdf-page-001-final.png", "图 4  嵌入水印后的最终 PNG 页面"), ("pdf-page-001-screenshot.jpg", "图 5  对最终页面的确定性 JPEG 截图/重编码结果")]:
        story.append(Image(str(FIG / name), width=8.7*cm, height=11.25*cm)); story.append(_p(caption, styles["Captionx"]))
    story.append(_table(["攻击流程", "实际结论"], [["内容替换", "最终字节 SM3 与 Manifest 不匹配；不升级为 VERIFIED，保留水印来源线索与摘要失败的分层语义。"], ["Manifest 篡改", "JCS 正文或签名校验失败，结论 REJECTED；不信任 Manifest 自带的身份声明。"], ["签名伪造/错误公钥", "SM2 验签或 issuer_trusted 失败；不登记、不报告为来源可信。"], ["旧版本回滚/撤销复用", "状态快照中的 latest 或 revoked 字段失败时返回 REJECTED；历史版本查看与冒充当前版本分开处理。"]], styles_dict)); story.append(Spacer(1, 8))

    metrics = json.loads((TABLES / "final_metrics.json").read_text(encoding="utf-8"))
    story.append(_p("5 技术指标", styles["H1x"]))
    story.append(_table(["分组", "样本/尝试", "gate通过", "gate率", "正确恢复", "恢复率", "G0误报"], [[g["media"], g["attempts"], g["gate_pass"], f"{g['gate_rate']:.2%}", g["correct_recovery"], f"{g['recovery_rate']:.2%}", g["false_positive"]] for g in metrics["scale_1000"]["groups"]], styles_dict)); story.append(Spacer(1, 8))
    story.append(_p("口径说明：992/1000 是 1000 次端到端发布尝试通过最终 gate 的比例；只有这 992 个已发布字节进入 D0 检测，因此 992/992 是 gate 通过子集的正确恢复率，不是 1000 个样本的总体恢复率。中心 95% 裁剪 S0 为独立传播鲁棒性实验，48/80 仅表示该裁剪子集的来源线索恢复结果。", styles["Bodyx"]))
    failures = json.loads((SUB / "logs" / "round9" / "gate-failures-summary.json").read_text(encoding="utf-8"))["rows"]
    story.append(_table(["失败样本", "媒体", "失败原因", "发布字节"], [[x["sample_id"], x["media_type"], x["reason"], "无"] for x in failures], styles_dict)); story.append(Spacer(1, 8))
    story.append(_p("8 条拒绝分为两类：wikimedia-artemis-earthrise 与 wikimedia-earth-from-space 是超过 16MP 的输入约束拒绝；r8-img-0058、0142、0178、0226、0394、0520 是最终编码字节未恢复本次 ContentID 的发布 gate 拒绝。六个内容恢复失败样本的三配置有界诊断见 submission/logs/round9/gate-failure-diagnostics.md；生产 G3 在 gate 前失败，没有生产 ContentID，因此诊断 CID 只用于可复现分析，不回写 Round9 统计。", styles["Bodyx"]))
    story.append(_table(["路径", "n", "平均 ms", "P50 ms", "P95 ms"], [["图片 D0 recover", 792, 540.827, 540.924, 791.749], ["图片 G3 full", 1584, 1164.3, 1156.362, 1646.974], ["PDF D0 recover", 200, 204.002, 196.872, 379.059], ["PDF G3 full", 400, 607.17, 579.003, 963.331], ["中心 95% 裁剪 S0", 80, "未汇总", 936.413, 1237.624]], styles_dict)); story.append(Spacer(1, 8))
    story.append(_p("存储开销按评测日志分开统计：992 个最终发布字节合计 824,071,188 bytes；隔离 Registry JSON 4,200,469 bytes、SQLite 425,943,040 bytes、状态快照 616,028 bytes。PDF 固定质量诊断覆盖 10 份文档、27 个页面：MSE 均值 537.942194，PSNR 均值 20.963666 dB，SSIM 均值 0.878221；文字层 10/10 丢失，书签 10/10 丢失。", styles["Bodyx"]))

    story.append(_p("6 系统测试与结果", styles["H1x"]))
    story.append(_p("测试覆盖密码后端、Manifest/JCS、Registry、图片水印、PDF 安全攻击、版本链、撤销、API、CLI、截图任务和本次 PDF 栅格化模块。新增 PDF 测试包含单页、三页逐页验证、确定性渲染、JPEG/PNG 重编码、尺寸参数、最终编码 gate 失败回归和服务回调。真实验收脚本还覆盖内容替换、Manifest 篡改、签名伪造、旧版本回滚、撤销复用等既有安全测试入口。", styles["Bodyx"]))
    story.append(_table(["检查", "状态", "证据或失败原因"], [["PDF 栅格化单元回归", "PASS 5/5", "tests/unit/test_pdf_raster.py；固定尺寸 600×400，PNG/JPEG 和 gate 失败均覆盖。"], ["截图模块回归", "PASS 3/3", "tests/unit/test_screenshot.py；全图候选会生成实际派生视图并保留原始字节摘要。"], ["真实 PDF 端到端", "PASS", "submission/logs/pdf-raster/pdf-raster-acceptance.json；page_count=1、gate PASS。"], ["Round9 规模", "PARTIAL", "1000 次尝试、992/1000 gate 通过；8 条失败保留，其中 2 条超过 16MP 输入拒绝、6 条最终编码字节未恢复。"], ["环境现场全量 pytest", "UNVERIFIED", "系统解释器和项目 .venv 缺 pytest/pip；使用既有隔离解释器完成定向回归，完整现场复核需恢复依赖。"], ["真实外部补证", "BLOCKED", "网络代理/DNS 不可用；AI 图片 0/20、外部真实 PDF 0/10，未把缺口写成完成。"], ["DOCX 原生渲染", "PENDING", "本机无 LibreOffice；已完成 DOCX XML 审计和同内容 PDF 逐页检查，提交电脑需用 Word/LibreOffice 原生复核。"]], styles_dict)); story.append(Spacer(1, 8))

    story.append(PageBreak())
    story.append(_p("7 应用前景与结论", styles["H1x"]))
    story.append(_p("系统可部署在普通 CPU 电脑，用于内容来源登记、内部发布审核、传播副本溯源和版本撤销查询。它适合把上传文件与已签名声明建立可审计关系，并为截图、重编码和 PDF 页面传播提供有限来源检索能力。PDF 栅格化模式会牺牲文字可搜索性、书签、链接和编辑能力，适用时应把原始 PDF 与发布页面副本分开保存。", styles["Bodyx"]))
    story.append(_p("当前结论由最终字节摘要、Manifest、SM2 签名、发布者信任、版本链和状态快照共同决定。水印可能被移除、损坏或复制，恢复 ContentID 不能单独证明当前内容完整、真实、合法或属于某位作者。系统不证明内容真实、合法、无偏见，也不把水印单独当作法律上的作者身份证明。后续工作包括补充可核验 AI/外部 PDF 来源、完善多页页面身份清单、降低裁剪同步延迟、恢复全量 pytest 环境并扩展音视频能力。", styles["Bodyx"]))
    story.append(PageBreak())
    story.append(_p("附录 提交前人工填写项", styles["H1x"]))
    story.append(_table(["字段", "待填写"], [["作品编号", "____________________________"], ["提交日期", "____________________________"], ["审批签字", "见 approval_page_separate/approval_page_placeholder.docx"], ["人工复核", "逐页检查 design_report.pdf 的表格、图片清晰度、页眉页脚和匿名信息"]], styles_dict))
    story.append(_p("报告中的统计数字均来自 submission/logs/round9 和 submission/logs/pdf-raster 下的结构化日志；提交前若重新运行评测，应重新生成 submission/tables/final_metrics.* 和本报告。", styles["Bodyx"]))

    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(font, 8); canvas.setFillColor(colors.HexColor("#486581")); canvas.drawCentredString(A4[0]/2, 0.75*cm, f"匿名提交版  作品编号：__________  |  {doc.page}"); canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(out)


if __name__ == "__main__":
    build()

