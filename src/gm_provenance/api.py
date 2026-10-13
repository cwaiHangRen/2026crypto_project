"""本地回环 FastAPI API；没有安装 FastAPI 时导入不会影响 CLI。"""
from __future__ import annotations
import json
import secrets
import os
import tempfile
from pathlib import Path
try:
    from fastapi import FastAPI, File, UploadFile, HTTPException, Body, Header
    from fastapi.responses import HTMLResponse
    from .service import ProvenanceService
    from . import media
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    app = FastAPI(title="gm-provenance", docs_url="/docs")
    service = ProvenanceService(PROJECT_ROOT / "data")
    async def _read_limited(file: UploadFile, limit: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, "文件过大")
            chunks.append(chunk)
        return b"".join(chunks)
    def _require_admin(authorization: str | None) -> None:
        """Require an explicitly provisioned local admin token for writes."""
        expected = os.environ.get("GM_PROVENANCE_ADMIN_TOKEN")
        token_file = service.root / "admin-token"
        if expected is None and token_file.exists():
            expected = token_file.read_text(encoding="utf-8").strip()
        supplied = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else None
        if not expected or not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "需要管理员认证")
    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")
    @app.get("/api/doctor")
    def doctor(): return service.doctor()
    @app.get("/api/contents")
    def contents(): return service.registry.list_contents()

    @app.get("/api/history/{content_id}")
    def history(content_id: str):
        try:
            return service.registry.history(content_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/benchmark")
    def benchmark():
        path = PROJECT_ROOT / "results" / "benchmark-final" / "summary.json"
        if not path.exists():
            raise HTTPException(404, "尚未找到 benchmark 结果，请先运行 CLI benchmark")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/register-demo")
    async def register_demo(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
        """为本地演示生成一次性密钥并登记上传内容；私钥不会落盘。"""
        _require_admin(authorization)
        suffix = Path(file.filename or "upload.bin").suffix.lower() or ".bin"
        with tempfile.TemporaryDirectory(dir=service.root) as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / ("source" + suffix)
            private = tmp_path / "demo-private.pem"
            public = tmp_path / "demo-public.pem"
            source.write_bytes(await _read_limited(file, media.MAX_BYTES))
            password = secrets.token_urlsafe(24)
            try:
                service.keygen(private, public, password)
                service.trust_key(public, "publisher-demo-001")
                result = service.register(source, private, password)
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(400, str(exc)) from exc
        return {k: v for k, v in result.items() if k not in ("manifest", "media_bytes", "watermark_profile")}
    @app.post("/api/verify")
    async def verify(file: UploadFile = File(...), mode: str = "full",
                     decoder_profile: str = "dct-difference-v1"):
        data = await _read_limited(file, media.MAX_BYTES)
        with tempfile.TemporaryDirectory(dir=service.root) as tmp:
            path = Path(tmp) / "api-upload.bin"
            path.write_bytes(data)
            try: return service.verify(path, None, mode, decoder_profile=decoder_profile)
            except (ValueError, FileNotFoundError) as exc: raise HTTPException(400, str(exc)) from exc

    @app.post("/api/transform-preview")
    async def transform_preview(file: UploadFile = File(...), operation: str = "jpeg-reencode",
                                angle: float = 0.0, mirror: bool = False,
                                authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        data = await _read_limited(file, media.MAX_BYTES)
        with tempfile.TemporaryDirectory(dir=service.root) as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.bin"
            transformed = tmp_path / "transformed.bin"
            source.write_bytes(data)
            try:
                transform = service.transform(source, transformed, operation, angle=angle, mirror=mirror)
                verified = service.verify(transformed, None, "full")
                after_bytes = len(transformed.read_bytes())
                previews = {}
                if operation in ("rotate", "mirror_rotate"):
                    import base64
                    import io
                    from PIL import Image
                    for label, path in (("before_image", source), ("after_image", transformed)):
                        with Image.open(path) as image:
                            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                            preview = io.BytesIO()
                            image.save(preview, "PNG")
                        previews[label] = "data:image/png;base64," + base64.b64encode(preview.getvalue()).decode("ascii")
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"transform": transform, "verification": verified,
                "before_bytes": len(data), "after_bytes": after_bytes, **previews}

    @app.post("/api/revoke")
    def revoke(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        if payload.get("confirm") is not True:
            raise HTTPException(403, "撤销写操作需要显式 confirm=true")
        try:
            return service.revoke(str(payload.get("target_type", "")), str(payload.get("target_id", "")), str(payload.get("reason", "web-demo")))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
except ImportError:
    app = None
