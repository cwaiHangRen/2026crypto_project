"""固定小型四组评测与攻击矩阵。

该模块是工程 smoke benchmark，不是性能或数据集达标声明。它生成少量合成图片，
使用真实的 :class:`ProvenanceService`、Manifest 和水印实现，并把评测真值留在
评测器记录中；验证器只收到媒体和（需要时）sidecar Manifest。
"""
from __future__ import annotations

import io
import json
import random
import statistics
import time
import base64
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw

from . import manifest, media
from .service import ProvenanceService

GROUPS = ("G0", "G1", "G2", "G3")
DEFAULT_SEED = 20261005


def _default_config() -> dict[str, Any]:
    return {
        "seed": DEFAULT_SEED,
        "images": {
            "jpeg_reencode": [90, 75, 50],
            "resize": [0.5, 0.75, 1.5],
            "crop_keep_area": [0.95, 0.90],
            "metadata_clear": True,
            "screenshot": {"viewport": [768, 768], "simulated": True},
        },
        "documents": {
            "pdf_render_dpi": [96, 120, 160],
            "page_delete": True,
            "page_reorder": True,
            "screenshot": {"simulated": True},
        },
    }


def load_attack_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取配置；没有 PyYAML 时对本项目的简单 YAML 做保守解析。"""
    defaults = _default_config()
    if path is None:
        return defaults
    path = Path(path)
    if not path.exists():
        return defaults
    try:
        import yaml  # type: ignore
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return _merge(defaults, value)
    except Exception:
        pass
    # 仅用于离线 bundled runtime：提取 seed、布尔开关和数值数组。
    import re
    text = path.read_text(encoding="utf-8")
    for key, raw in re.findall(r"^\s{2,}([A-Za-z_]+):\s*(\[[^\n]+\]|true|false|simulated_only)\s*$", text, re.M):
        if key == "seed":
            continue
        if raw.startswith("["):
            try: value = json.loads(raw.replace("'", '"'))
            except Exception: continue
        elif raw in ("true", "false"):
            value = raw == "true"
        else:
            value = raw
        for section in ("images", "documents"):
            if key in defaults[section]: defaults[section][key] = value
    m = re.search(r"^seed:\s*(\d+)", text, re.M)
    if m: defaults["seed"] = int(m.group(1))
    return defaults


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _fixture(path: Path, index: int, fmt: str = "PNG") -> None:
    image = Image.new("RGB", (768, 768), (245, 248, 252))
    draw = ImageDraw.Draw(image)
    hue = ((index * 53) % 180, 78 + (index * 19) % 100, 121 + (index * 11) % 90)
    draw.rounded_rectangle((48, 48, 720, 720), 28, fill=hue)
    draw.rectangle((120, 150, 648, 618), outline=(255, 255, 255), width=8)
    draw.text((180, 348), f"GM fixture {index:02d}", fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format=fmt, quality=95 if fmt == "JPEG" else None)


def _fixture_bytes(index: int, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (768, 768), (245, 248, 252)); draw = ImageDraw.Draw(image)
    hue = ((index * 53) % 180, 78 + (index * 19) % 100, 121 + (index * 11) % 90)
    draw.rounded_rectangle((48, 48, 720, 720), 28, fill=hue); draw.rectangle((120, 150, 648, 618), outline=(255, 255, 255), width=8); draw.text((180, 348), f"GM fixture {index:02d}", fill="white")
    out = io.BytesIO(); image.save(out, format=fmt, quality=95 if fmt == "JPEG" else None); return out.getvalue()


def _bytes_image(data: bytes, operation: str, value: Any = None) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    if operation == "jpeg_reencode":
        im.save(out, "JPEG", quality=int(value))
    elif operation == "resize":
        ratio = float(value)
        im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.Resampling.LANCZOS).save(out, "PNG")
    elif operation == "crop":
        keep = float(value); side = max(1, int(min(im.size) * keep)); left = (im.width - side) // 2; top = (im.height - side) // 2
        im.crop((left, top, left + side, top + side)).save(out, "PNG")
    elif operation == "metadata_clear":
        # Re-encoding without copying info removes EXIF/text metadata.
        im.save(out, "PNG")
    elif operation == "screenshot_simulated":
        # A deterministic render/scale/encode approximation; never labelled real screenshot.
        im.resize((768, 768), Image.Resampling.LANCZOS).save(out, "JPEG", quality=85)
    else:
        raise ValueError(f"未知图片变换: {operation}")
    return out.getvalue()


def _signed_only(service: ProvenanceService, source: Path, private: Path, password: str, output_root: Path) -> dict[str, Any]:
    """创建不含水印的合法签名凭证，供 G1 使用。"""
    original = source.read_bytes(); media_type = media.sniff(original)
    content_id = __import__("secrets").token_hex(16); asset_id = __import__("secrets").token_hex(16)
    public = service._public_from_private(private, password); key_id = service.crypto.key_id(public)
    service.registry.add_key(public, "publisher-benchmark", True, service.crypto)
    metadata = {"source_name": source.name, "source_size": len(original), "watermark_media_profile": {"algorithm": "none", "reason": "G1"}}
    body = {
        "schema_version": "1.0", "asset_id": asset_id, "content_id": content_id, "version": 1,
        "publisher_id": "publisher-benchmark", "signer_key_id": key_id,
        "generator": {"name": "gm-provenance", "version": "0.1.0", "method": "benchmark"},
        "created_at": "2026-01-01T00:00:00Z", "media_type": media_type, "content_size": len(original),
        "content_hash_alg": "SM3", "content_hash": service.crypto.sm3(original), "metadata": metadata,
        "metadata_hash": service.crypto.sm3(manifest._canonical(metadata)), "parent_content_id": None,
        "parent_manifest_hash": None, "transformation": {"operation": "publish"},
        "allowed_transformations": ["jpeg-reencode", "resize"], "watermark_profile": {"algorithm": "none"},
        "signature_suite": "SM2-SM3-DER", "sm2_user_id": service.crypto.DEFAULT_USER_ID if hasattr(service.crypto, "DEFAULT_USER_ID") else "1234567812345678",
    }
    # service.crypto has no DEFAULT_USER_ID attribute; manifest.sign_manifest supplies the protocol constant.
    body["sm2_user_id"] = "1234567812345678"
    envelope = manifest.sign_manifest(body, service.crypto, private, password)
    mh = manifest.manifest_hash(body, service.crypto)
    paths = service.registry.publish(envelope, original, mh)
    return {"content_id": content_id, "manifest_path": paths["manifest_path"], "media_path": paths["media_path"], "manifest": envelope}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _attack_records(service: ProvenanceService, source: Path, g3: dict[str, Any], root: Path,
                    sample_id: str) -> list[dict[str, Any]]:
    """运行少量攻击样本；攻击真值只在返回记录中保存。"""
    rows: list[dict[str, Any]] = []
    original_manifest = json.loads(Path(g3["manifest_path"]).read_text(encoding="utf-8"))

    def check(name: str, media_path: Path, manifest_path: Path | None, expected: str, expected_field: str | None = None,
              simulated: bool = False) -> None:
        started = time.perf_counter(); observed = service.verify(media_path, manifest_path, "full" if manifest_path else "recover"); elapsed = round((time.perf_counter() - started) * 1000, 3)
        actual = observed.get(expected_field) if expected_field else observed.get("conclusion")
        rows.append({"sample_id": sample_id, "kind": "attack", "attack": name, "simulated": simulated,
                     "detector_input": {"manifest_supplied": bool(manifest_path)},
                     "observed": {"conclusion": observed.get("conclusion"), "content_hash_match": observed.get("content_hash_match"), "signature_valid": observed.get("signature_valid"), "watermark_status": observed.get("watermark_status"), "elapsed_ms": elapsed},
                     "truth": {"expected": expected, "expected_field": expected_field}, "handled": actual == expected,
                     "media_path": str(media_path)})

    tampered = json.loads(json.dumps(original_manifest)); tampered["body"]["publisher_id"] = "attacker"
    tampered_path = root / "detector-credential-a.json"; _write(tampered_path, tampered)
    check("manifest_field_tamper", Path(g3["media_path"]), tampered_path, "REJECTED")

    signed_tampered = json.loads(json.dumps(original_manifest)); sig = bytearray(base64.b64decode(signed_tampered["signature"])); sig[0] ^= 1; signed_tampered["signature"] = base64.b64encode(bytes(sig)).decode("ascii")
    sig_path = root / "detector-credential-b.json"; _write(sig_path, signed_tampered)
    check("signature_bytes_tamper", Path(g3["media_path"]), sig_path, "REJECTED")

    replacement = root / "detector-media-a.bin"; replacement.write_bytes(_fixture_bytes(97))
    check("content_replacement_with_original_manifest", replacement, Path(g3["manifest_path"]), "FAIL", "content_hash_match")

    # 复制合法水印到另一份合成内容：验证器只能给出来源线索，不能声称内容完整。
    copied_bytes, _profile = media.embed(replacement.read_bytes(), original_manifest["body"]["content_id"], "image/png")
    copied = root / "detector-media-b.bin"; copied.write_bytes(copied_bytes)
    check("copied_watermark_on_unrelated_fixture", copied, None, "ORIGIN_HINT_ONLY", simulated=False)
    return rows


def _transform_cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    images = config.get("images", {})
    cases: list[dict[str, Any]] = [{"name": "exact", "operation": "identity", "value": None, "simulated": False}]
    for quality in images.get("jpeg_reencode", []): cases.append({"name": f"jpeg_q{quality}", "operation": "jpeg_reencode", "value": quality, "simulated": False})
    for ratio in images.get("resize", []): cases.append({"name": f"resize_{ratio}", "operation": "resize", "value": ratio, "simulated": False})
    for keep in images.get("crop_keep_area", []): cases.append({"name": f"crop_{keep}", "operation": "crop", "value": keep, "simulated": False})
    if images.get("metadata_clear"): cases.append({"name": "metadata_clear", "operation": "metadata_clear", "value": True, "simulated": False})
    cases.append({"name": "screenshot_simulated", "operation": "screenshot_simulated", "value": None, "simulated": True})
    return cases


def run_benchmark(output_dir: str | Path = "results/benchmark", config_path: str | Path | None = None,
                  sample_count: int = 1, seed: int | None = None) -> dict[str, Any]:
    """运行固定小型图片评测并写出 JSONL、JSON 和 Markdown。"""
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    config = load_attack_config(config_path); run_seed = int(seed if seed is not None else config.get("seed", DEFAULT_SEED)); random.seed(run_seed)
    root = output / "runtime"; service = ProvenanceService(root / "registry")
    private, public = root / "benchmark-private.pem", root / "benchmark-public.pem"; password = "benchmark-local-pass-2026"
    service.keygen(private, public, password)
    records: list[dict[str, Any]] = []
    cases = _transform_cases(config)
    for index in range(max(1, int(sample_count))):
        source = root / f"fixture-{index:02d}.png"; _fixture(source, index, "PNG")
        source_bytes = source.read_bytes()
        # 每组的发布产物只创建一次，再对其进行同一变换。
        g1 = _signed_only(service, source, private, password, root)
        g2_cid = __import__("secrets").token_hex(16); g2_bytes, g2_profile = media.embed(source_bytes, g2_cid, "image/png"); g2_path = root / f"g2-{index:02d}.png"; g2_path.write_bytes(g2_bytes)
        g3 = service.register(source, private, password, publisher_id="publisher-benchmark")
        bases = {
            "G0": {"media": source, "manifest": None, "content_id": None, "profile": "none"},
            "G1": {"media": Path(g1["media_path"]), "manifest": Path(g1["manifest_path"]), "content_id": g1["content_id"], "profile": "signature-only"},
            "G2": {"media": g2_path, "manifest": None, "content_id": g2_cid, "profile": g2_profile},
            "G3": {"media": Path(g3["media_path"]), "manifest": Path(g3["manifest_path"]), "content_id": g3["content_id"], "profile": g3["watermark_profile"]},
        }
        for case in cases:
            for group in GROUPS:
                base = bases[group]; data = Path(base["media"]).read_bytes()
                if case["operation"] == "identity": transformed = data
                else: transformed = _bytes_image(data, case["operation"], case["value"])
                # 验证器只看到不携带组别、变换名或真值的 opaque 输入路径。
                media_path = output / "samples" / f"detector-{len(records):06d}.bin"; media_path.parent.mkdir(parents=True, exist_ok=True); media_path.write_bytes(transformed)
                # 真值不进入 service.verify；它只从媒体和可选 sidecar 读取。
                started = time.perf_counter()
                # A supplied sidecar is an explicit hard check. For G3 after a
                # propagation transform, the sidecar is intentionally absent
                # so the benchmark measures registry-backed watermark recovery.
                supplied_manifest = base["manifest"]
                if group == "G3" and case["operation"] != "identity":
                    supplied_manifest = None
                observed = service.verify(media_path, supplied_manifest, "hard" if supplied_manifest else "recover")
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                recovered = observed.get("recovered_content_ids", [])
                expected = base["content_id"]
                if group == "G0": expected_outcome = "NO_PROVENANCE"; handled = observed.get("conclusion") == expected_outcome
                elif case["operation"] == "identity": expected_outcome = "VERIFIED_EXACT" if group in ("G1", "G3") else ("ORIGIN_HINT_ONLY" if group == "G2" else "NO_PROVENANCE"); handled = (observed.get("conclusion") == expected_outcome and (group != "G2" or expected in recovered))
                else: expected_outcome = "ORIGIN_HINT_ONLY" if group == "G3" else ("INDETERMINATE" if group == "G1" else ("ORIGIN_HINT_ONLY" if group == "G2" else "NO_PROVENANCE")); handled = (observed.get("conclusion") == expected_outcome and (group != "G2" or expected in recovered))
                records.append({"sample_id": f"fixture-{index:02d}", "group": group, "transform": case["name"], "simulated": case["simulated"], "media_type": media.sniff(transformed), "detector_input": {"manifest_supplied": bool(supplied_manifest)}, "observed": {"conclusion": observed.get("conclusion"), "watermark_status": observed.get("watermark_status"), "recovered_content_ids": recovered, "elapsed_ms": elapsed}, "truth": {"expected_content_id": expected, "expected_outcome": expected_outcome}, "handled": bool(handled), "media_path": str(media_path)})
        records.extend(_attack_records(service, source, g3, root, f"fixture-{index:02d}"))
    sample_records = [item for item in records if item.get("kind") != "attack"]
    attack_records = [item for item in records if item.get("kind") == "attack"]
    jsonl = output / "samples.jsonl"; jsonl.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in sample_records) + "\n", encoding="utf-8")
    attack_jsonl = output / "attacks.jsonl"; attack_jsonl.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in attack_records) + "\n", encoding="utf-8")
    by_group = {group: [r for r in records if r.get("group") == group] for group in GROUPS}
    measured = {group: {"samples": len(items), "handled": sum(1 for r in items if r["handled"]), "handled_rate": round(sum(1 for r in items if r["handled"]) / len(items), 4) if items else None, "mean_verify_ms": round(statistics.mean(r["observed"]["elapsed_ms"] for r in items), 3) if items else None} for group, items in by_group.items()}
    attacks = [r for r in records if r.get("kind") == "attack"]
    attack_measured = {r["attack"]: {"samples": 1, "handled": int(r["handled"]), "expected": r["truth"]["expected"], "observed": r["observed"].get("conclusion")} for r in attacks}
    summary = {"status": "LOCAL_SMOKE", "dataset": {"kind": "synthetic-fixtures", "base_samples": max(1, int(sample_count)), "media_types": ["image/png"], "independent_sources": max(1, int(sample_count)), "documents": "N/A", "representativeness": "小规模机制评测，不代表真实内容数据集"}, "seed": run_seed, "groups": list(GROUPS), "group_scope": {"G0": "无凭证、无水印", "G1": "仅签名凭证", "G2": "仅水印", "G3": "水印+签名+Registry；版本链/撤销在本轮未评估（N/A）"}, "targets": {"content_id_accuracy": {"target": "未在此 smoke benchmark 评估；不作 95% 承诺", "measured": None}, "verification_p95_ms": {"target": "未在此 smoke benchmark 评估；不作 P95 承诺", "measured": None}}, "measured": measured, "attacks": attack_measured, "transform_scope": {"image_cases": [c["name"] for c in cases], "pdf": {"status": "N/A", "reason": "本轮仅冻结 PNG fixture；PDF page delete/reorder/render 配置已保留，未伪装成实测", "screenshot": "simulated_only"}}, "artifacts": {"samples_jsonl": str(jsonl), "attacks_jsonl": str(attack_jsonl), "summary_json": str(output / "summary.json"), "markdown": str(output / "BENCHMARK.md")}}
    _write(output / "summary.json", summary)
    lines = ["# 小型四组评测", "", "状态：`LOCAL_SMOKE`。数据集是合成 PNG fixture，仅用于机制联调，不代表真实内容数据集。", "", "## 目标与实测", "", "| 项目 | 预设目标 | 本轮实测 |", "|---|---|---|", "| ContentID 正确率 | 未评估；不作 95% 承诺 | 未计算 |", "| 验证延迟 P95 | 未评估；不作 P95 承诺 | 未计算 |", "", "## 分组结果", "", "| 组 | 样本数 | 处理正确数 | 处理率 | 平均验证毫秒 |", "|---|---:|---:|---:|---:|"]
    lines += [f"| {g} | {measured[g]['samples']} | {measured[g]['handled']} | {measured[g]['handled_rate']} | {measured[g]['mean_verify_ms']} |" for g in GROUPS]
    lines += ["", "## 变换与范围", "", "已执行图片变换：" + ", ".join(c["name"] for c in cases) + "。截图为渲染/缩放/编码的模拟截图，未称为真实浏览器截图。", "PDF 页面删除、重排、重新渲染和截图目前为 N/A；配置项已冻结，待 PDF fixture 接入后再报告实测。", "", "逐样本记录见 `samples.jsonl`；攻击记录单独见 `attacks.jsonl`。两者中的 truth 只供评测器汇总，未传入检测器。"]
    lines += ["", "## 攻击矩阵", "", "| 攻击 | 预期 | 实测结论 | 处理 |", "|---|---|---|---|"]
    lines += [f"| {name} | {item['expected']} | {item['observed']} | {'是' if item['handled'] else '否'} |" for name, item in attack_measured.items()]
    (output / "BENCHMARK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 私钥只用于本轮本地评测，避免把敏感材料留在产物目录。
    private.unlink(missing_ok=True)
    return summary


__all__ = ["GROUPS", "load_attack_config", "run_benchmark"]
