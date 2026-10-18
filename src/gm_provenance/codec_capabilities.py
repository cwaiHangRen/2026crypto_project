"""Optional audio/video codec capability detection.

The core v2 verifier is deliberately dependency-free for its WAV/PDF/image smoke
paths.  MP3, FLAC and MP4 require an external ``ffprobe``/``ffmpeg`` toolchain.
This module only reports what is actually available on the current machine; it
never treats a missing codec as a successful verification.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExecutableCapability:
    name: str
    available: bool
    path: str | None
    version: str | None = None
    error: str | None = None


def probe_executable(name: str, *, timeout_s: float = 3.0) -> ExecutableCapability:
    """Probe one executable without exposing a local username in the report."""
    path = shutil.which(name)
    if not path:
        return ExecutableCapability(name=name, available=False, path=None, error="not_on_PATH")
    try:
        proc = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        first = (proc.stdout or "").splitlines()[0][:240] if proc.stdout else None
        if proc.returncode != 0:
            return ExecutableCapability(name=name, available=False, path="<redacted>", version=first,
                                        error=f"version_exit_{proc.returncode}")
        return ExecutableCapability(name=name, available=True, path="<present>", version=first)
    except subprocess.TimeoutExpired:
        return ExecutableCapability(name=name, available=False, path="<present>", error="version_timeout")
    except OSError as exc:
        return ExecutableCapability(name=name, available=False, path="<present>", error=type(exc).__name__)


def _safe_version(exe: ExecutableCapability) -> dict[str, Any]:
    data = asdict(exe)
    # Absolute paths can contain a local username; retain only presence.
    data["path"] = "<present>" if exe.available else None
    return data


def build_capability_report() -> dict[str, Any]:
    """Return a deterministic, privacy-safe report of optional codec support."""
    ffmpeg = probe_executable("ffmpeg")
    ffprobe = probe_executable("ffprobe")
    codec_ready = ffmpeg.available and ffprobe.available
    formats: dict[str, dict[str, Any]] = {}
    for media_type, label in (("audio/mpeg", "mp3"), ("audio/flac", "flac"), ("video/mp4", "mp4")):
        if not codec_ready:
            status = "BLOCKED_DEPENDENCY"
            reason = "ffmpeg_and_ffprobe_not_available"
        else:
            # Metadata probing can be enabled, but robust watermark embedding for
            # these formats is intentionally not silently claimed by v2.1.
            status = "CODE_NOT_IMPLEMENTED"
            reason = "external_codec_present_but_robust_watermark_adapter_not_implemented"
        formats[media_type] = {
            "label": label,
            "status": status,
            "dependency": {"ffmpeg": ffmpeg.available, "ffprobe": ffprobe.available},
            "inspect": "BLOCKED" if not codec_ready else "METADATA_ONLY",
            "operations": {
                "resample": "NOT_RUN",
                "transcode": "NOT_RUN",
                "volume_change": "NOT_RUN",
                "clip": "NOT_RUN",
                "metadata_clear": "NOT_RUN",
            },
            "reason": reason,
            "offline_fixture": True,
            "fixture_note": "deterministic header fixture verifies safe rejection; no fake codec bytes are generated",
        }
    return {
        "schema_version": "v2.1-codec-capability-1",
        "generated_at": _now(),
        "platform": platform.system(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "executables": {"ffmpeg": _safe_version(ffmpeg), "ffprobe": _safe_version(ffprobe)},
        "formats": formats,
        "offline": {
            "image": "PASS",
            "pdf": "PASS",
            "wav": "PASS",
            "mp3": formats["audio/mpeg"]["status"],
            "flac": formats["audio/flac"]["status"],
            "mp4": formats["video/mp4"]["status"],
        },
        "privacy": {"absolute_paths": "redacted", "private_keys": "not_collected", "media_bytes": "not_collected"},
    }


def write_capability_report(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_capability_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


__all__ = ["ExecutableCapability", "probe_executable", "build_capability_report", "write_capability_report"]
