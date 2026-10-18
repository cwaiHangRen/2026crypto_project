"""SM3 hash-chain audit log with signed checkpoints."""
from __future__ import annotations
import base64, hashlib, json, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
AUDIT_DOMAIN = b"GM-PROVENANCE-AUDIT-v2\x00"
EVENT_DOMAIN = AUDIT_DOMAIN + b"event\x00"
CHECKPOINT_DOMAIN = AUDIT_DOMAIN + b"checkpoint\x00"
def _utc_now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def canonical_bytes(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
def _sm3(data, crypto=None):
    if crypto is not None: return str(crypto.sm3(data))
    try: return hashlib.new("sm3", data).hexdigest()
    except ValueError as exc: raise RuntimeError("SM3 backend is required for audit logs") from exc
class AuditStore:
    """SQLite persistence for append-only events and checkpoints."""
    def __init__(self, root, crypto=None, checkpoint_interval=10):
        self.root=Path(root); self.root.mkdir(parents=True, exist_ok=True); self.db_path=self.root/"audit.sqlite"; self.crypto=crypto; self.checkpoint_interval=max(1,int(checkpoint_interval))
        with self._open() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS audit_events (
              sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
              content_id TEXT, actor TEXT NOT NULL, timestamp TEXT NOT NULL, result TEXT NOT NULL,
              details_json TEXT NOT NULL, previous_event_hash TEXT NOT NULL, event_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_checkpoints (
              checkpoint_id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL,
              last_event_hash TEXT NOT NULL, event_count INTEGER NOT NULL, created_at TEXT NOT NULL,
              signer_key_id TEXT, signature TEXT, signature_encoding TEXT NOT NULL, UNIQUE(last_sequence)
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

    def append(self, event_type, *, content_id=None, actor="system", result="PASS", details=None, timestamp=None, event_id=None, signer=None):
        if not isinstance(event_type,str) or not event_type.strip(): raise ValueError("event_type is required")
        if not isinstance(actor,str) or not actor.strip() or "\n" in actor or "\r" in actor: raise ValueError("actor is invalid")
        if not isinstance(result,str) or not result.strip(): raise ValueError("result is required")
        event_id=event_id or str(uuid.uuid4()); timestamp=timestamp or _utc_now(); details=details or {}
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM audit_events WHERE event_id=?",(event_id,)).fetchone(): db.execute("ROLLBACK"); raise ValueError("duplicate event_id")
            row=db.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone(); sequence=int(row[0])+1 if row else 1; previous=str(row[1]) if row else "0"*64
            payload={"sequence":sequence,"event_id":event_id,"event_type":event_type,"content_id":content_id,"actor":actor,"timestamp":timestamp,"result":result,"details":details,"previous_event_hash":previous}
            event_hash=_sm3(EVENT_DOMAIN+canonical_bytes(payload),self.crypto)
            db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?,?)",(sequence,event_id,event_type,content_id,actor,timestamp,result,json.dumps(details,ensure_ascii=False,sort_keys=True,separators=(",", ":")),previous,event_hash)); db.execute("COMMIT")
        event={**payload,"event_hash":event_hash}
        if sequence%self.checkpoint_interval==0: self.checkpoint(signer=signer)
        return event
    def checkpoint(self, *, signer=None):
        with self._open() as db:
            row=db.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
            if not row: raise ValueError("cannot checkpoint empty audit log")
            sequence,event_hash=int(row[0]),str(row[1]); prior=db.execute("SELECT checkpoint_id FROM audit_checkpoints WHERE last_sequence=?",(sequence,)).fetchone()
        body={"last_sequence":sequence,"last_event_hash":event_hash,"event_count":sequence,"created_at":_utc_now()}; checkpoint_id=_sm3(CHECKPOINT_DOMAIN+canonical_bytes(body),self.crypto)[:32]; signature=None; signer_key_id=None
        if signer:
            if self.crypto is None: raise ValueError("crypto is required for checkpoint signing")
            signer_key_id=signer.get("signer_key_id"); private_path=signer.get("private_path");
            if not signer_key_id or not private_path: raise ValueError("private_path and signer_key_id are required")
            signature=base64.b64encode(self.crypto.sign(CHECKPOINT_DOMAIN+canonical_bytes(body),private_path,signer.get("passphrase", ""))).decode("ascii")
        result={"checkpoint_id":checkpoint_id,**body,"signer_key_id":signer_key_id,"signature":signature,"signature_encoding":"base64-DER"}
        if prior is None:
            with self._open() as db: db.execute("INSERT INTO audit_checkpoints VALUES (?,?,?,?,?,?,?,?)",(checkpoint_id,sequence,event_hash,sequence,body["created_at"],signer_key_id,signature,"base64-DER"))
        return result
    def events(self):
        with self._open() as db: rows=db.execute("SELECT sequence,event_id,event_type,content_id,actor,timestamp,result,details_json,previous_event_hash,event_hash FROM audit_events ORDER BY sequence").fetchall()
        keys=("sequence","event_id","event_type","content_id","actor","timestamp","result","details","previous_event_hash","event_hash"); out=[]
        for row in rows:
            item=dict(zip(keys,row)); item["details"]=json.loads(item["details"]); out.append(item)
        return out
    def checkpoints(self):
        with self._open() as db: rows=db.execute("SELECT checkpoint_id,last_sequence,last_event_hash,event_count,created_at,signer_key_id,signature,signature_encoding FROM audit_checkpoints ORDER BY last_sequence").fetchall()
        keys=("checkpoint_id","last_sequence","last_event_hash","event_count","created_at","signer_key_id","signature","signature_encoding"); return [dict(zip(keys,row)) for row in rows]
    def verify(self, *, public_pem=None, signer_key_id=None):
        events=self.events(); issues=[]; expected_seq=1; previous="0"*64; previous_ts=None; ids=set()
        for event in events:
            seq=int(event["sequence"])
            if seq!=expected_seq: issues.append({"sequence":seq,"type":"sequence_gap","expected":expected_seq})
            expected_seq=seq+1
            if event["event_id"] in ids: issues.append({"sequence":seq,"type":"duplicate_event_id","event_id":event["event_id"]})
            ids.add(event["event_id"])
            if event["previous_event_hash"]!=previous: issues.append({"sequence":seq,"type":"previous_hash_mismatch","expected":previous,"actual":event["previous_event_hash"]})
            payload={k:event[k] for k in ("sequence","event_id","event_type","content_id","actor","timestamp","result","details","previous_event_hash")}; expected_hash=_sm3(EVENT_DOMAIN+canonical_bytes(payload),self.crypto)
            if event["event_hash"]!=expected_hash: issues.append({"sequence":seq,"type":"event_hash_mismatch","expected":expected_hash,"actual":event["event_hash"]})
            if previous_ts and event["timestamp"]<previous_ts: issues.append({"sequence":seq,"type":"timestamp_rollback","previous":previous_ts,"actual":event["timestamp"]})
            previous_ts=event["timestamp"]; previous=event["event_hash"]
        for checkpoint in self.checkpoints():
            matched=next((e for e in events if e["sequence"]==checkpoint["last_sequence"]),None)
            if matched is None: issues.append({"sequence":checkpoint["last_sequence"],"type":"missing_event","checkpoint_id":checkpoint["checkpoint_id"]}); continue
            if matched["event_hash"]!=checkpoint["last_event_hash"]: issues.append({"sequence":checkpoint["last_sequence"],"type":"checkpoint_hash_mismatch","checkpoint_id":checkpoint["checkpoint_id"]})
            if public_pem is not None and checkpoint.get("signature"):
                body={k:checkpoint[k] for k in ("last_sequence","last_event_hash","event_count","created_at")}
                try: valid=bool(self.crypto and self.crypto.verify(CHECKPOINT_DOMAIN+canonical_bytes(body),base64.b64decode(checkpoint["signature"],validate=True),public_pem))
                except Exception: valid=False
                if not valid: issues.append({"sequence":checkpoint["last_sequence"],"type":"checkpoint_signature_invalid","checkpoint_id":checkpoint["checkpoint_id"]})
        return {"valid":not issues,"event_count":len(events),"checkpoint_count":len(self.checkpoints()),"issues":issues,"first_bad_sequence":min((int(i.get("sequence",0)) for i in issues),default=None)}
    def export(self,path):
        payload={"protocol":"GM-PROVENANCE-AUDIT-v2","events":self.events(),"checkpoints":self.checkpoints()}; Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); return {"path":str(path),"event_count":len(payload["events"]),"checkpoint_count":len(payload["checkpoints"]),"verification":self.verify()}
    def verify_chain(self, **kwargs):
        """Compatibility name used by the v2 service/API."""
        return self.verify(**kwargs)


AuditService = AuditStore
def verify_audit_chain(root, crypto=None, **kwargs): return AuditStore(root,crypto=crypto).verify(**kwargs)
