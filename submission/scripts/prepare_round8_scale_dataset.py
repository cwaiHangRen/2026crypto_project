"""Prepare an auditable 1,000-row scale manifest without touching old data.

Network acquisition is intentionally not hidden here. Existing local pilot and
Round 7 challenge files are reused with source evidence; new rows are clearly
labelled internal synthetic fixtures. Missing AI and external real-PDF rows
remain visible in the summary instead of being relabelled as synthetic data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4, LETTER, landscape
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
OLD_MANIFEST = ROOT / "data/round3-development-v3/dataset_manifest.jsonl"
CHALLENGE_MANIFEST = ROOT / "data/round7-challenge-20261011/manifest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def thumb_hash(path: Path) -> str:
    im = Image.open(path).convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    return hashlib.sha256(im.tobytes()).hexdigest()


def font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def make_image(path: Path, seed: int, profile: str, width: int, height: int) -> None:
    rng = random.Random(seed)
    im = Image.new("RGB", (width, height), (246, 247, 242))
    draw = ImageDraw.Draw(im)
    if profile == "procedural-natural":
        for y in range(height):
            base = int(80 + 120 * y / max(1, height - 1))
            draw.line((0, y, width, y), fill=(base // 2, base, min(255, base + 20)))
        for _ in range(180):
            x, y = rng.randrange(width), rng.randrange(height)
            radius = rng.randrange(max(3, width // 100), max(6, width // 20))
            color = tuple(rng.randrange(30, 240) for _ in range(3))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    elif profile == "small-text":
        f = font(max(12, min(28, width // 45)))
        for y in range(24, height - 32, max(28, height // 25)):
            draw.text((20, y), f"Record {seed:04d} | observation {y:04d} | 12345 67890", fill=(20, 30, 40), font=f)
            draw.line((20, y + 22, width - 20, y + 22), fill=(170, 180, 190), width=1)
    elif profile == "chart":
        f = font(max(12, min(24, width // 55)))
        chart_left, chart_bottom = width // 8, height * 7 // 8
        draw.line((chart_left, 30, chart_left, chart_bottom), fill=(30, 30, 30), width=3)
        draw.line((chart_left, chart_bottom, width - 30, chart_bottom), fill=(30, 30, 30), width=3)
        for i in range(12):
            x = chart_left + 35 + i * max(20, (width - chart_left - 80) // 12)
            top = rng.randrange(height // 5, chart_bottom - 20)
            draw.rectangle((x, top, x + max(10, width // 70), chart_bottom), fill=(30 + i * 12, 80, 150))
            draw.text((x, chart_bottom + 6), str(i + 1), fill=(20, 20, 20), font=f)
    elif profile == "fine-lines":
        for i in range(90):
            y = (i * height + seed % 17) // 90
            draw.line((0, y, width, height - y), fill=(30 + (i + seed) % 100, 50 + seed % 30, 80), width=1)
            draw.line((0, height - y, width, y), fill=(140, 150, 170 - seed % 30), width=1)
        draw.text((18, 12), f"fine-line fixture {seed}", fill=(20, 20, 20), font=font(14))
    elif profile == "table":
        f = font(max(11, min(22, width // 60)))
        left, top = 20, 30
        cell_w, cell_h = max(50, (width - 40) // 8), max(28, (height - 60) // 18)
        for row in range(18):
            for col in range(8):
                x, y = left + col * cell_w, top + row * cell_h
                draw.rectangle((x, y, x + cell_w, y + cell_h), outline=(100, 110, 120), width=1)
                draw.text((x + 4, y + 5), str(rng.randrange(100, 9999)), fill=(20, 30, 40), font=f)
    else:
        for _ in range(12000):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.point((x, y), fill=tuple(rng.randrange(256) for _ in range(3)))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")


def make_pdf(path: Path, seed: int, profile: str, pages: int) -> None:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = landscape(A4) if profile in {"wide-table", "chart-report"} else LETTER
    c = Canvas(str(path), pagesize=size, invariant=1)
    width, height = size
    for page in range(pages):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(36, height - 42, f"Internal document {seed} / {profile}")
        c.setFont("Helvetica", 9)
        c.drawString(36, height - 60, f"Page {page + 1} of {pages}; generated content for protocol scale testing")
        if profile == "chart-report":
            c.setStrokeColorRGB(0.2, 0.2, 0.2)
            c.line(50, 100, width - 50, 100)
            for i in range(14):
                h = rng.randrange(50, int(height * 0.45))
                c.setFillColorRGB(0.1, 0.3 + i / 60, 0.6)
                c.rect(60 + i * 35, 100, 22, h, fill=1, stroke=0)
        elif profile == "wide-table":
            c.setFont("Helvetica", 8)
            for row in range(22):
                y = height - 100 - row * 22
                c.line(36, y, width - 36, y)
                for col in range(10):
                    x = 36 + col * ((width - 72) / 10)
                    c.line(x, y, x, y + 22)
                    c.drawString(x + 4, y + 6, str(rng.randrange(1000, 99999)))
        else:
            c.setFont("Helvetica", 10)
            for line in range(28):
                value = rng.uniform(0.1, 999.9)
                c.drawString(42, height - 100 - line * 18, f"Observation {line + 1:02d}: measured value {value:.3f}; independent document text.")
        c.showPage()
    c.save()


def inspect(path: Path, media_type: str) -> dict:
    result = {"local_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(path), "file_size_bytes": path.stat().st_size}
    if media_type.startswith("image/"):
        im = Image.open(path); im.load()
        result.update({"dimensions": [im.width, im.height], "page_count": None, "mode": im.mode, "pixel_count": im.width * im.height, "content_thumb_sha256": thumb_hash(path)})
        result["input_compatibility"] = "in_scope" if im.width * im.height <= 16 * 1024 * 1024 and im.mode in {"L", "RGB"} else "out_of_scope"
    else:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        result.update({"dimensions": None, "page_count": len(reader.pages), "mode": None, "pixel_count": None, "content_thumb_sha256": None, "input_compatibility": "in_scope"})
    return result


def old_rows() -> list[dict]:
    rows = [json.loads(line) for line in OLD_MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        row = row.copy(); row["previously_used"] = True; row["used_for_tuning"] = True; row["preprocessing"] = {"status": "none", "source_manifest": "round3-development-v3"}; row["input_compatibility"] = row.get("support_status", "unknown");
        yield row


def challenge_rows() -> list[dict]:
    doc = json.loads(CHALLENGE_MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for item in doc.get("items", []):
        if not item.get("challenge_included"):
            continue
        acq = item["acquisition"]; path = ROOT / str(acq["local_path"])
        attrs = item.get("attrs", {})
        row = {"sample_id": item["sample_id"], "source_family_id": item.get("source_family_id"), "media_type": "image/jpeg" if str(attrs.get("format", "")).upper() == "JPEG" else "image/png", "origin_category": item.get("category", "unknown"), "source_reference": item.get("source"), "license_reference": item.get("license_reference"), "license_status": "review", "generator_claim": item.get("generation_declared", "unknown"), "previously_used": True, "used_for_tuning": False, "split": "external-challenge", "support_status": "in_scope", "notes": "Round7 external challenge; license/redistribution evidence retained; not used for tuning.", "preprocessing": {"status": "none", "source_manifest": "round7-challenge-20261011"}}
        row.update(inspect(path, row["media_type"])); rows.append(row)
    return rows


def run(output: Path, target: int = 1000) -> Path:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing dataset: {output}")
    output.mkdir(parents=True)
    rows = list(old_rows()) + challenge_rows()
    current_images = sum(str(x.get("media_type", "")).startswith("image/") for x in rows)
    current_pdfs = sum(x.get("media_type") == "application/pdf" for x in rows)
    image_target, pdf_target = 800, 200
    new_images = image_target - current_images; new_pdfs = pdf_target - current_pdfs
    if new_images < 0 or new_pdfs < 0 or image_target + pdf_target != target:
        raise ValueError(f"unexpected starting composition images={current_images} pdfs={current_pdfs}")
    image_profiles = ["procedural-natural", "small-text", "chart", "fine-lines", "table", "texture"]
    for i in range(new_images):
        profile = image_profiles[i % len(image_profiles)]
        width = 640 + (i % 8) * 160
        height = 480 + (i % 7) * 144
        if i % 31 == 0:
            width, height = 2048, 1536
        path = output / "synthetic" / "images" / f"r8-image-{i + 1:04d}.png"
        make_image(path, 800000 + i, profile, width, height)
        meta = inspect(path, "image/png")
        rows.append({"sample_id": f"r8-img-{i + 1:04d}", "source_family_id": f"r8-synthetic-image-{profile}", "media_type": "image/png", "origin_category": "程序合成", "source_reference": "local deterministic fixture generator; no external provenance", "license_reference": "internal-test-only", "license_status": "internal-test-only", "generator_claim": "source generator=prepare_round8_scale_dataset.py; not an AI image claim; credential generator=gm-provenance", "previously_used": False, "used_for_tuning": False, "split": "scale-1000", "support_status": meta["input_compatibility"], "categories": profile, "notes": "New internal fixture. Distinct seed/layout; source family groups the controlled template family.", "preprocessing": {"status": "none", "rule": "original generated file"}, **meta})
    pdf_profiles = ["narrative-report", "wide-table", "chart-report", "measurement-log"]
    for i in range(new_pdfs):
        profile = pdf_profiles[i % len(pdf_profiles)]
        pages = 1 + (i % 4)
        path = output / "synthetic" / "pdfs" / f"r8-document-{i + 1:04d}.pdf"
        make_pdf(path, 900000 + i, profile, pages)
        meta = inspect(path, "application/pdf")
        rows.append({"sample_id": f"r8-pdf-{i + 1:04d}", "source_family_id": f"r8-synthetic-pdf-{profile}", "media_type": "application/pdf", "origin_category": "程序合成", "source_reference": "local deterministic PDF fixture generator; no external provenance", "license_reference": "internal-test-only", "license_status": "internal-test-only", "generator_claim": "source generator=prepare_round8_scale_dataset.py; not an external real document; credential generator=gm-provenance", "previously_used": False, "used_for_tuning": False, "split": "scale-1000", "support_status": "in_scope", "categories": profile, "notes": "New internal authored PDF with varied content/layout/page count; source family groups template family.", "preprocessing": {"status": "none", "rule": "original generated PDF"}, **meta})
    assert len(rows) == target and sum(str(x["media_type"]).startswith("image/") for x in rows) == image_target and sum(x["media_type"] == "application/pdf" for x in rows) == pdf_target
    ids = [x["sample_id"] for x in rows]
    if len(set(ids)) != len(ids): raise ValueError("duplicate sample_id")
    hashes = [x["sha256"] for x in rows]
    duplicate_hashes = {h: n for h, n in Counter(hashes).items() if n > 1}
    thumb_groups = defaultdict(list)
    for row in rows:
        if row.get("content_thumb_sha256"): thumb_groups[row["content_thumb_sha256"]].append(row["sample_id"])
    # Keep ``local_path`` because the frozen development evaluator consumes
    # that field.  ``source_file`` is a human-facing alias for the manifest
    # and must not make the manifest incompatible with the existing runner.
    for row in rows:
        row["source_file"] = row.get("local_path")
    manifest = output / "dataset_manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    summary = {"schema_version": "round8-scale-1000-1", "target_count": target, "actual_count": len(rows), "images": image_target, "pdfs": pdf_target, "previously_used": sum(bool(x.get("previously_used")) for x in rows), "new_rows": sum(not bool(x.get("previously_used")) for x in rows), "license_review": sum(x.get("license_status") == "review" for x in rows), "license_internal_test_only": sum(x.get("license_status") == "internal-test-only" for x in rows), "license_pending": sum(x.get("license_status") == "pending" for x in rows), "origin_categories": dict(Counter(x.get("origin_category") for x in rows)), "source_families": len({x.get("source_family_id") for x in rows}), "exact_duplicate_sha256_groups": duplicate_hashes, "thumbnail_duplicate_groups": {k: v for k, v in thumb_groups.items() if len(v) > 1}, "compatibility": dict(Counter(x.get("input_compatibility") for x in rows)), "missing_target_categories": {"natural_photo_target": 350, "ai_generated_target": 300, "small_text_chart_target": 150, "external_real_pdf_target": 100, "natural_photo_available": 0, "ai_generated_available": 0, "small_text_chart_available": sum(x.get("origin_category") == "程序合成" and x.get("media_type", "").startswith("image/") for x in rows), "external_real_pdf_available": 1}, "network_status": "DNS unavailable for official COCO/Wikimedia/HuggingFace endpoints during this run; no TLS bypass or download workaround used", "limitations": "This is a 1000-row protocol scale corpus with transparent internal synthetic majority; it is not a 1000-row real-world prevalence corpus. AI and external real-PDF gaps remain pending."}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare frozen Round8 scale manifest")
    parser.add_argument("--output", default="data/round8-scale-1000")
    parser.add_argument("--target", type=int, default=1000, choices=(1000,))
    args = parser.parse_args()
    run(ROOT / args.output, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
