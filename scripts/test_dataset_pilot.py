"""Run the selected pilot files through the local provenance service.

The test is intentionally small: it checks media inspection, registration,
hard verification and watermark recovery for every downloaded file. Image
files also receive one JPEG re-encode check. No external network access is
used by this script.
"""

from __future__ import annotations

import json
import secrets
import sys
import atexit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gm_provenance import media  # noqa: E402
from gm_provenance.crypto import OpenSSLCrypto  # noqa: E402
from gm_provenance.service import ProvenanceService  # noqa: E402


PILOT = ROOT / "data" / "external-pilot-20261006"
OUT = ROOT / "results" / "dataset-pilot-20261006"
FILES = [
    ("COCO", PILOT / "coco" / "000000000139.jpg"),
    ("COCO", PILOT / "coco" / "000000000285.jpg"),
    ("COCO", PILOT / "coco" / "000000000632.jpg"),
    ("GenImage", PILOT / "genimage" / "official-visualization-example.png"),
    ("DocLayNet", PILOT / "doclaynet" / "official-example-page.png"),
    ("arXiv", PILOT / "arxiv" / "doclaynet-paper-2206.01062v1.pdf"),
]

# These are acceptance expectations for the checked-in pilot, rather than
# guesses inferred from the file extension.  Keeping them here makes a
# downloaded replacement fail loudly when it no longer matches the manifest
# or when the service silently changes its verification semantics.
EXPECTED_MEDIA_TYPES = {
    "COCO": "image/jpeg",
    "GenImage": "image/png",
    "DocLayNet": "image/png",
    "arXiv": "application/pdf",
}
EXPECTED_PDF_PAGE_COUNT = 9


def _assert(condition: bool, message: str) -> None:
    """Raise a readable assertion error for the JSON report and CLI output."""
    if not condition:
        raise AssertionError(message)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    service = ProvenanceService(OUT / "registry")
    # Key generation intentionally requires fresh output paths.  Use a run
    # suffix so a second pilot run does not fail merely because prior evidence
    # is retained in the results directory.
    run_id = secrets.token_hex(6)
    private = OUT / f"publisher-private-{run_id}.pem"
    public = OUT / f"publisher-public-{run_id}.pem"
    # Test keys are temporary credentials. Keep the evidence report and
    # registry, but never leave a fresh private key in the results directory.
    atexit.register(lambda: private.unlink(missing_ok=True))
    atexit.register(lambda: public.unlink(missing_ok=True))
    password = "dataset-pilot-" + secrets.token_urlsafe(18)
    service.keygen(private, public, password)
    rows: list[dict] = []

    for dataset, path in FILES:
        row = {"dataset": dataset, "input": str(path.relative_to(ROOT)).replace("\\", "/")}
        try:
            raw = path.read_bytes()
            expected_media_type = EXPECTED_MEDIA_TYPES[dataset]
            inspected = media.inspect_media(raw)
            row["inspect"] = inspected
            row["expected_media_type"] = expected_media_type
            _assert(inspected.get("media_type") == expected_media_type,
                    f"source media_type={inspected.get('media_type')!r}, expected {expected_media_type!r}")
            _assert(len(raw) <= media.MAX_BYTES,
                    f"source bytes={len(raw)} exceeds MAX_BYTES={media.MAX_BYTES}")
            if expected_media_type == "application/pdf":
                source_pages = inspected.get("page_count")
                _assert(isinstance(source_pages, int) and source_pages > 0,
                        f"source PDF page_count invalid: {source_pages!r}")
                _assert(source_pages == EXPECTED_PDF_PAGE_COUNT,
                        f"source PDF page_count={source_pages}, expected {EXPECTED_PDF_PAGE_COUNT}")
                _assert(source_pages <= media.MAX_PAGES,
                        f"source PDF page_count={source_pages} exceeds MAX_PAGES={media.MAX_PAGES}")
            published = service.register(path, private, password, publisher_id="dataset-pilot-publisher")
            _assert(published.get("media_type") == expected_media_type,
                    f"published media_type={published.get('media_type')!r}, expected {expected_media_type!r}")
            _assert(published.get("media_bytes", 0) <= media.MAX_BYTES,
                    f"published bytes={published.get('media_bytes')} exceeds MAX_BYTES={media.MAX_BYTES}")
            exact = service.verify(published["media_path"], published["manifest_path"], "hard")
            recovered = service.verify(published["media_path"], None, "recover")
            _assert(exact.get("conclusion") == "VERIFIED_EXACT",
                    f"hard conclusion={exact.get('conclusion')!r}, expected 'VERIFIED_EXACT'")
            _assert(exact.get("content_hash_match") == "PASS",
                    f"hard content_hash_match={exact.get('content_hash_match')!r}, expected 'PASS'")
            _assert(recovered.get("watermark_status") == "FOUND",
                    f"recover watermark_status={recovered.get('watermark_status')!r}, expected 'FOUND'")
            _assert(recovered.get("conclusion") == "VERIFIED_EXACT",
                    f"recover conclusion={recovered.get('conclusion')!r}, expected 'VERIFIED_EXACT'")
            row.update(
                {
                    "status": "PASS",
                    "media_type": published["media_type"],
                    "content_id": published["content_id"],
                    "published_bytes": published["media_bytes"],
                    "hard_conclusion": exact.get("conclusion"),
                    "hard_content_hash_match": exact.get("content_hash_match"),
                    "recover_conclusion": recovered.get("conclusion"),
                    "recover_watermark_status": recovered.get("watermark_status"),
                    "assertions": {
                        "source_media_type": True,
                        "size_within_limit": True,
                        "hard_verified_exact": True,
                        "hard_content_hash_pass": True,
                        "recover_verified_exact": True,
                        "recover_watermark_found": True,
                    },
                }
            )
            if published["media_type"] in ("image/png", "image/jpeg"):
                transformed = OUT / "transformed" / f"{path.stem}-{len(rows):02d}.jpg"
                transformed.parent.mkdir(parents=True, exist_ok=True)
                service.transform(published["media_path"], transformed, "jpeg-reencode", 70)
                hint = service.verify(transformed, None, "recover")
                transformed_inspect = media.inspect_media(transformed.read_bytes())
                _assert(transformed_inspect.get("media_type") == "image/jpeg",
                        f"transformed media_type={transformed_inspect.get('media_type')!r}, expected 'image/jpeg'")
                _assert(hint.get("conclusion") == "ORIGIN_HINT_ONLY",
                        f"JPEG transform conclusion={hint.get('conclusion')!r}, expected 'ORIGIN_HINT_ONLY'")
                _assert(hint.get("watermark_status") == "FOUND",
                        f"JPEG transform watermark_status={hint.get('watermark_status')!r}, expected 'FOUND'")
                _assert(hint.get("content_hash_match") != "PASS",
                        "JPEG transform unexpectedly retained the published content hash")
                row["transform_recover_conclusion"] = hint.get("conclusion")
                row["transform_recover_watermark_status"] = hint.get("watermark_status")
                row["transform_media_type"] = transformed_inspect.get("media_type")
                row["transform_content_hash_match"] = hint.get("content_hash_match")
                row["assertions"].update(
                    {
                        "jpeg_transform_media_type": True,
                        "jpeg_transform_origin_hint": True,
                        "jpeg_transform_watermark_found": True,
                        "jpeg_transform_hash_not_exact": True,
                    }
                )
            else:
                published_inspect = media.inspect_media(Path(published["media_path"]).read_bytes())
                expected_pages = inspected.get("page_count")
                published_pages = published_inspect.get("page_count")
                # service.verify intentionally exposes the aggregate recovery
                # status.  Inspect the media detector as well to assert every
                # PDF page, rather than only the aggregate FOUND result.
                detected_pdf = media.detect(Path(published["media_path"]).read_bytes(), "application/pdf")
                recovered_pages = detected_pdf.get("pages", [])
                _assert(published_inspect.get("media_type") == "application/pdf",
                        f"published PDF media_type={published_inspect.get('media_type')!r}")
                _assert(published_pages == expected_pages == EXPECTED_PDF_PAGE_COUNT,
                        f"PDF page count source={expected_pages}, published={published_pages}, expected={EXPECTED_PDF_PAGE_COUNT}")
                _assert(published_pages <= media.MAX_PAGES,
                        f"published PDF page_count={published_pages} exceeds MAX_PAGES={media.MAX_PAGES}")
                _assert(len(recovered_pages) == published_pages,
                        f"recovered PDF pages={len(recovered_pages)}, expected {published_pages}")
                _assert(all(page.get("status") == "FOUND" for page in recovered_pages),
                        "one or more published PDF pages did not recover a watermark")
                row["published_inspect"] = published_inspect
                row["published_page_count"] = published_pages
                row["recovered_page_count"] = len(recovered_pages)
                row["pdf_detect_status"] = detected_pdf.get("status")
                row["pdf_limits"] = {
                    "source_bytes_within": len(raw) <= media.MAX_BYTES,
                    "published_bytes_within": published.get("media_bytes", 0) <= media.MAX_BYTES,
                    "source_pages_within": expected_pages <= media.MAX_PAGES,
                    "published_pages_within": published_pages <= media.MAX_PAGES,
                }
                row["assertions"].update(
                    {
                        "pdf_page_count_preserved": True,
                        "pdf_page_limit": True,
                        "pdf_all_pages_watermark_found": True,
                    }
                )
        except Exception as exc:
            row.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    report = {
        "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL",
        "total": len(rows),
        "passed": sum(row["status"] == "PASS" for row in rows),
        "network": "not_used",
        "limits": {"max_bytes": media.MAX_BYTES, "max_pdf_pages": media.MAX_PAGES},
        "expectations": {
            "media_types": EXPECTED_MEDIA_TYPES,
            "pdf_page_count": EXPECTED_PDF_PAGE_COUNT,
            "hard_conclusion": "VERIFIED_EXACT",
            "recover_watermark_status": "FOUND",
            "image_transform_conclusion": "ORIGIN_HINT_ONLY",
        },
        "rows": rows,
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "total", "passed", "network")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
