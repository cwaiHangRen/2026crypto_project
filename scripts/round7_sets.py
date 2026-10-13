"""Round 7 inventory, baseline audit, and target selection helpers."""
from __future__ import annotations
import hashlib, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = {"both_success":1441,"d0_failed_sync_success":1482,"d0_success_sync_failed":0,"both_failed":1731,"oracle_success_sync_failed":882,"attribution_total":3213,"published_attempts":180,"published_gate_pass":179}

def rows(path):
    p=Path(path); return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

def task_id(execution_fingerprint: str, key: str) -> str:
    return f"r7:{execution_fingerprint[:16]}:{key}"

def task_set(*paths):
    out={}
    for path in paths:
        for row in rows(path):
            key=row.get("key")
            if key: out.setdefault(str(key), row)
    return out


def _correct_single(observed, expected):
    if not isinstance(observed, dict) or observed.get("status") != "FOUND" or not expected:
        return False
    ids = list(dict.fromkeys(str(x) for x in observed.get("content_ids", [])))
    return len(ids) == 1 and ids[0] == str(expected)


def derive_sets(paired_path=None):
    """Derive mutually exclusive task-ID lists from durable paired rows.

    The lists are keyed by recorded ``task_id`` and retain the row key for
    human lookup.  No summary count is trusted as the source of membership.
    """
    path = Path(paired_path or ROOT / 'results/round6-sync/20261008T-round6-sync-final-r2/paired.jsonl')
    task_manifest = path.parent / "task_manifest.jsonl"
    task_ids = {}
    if task_manifest.exists():
        for item in rows(task_manifest):
            if item.get("key") and item.get("task_id"):
                task_ids[str(item["key"])] = item["task_id"]
    source = {}
    for row in rows(path):
        if row.get("role", "paired") != "paired" or not row.get("key"):
            continue
        key = str(row["key"])
        if key in source and source[key] != row:
            raise ValueError(f"conflicting duplicate task row: {key}")
        source[key] = row
    sets = {name: [] for name in ("d0_success", "d0_failed_sync_success", "dsync_not_success", "oracle_success_sync_failed", "oracle_na", "input_or_execution_error")}
    classified = {}
    for key, row in sorted(source.items()):
        expected = row.get("expected_content_id")
        d0 = _correct_single((row.get("d0") or {}).get("observed"), expected)
        sync = _correct_single((row.get("sync") or {}).get("observed"), expected)
        oracle = _correct_single(((row.get("oracle") or {}).get("observed")), expected)
        sync_obs = (row.get("sync") or {}).get("observed") or {}
        error = bool(row.get("transform_error")) or sync_obs.get("status") in {"ERROR", "UNAVAILABLE"} or not isinstance(sync_obs, dict)
        ref = {"task_id": row.get("task_id") or task_ids.get(key), "key": key, "sample_id": row.get("sample_id"), "profile": row.get("profile"), "transform": row.get("transform"), "pair_cell": row.get("pair_cell"), "sync_status": sync_obs.get("status")}
        # Error rows are reported separately but also remain in dsync_not_success
        # because they did not recover; this makes the denominator explicit.
        if d0:
            name = "d0_success"
        elif sync:
            name = "d0_failed_sync_success"
        else:
            name = "dsync_not_success"
        sets[name].append(ref)
        classified[key] = name
        if oracle and not sync:
            sets["oracle_success_sync_failed"].append(ref)
        if not oracle:
            sets["oracle_na"].append(ref)
        if error:
            sets["input_or_execution_error"].append(ref)
    return {"source": str(path).replace('\\', '/'), "unique_paired_rows": len(source), "sets": sets, "classification": classified,
            "counts": {name: len(value) for name, value in sets.items()},
            "definitions": {"d0_success": "D0 expected ID, correct single output", "d0_failed_sync_success": "D0 failed and sync expected ID, correct single output", "dsync_not_success": "sync did not produce expected correct single output, including conflict/truncation/budget/error", "oracle_success_sync_failed": "offline geometry oracle expected ID and sync did not", "oracle_na": "oracle missing, not FOUND expected, or not applicable", "input_or_execution_error": "transform or detector infrastructure ERROR/UNAVAILABLE"}}


def publish_gate_audit(attempts_path=None):
    path = Path(attempts_path or ROOT / 'results/round5-watermark/20261007T-round5-watermark-final/attempts.jsonl')
    attempted = gate_pass = gate_fail = published = 0
    rows_out = []
    for row in rows(path):
        # The publish-combination denominator is the exact marked artifact for
        # each source/profile.  Propagation transforms and negative controls
        # are separate records and must not inflate this gate denominator.
        if row.get("role") == "negative_control" or row.get("transform") != "exact" or not row.get("attempted"):
            continue
        attempted += 1
        passed = bool(row.get("gate_passed"))
        gate_pass += int(passed); gate_fail += int(not passed); published += int(bool(row.get("published")))
        rows_out.append({"sample_id": row.get("sample_id"), "profile": row.get("profile"), "attempted": True, "gate_passed": passed, "published": bool(row.get("published")), "content_id": row.get("content_id")})
    return {"source": str(path).replace('\\', '/'), "publish_attempts": attempted, "gate_pass": gate_pass, "gate_fail": gate_fail, "published": published, "rows": rows_out}

def audit_baseline(summary_path=None, paired_path=None, round5_summary_path=None):
    """Audit recorded counts and derive attribution; values are never hard-coded into output."""
    summary_path=Path(summary_path or ROOT/'results/round6-sync/20261008T-round6-sync-final-r2/summary.json')
    s=json.loads(summary_path.read_text(encoding='utf-8'))
    cells=s.get('pairing_four_cells',{})
    paired=int(s.get('paired_rows',sum(cells.values())))
    oracle=int(s.get('oracle_success_sync_failed',0))
    attribution=int(cells.get('d0_failed_sync_success',0)+cells.get('both_failed',0))
    out={"source":str(summary_path),"paired_rows":paired,"pairing_four_cells":dict(cells),"oracle_success_sync_failed":oracle,"attribution_total":attribution,"baseline_contract":{k:(v==BASELINE.get(k)) for k,v in {**cells,'oracle_success_sync_failed':oracle,'attribution_total':attribution}.items()}}
    if round5_summary_path:
        r5=json.loads(Path(round5_summary_path).read_text(encoding='utf-8')); out['round5']={"published_attempts":r5.get('published_attempts',r5.get('attempts',None)),"published_gate_pass":r5.get('published_gate_pass',r5.get('gate_pass',None))}
    if paired_path:
        derived = derive_sets(paired_path)
        out['row_derived_counts'] = derived['counts']
        out['row_derived_attribution_total'] = derived['counts']['d0_failed_sync_success'] + derived['counts']['dsync_not_success']
        pr=rows(paired_path); task_manifest_path=Path(paired_path).parent/'task_manifest.jsonl'; task_map={x.get('key'):x.get('task_id') for x in rows(task_manifest_path)} if task_manifest_path.exists() else {}
        effective_task_ids=[r.get('task_id') or task_map.get(r.get('key')) for r in pr]
        out['task_id_count']=len({x for x in effective_task_ids if x}); out['task_id_missing']=sum(not x for x in effective_task_ids); out['task_id_duplicates']=len([x for x in effective_task_ids if x])-len({x for x in effective_task_ids if x}); out['duplicate_keys']=len(pr)-len({r.get('key') for r in pr if r.get('key')})
        out['summary_matches_rows'] = derived['counts']['d0_success'] == cells.get('both_success', 0) and derived['counts']['d0_failed_sync_success'] == cells.get('d0_failed_sync_success', 0)
    return out

def candidate_config(base=None):
    c=json.loads(json.dumps(base or json.loads((ROOT/'configs/round6-sync-evaluation.json').read_text(encoding='utf-8'))))
    c['schema_version']='round7-development-1'; c.setdefault('decoder',{})['profile']='decoder-v3-sync'
    c['execution_version']='decoder-v3-sync'; c['candidate_profiles']=['v1','v2','feedback']
    return c

def select_targets(keys, target_only=None):
    keys=sorted(set(map(str,keys)))
    if not target_only: return keys
    wanted={x.strip() for x in str(target_only).split(',') if x.strip()}
    return [k for k in keys if k in wanted or any(k.startswith(x) for x in wanted)]

def summarize(rows_):
    cells=Counter(r.get('pair_cell') for r in rows_ if r.get('pair_cell'))
    by_profile=Counter((r.get('profile'),r.get('transform')) for r in rows_)
    return {'paired_rows':len(rows_),'pairing_four_cells':dict(cells),'by_profile_transform':{f'{p}|{t}':n for (p,t),n in sorted(by_profile.items())}}
