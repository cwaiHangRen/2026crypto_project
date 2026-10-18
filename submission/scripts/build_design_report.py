"""生成匿名中文设计报告 DOCX 和可直接粘贴的 Markdown 内容。"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "submission"
FIG = SUB / "figures"
TABLES = SUB / "tables"
LOGS = SUB / "logs"


def _font(size: int, bold: bool = False, color: str | None = None):
    font = Pt(size)
    return font


def _shade(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_cell(cell, text: str, *, bold: bool = False, fill: str | None = None):
    cell.text = str(text)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragraph in cell.paragraphs:
        paragraph.paragraph_format.space_after = Pt(0)
        for run in paragraph.runs:
            run.font.name = "Microsoft YaHei"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            run.font.size = Pt(9)
            run.bold = bold
    if fill:
        _shade(cell, fill)


def _table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, header in enumerate(headers):
        _set_cell(table.rows[0].cells[i], header, bold=True, fill="DCE6F1")
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            _set_cell(cells[i], value)
    if widths:
        for row in table.rows:
            for i, width in enumerate(widths):
                row.cells[i].width = Inches(width)
    doc.add_paragraph()
    return table


def _para(doc, text: str, *, bold_prefix: str | None = None):
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.space_after = Pt(5)
    if bold_prefix and text.startswith(bold_prefix):
        p.add_run(bold_prefix).bold = True
        p.add_run(text[len(bold_prefix):])
    else:
        p.add_run(text)
    for run in p.runs:
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        run.font.size = Pt(10.5)
    return p


def _heading(doc, text: str, level: int = 1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    return p


def _make_diagrams():
    from PIL import Image, ImageDraw, ImageFont

    FIG.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
        small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
    except OSError:
        font = ImageFont.load_default(); small = font

    def box(draw, xy, label, fill):
        draw.rounded_rectangle(xy, radius=18, fill=fill, outline="#274C77", width=3)
        x0, y0, x1, y1 = xy
        bbox = draw.multiline_textbbox((0, 0), label, font=font, spacing=5, align="center")
        draw.multiline_text(((x0 + x1 - (bbox[2] - bbox[0])) / 2,
                             (y0 + y1 - (bbox[3] - bbox[1])) / 2), label,
                            font=font, fill="#102A43", spacing=5, align="center")

    def arrow(draw, a, b):
        draw.line((a[0], a[1], b[0], b[1]), fill="#486581", width=5)
        draw.polygon([(b[0], b[1]), (b[0]-14, b[1]-8), (b[0]-14, b[1]+8)], fill="#486581")

    canvas = Image.new("RGB", (1800, 850), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 30), "gm-provenance 总体架构", font=font, fill="#102A43")
    boxes = [
        ((70, 150, 390, 330), "内容导入\nPNG/JPEG/PDF", "#E8F1F8"),
        ((500, 150, 820, 330), "ContentID\n水印嵌入", "#D9F0E3"),
        ((930, 150, 1250, 330), "最终编码字节\nSM3 + Manifest", "#FFF1CC"),
        ((1360, 150, 1730, 330), "SM2 签名\nSQLite 登记", "#FCE8E6"),
        ((260, 510, 650, 690), "传播变换\n压缩/缩放/截图", "#E8F1F8"),
        ((760, 510, 1140, 690), "盲恢复\nD0/S0 + 页面范围", "#D9F0E3"),
        ((1250, 510, 1630, 690), "联合验证\n完整/来源线索/拒绝", "#FFF1CC"),
    ]
    for xy, label, fill in boxes: box(draw, xy, label, fill)
    arrow(draw, (390, 240), (500, 240)); arrow(draw, (820, 240), (930, 240)); arrow(draw, (1250, 240), (1360, 240))
    arrow(draw, (650, 600), (760, 600)); arrow(draw, (1140, 600), (1250, 600))
    arrow(draw, (660, 330), (460, 510)); arrow(draw, (1080, 330), (950, 510)); arrow(draw, (1540, 330), (1450, 510))
    canvas.save(FIG / "architecture.png")

    flow = Image.new("RGB", (1800, 920), "white")
    draw = ImageDraw.Draw(flow)
    draw.text((55, 30), "联合验证决策流程", font=font, fill="#102A43")
    flow_boxes = [
        ((100, 170, 500, 340), "读取最终编码字节\nSM3 摘要", "#E8F1F8"),
        ((700, 170, 1100, 340), "Manifest / SM2\n信任与版本链", "#FFF1CC"),
        ((1300, 170, 1700, 340), "水印盲恢复\nContentID 候选", "#D9F0E3"),
        ((390, 570, 780, 740), "状态快照\n撤销/最新版本", "#FCE8E6"),
        ((1010, 570, 1410, 740), "结论\nVERIFIED / HINT / REJECTED", "#E8F1F8"),
    ]
    for xy, label, fill in flow_boxes: box(draw, xy, label, fill)
    arrow(draw, (500, 255), (700, 255)); arrow(draw, (1100, 255), (1300, 255)); arrow(draw, (900, 340), (590, 570)); arrow(draw, (1500, 340), (1210, 570)); arrow(draw, (780, 655), (1010, 655))
    flow.save(FIG / "verification-flow.png")


def build() -> Path:
    _make_diagrams()
    metrics = json.loads((TABLES / "final_metrics.json").read_text(encoding="utf-8"))
    pdf_log = json.loads((LOGS / "pdf-raster" / "pdf-raster-acceptance.json").read_text(encoding="utf-8"))
    doc = Document()
    doc.core_properties.author = "匿名提交"
    doc.core_properties.last_modified_by = "匿名提交"
    doc.core_properties.title = "匿名提交版设计报告"
    doc.core_properties.subject = "gm-provenance PDF 栅格化与可验证溯源验收"
    section = doc.sections[0]
    section.top_margin = Inches(0.65); section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75); section.right_margin = Inches(0.75)
    styles = doc.styles
    styles["Normal"].font.name = "Microsoft YaHei"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    styles["Normal"].font.size = Pt(10.5)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("面向 AI 生成内容的密码水印与可验证溯源系统设计实现")
    run.bold = True; run.font.size = Pt(20); run.font.name = "Microsoft YaHei"; run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    subtitle = doc.add_paragraph(); subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sr = subtitle.add_run("匿名提交版  作品编号：__________  日期：__________")
    sr.font.size = Pt(11); sr.font.name = "Microsoft YaHei"; sr._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    doc.add_paragraph()

    _heading(doc, "1 基本信息表", 1)
    abstract = ("本作品实现一个在普通计算机上运行的内容来源证明原型，将国密 SM3 摘要、SM2 签名、线性版本链、撤销状态与媒体水印组合为可复现的本地验证流程。系统先为发布版本生成随机 ContentID，将其嵌入最终媒体编码，再对最终字节计算 SM3、构造 JCS Manifest 并签名登记。图片支持 PNG、JPEG 和有限几何恢复；PDF 通过 PDFium 固定 120 DPI 渲染页面，在页面图像中嵌入 watermark-v1，并对最终 PNG/JPEG 编码字节执行盲恢复 gate。验证器分别报告 Manifest、签名、发布者信任、字节摘要、版本链、授权、撤销和状态新鲜度，水印只作为传播后的来源检索线索。Round9 固定清单包含 800 张图片和 200 份 PDF，1000 次发布尝试中 992 次通过最终 gate，成功发布字节的 D0 预期 ID 恢复为 992/992，G0 无水印控制组误报为 0/992。报告同时保留合成样本比例、外部内容缺口、裁剪同步耗时和 PDF 栅格化导致文字层丢失等限制。")
    _table(doc, ["字段", "内容"], [
        ("作品题目", "面向 AI 生成内容的密码水印与可验证溯源系统设计实现"),
        ("作品类别", "软件系统 / 密码协议与媒体验证"),
        ("摘要", abstract),
        ("关键词", "SM2；SM3；内容凭证；鲁棒水印；PDF 栅格化"),
    ], [1.2, 5.9])

    _heading(doc, "2 作品功能与性能说明", 1)
    _para(doc, "系统提供内容登记、发布、传播、验证、撤销、版本派生和攻击演示。登记阶段使用本地受信公钥清单，发布阶段固定遵守“随机 ContentID → 水印嵌入 → 最终编码字节 → SM3 → JCS Manifest → SM2 → SQLite 登记”的顺序。验证阶段支持 hard、recover、full 三种模式，未执行的检查保持 NOT_CHECKED，状态快照不可用时返回 UNKNOWN。")
    _table(doc, ["功能", "实现与验收口径"], [
        ("登记/发布", "图片与 PDF 均执行最终编码字节盲恢复 gate；通过后才计算摘要和签名。"),
        ("传播/验证", "JPEG、缩放、元数据清除、截图模拟、旋转/镜像和有限裁剪均保留检测范围与停止原因。"),
        ("撤销/版本", "父 ContentID、父 Manifest 摘要、版本号、发布者和授权操作参与验证；状态快照区分撤销与最新版本。"),
        ("PDF 页面流程", "固定 120 DPI、RGB/L、白底 alpha 展平、Lanczos 尺寸重建；逐页写出渲染页、最终编码页、SHA-256、恢复 ID 和回调验证。"),
        ("性能", "图片 D0 recover 平均/P50/P95 为 540.827/540.924/791.749 ms；PDF D0 recover 为 204.002/196.872/379.059 ms；完整验证延迟另列。"),
    ], [1.5, 5.6])

    doc.add_page_break()
    _heading(doc, "3 设计与实现方案", 1)
    doc.add_picture(str(FIG / "architecture.png"), width=Inches(6.7))
    p = doc.paragraphs[-1]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _para(doc, "图 1  总体架构。水印进入媒体像素或页面渲染内容，SM3/SM2 对最终发布字节和 Manifest 提供来源与完整性证据。")
    _para(doc, "ContentID 是登记发布版本的随机标识。Manifest 正文包括媒体类型、最终字节长度与 SM3、元数据摘要、父版本、允许变换、水印 profile、签名套件和 SM2 用户标识。规范化采用 RFC 8785 JCS，签名消息使用固定协议域分离前缀与 canonical body。发布者信任、签名有效性和状态新鲜度分别保存，避免把有效签名自动等同于受信发布者或最新未撤销状态。")
    doc.add_picture(str(FIG / "verification-flow.png"), width=Inches(6.7)); doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _para(doc, "图 2  联合验证流程。水印是传播后识别线索，不能替代 SM3/Manifest/SM2 的来源和完整性证据；恢复到旧 ContentID 后，当前摘要不匹配时只报告 ORIGIN_HINT_ONLY。")

    _heading(doc, "4 运行结果", 1)
    _para(doc, "真实 PDF 验收使用本地已保存的真实论文 PDF 第一页，在隔离 Registry 中完成一次登记与发布。服务层 hard 验证确认 Manifest、SM2 签名、受信发布者、最终 PDF 摘要、根版本链和授权字段；随后 PDF 页面按 120 DPI 渲染，嵌入 watermark-v1，写出 PNG 页面并从最终编码字节盲恢复本次 ContentID。该运行的 page_count=1、status=PASS、registration_allowed=true，日志同时记录页面级 SM3 和完整 PDF 凭证验证字段。")
    for name, caption in [("pdf-page-001-rendered.png", "图 3  真实 PDF 页面固定 DPI 渲染结果"), ("pdf-page-001-final.png", "图 4  嵌入水印后的最终 PNG 页面"), ("pdf-page-001-screenshot.jpg", "图 5  对最终页面的确定性 JPEG 截图/重编码结果")]:
        doc.add_picture(str(FIG / name), width=Inches(3.0)); doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _para(doc, caption)
    _table(doc, ["攻击流程", "实际结论"], [
        ("内容替换", "最终字节 SM3 与 Manifest 不匹配；不升级为 VERIFIED，保留水印来源线索与摘要失败的分层语义。"),
        ("Manifest 篡改", "JCS 正文或签名校验失败，结论 REJECTED；不信任 Manifest 自带的身份声明。"),
        ("签名伪造/错误公钥", "SM2 验签或 issuer_trusted 失败；不登记、不报告为来源可信。"),
        ("旧版本回滚/撤销复用", "状态快照中的 latest 或 revoked 字段失败时返回 REJECTED；历史版本查看与冒充当前版本分开处理。"),
    ], [1.8, 5.3])

    _heading(doc, "5 技术指标", 1)
    rows = []
    for group in metrics["scale_1000"]["groups"]:
        rows.append((group["media"], group["attempts"], group["gate_pass"], f"{group['gate_rate']:.2%}", group["correct_recovery"], f"{group['recovery_rate']:.2%}", group["false_positive"]))
    _table(doc, ["分组", "样本/尝试", "gate通过", "gate率", "正确恢复", "恢复率", "G0误报"], rows, [1.0, 1.0, 1.0, 0.8, 1.0, 0.8, 0.8])
    _para(doc, "口径说明：992/1000 是 1000 次端到端发布尝试通过最终 gate 的比例；只有这 992 个已发布字节进入 D0 检测，因此 992/992 是 gate 通过子集的正确恢复率，不是 1000 个样本的总体恢复率。中心 95% 裁剪 S0 为独立传播鲁棒性实验，48/80 仅表示该裁剪子集的来源线索恢复结果。")
    failures = json.loads((LOGS / "round9" / "gate-failures-summary.json").read_text(encoding="utf-8"))["rows"]
    _table(doc, ["失败样本", "媒体", "失败原因", "发布字节"], [(x["sample_id"], x["media_type"], x["reason"], "无") for x in failures], [1.7, 0.7, 3.9, 0.7])
    _table(doc, ["路径", "n", "平均 ms", "P50 ms", "P95 ms"], [
        ("图片 D0 recover", 792, 540.827, 540.924, 791.749),
        ("图片 G3 full", 1584, 1164.3, 1156.362, 1646.974),
        ("PDF D0 recover", 200, 204.002, 196.872, 379.059),
        ("PDF G3 full", 400, 607.17, 579.003, 963.331),
        ("中心 95% 裁剪 S0", 80, "未汇总", 936.413, 1237.624),
    ], [2.0, 0.8, 1.1, 1.1, 1.1])
    _para(doc, "存储开销按评测日志分开统计：992 个最终发布字节合计 824,071,188 bytes；隔离 Registry JSON 4,200,469 bytes、SQLite 425,943,040 bytes、状态快照 616,028 bytes。后面三项是逐样本评测隔离开销，不等于产品部署 footprint。PDF 固定质量诊断覆盖 10 份文档、27 个页面：MSE 均值 537.942194，PSNR 均值 20.963666 dB，SSIM 均值 0.878221；文字层 10/10 丢失，书签 10/10 丢失。")

    _heading(doc, "6 系统测试与结果", 1)
    _para(doc, "测试覆盖密码后端、Manifest/JCS、Registry、图片水印、PDF 安全攻击、版本链、撤销、API、CLI、截图任务和本次 PDF 栅格化模块。新增 PDF 测试包含单页、三页逐页验证、确定性渲染、JPEG/PNG 重编码、尺寸参数、最终编码 gate 失败回归和服务回调。真实验收脚本还覆盖内容替换、Manifest 篡改、签名伪造、旧版本回滚、撤销复用等既有安全测试入口。")
    _table(doc, ["检查", "状态", "证据或失败原因"], [
        ("PDF 栅格化单元回归", "PASS 5/5", "tests/unit/test_pdf_raster.py；固定尺寸 600×400，PNG/JPEG 和 gate 失败均覆盖。"),
        ("截图模块回归", "PASS 3/3", "tests/unit/test_screenshot.py；全图候选会生成实际派生视图并保留原始字节摘要。"),
        ("真实 PDF 端到端", "PASS", "submission/logs/pdf-raster/pdf-raster-acceptance.json；page_count=1、gate PASS。"),
        ("Round9 规模", "PARTIAL", "1000 次尝试、992 gate 通过；8 条失败保留，2 条超过 16MP，其他失败与输入/随机载荷相关。"),
        ("环境现场全量 pytest", "UNVERIFIED", "系统解释器和项目 .venv 缺 pytest/pip；使用既有隔离解释器完成定向回归，完整现场复核需恢复依赖。"),
        ("真实外部补证", "BLOCKED", "网络代理/DNS 不可用；AI 0/20、外部真实 PDF 0/10，未把缺口写成完成。"),
    ], [1.7, 1.1, 5.0])

    doc.add_page_break()
    _heading(doc, "7 应用前景与结论", 1)
    _para(doc, "系统可部署在普通 CPU 电脑，用于内容来源登记、内部发布审核、传播副本溯源和版本撤销查询。它适合把上传文件与已签名声明建立可审计关系，并为截图、重编码和 PDF 页面传播提供有限来源检索能力。PDF 栅格化模式会牺牲文字可搜索性、书签、链接和编辑能力，适用时应把原始 PDF 与发布页面副本分开保存。")
    _para(doc, "当前结论由最终字节摘要、Manifest、SM2 签名、发布者信任、版本链和状态快照共同决定。水印可能被移除、损坏或复制，恢复 ContentID 不能单独证明当前内容完整、真实、合法或属于某位作者。系统不证明内容真实、合法、无偏见，也不把水印单独当作法律上的作者身份证明。后续工作包括补充可核验 AI/外部 PDF 来源、完善多页页面身份清单、降低裁剪同步延迟、恢复全量 pytest 环境并扩展音视频能力。")

    doc.add_page_break()
    _heading(doc, "附录 提交前人工填写项", 1)
    _table(doc, ["字段", "待填写"], [("作品编号", "____________________________"), ("提交日期", "____________________________"), ("审批签字", "见 approval_page_separate/approval_page_placeholder.docx"), ("人工复核", "逐页检查 design_report.pdf 的表格、图片清晰度、页眉页脚和匿名信息")], [1.5, 5.6])
    _para(doc, "报告中的统计数字均来自 submission/logs/round9 和 submission/logs/pdf-raster 下的结构化日志；提交前若重新运行评测，应重新生成 submission/tables/final_metrics.* 和本报告。")

    # Footer with a neutral work number placeholder and page number field.
    for section in doc.sections:
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run("匿名提交版  作品编号：__________  |  ")
        fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE"); footer._p.append(fld)

    out = SUB / "design_report.docx"
    doc.save(out)
    # Save a direct-paste text version with the same section headings.
    md = SUB / "design_report_content.md"
    md.write_text("\n".join(p.text for p in doc.paragraphs if p.text.strip()) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())

