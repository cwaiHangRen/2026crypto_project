"""SQLite persistence for the v2 orchestration layer.

The v1 registry remains untouched.  V2 writes to ``v2.sqlite`` and keeps the
full canonical manifest and verification evidence so an export is auditable.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class V2Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "v2.sqlite"
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS v2_contents (
                    content_id TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    final_sha256 TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL UNIQUE,
                    key_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    parent_content_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    descriptor_json TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    signature_b64 TEXT NOT NULL,
                    media BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v2_contents_parent ON v2_contents(parent_content_id);
                CREATE TABLE IF NOT EXISTS v2_batches (
                    batch_id TEXT PRIMARY KEY,
                    merkle_root TEXT NOT NULL,
                    leaf_count INTEGER NOT NULL,
                    tree_height INTEGER NOT NULL,
                    key_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    signature_b64 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_audit (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    content_id TEXT,
                    actor TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    result TEXT NOT NULL,
                    previous_event_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v2_audit_content ON v2_audit(content_id);
                """
            )

    def put_content(self, row: dict[str, Any]) -> None:
        fields = (
            "content_id", "media_type", "original_sha256", "final_sha256", "manifest_hash",
            "key_id", "version", "parent_content_id", "status", "created_at", "descriptor_json",
            "manifest_json", "signature_b64", "media",
        )
        values = [row.get(name) for name in fields]
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO v2_contents (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                values,
            )
            con.execute("COMMIT")

    def get_content(self, content_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM v2_contents WHERE content_id=?", (content_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for key in ("descriptor_json", "manifest_json"):
            out[key[:-5] if key.endswith("_json") else key] = json.loads(out.pop(key))
        return out

    def list_contents(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT content_id,media_type,final_sha256,manifest_hash,key_id,version,parent_content_id,status,created_at FROM v2_contents ORDER BY created_at,content_id").fetchall()
        return [dict(r) for r in rows]

    def put_batch(self, row: dict[str, Any]) -> None:
        with self._lock, self._connect() as con:
            con.execute("INSERT INTO v2_batches VALUES(?,?,?,?,?,?,?,?)", (
                row["batch_id"], row["merkle_root"], int(row["leaf_count"]), int(row["tree_height"]),
                row["key_id"], row["created_at"], json.dumps(row["manifest"], ensure_ascii=False, sort_keys=True), row["signature_b64"],
            ))

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM v2_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["manifest"] = json.loads(out.pop("manifest_json"))
        return out

    def append_audit(self, row: dict[str, Any]) -> None:
        with self._lock, self._connect() as con:
            con.execute("INSERT INTO v2_audit VALUES(?,?,?,?,?,?,?,?,?)", (
                row["event_id"], row["event_type"], row.get("content_id"), row["actor"], row["timestamp"],
                row["result"], row.get("previous_event_hash"), row["event_hash"],
                json.dumps(row.get("details", {}), ensure_ascii=False, sort_keys=True),
            ))

    def audit_rows(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM v2_audit ORDER BY rowid").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            out.append(item)
        return out
