"""Audit artifacts left by Round9 publish-gate failures.

This is a read-only audit of the isolated benchmark directories.  It does not
delete, repair, publish, sign, or mutate any registry.  The purpose is to
separate the absence of a final published media file from pre-gate evaluation
artifacts such as a signed manifest or an isolated registry row.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def group_for_body(body: dict) -> str:
    profile = (body.get("watermark_profile") or {}).get("algorithm")
    metadata_profile = ((body.get("metadata") or {}).get("watermark_media_profile") or {}).get("algorithm")
    if profile == "none" or metadata_profile == "none":
        return "G1"
    if profile in {"dct-difference-v1", "watermark-v1", "watermark-v2-residual", "watermark-v2-feedback"}:
        return "G3"
    return "unknown"


def audit_run(run_dir: Path) -> dict:
    failures = load_jsonl(run_dir / "failures.jsonl") if (run_dir / "failures.jsonl").exists() else []
    sample_root = run_dir / "samples"
    rows: list[dict] = []
    for failure in failures:
        sample_id = str(failure["sample_id"])
        sample_dir = sample_root / sample_id
        manifest_files = sorted(sample_dir.rglob("manifests/*.json"))
        manifest_profiles: list[str] = []
        manifest_versions: list[int] = []
        manifest_groups: list[str] = []
        for manifest_path in manifest_files:
            body = json.loads(manifest_path.read_text(encoding="utf-8")).get("body", {})
            profile = (body.get("watermark_profile") or {}).get("algorithm")
            if profile is not None:
                manifest_profiles.append(str(profile))
            manifest_groups.append(group_for_body(body))
            if body.get("version") is not None:
                manifest_versions.append(int(body["version"]))

        registry_dbs = sorted(sample_dir.rglob("registry.sqlite"))
        db_rows: list[dict] = []
        for db_path in registry_dbs:
            connection = sqlite3.connect(db_path)
            try:
                counts = {}
                for table in ("contents", "assets", "revocations"):
                    counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                content_rows = connection.execute("SELECT version, manifest_json FROM contents").fetchall()
                versions = [int(row[0]) for row in content_rows]
                content_groups = Counter(
                    group_for_body(json.loads(row[1]).get("body", {})) for row in content_rows
                )
            finally:
                connection.close()
            db_rows.append({
                "path": str(db_path.relative_to(run_dir)).replace("\\", "/"),
                "counts": counts,
                "content_versions": versions,
                "content_group_counts": dict(content_groups),
            })

        final_media = sorted(
            path for path in sample_dir.rglob("*")
            if path.is_file() and ("G3-" in path.name or path.name.endswith("-final.bin"))
        )
        state_snapshot_files = sorted(
            path for path in sample_dir.rglob("*")
            if path.is_file() and "state" in path.name.lower() and path.suffix.lower() in {".json", ".jsonl"}
        )
        rows.append({
            "sample_id": sample_id,
            "stage": failure.get("stage"),
            "error_class": str(failure.get("error", "")).split(":", 1)[0],
            "sample_dir_exists": sample_dir.is_dir(),
            "source_file_count": int((sample_dir / "source.bin").exists()),
            "signed_manifest_count": len(manifest_files),
            "manifest_watermark_profiles": sorted(set(manifest_profiles)),
            "manifest_group_counts": dict(Counter(manifest_groups)),
            "manifest_versions": sorted(set(manifest_versions)),
            "registry_databases": db_rows,
            "final_published_media_count": len(final_media),
            "state_snapshot_count": len(state_snapshot_files),
        })

    return {
        "schema_version": "round9-gate-failure-artifact-audit-2",
        "run": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
        "failure_count": len(rows),
        "rows": rows,
        "aggregate": {
            "signed_manifest_files": sum(row["signed_manifest_count"] for row in rows),
            "registry_database_files": sum(len(row["registry_databases"]) for row in rows),
            "registry_content_rows": sum(
                db["counts"]["contents"] for row in rows for db in row["registry_databases"]
            ),
            "registry_asset_rows": sum(
                db["counts"]["assets"] for row in rows for db in row["registry_databases"]
            ),
            "registry_content_group_counts": dict(Counter(
                group
                for row in rows
                for db in row["registry_databases"]
                for group, count in db["content_group_counts"].items()
                for _ in range(count)
            )),
            "registry_revocation_rows": sum(
                db["counts"]["revocations"] for row in rows for db in row["registry_databases"]
            ),
            "final_published_media_files": sum(row["final_published_media_count"] for row in rows),
            "state_snapshot_files": sum(row["state_snapshot_count"] for row in rows),
        },
        "interpretation": (
            "Classify an artifact by its signed body, not its directory name. In the historical "
            "run, watermark_profile=none denotes the legitimate G1 signed-only control; a G3 "
            "publish failure must have no G3 signed body or G3 Registry contents/assets."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit artifacts left by Round9 gate failures")
    parser.add_argument(
        "--run",
        default="results/pilot-round3/20261014T-round9-default-images-800",
        help="isolated Round9 run directory",
    )
    parser.add_argument(
        "--output",
        default="results/round9-correction-20261014-v2/gate_failure_artifact_audit.json",
    )
    args = parser.parse_args()
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    result = audit_run(run_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
