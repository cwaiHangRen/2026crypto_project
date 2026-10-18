"""生成真实 PDF 页面栅格化验收产物。

脚本先在隔离 Registry 中登记一份真实 PDF 单页样本，再验证发布 PDF 的
SM3/Manifest/SM2 绑定，最后对该发布 PDF 做固定 DPI 页面渲染、再次嵌入
watermark-v1、PNG/JPEG 编码和最终字节盲恢复。所有页面摘要和验证回调写入
pdf-raster-log.json；私钥只在临时目录中存在并在结束时删除。
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from gm_provenance.pdf_raster import PdfRasterConfig, process_pdf
from gm_provenance.service import ProvenanceService


def _first_page(source: Path, destination: Path) -> Path:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source))
    if not reader.pages:
        raise ValueError("源 PDF 不包含页面")
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    with destination.open("wb") as stream:
        writer.write(stream)
    return destination


def run(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    original = output / "source-original.pdf"
    _first_page(source, original)
    # Keep the registry outside the final submission material.  The manifest
    # and media are represented by the structured verification summary below.
    temp_parent = output / ".tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gm-provenance-pdf-", dir=temp_parent) as temp:
        registry_root = Path(temp) / "registry"
        key_root = Path(temp) / "keys"
        key_root.mkdir(parents=True, exist_ok=True)
        private = key_root / "publisher-private.pem"
        public = key_root / "publisher-public.pem"
        password = "local-acceptance-passphrase"
        service = ProvenanceService(registry_root)
        service.keygen(private, public, password)
        service.trust_key(public, "publisher-demo-001")
        published = service.register(original, private, password, "publisher-demo-001")
        published_pdf = Path(published["media_path"])
        manifest_path = Path(published["manifest_path"])
        credential = service.verify(published_pdf, manifest_path, "hard")
        content_id = published["content_id"]

        def verify_page(final_bytes: bytes, page: int) -> dict:
            # The signed credential binds the complete published PDF.  The
            # page callback records that full check beside page-level SM3 and
            # watermark evidence instead of pretending a page hash is the
            # whole-document hash.
            return {
                "status": "PAGE_EVIDENCE_WITH_PDF_CREDENTIAL",
                "page": page,
                "checked_scope": "page",
                "final_page_sm3": service.crypto.sm3(final_bytes),
                "pdf_manifest_verification": {
                    "conclusion": credential.get("conclusion"),
                    "manifest_presence": credential.get("manifest_presence"),
                    "manifest_schema": credential.get("manifest_schema"),
                    "signature_valid": credential.get("signature_valid"),
                    "issuer_trusted": credential.get("issuer_trusted"),
                    "content_hash_match": credential.get("content_hash_match"),
                    "parent_chain_status": credential.get("parent_chain_status"),
                    "authorization_status": credential.get("authorization_status"),
                    "content_revocation_status": credential.get("content_revocation_status"),
                    "key_revocation_status": credential.get("key_revocation_status"),
                    "state_freshness": credential.get("state_freshness"),
                    "latest_version_status": credential.get("latest_version_status"),
                },
                "page_hash_binding": "NOT_APPLICABLE: manifest binds complete PDF bytes",
            }

        raster_dir = output / "pdf-raster"
        result = process_pdf(
            published_pdf.read_bytes(),
            content_id,
            config=PdfRasterConfig(dpi=120, color_space="RGB", final_format="PNG"),
            output_dir=raster_dir,
            verify_fn=verify_page,
        )
        summary = {
            "source_pdf": "source-original.pdf",
            "source_pdf_sha256": result.original_pdf_sha256,
            "registered_pdf_sha256": service.crypto.sm3(published_pdf.read_bytes()),
            "content_id": content_id,
            "published_pdf_media_type": published["media_type"],
            "credential_verification": verify_page(b"page-evidence", 1)["pdf_manifest_verification"],
            "raster": result.as_dict(),
            "limitations": [
                "Manifest/SM3/SM2 绑定的是完整发布 PDF；页面回调单独记录页面 SM3，不能把页面摘要当作整份 PDF 摘要。",
                "本验收使用真实 PDF 的第一页；多页逐页 gate 支持在 tests/unit/test_pdf_raster.py 中覆盖，整本文档语义仍需页身份清单。",
            ],
        }
        # Remove local paths and timing-sensitive private material from the
        # summary while preserving the exact evidence fields.
        (output / "pdf-raster-acceptance.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        private.unlink(missing_ok=True)
        public.unlink(missing_ok=True)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="真实 PDF 页面栅格化验收")
    parser.add_argument("--source-pdf", type=Path, default=Path("sample_data/real_pdf_page1.pdf"))
    parser.add_argument("--output", type=Path, default=Path("submission/logs/pdf-raster"))
    args = parser.parse_args()
    summary = run(args.source_pdf, args.output)
    print(json.dumps({
        "status": summary["raster"]["status"],
        "registration_allowed": summary["raster"]["registration_allowed"],
        "page_count": summary["raster"]["page_count"],
        "content_id": summary["content_id"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["raster"]["registration_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
