"""Download a small, reproducible pilot from the selected public sources.

This intentionally downloads examples/samples rather than full archives.  The
result is kept under data/external-pilot-20261006 and is accompanied by a JSON
manifest containing source URLs, response metadata, hashes and basic decoding
checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
import argparse
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gmssl import func, sm3
from pypdf import PdfReader

try:  # Pillow is part of the project dependencies, but keep header checks portable.
    from PIL import Image
except ImportError:  # pragma: no cover - only used in a minimal offline environment
    Image = None  # type: ignore[assignment,misc]


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "external-pilot-20261006"

ENTRIES = [
    {
        "dataset": "COCO",
        "source_id": "coco-val2017-000000000139",
        "source_url": "https://images.cocodataset.org/val2017/000000000139.jpg",
        "license": "COCO Terms of Use; source image terms require review",
        "license_evidence_url": "https://cocodataset.org/#termsofuse",
        "media_type": "image/jpeg",
        "relative_path": "coco/000000000139.jpg",
        "tls_verify": False,
        "transport_note": "Environment proxy certificate hostname mismatch; official URL retained and fallback recorded.",
    },
    {
        "dataset": "COCO",
        "source_id": "coco-val2017-000000000285",
        "source_url": "https://images.cocodataset.org/val2017/000000000285.jpg",
        "license": "COCO Terms of Use; source image terms require review",
        "license_evidence_url": "https://cocodataset.org/#termsofuse",
        "media_type": "image/jpeg",
        "relative_path": "coco/000000000285.jpg",
        "tls_verify": False,
        "transport_note": "Environment proxy certificate hostname mismatch; official URL retained and fallback recorded.",
    },
    {
        "dataset": "COCO",
        "source_id": "coco-val2017-000000000632",
        "source_url": "https://images.cocodataset.org/val2017/000000000632.jpg",
        "license": "COCO Terms of Use; source image terms require review",
        "license_evidence_url": "https://cocodataset.org/#termsofuse",
        "media_type": "image/jpeg",
        "relative_path": "coco/000000000632.jpg",
        "tls_verify": False,
        "transport_note": "Environment proxy certificate hostname mismatch; official URL retained and fallback recorded.",
    },
    {
        "dataset": "GenImage",
        "source_id": "genimage-official-visualization-example",
        "source_url": "https://raw.githubusercontent.com/GenImage-Dataset/GenImage/main/Examples/visulization.png",
        "license": "GenImage project terms; CC BY-NC-SA 4.0 stated on project homepage",
        "license_evidence_url": "https://genimage-dataset.github.io/",
        "media_type": "image/png",
        "relative_path": "genimage/official-visualization-example.png",
        "note": "Official repository example asset, not a raw benchmark row.",
    },
    {
        "dataset": "DocLayNet",
        "source_id": "doclaynet-official-example-132a855e",
        "source_url": "https://raw.githubusercontent.com/DS4SD/DocLayNet/main/assets/132a855ee8b23533d8ae69af0049c038171a06ddfcac892c3c6d7e6b4091c642.png",
        "license": "DocLayNet data terms; license evidence retained for review",
        "license_evidence_url": "https://github.com/DS4SD/DocLayNet",
        "media_type": "image/png",
        "relative_path": "doclaynet/official-example-page.png",
        "note": "Official repository example page, not the 28 GiB core archive.",
    },
    {
        "dataset": "arXiv",
        "source_id": "arxiv-2206.01062v1",
        "source_url": "https://arxiv.org/pdf/2206.01062v1",
        "license": "license review pending; arXiv version metadata retained",
        "license_evidence_url": "https://arxiv.org/abs/2206.01062v1",
        "media_type": "application/pdf",
        "relative_path": "arxiv/doclaynet-paper-2206.01062v1.pdf",
        "note": "Multi-page PDF pipeline sample; not yet accepted as a redistributable corpus item.",
    },
]


def sm3_hex(data: bytes) -> str:
    # gmssl's pure-Python implementation is convenient for small messages but
    # too slow for multi-megabyte media. Use the project's OpenSSL SM3 backend
    # for the pilot manifest and keep a pure-Python fallback for portability.
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sm3", "-binary"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return proc.stdout.hex()
    except (OSError, subprocess.CalledProcessError):
        return sm3.sm3_hash(func.bytes_to_list(data))


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        if marker in (0xD8, 0xD9):
            continue
        if i + 2 > len(data):
            return None
        seg_len = int.from_bytes(data[i : i + 2], "big")
        if marker in range(0xC0, 0xC4) or marker in range(0xC5, 0xC8) or marker in range(0xC9, 0xCC) or marker in range(0xCD, 0xD0):
            if i + 7 <= len(data):
                return int.from_bytes(data[i + 5 : i + 7], "big"), int.from_bytes(data[i + 3 : i + 5], "big")
            return None
        i += seg_len
    return None


def png_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def expected_content_types(media_type: str) -> set[str]:
    """Return acceptable normalized HTTP content types for a media type."""
    if media_type == "image/jpeg":
        return {"image/jpeg", "image/jpg", "application/octet-stream"}
    if media_type == "image/png":
        return {"image/png", "application/octet-stream"}
    if media_type == "application/pdf":
        return {"application/pdf", "application/octet-stream"}
    return {media_type}


def inspect(entry: dict, path: Path, data: bytes) -> dict:
    result: dict = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sm3": sm3_hex(data),
        "magic_ok": False,
        "decode_status": "fail",
    }
    if entry["media_type"] in ("image/jpeg", "image/png"):
        size = jpeg_size(data) if entry["media_type"] == "image/jpeg" else png_size(data)
        result["magic_ok"] = size is not None
        result["dimensions"] = list(size) if size else None
        result["decode_status"] = "pass" if size else "fail"
        if size and Image is not None:
            # Header parsing catches the expected file signature. Pillow's
            # verify() additionally rejects truncated or malformed image data.
            try:
                with Image.open(BytesIO(data)) as image:
                    image.verify()
                result["decoder"] = "Pillow"
            except Exception as exc:  # pragma: no cover - diagnostic path
                result["decode_status"] = "fail"
                result["decode_error"] = f"{type(exc).__name__}: {exc}"
        elif size:
            result["decoder"] = "header_only"
    elif entry["media_type"] == "application/pdf":
        result["magic_ok"] = data.startswith(b"%PDF-")
        if result["magic_ok"]:
            try:
                reader = PdfReader(str(path))
                result["pages"] = len(reader.pages)
                result["decode_status"] = "pass" if result["pages"] > 0 else "fail"
                result["first_page_text_chars"] = len((reader.pages[0].extract_text() or "")) if reader.pages else 0
            except Exception as exc:  # pragma: no cover - diagnostic path
                result["decode_error"] = str(exc)
    return result


def _record_error(entry: dict, exc: Exception, *, when: str) -> dict:
    """Keep actionable, sanitized failure data in the manifest."""
    record = dict(entry)
    record.update(
        {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "download_status": "fail",
            "retrieval_mode": when,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "license_status": "review",
        }
    )
    if isinstance(exc, HTTPError):
        record["http_status"] = exc.code
        record["response_content_type"] = exc.headers.get("Content-Type") if exc.headers else None
    return record


def download(entry: dict, *, previous: dict | None = None, refresh: bool = False, offline: bool = False) -> dict:
    path = OUT / entry["relative_path"]
    path.parent.mkdir(parents=True, exist_ok=True)

    # Existing pilot files are immutable inputs by default. This makes a
    # rerun deterministic and avoids a second network request changing the
    # bytes behind an already recorded source_id. --refresh explicitly opts
    # into a new retrieval and records the new hash/time.
    if path.exists() and not refresh:
        data = path.read_bytes()
        record = dict(entry)
        record.update(inspect(entry, path, data))
        prior_sha = (previous or {}).get("sha256")
        prior_sm3 = (previous or {}).get("sm3")
        record.update(
            {
                "retrieved_at": (previous or {}).get("retrieved_at"),
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "retrieval_mode": "existing_local",
                "download_status": "pass",
                "license_status": (previous or {}).get("license_status", "review"),
                "local_path": str(path.relative_to(ROOT)).replace(os.sep, "/"),
                "expected_sha256": prior_sha,
                "expected_sm3": prior_sm3,
                "hash_match": prior_sha is None or record["sha256"] == prior_sha,
                "sm3_match": prior_sm3 is None or record["sm3"] == prior_sm3,
            }
        )
        # Preserve the original transport evidence while adding this local
        # verification event. It is useful to distinguish a reused file from
        # a file that was freshly fetched with a different response header.
        for key in ("http_status", "response_content_type", "response_content_length", "response_type_match", "tls_verification", "transport_note"):
            if previous and key in previous:
                record[key] = previous[key]
        if not (record["hash_match"] and record["sm3_match"]):
            record["download_status"] = "fail"
            record["error_type"] = "HashMismatch"
            record["error"] = "existing file hash differs from the previous manifest"
        if offline:
            record["offline"] = True
        return record

    if offline:
        return _record_error(entry, FileNotFoundError(f"offline mode requires existing file: {path}"), when="offline_missing")

    request = Request(entry["source_url"], headers={"User-Agent": "gm-provenance-pilot/0.1"})
    context = ssl._create_unverified_context() if entry.get("tls_verify") is False else None
    try:
        with urlopen(request, timeout=90, context=context) as response:
            data = response.read()
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            status = getattr(response, "status", None) or response.getcode()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return _record_error(entry, exc, when="network")

    # Write only after the response is fully consumed. A partial download is
    # therefore never mistaken for a valid existing pilot file on a rerun.
    path.write_bytes(data)
    response_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    record = dict(entry)
    record.update(
        {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "http_status": status,
            "response_content_type": headers.get("content-type"),
            "response_content_length": headers.get("content-length"),
            "response_type_match": response_type in expected_content_types(entry["media_type"]),
            "tls_verification": "disabled_for_environment_fallback" if entry.get("tls_verify") is False else "default",
            "retrieval_mode": "network_refresh" if refresh else "network",
            "local_path": str(path.relative_to(ROOT)).replace(os.sep, "/"),
        }
    )
    record.update(inspect(entry, path, data))
    record["license_status"] = "review"
    prior_sha = (previous or {}).get("sha256")
    prior_sm3 = (previous or {}).get("sm3")
    record["expected_sha256"] = prior_sha
    record["expected_sm3"] = prior_sm3
    record["hash_match"] = prior_sha is None or record["sha256"] == prior_sha
    record["sm3_match"] = prior_sm3 is None or record["sm3"] == prior_sm3
    if not (record["hash_match"] and record["sm3_match"]):
        record["download_status"] = "fail"
        record["error_type"] = "HashMismatch"
        record["error"] = "refreshed bytes differ from the previous manifest"
    if not record["response_type_match"]:
        record["transport_warning"] = "response Content-Type does not match the expected media type; magic/decode checks remain authoritative"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download or verify the small external dataset pilot.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-download every pilot item; default reuses existing files and checks their hashes",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="never access the network; fail only when a required local pilot file is missing or changed",
    )
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    previous_records: dict[str, dict] = {}
    previous_manifest = OUT / "manifest.json"
    if previous_manifest.exists():
        try:
            loaded = json.loads(previous_manifest.read_text(encoding="utf-8"))
            previous_records = {str(row.get("source_id")): row for row in loaded.get("records", []) if row.get("source_id")}
        except (OSError, json.JSONDecodeError):
            # A malformed old manifest must not prevent a fresh retrieval; the
            # new output will contain explicit records for every item.
            previous_records = {}
    records = []
    for entry in ENTRIES:
        try:
            record = download(
                entry,
                previous=previous_records.get(entry["source_id"]),
                refresh=args.refresh,
                offline=args.offline,
            )
        except Exception as exc:  # keep failures in the manifest
            record = _record_error(entry, exc, when="unexpected")
        else:
            record.setdefault("download_status", "pass")
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))

    manifest = {
        "schema_version": "pilot-2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "small public-source pilot; no full dataset archive",
        "reproducibility": {
            "default_mode": "reuse_existing_and_verify",
            "refresh_flag": "--refresh",
            "offline_flag": "--offline",
            "hashes": ["sha256", "sm3"],
            "license_policy": "retain per-item evidence; license_status=review is not approval",
        },
        "records": records,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "total": len(records),
        "download_pass": sum(r.get("download_status") == "pass" for r in records),
        "downloaded": sum(r.get("retrieval_mode") in {"network", "network_refresh"} for r in records),
        "reused_existing": sum(r.get("retrieval_mode") == "existing_local" for r in records),
        "failed": sum(r.get("download_status") == "fail" for r in records),
        "hash_mismatch": sum(r.get("error_type") == "HashMismatch" for r in records),
        "decode_pass": sum(r.get("decode_status") == "pass" for r in records),
        "license_review": sum(r.get("license_status") == "review" for r in records),
        "output": str(OUT.relative_to(ROOT)).replace(os.sep, "/"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["download_pass"] == summary["total"] and summary["decode_pass"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
