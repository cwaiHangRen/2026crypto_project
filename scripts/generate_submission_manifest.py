"""Generate the auditable submission file manifest without including its own hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "submission"


def purpose(path: str) -> str:
    if path in {"design_report.docx", "design_report.pdf", "design_report_content.md"}:
        return "正式报告交付物"
    if path.startswith("approval_page_separate/"):
        return "人工审批签字占位"
    if path.startswith("figures/"):
        return "报告图件"
    if path.startswith("tables/"):
        return "结构化指标表"
    if path.startswith("logs/"):
        return "可审计运行日志"
    if path.startswith("sample_data/"):
        return "样本清单与 PDF 样本"
    if path.startswith("src/"):
        return "源代码"
    if path.startswith("tests/"):
        return "自动化测试"
    if path.startswith("scripts/"):
        return "复现与验收脚本"
    if path.startswith("configs/"):
        return "源代码/测试/配置/文档"
    if path.startswith("docs/"):
        return "源代码/测试/配置/文档"
    if path in {"README.md", "pyproject.toml", "requirements.txt", "AGENTS.md", "CONTRIBUTING.md"}:
        return "提交说明或依赖文件"
    return "提交文件"


def main() -> int:
    md = SUB / "SUBMISSION_MANIFEST.md"
    # Keep the human-facing manifest free of machine-specific paths and
    # signatures.  The generated SHA-256 table is the authoritative audit.
    md.write_text(
        "# 最终提交目录说明\n\n"
        "本目录包含匿名提交版报告、源码、测试、配置、脚本、1000 行样本清单、真实 PDF 页面图、结构化日志和审批签字页占位文件。\n\n"
        "- `design_report.docx`：可编辑中文报告。\n"
        "- `design_report.pdf`：由同一报告内容生成并已用 PDFium 逐页检查的 PDF；本机缺少 LibreOffice，DOCX 原生转换日志记录为阻塞。\n"
        "- `figures/`：总体架构、验证流程和真实 PDF 渲染/最终编码/确定性 JPEG 页面。\n"
        "- `tables/`：从 Round9 JSON 日志生成的图片/PDF 分组、变换、延迟、质量和存储指标。\n"
        "- `logs/`：匿名化 PDF 栅格化验收、Round9 汇总、六个失败样本诊断和报告构建状态；私钥、令牌、SQLite 和运行数据库不在此目录。\n"
        "- `sample_data/`：真实 PDF 第一页和 1000 行清单副本（800 图片、200 PDF），以及清单审计摘要。\n"
        "- `approval_page_separate/`：作品编号、日期和审批签字页占位。\n\n"
        "Round9 的 1000 行清单是协议规模数据，内部合成样本占多数；AI 可核验样本 0/20、外部真实 PDF 0/10。输入质量门支持不超过 16MP，超限输入拒绝且不生成发布字节。报告保留这些缺口与 PDF 栅格化丢失文字层的限制。\n\n"
        "验证状态：PDF 栅格化/截图定向回归 9/9 PASS；CLI、环境检查和 compileall PASS；历史 API 验收摘要 9/9 PASS。当前主机全量 pytest 与 API 现场复跑未完成，原因是部分测试/Starlette TestClient 在受限 Windows loopback 环境挂起，详见匿名化 logs/pytest-* 与 logs/api-acceptance-*。\n\n"
        "人工必填：作品编号、提交日期、审批签字；提交电脑需用 Word/LibreOffice 原生复核 DOCX。\n",
        encoding="utf-8",
    )
    files = []
    for path in sorted(SUB.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SUB).as_posix()
        if rel in {"SUBMISSION_MANIFEST.txt", "file_manifest.sha256.json"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": digest, "purpose": purpose(rel)})
    machine = {
        "schema_version": 1,
        "scope": "submission",
        "excluded": [".venv", "cache", "private keys", "runtime databases", "SUBMISSION_MANIFEST.txt", "file_manifest.sha256.json"],
        "files": files,
    }
    (SUB / "file_manifest.sha256.json").write_text(json.dumps(machine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "gm-provenance 最终提交文件清单",
        "生成时间：2026-10-18",
        "说明：SUBMISSION_MANIFEST.txt 与 file_manifest.sha256.json 自身不列入哈希；人工必填项见 SUBMISSION_MANIFEST.md。",
        "",
    ]
    lines.extend(f"{x['path']}\t{x['bytes']}\t{x['sha256']}\t{x['purpose']}" for x in files)
    lines.extend([
        "",
        "人工必填：作品编号、提交日期、审批签字；DOCX 原生渲染需在提交电脑用 Word/LibreOffice 复核。",
    ])
    (SUB / "SUBMISSION_MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "manifest": str(SUB / "SUBMISSION_MANIFEST.txt")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
