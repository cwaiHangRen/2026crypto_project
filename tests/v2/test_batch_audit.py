import json
import sqlite3
from pathlib import Path

from gm_provenance.batch_proof import BatchProofService, BatchProofStore, MerkleTree, canonical_bytes
from gm_provenance.audit_service import AuditStore


def test_merkle_batch_and_proof_roundtrip(tmp_path):
    service = BatchProofService(tmp_path)
    leaves = ["cid-c", "cid-a", "cid-b", "cid-d", "cid-e"]
    batch = service.create_batch(leaves, "publisher-v2")
    assert batch["leaves"] == sorted(leaves)
    assert batch["leaf_count"] == 5
    proof = service.proof(batch, "cid-d")
    assert service.verify_proof(batch, proof)["valid"] is True
    assert service.verify_proof(batch, proof, expected_leaf="cid-a")["valid"] is False
    proof["batch_id"] = "0" * 32
    assert service.verify_proof(batch, proof)["valid"] is False


def test_merkle_tamper_root_and_cross_batch_rejected():
    tree = MerkleTree(["a", "b", "c"])
    proof = tree.proof("b", "batch-a").as_dict()
    assert MerkleTree.verify(proof, tree.root, expected_batch_id="batch-a")
    proof["siblings"][0] = "0" * 64
    assert not MerkleTree.verify(proof, tree.root, expected_batch_id="batch-a")
    proof = tree.proof("b", "batch-a").as_dict()
    assert not MerkleTree.verify(proof, tree.root, expected_batch_id="batch-b")


def test_batch_sqlite_persistence(tmp_path):
    service = BatchProofService(tmp_path)
    batch = service.create_batch(["x", "y"], "publisher")
    proof = service.proof(batch, "x")
    store = BatchProofStore(tmp_path)
    assert store.get_batch(batch["batch_id"])["merkle_root"] == batch["merkle_root"]
    assert store.get_proof(batch["batch_id"], "x")["leaf"] == "x"


def test_audit_chain_and_checkpoint(tmp_path):
    audit = AuditStore(tmp_path, checkpoint_interval=2)
    first = audit.append("register", content_id="a", actor="tester", details={"media_type": "image/png"})
    audit.append("verify", content_id="a", actor="tester", result="PASS")
    audit.append("revoke", content_id="a", actor="tester", result="FAIL")
    checked = audit.verify()
    assert checked["valid"] is True
    assert checked["event_count"] == 3
    assert checked["checkpoint_count"] == 1
    exported = audit.export(tmp_path / "audit.json")
    assert exported["verification"]["valid"] is True
    payload = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert payload["events"][0]["previous_event_hash"] == "0" * 64


def test_audit_tamper_reports_first_node(tmp_path):
    audit = AuditStore(tmp_path, checkpoint_interval=10)
    audit.append("register", actor="tester")
    audit.append("verify", actor="tester", result="PASS")
    with sqlite3.connect(audit.db_path) as db:
        db.execute("UPDATE audit_events SET result='FAIL' WHERE sequence=2")
    result = audit.verify()
    assert result["valid"] is False
    assert result["first_bad_sequence"] == 2
    assert any(i["type"] == "event_hash_mismatch" for i in result["issues"])


def test_audit_duplicate_event_and_timestamp_rollback(tmp_path):
    audit = AuditStore(tmp_path)
    audit.append("register", actor="tester", event_id="fixed", timestamp="2026-01-02T00:00:00Z")
    try:
        audit.append("verify", actor="tester", event_id="fixed")
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate event_id must be rejected")
    audit.append("verify", actor="tester", timestamp="2025-01-01T00:00:00Z")
    result = audit.verify()
    assert result["valid"] is False
    assert any(i["type"] == "timestamp_rollback" for i in result["issues"])
