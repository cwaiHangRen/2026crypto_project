"""中文命令行入口。"""
from __future__ import annotations

import argparse, getpass, json, secrets
from pathlib import Path
from .service import ProvenanceService
from .benchmark import run_benchmark


def _password(value):
    return value if value else getpass.getpass("请输入本地演示私钥口令（不会写入日志）：")


def build_parser():
    p = argparse.ArgumentParser(prog="gm-provenance", description="国密内容凭证与媒体水印演示")
    p.add_argument("--root", default="data", help="本地登记与产物目录")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查 OpenSSL、SM3/SM2 和媒体后端")
    k = sub.add_parser("keygen", help="生成加密演示私钥和公钥"); k.add_argument("--private", required=True); k.add_argument("--public", required=True); k.add_argument("--passphrase")
    r = sub.add_parser("register", help="嵌入水印并发布文件"); r.add_argument("input"); r.add_argument("--private", required=True); r.add_argument("--passphrase"); r.add_argument("--publisher", default="publisher-demo-001"); r.add_argument("--parent")
    derv = sub.add_parser("derive", help="按父版本授权生成下一版本"); derv.add_argument("source", help="父 ContentID、已发布媒体文件或 Manifest"); derv.add_argument("--private", required=True); derv.add_argument("--passphrase"); derv.add_argument("--operation", choices=["jpeg-reencode", "resize"], required=True); derv.add_argument("--quality", type=int, default=75)
    v = sub.add_parser("verify", help="分层验证文件"); v.add_argument("file"); v.add_argument("--manifest"); v.add_argument("--mode", choices=["hard", "recover", "full"], default="full"); v.add_argument("--state-snapshot"); v.add_argument("--state-public")
    t = sub.add_parser("transform", help="执行可复现传播变换"); t.add_argument("input"); t.add_argument("output"); t.add_argument("--operation", choices=["jpeg-reencode", "resize"], default="jpeg-reencode"); t.add_argument("--quality", type=int, default=75)
    h = sub.add_parser("history", help="查看单父版本链"); h.add_argument("content_id")
    rv = sub.add_parser("revoke", help="登记内容或密钥撤销"); rv.add_argument("target_type", choices=["content", "key"]); rv.add_argument("target_id"); rv.add_argument("--reason", required=True)
    ss = sub.add_parser("state-snapshot", help="用独立状态密钥签发登记状态快照"); ss.add_argument("--private", required=True); ss.add_argument("--passphrase"); ss.add_argument("--output"); ss.add_argument("--public"); ss.add_argument("--ttl", type=int, default=300)
    d = sub.add_parser("demo", help="运行本地图片端到端演示"); d.add_argument("--output", default="results/demo")
    b = sub.add_parser("benchmark", help="运行当前固定小型基准"); b.add_argument("--output", default="results/benchmark"); b.add_argument("--config", default="configs/attacks.yaml"); b.add_argument("--samples", type=int, default=1)
    sub.add_parser("export", help="导出匿名登记状态")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv); service = ProvenanceService(args.root)
    if args.command == "doctor": out = service.doctor()
    elif args.command == "keygen": out = service.keygen(args.private, args.public, _password(args.passphrase))
    elif args.command == "register": out = service.register(args.input, args.private, _password(args.passphrase), args.publisher, parent_content_id=args.parent)
    elif args.command == "derive": out = service.derive(args.source, args.private, _password(args.passphrase), args.operation, args.quality)
    elif args.command == "verify": out = service.verify(args.file, args.manifest, args.mode, args.state_snapshot, args.state_public)
    elif args.command == "transform": out = service.transform(args.input, args.output, args.operation, args.quality)
    elif args.command == "history": out = service.registry.history(args.content_id)
    elif args.command == "revoke": out = service.revoke(args.target_type, args.target_id, args.reason)
    elif args.command == "state-snapshot": out = service.state_snapshot(args.private, _password(args.passphrase), args.output, args.public, args.ttl)
    elif args.command == "export": out = {"status": service.registry.status(), "contents": service.registry.list_contents()}
    elif args.command == "benchmark": out = run_benchmark(args.output, args.config, args.samples)
    elif args.command == "demo": out = run_demo(service, Path(args.output))
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)); return 0


def run_demo(service: ProvenanceService, output: Path):
    from PIL import Image, ImageDraw
    import io, shutil
    output.mkdir(parents=True, exist_ok=True); private, public = output / "demo-private.pem", output / "demo-public.pem"; password = secrets.token_urlsafe(24)
    image = Image.new("RGB", (768, 768), (245, 248, 252)); draw = ImageDraw.Draw(image); draw.rounded_rectangle((64, 64, 704, 704), 24, fill=(31, 78, 121)); draw.text((210, 350), "GM Provenance", fill="white")
    source = output / "source.png"; image.save(source)
    service.keygen(private, public, password); published = service.register(source, private, password)
    exact = service.verify(published["media_path"], published["manifest_path"], "hard")
    full = service.verify(published["media_path"], published["manifest_path"], "full")
    manifest_copy = output / "manifest-copy.json"; shutil.copyfile(published["manifest_path"], manifest_copy)
    recovered = service.verify(published["media_path"], None, "recover")
    # 演示中保留密钥文件权限；产物摘要不包含口令。
    result = {"published": {k: v for k, v in published.items() if k not in ("manifest", "media_bytes", "watermark_profile")}, "exact": exact, "full_without_state": full, "recovered": recovered, "note": "hard 是凭证硬绑定检查；full 还需要新鲜状态快照；恢复来源线索不等于当前文件完整性通过"}
    (output / "demo-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        private.unlink()
    except FileNotFoundError:
        pass
    return result


if __name__ == "__main__": raise SystemExit(main())
