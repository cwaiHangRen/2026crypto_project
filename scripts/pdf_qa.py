"""生成 PDF 结构与水印攻击的可复核证据。

用法：``python scripts/pdf_qa.py --output results/pdf-qa``。
脚本只使用项目已有的 ReportLab、pypdf、PDFium 和 Pillow，并把每个案例的
期望状态、实际状态和页面范围写入 JSON；失败时返回非零退出码。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gm_provenance import media  # noqa: E402


CID_A = "fedcba9876543210fedcba9876543210"
CID_B = "0123456789abcdef0123456789abcdef"


def source_pdf(pages: int = 3, pagesize: tuple[int, int] = (360, 240)) -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=pagesize)
    for index in range(pages):
        # Keep source artwork low-frequency enough for the current DCT baseline.
        c.setFont("Helvetica", 10)
        c.drawString(20, pagesize[1] - 35, f"Page {index + 1}")
        c.showPage()
    c.save()
    return out.getvalue()


def rewrite_pages(data: bytes, order: list[int], replacements: dict[int, bytes] | None = None) -> bytes:
    replacements = replacements or {}
    original = PdfReader(io.BytesIO(data))
    readers = {index: PdfReader(io.BytesIO(value)) for index, value in replacements.items()}
    writer = PdfWriter()
    for output_index, source_index in enumerate(order):
        reader = readers.get(output_index, original)
        writer.add_page(reader.pages[0] if reader is not original else reader.pages[source_index])
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def page_scope(result: dict) -> list[dict]:
    return [{"page": p.get("page"), "status": p.get("status"), "content_ids": p.get("content_ids", [])}
            for p in result.get("pages", [])]


def case(name: str, expected: str, result: dict, notes: str = "") -> dict:
    observed = result.get("status")
    return {"name": name, "expected": expected, "observed": observed,
            "pass": observed == expected, "content_ids": result.get("content_ids", []),
            "pages": page_scope(result), "details": result.get("details", {}), "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "pdf-qa")
    args = parser.parse_args()
    out = args.output if args.output.is_absolute() else ROOT / args.output
    artifacts = out / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    raw = source_pdf(3)
    marked_a, profile_a = media.embed(raw, CID_A, "application/pdf")
    marked_b, _ = media.embed(raw, CID_B, "application/pdf")
    save(artifacts / "source.pdf", raw)
    save(artifacts / "marked-a.pdf", marked_a)
    save(artifacts / "marked-b.pdf", marked_b)

    result = media.detect(marked_a, "application/pdf")
    rows.append(case("baseline-marked", "FOUND", result, "3 pages; expected all pages FOUND"))

    deleted = rewrite_pages(marked_a, [0, 2])
    save(artifacts / "page-deleted.pdf", deleted)
    rows.append(case("page-deletion", "FOUND", media.detect(deleted, "application/pdf"), "page 2 removed"))

    reordered = rewrite_pages(marked_a, [2, 0, 1])
    save(artifacts / "page-reordered.pdf", reordered)
    rows.append(case("page-reorder", "FOUND", media.detect(reordered, "application/pdf"), "3 pages reordered"))

    replaced = rewrite_pages(marked_a, [0, 1, 2], {1: raw})
    save(artifacts / "page-replaced-plain.pdf", replaced)
    rows.append(case("page-replacement-plain", "NOT_FOUND", media.detect(replaced, "application/pdf"),
                     "middle page replaced by an unmarked original"))

    conflict = rewrite_pages(marked_a, [0, 1, 2], {2: marked_b})
    save(artifacts / "content-id-conflict.pdf", conflict)
    rows.append(case("multiple-content-id-conflict", "CONFLICT", media.detect(conflict, "application/pdf"),
                     "last page uses a different ContentID"))

    unmarked = source_pdf(2)
    rows.append(case("unmarked-pages", "NOT_FOUND", media.detect(unmarked, "application/pdf"), "2 pages without watermark"))

    pdf = pdfium.PdfDocument(marked_a)
    actual = pdf[0].render(scale=2).to_pil()
    actual_png = io.BytesIO(); actual.save(actual_png, "PNG")
    save(artifacts / "screenshot-actual-render.png", actual_png.getvalue())
    rows.append(case("screenshot-actual-pdfium-render", "FOUND", media.detect(actual_png.getvalue(), "image/png"),
                     "actual PDFium page render; image scope has no pages[]"))

    simulated = actual.resize((360, 240), Image.Resampling.LANCZOS)
    simulated_png = io.BytesIO(); simulated.save(simulated_png, "PNG")
    save(artifacts / "screenshot-simulated-viewport.png", simulated_png.getvalue())
    rows.append(case("screenshot-simulated-pillow-viewport", "FOUND", media.detect(simulated_png.getvalue(), "image/png"),
                     "simulated screenshot is Pillow resize of the actual render"))

    too_many = source_pdf(media.MAX_PAGES + 1)
    try:
        media.inspect_media(too_many)
        rows.append({"name": "limit-page-count", "expected": "REJECTED", "observed": "ACCEPTED", "pass": False})
    except ValueError as exc:
        rows.append({"name": "limit-page-count", "expected": "REJECTED", "observed": "REJECTED", "pass": True,
                     "error": str(exc), "pages": media.MAX_PAGES + 1})

    huge_page = source_pdf(1, pagesize=(3000, 3000))
    try:
        media.embed(huge_page, CID_A, "application/pdf")
        rows.append({"name": "limit-page-pixels", "expected": "REJECTED", "observed": "ACCEPTED", "pass": False})
    except ValueError as exc:
        rows.append({"name": "limit-page-pixels", "expected": "REJECTED", "observed": "REJECTED", "pass": True,
                     "error": str(exc), "pagesize": [3000, 3000]})

    over_20mb = b"%PDF-1.7\n" + b"0" * (media.MAX_BYTES + 1)
    try:
        media.inspect_media(over_20mb)
        rows.append({"name": "limit-20mb", "expected": "REJECTED", "observed": "ACCEPTED", "pass": False})
    except ValueError as exc:
        rows.append({"name": "limit-20mb", "expected": "REJECTED", "observed": "REJECTED", "pass": True,
                     "error": str(exc), "bytes": len(over_20mb)})

    payload = {"tool": "scripts/pdf_qa.py", "limits": {"max_bytes": media.MAX_BYTES,
              "max_pages": media.MAX_PAGES, "max_pixels": media.MAX_PIXELS, "pdf_dpi": media.PDF_DPI},
              "watermark_profile": profile_a, "cases": rows,
              "passed": sum(bool(row.get("pass")) for row in rows), "total": len(rows),
              "limitations": [
                  "PDF发布逐页栅格化，原文字层、书签和交互对象不保留。",
                  "截图仅验证水印像素恢复；截图文件本身没有PDF页范围。",
                  ("当前运行使用真实 Reed-Solomon 纠错。" if profile_a.get("watermark_profile", {}).get("ecc", "").startswith("reed-solomon")
                   else "当前运行使用 crc-only-fallback；不能据此宣称 Reed-Solomon 纠错。"),
                  "超过20MB的detect结果由解析失败映射为NOT_FOUND；上限拒绝证据来自inspect_media。",
              ]}
    (out / "pdf-qa.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [row["name"] for row in rows if not row.get("pass")]
    (out / "README.txt").write_text("PDF QA 证据由 scripts/pdf_qa.py 生成；详见 pdf-qa.json 和 artifacts/。\n",
                                     encoding="utf-8")
    print(json.dumps({"output": str(out), "passed": payload["passed"], "total": payload["total"], "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
