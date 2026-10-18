"""Run the v2 deterministic attack matrix for one published media file.

Usage: python scripts/run_v2_attack_matrix.py marked.png --content-id <hex> --output results/v2-attack
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from gm_provenance.attack_lab import attack_summary, default_attack_cases, run_attack_matrix, write_attack_matrix
from gm_provenance.media_adapters import adapter_for


def main(argv=None):
    p=argparse.ArgumentParser(description="gm-provenance v2 media attack matrix")
    p.add_argument("input", type=Path); p.add_argument("--content-id", required=True); p.add_argument("--output", type=Path, default=Path("results/v2-attack")); args=p.parse_args(argv)
    raw=args.input.read_bytes(); adapter=adapter_for(raw); cases=default_attack_cases(adapter.media_type)
    args.output.mkdir(parents=True, exist_ok=True)
    rows=run_attack_matrix(raw,args.content_id,cases,adapter=adapter,output_dir=args.output/"samples",log_path=args.output/"attack-matrix.json")
    write_attack_matrix(rows,args.output/"attack-matrix.csv")
    summary=attack_summary(rows); (args.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"media_type":adapter.media_type,"count":len(rows),"summary":summary,"output":str(args.output)},ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
