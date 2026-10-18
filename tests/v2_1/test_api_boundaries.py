from __future__ import annotations

import asyncio
import pytest


def test_filename_is_basename_and_nul_safe():
    from gm_provenance.v2_api import _safe_filename
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _safe_filename(r"..\..\secret.pem")
    with pytest.raises(HTTPException):
        _safe_filename("bad\x00name.png")


def test_upload_size_boundary_is_explicit():
    from gm_provenance.v2_api import MAX_UPLOAD_BYTES, _read_upload

    class Upload:
        filename = "oversize.bin"
        content_type = "application/octet-stream"
        async def read(self, n):
            return b"x" * (MAX_UPLOAD_BYTES + 1)

    try:
        from fastapi import HTTPException
        asyncio.run(_read_upload(Upload()))
    except HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("oversize upload was accepted")
