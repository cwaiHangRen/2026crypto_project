"""Optional FastAPI dashboard/API for v2; importing v1 API is not required."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Body
    from fastapi.responses import HTMLResponse
    from .v2_service import V2Service

    ROOT = Path.cwd() / "data-v2"
    service = V2Service(ROOT)
    app = FastAPI(title="gm-provenance v2", docs_url="/docs")

    def _public_result(result: dict) -> dict:
        """Keep binary published bytes in the local store, never in JSON API output."""
        output = dict(result)
        output.pop("media_bytes_value", None)
        return output

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (Path(__file__).parent / "ui" / "v2.html").read_text(encoding="utf-8")

    @app.get("/api/v2/contents")
    def contents():
        return service.store.list_contents()

    @app.get("/api/v2/key-status")
    def key_status():
        return service.key_manager.public_status()

    @app.post("/api/v2/keygen")
    def keygen(payload: dict = Body(...)):
        try:
            result = service.key_manager.generate(publisher_id=str(payload.get("publisher_id", "demo")), passphrase=str(payload["passphrase"]))
            service.audit_service().append("keygen", actor=str(payload.get("publisher_id", "demo")), result="PASS", details={"key_id": result["key_id"]})
            return service.key_manager.sanitized(result)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v2/key-revoke")
    def key_revoke(payload: dict = Body(...)):
        try:
            result = service.key_manager.revoke(str(payload["key_id"]), str(payload.get("reason", "api")))
            service.audit_service().append("key-revoke", actor="key-admin", result="PASS", details={"key_id": payload["key_id"], "reason": payload.get("reason", "api")})
            return service.key_manager.sanitized(result)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v2/audit")
    def audit():
        from .audit_service import AuditService
        return {"events": service.audit_events(), "verification": AuditService(ROOT, crypto=service.crypto).verify_chain()}

    @app.post("/api/v2/register")
    async def register(file: UploadFile = File(...), publisher_id: str = Form("demo"), key_id: str | None = Form(None)):
        raw = await file.read()
        try:
            return _public_result(service.register_bytes(raw, filename=file.filename or "upload.bin", publisher_id=publisher_id, key_id=key_id))
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v2/verify")
    async def verify(file: UploadFile = File(...), content_id: str | None = Form(None), batch_id: str | None = Form(None), proof: str = Form("")):
        raw = await file.read()
        try:
            return service.verify_bytes(raw, content_id=content_id, batch_id=batch_id, proof=[x for x in proof.split(",") if x] or None)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v2/batches")
    def create_batch(payload: dict):
        try:
            return service.create_batch(payload["content_ids"], publisher_id=payload.get("publisher_id", "demo"), key_id=payload.get("key_id"))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

except ImportError:  # pragma: no cover - optional API dependency
    app = None
