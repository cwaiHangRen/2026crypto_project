"""离线任意角旋转验证：保留真值、失败输入和旧检测器的同输入对照。

主实验为 expand=True 的 PNG 输出，含左右镜像及黑/白/透明边框；JPEG
重新编码、expand=False 和中心裁剪单列统计，不以它们的失败冒充主机制失败。
检测器始终只收到文件字节；原图检出失败的载体也保留在全部分母中。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import platform
import secrets
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from gm_provenance import watermark
from validate_rotation_mirror import correct, fixture, timing

SEED = 20261013
BOUNDARY_ANGLES = (0.1, 44.65, 45.35, 89.9, 179.9, 269.9, 359.9)
EXTRA_ANGLES = (13.7, 44.65)
SUITES = ("core", "jpeg_q95", "jpeg_q85", "expand_false", "crop")
BORDERS = ("black", "white", "transparent")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(data: bytes, mirror: bool, angle: float, border: str,
              suite: str) -> tuple[bytes, dict]:
    """Pillow 的角度为逆时针；透明图在 JPEG 编码前合成到白色背景。"""
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.copy()
    source_mode = image.mode
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if border == "transparent":
        image = image.convert("RGBA")
        fill = (0, 0, 0, 0)
    else:
        image = image.convert("L" if image.mode == "L" else "RGB")
        fill = 0 if border == "black" else 255
        if image.mode == "RGB":
            fill = (fill, fill, fill)
    expand = suite != "expand_false"
    image = image.rotate(angle, Image.Resampling.BICUBIC, expand=expand, fillcolor=fill)
    crop_box = None
    if suite == "crop":
        # 裁去画布四周各 10%，保留真实失败以区分有边框与截断场景。
        dx, dy = round(image.width * 0.10), round(image.height * 0.10)
        crop_box = [dx, dy, image.width - dx, image.height - dy]
        image = image.crop(crop_box)
    encoding = "JPEG" if suite.startswith("jpeg_q") else "PNG"
    quality = int(suite.removeprefix("jpeg_q")) if encoding == "JPEG" else None
    alpha_flatten_background = None
    if encoding == "JPEG" and image.mode == "RGBA":
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
        alpha_flatten_background = "white"
    out = io.BytesIO()
    image.save(out, encoding, **({"quality": quality} if quality else {}))
    return out.getvalue(), {"source_mode": source_mode, "output_mode": image.mode,
                            "output_size": list(image.size), "output_format": encoding,
                            "jpeg_quality": quality, "expand": expand, "crop_box": crop_box,
                            "alpha_flatten_background": alpha_flatten_background,
                            "resampling": "Pillow BICUBIC"}


def measure(detector, data: bytes) -> dict:
    """保留完整检测详情；检测异常也是一条失败记录，不中止后续用例。"""
    started = time.perf_counter()
    try:
        result = dict(detector(data))
    except Exception as exc:
        result = {"status": "ERROR", "content_ids": [],
                  "details": {"error": f"{type(exc).__name__}: {exc}"}}
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def _load_baseline(path: Path | None):
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("arbitrary_rotation_baseline", path)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载基线源码")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.detect_image


def _angles(custom: list[float] | None, random_count: int) -> list[dict]:
    values = BOUNDARY_ANGLES if custom is None else custom
    rows = [{"angle": float(angle) % 360, "angle_kind": "boundary" if custom is None else "specified"}
            for angle in values]
    rng = np.random.default_rng(SEED)
    rows.extend({"angle": round(float(angle), 6), "angle_kind": "random"}
                for angle in rng.uniform(0, 360, random_count))
    return rows


def _sources(sample_count: int, real_dir: Path | None):
    for index in range(sample_count):
        raw, info = fixture(index)
        yield f"synthetic-{index:03d}", raw, {**info, "source_kind": "synthetic", "fixture_index": index}
    if real_dir is not None:
        images = sorted(path for path in real_dir.iterdir()
                        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if not images:
            raise ValueError("本地真实图目录中未找到 PNG/JPEG 文件")
        for index, path in enumerate(images):
            raw = path.read_bytes()
            with Image.open(io.BytesIO(raw)) as image:
                info = {"size": list(image.size), "mode": image.mode, "format": image.format}
            yield f"real-{index:03d}", raw, {**info, "source_kind": "local_real_image",
                                            "original_path": str(path.resolve()), "original_filename": path.name}


def _score(rows: list[dict]) -> dict:
    return {"cases": len(rows), "correct": sum(row["passed"] for row in rows),
            "baseline_correct": sum(row["baseline_passed"] is True for row in rows),
            "current_timing": timing(rows, "observed"), "baseline_timing": timing(rows, "baseline")}


def _versions() -> dict:
    versions = {}
    for name in ("numpy", "Pillow", "scipy", "reedsolo"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def _source_fingerprints(baseline_source: Path | None) -> dict:
    paths = [Path(__file__), Path(watermark.__file__), ROOT / "src/gm_provenance/rotation.py"]
    fingerprints = {str(path.resolve().relative_to(ROOT)): sha256(path.read_bytes())
                    for path in paths if path.is_file()}
    if baseline_source is not None:
        fingerprints["baseline:" + str(baseline_source.resolve())] = sha256(baseline_source.read_bytes())
    return fingerprints


def run(output: Path, sample_count: int, baseline_source: Path | None,
        real_dir: Path | None = None, random_angle_count: int = 3,
        angles: list[float] | None = None, suites: tuple[str, ...] = SUITES,
        extra_angles: list[float] | None = None, extra_sample_count: int = 2) -> dict:
    if output.resolve().exists():
        raise ValueError("输出目录已存在，请选择新目录以保留历史记录")
    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("验证产物必须留在本工程")
    output.mkdir(parents=True, exist_ok=False)
    inputs, source_dir = output / "inputs", output / "sources"
    inputs.mkdir()
    source_dir.mkdir()
    started = time.perf_counter()
    source_fingerprints = _source_fingerprints(baseline_source)
    baseline = _load_baseline(baseline_source)
    angle_plan = _angles(angles, random_angle_count)
    extra_angle_plan = _angles(list(EXTRA_ANGLES) if extra_angles is None else extra_angles, 0)
    extra_source_counts = {}
    manifest_references = []
    if real_dir is not None:
        for path in sorted(real_dir.glob("*manifest*.json")):
            data = path.read_bytes()
            saved = source_dir / path.name
            saved.write_bytes(data)
            manifest_references.append({"original_path": str(path.resolve()), "sha256": sha256(data),
                                        "saved_path": str(saved.relative_to(output))})
    warm_raw, _ = fixture(0)
    warm_marked, _ = watermark.embed_image(warm_raw, "0123456789abcdef0123456789abcdef")
    warmups = {"current": measure(watermark.detect_image, warm_marked),
               "baseline": measure(baseline, warm_marked) if baseline else None}
    rows, sources = [], []
    with (output / "samples.jsonl").open("w", encoding="utf-8") as log:
        for sample_id, raw, info in _sources(sample_count, real_dir):
            kind_index = extra_source_counts.get(info["source_kind"], 0)
            extra_enabled = kind_index < extra_sample_count
            extra_source_counts[info["source_kind"]] = kind_index + 1
            # 与发布协议一致：每个载体用随机 ContentID，固定 seed 仅用于图像与角度。
            # 真值和原始检测字节均保存，重放不需要重新生成这个编号。
            cid = secrets.token_hex(16)
            fmt = "JPEG" if info["format"] in ("JPEG", "JPG") else "PNG"
            marked, profile = watermark.embed_image(raw, cid, fmt)
            original_path = source_dir / f"{sample_id}-original.{fmt.lower()}"
            marked_path = source_dir / f"{sample_id}-marked.{fmt.lower()}"
            original_path.write_bytes(raw)
            marked_path.write_bytes(marked)
            original_baseline = measure(baseline, marked) if baseline else None
            original_observed = measure(watermark.detect_image, marked)
            original_negative = measure(watermark.detect_image, raw)
            source_ok = correct(original_observed, cid)
            sources.append({"sample_id": sample_id, **info, "content_id": cid, "profile": profile,
                            "original_path_saved": str(original_path.relative_to(output)),
                            "marked_path_saved": str(marked_path.relative_to(output)),
                            "original_sha256": sha256(raw), "marked_sha256": sha256(marked),
                            "original_detection": original_observed, "original_baseline": original_baseline,
                            "original_unmarked_detection": original_negative,
                            "included_in_extra_suites": extra_enabled,
                            "original_detectable": source_ok,
                            "original_baseline_detectable": correct(original_baseline, cid) if original_baseline else None})
            (output / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
            for suite in suites:
                if suite != "core" and not extra_enabled:
                    continue
                for angle_info in (angle_plan if suite == "core" else extra_angle_plan):
                    for mirror in (False, True):
                        for border in BORDERS:
                            for kind, base, expected in (("marked", marked, cid), ("unmarked", raw, None)):
                                data, transformation = transform(base, mirror, angle_info["angle"], border, suite)
                                path = inputs / f"detector-{len(rows):06d}.{transformation['output_format'].lower()}"
                                path.write_bytes(data)
                                old = measure(baseline, data) if baseline else None
                                observed = measure(watermark.detect_image, data)
                                truth = {"status": "FOUND" if expected else "NOT_FOUND",
                                         "content_ids": [expected] if expected else []}
                                row = {"sample_id": sample_id, **info, "kind": kind, "suite": suite,
                                       **angle_info, "mirror": mirror, "border": border,
                                       **transformation, "truth": truth, "expected_content_id": expected,
                                       "original_detectable": source_ok, "detector_input": "file bytes only",
                                       "input_sha256": sha256(data), "input_bytes": len(data),
                                       "path": str(path.relative_to(output)), "baseline": old,
                                       "baseline_passed": correct(old, expected) if old else None,
                                       "observed": observed, "passed": correct(observed, expected)}
                                rows.append(row)
                                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                                log.flush()
            print(f"已验证 {sample_id}，累计 {len(rows)} 个检测输入", flush=True)
    core = [row for row in rows if row["suite"] == "core" and row["kind"] == "marked"]
    negatives = [row for row in rows if row["kind"] == "unmarked"]
    groups = []
    for suite in suites:
        for source_kind in ("synthetic", "local_real_image"):
            for kind in ("marked", "unmarked"):
                selected = [row for row in rows if row["suite"] == suite
                            and row["source_kind"] == source_kind and row["kind"] == kind]
                if selected:
                    groups.append({"suite": suite, "source_kind": source_kind, "kind": kind, **_score(selected)})
    by_border = [{"border": border, **_score([row for row in core if row["border"] == border])} for border in BORDERS]
    by_angle = [{**angle_info, **_score([row for row in core if row["angle"] == angle_info["angle"]])}
                for angle_info in angle_plan]
    original_ok = sum(source["original_detectable"] for source in sources)
    original_negative_ok = all(correct(source["original_unmarked_detection"], None) for source in sources)
    # 所有源图都计入核心统计；没有按原图检出情况筛除载体。
    final_fingerprints = _source_fingerprints(baseline_source)
    source_unchanged = source_fingerprints == final_fingerprints
    current_observations = [warmups["current"]] + [row["observed"] for row in rows]
    current_observations += [source[field] for source in sources
                             for field in ("original_detection", "original_unmarked_detection")]
    baseline_observations = ([warmups["baseline"]] + [row["baseline"] for row in rows]
                             + [source["original_baseline"] for source in sources]) if baseline else []
    detector_errors = sum(item["status"] == "ERROR" for item in current_observations)
    baseline_errors = sum(item["status"] == "ERROR" for item in baseline_observations)
    passed = (bool(core) and original_ok == len(sources) and original_negative_ok and source_unchanged
              and detector_errors == 0 and baseline_errors == 0
              and all(row["passed"] for row in core) and all(row["passed"] for row in negatives))
    summary = {"status": "PASS" if passed else "FAIL", "seed": SEED,
               "scope": "固定合成载体和可选本地真实图；规模有限，不作比赛识别率或延迟达标声明",
               "platform": platform.platform(), "python": platform.python_version(), "versions": _versions(),
               "source_sha256": source_fingerprints, "source_sha256_at_finish": final_fingerprints,
               "detector_sources_unchanged_during_run": source_unchanged,
               "content_id_generation": "secrets.token_hex(16)；随机编号保存在 sources 与逐条真值中，可用保留字节复验",
               "detector_errors_all_suites": detector_errors, "baseline_errors_all_suites": baseline_errors,
               "baseline_source": str(baseline_source.resolve()) if baseline_source else None,
               "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "git_changed_paths": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines(),
               "timing_method": "每检测器排除一次预热；旧实现先测、新实现后测；perf_counter；P95 线性分位数；串行执行",
               "warmups_excluded": warmups, "wall_time_seconds": round(time.perf_counter() - started, 3),
               "base_samples": len(sources), "synthetic_samples": sample_count,
               "source_manifest_references": manifest_references, "sources": sources,
               "original_detectable": original_ok, "angle_plan": angle_plan, "suites": list(suites),
               "extra_angle_plan": extra_angle_plan, "extra_sample_count_per_source_kind": extra_sample_count,
               "extra_sample_selection": "各载体类型按输入次序选前 N 个，与原图/变换检出结果无关",
               "core": _score(core), "by_border": by_border, "by_angle": by_angle, "groups": groups,
               "negative": {**_score(negatives),
                            "false_positives": sum(bool(row["observed"]["content_ids"]) for row in negatives),
                            "detector_errors": sum(row["observed"]["status"] == "ERROR" for row in negatives)},
               "boundary_failures": sum(not row["passed"] for row in rows
                                        if row["kind"] == "marked" and row["suite"] != "core"),
               "acceptance": "原图与全部 core PNG 正例须正确；所有转换负例须无误报；检测无异常且源码在运行中不变；额外有损/裁剪正例只统计"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 任意角旋转实测", "", f"状态：{summary['status']}。载体 {len(sources)} 个，图像/角度固定 seed {SEED}，ContentID 随机生成并保留。",
             "", f"本次执行组：{', '.join(suites)}。",
             "", summary["scope"], "", "| 分组 | 载体 | 类型 | 用例 | 旧实现正确 | 新实现正确 | 平均 ms | P95 ms |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for group in groups:
        current = group["current_timing"]
        lines.append(f"| {group['suite']} | {group['source_kind']} | {group['kind']} | {group['cases']} | "
                     f"{group['baseline_correct']} | {group['correct']} | {current['mean_ms']} | {current['p95_ms']} |")
    lines += ["", f"原方向正确检出：{original_ok}/{len(sources)}；转换负例误报：{summary['negative']['false_positives']}/{len(negatives)}。",
              "", "主组 core 是 expand=True 的 PNG，逐一覆盖黑色、白色和透明边框，以及左右镜像。",
              "JPEG Q95/Q85、expand=False 与中心裁去画布四周各 10% 是独立边界实验；其正例失败不会从分母中移除。",
              f"额外实验各载体类型按输入次序选择前 {extra_sample_count} 个，使用 {len(extra_angle_plan)} 个固定角度；选择与检出结果无关。",
              "透明图片进入 JPEG 额外实验时先合成到白色背景。输入原图为 PNG/JPEG，变换统一用 Pillow BICUBIC。",
              "检测器仅接收文件字节，无原图、角度、边框、尺寸、ContentID 或 sidecar；真值仅用于结果核对。",
              "原图恢复失败也计入全部对应分母；全部输入在 sources/ 与 inputs/，逐条真值/完整观测在 samples.jsonl。",
              "本地真实图的既有 manifest 原样保留并记录原路径及 SHA256；本脚本不下载图片。",
              "环境、源码 SHA256、预热、平均/P95/最长耗时见 summary.json；方向匹配不证明唯一传播历史。",
              "", f"总墙钟耗时：{summary['wall_time_seconds']} 秒。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=5)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--real-image-dir", type=Path)
    parser.add_argument("--random-angle-count", type=int, default=3)
    parser.add_argument("--angles", type=float, nargs="+", help="替换边界角列表；仍追加 random-angle-count 个随机角")
    parser.add_argument("--extra-angles", type=float, nargs="+", help="额外 JPEG/截断实验角度；默认 13.7、44.65")
    parser.add_argument("--extra-sample-count", type=int, default=2,
                        help="各载体类型按输入次序用于额外实验的前 N 个；默认 2，不按检出结果筛选")
    parser.add_argument("--suites", choices=SUITES, nargs="+", default=list(SUITES))
    args = parser.parse_args()
    if args.sample_count < 0 or args.random_angle_count < 0 or args.extra_sample_count < 0:
        parser.error("sample-count、random-angle-count 与 extra-sample-count 不能小于零")
    if not args.sample_count and args.real_image_dir is None:
        parser.error("至少提供一个合成载体或本地真实图目录")
    if args.angles is not None and any(not math.isfinite(angle) for angle in args.angles):
        parser.error("angles 必须是有限数值")
    if args.extra_angles is not None and any(not math.isfinite(angle) for angle in args.extra_angles):
        parser.error("extra-angles 必须是有限数值")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        parser.error("输出必须为本工程内的新目录，禁止覆盖历史记录")
    if args.real_image_dir is not None and not args.real_image_dir.is_dir():
        parser.error("real-image-dir 必须是已存在的本地目录")
    if args.baseline_source is not None and not args.baseline_source.is_file():
        parser.error("baseline-source 必须是已存在的源码文件")
    if "core" not in args.suites:
        parser.error("suites 必须包含主组 core；边界实验不能单独冒充验收")
    summary = run(output, args.sample_count, args.baseline_source, args.real_image_dir,
                  args.random_angle_count, args.angles, tuple(dict.fromkeys(args.suites)),
                  args.extra_angles, args.extra_sample_count)
    print(json.dumps({key: summary[key] for key in ("status", "base_samples", "core", "negative", "boundary_failures", "wall_time_seconds")},
                     ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
