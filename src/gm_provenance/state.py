"""Signed registry state snapshots and freshness semantics."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import rfc8785
except Exception:  # pragma: no cover
    rfc8785 = None

try:
    from .manifest import _canonical as _manifest_jcs
except Exception:  # pragma: no cover
    _manifest_jcs = None

DOMAIN = b"GM-PROVENANCE-STATE-v1\x00"


def _jcs(obj: Any) -> bytes:
    if rfc8785 is not None:
        return rfc8785.dumps(obj)
    if _manifest_jcs is not None:
        return _manifest_jcs(obj)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _now(now: datetime | None = None) -> datetime:
    d = now or datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(v: Any) -> datetime:
    if not isinstance(v, str):
        raise ValueError("timestamp must be string")
    x = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return x.astimezone(timezone.utc)


def sign_snapshot(status: dict[str, Any], crypto: Any, private_path: str, passphrase: str, ttl_seconds: int = 300) -> dict[str, Any]:
    """Sign a complete state body with the independent registry state key."""
    if not isinstance(status, dict) or not isinstance(status.get("sequence"), int):
        raise ValueError("status.sequence required")
    if ttl_seconds <= 0 or ttl_seconds > 86400:
        raise ValueError("ttl_seconds out of range")
    issued = _now()
    body = {
        "schema_version": int(status.get("schema_version", 1)),
        "sequence": status["sequence"],
        "issued_at": _iso(issued),
        "expires_at": _iso(issued + timedelta(seconds=ttl_seconds)),
        "latest": status.get("latest", {}),
        "revoked_content": status.get("revoked_content", []),
        "revoked_keys": status.get("revoked_keys", []),
        "revocations": status.get("revocations", []),
    }
    sig = crypto.sign(DOMAIN + _jcs(body), private_path, passphrase)
    if not isinstance(sig, (bytes, bytearray)):
        raise ValueError("crypto.sign must return DER bytes")
    return {"body": body, "signature": base64.b64encode(bytes(sig)).decode("ascii"), "signature_encoding": "base64-DER", "protocol": "GM-PROVENANCE-STATE-v1"}


def verify_snapshot(envelope: dict[str, Any], crypto: Any, public_pem: bytes, high_water: int = 0, now: datetime | None = None) -> dict[str, Any]:
    """Verify syntax, signature, freshness and anti-rollback high-water mark."""
    body = envelope.get("body") if isinstance(envelope, dict) else None
    reasons: list[str] = []
    if not isinstance(body, dict):
        return {"status": "FAIL", "sequence": None, "body": body, "reasons": ["missing body"]}
    if envelope.get("protocol") not in (None, "GM-PROVENANCE-STATE-v1") or envelope.get("signature_encoding") not in (None, "base64-DER"):
        return {"status": "FAIL", "sequence": body.get("sequence"), "body": body, "reasons": ["unsupported envelope"]}
    required = ("schema_version", "sequence", "issued_at", "expires_at", "latest", "revoked_content", "revoked_keys", "revocations")
    if any(k not in body for k in required) or set(body) != set(required):
        return {"status": "FAIL", "sequence": body.get("sequence"), "body": body, "reasons": ["malformed body"]}
    seq = body.get("sequence")
    if not isinstance(seq, int) or seq < 0 or body.get("schema_version") != 1:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["invalid schema or sequence"]}
    if not isinstance(body["latest"], dict) or not isinstance(body["revoked_content"], list) or not isinstance(body["revoked_keys"], list) or not isinstance(body["revocations"], list):
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["invalid state fields"]}
    try:
        issued, expires = _parse_time(body["issued_at"]), _parse_time(body["expires_at"])
    except Exception:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["invalid timestamp"]}
    if expires <= issued:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["invalid expiry"]}
    try:
        raw = envelope.get("signature")
        if isinstance(raw, str):
            sig = base64.b64decode(raw, validate=True)
        elif isinstance(raw, (bytes, bytearray)):
            sig = bytes(raw)
        else:
            raise ValueError
    except Exception:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["invalid signature encoding"]}
    try:
        canonical = _jcs(body)
        valid_sig = bool(crypto.verify(DOMAIN + canonical, sig, public_pem))
    except Exception:
        valid_sig = False
    if not valid_sig:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": ["signature invalid"]}
    t = _now(now)
    if issued > t:
        reasons.append("issued_at in future")
    if seq < high_water:
        reasons.append("sequence rollback")
    if reasons:
        return {"status": "FAIL", "sequence": seq, "body": body, "reasons": reasons}
    if t >= expires:
        return {"status": "UNKNOWN", "sequence": seq, "body": body, "reasons": ["snapshot expired"]}
    return {"status": "PASS", "sequence": seq, "body": body, "reasons": []}
