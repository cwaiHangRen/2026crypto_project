"""Probe optional MP3/FLAC/MP4 support and write a privacy-safe report."""
from __future__ import annotations

import argparse
from pathlib import Path

from gm_provenance.codec_capabilities import build_capability_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="logs/v2.1/codec-capability.json")
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    import json
    report = build_capability_report()
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
