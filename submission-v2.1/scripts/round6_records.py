"""Small durability and identity helpers for the Round 6 experiment only."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

AGGREGATOR_VERSION = "round6-metrics-4"
METRIC_POLICY = {
    "primary": "FOUND with exactly the expected unique ID, no conflict or return truncation",
    "partial_found": "Included in primary if FOUND; independently list search_complete=false",
    "expected_hit": "Expected ID occurs in returned set, independently of status",
    "missing": "Not a detector outcome and excluded from observed denominators",
    "invalid_transform": "Precondition failure, not a detector attempt",
    "gate_rejected": "No detector execution; retained in end-to-end attempt denominator",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def judge(observed, expected=None):
    if not isinstance(observed, dict):
        return {"observed": False, "expected_id_hit": False, "correct_single_output": False, "search_complete": None}
    ids = sorted(set(map(str, observed.get("content_ids") or observed.get("recovered_content_ids") or [])))
    detail = observed.get("details") or {}
    stop = observed.get("stop_reason", detail.get("stop_reason"))
    truncated = bool(observed.get("truncated") or detail.get("truncated") or stop == "return_budget_exhausted")
    status = observed.get("status", "UNKNOWN")
    hit = expected is not None and expected in ids
    single = expected is not None and ids == [expected] and status == "FOUND" and not truncated
    return {"observed": True, "expected_id_hit": hit, "correct_single_output": single,
            "search_complete": observed.get("search_complete", detail.get("search_complete")),
            "stop_reason": stop, "truncated": truncated, "status": status, "observed_ids": ids,
            "conflict": status == "CONFLICT" or len(ids) > 1,
            "wrong_ids": [x for x in ids if expected is None or x != expected]}


def audit_jsonl(path):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b""
    chunks = raw.splitlines(keepends=True)
    unique, duplicates, corrupt, tail = {}, [], [], None
    for line, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        try:
            row = json.loads(chunk)
            if not isinstance(row, dict) or not row.get("key"):
                raise ValueError("missing object/task key")
        except (ValueError, UnicodeDecodeError) as exc:
            issue = {"line": line, "error": str(exc), "sha256": hashlib.sha256(chunk).hexdigest(), "hex": chunk.hex()}
            if line == len(chunks) and not chunk.endswith(b"\n"):
                tail = issue
            else:
                corrupt.append(issue)
            continue
        key = row["key"]
        if key in unique:
            duplicates.append({"key": key, "line": line, "identical": unique[key] == row})
        else:
            unique[key] = row
    conflicts = [x for x in duplicates if not x["identical"]]
    return list(unique.values()), {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "physical_lines": len(chunks), "unique_rows": len(unique), "duplicates": duplicates,
        "conflicting_duplicates": conflicts, "corrupt_lines": corrupt, "partial_tail": tail,
        "safe": not corrupt and not conflicts and tail is None}


def checked_rows(path):
    rows, audit = audit_jsonl(path)
    if not audit["safe"]:
        raise ValueError(f"JSONL needs audited recovery copy, not in-place truncation: {audit}")
    return rows, {x["key"] for x in rows}


def unique_rows(rows):
    """Never choose a favourable (or last) outcome from contradictory rows."""
    by_key, duplicates = {}, {}
    for row in rows:
        key = row.get("key")
        if not key:
            raise ValueError("missing task key")
        if key in by_key:
            if row != by_key[key]:
                raise ValueError(f"conflicting duplicate task: {key}")
            duplicates[key] = duplicates.get(key, 1) + 1
        else:
            by_key[key] = row
    return list(by_key.values()), duplicates


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


@contextlib.contextmanager
def exclusive_run_lock(path):
    """OS file lock: released on crash, unlike a stale PID-only lock file."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if file.tell() == 0:
            file.write(b"0"); file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            try: msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc: raise RuntimeError("Another Round6 runner owns the lock") from exc
        else:
            import fcntl
            try: fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc: raise RuntimeError("Another Round6 runner owns the lock") from exc
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt": msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(file, fcntl.LOCK_UN)


def require_resume_match(previous, current, planned_keys, actual_keys):
    if previous.get("execution_fingerprint") != current.get("execution_fingerprint"):
        raise ValueError("Execution fingerprint changed; choose a new run_id")
    if previous.get("config") != current.get("config"):
        raise ValueError("Frozen configuration changed; choose a new run_id")
    if set(actual_keys) - set(planned_keys):
        raise ValueError("Recorded task does not belong to frozen inventory")


def process_identity():
    # PID + process creation time + a per-session nonce protect against PID reuse.
    created = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            times = [wintypes.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(x) for x in times)):
                created = (times[0].dwHighDateTime << 32) + times[0].dwLowDateTime
        except (OSError, OverflowError, ctypes.ArgumentError):
            # PID plus the per-session nonce still prevents a stale PID file
            # from being mistaken for the current evaluator.
            created = None
    return {"pid": os.getpid(), "process_creation_filetime": created,
            "session_nonce": os.urandom(16).hex(), "observed_unix_time": time.time()}
