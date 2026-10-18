"""Run the standalone decoder-v2-sync on a small set of image files.

The probe never receives expected IDs or source geometry; it is intended for
developer diagnostics and writes JSON to stdout.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gm_provenance.watermark_sync import SyncConfig, detect_image_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="probe decoder-v2-sync")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "decoder-v2-sync.json")
    args = parser.parse_args()
    config = SyncConfig(**json.loads(args.config.read_text(encoding="utf-8")))
    rows = []
    for path in args.images:
        try:
            result = detect_image_sync(path.read_bytes(), config)
        except Exception as exc:  # keep one bad fixture from hiding others
            result = {"status": "ERROR", "content_ids": [], "details": {"error": str(exc)}}
        rows.append({"path": str(path), **result})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
