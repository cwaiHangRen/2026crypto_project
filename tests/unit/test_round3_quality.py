from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas

from gm_provenance.quality import analyze
from gm_provenance.media import embed as embed_media
from gm_provenance.watermark import encode_without_watermark, embed_image


CID = "0123456789abcdef0123456789abcdef"


def _source(path: Path, size=(768, 640)) -> None:
    y, x = np.indices((size[1], size[0]))
    arr = np.empty((size[1], size[0], 3), dtype=np.uint8)
    arr[..., 0] = (x * 3 + y) % 256
    arr[..., 1] = (y * 2 + 17) % 256
    arr[..., 2] = ((x + y) * 2) % 256
    Image.fromarray(arr, "RGB").save(path, "PNG")


def test_control_reuses_publisher_chain_without_coefficients(tmp_path: Path):
    source = tmp_path / "source.png"
    _source(source)
    raw = source.read_bytes()
    control, profile = encode_without_watermark(raw, "PNG")
    marked, _ = embed_image(raw, CID, "PNG")
    assert profile["watermark"] == "disabled"
    assert Image.open(io.BytesIO(control)).size == Image.open(io.BytesIO(marked)).size == (768, 640)
    # The control must be a real resized/reconstructed image, rather than a
    # byte-preserving copy of A; production output has the same geometry.
    assert control != raw


def test_analyze_image_saves_abc_metrics_and_crops(tmp_path: Path):
    source = tmp_path / "source.png"
    published = tmp_path / "published.png"
    _source(source)
    published.write_bytes(embed_image(source.read_bytes(), CID, "PNG")[0])
    result = analyze(source, published, tmp_path / "evidence", "sample-1")
    assert result["status"] == "PASS"
    assert set(result["pairs"]) == {"A_B", "A_C", "B_C"}
    assert result["pairs"]["A_B"]["ssim_parameters"]["window_size"] == 11
    for region in result["crops"].values():
        for path in region["files"].values():
            assert Path(path).exists()


def test_pdf_text_loss_and_empty_features_are_not_counted_as_preserved(tmp_path: Path):
    source = tmp_path / "searchable.pdf"
    with source.open("wb") as stream:
        pdf = canvas.Canvas(stream, pagesize=(360, 240))
        pdf.drawString(36, 180, "Searchable source text")
        pdf.showPage()
        pdf.save()
    published = tmp_path / "published.pdf"
    published.write_bytes(embed_media(source.read_bytes(), CID, "application/pdf")[0])

    result = analyze(source, published, tmp_path / "pdf-evidence", "pdf-1")
    assert result["source_features"]["text_present"] is True
    assert result["published_features"]["text_present"] is False
    assert result["feature_changes"]["status"]["text_layer"] == "LOST"
    # No bookmarks, links, or form fields in this fixture are N/A, rather than
    # being incorrectly reported as preserved because both counts are zero.
    assert result["feature_changes"]["status"]["bookmarks"] == "N/A_NO_SOURCE_FEATURE"
    assert result["feature_changes"]["status"]["links"] == "N/A_NO_SOURCE_FEATURE"
    assert result["feature_changes"]["status"]["forms"] == "N/A_NO_SOURCE_FEATURE"
    assert result["representative_crops"]


def _pdf_text(path: Path, texts: list[str]) -> None:
    with path.open("wb") as stream:
        pdf = canvas.Canvas(stream, pagesize=(360, 240))
        for text in texts:
            pdf.drawString(36, 180, text)
            pdf.showPage()
        pdf.save()


def test_pdf_page_count_has_no_phantom_end_page(tmp_path: Path):
    source, published = tmp_path / "source.pdf", tmp_path / "published.pdf"
    _pdf_text(source, ["page one", "page two"])
    _pdf_text(published, ["page one", "page two"])
    result = analyze(source, published, tmp_path / "evidence", "pdf-pages")
    assert len(result["pages"]) == 2
    assert all(page["status"] == "PASS" for page in result["pages"])


def test_pdf_text_digest_detects_same_length_different_content(tmp_path: Path):
    source, published = tmp_path / "source.pdf", tmp_path / "published.pdf"
    _pdf_text(source, ["AAAA"])
    _pdf_text(published, ["BBBB"])
    result = analyze(source, published, tmp_path / "evidence", "pdf-text")
    assert result["source_features"]["text_chars"] == result["published_features"]["text_chars"]
    assert result["feature_changes"]["status"]["text_layer"] == "CHANGED"
    assert result["feature_changes"]["text_layer_preserved"] is False
