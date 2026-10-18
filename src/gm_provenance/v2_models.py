"""Small, serialisable v2 domain models shared by the service and API."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class MediaDescriptor:
    media_type: str
    original_sha256: str
    final_sha256: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    frame_rate: float | None = None
    codec: str | None = None
    page_count: int | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationResult:
    content_id: str | None
    watermark: dict[str, Any]
    sm3: dict[str, Any]
    manifest: dict[str, Any]
    signature: dict[str, Any]
    merkle: dict[str, Any]
    version_chain: dict[str, Any]
    revocation: dict[str, Any]
    decision: str
    reasons: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AttackCase:
    attack_id: str
    media_type: str
    attack_type: str
    parameters: dict[str, Any]
    input_file: str
    expected_result: str
    actual_result: str | None = None
    watermark_recovered: bool | None = None
    digest_match: bool | None = None
    signature_valid: bool | None = None
    version_chain_valid: bool | None = None
    elapsed_ms: float | None = None
    log_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
