"""在没有 pytest 时运行第一轮核心闭环的可复现冒烟测试。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from PIL import Image, ImageDraw
    from gm_provenance.crypto import OpenSSLCrypto
    from gm_provenance.service import ProvenanceService
    from gm_provenance.watermark import detect_image, embed_image

    out = ROOT / "results" / "smoke-test"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    report: dict[str, object] = {"tests": [], "status": "PASS"}

    def check(name, fn):
        try:
            value = fn()
            report["tests"].append({"name": name, "status": "PASS", "details": value})
        except Exception as exc:
            report["tests"].append({"name": name, "status": "FAIL", "error": type(exc).__name__ + ": " + str(exc)})
            report["status"] = "FAIL"

    crypto = OpenSSLCrypto()
    check("crypto.doctor", crypto.doctor)

    cid = "0123456789abcdef0123456789abcdef"
    image = Image.new("RGB", (768, 768), (245, 248, 252))
    ImageDraw.Draw(image).rectangle((64, 64, 704, 704), outline=(31, 78, 121), width=10)
    source = out / "source.png"
    image.save(source)

    def image_roundtrip():
        marked, profile = embed_image(source.read_bytes(), cid, "PNG")
        detected = detect_image(marked)
        assert detected["status"] == "FOUND" and detected["content_ids"] == [cid]
        assert detect_image(source.read_bytes())["status"] == "NOT_FOUND"
        return {"profile": profile, "detected": detected}

    check("watermark.png_roundtrip", image_roundtrip)

    service = ProvenanceService(out / "registry")
    private, public = out / "demo-private.pem", out / "demo-public.pem"
    passphrase = "smoke-passphrase-123"

    def publish_verify():
        service.keygen(private, public, passphrase)
        published = service.register(source, private, passphrase)
        exact = service.verify(published["media_path"], published["manifest_path"], "hard")
        assert exact["conclusion"] == "VERIFIED_EXACT"
        full = service.verify(published["media_path"], published["manifest_path"], "full")
        assert full["state_freshness"] == "UNKNOWN" and full["conclusion"] == "INDETERMINATE"
        return {"content_id": published["content_id"], "exact": exact, "full_without_state": full}

    check("service.publish_verify", publish_verify)

    def transform_hint():
        published = service.registry.list_contents()[0]
        transformed = out / "reencoded.jpg"
        service.transform(published["media_path"], transformed, "jpeg-reencode", 50)
        result = service.verify(transformed, None, "recover")
        assert result["conclusion"] == "ORIGIN_HINT_ONLY"
        return result

    check("service.reencode_origin_hint", transform_hint)
    private.unlink(missing_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
