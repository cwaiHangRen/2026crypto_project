"""Deterministic batch credentials and Merkle proofs for the v2 API.

The batch layer deliberately operates on already authenticated ContentID or
Manifest hashes.  A Merkle proof is a compact membership witness; it does not
replace the SM2 Manifest signature or the media watermark gate.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import contextmanager
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MERKLE_DOMAIN = b"GM-PROVENANCE-MERKLE-v2\x00"
LEAF_DOMAIN = MERKLE_DOMAIN + b"leaf\x00"
NODE_DOMAIN = MERKLE_DOMAIN + b"node\x00"
BATCH_DOMAIN = MERKLE_DOMAIN + b"batch\x00"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    """Canonical UTF-8 JSON used for batch records and proofs."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(data: bytes, crypto: Any | None = None) -> str:
    if crypto is not None:
        return str(crypto.sm3(data))
    try:
        return hashlib.new("sm3", data).hexdigest()
    except ValueError as exc:  # pragma: no cover - Python builds without SM3
        raise RuntimeError("SM3 backend is required for Merkle proofs") from exc


def _hex_bytes(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("digest must be a 64-character hexadecimal string")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("digest must be hexadecimal") from exc


def leaf_hash(leaf: str, crypto: Any | None = None) -> str:
    if not isinstance(leaf, str) or not leaf:
        raise ValueError("leaf must be a non-empty string")
    return _digest(LEAF_DOMAIN + len(leaf.encode("utf-8")).to_bytes(4, "big") + leaf.encode("utf-8"), crypto)


def node_hash(left: str, right: str, crypto: Any | None = None) -> str:
    _hex_bytes(left); _hex_bytes(right)
    return _digest(NODE_DOMAIN + _hex_bytes(left) + _hex_bytes(right), crypto)


def batch_signing_descriptor(batch: dict[str, Any]) -> dict[str, Any]:
    """Return the exact stable fields covered by a batch signature."""
    return {"leaf_count": int(batch["leaf_count"]), "merkle_root": str(batch["merkle_root"]),
            "created_at": str(batch["created_at"]), "publisher_id": str(batch["publisher_id"]),
            "signer_key_id": batch.get("signer_key_id"),
            "protocol": batch.get("protocol", "GM-PROVENANCE-BATCH-v2")}


@dataclass(frozen=True)
class MerkleProof:
    batch_id: str
    leaf: str
    leaf_hash: str
    index: int
    tree_size: int
    siblings: tuple[str, ...]
    directions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["siblings"] = list(self.siblings)
        result["directions"] = list(self.directions)
        return result


@dataclass(frozen=True)
class BatchCredential:
    batch_id: str
    merkle_root: str
    tree_height: int
    leaf_count: int
    leaves: tuple[str, ...]
    created_at: str
    publisher_id: str
    signer_key_id: str | None = None
    signature: str | None = None
    signature_encoding: str = "base64-DER"
    protocol: str = "GM-PROVENANCE-BATCH-v2"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["leaves"] = list(self.leaves)
        return result


class MerkleTree:
    """Sorted, deterministic binary tree with duplicate-last odd handling."""

    def __init__(self, leaves: Iterable[str], crypto: Any | None = None):
        unique = sorted({str(v) for v in leaves})
        if not unique:
            raise ValueError("at least one leaf is required")
        if any(not v for v in unique):
            raise ValueError("leaf must be non-empty")
        self.crypto = crypto
        self.leaves = tuple(unique)

        self._levels: list[list[str]] = [[leaf_hash(v, crypto) for v in self.leaves]]
        while len(self._levels[-1]) > 1:
            level = self._levels[-1]
            nxt: list[str] = []
            for offset in range(0, len(level), 2):
                right = level[offset + 1] if offset + 1 < len(level) else level[offset]
                nxt.append(node_hash(level[offset], right, crypto))
            self._levels.append(nxt)
        self.root = self._levels[-1][0]

    @property
    def height(self) -> int:
        return len(self._levels) - 1

    def proof(self, leaf: str, batch_id: str) -> MerkleProof:
        try:
            index = self.leaves.index(leaf)
        except ValueError as exc:
            raise KeyError(leaf) from exc
        siblings: list[str] = []
        directions: list[str] = []
        current = index
        for level in self._levels[:-1]:
            sibling = current - 1 if current % 2 else current + 1
            if sibling >= len(level):
                sibling = current  # duplicate-last
            siblings.append(level[sibling])
            directions.append("L" if current % 2 else "R")
            current //= 2
        return MerkleProof(batch_id, leaf, leaf_hash(leaf, self.crypto), index, len(self.leaves), tuple(siblings), tuple(directions))

    @classmethod
    def verify(cls, proof: MerkleProof | dict[str, Any], root: str, crypto: Any | None = None, expected_batch_id: str | None = None) -> bool:
        if isinstance(proof, dict):
            proof = MerkleProof(batch_id=str(proof["batch_id"]), leaf=str(proof["leaf"]), leaf_hash=str(proof["leaf_hash"]), index=int(proof["index"]), tree_size=int(proof["tree_size"]), siblings=tuple(proof["siblings"]), directions=tuple(proof["directions"]))
        if expected_batch_id is not None and proof.batch_id != expected_batch_id:
            return False
        if len(proof.siblings) != len(proof.directions) or proof.tree_size < 1 or not 0 <= proof.index < proof.tree_size:
            return False
        try:
            current = leaf_hash(proof.leaf, crypto)
            if current != proof.leaf_hash:
                return False
            for sibling, direction in zip(proof.siblings, proof.directions):
                current = node_hash(sibling, current, crypto) if direction == "L" else node_hash(current, sibling, crypto) if direction == "R" else ""
                if not current:
                    return False
            return current == root
        except (ValueError, TypeError, KeyError):
            return False


class BatchProofService:
    """Create, persist, and verify signed batch credentials."""

    def __init__(self, root: Path | str | None = None, crypto: Any | None = None):
        self.root = Path(root) if root is not None else None
        self.crypto = crypto
        self.store = BatchProofStore(self.root, crypto=crypto) if self.root else None

    def create_batch(self, leaves: Iterable[str], publisher_id: str, *, signer_key_id: str | None = None,
                    private_path: str | Path | None = None, passphrase: str | None = None,
                    created_at: str | None = None) -> dict[str, Any]:
        tree = MerkleTree(leaves, self.crypto)
        created = created_at or _utc_now()
        descriptor = batch_signing_descriptor({"leaf_count": len(tree.leaves), "merkle_root": tree.root, "created_at": created, "publisher_id": publisher_id, "signer_key_id": signer_key_id, "protocol": "GM-PROVENANCE-BATCH-v2"})
        batch_id = _digest(BATCH_DOMAIN + canonical_bytes(descriptor), self.crypto)[:32]
        signature = None
        if private_path is not None:
            if self.crypto is None or not signer_key_id:
                raise ValueError("crypto and signer_key_id are required for a signed batch")
            signature = base64.b64encode(self.crypto.sign(BATCH_DOMAIN + canonical_bytes(descriptor), private_path, passphrase or "")).decode("ascii")
        credential = BatchCredential(batch_id, tree.root, tree.height, len(tree.leaves), tree.leaves, created, publisher_id, signer_key_id, signature)
        result = credential.as_dict()
        if self.store:
            self.store.save_batch(result)
        return result

    def verify_batch_signature(self, batch: dict[str, Any] | BatchCredential, public_pem: bytes) -> bool:
        data = batch.as_dict() if isinstance(batch, BatchCredential) else dict(batch)
        signature = data.get("signature")
        if not signature or self.crypto is None:
            return False
        try:
            return bool(self.crypto.verify(BATCH_DOMAIN + canonical_bytes(batch_signing_descriptor(data)), base64.b64decode(signature, validate=True), public_pem))
        except Exception:
            return False

    def proof(self, batch: dict[str, Any] | BatchCredential, leaf: str) -> dict[str, Any]:
        data = batch.as_dict() if isinstance(batch, BatchCredential) else dict(batch)
        tree = MerkleTree(data["leaves"], self.crypto)
        if tree.root != data["merkle_root"]:
            raise ValueError("batch leaves do not match merkle_root")
        result = tree.proof(leaf, data["batch_id"]).as_dict()
        if self.store:
            self.store.save_proof(result)
        return result

    def verify_proof(self, batch: dict[str, Any] | BatchCredential, proof: dict[str, Any] | MerkleProof, *, expected_leaf: str | None = None) -> dict[str, Any]:
        data = batch.as_dict() if isinstance(batch, BatchCredential) else dict(batch)
        leaf = proof.leaf if isinstance(proof, MerkleProof) else proof.get("leaf")
        valid = expected_leaf is None or leaf == expected_leaf
        valid = valid and MerkleTree.verify(proof, data["merkle_root"], self.crypto, expected_batch_id=data["batch_id"])
        return {"valid": bool(valid), "batch_id": data.get("batch_id"), "leaf": leaf, "merkle_root": data.get("merkle_root"), "reason": "ok" if valid else "proof_mismatch"}

    # Stable short aliases for integrations that expose a generic batch API.
    create = create_batch
    generate_proof = proof
    verify = verify_proof


class BatchProofStore:
    """Small SQLite persistence layer independent from the v1 registry tables."""

    def __init__(self, root: Path | str, crypto: Any | None = None):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "batch_proof.sqlite"
        self.crypto = crypto
        with self._open() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS batch_credentials (
              batch_id TEXT PRIMARY KEY, merkle_root TEXT NOT NULL, tree_height INTEGER NOT NULL,
              leaf_count INTEGER NOT NULL, leaves_json TEXT NOT NULL, created_at TEXT NOT NULL,
              publisher_id TEXT NOT NULL, signer_key_id TEXT, signature TEXT,
              signature_encoding TEXT NOT NULL, protocol TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS merkle_proofs (
              batch_id TEXT NOT NULL, leaf TEXT NOT NULL, proof_json TEXT NOT NULL,
              created_at TEXT NOT NULL, PRIMARY KEY(batch_id, leaf),
              FOREIGN KEY(batch_id) REFERENCES batch_credentials(batch_id)
            );
            """)

    @contextmanager
    def _open(self):
        db = sqlite3.connect(self.db_path)
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()

    def save_batch(self, batch: dict[str, Any]) -> None:
        with self._open() as db:
            db.execute("INSERT OR REPLACE INTO batch_credentials VALUES (?,?,?,?,?,?,?,?,?,?,?)", (batch["batch_id"], batch["merkle_root"], batch["tree_height"], batch["leaf_count"], json.dumps(batch["leaves"], ensure_ascii=False, separators=(",", ":")), batch["created_at"], batch["publisher_id"], batch.get("signer_key_id"), batch.get("signature"), batch.get("signature_encoding", "base64-DER"), batch.get("protocol", "GM-PROVENANCE-BATCH-v2")))

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._open() as db:
            row = db.execute("SELECT * FROM batch_credentials WHERE batch_id=?", (batch_id,)).fetchone()
        if not row: return None
        keys = ["batch_id", "merkle_root", "tree_height", "leaf_count", "leaves_json", "created_at", "publisher_id", "signer_key_id", "signature", "signature_encoding", "protocol"]
        out = dict(zip(keys, row)); out["leaves"] = json.loads(out.pop("leaves_json")); return out

    def save_proof(self, proof: dict[str, Any]) -> None:
        with self._open() as db:
            db.execute("INSERT OR REPLACE INTO merkle_proofs VALUES (?,?,?,?)", (proof["batch_id"], proof["leaf"], json.dumps(proof, ensure_ascii=False, separators=(",", ":")), _utc_now()))

    def get_proof(self, batch_id: str, leaf: str) -> dict[str, Any] | None:
        with self._open() as db:
            row = db.execute("SELECT proof_json FROM merkle_proofs WHERE batch_id=? AND leaf=?", (batch_id, leaf)).fetchone()
        return json.loads(row[0]) if row else None


def create_batch(leaves: Iterable[str], publisher_id: str, **kwargs: Any) -> dict[str, Any]:
    return BatchProofService(kwargs.pop("root", None), kwargs.pop("crypto", None)).create_batch(leaves, publisher_id, **kwargs)


def build_batch(leaves: Iterable[str], publisher_id: str = "publisher-v2", **kwargs: Any) -> dict[str, Any]:
    """Short API alias used by the v2 service and dashboard."""
    return create_batch(leaves, publisher_id, **kwargs)


def verify_proof(batch: dict[str, Any] | str, proof: dict[str, Any] | list[str], crypto: Any | None = None) -> dict[str, Any]:
    """Verify a proof using either the full batch or ``(leaf, proof, root)``.

    The latter shape is retained for the lightweight v2 service.  It accepts
    a complete proof object; a bare sibling list is rejected because its
    left/right directions are deliberately part of the authenticated proof.
    """
    if isinstance(batch, str):
        root = crypto if isinstance(crypto, str) else None
        if not root or not isinstance(proof, dict):
            return {"valid": False, "leaf": batch, "merkle_root": root, "reason": "proof_format_required"}
        valid = MerkleTree.verify(proof, root, expected_batch_id=None) and proof.get("leaf") == batch
        return {"valid": bool(valid), "leaf": batch, "merkle_root": root, "reason": "ok" if valid else "proof_mismatch"}
    return BatchProofService(crypto=crypto).verify_proof(batch, proof)


def proof_for(batch: dict[str, Any], leaf: str, crypto: Any | None = None) -> dict[str, Any]:
    """Compatibility helper for the v2 CLI.

    A persisted batch must retain its sorted leaf list (``leaves`` or
    ``manifest.leaves``) so a proof can be regenerated offline.
    """
    leaves = batch.get("leaves")
    if leaves is None and isinstance(batch.get("manifest"), dict):
        leaves = batch["manifest"].get("leaves")
    if leaves is None:
        raise ValueError("batch does not contain its deterministic leaf list")
    return BatchProofService(crypto=crypto).proof({**batch, "leaves": leaves}, leaf)
