"""统一下载并核验本项目选定的数据集资源。

默认行为：

* 已存在且通过大小/哈希/ZIP 探测的文件直接跳过，不重复下载；
* 缺失文件下载到旁路临时文件，下载完成并核验后再就位；
* 目标文件小于固定大小时支持断点续传；
* ``--status`` 和 ``--dry-run`` 完全离线，不会访问网络；
* 每次运行都把结果写入 ``data/dataset-downloads-20261006/manifest.json``。

GenImage 和 arXiv 在本项目中保留的是官方示例/论文样本；它们没有一个可
固定大小、可一次性下载的官方全集，因此脚本会明确报告 ``full_unavailable``，
不会伪装成全集已经下载。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PILOT_ROOT = ROOT / "data" / "external-pilot-20261006"
FULL_ROOT = Path(os.environ.get("GM_PROVENANCE_FULL_DATASETS", r"D:\gm-provenance-datasets\full-20261006\archives"))
MANIFEST = ROOT / "data" / "dataset-downloads-20261006" / "manifest.json"


PILOT_ITEMS = [
    {
        "dataset": "COCO",
        "id": "coco-val2017-000000000139",
        "url": "https://images.cocodataset.org/val2017/000000000139.jpg",
        "relative_path": "coco/000000000139.jpg",
        "bytes": 161811,
        "sha256": "ffe0f0cec3b2e27aab1967229cdf0a0d7751dcdd5800322f0b8ac0dffb3b8a8d",
        "media_type": "image/jpeg",
    },
    {
        "dataset": "COCO",
        "id": "coco-val2017-000000000285",
        "url": "https://images.cocodataset.org/val2017/000000000285.jpg",
        "relative_path": "coco/000000000285.jpg",
        "bytes": 335861,
        "sha256": "f3a2974ce3686332609124c70e3e6a2e3aca43fccf1cd1bd7c5c03820977f57d",
        "media_type": "image/jpeg",
    },
    {
        "dataset": "COCO",
        "id": "coco-val2017-000000000632",
        "url": "https://images.cocodataset.org/val2017/000000000632.jpg",
        "relative_path": "coco/000000000632.jpg",
        "bytes": 155667,
        "sha256": "a4cd7f45ac1ce27eaafb254b23af7c0b18a064be08870ceaaf03b2147f2ce550",
        "media_type": "image/jpeg",
    },
    {
        "dataset": "GenImage",
        "id": "genimage-official-visualization-example",
        "url": "https://raw.githubusercontent.com/GenImage-Dataset/GenImage/main/Examples/visulization.png",
        "relative_path": "genimage/official-visualization-example.png",
        "bytes": 3689831,
        "sha256": "7a1f82fb4dec0bda7bc8cf5c17377a2526bce5ee3f82cf2acb93602acde01f98",
        "media_type": "image/png",
    },
    {
        "dataset": "DocLayNet",
        "id": "doclaynet-official-example-132a855e",
        "url": "https://raw.githubusercontent.com/DS4SD/DocLayNet/main/assets/132a855ee8b23533d8ae69af0049c038171a06ddfcac892c3c6d7e6b4091c642.png",
        "relative_path": "doclaynet/official-example-page.png",
        "bytes": 458972,
        "sha256": "70415854a072c031031037cddc729eb966ea29df296ba98b505aff01ef2bd033",
        "media_type": "image/png",
    },
    {
        "dataset": "arXiv",
        "id": "arxiv-2206.01062v1",
        "url": "https://arxiv.org/pdf/2206.01062v1",
        "relative_path": "arxiv/doclaynet-paper-2206.01062v1.pdf",
        "bytes": 4310680,
        "sha256": "5dfbd8c115a15fd3396b68409124cfee29fc8efac7b5c846634ff924e635e0dc",
        "media_type": "application/pdf",
    },
]

ARCHIVES = [
    {
        "dataset": "COCO",
        "name": "train2017.zip",
        "url": "https://images.cocodataset.org/zips/train2017.zip",
        "bytes": 19336861798,
    },
    {
        "dataset": "COCO",
        "name": "val2017.zip",
        "url": "https://images.cocodataset.org/zips/val2017.zip",
        "bytes": 815585330,
    },
    {
        "dataset": "COCO",
        "name": "annotations_trainval2017.zip",
        "url": "https://images.cocodataset.org/annotations/annotations_trainval2017.zip",
        "bytes": 252907541,
    },
    {
        "dataset": "DocLayNet",
        "name": "DocLayNet_core.zip",
        "url": "https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/DocLayNet_core.zip",
        "bytes": 30012083650,
    },
    {
        "dataset": "DocLayNet",
        "name": "DocLayNet_extra.zip",
        "url": "https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/DocLayNet_extra.zip",
        "bytes": 8008878198,
    },
]

FULL_UNAVAILABLE = {
    "GenImage": "官方全集是多镜像/超大规模资源，没有本项目可固定大小的一键归档。",
    "arXiv": "arXiv 是开放集合，不是一个固定大小且统一许可证的单一归档。",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_signature_ok(path: Path, media_type: str) -> bool:
    with path.open("rb") as handle:
        head = handle.read(16)
    if media_type == "image/jpeg":
        return head.startswith(b"\xff\xd8")
    if media_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "application/pdf":
        return head.startswith(b"%PDF-")
    return False


def inspect_pilot(item: dict, path: Path) -> dict:
    result = {
        "kind": "pilot",
        "dataset": item["dataset"],
        "id": item["id"],
        "path": str(path),
        "expected_bytes": item["bytes"],
        "expected_sha256": item["sha256"],
        "source_url": item["url"],
    }
    if not path.exists():
        result.update({"status": "missing", "action": "download"})
        return result
    result["actual_bytes"] = path.stat().st_size
    if result["actual_bytes"] != item["bytes"]:
        result.update({"status": "size_mismatch", "action": "repair_with_refresh"})
        return result
    result["sha256"] = sha256_file(path)
    result["signature_ok"] = file_signature_ok(path, item["media_type"])
    if result["sha256"] == item["sha256"] and result["signature_ok"]:
        result.update({"status": "existing_verified", "action": "skip"})
    else:
        result.update({"status": "hash_or_signature_mismatch", "action": "repair_with_refresh"})
    return result


def inspect_zip(path: Path) -> dict:
    result = {"zip_open": False, "member_count": 0, "sample_read": False}
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            result["zip_open"] = True
            result["member_count"] = len(members)
            sample = next((member for member in members if not member.is_dir()), None)
            if sample is not None:
                with archive.open(sample) as handle:
                    handle.read(64 * 1024)
                result["sample_read"] = True
    except (OSError, zipfile.BadZipFile) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def inspect_archive(entry: dict, path: Path) -> dict:
    result = {
        "kind": "archive",
        "dataset": entry["dataset"],
        "name": entry["name"],
        "path": str(path),
        "expected_bytes": entry["bytes"],
        "source_url": entry["url"],
    }
    parts_dir = path.parent / f"{path.name}.parts"
    if not path.exists():
        if parts_dir.exists():
            result.update({"status": "partial_parts", "action": "resume"})
        else:
            result.update({"status": "missing", "action": "download"})
        return result
    result["actual_bytes"] = path.stat().st_size
    if result["actual_bytes"] != entry["bytes"]:
        result["status"] = "partial" if result["actual_bytes"] < entry["bytes"] else "size_conflict"
        result["action"] = "resume" if result["status"] == "partial" else "manual_review"
        return result
    result["zip_check"] = inspect_zip(path)
    if result["zip_check"].get("zip_open") and result["zip_check"].get("sample_read"):
        result.update({"status": "existing_verified", "action": "skip"})
    else:
        result.update({"status": "invalid_archive", "action": "manual_review"})
    return result


def infer_chunk_mib(parts_dir: Path, fallback: int) -> int:
    """Infer the existing Range chunk size so old partial downloads are reused."""
    sizes = [
        part.stat().st_size
        for part in parts_dir.glob("part-*" )
        if part.is_file() and not part.name.endswith(".partial") and part.stat().st_size > 0
    ]
    if not sizes:
        return fallback
    common_size, _ = Counter(sizes).most_common(1)[0]
    mib = common_size // (1024 * 1024)
    return mib if mib >= 1 and common_size % (1024 * 1024) == 0 else fallback


def curl_download(url: str, destination: Path, *, resume: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "curl.exe",
        "-k",
        "--fail",
        "--location",
        "--retry",
        "5",
        "--retry-delay",
        "5",
        "--connect-timeout",
        "30",
        "--max-time",
        "0",
    ]
    if resume:
        command.extend(["--continue-at", "-"])
    command.extend(["--output", str(destination), url])
    completed = subprocess.run(command, capture_output=True, text=True, shell=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "curl failed")[-2000:]
        raise RuntimeError(detail)


def download_pilot(item: dict, current: dict, *, refresh: bool, dry_run: bool) -> dict:
    if current["status"] == "existing_verified" and not refresh:
        return current
    if dry_run:
        current["action"] = "would_refresh" if refresh else current.get("action", "would_download")
        return current
    target = PILOT_ROOT / item["relative_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not refresh:
        current["status"] = "repair_required"
        current["error"] = "已有文件未通过固定哈希；为避免覆盖原文件，请显式使用 --refresh。"
        return current
    suffix = ".refreshing" if refresh else ".part"
    staging = target.with_name(target.name + suffix)
    started = time.monotonic()
    try:
        curl_download(item["url"], staging, resume=staging.exists() and not refresh)
        checked = inspect_pilot(item, staging)
        if checked["status"] != "existing_verified":
            checked["status"] = "download_failed_validation"
            checked["action"] = "retry"
            return checked
        if target.exists() and refresh:
            backup = target.with_name(target.name + f".previous-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            os.replace(target, backup)
            checked["previous_backup"] = str(backup)
        os.replace(staging, target)
        checked = inspect_pilot(item, target)
        checked.update({"status": "downloaded_verified", "action": "done"})
        checked["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return checked
    except (OSError, RuntimeError) as exc:
        current.update({"status": "download_failed", "action": "retry", "error": str(exc)})
        return current


def _download_range(url: str, start: int, end: int, part: Path) -> None:
    expected = end - start + 1
    if part.exists() and part.stat().st_size == expected:
        return
    # Never delete a pre-existing partial chunk.  A new attempt gets its own
    # temporary name and replaces only the completed target chunk.
    temp = part.with_name(part.name + f".download-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    command = [
        "curl.exe", "-k", "--fail", "--location", "--retry", "5", "--retry-delay", "3",
        "--connect-timeout", "30", "--max-time", "0", "--range", f"{start}-{end}",
        "--output", str(temp), url,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, shell=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "curl range failed")[-2000:])
    if temp.stat().st_size != expected:
        raise RuntimeError(f"range {start}-{end} size={temp.stat().st_size}, expected={expected}")
    if part.exists():
        backup = part.with_name(part.name + f".previous-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        os.replace(part, backup)
    os.replace(temp, part)


def download_archive_parallel(entry: dict, target: Path, workers: int, chunk_mib: int) -> None:
    total = entry["bytes"]
    chunk = max(1, chunk_mib) * 1024 * 1024
    parts_dir = target.parent / f"{target.name}.parts"
    ranges = []
    start = 0
    index = 0
    while start < total:
        end = min(total - 1, start + chunk - 1)
        ranges.append((index, start, end, parts_dir / f"part-{index:06d}"))
        start, index = end + 1, index + 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_range, entry["url"], begin, end, part) for _, begin, end, part in ranges]
        for future in as_completed(futures):
            future.result()
    assembling = target.with_suffix(target.suffix + ".assembling")
    assembling.unlink(missing_ok=True)
    with assembling.open("wb") as output:
        for _, _, _, part in ranges:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
    if assembling.stat().st_size != total:
        raise RuntimeError(f"assembled size={assembling.stat().st_size}, expected={total}")
    if target.exists():
        backup = target.with_name(target.name + f".previous-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        os.replace(target, backup)
    os.replace(assembling, target)
    # Keep the part directory as a recoverable local cache.  It is harmless
    # after the final archive exists and avoids deleting user/download history.


def download_archive(entry: dict, current: dict, *, refresh: bool, dry_run: bool, workers: int, chunk_mib: int) -> dict:
    if current["status"] == "existing_verified" and not refresh:
        return current
    if dry_run:
        current["action"] = "would_refresh" if refresh else current.get("action", "would_download")
        return current
    target = FULL_ROOT / entry["name"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if current["status"] in {"size_conflict", "invalid_archive"} and not refresh:
        current["status"] = "manual_review"
        current["error"] = "已存在同名文件但无法安全确认完整；显式使用 --refresh 才会另存并替换。"
        return current
    started = time.monotonic()
    try:
        if refresh:
            staging = target.with_name(target.name + ".refreshing")
            staging.unlink(missing_ok=True)
            if workers > 1:
                download_archive_parallel(entry, staging, workers, chunk_mib)
            else:
                curl_download(entry["url"], staging, resume=False)
            candidate = staging
        elif target.exists():
            candidate = target
            parts_dir = target.parent / f"{target.name}.parts"
            if parts_dir.exists():
                download_archive_parallel(entry, target, max(1, workers), infer_chunk_mib(parts_dir, chunk_mib))
            elif workers == 1 or target.stat().st_size < entry["bytes"]:
                curl_download(entry["url"], target, resume=True)
        else:
            parts_dir = target.parent / f"{target.name}.parts"
            if parts_dir.exists():
                candidate = target
                download_archive_parallel(entry, candidate, max(1, workers), infer_chunk_mib(parts_dir, chunk_mib))
            elif workers > 1:
                candidate = target
                download_archive_parallel(entry, candidate, workers, chunk_mib)
            else:
                candidate = target.with_name(target.name + ".part")
                curl_download(entry["url"], candidate, resume=candidate.exists())
        checked = inspect_archive(entry, candidate)
        if checked["status"] not in {"existing_verified"}:
            checked.update({"status": "download_failed_validation", "action": "retry"})
            return checked
        if candidate != target:
            if target.exists() and refresh:
                backup = target.with_name(target.name + f".previous-{datetime.now().strftime('%Y%m%d%H%M%S')}")
                os.replace(target, backup)
                checked["previous_backup"] = str(backup)
            os.replace(candidate, target)
        checked = inspect_archive(entry, target)
        checked.update({"status": "downloaded_verified", "action": "done"})
        checked["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return checked
    except (OSError, RuntimeError) as exc:
        current.update({"status": "download_failed", "action": "retry", "error": str(exc)})
        return current


def unavailable_record(dataset: str) -> dict:
    return {
        "kind": "full_unavailable",
        "dataset": dataset,
        "status": "full_unavailable",
        "action": "manual_selection_required",
        "reason": FULL_UNAVAILABLE[dataset],
    }


def selected(dataset: str) -> tuple[list[dict], list[dict]]:
    if dataset == "all":
        return list(PILOT_ITEMS), list(ARCHIVES)
    return [item for item in PILOT_ITEMS if item["dataset"] == dataset], [entry for entry in ARCHIVES if entry["dataset"] == dataset]


def write_manifest(records: list[dict], *, args: argparse.Namespace) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "download-all-1",
        "generated_at": utc_now(),
        "pilot_root": str(PILOT_ROOT),
        "full_root": str(FULL_ROOT),
        "network_enabled": not (args.status or args.dry_run),
        "records": records,
        "summary": {
            "existing_verified": sum(r.get("status") == "existing_verified" for r in records),
            "downloaded_verified": sum(r.get("status") == "downloaded_verified" for r in records),
            "missing": sum(r.get("status") == "missing" for r in records),
            "partial": sum(r.get("status") in {"partial", "partial_parts"} for r in records),
            "failed": sum(r.get("status") in {"download_failed", "download_failed_validation", "repair_required", "manual_review", "hash_or_signature_mismatch", "size_mismatch", "invalid_archive", "size_conflict"} for r in records),
            "full_unavailable": sum(r.get("status") == "full_unavailable" for r in records),
        },
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    global PILOT_ROOT, FULL_ROOT, MANIFEST
    parser = argparse.ArgumentParser(description="下载并检查本项目全部已选数据集资源，默认跳过已完整验证的文件。")
    parser.add_argument("--dataset", choices=["COCO", "GenImage", "DocLayNet", "arXiv", "all"], default="all")
    parser.add_argument("--status", action="store_true", help="只检查本地状态，不联网、不下载")
    parser.add_argument("--dry-run", action="store_true", help="显示会执行的动作，不联网、不下载")
    parser.add_argument("--offline", action="store_true", help="--status 的别名，完全离线检查")
    parser.add_argument("--refresh", action="store_true", help="显式重新下载已验证文件，并保留旧文件备份")
    parser.add_argument("--pilot-root", type=Path, default=PILOT_ROOT, help="pilot 文件目录")
    parser.add_argument("--full-root", type=Path, default=FULL_ROOT, help="完整归档目录")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help="统一状态清单路径")
    parser.add_argument("--workers", type=int, default=1, help="大归档的 Range 并行数；默认 1，避免额外磁盘占用")
    parser.add_argument("--chunk-mib", type=int, default=16, help="并行下载分块大小，默认 16 MiB")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.chunk_mib < 1:
        parser.error("--workers 和 --chunk-mib 必须为正整数")
    if args.offline:
        args.status = True
    if args.status:
        args.dry_run = True
        args.refresh = False

    PILOT_ROOT = args.pilot_root
    FULL_ROOT = args.full_root
    MANIFEST = args.manifest

    pilot_items, archives = selected(args.dataset)
    records: list[dict] = []
    for item in pilot_items:
        current = inspect_pilot(item, PILOT_ROOT / item["relative_path"])
        record = download_pilot(item, current, refresh=args.refresh, dry_run=args.dry_run)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
    for entry in archives:
        current = inspect_archive(entry, FULL_ROOT / entry["name"])
        record = download_archive(entry, current, refresh=args.refresh, dry_run=args.dry_run, workers=args.workers, chunk_mib=args.chunk_mib)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
    unavailable_datasets = list(FULL_UNAVAILABLE) if args.dataset == "all" else ([args.dataset] if args.dataset in FULL_UNAVAILABLE else [])
    if unavailable_datasets:
        records.extend(unavailable_record(dataset) for dataset in unavailable_datasets)
        for record in records[-len(unavailable_datasets):]:
            print(json.dumps(record, ensure_ascii=False))
    write_manifest(records, args=args)
    summary = json.loads(MANIFEST.read_text(encoding="utf-8"))["summary"]
    summary["manifest"] = str(MANIFEST)
    print(json.dumps(summary, ensure_ascii=False))
    failed = summary["failed"] > 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
