"""Validate externally supplied media without downloading or publishing it.

The importer is deliberately a local boundary.  It reads a JSON/JSONL manifest
next to an incoming directory, validates the bytes with the project's media
adapter, records SHA-256/SM3 and checks duplicate hashes against existing
manifests.  It never signs, registers, changes a Registry, or contacts a
network service.  Missing source/licence evidence remains ``pending``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gm_provenance import media  # noqa: E402
from gm_provenance.crypto import OpenSSLCrypto  # noqa: E402


DEFAULT_INCOMING = ROOT / "data" / "incoming-materials"
DEFAULT_OUT = ROOT / "results" / "round9-correction-20261014" / "local-import"
_HASH_MANIFEST_NAMES = {
    "manifest.json",
    "manifest.jsonl",
    "images_manifest.jsonl",
    "pdfs_manifest.jsonl",
}


def _json_rows(path: Path) -> Iterable[dict[str, Any]]:
    """Yield records from the two manifest formats used in this repository."""
    try:
        if path.suffix.lower() == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        yield row
            return
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if isinstance(loaded, dict) and isinstance(loaded.get("records"), list):
        for row in loaded["records"]:
            if isinstance(row, dict):
                yield row
    elif isinstance(loaded, list):
        for row in loaded:
            if isinstance(row, dict):
                yield row


def _manifest_files(existing_root: Path, explicit: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in explicit:
        files.add(path.resolve())
    if existing_root.exists():
        for path in existing_root.rglob("*"):
            if not path.is_file() or path.parent.name == "__pycache__":
                continue
            if "results" in path.parts:
                continue
            if path.name.lower() in _HASH_MANIFEST_NAMES:
                files.add(path.resolve())
    return sorted(files)


def _known_hashes(paths: Iterable[Path]) -> dict[str, list[str]]:
    known: dict[str, list[str]] = {}
    for path in paths:
        for row in _json_rows(path):
            for key in ("sha256", "expected_sha256"):
                value = row.get(key)
                if isinstance(value, str) and len(value) == 64:
                    known.setdefault(value.lower(), []).append(str(path))
    return known


def _resolve_input(manifest_dir: Path, row: dict[str, Any]) -> Path:
    value = row.get("local_path") or row.get("relative_path") or row.get("filename")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("缺少 local_path、relative_path 或 filename")
    candidate = Path(value)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        # Existing project manifests use either a manifest-relative path or a
        # project-relative local_path.  Accept both while keeping the path
        # inside the repository or the incoming tree.
        options = [(manifest_dir / candidate).resolve(), (ROOT / candidate).resolve()]
        path = next((item for item in options if item.is_file()), options[0])
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    return path


def _source_status(row: dict[str, Any]) -> tuple[str, list[str]]:
    missing: list[str] = []
    if not (row.get("source_reference") or row.get("source_url") or row.get("source_record") or row.get("generation_record")):
        missing.append("source_reference")
    if not (row.get("license_reference") or row.get("license_evidence_url") or row.get("usage_basis") or row.get("license")):
        missing.append("license_reference")
    license_status = str(row.get("license_status", "")).lower()
    if license_status not in {"approved", "verified", "review", "pending"}:
        missing.append("license_status")
    if missing or license_status not in {"approved", "verified"}:
        return "pending", missing
    return "review_ready", []


def _record(row: dict[str, Any], path: Path, known: dict[str, list[str]], seen: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sample_id": row.get("sample_id") or row.get("source_id") or path.stem,
        "filename": row.get("filename") or path.name,
        "source_id": row.get("source_id"),
        "dataset": row.get("dataset"),
        "source_reference": row.get("source_reference") or row.get("source_url"),
        "license_reference": row.get("license_reference") or row.get("license_evidence_url"),
        "license_status": row.get("license_status", "pending"),
        "usage_basis": row.get("usage_basis") or row.get("license"),
        "source_record": row.get("source_record") or row.get("generation_record") or row.get("note"),
        "source_date": row.get("source_date") or row.get("generated_at") or row.get("date"),
        "generator_tool": row.get("generator_tool") or row.get("tool"),
        "origin_category": row.get("origin_category") or row.get("dataset"),
        "source_family_id": row.get("source_family_id"),
        "generator_claim": row.get("generator_claim"),
        "used_for_tuning": bool(row.get("used_for_tuning", False)),
        "path": str(path.relative_to(ROOT)).replace(os.sep, "/") if path.is_relative_to(ROOT) else str(path),
    }
    data = path.read_bytes()
    result["bytes"] = len(data)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    result["sm3"] = OpenSSLCrypto().sm3(data)
    try:
        inspected = media.inspect_media(data)
        result["inspect"] = inspected
        result["media_type"] = inspected.get("media_type")
        result["input_compatibility"] = "supported"
    except Exception as exc:
        result.update({
            "input_compatibility": "invalid_or_unsupported",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
    source_status, missing = _source_status(row)
    result["source_status"] = source_status
    result["missing_evidence"] = missing
    result["source_family_status"] = "provided" if result["source_family_id"] else "pending"
    digest = result["sha256"]
    duplicate_paths = list(known.get(digest, []))
    if digest in seen:
        duplicate_paths.append(seen[digest])
    result["duplicate_status"] = "duplicate" if duplicate_paths else "new"
    result["duplicate_references"] = sorted(set(duplicate_paths))
    seen.setdefault(digest, str(path))
    result["import_status"] = "validated" if result.get("input_compatibility") == "supported" else "rejected"
    if result["source_status"] == "pending" or result["source_family_status"] == "pending":
        result["review_status"] = "pending"
    else:
        result["review_status"] = "ready_for_human_review"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线校验并登记外部图片/PDF素材，不下载、不签名、不入Registry")
    parser.add_argument("--incoming", type=Path, default=DEFAULT_INCOMING, help="包含素材和 manifest.json/jsonl 的目录")
    parser.add_argument("--manifest", type=Path, default=None, help="输入清单；默认在 incoming 下寻找 manifest.jsonl 或 manifest.json")
    parser.add_argument("--existing-root", type=Path, default=ROOT / "data", help="用于重复哈希检查的本地数据根目录")
    parser.add_argument("--existing-manifest", type=Path, action="append", default=[], help="额外的历史清单，可重复指定")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 JSON 报告目录")
    args = parser.parse_args(argv)
    incoming = args.incoming.resolve()
    if args.manifest:
        manifest_path = args.manifest.resolve()
    else:
        candidates = [incoming / "manifest.jsonl", incoming / "manifest.json"]
        manifest_path = next((p for p in candidates if p.exists()), candidates[0])
    if not manifest_path.is_file():
        print(f"输入清单不存在: {manifest_path}", file=sys.stderr)
        return 2
    rows = list(_json_rows(manifest_path))
    if not rows:
        print(f"输入清单没有可用 records: {manifest_path}", file=sys.stderr)
        return 2
    history = _manifest_files(args.existing_root.resolve(), [p.resolve() for p in args.existing_manifest])
    known = _known_hashes(history)
    seen: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        try:
            path = _resolve_input(manifest_path.parent, row)
            record = _record(row, path, known, seen)
        except Exception as exc:
            record = {
                "sample_id": row.get("sample_id") or row.get("source_id") or f"row-{index:04d}",
                "import_status": "rejected",
                "review_status": "pending",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "local-import-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network": "not_used",
        "manifest": str(manifest_path),
        "history_manifests": [str(p) for p in history],
        "total": len(records),
        "validated": sum(r.get("import_status") == "validated" for r in records),
        "rejected": sum(r.get("import_status") == "rejected" for r in records),
        "new_hashes": sum(r.get("duplicate_status") == "new" for r in records),
        "duplicates": sum(r.get("duplicate_status") == "duplicate" for r in records),
        "source_pending": sum(r.get("review_status") == "pending" for r in records),
        "source_families_missing": sum(r.get("source_family_status") == "pending" for r in records),
        "records": records,
        "side_effects": {"registry": False, "signatures": False, "network": False},
    }
    (args.out / "import_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total", "validated", "rejected", "duplicates", "source_pending", "network")}, ensure_ascii=False))
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
