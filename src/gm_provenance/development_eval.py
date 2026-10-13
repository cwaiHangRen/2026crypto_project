"""Round-3 development evaluation helpers.

The evaluator deliberately keeps evaluator truth (expected ContentID and the
expected outcome) outside the call to :meth:`ProvenanceService.verify`.
``evaluate_sample`` is intentionally small enough for the round-3 runner to
checkpoint one sample at a time, while still exercising the real media and
registry implementations.
"""
from __future__ import annotations

import base64
import gc
import io
import json
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from . import media, manifest, state
from .benchmark import _signed_only
from .service import ProvenanceService

GROUPS = ("G0", "G1", "G2", "G3")
DEFAULT_CONFIG: dict[str, Any] = {
    "seed": 20261007,
    "watermark_profile": "watermark-v1",
    "groups": ["G0", "G1", "G2", "G3"],
    "sidecar_states": ["retained", "lost"],
    "verification_modes": {"retained": "hard", "lost": "recover"},
    "images": {
        "jpeg_quality": [90, 75, 50],
        "resize": [0.75],
        "resize_jpeg": [[0.75, 75]],
        "crop_keep": [0.95],
        "metadata_clear": True,
        "format_convert": True,
        "screenshot_simulated": True,
    },
    "pdf": {
        "render_dpi": [120],
        "screenshot_simulated": True,
        "delete_page": True,
        "reorder_pages": True,
        "replace_unmarked": True,
        "cross_content_id": True,
    },
    "performance": {"repeats": 1, "warmup": 0},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the JSON development-evaluation configuration."""
    if path is None:
        return _merge({}, DEFAULT_CONFIG)
    p = Path(path)
    if not p.exists():
        return _merge({}, DEFAULT_CONFIG)
    value = json.loads(p.read_text(encoding="utf-8"))
    return _merge(DEFAULT_CONFIG, value if isinstance(value, dict) else {})


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _image_transform(data: bytes, operation: str, value: Any = None) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    if operation == "identity":
        return data
    if operation == "jpeg":
        im.save(out, "JPEG", quality=int(value))
    elif operation == "resize":
        ratio = float(value)
        im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.Resampling.LANCZOS).save(out, "PNG")
    elif operation == "crop":
        # ``keep`` denotes retained area.  Scale both dimensions by sqrt so
        # the original aspect ratio is preserved for non-square images.
        keep = float(value); scale = max(0.01, min(1.0, keep)) ** 0.5
        width = max(1, round(im.width * scale)); height = max(1, round(im.height * scale))
        left, top = (im.width - width) // 2, (im.height - height) // 2
        im.crop((left, top, left + width, top + height)).save(out, "PNG")
    elif operation == "resize_jpeg":
        ratio, quality = value
        im = im.resize((max(1, round(im.width * float(ratio))), max(1, round(im.height * float(ratio)))), Image.Resampling.LANCZOS)
        im.save(out, "JPEG", quality=int(quality))
    elif operation in ("metadata_clear", "format_convert"):
        if operation == "metadata_clear":
            # With no metadata to remove, retain the exact published bytes;
            # this permits a legitimate byte-identical binding check.
            if not Image.open(io.BytesIO(data)).info:
                return data
            im.save(out, "PNG")
        else:
            im.save(out, "JPEG", quality=90)
    elif operation == "screenshot_simulated":
        im.resize((768, 768), Image.Resampling.LANCZOS).save(out, "JPEG", quality=85)
    else:
        raise ValueError(f"unknown image transformation: {operation}")
    return out.getvalue()


def _pdf_transform(data: bytes, operation: str, value: Any = None) -> bytes:
    """Apply a deterministic PDF propagation transform.

    Rendering and screenshots are explicitly labelled simulated in records;
    no browser or reader screenshot is claimed here.
    """
    import pypdfium2 as pdfium
    if operation in ("render", "screenshot_simulated"):
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        src = pdfium.PdfDocument(data); out = io.BytesIO(); c = canvas.Canvas(out)
        dpi = float(value or media.PDF_DPI); scale = dpi / 72.0
        for page in src:
            pil = page.render(scale=scale).to_pil().convert("RGB")
            if operation == "screenshot_simulated":
                pil = pil.resize((max(1, pil.width // 2), max(1, pil.height // 2)), Image.Resampling.LANCZOS)
            c.setPageSize((page.get_width(), page.get_height())); c.drawImage(ImageReader(pil), 0, 0, width=page.get_width(), height=page.get_height()); c.showPage()
        c.save(); return out.getvalue()
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data)); writer = PdfWriter(); pages = list(reader.pages)
    if operation == "delete_page":
        pages = pages[1:] if len(pages) > 1 else []
    elif operation == "reorder_pages":
        pages = list(reversed(pages))
    elif operation == "replace_unmarked":
        # Replace the first page with an unmarked blank page generated by a
        # separate PDF.  This models a page-level replacement without
        # asserting that the detector can identify the original page identity.
        from reportlab.pdfgen import canvas
        blank = io.BytesIO(); c = canvas.Canvas(blank); c.showPage(); c.save(); pages = [PdfReader(io.BytesIO(blank.getvalue())).pages[0]] + pages[1:]
    else:
        raise ValueError(f"unknown PDF transformation: {operation}")
    for page in pages: writer.add_page(page)
    out = io.BytesIO(); writer.write(out); return out.getvalue()


def _transform(data: bytes, operation: str, value: Any = None) -> bytes:
    return _pdf_transform(data, operation, value) if data.startswith(b"%PDF-") else _image_transform(data, operation, value)


def _replacement_media(data: bytes) -> bytes:
    """Create a valid same-type replacement whose digest differs from source."""
    if data.startswith(b"%PDF-"):
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(data)); writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({"/Round3Replacement": "true"})
        out = io.BytesIO(); writer.write(out); return out.getvalue()
    image = Image.open(io.BytesIO(data)).convert("RGB")
    # Change a single pixel while preserving a decodable image container.
    px = image.getpixel((0, 0)); image.putpixel((0, 0), tuple((int(v) + 1) % 256 for v in px))
    out = io.BytesIO()
    fmt = "JPEG" if data[:2] == b"\xff\xd8" else "PNG"
    image.save(out, fmt, quality=95 if fmt == "JPEG" else None)
    return out.getvalue()


def _unrelated_media(data: bytes) -> bytes:
    """Generate a decodable, visibly different carrier for watermark-copy."""
    if data.startswith(b"%PDF-"):
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas
        reader = PdfReader(io.BytesIO(data)); out = io.BytesIO(); c = canvas.Canvas(out)
        for page in reader.pages:
            width = float(page.mediabox.width); height = float(page.mediabox.height)
            c.setPageSize((width, height)); c.setFillColorRGB(0.05, 0.65, 0.25); c.rect(0, 0, width, height, fill=1, stroke=0); c.setFillColorRGB(1, 1, 1); c.drawString(24, max(24, height - 40), "unrelated carrier"); c.showPage()
        c.save(); return out.getvalue()
    image = Image.open(io.BytesIO(data)).convert("RGB"); out = io.BytesIO(); carrier = Image.new("RGB", image.size, (13, 171, 47))
    if data[:2] == b"\xff\xd8": carrier.save(out, "JPEG", quality=90)
    else: carrier.save(out, "PNG")
    return out.getvalue()


def _cross_cid_pdf(first: bytes, second: bytes) -> bytes:
    """Build a genuine mixed-CID PDF by combining pages from two PDFs."""
    from pypdf import PdfReader, PdfWriter
    left, right = PdfReader(io.BytesIO(first)), PdfReader(io.BytesIO(second))
    writer = PdfWriter()
    if left.pages:
        writer.add_page(left.pages[0])
    if right.pages:
        writer.add_page(right.pages[0])
    out = io.BytesIO(); writer.write(out); return out.getvalue()


def _media_dimensions(data: bytes) -> dict[str, Any]:
    try:
        info = media.inspect_media(data)
        return {k: info[k] for k in ("width", "height", "page_count") if k in info}
    except Exception as exc:
        return {"status": "UNKNOWN", "error": type(exc).__name__}


def _cases(config: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
    if media_type == "application/pdf":
        p = config.get("pdf", {}); rows = [{"name": "exact", "operation": "identity", "value": None, "simulated": False}]
        for dpi in p.get("render_dpi", []): rows.append({"name": f"render_{dpi}dpi", "operation": "render", "value": dpi, "simulated": False})
        if p.get("screenshot_simulated"): rows.append({"name": "screenshot_simulated", "operation": "screenshot_simulated", "value": None, "simulated": True})
        if p.get("delete_page"): rows.append({"name": "delete_page", "operation": "delete_page", "value": None, "simulated": False})
        if p.get("reorder_pages"): rows.append({"name": "reorder_pages", "operation": "reorder_pages", "value": None, "simulated": False})
        if p.get("replace_unmarked"): rows.append({"name": "replace_unmarked", "operation": "replace_unmarked", "value": None, "simulated": False})
        return rows
    p = config.get("images", {}); rows = [{"name": "exact", "operation": "identity", "value": None, "simulated": False}]
    for q in p.get("jpeg_quality", []): rows.append({"name": f"jpeg_q{q}", "operation": "jpeg", "value": q, "simulated": False})
    for ratio in p.get("resize", []): rows.append({"name": f"resize_{ratio}", "operation": "resize", "value": ratio, "simulated": False})
    for ratio, quality in p.get("resize_jpeg", []): rows.append({"name": f"resize_{ratio}_jpeg_q{quality}", "operation": "resize_jpeg", "value": [ratio, quality], "simulated": False})
    for keep in p.get("crop_keep", []): rows.append({"name": f"crop_{keep}", "operation": "crop", "value": keep, "simulated": False})
    if p.get("metadata_clear"): rows.append({"name": "metadata_clear", "operation": "metadata_clear", "value": None, "simulated": False})
    if p.get("format_convert"): rows.append({"name": "format_convert", "operation": "format_convert", "value": None, "simulated": False})
    if p.get("screenshot_simulated"): rows.append({"name": "screenshot_simulated", "operation": "screenshot_simulated", "value": None, "simulated": True})
    return rows


def _service_for(run_dir: Path, config: dict[str, Any]) -> tuple[ProvenanceService, Path, str]:
    service = config.get("service")
    if service is not None:
        private = Path(config["private_path"]); password = str(config.get("passphrase", "")); return service, private, password
    root = run_dir / "registry"; service = ProvenanceService(root)
    private = run_dir / "evaluation-private.pem"; public = run_dir / "evaluation-public.pem"
    password = str(config.get("passphrase") or secrets.token_urlsafe(32))
    if not private.exists() or "passphrase" not in config:
        for old in (private, public):
            try: old.unlink()
            except FileNotFoundError: pass
        service.keygen(private, public, password)
        config["_ephemeral_credentials"] = True
    key_id = service.trust_key(public, str(config.get("publisher_id", "publisher-round3")))
    config.setdefault("publisher_id", "publisher-round3")
    return service, private, password


def _verify(service: ProvenanceService, path: Path, manifest_path: Path | None, mode: str, config: dict[str, Any],
            state_snapshot_path: Path | None = None, state_public_path: Path | None = None) -> tuple[dict[str, Any], dict[str, float]]:
    # ProvenanceService performs the actual read/decode once.  Do not warm a
    # second read merely to manufacture a file-read timing component.
    t1 = time.perf_counter_ns()
    try:
        observed = service.verify(path, manifest_path, mode, state_snapshot_path=state_snapshot_path, state_public_path=state_public_path)
    except Exception as exc:
        observed = {"conclusion": "ERROR", "reasons": [f"{type(exc).__name__}: {exc}"], "verification_mode": mode}
        config.setdefault("_errors", []).append({"stage": "verify", "path": str(path), "mode": mode, "error": f"{type(exc).__name__}: {exc}"})
    total_ms = (time.perf_counter_ns() - t1) / 1_000_000
    return observed, {"read_ms": None, "verify_ms": round(total_ms, 3), "total_ms": round(total_ms, 3)}


def evaluate_sample(sample: dict[str, Any], run_dir: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate one real input across G0--G3 and configured transformations.

    ``sample`` needs ``sample_id`` and ``local_path`` (or ``path``).  The
    return value is stable JSON-friendly data so a runner can append each
    section to JSONL and resume after interruption.
    """
    cfg = _merge(DEFAULT_CONFIG, config or {}); out = Path(run_dir); out.mkdir(parents=True, exist_ok=True)
    sample_id = str(sample.get("sample_id") or sample.get("id") or Path(sample.get("local_path", sample.get("path", "sample"))).stem)
    source_path = Path(sample.get("local_path") or sample.get("path"))
    started = time.monotonic(); failures: list[dict[str, Any]] = []; rows: list[dict[str, Any]] = []; attacks: list[dict[str, Any]] = []; timings: list[dict[str, Any]] = []
    sample_dir = out / "samples" / sample_id; sample_dir.mkdir(parents=True, exist_ok=True)
    try:
        original = source_path.read_bytes(); media_type = media.sniff(original)
    except Exception as exc:
        return {"samples": [], "attacks": [], "timings": [], "failures": [{"sample_id": sample_id, "stage": "read", "error": f"{type(exc).__name__}: {exc}"}], "storage": {}, "metadata": {"sample_id": sample_id, "status": "FAILED"}}
    source_copy = sample_dir / ("source.pdf" if media_type == "application/pdf" else "source.bin"); source_copy.write_bytes(original)
    # Keep registry and signing material isolated per sample.  Lifecycle
    # attacks revoke a key/content only inside this sample's registry.
    service, private, password = _service_for(sample_dir, cfg)
    state_private = sample_dir / "state-private.pem"
    state_public = sample_dir / "state-public.pem"
    state_password = str(cfg.get("state_passphrase") or secrets.token_urlsafe(32))
    if not state_private.exists():
        service.keygen(state_private, state_public, state_password)
    def verify_current(verifier: ProvenanceService, path: Path, manifest_path: Path | None, mode: str):
        return _verify(verifier, path, manifest_path, mode, cfg, state_snapshot, state_public)
    # G0/G2 are deliberately verified against registries that contain no G3
    # entries.  This keeps registry-backed lookup from silently gifting a
    # credential to the watermark-only and no-provenance controls.
    isolated_services = {
        "G0": ProvenanceService(sample_dir / "isolated-g0-registry"),
        "G2": ProvenanceService(sample_dir / "isolated-g2-registry"),
    }
    publisher = str(cfg.get("publisher_id", "publisher-round3")); groups: dict[str, dict[str, Any]] = {}
    state_snapshot = sample_dir / "state-snapshot.json"
    try:
        publish_started = time.perf_counter_ns()
        g1 = _signed_only(service, source_copy, private, password, sample_dir, publisher)
        g1_publish_ms = (time.perf_counter_ns() - publish_started) / 1_000_000
        publish_started = time.perf_counter_ns()
        g3 = service.register(source_copy, private, password, publisher_id=publisher)
        g3_publish_ms = (time.perf_counter_ns() - publish_started) / 1_000_000
        timings.extend([{"sample_id": sample_id, "group": "G1", "phase": "publish", "publish_ms": round(g1_publish_ms, 3)}, {"sample_id": sample_id, "group": "G3", "phase": "publish", "publish_ms": round(g3_publish_ms, 3)}])
        g2_cid = secrets.token_hex(16); g2_bytes, g2_profile = media.embed(original, g2_cid, media_type); g2_path = sample_dir / "G2.bin"; g2_path.write_bytes(g2_bytes)
        groups = {
            "G0": {"path": source_copy, "manifest": None, "content_id": None},
            "G1": {"path": Path(g1["media_path"]), "manifest": Path(g1["manifest_path"]), "content_id": g1["content_id"]},
            "G2": {"path": g2_path, "manifest": None, "content_id": g2_cid},
            "G3": {"path": Path(g3["media_path"]), "manifest": Path(g3["manifest_path"]), "content_id": g3["content_id"]},
        }
    except Exception as exc:
        failures.append({"sample_id": sample_id, "stage": "publish", "error": f"{type(exc).__name__}: {exc}"})
    if groups:
        for case in _cases(cfg, media_type):
            for group in cfg.get("groups", GROUPS):
                if group not in groups: continue
                base = groups[group]
                sidecar_values = ["not_applicable"] if group in ("G0", "G2") else cfg.get("sidecar_states", ["retained", "lost"])
                for sidecar in sidecar_values:
                    # G0/G2 have no sidecar capability; keep one explicit N/A row.
                    manifest_path = base["manifest"] if sidecar == "retained" and base["manifest"] else None
                    mode = str(cfg.get("verification_modes", {}).get(sidecar, "hard" if manifest_path else "recover"))
                    if not manifest_path:
                        mode = "recover"
                    sidecar_value = "not_applicable" if group in ("G0", "G2") else sidecar
                    try:
                        transform_started = time.monotonic(); base_bytes = Path(base["path"]).read_bytes(); transformed = base_bytes if case["operation"] == "identity" else _transform(base_bytes, case["operation"], case["value"]); transform_ms = (time.monotonic() - transform_started) * 1000
                        media_path = sample_dir / f"{group}-{case['name']}-{sidecar}.bin"; media_path.write_bytes(transformed)
                        verifier = isolated_services.get(group, service)
                        observed, measured = verify_current(verifier, media_path, manifest_path, mode)
                        expected = base["content_id"]
                        identity = transformed == base_bytes
                        if group == "G0": expected_outcome = "NO_PROVENANCE"
                        elif group == "G2": expected_outcome = "ORIGIN_HINT_ONLY"
                        elif group == "G1": expected_outcome = "VERIFIED_BINDING_ONLY" if identity and manifest_path else ("NO_PROVENANCE" if not manifest_path else "INDETERMINATE")
                        elif identity: expected_outcome = "VERIFIED_BINDING_ONLY"
                        elif manifest_path: expected_outcome = "INDETERMINATE"
                        else: expected_outcome = "ORIGIN_HINT_ONLY"
                        content_ids = observed.get("recovered_content_ids", [])
                        capabilities = {"content_id_recovery": group in ("G2", "G3"), "sidecar_lookup": group in ("G1", "G3"), "registry_lookup_when_sidecar_lost": group == "G3", "signature": group in ("G1", "G3"), "revocation": "N/A" if group in ("G0", "G1", "G2") else "full_only"}
                        row = {"sample_id": sample_id, "group": group, "transform": case["name"], "media_type": media_type, "simulated": case["simulated"], "sidecar": sidecar_value, "capabilities": capabilities, "detector_input": {"manifest_supplied": bool(manifest_path), "mode": mode}, "observed": {k: observed.get(k) for k in ("conclusion", "manifest_presence", "watermark_status", "watermark_details", "signature_valid", "issuer_trusted", "content_hash_match", "parent_chain_status", "authorization_status", "content_revocation_status", "key_revocation_status", "state_freshness", "latest_version_status", "recovered_content_ids", "reasons")}, "media_pages": (observed.get("watermark_details") or {}).get("pages") if media_type == "application/pdf" else None, "truth": {"expected_content_id": expected, "expected_outcome": expected_outcome}, "recognition": {"watermark_recovered": bool(expected and expected in content_ids)}, "handled": bool(observed.get("conclusion") == expected_outcome), "media_path": str(media_path), "media_dimensions": _media_dimensions(transformed), "file_bytes": len(transformed)}
                        rows.append(row); timings.append({"sample_id": sample_id, "group": group, "transform": case["name"], "mode": mode, "transform_ms": round(transform_ms, 3), **measured})
                    except Exception as exc:
                        failures.append({"sample_id": sample_id, "stage": "verify", "group": group, "transform": case["name"], "error": f"{type(exc).__name__}: {exc}"})
    # Full scope is measured separately for the exact G3 publication.  A
    # fresh signed state snapshot is preparation for validation, and its time
    # is retained in the timing record instead of being folded into hard or
    # recover.
    if groups and "G3" in groups:
        try:
            state_started = time.monotonic(); state_info = service.state_snapshot(state_private, state_password, output_path=state_snapshot, state_public_path=state_public); state_ms = (time.monotonic() - state_started) * 1000
            exact_path = sample_dir / "G3-exact-full.bin"; exact_path.write_bytes(Path(groups["G3"]["path"]).read_bytes())
            observed, measured = verify_current(service, exact_path, groups["G3"]["manifest"], "full")
            rows.append({"sample_id": sample_id, "group": "G3", "transform": "exact", "media_type": media_type, "simulated": False, "sidecar": "retained", "capabilities": {"content_id_recovery": True, "sidecar_lookup": True, "registry_lookup_when_sidecar_lost": True, "signature": True, "revocation": "full"}, "detector_input": {"manifest_supplied": True, "mode": "full"}, "observed": {k: observed.get(k) for k in ("conclusion", "manifest_presence", "watermark_status", "watermark_details", "signature_valid", "issuer_trusted", "content_hash_match", "parent_chain_status", "authorization_status", "content_revocation_status", "key_revocation_status", "state_freshness", "latest_version_status", "recovered_content_ids", "reasons")}, "media_pages": (observed.get("watermark_details") or {}).get("pages") if media_type == "application/pdf" else None, "truth": {"expected_content_id": groups["G3"]["content_id"], "expected_outcome": "VERIFIED_EXACT"}, "handled": observed.get("conclusion") == "VERIFIED_EXACT", "media_path": str(exact_path), "media_dimensions": _media_dimensions(exact_path.read_bytes()), "file_bytes": exact_path.stat().st_size})
            timings.append({"sample_id": sample_id, "group": "G3", "transform": "exact", "mode": "full", "state_snapshot_ms": round(state_ms, 3), **measured})
        except Exception as exc:
            failures.append({"sample_id": sample_id, "stage": "full_state", "group": "G3", "error": f"{type(exc).__name__}: {exc}"})
    # Authorized derivation is a real service operation and is meaningful for
    # images in the current implementation.  PDF derivation remains explicit
    # N/A rather than being silently treated as a pass.
    if groups and "G3" in groups:
        if media_type not in ("application/pdf",):
            try:
                child = service.derive(groups["G3"]["content_id"], private, password, "resize")
                service.state_snapshot(state_private, state_password, output_path=state_snapshot, state_public_path=state_public)
                child_path = sample_dir / "G3-derive-resize.bin"; child_path.write_bytes(Path(child["media_path"]).read_bytes())
                observed, measured = verify_current(service, child_path, Path(child["manifest_path"]), "full")
                rows.append({"sample_id": sample_id, "group": "G3", "transform": "derive_resize", "media_type": media_type, "simulated": False, "sidecar": "retained", "capabilities": {"content_id_recovery": True, "sidecar_lookup": True, "registry_lookup_when_sidecar_lost": True, "signature": True, "parent_chain": True, "authorization": True, "revocation": "full"}, "detector_input": {"manifest_supplied": True, "mode": "full"}, "observed": {k: observed.get(k) for k in ("conclusion", "parent_chain_status", "authorization_status", "latest_version_status", "state_freshness", "reasons")}, "truth": {"expected_content_id": child["content_id"], "expected_outcome": "VERIFIED_DERIVATIVE"}, "handled": observed.get("conclusion") == "VERIFIED_DERIVATIVE", "media_path": str(child_path)})
                timings.append({"sample_id": sample_id, "group": "G3", "transform": "derive_resize", "mode": "full", **measured})
                parent_observed, parent_timing = verify_current(service, groups["G3"]["path"], groups["G3"]["manifest"], "full")
                attacks.append({"sample_id": sample_id, "attack": "historical_parent", "observed": {"conclusion": parent_observed.get("conclusion"), "latest_version_status": parent_observed.get("latest_version_status")}, "truth": {"expected": "INDETERMINATE", "expected_field": "conclusion", "expected_latest_version_status": "FAIL"}, "handled": parent_observed.get("conclusion") == "INDETERMINATE" and parent_observed.get("latest_version_status") == "FAIL", "timing": parent_timing})
                historical_hard, historical_hard_timing = verify_current(service, groups["G3"]["path"], groups["G3"]["manifest"], "hard")
                attacks.append({"sample_id": sample_id, "attack": "historical_view_hard", "observed": {"conclusion": historical_hard.get("conclusion"), "content_hash_match": historical_hard.get("content_hash_match")}, "truth": {"expected": "VERIFIED_BINDING_ONLY", "expected_field": "conclusion", "reason": "合法历史查看只声明硬绑定"}, "handled": historical_hard.get("conclusion") == "VERIFIED_BINDING_ONLY", "timing": historical_hard_timing})
            except Exception as exc:
                failures.append({"sample_id": sample_id, "stage": "derive", "group": "G3", "error": f"{type(exc).__name__}: {exc}"})
        else:
            attacks.append({"sample_id": sample_id, "attack": "derive_pdf", "applicable": False, "observed": {"conclusion": "N/A"}, "truth": {"expected": "N/A", "reason": "service derive currently supports images only"}, "handled": None, "timing": None})
    # A compact attack set uses isolated files and records failures as errors,
    # never as successful attack detections.
    if groups and "G3" in groups:
        g3 = groups["G3"]
        try:
            env = json.loads(Path(g3["manifest"]).read_text(encoding="utf-8")); tampered = json.loads(json.dumps(env)); tampered["body"]["publisher_id"] = "attacker"; tp = sample_dir / "manifest-tampered.json"; _write_json(tp, tampered)
            replaced = sample_dir / "replacement.bin"; replaced.write_bytes(_replacement_media(original))
            for name, path, sidecar, expected, expected_field in (("manifest_tamper", g3["path"], tp, "REJECTED", "conclusion"), ("content_replacement", replaced, g3["manifest"], "FAIL", "content_hash_match"), ("sidecar_lost", g3["path"], None, "VERIFIED_BINDING_ONLY", "conclusion")):
                observed, measured = verify_current(service, path, sidecar, "full" if sidecar else "recover"); attacks.append({"sample_id": sample_id, "attack": name, "observed": {"conclusion": observed.get("conclusion"), "content_hash_match": observed.get("content_hash_match"), "watermark_status": observed.get("watermark_status")}, "truth": {"expected": expected, "expected_field": expected_field}, "handled": observed.get(expected_field) == expected, "timing": measured})
            # Signature corruption and an unregistered signer are separate
            # predicates; neither is counted from a generic ERROR outcome.
            bad_sig = json.loads(json.dumps(env)); raw_sig = bytearray(base64.b64decode(bad_sig["signature"])); raw_sig[0] ^= 1; bad_sig["signature"] = base64.b64encode(bytes(raw_sig)).decode("ascii")
            bad_sig_path = sample_dir / "signature-tampered.json"; _write_json(bad_sig_path, bad_sig)
            observed, measured = verify_current(service, g3["path"], bad_sig_path, "full")
            attacks.append({"sample_id": sample_id, "attack": "signature_tamper", "observed": {"conclusion": observed.get("conclusion"), "signature_valid": observed.get("signature_valid")}, "truth": {"expected": "FAIL", "expected_field": "signature_valid"}, "handled": observed.get("signature_valid") == "FAIL", "timing": measured})
            untrusted_private = sample_dir / "untrusted-private.pem"; untrusted_public = sample_dir / "untrusted-public.pem"; untrusted_password = secrets.token_urlsafe(32)
            service.keygen(untrusted_private, untrusted_public, untrusted_password)
            untrusted_key_id = service.trust_key(untrusted_public, publisher, trusted=False)
            unknown_body = json.loads(json.dumps(env["body"])); unknown_body["signer_key_id"] = untrusted_key_id
            unknown_key = manifest.sign_manifest(unknown_body, service.crypto, untrusted_private, untrusted_password)
            unknown_key_path = sample_dir / "untrusted-key.json"; _write_json(unknown_key_path, unknown_key)
            observed, measured = verify_current(service, g3["path"], unknown_key_path, "full")
            attacks.append({"sample_id": sample_id, "attack": "untrusted_key", "observed": {"conclusion": observed.get("conclusion"), "issuer_trusted": observed.get("issuer_trusted"), "signature_valid": observed.get("signature_valid")}, "truth": {"expected": "FAIL", "expected_field": "issuer_trusted"}, "handled": observed.get("issuer_trusted") == "FAIL", "timing": measured})
            copied = sample_dir / "copied-watermark.bin"; copied.write_bytes(media.embed(_unrelated_media(original), env["body"]["content_id"], media_type)[0])
            observed, measured = verify_current(service, copied, None, "recover")
            attacks.append({"sample_id": sample_id, "attack": "copied_watermark_unrelated", "observed": {"conclusion": observed.get("conclusion"), "content_hash_match": observed.get("content_hash_match"), "watermark_status": observed.get("watermark_status")}, "truth": {"expected": "FAIL", "expected_field": "content_hash_match"}, "handled": observed.get("content_hash_match") == "FAIL", "timing": measured})
            if cfg.get("pdf", {}).get("cross_content_id", True) or cfg.get("images", {}).get("cross_content_id", True):
                foreign = service.register(source_copy, private, password, publisher_id=publisher)
                foreign_path = sample_dir / "foreign-content.bin"; foreign_path.write_bytes(Path(foreign["media_path"]).read_bytes())
                service.state_snapshot(state_private, state_password, output_path=state_snapshot, state_public_path=state_public)
                cross_path = foreign_path
                if media_type == "application/pdf":
                    cross_path = sample_dir / "cross-content-id.pdf"; cross_path.write_bytes(_cross_cid_pdf(Path(g3["path"]).read_bytes(), foreign_path.read_bytes()))
                observed, measured = verify_current(service, cross_path, g3["manifest"], "full")
                attacks.append({"sample_id": sample_id, "attack": "cross_content_id", "observed": {"conclusion": observed.get("conclusion"), "content_hash_match": observed.get("content_hash_match"), "watermark_status": observed.get("watermark_status"), "reasons": observed.get("reasons")}, "truth": {"expected": "REJECTED", "expected_field": "conclusion", "reason": "foreign valid watermark must not satisfy the original sidecar"}, "handled": observed.get("conclusion") == "REJECTED", "timing": measured})
            # Lifecycle checks use this sample's isolated registry and are
            # therefore safe to mutate without affecting neighbouring samples.
            env_body = env.get("body", {}); cid = env_body.get("content_id"); key_id = env_body.get("signer_key_id")
            old_state = sample_dir / "state-old-signed.json"
            if state_snapshot.exists(): shutil.copy2(state_snapshot, old_state)
            if isinstance(cid, str):
                service.revoke("content", cid, "round3-content-case"); service.state_snapshot(state_private, state_password, output_path=state_snapshot, state_public_path=state_public)
                observed, measured = verify_current(service, g3["path"], g3["manifest"], "full"); attacks.append({"sample_id": sample_id, "attack": "content_revoke", "observed": {"conclusion": observed.get("conclusion"), "content_revocation_status": observed.get("content_revocation_status")}, "truth": {"expected": "FAIL", "expected_field": "content_revocation_status"}, "handled": observed.get("content_revocation_status") == "FAIL", "timing": measured})
            if isinstance(key_id, str):
                service.revoke("key", key_id, "round3-key-case"); service.state_snapshot(state_private, state_password, output_path=state_snapshot, state_public_path=state_public)
                observed, measured = verify_current(service, g3["path"], g3["manifest"], "full"); attacks.append({"sample_id": sample_id, "attack": "key_revoke", "observed": {"conclusion": observed.get("conclusion"), "content_revocation_status": observed.get("content_revocation_status"), "key_revocation_status": observed.get("key_revocation_status")}, "truth": {"expected": "FAIL", "expected_field": "key_revocation_status"}, "handled": observed.get("key_revocation_status") == "FAIL", "timing": measured})
            snapshot = sample_dir / "state-snapshot.json"; backup = sample_dir / "state-snapshot.missing.json"
            if snapshot.exists():
                snapshot.replace(backup)
                try:
                    observed, measured = verify_current(service, g3["path"], g3["manifest"], "full"); attacks.append({"sample_id": sample_id, "attack": "state_missing", "observed": {"conclusion": observed.get("conclusion"), "state_freshness": observed.get("state_freshness")}, "truth": {"expected": "UNKNOWN", "expected_field": "state_freshness"}, "handled": observed.get("state_freshness") == "UNKNOWN" and observed.get("conclusion") != "ERROR", "timing": measured})
                finally:
                    backup.replace(snapshot)
            # Expired and unavailable snapshots must remain UNKNOWN; neither
            # state condition is promoted to a successful attack detection.
            if snapshot.exists():
                expired = sample_dir / "state-expired.json"; status = service.registry.status(); expired_body = {"schema_version": 1, "sequence": int(status.get("sequence", 0)), "issued_at": "2000-01-01T00:00:00Z", "expires_at": "2000-01-01T00:05:00Z", "latest": status.get("latest", {}), "revoked_content": status.get("revoked_content", []), "revoked_keys": status.get("revoked_keys", []), "revocations": status.get("revocations", [])}; expired_obj = {"body": expired_body, "signature": base64.b64encode(service.crypto.sign(state.DOMAIN + state._jcs(expired_body), state_private, state_password)).decode("ascii"), "signature_encoding": "base64-DER", "protocol": "GM-PROVENANCE-STATE-v1"}; _write_json(expired, expired_obj)
                observed, measured = _verify(service, g3["path"], g3["manifest"], "full", cfg, expired, state_public)
                attacks.append({"sample_id": sample_id, "attack": "state_expired", "observed": {"conclusion": observed.get("conclusion"), "state_freshness": observed.get("state_freshness")}, "truth": {"expected": "UNKNOWN", "expected_field": "state_freshness"}, "handled": observed.get("state_freshness") == "UNKNOWN", "timing": measured})
                mismatch = old_state
                observed, measured = _verify(service, g3["path"], g3["manifest"], "full", cfg, mismatch, state_public)
                attacks.append({"sample_id": sample_id, "attack": "state_mismatch", "observed": {"conclusion": observed.get("conclusion"), "state_freshness": observed.get("state_freshness")}, "truth": {"expected": "FAIL", "expected_field": "state_freshness", "reason": "validly signed but stale sequence must fail anti-rollback freshness"}, "handled": observed.get("state_freshness") == "FAIL", "timing": measured})
        except Exception as exc:
            failures.append({"sample_id": sample_id, "stage": "attacks", "error": f"{type(exc).__name__}: {exc}"})
    # The signing key is an execution fixture, never a result artifact.
    # Private keys are execution fixtures; never leave them in a result run.
    for error in cfg.pop("_errors", []):
        failures.append({"sample_id": sample_id, **error})
    for attack in attacks:
        attack.setdefault("group", "G3")
        attack.setdefault("applicable", True)
        attack.setdefault("predicate", attack.get("truth", {}).get("expected_field", "conclusion"))
    for credential in (sample_dir / "evaluation-private.pem", sample_dir / "evaluation-public.pem", sample_dir / "untrusted-private.pem", sample_dir / "untrusted-public.pem", state_private):
        try:
            credential.unlink()
        except FileNotFoundError:
            pass
    files = [p for p in sample_dir.rglob("*") if p.is_file()]
    retention = str(cfg.get("artifact_retention", "minimal"))
    bytes_before_prune = sum(p.stat().st_size for p in files)
    if retention == "minimal":
        # Keep enough evidence for independent inspection and A/B/C quality
        # diagnostics, while avoiding dozens of repeated full-size PDF renders
        # in a 100-sample run.
        keep_names = {"source.bin", "source.pdf", "G3-exact-retained.bin", "G3-exact-full.bin", "G3-derive-resize.bin", "state-snapshot.json", "state-public.pem"}
        for candidate in list(sample_dir.rglob("*")):
            if not candidate.is_file():
                continue
            if candidate.name in keep_names or candidate.parent.name == "manifests":
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
        for directory in sorted((p for p in sample_dir.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
    if retention == "minimal":
        for record in rows:
            media_ref = record.get("media_path")
            if media_ref and Path(media_ref).is_relative_to(sample_dir) and not Path(media_ref).exists():
                record["media_path"] = "not retained"
        for record in attacks:
            media_ref = record.get("media_path")
            if media_ref and Path(media_ref).is_relative_to(sample_dir) and not Path(media_ref).exists():
                record["media_path"] = "not retained"
    files = [p for p in sample_dir.rglob("*") if p.is_file()]
    registry_files = [p for p in (sample_dir / "registry").rglob("*") if p.is_file()]
    isolated_registry_files = [p for name in ("isolated-g0-registry", "isolated-g2-registry") for p in (sample_dir / name).rglob("*") if p.is_file()]
    storage = {"bytes": sum(p.stat().st_size for p in files), "bytes_before_prune": bytes_before_prune, "artifact_retention": retention, "manifest_bytes": sum(p.stat().st_size for p in files if p.suffix == ".json"), "media_bytes": sum(p.stat().st_size for p in files if p.suffix in (".bin", ".png", ".jpg", ".jpeg", ".pdf")), "registry_bytes": sum(p.stat().st_size for p in registry_files + isolated_registry_files), "sample_dir": str(sample_dir)}
    metadata = {"sample_id": sample_id, "source_family_id": sample.get("source_family_id"), "source_reference": sample.get("source_reference"), "license_status": sample.get("license_status"), "origin_category": sample.get("origin_category"), "media_type": media_type, "base_bytes": len(original), "groups": list(groups), "elapsed_ms": round((time.monotonic() - started) * 1000, 3), "watermark_profile": cfg.get("watermark_profile"), "truth_is_evaluator_only": True}
    gc.collect()
    return {"samples": rows, "attacks": attacks, "timings": timings, "failures": failures, "storage": storage, "metadata": metadata}


__all__ = ["GROUPS", "DEFAULT_CONFIG", "load_config", "evaluate_sample"]
