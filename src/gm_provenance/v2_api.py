"""Bounded FastAPI dashboard/API for the v2 demonstration.

The API keeps media bytes in the local v2 store. It enforces upload limits,
magic-byte/MIME consistency, safe metadata names, stable error messages, and
optional Bearer authentication for management writes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse
    from .v2_service import V2Service

    ROOT = Path.cwd() / "data-v2"
    MAX_UPLOAD_BYTES = max(1, int(os.environ.get("GM_V2_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024))))
    _BEARER_TOKEN = os.environ.get("GM_V2_API_BEARER", "").strip()
    _CONTENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
    service = V2Service(ROOT)
    app = FastAPI(title="gm-provenance v2", docs_url="/docs")

    def _public_result(result: dict) -> dict:
        output = dict(result)
        output.pop("media_bytes_value", None)
        return output

    def _error(code: str, message: str, status: int) -> HTTPException:
        # Do not expose backend exception strings, which may contain local
        # paths, usernames, or dependency details.
        return HTTPException(status, detail={"error": code, "message": message})

    def _require_bearer(authorization: str | None) -> None:
        if not _BEARER_TOKEN:
            return
        if not authorization or not authorization.startswith("Bearer ") or authorization[7:].strip() != _BEARER_TOKEN:
            raise _error("unauthorized", "Bearer token required", 401)

    def _safe_filename(name: str | None) -> str:
        candidate = name or "upload.bin"
        if "\x00" in candidate or "/" in candidate or "\\" in candidate or candidate in {".", ".."}:
            raise _error("invalid_filename", "filename must be a plain file name", 400)
        if len(candidate) > 255:
            raise _error("invalid_filename", "filename is too long", 400)
        return candidate

    def _declared_mime_ok(data: bytes, content_type: str | None) -> bool:
        declared = (content_type or "").split(";", 1)[0].strip().lower()
        if not declared or declared == "application/octet-stream":
            return True
        if data.startswith(b"%PDF-"):
            return declared == "application/pdf"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return declared == "image/png"
        if data.startswith(b"\xff\xd8"):
            return declared in {"image/jpeg", "image/jpg"}
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return declared in {"audio/wav", "audio/x-wav"}
        if data.startswith(b"fLaC"):
            return declared == "audio/flac"
        if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xff and data[1] & 0xe0 == 0xe0):
            return declared in {"audio/mpeg", "audio/mp3"}
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return declared == "video/mp4"
        return True

    async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
        name = _safe_filename(file.filename)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise _error("payload_too_large", "upload exceeds configured size limit", 413)
        if not data:
            raise _error("empty_payload", "upload is empty", 400)
        if not _declared_mime_ok(data, file.content_type):
            raise _error("mime_mismatch", "declared MIME does not match media bytes", 415)
        return data, name

    def _validate_content_id(value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not _CONTENT_ID_RE.fullmatch(value):
            raise _error("invalid_content_id", "content_id must be 32 lowercase hexadecimal characters", 400)
        return value

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
    def keygen(payload: dict = Body(...), authorization: str | None = Header(None)):
        _require_bearer(authorization)
        try:
            publisher = str(payload.get("publisher_id", "demo"))
            passphrase = str(payload["passphrase"])
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", publisher) or not 8 <= len(passphrase) <= 4096:
                raise ValueError("invalid keygen fields")
            result = service.key_manager.generate(publisher_id=publisher, passphrase=passphrase)
            service.audit_service().append("keygen", actor=publisher, result="PASS", details={"key_id": result["key_id"]})
            return service.key_manager.sanitized(result)
        except (KeyError, ValueError, OSError) as exc:
            raise _error("invalid_request", "keygen request rejected", 400) from exc

    @app.post("/api/v2/key-revoke")
    def key_revoke(payload: dict = Body(...), authorization: str | None = Header(None)):
        _require_bearer(authorization)
        try:
            key_id = str(payload["key_id"])
            reason = str(payload.get("reason", "api"))[:512]
            result = service.key_manager.revoke(key_id, reason)
            service.audit_service().append("key-revoke", actor="key-admin", result="PASS", details={"key_id": key_id, "reason": reason})
            return service.key_manager.sanitized(result)
        except (KeyError, ValueError) as exc:
            raise _error("invalid_request", "key revoke request rejected", 400) from exc

    @app.get("/api/v2/audit")
    def audit():
        from .audit_service import AuditService
        return {"events": service.audit_events(), "verification": AuditService(ROOT, crypto=service.crypto).verify_chain()}

    @app.post("/api/v2/register")
    async def register(file: UploadFile = File(...), publisher_id: str = Form("demo"), key_id: str | None = Form(None), passphrase: str | None = Form(None), authorization: str | None = Header(None)):
        _require_bearer(authorization)
        try:
            raw, filename = await _read_upload(file)
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", publisher_id):
                raise _error("invalid_publisher", "publisher_id is invalid", 400)
            if passphrase is not None and not 8 <= len(passphrase) <= 4096:
                raise _error("invalid_passphrase", "passphrase length is invalid", 400)
            return _public_result(service.register_bytes(raw, filename=filename, publisher_id=publisher_id, key_id=key_id, passphrase=passphrase))
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError) as exc:
            raise _error("invalid_request", "registration request rejected", 400) from exc

    @app.post("/api/v2/verify")
    async def verify(file: UploadFile = File(...), content_id: str | None = Form(None), batch_id: str | None = Form(None), proof: str = Form("")):
        try:
            raw, _filename = await _read_upload(file)
            return _public_result(service.verify_bytes(raw, content_id=_validate_content_id(content_id), batch_id=batch_id, proof=[x for x in proof.split(",") if x] or None))
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError) as exc:
            raise _error("invalid_request", "verification request rejected", 400) from exc

    @app.post("/api/v2/batches")
    def create_batch(payload: dict, authorization: str | None = Header(None)):
        _require_bearer(authorization)
        try:
            content_ids = payload["content_ids"]
            if not isinstance(content_ids, list) or not 1 <= len(content_ids) <= 256 or any(not _CONTENT_ID_RE.fullmatch(str(x)) for x in content_ids):
                raise ValueError("invalid content_ids")
            return service.create_batch(content_ids, publisher_id=payload.get("publisher_id", "demo"), key_id=payload.get("key_id"))
        except (ValueError, KeyError, TypeError) as exc:
            raise _error("invalid_request", "batch request rejected", 400) from exc

except ImportError:  # pragma: no cover - optional API dependency
    app = None
