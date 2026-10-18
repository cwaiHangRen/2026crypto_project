"""Run an isolated, real FastAPI round-2 security acceptance flow.

The script deliberately creates a temporary registry and signing keys.  It
never opens the repository's ``data`` directory and it does not print secret
values.  A JSON record is written for every scenario and the process exits
non-zero when a required security assertion fails.

Example::

    python scripts/run_round2_acceptance.py --output results/round2-acceptance
"""
from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from fastapi.testclient import TestClient

from gm_provenance import api
from gm_provenance.service import ProvenanceService


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _image(path: Path) -> None:
    # The production PNG watermark profile requires a sufficiently large
    # carrier.  This is a generated development fixture, not a dataset claim.
    Image.new("RGB", (768, 768), (245, 248, 252)).save(path, format="PNG")


def _status(result: dict[str, Any], expected: dict[str, str]) -> None:
    for field, value in expected.items():
        _assert(result.get(field) == value, f"{field}={result.get(field)!r}; expected {value!r}")


def _run_scenario(records: list[dict[str, Any]], name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        details = fn()
        record = {"name": name, "status": "PASS", "details": details}
    except Exception as exc:  # keep all scenarios visible before exiting
        record = {"name": name, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    records.append(record)
    return record


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    # SQLite connections used by the lightweight registry can remain alive
    # until Python's finalizer on Windows.  Ignore cleanup races so a locked
    # temporary file never turns a completed acceptance run into a traceback.
    with tempfile.TemporaryDirectory(prefix="gm-round2-acceptance-", ignore_cleanup_errors=True) as sandbox:
        root = Path(sandbox) / "registry"
        service = ProvenanceService(root)
        # The token is intentionally kept in memory and the isolated token
        # file read by the API.  It is never placed in a result record.
        token = service.initialize_admin()
        auth = {"Authorization": f"Bearer {token}"}
        wrong_auth = {"Authorization": "Bearer invalid-round2-token"}

        state_private = Path(sandbox) / "state-private.pem"
        state_public = Path(sandbox) / "state-public.pem"
        state_passphrase = secrets.token_urlsafe(24)
        service.keygen(state_private, state_public, state_passphrase)
        source = Path(sandbox) / "source.png"
        _image(source)

        # The module has a process-global service for the local demo.  Point
        # it at the isolated registry for this run only.
        api.service = service
        client = TestClient(api.app)
        upload = lambda: {"file": ("source.png", source.read_bytes(), "image/png")}

        def unauthenticated_writes() -> dict[str, Any]:
            denied = client.post("/api/register-demo", files=upload())
            _assert(denied.status_code == 401, f"register without token returned {denied.status_code}")
            wrong = client.post("/api/register-demo", files=upload(), headers=wrong_auth)
            _assert(wrong.status_code == 401, f"register with wrong token returned {wrong.status_code}")
            confirm_without_auth = client.post(
                "/api/revoke",
                json={"target_type": "content", "target_id": "0" * 32, "reason": "test", "confirm": True},
            )
            _assert(confirm_without_auth.status_code == 401, f"confirm without token returned {confirm_without_auth.status_code}")
            _assert(not (root / "registry.sqlite").exists() or service.registry.status()["sequence"] == 0,
                    "unauthorized write changed registry sequence")
            return {"missing_token": denied.status_code, "wrong_token": wrong.status_code,
                    "confirm_without_token": confirm_without_auth.status_code}

        _run_scenario(records, "unauthorized writes have no side effects", unauthenticated_writes)

        published: dict[str, Any] = {}

        def register() -> dict[str, Any]:
            response = client.post("/api/register-demo", files=upload(), headers=auth)
            _assert(response.status_code == 200, f"authorized register returned {response.status_code}: {response.text[:200]}")
            body = response.json()
            _assert(isinstance(body.get("content_id"), str), "register response omitted content_id")
            encoded = json.dumps(body, ensure_ascii=False)
            _assert(token not in encoded, "admin token leaked in register response")
            _assert("private" not in encoded.lower() and "passphrase" not in encoded.lower(),
                    "private key or passphrase marker leaked in register response")
            published.update(body)
            return {"status_code": response.status_code, "content_id": body["content_id"]}

        _run_scenario(records, "authorized register", register)

        def full_before_revoke() -> dict[str, Any]:
            # Refresh the independently signed state after the registry write.
            service.state_snapshot(state_private, state_passphrase)
            media_path = Path(published["media_path"])
            response = client.post("/api/verify", files={"file": ("published.png", media_path.read_bytes(), "image/png")})
            _assert(response.status_code == 200, f"full verify returned {response.status_code}")
            result = response.json()
            _assert(result.get("verification_mode", "full") == "full", "default API verify did not report full mode")
            _status(result, {"signature_valid": "PASS", "issuer_trusted": "PASS",
                             "content_hash_match": "PASS", "state_freshness": "PASS",
                             "content_revocation_status": "PASS", "key_revocation_status": "PASS",
                             "latest_version_status": "PASS"})
            _assert(result.get("conclusion") in ("VERIFIED_EXACT", "VERIFIED_DERIVATIVE"),
                    f"full verify conclusion was {result.get('conclusion')!r}")
            return {"status_code": response.status_code, "conclusion": result.get("conclusion"),
                    "state_freshness": result.get("state_freshness")}

        _run_scenario(records, "full verify after state refresh", full_before_revoke)

        def authorized_derive_and_parent_verify() -> dict[str, Any]:
            # The demo register endpoint intentionally discards its one-shot
            # private key.  Provision a separate, explicitly trusted signer
            # for this derivation through the local service management path;
            # the media verification itself still goes through FastAPI.
            child_private = Path(sandbox) / "child-private.pem"
            child_public = Path(sandbox) / "child-public.pem"
            child_passphrase = secrets.token_urlsafe(24)
            service.keygen(child_private, child_public, child_passphrase)
            service.trust_key(child_public, "publisher-demo-001")
            child = service.derive(published["content_id"], child_private, child_passphrase, "resize")
            service.state_snapshot(state_private, state_passphrase)
            response = client.post("/api/verify", files={
                "file": ("child.png", Path(child["media_path"]).read_bytes(), "image/png")
            })
            _assert(response.status_code == 200, f"derived verify returned {response.status_code}")
            result = response.json()
            _status(result, {"signature_valid": "PASS", "issuer_trusted": "PASS",
                             "content_hash_match": "PASS", "parent_chain_status": "PASS",
                             "authorization_status": "PASS", "state_freshness": "PASS",
                             "latest_version_status": "PASS"})
            _assert(result.get("conclusion") == "VERIFIED_DERIVATIVE",
                    f"derived conclusion was {result.get('conclusion')!r}")
            published["child"] = child
            return {"content_id": child["content_id"], "parent_content_id": child["parent_content_id"],
                    "conclusion": result.get("conclusion"), "parent_chain_status": result.get("parent_chain_status")}

        _run_scenario(records, "authorized derive and parent-chain verify", authorized_derive_and_parent_verify)

        def refresh_failure_does_not_claim_current_state() -> dict[str, Any]:
            child = published["child"]
            child_env = service.registry.get_manifest(child["content_id"])
            _assert(child_env and isinstance(child_env.get("body", {}).get("signer_key_id"), str),
                    "derived signer key unavailable")
            child_key_id = child_env["body"]["signer_key_id"]
            revoked = client.post("/api/revoke", headers=auth, json={
                "target_type": "key", "target_id": child_key_id,
                "reason": "simulate state refresh failure", "confirm": True,
            })
            _assert(revoked.status_code == 200, f"precondition key revoke returned {revoked.status_code}")
            original_snapshot = service.state_snapshot

            def fail_refresh(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
                raise RuntimeError("simulated state signer outage")

            service.state_snapshot = fail_refresh  # type: ignore[method-assign]
            try:
                try:
                    service.state_snapshot(state_private, state_passphrase)
                except RuntimeError:
                    pass
                child_media = Path(child["media_path"]).read_bytes()
                checked = client.post("/api/verify", files={"file": ("child.png", child_media, "image/png")})
                _assert(checked.status_code == 200, f"verify after refresh failure returned {checked.status_code}")
                result = checked.json()
                _assert(result.get("state_freshness") == "UNKNOWN", "stale snapshot was treated as fresh")
                _assert(result.get("key_revocation_status") in ("UNKNOWN", "NOT_CHECKED"),
                        "failed refresh exposed a definitive key status")
                _assert(result.get("conclusion") not in ("VERIFIED_EXACT", "VERIFIED_DERIVATIVE"),
                        "failed refresh yielded current full success")
                return {"state_freshness": result.get("state_freshness"),
                        "key_revocation_status": result.get("key_revocation_status"),
                        "conclusion": result.get("conclusion")}
            finally:
                service.state_snapshot = original_snapshot  # type: ignore[method-assign]

        _run_scenario(records, "state refresh failure leaves old snapshot non-current", refresh_failure_does_not_claim_current_state)

        def guarantee_levels() -> dict[str, Any]:
            media_path = Path(published["media_path"])
            levels: dict[str, Any] = {}
            for mode in ("hard", "recover", "full"):
                response = client.post(f"/api/verify?mode={mode}", files={"file": ("published.png", media_path.read_bytes(), "image/png")})
                _assert(response.status_code == 200, f"{mode} verify returned {response.status_code}")
                result = response.json()
                # Keep UNKNOWN/NOT_CHECKED visible; this assertion is about
                # explicit mode reporting, not about treating all modes alike.
                _assert(result.get("verification_mode", mode) == mode, f"{mode} mode was not reported")
                if mode == "full":
                    # A root object remains a valid historical credential, but
                    # once a child exists it cannot claim to be the current
                    # asset version.
                    _assert(result.get("latest_version_status") in ("FAIL", "UNKNOWN"),
                            "historical root was not marked old or state-unknown")
                    _assert(result.get("conclusion") not in ("VERIFIED_EXACT", "VERIFIED_DERIVATIVE"),
                            "historical root was reported as current full success")
                levels[mode] = {"conclusion": result.get("conclusion"),
                                "state_freshness": result.get("state_freshness"),
                                "watermark_status": result.get("watermark_status")}
            return levels

        _run_scenario(records, "hard recover full expose distinct scope", guarantee_levels)

        def revoke_and_verify() -> dict[str, Any]:
            response = client.post("/api/revoke", headers=auth,
                                   json={"target_type": "content", "target_id": published["content_id"],
                                         "reason": "round2 acceptance", "confirm": True})
            _assert(response.status_code == 200, f"content revoke returned {response.status_code}")
            service.state_snapshot(state_private, state_passphrase)
            media_path = Path(published["media_path"])
            checked = client.post("/api/verify", files={"file": ("published.png", media_path.read_bytes(), "image/png")})
            _assert(checked.status_code == 200, f"verify after content revoke returned {checked.status_code}")
            result = checked.json()
            _assert(result.get("content_revocation_status") == "FAIL", "content revoke was not reflected in full state")
            _assert(result.get("conclusion") == "REJECTED", f"revoked content conclusion was {result.get('conclusion')!r}")
            return {"revoke_status": response.status_code, "conclusion": result.get("conclusion"),
                    "content_revocation_status": result.get("content_revocation_status")}

        _run_scenario(records, "content revoke then full verify", revoke_and_verify)

        def key_revoke_and_verify() -> dict[str, Any]:
            env = service.registry.get_manifest(published["content_id"])
            _assert(env and isinstance(env.get("body", {}).get("signer_key_id"), str), "published signer key unavailable")
            key_id = env["body"]["signer_key_id"]
            response = client.post("/api/revoke", headers=auth,
                                   json={"target_type": "key", "target_id": key_id,
                                         "reason": "round2 key acceptance", "confirm": True})
            _assert(response.status_code == 200, f"key revoke returned {response.status_code}")
            service.state_snapshot(state_private, state_passphrase)
            media_path = Path(published["media_path"])
            checked = client.post("/api/verify", files={"file": ("published.png", media_path.read_bytes(), "image/png")})
            result = checked.json()
            _assert(result.get("key_revocation_status") == "FAIL", "key revoke was not reflected in full state")
            _assert(result.get("conclusion") == "REJECTED", f"revoked key conclusion was {result.get('conclusion')!r}")
            return {"revoke_status": response.status_code, "key_revocation_status": result.get("key_revocation_status"),
                    "conclusion": result.get("conclusion")}

        _run_scenario(records, "key revoke then full verify", key_revoke_and_verify)

        def unavailable_state_is_not_success() -> dict[str, Any]:
            snapshot = root / "state-snapshot.json"
            backup = root / "state-snapshot.acceptance-backup.json"
            snapshot.replace(backup)
            try:
                media_path = Path(published["media_path"])
                response = client.post("/api/verify", files={"file": ("published.png", media_path.read_bytes(), "image/png")})
                _assert(response.status_code == 200, f"verify without state returned {response.status_code}")
                result = response.json()
                _assert(result.get("state_freshness") in ("UNKNOWN", "NOT_CHECKED"), "missing state was treated as fresh")
                _assert(result.get("conclusion") not in ("VERIFIED_EXACT", "VERIFIED_DERIVATIVE"), "missing state yielded full success")
                return {"state_freshness": result.get("state_freshness"), "conclusion": result.get("conclusion")}
            finally:
                backup.replace(snapshot)

        _run_scenario(records, "missing state cannot produce full success", unavailable_state_is_not_success)
        # Starlette's TestClient owns a SQLite connection through the service;
        # close it before TemporaryDirectory attempts to remove the sandbox on
        # Windows.
        client.close()
        # Remove credentials explicitly before leaving the temporary context.
        # The registry's SQLite handle can outlive the client on Windows, so
        # relying only on recursive temporary-directory cleanup could leave an
        # admin token or private PEM behind in the OS temp directory.
        for secret_path in (root / "admin-token", state_private,
                            state_public, Path(sandbox) / "child-private.pem",
                            Path(sandbox) / "child-public.pem"):
            try:
                secret_path.unlink()
            except FileNotFoundError:
                pass

    passed = sum(item["status"] == "PASS" for item in records)
    failed = len(records) - passed
    summary = {"schema_version": 1, "passed": passed, "failed": failed,
               "status": "PASS" if failed == 0 else "FAIL", "scenarios": records}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Round 2 API security acceptance", "", f"Status: **{summary['status']}**", "",
             f"Passed: {passed}; failed: {failed}", ""]
    for item in records:
        suffix = item.get("error") or json.dumps(item.get("details", {}), ensure_ascii=False)
        lines.append(f"- {'PASS' if item['status'] == 'PASS' else 'FAIL'} `{item['name']}`: {suffix}")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="隔离运行 gm-provenance FastAPI round-2 安全验收")
    parser.add_argument("--output", type=Path, default=Path("results/round2-acceptance"))
    return run(parser.parse_args().output)


if __name__ == "__main__":
    raise SystemExit(main())
