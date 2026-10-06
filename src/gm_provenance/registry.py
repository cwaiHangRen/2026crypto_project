"""SQLite-backed local registry for manifests, media, keys and revocations.

The database is the atomic local authority for this prototype.  It is not a
tamper-proof ledger; callers that need freshness should use ``state.py``
snapshots signed by a separate service key.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_json(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _encode_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_json(v) for v in value]
    return value


def _decode_json(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__bytes__"} and isinstance(value["__bytes__"], str):
            try:
                return base64.b64decode(value["__bytes__"], validate=True)
            except Exception:
                return value
        return {k: _decode_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_json(v) for v in value]
    return value


class Registry:
    """Local SQLite registry.

    ``root`` contains ``registry.sqlite`` and materialized ``media`` and
    ``manifests`` files.  BLOBs and complete manifests are committed together
    in one transaction; exported files are convenience copies.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        (self.root / "manifests").mkdir(exist_ok=True)
        self.db_path = self.root / "registry.sqlite"
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _init_db(self) -> None:
        with self._connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta(key,value) VALUES ('sequence','0'), ('issued_at','');
                INSERT OR IGNORE INTO meta(key,value) VALUES ('high_water','0');
                CREATE TABLE IF NOT EXISTS keys (
                    key_id TEXT PRIMARY KEY, public_pem BLOB NOT NULL,
                    publisher_id TEXT NOT NULL, trusted INTEGER NOT NULL CHECK(trusted IN (0,1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS contents (
                    content_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, version INTEGER NOT NULL,
                    publisher_id TEXT NOT NULL, manifest_hash TEXT NOT NULL UNIQUE,
                    manifest_json TEXT NOT NULL, media BLOB NOT NULL,
                    media_path TEXT NOT NULL, manifest_path TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(asset_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_contents_hash ON contents(manifest_hash);
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY, current_content_id TEXT NOT NULL,
                    FOREIGN KEY(current_content_id) REFERENCES contents(content_id)
                );
                CREATE TABLE IF NOT EXISTS revocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sequence INTEGER NOT NULL,
                    target_type TEXT NOT NULL CHECK(target_type IN ('content','key')),
                    target_id TEXT NOT NULL, reason TEXT NOT NULL,
                    issuer TEXT NOT NULL, issued_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_revocations_target ON revocations(target_type,target_id);
                """
            )

    def _bump(self, c: sqlite3.Connection) -> int:
        row = c.execute("SELECT value FROM meta WHERE key='sequence'").fetchone()
        seq = int(row[0]) + 1
        now = _utc_now()
        c.execute("UPDATE meta SET value=? WHERE key='sequence'", (str(seq),))
        c.execute("UPDATE meta SET value=? WHERE key='issued_at'", (now,))
        return seq

    def add_key(self, public_pem: bytes, publisher_id: str, trusted: bool, crypto: Any) -> str:
        key_id = crypto.key_id(public_pem)
        if not isinstance(key_id, str) or len(key_id) != 64:
            raise ValueError("crypto.key_id must return 64-char hex")
        try:
            int(key_id, 16)
        except ValueError as e:
            raise ValueError("invalid key_id") from e
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT public_pem,publisher_id,trusted FROM keys WHERE key_id=?", (key_id,)).fetchone()
            if old and bytes(old[0]) != bytes(public_pem):
                raise ValueError("key_id collision with different public key")
            if old and old[1] == publisher_id and bool(old[2]) == bool(trusted):
                c.execute("COMMIT")
                return key_id
            c.execute(
                "INSERT INTO keys(key_id,public_pem,publisher_id,trusted,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key_id) DO UPDATE SET public_pem=excluded.public_pem,publisher_id=excluded.publisher_id,trusted=excluded.trusted",
                (key_id, sqlite3.Binary(public_pem), publisher_id, int(bool(trusted)), _utc_now()),
            )
            self._bump(c)
            c.execute("COMMIT")
        return key_id

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM keys WHERE key_id=?", (key_id,)).fetchone()
        if not r:
            return None
        return {"key_id": r["key_id"], "public_pem": bytes(r["public_pem"]), "publisher_id": r["publisher_id"], "trusted": bool(r["trusted"])}

    def set_trust(self, key_id: str, trusted: bool) -> None:
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM keys WHERE key_id=?", (key_id,)).fetchone():
                raise KeyError(key_id)
            c.execute("UPDATE keys SET trusted=? WHERE key_id=?", (int(bool(trusted)), key_id))
            self._bump(c)
            c.execute("COMMIT")

    def publish(self, envelope: dict[str, Any], media_bytes: bytes, manifest_hash: str) -> dict[str, str]:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("body"), dict):
            raise ValueError("envelope.body is required")
        body = envelope["body"]
        content_id, asset_id = body.get("content_id"), body.get("asset_id")
        publisher_id, version = body.get("publisher_id"), body.get("version")
        if not isinstance(content_id, str) or len(content_id) != 32 or content_id != content_id.lower() or not isinstance(asset_id, str) or not asset_id:
            raise ValueError("invalid content or asset id")
        try:
            int(content_id, 16)
        except ValueError as e:
            raise ValueError("content_id must be 32-char lowercase hex") from e
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be positive integer")
        if not isinstance(publisher_id, str) or not publisher_id:
            raise ValueError("publisher_id required")
        if not isinstance(manifest_hash, str) or len(manifest_hash) != 64 or manifest_hash != manifest_hash.lower():
            raise ValueError("manifest_hash must be 64-char hex")
        try:
            int(manifest_hash, 16)
        except ValueError as e:
            raise ValueError("invalid manifest_hash") from e
        parent_id, parent_hash = body.get("parent_content_id"), body.get("parent_manifest_hash")
        created = _utc_now()
        manifest_json = json.dumps(_encode_json(envelope), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        media = bytes(media_bytes)
        media_path = str(self.root / "media" / f"{content_id}.bin")
        manifest_path = str(self.root / "manifests" / f"{content_id}.json")
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM contents WHERE content_id=?", (content_id,)).fetchone():
                c.execute("ROLLBACK"); raise ValueError("duplicate content_id")
            if c.execute("SELECT 1 FROM contents WHERE manifest_hash=?", (manifest_hash,)).fetchone():
                c.execute("ROLLBACK"); raise ValueError("duplicate manifest_hash")
            parent = None
            if parent_id is None:
                if parent_hash is not None or version != 1:
                    c.execute("ROLLBACK"); raise ValueError("root version must have null parent and version=1")
            else:
                parent = c.execute("SELECT * FROM contents WHERE content_id=?", (parent_id,)).fetchone()
                if not parent or parent_hash != parent["manifest_hash"]:
                    c.execute("ROLLBACK"); raise ValueError("parent index/hash mismatch")
                if parent["asset_id"] != asset_id or parent["publisher_id"] != publisher_id or version != parent["version"] + 1:
                    c.execute("ROLLBACK"); raise ValueError("parent asset/publisher/version mismatch")
                current = c.execute("SELECT current_content_id FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
                if current and current[0] != parent_id:
                    c.execute("ROLLBACK"); raise ValueError("parent is not current asset version; fork rejected")
            if parent_id is None and c.execute("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)).fetchone():
                c.execute("ROLLBACK"); raise ValueError("asset already has a root version")
            c.execute("INSERT INTO contents VALUES(?,?,?,?,?,?,?,?,?,?)", (content_id, asset_id, version, publisher_id, manifest_hash, manifest_json, sqlite3.Binary(media), media_path, manifest_path, created))
            c.execute("INSERT INTO assets(asset_id,current_content_id) VALUES(?,?) ON CONFLICT(asset_id) DO UPDATE SET current_content_id=excluded.current_content_id", (asset_id, content_id))
            self._bump(c)
            c.execute("COMMIT")
        try:
            Path(media_path).write_bytes(media)
            # Export keeps byte fields in an explicit base64 marker; the DB
            # remains the authoritative copy and ``get_manifest`` decodes it.
            Path(manifest_path).write_text(json.dumps(json.loads(manifest_json), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return {"content_id": content_id, "media_path": media_path, "manifest_path": manifest_path}

    def get_manifest(self, content_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            r = c.execute("SELECT manifest_json FROM contents WHERE content_id=?", (content_id,)).fetchone()
        return _decode_json(json.loads(r[0])) if r else None

    def find_by_hash(self, manifest_hash: str) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT manifest_json FROM contents WHERE manifest_hash=?", (manifest_hash,)).fetchall()
        return [_decode_json(json.loads(r[0])) for r in rows]

    def list_contents(self) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT content_id,asset_id,version,publisher_id,manifest_hash,media_path,manifest_path,created_at FROM contents ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def history(self, content_id: str) -> list[dict[str, Any]]:
        out, seen, cur = [], set(), content_id
        with self._connect() as c:
            for _ in range(64):
                if cur in seen:
                    raise ValueError("cycle in parent chain")
                seen.add(cur)
                r = c.execute("SELECT manifest_json FROM contents WHERE content_id=?", (cur,)).fetchone()
                if not r:
                    raise KeyError(cur)
                env = _decode_json(json.loads(r[0])); out.append(env)
                cur = env.get("body", {}).get("parent_content_id")
                if cur is None:
                    return out
        raise ValueError("parent chain exceeds 64")

    def read_media(self, content_id: str) -> bytes:
        with self._connect() as c:
            r = c.execute("SELECT media FROM contents WHERE content_id=?", (content_id,)).fetchone()
        if not r:
            raise KeyError(content_id)
        return bytes(r[0])

    def current(self, asset_id: str) -> str | None:
        with self._connect() as c:
            r = c.execute("SELECT current_content_id FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return r[0] if r else None

    def revoke(self, target_type: str, target_id: str, reason: str, issuer: str = "registry-demo") -> dict[str, Any]:
        if target_type not in ("content", "key"):
            raise ValueError("target_type must be content or key")
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            seq = self._bump(c); now = _utc_now()
            c.execute("INSERT INTO revocations(sequence,target_type,target_id,reason,issuer,issued_at) VALUES(?,?,?,?,?,?)", (seq, target_type, target_id, reason, issuer, now))
            c.execute("COMMIT")
        return {"sequence": seq, "target_type": target_type, "target_id": target_id, "reason": reason, "issuer": issuer, "issued_at": now}

    def status(self) -> dict[str, Any]:
        with self._connect() as c:
            seq = int(c.execute("SELECT value FROM meta WHERE key='sequence'").fetchone()[0]); issued = c.execute("SELECT value FROM meta WHERE key='issued_at'").fetchone()[0]
            latest = {r[0]: r[1] for r in c.execute("SELECT asset_id,current_content_id FROM assets")}
            revs = [dict(r) for r in c.execute("SELECT sequence,target_type,target_id,reason,issuer,issued_at FROM revocations ORDER BY id")]
        return {"schema_version": 1, "sequence": seq, "issued_at": issued or _utc_now(), "latest": latest, "revoked_content": [r["target_id"] for r in revs if r["target_type"] == "content"], "revoked_keys": [r["target_id"] for r in revs if r["target_type"] == "key"], "revocations": revs}

    def accept_sequence(self, seq: int) -> bool:
        if not isinstance(seq, int) or seq < 0:
            return False
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            high = int(c.execute("SELECT value FROM meta WHERE key='high_water'").fetchone()[0])
            if seq < high:
                c.execute("ROLLBACK"); return False
            if seq > high:
                c.execute("UPDATE meta SET value=? WHERE key='high_water'", (str(seq),))
            c.execute("COMMIT")
        return True
