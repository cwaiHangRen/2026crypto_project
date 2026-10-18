import io
import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas

from gm_provenance.media import embed, detect, inspect_media

CID = "fedcba9876543210fedcba9876543210"


def source_pdf(pages=2):
    b = io.BytesIO(); c = canvas.Canvas(b, pagesize=(360, 240))
    for i in range(pages):
        c.setFont("Helvetica", 18); c.drawString(30, 180, f"Page {i + 1} text")
        arr = np.full((80, 120, 3), (40 + i * 60, 120, 180), np.uint8)
        ib = io.BytesIO(); Image.fromarray(arr).save(ib, "PNG"); ib.seek(0)
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(ib), 30, 40, width=120, height=80); c.showPage()
    c.save(); return b.getvalue()


def test_pdf_pages_roundtrip():
    raw = source_pdf()
    assert inspect_media(raw)["page_count"] == 2
    marked, profile = embed(raw, CID, "application/pdf")
    result = detect(marked, "application/pdf")
    assert profile["text_layer"] == "discarded"
    assert result["status"] == "FOUND" and result["content_ids"] == [CID]
    assert len(result["pages"]) == 2


def test_pdf_detection_exposes_current_page_scope_without_original_identity_claim():
    result = detect(source_pdf(2), "application/pdf")
    details = result["details"]
    assert details["current_page_count"] == 2
    assert details["original_page_identity"] == "NOT_CHECKED"
    assert details["whole_file_digest"] == "NOT_CHECKED"
    assert "current rendered pages" in details["page_source_hint"]
