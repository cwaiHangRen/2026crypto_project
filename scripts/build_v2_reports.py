from __future__ import annotations

from pathlib import Path


def build_pdf(source: Path, output: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors
    font = "Helvetica"
    for candidate in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc")):
        if candidate.exists():
            try: pdfmetrics.registerFont(TTFont("V2Chinese", str(candidate), subfontIndex=0)); font = "V2Chinese"; break
            except Exception: pass
    styles = getSampleStyleSheet(); body = ParagraphStyle("v2body", parent=styles["BodyText"], fontName=font, fontSize=9.5, leading=14, spaceAfter=7); heading = ParagraphStyle("v2h", parent=styles["Heading1"], fontName=font, fontSize=14, leading=18, spaceAfter=8)
    story = []
    lines = source.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(set(cell) <= {"-", ":", " "} for cell in cells):
                    rows.append([Paragraph(cell.replace("&", "&amp;"), body) for cell in cells])
                index += 1
            if rows:
                table = Table(rows, repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(table); story.append(Spacer(1, 8))
            continue
        if line.startswith("# "): story.append(Paragraph(line[2:], heading))
        elif line.startswith("## "): story.append(Paragraph(line[3:], heading))
        elif line.strip(): story.append(Paragraph(line.replace("&", "&amp;"), body))
        else: story.append(Spacer(1, 4))
        index += 1
    SimpleDocTemplate(str(output), pagesize=A4, rightMargin=45, leftMargin=45, topMargin=45, bottomMargin=45, title="v2 系统设计与扩展报告").build(story)


def build_docx(source: Path, output: Path) -> None:
    from docx import Document
    doc = Document(); doc.core_properties.title = "v2 系统设计与扩展报告"; doc.core_properties.author = "匿名提交"
    lines = source.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(set(cell) <= {"-", ":", " "} for cell in cells): rows.append(cells)
                index += 1
            if rows:
                table = doc.add_table(rows=len(rows), cols=len(rows[0])); table.style = "Table Grid"
                for r, row in enumerate(rows):
                    for c, value in enumerate(row): table.cell(r, c).text = value
            continue
        if line.startswith("# "): doc.add_heading(line[2:], level=1)
        elif line.startswith("## "): doc.add_heading(line[3:], level=2)
        elif line.strip(): doc.add_paragraph(line)
        index += 1
    doc.save(output)
