"""Run a deterministic real-PDF raster demonstration and export its evidence."""
from __future__ import annotations

import json
from pathlib import Path

from gm_provenance.media_adapters import PdfAdapter


def main() -> int:
    source = Path("sample_data/real_pdf_page1.pdf")
    out = Path("logs/v2/pdf-demo")
    out.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    op = PdfAdapter().embed(raw, "0123456789abcdef0123456789abcdef", output_dir=out)
    (out / "published.pdf").write_bytes(op.final_bytes or b"")
    summary = {
        "source": str(source).replace("\\", "/"),
        "media_type": op.media_type,
        "content_id": op.content_id,
        "watermark_recovered": op.watermark_recovered,
        "registration_allowed": op.details.get("registration_allowed"),
        "pdf_raster": op.details.get("pdf_raster", {}),
        "final_sha256": op.final_sha256,
    }
    (out / "v2-pdf-demo-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
