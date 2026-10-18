"""V2 unified media adapter boundary.

The adapters expose one structured result for registration/propagation checks without
turning the watermark into a cryptographic proof. Image and PDF use the existing v1
watermark and deterministic PDF raster path. WAV has a small offline PCM LSB carrier;
MP3/FLAC/MP4 stay explicitly unavailable unless an optional codec backend is installed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import struct
import wave
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageEnhance, ImageDraw

from . import media


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class MediaInfo:
    media_type: str
    available: bool
    size_bytes: int
    sha256: str
    details: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class MediaOperation:
    media_type: str
    content_id: str | None
    original_sha256: str
    final_sha256: str | None
    final_bytes: bytes | None
    watermark_recovered: bool | None
    recovered_content_ids: list[str]
    manifest_status: str = "NOT_CHECKED"
    signature_status: str = "NOT_CHECKED"
    version_chain_status: str = "NOT_CHECKED"
    revocation_status: str = "NOT_CHECKED"
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def as_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        out = _jsonable(asdict(self))
        if not include_bytes:
            out.pop("final_bytes", None)
        return out

    def __iter__(self):
        """Compatibility with the v1 ``embed -> (bytes, profile)`` shape."""
        yield self.final_bytes
        yield self.details.get("profile", self.details.get("watermark_profile", {}))

    def get(self, key: str, default: Any = None) -> Any:
        """Expose the detector-like view consumed by v2 service code."""
        if key == "status":
            return "FOUND" if self.watermark_recovered else "NOT_FOUND"
        if key == "content_ids":
            return list(self.recovered_content_ids)
        if key == "details":
            return self.details
        return default

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, None)
        if value is None:
            raise KeyError(key)
        return value


class MediaAdapterError(ValueError):
    pass


class MediaUnavailableError(MediaAdapterError):
    pass


class MediaAdapter(ABC):
    media_type: str
    capability: str = "offline"

    @abstractmethod
    def inspect(self, data: bytes) -> MediaInfo: ...

    @abstractmethod
    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation: ...

    @abstractmethod
    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation: ...

    @abstractmethod
    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes: ...

    def describe(self, raw: bytes, final: bytes | None = None):
        """Build the common descriptor for adapters with no custom fields."""
        from .v2_models import MediaDescriptor
        info = self.inspect(raw); output = final if final is not None else raw; details = info.details
        return MediaDescriptor(media_type=info.media_type, original_sha256=info.sha256,
                               final_sha256=_sha256(output), size_bytes=len(output),
                               width=details.get("width"), height=details.get("height"),
                               duration_seconds=details.get("duration_s"), sample_rate=details.get("sample_rate"),
                               channels=details.get("channels"), frame_rate=details.get("frame_rate"),
                               codec=details.get("codec"), page_count=details.get("page_count"), pages=details.get("pages", []))

    def verify(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation:
        result = self.detect(data, content_id, **kwargs)
        if content_id is not None:
            result.content_id = content_id
            result.watermark_recovered = content_id in result.recovered_content_ids
        return result


class ImageAdapter(MediaAdapter):
    media_type = "image"
    capability = "offline"

    _FORMATS = {"image/png", "image/jpeg"}

    def describe(self, raw: bytes, final: bytes | None = None):
        """Return the shared v2 MediaDescriptor without importing service state."""
        from .v2_models import MediaDescriptor
        info = self.inspect(raw); output = final if final is not None else raw
        return MediaDescriptor(media_type=info.details.get("mime", self.media_type), original_sha256=info.sha256,
                               final_sha256=_sha256(output), size_bytes=len(output), width=info.details.get("width"),
                               height=info.details.get("height"), page_count=info.details.get("page_count"), pages=info.details.get("pages", []))

    def inspect(self, data: bytes) -> MediaInfo:
        try:
            mt = media.sniff(data)
            if mt not in self._FORMATS:
                raise MediaAdapterError("ImageAdapter 只接受 PNG/JPEG")
            im = Image.open(io.BytesIO(data)); im.load()
            return MediaInfo("image", True, len(data), _sha256(data), {
                "mime": mt, "format": im.format, "width": im.width, "height": im.height,
                "mode": im.mode, "pixels": im.width * im.height,
            })
        except Exception as exc:
            if isinstance(exc, MediaAdapterError):
                raise
            raise MediaAdapterError("无法读取图片") from exc

    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data)
        output_format = str(kwargs.get("output_format", "PNG"))
        profile = str(kwargs.get("watermark_profile", "watermark-v1"))
        final, profile_info = media.embed(data, content_id, info.details["mime"], profile)
        detected = media.detect(final, info.details["mime"], kwargs.get("decoder_profile", "dct-difference-v1"))
        recovered = list(detected.get("content_ids") or [])
        return MediaOperation("image", content_id, info.sha256, _sha256(final), final,
                              content_id in recovered, recovered,
                              details={"profile": profile_info, "detector": detected,
                                       "output_format": output_format})

    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data)
        decoder = str(kwargs.get("decoder_profile", "dct-difference-v1"))
        result = media.detect(data, info.details["mime"], decoder)
        recovered = list(result.get("content_ids") or [])
        return MediaOperation("image", None, info.sha256, info.sha256, None,
                              bool(recovered), recovered,
                              details={"decoder_profile": decoder, "detector": result,
                                       "media": info.as_dict()})

    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes:
        params = dict(params or {})
        info = self.inspect(data)
        im = Image.open(io.BytesIO(data)); im.load()
        attack = str(attack_type).lower()
        try:
            if attack in {"jpeg", "jpeg_quality", "reencode"}:
                quality = int(params.get("quality", 70)); out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=max(1, min(100, quality)), optimize=False)
                return out.getvalue()
            if attack in {"png_jpeg", "png_to_jpeg", "png-to-jpeg"}:
                out = io.BytesIO(); im.convert("RGB").save(out, "JPEG", quality=int(params.get("quality", 85)), optimize=False); return out.getvalue()
            if attack in {"resize", "scale"}:
                scale = float(params.get("scale", 0.75)); size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
                im = im.resize(size, Image.Resampling.LANCZOS)
            elif attack == "crop":
                ratio = float(params.get("ratio", 0.9)); w, h = im.size; nw, nh = max(8, round(w * ratio)), max(8, round(h * ratio)); left, top = (w - nw) // 2, (h - nh) // 2; im = im.crop((left, top, left + nw, top + nh))
            elif attack == "rotate":
                im = im.rotate(float(params.get("angle", 2.0)), resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")
            elif attack == "mirror":
                im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            elif attack in {"screenshot", "screen"}:
                # Deterministic screenshot surrogate: render through RGB and a
                # fixed canvas resize, without depending on GUI state.
                im = im.convert("RGB").resize((int(params.get("width", im.width)), int(params.get("height", im.height))), Image.Resampling.LANCZOS)
            elif attack in {"metadata_clear", "strip_metadata"}:
                pass
            elif attack in {"filter", "blur"}:
                from PIL import ImageFilter
                im = im.filter(ImageFilter.GaussianBlur(float(params.get("radius", 1.0))))
            elif attack in {"replace", "local_replace"}:
                im = im.convert("RGB")
                ratio = float(params.get("ratio", 0.12)); w, h = max(1, round(im.width * ratio)), max(1, round(im.height * ratio)); left, top = (im.width - w) // 2, (im.height - h) // 2
                fill = tuple(int(x) for x in params.get("fill", [255, 255, 255])); ImageDraw.Draw(im).rectangle((left, top, left + w, top + h), fill=fill)
            elif attack in {"watermark_copy", "splice"}:
                # Intentional copy attack: duplicate a rectangular watermarked
                # region. The result remains an image and is expected to be
                # classified by the detector, not treated as proof.
                im = im.convert("RGB"); box = (0, 0, im.width // 2, im.height // 2); tile = im.crop(box); im.paste(tile.resize((im.width, im.height), Image.Resampling.BILINEAR), (0, 0))
            else:
                raise MediaAdapterError(f"不支持的图片攻击: {attack_type}")
            out = io.BytesIO(); fmt = "JPEG" if info.details["mime"] == "image/jpeg" else "PNG"
            save_kwargs = {"optimize": False}
            if fmt == "JPEG": save_kwargs["quality"] = int(params.get("quality", 95))
            im.convert("RGB" if fmt == "JPEG" else im.mode).save(out, fmt, **save_kwargs)
            return out.getvalue()
        finally:
            im.close()


class PdfAdapter(MediaAdapter):
    media_type = "pdf"
    capability = "offline-pdfium"

    def inspect(self, data: bytes) -> MediaInfo:
        if not data.startswith(b"%PDF-"):
            raise MediaAdapterError("输入不是 PDF")
        try:
            from .pdf_raster import iter_rendered_pages, PdfRasterConfig
            pages = []
            for p in iter_rendered_pages(data, PdfRasterConfig()):
                try: pages.append({"page": p.page_number, "width": p.expected_size[0], "height": p.expected_size[1], "pixels": p.expected_size[0] * p.expected_size[1]})
                finally: p.image.close()
            return MediaInfo("pdf", True, len(data), _sha256(data), {"page_count": len(pages), "dpi": 120, "pages": pages})
        except Exception as exc:
            raise MediaAdapterError("无法读取 PDF") from exc

    def describe(self, raw: bytes, final: bytes | None = None):
        from .v2_models import MediaDescriptor
        info = self.inspect(raw); out = final if final is not None else raw
        return MediaDescriptor(media_type="application/pdf", original_sha256=info.sha256, final_sha256=_sha256(out), size_bytes=len(out), page_count=info.details.get("page_count"), pages=info.details.get("pages", []))

    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data)
        from .pdf_raster import PdfRasterConfig, process_pdf
        config = PdfRasterConfig(final_format=str(kwargs.get("final_format", "PNG")), jpeg_quality=int(kwargs.get("jpeg_quality", 95)), resize=kwargs.get("resize"))
        result = process_pdf(data, content_id, config=config, output_dir=kwargs.get("output_dir"))
        finals = [page.final_bytes for page in result.pages]
        # Keep a complete PDF container for the service while retaining the
        # page-level deterministic raster evidence in ``pdf_raster``.
        published, profile = media.embed(data, content_id, "application/pdf", "watermark-v1")
        detected = media.detect(published, "application/pdf")
        recovered = list(detected.get("content_ids") or [])
        return MediaOperation("pdf", content_id, info.sha256, _sha256(published), published,
                              content_id in recovered and result.registration_allowed, recovered,
                              details={"profile": profile, "detector": detected, "pdf_raster": result.as_dict(),
                                       "page_artifacts": [_sha256(x) for x in finals],
                                       "registration_allowed": result.registration_allowed})

    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data)
        result = media.detect(data, "application/pdf", kwargs.get("decoder_profile", "dct-difference-v1"))
        recovered = list(result.get("content_ids") or [])
        return MediaOperation("pdf", None, info.sha256, info.sha256, None, bool(recovered), recovered,
                              details={"detector": result, "media": info.as_dict()})

    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes:
        params = dict(params or {}); attack = str(attack_type).lower()
        if attack in {"rasterize", "screenshot", "reencode"}:
            from .pdf_raster import iter_rendered_pages, PdfRasterConfig
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
            out = io.BytesIO(); c = canvas.Canvas(out)
            for page in iter_rendered_pages(data, PdfRasterConfig(final_format="PNG")):
                try:
                    c.setPageSize((page.expected_size[0], page.expected_size[1])); c.drawImage(ImageReader(io.BytesIO(page.rendered_bytes)), 0, 0, width=page.expected_size[0], height=page.expected_size[1]); c.showPage()
                finally: page.image.close()
            c.save(); return out.getvalue()
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:
            raise MediaUnavailableError("PDF 攻击需要 pypdf") from exc
        reader = PdfReader(io.BytesIO(data)); writer = PdfWriter(); pages = list(reader.pages)
        if attack in {"page_delete", "delete_page"}:
            index = int(params.get("page", len(pages) - 1)); pages = [p for i, p in enumerate(pages) if i != index]
        elif attack in {"page_reorder", "reorder"}:
            order = list(params.get("order", reversed(range(len(pages))))); pages = [pages[int(i)] for i in order if 0 <= int(i) < len(pages)]
        elif attack in {"metadata_clear", "strip_metadata"}:
            pass
        elif attack in {"content_replace", "replace"}:
            # Replacing page content without a PDF editor is intentionally
            # represented by deterministic blank page replacement.
            index = int(params.get("page", 0));
            if pages:
                from reportlab.pdfgen import canvas
                blank = io.BytesIO(); c = canvas.Canvas(blank); c.showPage(); c.save(); pages[index] = PdfReader(io.BytesIO(blank.getvalue())).pages[0]
        elif attack in {"rollback", "old_version"}:
            pages = pages[:1]
        else:
            raise MediaAdapterError(f"不支持的 PDF 攻击: {attack_type}")
        if attack in {"metadata_clear", "strip_metadata"}:
            writer.add_metadata({})
        for page in pages: writer.add_page(page)
        out = io.BytesIO(); writer.write(out); return out.getvalue()


class WavAdapter(MediaAdapter):
    media_type = "audio/wav"
    capability = "offline-pcm16"
    _MAGIC = b"GMV2WAV1"

    def inspect(self, data: bytes) -> MediaInfo:
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                details = {"channels": w.getnchannels(), "sample_width": w.getsampwidth(), "sample_rate": w.getframerate(), "frames": w.getnframes(), "duration_s": w.getnframes() / max(1, w.getframerate()), "compression": w.getcomptype()}
            if details["sample_width"] != 2:
                return MediaInfo(self.media_type, False, len(data), _sha256(data), details, "当前离线嵌入仅支持 PCM16")
            return MediaInfo(self.media_type, True, len(data), _sha256(data), details)
        except Exception as exc:
            raise MediaAdapterError("无法读取 WAV") from exc

    @staticmethod
    def _frames(data: bytes) -> tuple[dict[str, Any], bytearray]:
        with wave.open(io.BytesIO(data), "rb") as w:
            params = {"channels": w.getnchannels(), "width": w.getsampwidth(), "rate": w.getframerate(), "frames": w.getnframes(), "comptype": w.getcomptype(), "compname": w.getcompname()}
            raw = bytearray(w.readframes(w.getnframes()))
        return params, raw

    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data)
        if not info.available: raise MediaUnavailableError(info.reason or "WAV 不可用")
        if len(content_id) != 32: raise ValueError("content_id 必须为 32 位十六进制")
        params, raw = self._frames(data); payload = self._MAGIC + bytes.fromhex(content_id); bits = [(b >> shift) & 1 for b in payload for shift in range(7, -1, -1)]
        if len(raw) < len(bits): raise MediaAdapterError("WAV 样本不足以承载 ContentID")
        for i, bit in enumerate(bits): raw[i] = (raw[i] & 0xFE) | bit
        out = io.BytesIO();
        with wave.open(out, "wb") as w: w.setnchannels(params["channels"]); w.setsampwidth(params["width"]); w.setframerate(params["rate"]); w.writeframes(raw)
        final = out.getvalue(); detected = self.detect(final); detected.content_id = content_id; detected.final_bytes = final; detected.final_sha256 = _sha256(final); detected.original_sha256 = info.sha256; detected.watermark_recovered = content_id in detected.recovered_content_ids; detected.details["embedding"] = {"bits": len(bits), "algorithm": "pcm16-lsb-v2"}; return detected

    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation:
        info = self.inspect(data); recovered: list[str] = []
        if info.available:
            _params, raw = self._frames(data); n = len(self._MAGIC) * 8 + 16 * 8
            bits = [raw[i] & 1 for i in range(min(n, len(raw)))]; payload = bytearray()
            for i in range(0, len(bits) - 7, 8): payload.append(sum(bits[i + j] << (7 - j) for j in range(8)))
            if payload[:len(self._MAGIC)] == self._MAGIC and len(payload) >= len(self._MAGIC) + 16: recovered.append(payload[len(self._MAGIC):len(self._MAGIC) + 16].hex())
        return MediaOperation(self.media_type, None, info.sha256, info.sha256, None, bool(recovered), recovered, details={"media": info.as_dict(), "algorithm": "pcm16-lsb-v2"})

    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes:
        params = dict(params or {}); info = self.inspect(data)
        if not info.available: raise MediaUnavailableError(info.reason or "WAV 不可用")
        p, raw = self._frames(data); channels = p["channels"]; samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
        attack = str(attack_type).lower()
        if attack in {"volume", "volume_change"}:
            factor = float(params.get("factor", 0.7)); samples = [max(-32768, min(32767, round(x * factor))) for x in samples]
        elif attack in {"resample", "sample_rate"}:
            factor = float(params.get("factor", 0.8)); src = len(samples) // channels; dst = max(1, round(src * factor)); out = []
            for i in range(dst):
                pos = i / max(1, dst - 1) * max(0, src - 1); j = min(src - 1, int(round(pos))); out.extend(samples[j * channels:(j + 1) * channels])
            samples = out; p["rate"] = max(1, round(p["rate"] * factor))
        elif attack in {"crop", "clip"}:
            ratio = float(params.get("ratio", 0.8)); n = max(1, round(len(samples) * ratio)); samples = samples[:n]
        elif attack in {"transcode", "reencode"}:
            pass
        else: raise MediaAdapterError(f"不支持的 WAV 攻击: {attack_type}")
        raw2 = struct.pack("<" + "h" * len(samples), *samples); out = io.BytesIO()
        with wave.open(out, "wb") as w: w.setnchannels(channels); w.setsampwidth(2); w.setframerate(p["rate"]); w.writeframes(raw2)
        return out.getvalue()


class AudioAdapter(WavAdapter):
    """Offline audio adapter; PCM16 WAV is enabled, codec formats are explicit flags."""
    media_type = "audio"


class VideoAdapter(MediaAdapter):
    """Safe MP4 boundary. External FFmpeg/GStreamer is opt-in and not assumed."""
    media_type = "video/mp4"
    capability = "unavailable"

    def inspect(self, data: bytes) -> MediaInfo:
        return MediaInfo(self.media_type, False, len(data), _sha256(data), {},
                         "MP4 需要可选系统编解码器；离线安全模式未启用外部组件")

    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation:
        raise MediaUnavailableError(self.inspect(data).reason or "MP4 unavailable")

    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation:
        raise MediaUnavailableError(self.inspect(data).reason or "MP4 unavailable")

    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes:
        raise MediaUnavailableError(self.inspect(data).reason or "MP4 unavailable")


class UnavailableAdapter(MediaAdapter):
    def __init__(self, media_type: str, reason: str): self.media_type, self.reason = media_type, reason; self.capability = "unavailable"
    def inspect(self, data: bytes) -> MediaInfo: return MediaInfo(self.media_type, False, len(data), _sha256(data), {}, self.reason)
    def embed(self, data: bytes, content_id: str, **kwargs: Any) -> MediaOperation: raise MediaUnavailableError(self.reason)
    def detect(self, data: bytes, content_id: str | None = None, **kwargs: Any) -> MediaOperation: raise MediaUnavailableError(self.reason)
    def attack(self, data: bytes, attack_type: str, params: dict[str, Any] | None = None) -> bytes: raise MediaUnavailableError(self.reason)


def adapter_for(data: bytes, media_type: str | None = None) -> MediaAdapter:
    mt = (media_type or "").lower()
    if not mt:
        if data.startswith(b"%PDF-"): mt = "application/pdf"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"): mt = "image/png"
        elif data.startswith(b"\xff\xd8"): mt = "image/jpeg"
        elif data.startswith(b"RIFF") and data[8:12] == b"WAVE": mt = "audio/wav"
        elif len(data) >= 12 and data[4:8] == b"ftyp": mt = "video/mp4"
    if mt in ("image/png", "image/jpeg", "image"): return ImageAdapter()
    if mt in ("application/pdf", "pdf"): return PdfAdapter()
    if mt in ("audio/wav", "audio/x-wav", "wav", "audio"): return AudioAdapter()
    if mt in ("audio/mpeg", "audio/mp3", "audio/flac"):
        return UnavailableAdapter(mt, "该格式需要可选系统编解码器；离线安全模式未启用外部组件")
    if mt in ("video/mp4", "video"):
        return VideoAdapter()
    raise MediaAdapterError("无法识别媒体类型")


def inspect_media(data: bytes, media_type: str | None = None) -> MediaInfo:
    return adapter_for(data, media_type).inspect(data)


__all__ = ["MediaAdapter", "MediaAdapterError", "MediaUnavailableError", "MediaInfo", "MediaOperation", "ImageAdapter", "PdfAdapter", "WavAdapter", "AudioAdapter", "VideoAdapter", "UnavailableAdapter", "adapter_for", "inspect_media"]
