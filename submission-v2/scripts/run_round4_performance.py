#!/usr/bin/env python3
"""Fixed round-4 diagnostics; observational wrappers, no service optimization.

Run serially. Each root/derived target has a separate Registry with that target
current. Main paired_sidecar trials share all inputs except mode. Uninstrumented
controls measure end-to-end observer overhead; they are never used for stages.
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
from datetime import datetime, timezone
import functools
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import secrets
import statistics
import subprocess
import sys
import time
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gm_provenance import manifest, media, state, watermark  # noqa: E402
from gm_provenance.crypto import OpenSSLCrypto  # noqa: E402
from gm_provenance.registry import Registry  # noqa: E402
from gm_provenance.service import ProvenanceService  # noqa: E402

CONFIG = {
    "schema_version": "round4-performance-2", "seed": 20261007,
    "dataset_manifest": "data/round3-development-v3/dataset_manifest.jsonl",
    "strata": [
        {"sample_id": "img-synth-001", "stratum": "png", "versions": ["root", "jpeg-reencode", "resize"]},
        {"sample_id": "img-real-coco-139", "stratum": "jpeg", "versions": ["root", "jpeg-reencode", "resize"]},
        {"sample_id": "pdf-synth-001", "stratum": "pdf_single", "versions": ["root"]},
        {"sample_id": "pdf-synth-002", "stratum": "pdf_multipage", "versions": ["root"]},
    ],
    "warmups": 1, "repeats": 3, "watermark_profile": "watermark-v1",
    "groups": {
        "paired_sidecar": {"modes": ["hard", "recover", "full"], "sidecar": True},
        "lost_recovery": {"modes": ["recover", "full"], "sidecar": False},
        "no_provenance": {"modes": ["hard", "recover", "full"], "sidecar": False, "root_only": True},
        "invalid_input": {"modes": ["hard", "recover", "full"], "sidecar": False, "root_only": True},
    },
    "state_policy": "separate registry per version; target is latest; independent state key; same snapshot supplied to every mode; full rereads and verifies the complete snapshot each call; high-water preaccepted",
    "measurement_policy": "one warmup plus three measured repeats per input/mode/observer; rotated mode order and alternating baseline/instrumented order",
}
STAGES = ("read", "inspect_decode", "watermark", "pdf_render", "sm3", "canonicalization", "sm2verify", "pubkey_conversion", "parent", "db", "state", "doctor")
OPENSSL_CATEGORIES = ("sm3", "sm2verify", "sm2sign", "pubkey_convert", "pubkey_check", "version", "keygen", "other")
CTX = contextvars.ContextVar("round4_diagnostic_context", default=None)
PATCHES: list[tuple[Any, str, Any]] = []


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path):
    with Path(path).open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def git_bytes(args):
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=20, check=False)
    if proc.returncode:
        raise RuntimeError("git provenance command failed")
    return proc.stdout


def source_snapshot():
    paths = [Path(__file__), *sorted((ROOT / "src/gm_provenance").glob("*.py")), ROOT / "docs/ROUND4_PERFORMANCE.md"]
    files = {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in paths}
    dirty_names = git_bytes(["ls-files", "--modified", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    dirty_files = {name: sha256_file(ROOT / name) if (ROOT / name).is_file() else "MISSING" for name in sorted(set(dirty_names)) if name}
    return {"source_files": files, "source_hash": hash_json(files),
            "git_head": git_bytes(["rev-parse", "HEAD"]).decode().strip(),
            "dirty_files": dirty_files, "dirty_hash": hash_json(dirty_files),
            "git_status_hash": hashlib.sha256(git_bytes(["status", "--porcelain=v1"]).strip()).hexdigest()}


def dependencies():
    versions = {}
    for name in ("Pillow", "numpy", "scipy", "reedsolo", "pypdf", "pypdfium2", "reportlab", "rfc8785"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "NOT_INSTALLED"
    value = {"packages": versions, "python": sys.version, "executable": str(Path(sys.executable).resolve()), "platform": platform.platform()}
    return {**value, "dependency_hash": hash_json(value)}


def selected_rows():
    index = {r["sample_id"]: r for r in (json.loads(line) for line in (ROOT / CONFIG["dataset_manifest"]).read_text(encoding="utf-8").splitlines() if line.strip())}
    selected = []
    for spec in CONFIG["strata"]:
        row = {**index[spec["sample_id"]], **spec}
        if sha256_file(ROOT / row["local_path"]) != row["sha256"]:
            raise ValueError("dataset SHA256 mismatch: " + row["sample_id"])
        selected.append(row)
    return selected


@contextlib.contextmanager
def observation():
    ctx = {"inclusive_ns": {}, "exclusive_ns": {}, "stack": [], "calls": {},
           "openssl": {c: {"calls": 0, "nonzero": 0, "exceptions": 0, "elapsed_ms": 0.0} for c in OPENSSL_CATEGORIES},
           "sm3_inputs": {}, "pubkey_inputs": {}, "db_methods": {}, "sql": {}, "state_rebuilds": 0}
    token = CTX.set(ctx)
    try:
        yield ctx
    finally:
        CTX.reset(token)


@contextlib.contextmanager
def span(category):
    ctx = CTX.get()
    if ctx is None:
        yield
        return
    frame = [time.perf_counter_ns(), 0]
    ctx["stack"].append(frame)
    try:
        yield
    finally:
        elapsed = time.perf_counter_ns() - frame[0]
        ctx["stack"].pop()
        if ctx["stack"]:
            ctx["stack"][-1][1] += elapsed
        for key, value in (("inclusive_ns", elapsed), ("exclusive_ns", elapsed - frame[1])):
            ctx[key][category] = ctx[key].get(category, 0) + value


def replace(owner, name, replacement):
    PATCHES.append((owner, name, getattr(owner, name)))
    setattr(owner, name, replacement)


def fingerprint_input(ctx, category, data):
    if isinstance(data, bytes):
        digest = hashlib.sha256(data).hexdigest()
        entry = ctx[category].setdefault(digest, {"bytes": len(data), "calls": 0})
        entry["calls"] += 1


def timed(owner, name, category):
    original = getattr(owner, name)

    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        ctx = CTX.get()
        if ctx is not None:
            counts = ctx["calls"].setdefault(category, {"calls": 0, "exceptions": 0})
            counts["calls"] += 1
            if owner is OpenSSLCrypto and name in ("sm3", "public_der"):
                fingerprint_input(ctx, "sm3_inputs" if name == "sm3" else "pubkey_inputs", args[1] if len(args) > 1 else kwargs.get("data", kwargs.get("public_pem")))
            if owner is Registry:
                ctx["db_methods"][name] = ctx["db_methods"].get(name, 0) + 1
                if name == "status":
                    ctx["state_rebuilds"] += 1
        try:
            with span(category):
                return original(*args, **kwargs)
        except Exception:
            if ctx is not None:
                counts["exceptions"] += 1
            raise
    replace(owner, name, wrapped)


def install_instrumentation():
    if PATCHES:
        raise RuntimeError("instrumentation already installed")
    from PIL import Image
    timed(Path, "read_bytes", "read")
    timed(Path, "read_text", "read")
    timed(media, "sniff", "inspect_decode")
    timed(media, "inspect_media", "inspect_decode")
    timed(Image, "open", "inspect_decode")
    # ImageFile.load overrides Image.load for compressed inputs; observe both.
    from PIL import ImageFile
    timed(Image.Image, "load", "inspect_decode")
    timed(ImageFile.ImageFile, "load", "inspect_decode")
    timed(watermark, "detect_image", "watermark")
    timed(manifest, "_canonical", "canonicalization")
    timed(state, "_jcs", "canonicalization")
    for name, category in (("sm3", "sm3"), ("verify", "sm2verify"), ("public_der", "pubkey_conversion"), ("doctor", "doctor")):
        timed(OpenSSLCrypto, name, category)
    timed(ProvenanceService, "_check_parent", "parent")
    timed(ProvenanceService, "_check_authorization", "parent")
    timed(ProvenanceService, "verify_state_snapshot", "state")
    for name in ("get_key", "get_manifest", "read_media", "status", "accept_sequence"):
        timed(Registry, name, "db")

    original_connect = Registry._connect
    def connect(registry):
        connection = original_connect(registry)
        # SQL is never retained: only first-token categories are counted.
        def trace(statement):
            ctx = CTX.get()
            if ctx is not None:
                category = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "EMPTY"
                category = category if category in ("SELECT", "BEGIN", "COMMIT", "ROLLBACK", "UPDATE", "INSERT", "PRAGMA") else "OTHER"
                ctx["sql"][category] = ctx["sql"].get(category, 0) + 1
        connection.set_trace_callback(trace)
        return connection
    replace(Registry, "_connect", connect)

    original_pdf = media._pdf_pages
    def pdf_pages(data):
        iterator = original_pdf(data)
        try:
            while True:
                try:
                    with span("pdf_render"):
                        item = next(iterator)
                except StopIteration:
                    break
                yield item
        finally:
            with span("pdf_render"):
                iterator.close()
    replace(media, "_pdf_pages", pdf_pages)

    original_run = OpenSSLCrypto._run
    def run(crypto, args, data=None, passphrase=None):
        ctx = CTX.get()
        if ctx is None:
            return original_run(crypto, args, data, passphrase)
        category = "other"
        if args[0] == "dgst": category = "sm3"
        elif args[0] == "version": category = "version"
        elif args[0] == "genpkey": category = "keygen"
        elif args[0] == "pkey": category = "pubkey_check" if "-pubcheck" in args else "pubkey_convert"
        elif args[0] == "pkeyutl": category = "sm2verify" if "-verify" in args else "sm2sign"
        entry = ctx["openssl"][category]
        entry["calls"] += 1
        started = time.perf_counter_ns()
        try:
            result = original_run(crypto, args, data, passphrase)
            entry["nonzero"] += result.returncode != 0
            return result
        except Exception:
            entry["exceptions"] += 1
            raise
        finally:
            entry["elapsed_ms"] += (time.perf_counter_ns() - started) / 1e6
    replace(OpenSSLCrypto, "_run", run)


def restore_instrumentation():
    for owner, name, original in reversed(PATCHES):
        setattr(owner, name, original)
    PATCHES.clear()


def prepare_cases(selected, runtime, private, public, state_private, password):
    cases = []
    for row in selected:
        info = media.inspect_media((ROOT / row["local_path"]).read_bytes())
        if row["stratum"] == "pdf_single" and info.get("page_count") != 1:
            raise ValueError("single-page stratum mismatch")
        if row["stratum"] == "pdf_multipage" and info.get("page_count", 0) <= 1:
            raise ValueError("multipage stratum mismatch")
        for version in row["versions"]:
            case_id = row["sample_id"] + "-" + version
            root = runtime / case_id
            service = ProvenanceService(root / "registry")
            service.trust_key(public, "round4-perf")
            published = service.register(ROOT / row["local_path"], private, password, publisher_id="round4-perf", watermark_profile=CONFIG["watermark_profile"])
            if version != "root":
                published = service.derive(published["content_id"], private, password, version, quality=75, watermark_profile=CONFIG["watermark_profile"])
            snapshot = service.state_snapshot(state_private, password, root / "state.json", root / "state-public.pem", ttl_seconds=86400)
            # Preaccept high-water outside timing, making state identical for
            # baseline/instrumented and every mode/repeat. No cache is added.
            if service.verify_state_snapshot(snapshot["snapshot_path"], snapshot["state_public_path"])["status"] != "PASS":
                raise ValueError("prepared snapshot did not verify")
            invalid = root / "invalid.bin"
            invalid.write_bytes(b"round4 intentionally unsupported bytes\n")
            cases.append({"case_id": case_id, "sample_id": row["sample_id"], "stratum": row["stratum"], "version": version,
                          "service": service, "published": published, "source": ROOT / row["local_path"], "invalid": invalid,
                          "snapshot": snapshot["snapshot_path"], "state_public": snapshot["state_public_path"],
                          "hashes": {"media": sha256_file(published["media_path"]), "sidecar": sha256_file(published["manifest_path"]),
                                     "state": sha256_file(snapshot["snapshot_path"]), "registry": sha256_file(service.registry.db_path)},
                          "source_info": info})
    return cases


def execute(case, group, mode, repeat, phase, instrumented):
    cfg = CONFIG["groups"][group]
    path = case["source"] if group == "no_provenance" else case["invalid"] if group == "invalid_input" else Path(case["published"]["media_path"])
    sidecar = Path(case["published"]["manifest_path"]) if cfg["sidecar"] else None
    if instrumented:
        install_instrumentation()
    try:
        with observation() as ctx:
            started = time.perf_counter_ns()
            result, error = None, None
            try:
                result = case["service"].verify(path, sidecar, mode, case["snapshot"], case["state_public"])
            except Exception as exc:
                error = {"type": type(exc).__name__}  # Never retain exception messages/arguments.
            elapsed_ns = time.perf_counter_ns() - started
    finally:
        if instrumented:
            restore_instrumentation()
    status = "error" if error else "early_return" if result.get("manifest_presence") != "PASS" or result.get("manifest_schema") == "FAIL" else "success"
    observed = {k: v for k, v in (result or {}).items() if k != "timings"}
    row = {"case_id": case["case_id"], "sample_id": case["sample_id"], "stratum": case["stratum"], "version": case["version"],
           "group": group, "mode": mode, "repeat": repeat, "phase": phase, "instrumented": instrumented,
           "input_identity": hash_json([sha256_file(path), sha256_file(sidecar) if sidecar else None, case["hashes"]]),
           "manifest_supplied": sidecar is not None, "state_supplied": True,
           "execution_status": status, "error": error, "observed": observed,
           "total_ms": elapsed_ns / 1e6, "service_total_ms": (result or {}).get("timings", {}).get("total_ms")}
    if instrumented:
        row["inclusive_ms"] = {s: ctx["inclusive_ns"].get(s, 0) / 1e6 for s in STAGES}
        row["exclusive_ms"] = {s: ctx["exclusive_ns"].get(s, 0) / 1e6 for s in STAGES}
        row["unattributed_ms"] = (elapsed_ns - sum(ctx["exclusive_ns"].values())) / 1e6
        row["counters"] = {k: ctx[k] for k in ("calls", "openssl", "db_methods", "sql", "state_rebuilds", "sm3_inputs", "pubkey_inputs")}
        row["counters"]["sm3_distinct_inputs"] = len(ctx["sm3_inputs"])
        row["counters"]["sm3_repeated_inputs"] = sum(max(0, v["calls"] - 1) for v in ctx["sm3_inputs"].values())
    return row


def run_trials(cases, path):
    rows = []
    with path.open("a", encoding="utf-8") as fh:
        for case in cases:
            for group, cfg in CONFIG["groups"].items():
                if cfg.get("root_only") and case["version"] != "root":
                    continue
                for repeat in range(CONFIG["warmups"] + CONFIG["repeats"]):
                    phase = "warmup" if repeat < CONFIG["warmups"] else "measured"
                    modes = cfg["modes"]
                    offset = repeat % len(modes)
                    for mode in modes[offset:] + modes[:offset]:
                        for instrumented in ((False, True) if repeat % 2 == 0 else (True, False)):
                            row = execute(case, group, mode, repeat, phase, instrumented)
                            rows.append(row)
                            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                            fh.flush()
    return rows


def statistics_ms(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": statistics.mean(values), "p50": statistics.median(values),
            "p95": values[0] if len(values) == 1 else statistics.quantiles(values, n=100, method="inclusive")[94], "min": values[0], "max": values[-1]}


def summarize(rows):
    measured = [r for r in rows if r["phase"] == "measured"]
    grouped = {}
    for row in measured:
        key = "/".join(str(row[k]) for k in ("group", "stratum", "version", "mode", "execution_status")) + ("/instrumented" if row["instrumented"] else "/baseline")
        grouped.setdefault(key, []).append(row)
    summary = {}
    for key, items in grouped.items():
        summary[key] = {"total_ms": statistics_ms([r["total_ms"] for r in items])}
        if items[0]["instrumented"]:
            for kind in ("inclusive_ms", "exclusive_ms"):
                summary[key][kind] = {s: statistics_ms([r[kind][s] for r in items]) for s in STAGES}
    pairs = {}
    for row in measured:
        key = "/".join(str(row[k]) for k in ("case_id", "group", "mode", "repeat"))
        pairs.setdefault(key, {})["instrumented" if row["instrumented"] else "baseline"] = row
    overhead = []
    for key, pair in pairs.items():
        if len(pair) != 2:
            continue
        a, b = pair["baseline"], pair["instrumented"]
        overhead.append({"pair": key, "baseline_ms": a["total_ms"], "instrumented_ms": b["total_ms"],
                         "delta_ms": b["total_ms"] - a["total_ms"],
                         "ratio": b["total_ms"] / a["total_ms"] if a["total_ms"] else None,
                         "same_inputs": a["input_identity"] == b["input_identity"],
                         "same_result": a["observed"] == b["observed"] and a["error"] == b["error"],
                         "execution_status": b["execution_status"]})
    return {"rows_including_warmup": len(rows), "measured_rows": len(measured),
            "execution": {status: sum(r["execution_status"] == status for r in measured if r["instrumented"]) for status in ("success", "early_return", "error")},
            "groups": summary, "overhead_pairs": overhead,
            "overhead_note": "Adjacent baseline/instrumented real calls; deltas may be negative from OS cache/scheduling. No subtraction from stage timings. Nested inclusive timers must not be summed.",
            "instrumentation_semantic_mismatches": sum(not r["same_result"] or not r["same_inputs"] for r in overhead)}


def write_report(output, run, summary):
    text = f"""# Round 4 性能诊断

- run_id：`{run['run_id']}`；状态：`{run['status']}`。
- 详细配置、选定 v3 数据记录、运行前后源码与脏文件哈希、依赖和 doctor 见 run_manifest.json。
- paired_sidecar 三模式使用完全相同的 sidecar、状态、公钥、信任库、父链。每个目标独立 Registry，目标始终是 latest。
- lost_recovery 的 recover/full 均无 sidecar；no_provenance 和 invalid_input 单独分组。
- 每组每模式预热 1 次、正式重复 3 次；baseline/instrumented 交替顺序。每次 full 调用都重读并验签完整状态快照，未加缓存。
- 正式 instrumented 执行分布：`{summary.get('execution', {})}`；观测语义差异数：`{summary.get('instrumentation_semantic_mismatches', 'N/A')}`。
- success 仅表示服务走完终态，不等于 VERIFIED；请同时查看 observed.conclusion 与各状态字段。
- inclusive_ms 为嵌套总时长，不能相加。exclusive_ms 扣除被观测子调用，exclusive 加 unattributed 等于 total；OpenSSL elapsed 已包含在上层，不另加。
- overhead_pairs 直接比较未包装/多层包装的真实调用；包含计时、SHA256 观测指纹、SQL trace、计数成本。允许负差值，不把噪声推成优化。
- 每格仅 3 次重复，P95 为描述性插值；不能解释为服务延迟承诺。
- trials.jsonl 保留预热/正式、返回/提前返回/异常及输入指纹、SM3 重复次数、公钥/SQL/状态重建和 OpenSSL 分类。
"""
    (output / "ROUND4_PERFORMANCE.md").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="results/round4-performance")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    output = (ROOT / args.output_root / run_id).resolve()
    output.relative_to(ROOT)  # Keep all artifacts in the project.
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir()
    run = {"run_id": run_id, "schema_version": CONFIG["schema_version"], "status": "STARTED", "started_at": utc_now(), "config": CONFIG, "config_hash": hash_json(CONFIG)}
    write_json(output / "config.json", CONFIG)
    write_json(output / "run_manifest.json", run)
    rows, error = [], None
    private, state_private = runtime / "private.pem", runtime / "state-private.pem"
    try:
        selected = selected_rows()
        run.update({"selected_data": selected, "dataset_manifest_sha256": sha256_file(ROOT / CONFIG["dataset_manifest"]), "data_hash": hash_json({r["sample_id"]: r["sha256"] for r in selected}), "source_before": source_snapshot(), "dependencies": dependencies()})
        crypto = OpenSSLCrypto()
        install_instrumentation()
        try:
            with observation() as ctx:
                run["doctor"] = crypto.doctor()
            run["doctor_counters"] = {k: ctx[k] for k in ("calls", "openssl")}
        finally:
            restore_instrumentation()
        public = runtime / "public.pem"
        password = secrets.token_urlsafe(32)
        crypto.generate_key(private, public, password)
        crypto.generate_key(state_private, runtime / "state-public.pem", password)
        cases = prepare_cases(selected, runtime, private, public, state_private, password)
        run["cases"] = [{k: c[k] for k in ("case_id", "sample_id", "stratum", "version", "hashes", "source_info")} for c in cases]
        write_json(output / "run_manifest.json", run)
        rows = run_trials(cases, output / "trials.jsonl")
        run["source_after"] = source_snapshot()
        run["source_changed_during_run"] = run["source_before"]["source_hash"] != run["source_after"]["source_hash"]
        run["registry_after_hash"] = {c["case_id"]: sha256_file(c["service"].registry.db_path) for c in cases}
        run["registry_changed_during_verify"] = {c["case_id"]: c["hashes"]["registry"] != run["registry_after_hash"][c["case_id"]] for c in cases}
    except Exception as exc:
        error = {"type": type(exc).__name__}  # No secrets in exception text.
        if (output / "trials.jsonl").exists():
            rows = [json.loads(line) for line in (output / "trials.jsonl").read_text(encoding="utf-8").splitlines() if line]
    finally:
        restore_instrumentation()
        private.unlink(missing_ok=True)
        state_private.unlink(missing_ok=True)
    run.update({"status": "BLOCKED" if error else "LOCAL_DIAGNOSTIC", "error": error, "finished_at": utc_now()})
    summary = summarize(rows)
    summary.update({"run_id": run_id, "status": run["status"], "error": error})
    write_json(output / "run_manifest.json", run)
    write_json(output / "summary.json", summary)
    write_report(output, run, summary)
    print(json.dumps({"run_id": run_id, "status": run["status"], "output": str(output), "rows": len(rows), "error": error}, ensure_ascii=False))
    return 2 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
