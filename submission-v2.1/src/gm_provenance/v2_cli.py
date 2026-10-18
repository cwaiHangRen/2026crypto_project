"""CLI for the v2 feature set; v1 ``gm-provenance`` remains unchanged."""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path


def _password(value: str | None) -> str:
    return value if value else getpass.getpass("请输入密钥口令（不会写入日志）：")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gm-provenance-v2", description="多媒体内容凭证、批量证明和可验证审计 v2")
    parser.add_argument("--root", default="data-v2")
    sub = parser.add_subparsers(dest="command", required=True)
    kg = sub.add_parser("keygen", help="生成 v2 发布者密钥")
    kg.add_argument("--publisher", required=True); kg.add_argument("--passphrase"); kg.add_argument("--key-id")
    ki = sub.add_parser("key-import", help="导入已有公钥元数据")
    ki.add_argument("--public", required=True); ki.add_argument("--publisher", required=True); ki.add_argument("--private-path")
    kr = sub.add_parser("key-rotate", help="轮换发布者密钥")
    kr.add_argument("--publisher", required=True); kr.add_argument("--passphrase"); kr.add_argument("--key-id")
    kv = sub.add_parser("key-revoke", help="吊销密钥")
    kv.add_argument("key_id"); kv.add_argument("--reason", required=True)
    sub.add_parser("key-status", help="查看密钥生命周期状态")
    reg = sub.add_parser("register", help="登记媒体并生成 v2 凭证")
    reg.add_argument("input"); reg.add_argument("--publisher", required=True); reg.add_argument("--key-id"); reg.add_argument("--passphrase")
    ver = sub.add_parser("verify", help="验证媒体、Manifest、水印和批量证明")
    ver.add_argument("input"); ver.add_argument("--content-id"); ver.add_argument("--batch-id"); ver.add_argument("--proof", default=""); ver.add_argument("--proof-file")
    bc = sub.add_parser("batch-create", help="创建 Merkle 批量凭证")
    bc.add_argument("content_ids", nargs="+"); bc.add_argument("--publisher", required=True); bc.add_argument("--key-id"); bc.add_argument("--passphrase")
    bp = sub.add_parser("batch-proof", help="导出一个内容的 Merkle proof")
    bp.add_argument("batch_id"); bp.add_argument("manifest_hash")
    av = sub.add_parser("audit-verify", help="验证审计哈希链")
    av.add_argument("--json", action="store_true")
    mi = sub.add_parser("manifest-inspect", help="显示规范化 Manifest 字段、长度和摘要")
    mi.add_argument("manifest")
    ar = sub.add_parser("attack-run", help="运行统一攻击矩阵")
    ar.add_argument("input"); ar.add_argument("--output", default="results/v2-attack")
    sub.add_parser("export", help="导出 v2 内容、批量和审计状态")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from .v2_service import V2Service
    from .key_manager import KeyManager
    root = Path(args.root)
    km = KeyManager(root)
    if args.command == "keygen":
        out = km.sanitized(km.generate(publisher_id=args.publisher, passphrase=_password(args.passphrase), key_id=args.key_id))
        from .audit_service import AuditService
        AuditService(root, crypto=km.crypto).append("keygen", actor=args.publisher, result="PASS", details={"key_id": out["key_id"], "algorithm": out["algorithm"]})
    elif args.command == "key-import":
        out = km.sanitized(km.import_public(args.public, args.publisher, private_path=args.private_path))
        from .audit_service import AuditService
        AuditService(root, crypto=km.crypto).append("key-import", actor=args.publisher, result="PASS", details={"key_id": out["key_id"], "algorithm": out["algorithm"]})
    elif args.command == "key-rotate":
        out = km.sanitized(km.rotate(publisher_id=args.publisher, passphrase=_password(args.passphrase), predecessor_key_id=args.key_id))
        from .audit_service import AuditService
        AuditService(root, crypto=km.crypto).append("key-rotate", actor=args.publisher, result="PASS", details={"key_id": out["key_id"], "predecessor_key_id": out.get("predecessor_key_id")})
    elif args.command == "key-revoke":
        out = km.sanitized(km.revoke(args.key_id, args.reason))
        from .audit_service import AuditService
        AuditService(root, crypto=km.crypto).append("key-revoke", actor="key-admin", result="PASS", details={"key_id": args.key_id, "reason": args.reason})
    elif args.command == "key-status":
        out = km.public_status()
    else:
        service = V2Service(root, key_manager=km)
        if args.command == "register":
            out = service.register_bytes(Path(args.input).read_bytes(), filename=Path(args.input).name, publisher_id=args.publisher,
                                         key_id=args.key_id, passphrase=_password(args.passphrase))
            out.pop("media_bytes_value", None)
        elif args.command == "verify":
            if args.proof_file:
                proof = json.loads(Path(args.proof_file).read_text(encoding="utf-8"))
            elif args.proof.strip().startswith("{"):
                proof = json.loads(args.proof)
            else:
                proof = [x for x in args.proof.split(",") if x] or None
            out = service.verify_bytes(Path(args.input).read_bytes(), content_id=args.content_id, batch_id=args.batch_id, proof=proof or None)
        elif args.command == "batch-create":
            out = service.create_batch(args.content_ids, publisher_id=args.publisher, key_id=args.key_id, passphrase=_password(args.passphrase))
        elif args.command == "batch-proof":
            from .batch_proof import proof_for
            batch = service.store.get_batch(args.batch_id)
            out = proof_for(batch, args.manifest_hash)
        elif args.command == "audit-verify":
            from .audit_service import AuditService
            out = AuditService(root, crypto=service.crypto).verify_chain()
        elif args.command == "manifest-inspect":
            from .manifest_v2 import inspect_manifest
            out = inspect_manifest(json.loads(Path(args.manifest).read_text(encoding="utf-8")), service.crypto)
        elif args.command == "attack-run":
            from .attack_lab import run_attack_matrix
            from .media_adapters import adapter_for
            raw = Path(args.input).read_bytes()
            adapter = adapter_for(raw)
            # Attack laboratory is intentionally a pure media operation.  The
            # CLI accepts an already watermarked file and recovers its ID first.
            detected = adapter.detect(raw)
            ids = detected.recovered_content_ids
            if len(ids) != 1:
                raise ValueError("attack-run 需要输入能唯一恢复 ContentID 的已发布媒体")
            out_dir = Path(args.output); out_dir.mkdir(parents=True, exist_ok=True)
            rows = run_attack_matrix(raw, ids[0], adapter=adapter, output_dir=out_dir, log_path=out_dir / "attack-matrix.json")
            out = {"count": len(rows), "results": [row.as_dict() for row in rows]}
        elif args.command == "export":
            out = {"contents": service.store.list_contents(), "audit": service.audit_events(), "audit_verification": service.audit_service().verify_chain()}
        else:
            raise ValueError(args.command)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
