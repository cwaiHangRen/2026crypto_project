from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "submission/logs/round9/gate-failure-diagnostics.json"
    dst = src.with_suffix(".md")
    data = json.loads(src.read_text(encoding="utf-8"))
    lines = [
        "# Round9 六个 ContentID 恢复失败样本有界诊断",
        "",
        "生产 G3 在发布 gate 之前失败，没有产生最终 Manifest，因此生产 ContentID 不可得。本诊断为每个样本使用稳定的诊断 ContentID，逐样本最多运行 v1+D0、v1+S0、既有 v2-residual 三种配置；只有最终编码字节的 direct D0 恢复等于诊断 ContentID 才记为 gate 通过。",
        "",
        "|样本|原始尺寸/格式|诊断 CID|配置|最终编码/强度|候选 ContentID|RS/同步/MSE 摘要|结论|",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in data["rows"]:
        for config in row["configurations"]:
            if config.get("status") in {"timeout", "error"}:
                summary = config.get("failure_stage", "error") + " " + str(config.get("error", ""))
                gate = "TIMEOUT/ERROR"
                ids = "—"
            else:
                ids = ",".join(config.get("direct_detection", {}).get("candidate_content_ids", [])) or "无"
                gate = "PASS" if config.get("gate", {}).get("passed") else "FAIL"
                if config["configuration"] == "v1+S0":
                    sync = config.get("sync_detection", {})
                    summary = (
                        f"S0={sync.get('status')}; stop={sync.get('stop_reason')}; "
                        f"attempts={sync.get('decode_attempts')}"
                    )
                elif config["configuration"] == "existing-v2-candidate":
                    post = config.get("residual_diagnostic", {}).get("post_encode", {})
                    summary = (
                        f"RS={post.get('ecc_decoded')}/{post.get('ecc_matches_expected')}; "
                        f"bit_err={post.get('bit_errors_before_ecc')}; "
                        f"MSE系数误差={round(float(post.get('mean_abs_coefficient_error', 0.0)), 3)}"
                    )
                else:
                    summary = "RS/CRC=" + ("recovered" if gate == "PASS" else "not recovered")
            profile = config.get("embed_profile", {})
            encoding = f"{config.get('output_format', 'PNG')} / {profile.get('strength', '—')}"
            lines.append(
                f"|{row['sample_id']}|{row['original_dimensions']} / {row['original_format']}|"
                f"{row['diagnostic_content_id']}|{config['configuration']}|{encoding}|{ids}|{summary}|{gate}|"
            )
    lines.extend([
        "",
        "结论：v1+D0 在诊断 CID 下对 4 个样本可恢复，对 r8-img-0058 和 r8-img-0226 仍失败；v1+S0 没有为这两个样本提供恢复，并在既有预算内停止；既有 v2-residual 对 0058、0226、0178 未恢复。其他样本的诊断成功不等于原 Round9 生产 CID 成功。没有修改生产配置、gate 或 Round9 统计，也没有删除失败证据。",
        "",
    ])
    dst.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
