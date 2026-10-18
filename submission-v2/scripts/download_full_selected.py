"""Resumably download the fixed, finite COCO/DocLayNet archives.

This script deliberately excludes the open-ended arXiv corpus and the
multi-hundred-GB GenImage mirrors. It downloads only the five archives whose
official URLs and sizes can be pinned, keeps them outside the source tree by
default, and writes a manifest in the project for later tests.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(r"D:\gm-provenance-datasets\full-20261006\archives")
MANIFEST = ROOT / "data" / "external-full-20261006" / "manifest.json"

ARCHIVES = [
    {
        "dataset": "COCO",
        "name": "train2017.zip",
        "url": "https://images.cocodataset.org/zips/train2017.zip",
        "expected_bytes": 19336861798,
        "license_url": "https://cocodataset.org/#termsofuse",
    },
    {
        "dataset": "COCO",
        "name": "val2017.zip",
        "url": "https://images.cocodataset.org/zips/val2017.zip",
        "expected_bytes": 815585330,
        "license_url": "https://cocodataset.org/#termsofuse",
    },
    {
        "dataset": "COCO",
        "name": "annotations_trainval2017.zip",
        "url": "https://images.cocodataset.org/annotations/annotations_trainval2017.zip",
        "expected_bytes": 252907541,
        "license_url": "https://cocodataset.org/#termsofuse",
    },
    {
        "dataset": "DocLayNet",
        "name": "DocLayNet_core.zip",
        "url": "https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/DocLayNet_core.zip",
        "expected_bytes": 30012083650,
        "license_url": "https://github.com/DS4SD/DocLayNet",
    },
    {
        "dataset": "DocLayNet",
        "name": "DocLayNet_extra.zip",
        "url": "https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/DocLayNet_extra.zip",
        "expected_bytes": 8008878198,
        "license_url": "https://github.com/DS4SD/DocLayNet",
    },
]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_zip(path: Path) -> dict:
    result: dict = {"zip_open": False, "member_count": None, "sample_members": [], "sample_read": False}
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        result["zip_open"] = True
        result["member_count"] = len(members)
        result["sample_members"] = [member.filename for member in members[:10]]
        sample = next((member for member in members if not member.is_dir()), None)
        if sample is not None:
            with archive.open(sample) as handle:
                handle.read(64 * 1024)
            result["sample_read"] = True
    return result


def _download_range(url: str, start: int, end: int, part: Path) -> None:
    part.parent.mkdir(parents=True, exist_ok=True)
    expected = end - start + 1
    if part.exists() and part.stat().st_size == expected:
        return
    temp = part.with_suffix(part.suffix + ".partial")
    temp.unlink(missing_ok=True)
    command = [
        "curl.exe", "-k", "--fail", "--location", "--retry", "5", "--retry-delay", "3",
        "--connect-timeout", "30", "--max-time", "0", "--range", f"{start}-{end}",
        "--output", str(temp), url,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, shell=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1000:])
    if temp.stat().st_size != expected:
        raise RuntimeError(f"range {start}-{end} size={temp.stat().st_size}, expected={expected}")
    os.replace(temp, part)


def _download_parallel(entry: dict, path: Path, workers: int, chunk_mib: int) -> None:
    total = entry["expected_bytes"]
    chunk = max(1, chunk_mib) * 1024 * 1024
    parts_dir = path.parent / (path.name + ".parts")
    ranges = []
    start = 0
    index = 0
    while start < total:
        end = min(total - 1, start + chunk - 1)
        ranges.append((index, start, end, parts_dir / f"part-{index:06d}"))
        start, index = end + 1, index + 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_range, entry["url"], start, end, part) for _, start, end, part in ranges]
        for future in as_completed(futures):
            future.result()
    assembled = path.with_suffix(path.suffix + ".assembling")
    assembled.unlink(missing_ok=True)
    with assembled.open("wb") as output:
        for _, start, end, part in ranges:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
    if assembled.stat().st_size != total:
        raise RuntimeError(f"assembled size={assembled.stat().st_size}, expected={total}")
    os.replace(assembled, path)
    for _, _, _, part in ranges:
        part.unlink(missing_ok=True)
    try:
        parts_dir.rmdir()
    except OSError:
        pass


def download(entry: dict, output: Path, refresh: bool, workers: int, chunk_mib: int) -> dict:
    path = output / entry["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(entry)
    record.update({
        "path": str(path),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tls_verification": "disabled_for_environment_fallback",
        "transport_note": "curl uses -k because the current environment proxy has a certificate hostname mismatch; verify archives independently before redistribution.",
    })
    local_size = path.stat().st_size if path.exists() else 0
    if path.exists() and not refresh and local_size == entry["expected_bytes"]:
        record["retrieval_mode"] = "existing_local"
    else:
        started = time.monotonic()
        try:
            if workers > 1:
                _download_parallel(entry, path, workers, chunk_mib)
            else:
                command = [
                    "curl.exe", "-k", "--fail", "--location", "--retry", "5", "--retry-delay", "5",
                    "--connect-timeout", "30", "--continue-at", "-", "--output", str(path), entry["url"],
                ]
                completed = subprocess.run(command, capture_output=True, text=True, shell=False)
                if completed.returncode != 0:
                    record.update({"status": "FAIL", "retrieval_mode": "network", "error": completed.stderr[-2000:]})
                    return record
        except Exception as exc:
            record.update({"status": "FAIL", "retrieval_mode": "network_parallel" if workers > 1 else "network", "error": str(exc)})
            return record
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        record["retrieval_mode"] = ("network_parallel_refresh" if refresh else "network_parallel_resume" if local_size else "network_parallel") if workers > 1 else ("network_refresh" if refresh else ("network_resume" if local_size else "network"))
    record["actual_bytes"] = path.stat().st_size
    record["size_match"] = record["actual_bytes"] == entry["expected_bytes"]
    if not record["size_match"]:
        record.update({"status": "FAIL", "error": "downloaded size differs from pinned response-header size"})
        return record
    record["sha256"] = hash_file(path)
    try:
        record["zip_check"] = inspect_zip(path)
        record["status"] = "PASS" if record["zip_check"].get("sample_read") else "FAIL"
    except (OSError, zipfile.BadZipFile) as exc:
        record.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download fixed COCO and DocLayNet archives with resume support.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dataset", choices=["COCO", "DocLayNet", "all"], default="all")
    parser.add_argument("--name", help="download one archive by its exact filename")
    parser.add_argument("--workers", type=int, default=1, help="parallel range workers; 1 uses curl resume directly")
    parser.add_argument("--chunk-mib", type=int, default=16, help="range size for parallel workers")
    parser.add_argument("--refresh", action="store_true", help="re-request archives even if local files exist")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    entries = [entry for entry in ARCHIVES if args.dataset == "all" or entry["dataset"] == args.dataset]
    if args.name:
        entries = [entry for entry in entries if entry["name"] == args.name]
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for entry in entries:
        record = download(entry, args.output, args.refresh, args.workers, args.chunk_mib)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
        if record.get("status") != "PASS":
            break
    manifest = {
        "schema_version": "full-selected-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "COCO 2017 train/val + annotations and DocLayNet core/extra",
        "note": "This is not a complete GenImage or arXiv corpus; those sources have no finite practical archive in this project run.",
        "records": records,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(record.get("status") == "PASS" for record in records)
    print(json.dumps({"status": "PASS" if passed == len(entries) else "FAIL", "passed": passed, "total": len(entries), "manifest": str(MANIFEST)}, ensure_ascii=False))
    return 0 if passed == len(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
