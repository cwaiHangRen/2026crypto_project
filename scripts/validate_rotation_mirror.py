"""离线旋转/镜像验证：真实水印、固定合成载体、负例与服务闭环。

真值仅保存在评测记录中；检测器只收到文件字节。输出目录必须是新目录，
不会覆盖历史失败记录。可加载改动前的 watermark.py 做同输入对照。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import platform
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gm_provenance import watermark
from gm_provenance.service import ProvenanceService

SEED = 20261013
ORIENTATIONS = [(mirror, angle) for mirror in (False, True) for angle in (0, 90, 180, 270)]
ROTATIONS = {0: None, 90: Image.Transpose.ROTATE_90,
             180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}


def fixture(index: int) -> tuple[bytes, dict]:
    """生成可复现的合成图；这些载体不是自然图像数据集。"""
    width, height = [(768, 768), (896, 640), (640, 896), (512, 768), (1024, 768)][index % 5]
    y, x = np.indices((height, width), dtype=np.float32)
    rng = np.random.default_rng(SEED + index)
    arr = np.empty((height, width, 3), np.uint8)
    for channel in range(3):
        value = (45 + channel * 20 + 85 * x / width + 35 * y / height
                 + 25 * np.sin((x / width + y / height) * np.pi * (2 + index % 3))
                 + float(rng.uniform(-12, 12)))
        arr[..., channel] = np.clip(value, 0, 255).astype(np.uint8)
    image = Image.fromarray(arr)
    draw = ImageDraw.Draw(image)
    if index % 3 != 0:
        draw.rectangle((width // 6, height // 5, width // 2, height // 2), outline="white", width=4)
        draw.text((width // 5, height // 3), f"SOURCE {index:02d}", fill="black")
    mode = "L" if index % 4 == 0 else "RGB"
    image = image.convert(mode)
    fmt = "PNG" if index % 2 == 0 else "JPEG"
    out = io.BytesIO()
    image.save(out, fmt, quality=95)
    return out.getvalue(), {"size": [width, height], "mode": mode, "format": fmt}


def transform(data: bytes, mirror: bool, angle: int, encoding: str) -> bytes:
    if not mirror and angle == 0 and encoding == "lossless":
        return data
    image = Image.open(io.BytesIO(data))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if ROTATIONS[angle] is not None:
        image = image.transpose(ROTATIONS[angle])
    out = io.BytesIO()
    if encoding == "lossless":
        image.save(out, "PNG")
    else:
        image.save(out, "JPEG", quality=int(encoding.removeprefix("jpeg_q")))
    return out.getvalue()


def measure(detector, data: bytes) -> dict:
    started = time.perf_counter()
    result = detector(data)
    return {"status": result["status"], "content_ids": result["content_ids"],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def correct(result: dict, cid: str | None) -> bool:
    return (result["status"] == "FOUND" and result["content_ids"] == [cid]) if cid else (
        result["status"] == "NOT_FOUND" and result["content_ids"] == [])


def timing(rows: list[dict], field: str) -> dict | None:
    values = [row[field]["elapsed_ms"] for row in rows if row.get(field) is not None]
    if not values:
        return None
    return {"count": len(values), "mean_ms": round(statistics.mean(values), 3),
            "p95_ms": round(float(np.percentile(values, 95)), 3), "max_ms": max(values)}


def service_checks(output: Path) -> list[dict]:
    service = ProvenanceService(output / "service-registry")
    records = []
    # 密钥仅存在于本工程内的临时目录，不进入验证记录或提交材料。
    with tempfile.TemporaryDirectory(dir=output, prefix="temporary-keys-") as temporary:
        private, public = Path(temporary) / "private.pem", Path(temporary) / "public.pem"
        password = secrets.token_urlsafe(24)
        service.keygen(private, public, password)
        for index in (1, 2):
            raw, info = fixture(index)
            source = output / f"service-source-{index}.bin"
            source.write_bytes(raw)
            published = service.register(source, private, password)
            marked = Path(published["media_path"]).read_bytes()
            for mirror, angle in ORIENTATIONS:
                changed = transform(marked, mirror, angle, "lossless")
                path = output / f"service-detector-{len(records):03d}.bin"
                path.write_bytes(changed)
                observed = service.verify(path, None, "recover")
                exact = changed == marked
                passed = (observed["watermark_status"] == "FOUND"
                          and observed["recovered_content_ids"] == [published["content_id"]]
                          and observed["signature_valid"] == "PASS"
                          and observed["issuer_trusted"] == "PASS"
                          and observed["content_hash_match"] == ("PASS" if exact else "FAIL")
                          and observed["conclusion"] == ("VERIFIED_EXACT" if exact else "ORIGIN_HINT_ONLY")
                          and observed["state_freshness"] == "NOT_CHECKED")
                records.append({"kind": "service", "format": info["format"], "mirror": mirror,
                                "angle": angle, "manifest_supplied": False,
                                "expected_content_id": published["content_id"],
                                "observed": observed, "passed": passed})
    return records


def run(output: Path, sample_count: int, baseline_source: Path | None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    baseline = None
    if baseline_source is not None:
        spec = importlib.util.spec_from_file_location("rotation_mirror_baseline", baseline_source)
        if spec is None or spec.loader is None:
            raise ValueError("无法加载基线源码")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        baseline = module.detect_image
    warm_raw, _ = fixture(0)
    warm_marked, _ = watermark.embed_image(warm_raw, "0123456789abcdef0123456789abcdef")
    measure(watermark.detect_image, warm_marked)
    if baseline:
        measure(baseline, warm_marked)
    rows, sources = [], []
    with (output / "samples.jsonl").open("w", encoding="utf-8") as log:
        for index in range(sample_count):
            raw, info = fixture(index)
            cid = secrets.token_hex(16)
            marked, profile = watermark.embed_image(raw, cid, info["format"])
            reference = measure(baseline or watermark.detect_image, marked)
            source_ok = correct(reference, cid)
            sources.append({"sample_id": index, **info, "content_id": cid, "profile": profile,
                            "original_detection": reference, "original_detectable": source_ok})
            for encoding in ("lossless", "jpeg_q95", "jpeg_q85"):
                for mirror, angle in ORIENTATIONS:
                    for kind, base, expected in (("marked", marked, cid), ("unmarked", raw, None)):
                        data = transform(base, mirror, angle, encoding)
                        path = inputs / f"detector-{len(rows):05d}.bin"
                        path.write_bytes(data)
                        old = measure(baseline, data) if baseline else None
                        observed = measure(watermark.detect_image, data)
                        row = {"sample_id": index, **info, "kind": kind, "encoding": encoding,
                               "mirror": mirror, "angle": angle, "expected_content_id": expected,
                               "original_detectable": source_ok, "detector_input": "file bytes only",
                               "input_sha256": hashlib.sha256(data).hexdigest(),
                               "path": str(path.relative_to(output)), "baseline": old,
                               "baseline_passed": correct(old, expected) if old else None,
                               "observed": observed, "passed": correct(observed, expected)}
                        rows.append(row)
                        log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(f"已验证 {index + 1}/{sample_count} 个合成载体", flush=True)
    service = service_checks(output)
    (output / "service.json").write_text(json.dumps(service, ensure_ascii=False, indent=2), encoding="utf-8")
    core = [row for row in rows if row["kind"] == "marked" and row["encoding"] == "lossless"]
    preservation = [row for row in core if row["original_detectable"]]
    negatives = [row for row in rows if row["kind"] == "unmarked"]
    by_encoding = {}
    for encoding in ("lossless", "jpeg_q95", "jpeg_q85"):
        selected = [row for row in rows if row["kind"] == "marked" and row["encoding"] == encoding]
        by_encoding[encoding] = {"cases": len(selected), "correct": sum(row["passed"] for row in selected),
                                 "baseline_correct": sum(row["baseline_passed"] is True for row in selected),
                                 "current_timing": timing(selected, "observed"),
                                 "baseline_timing": timing(selected, "baseline")}
    groups = []
    for mirror, angle in ORIENTATIONS:
        selected = [row for row in core if row["mirror"] == mirror and row["angle"] == angle]
        groups.append({"mirror": mirror, "angle": angle, "cases": len(selected),
                       "correct": sum(row["passed"] for row in selected),
                       "baseline_correct": sum(row["baseline_passed"] is True for row in selected)})
    original_ok = sum(source["original_detectable"] for source in sources)
    passed = (original_ok == sample_count and all(row["passed"] for row in core)
              and all(row["passed"] for row in negatives) and all(row["passed"] for row in service))
    summary = {"status": "PASS" if passed else "FAIL", "seed": SEED,
               "scope": "固定合成载体；非真实图像数据集，不作比赛识别率或延迟达标声明",
               "platform": platform.platform(), "python": platform.python_version(),
               "versions": {name: version(name) for name in ("numpy", "Pillow", "scipy", "reedsolo")},
               "baseline_source": str(baseline_source) if baseline_source else None,
               "baseline_sha256": hashlib.sha256(baseline_source.read_bytes()).hexdigest() if baseline_source else None,
               "current_source_sha256": hashlib.sha256(Path(watermark.__file__).read_bytes()).hexdigest(),
               "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "timing_method": "每检测器排除一次预热；旧实现先测、新实现后测；perf_counter；P95 线性分位数",
               "sources": sources, "original_detectable": original_ok, "base_samples": sample_count,
               "core": by_encoding["lossless"], "geometry_preservation": {
                   "cases": len(preservation), "correct": sum(row["passed"] for row in preservation)},
               "by_orientation": groups, "by_encoding": by_encoding,
               "negative": {"cases": len(negatives), "false_positives": sum(not row["passed"] for row in negatives),
                            "current_timing": timing(negatives, "observed"), "baseline_timing": timing(negatives, "baseline")},
               "service": {"cases": len(service), "passed": sum(row["passed"] for row in service)},
               "jpeg_failures": sum(not row["passed"] for row in rows if row["kind"] == "marked" and row["encoding"] != "lossless")}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 旋转镜像实测", "", f"状态：{summary['status']}。固定合成载体 {sample_count} 个。",
             "", summary["scope"], "", "| 左右镜像后旋转 | 角度（逆时针） | 用例 | 旧实现正确 | 新实现正确 |",
             "|---|---:|---:|---:|---:|"]
    lines += [f"| {item['mirror']} | {item['angle']} | {item['cases']} | {item['baseline_correct']} | {item['correct']} |" for item in groups]
    lines += ["", "| 输出编码 | 正例 | 旧实现正确 | 新实现正确 | 新实现平均 ms | 新实现 P95 ms |",
              "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {name} | {item['cases']} | {item['baseline_correct']} | {item['correct']} | {item['current_timing']['mean_ms']} | {item['current_timing']['p95_ms']} |" for name, item in by_encoding.items()]
    lines += ["", f"原方向可检出：{original_ok}/{sample_count}；负例：{len(negatives)}，误报：{summary['negative']['false_positives']}。",
              f"真实 SM2/SM3 服务闭环：{summary['service']['passed']}/{len(service)}，调用时未传 sidecar。",
              "", "JPEG 重新编码是额外有损实验，失败逐条保存在 samples.jsonl；不会从统计中排除。",
              "全部真值仅用于评测，检测器只收到字节。方向标签表示尝试的操作，不证明唯一传播历史。",
              "正例与负例耗时、依赖版本和源码指纹见 summary.json；服务字段见 service.json。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=30)
    parser.add_argument("--baseline-source", type=Path)
    args = parser.parse_args()
    if args.sample_count < 1:
        parser.error("sample-count 必须大于零")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("验证产物必须留在本工程")
    if output.exists():
        parser.error("输出目录已存在，请选择新目录以保留历史记录")
    summary = run(output, args.sample_count, args.baseline_source)
    print(json.dumps({key: summary[key] for key in ("status", "base_samples", "core", "negative", "service", "jpeg_failures")}, ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
