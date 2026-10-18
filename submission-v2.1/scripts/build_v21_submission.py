"""Build the v2.1 release-candidate directory and audited archive."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission-v2.1"


def _copy_tree(name: str) -> None:
    source = ROOT / name
    target = OUT / name
    if source.exists():
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.sqlite", "*.db", "private", "results", "*.tmp"),
        )


def _purpose(path: str) -> str:
    if path.endswith((".docx", ".pdf", ".md")) and "report" in path:
        return "v2.1 设计与验收报告"
    if path.startswith("src/"):
        return "v1/v2/v2.1 源代码"
    if path.startswith("tests/"):
        return "v1/v2/v2.1 测试"
    if path.startswith("logs/"):
        return "v2/v2.1 可审计日志"
    if path.startswith("tables/"):
        return "v2.1 指标表"
    if path.startswith("figures/"):
        return "v2.1 图表"
    if path.startswith("scripts/"):
        return "v2/v2.1 复现脚本"
    if path.startswith("configs/"):
        return "v2/v2.1 配置"
    if path.startswith("sample_data/"):
        return "离线固定样本"
    return "说明文件"


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for name in ("src", "tests", "docs", "configs", "scripts", "logs", "tables", "figures", "sample_data"):
        _copy_tree(name)
    for name in ("README.md", "pyproject.toml", "requirements.txt", "AGENTS.md", "CONTRIBUTING.md"):
        shutil.copy2(ROOT / name, OUT / name)
    shutil.copy2(ROOT / "docs/V2_DESIGN_REPORT.md", OUT / "design_report_v2_1.md")
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from scripts.build_v2_reports import build_pdf, build_docx
        build_pdf(OUT / "design_report_v2_1.md", OUT / "design_report_v2_1.pdf")
        build_docx(OUT / "design_report_v2_1.md", OUT / "design_report_v2_1.docx")
    except Exception as exc:
        (OUT / "logs/report-render-status.txt").write_text(
            f"DOCX/PDF conversion unavailable: {type(exc).__name__}: {exc}\n", encoding="utf-8"
        )

    files = []
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.name in {"SUBMISSION_MANIFEST.txt", "file_manifest.sha256.json"}:
            continue
        rel = path.relative_to(OUT).as_posix()
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "purpose": _purpose(rel)})
    machine = {
        "schema_version": 1,
        "scope": "v2.1-submission",
        "excluded": [".venv", "cache", "private keys", "runtime databases", "results", "temporary files"],
        "files": files,
    }
    (OUT / "file_manifest.sha256.json").write_text(json.dumps(machine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["gm-provenance v2.1 发布候选文件清单", "生成时间：2026-10-18", "说明：本清单不包含私钥、令牌、运行数据库和缓存。", ""]
    lines.extend(f"{x['path']}\t{x['bytes']}\t{x['sha256']}\t{x['purpose']}" for x in files)
    (OUT / "SUBMISSION_MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    archive = ROOT / "gm-provenance-v2.1-submission-20261018.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(OUT).as_posix())
    print(json.dumps({"directory": str(OUT), "archive": str(archive), "files": len(files)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
