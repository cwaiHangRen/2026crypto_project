"""测试用的显式身份初始化辅助函数。

密钥生成只创建密钥材料；需要发布或派生时，测试必须显式登记并信任公钥。
"""

from pathlib import Path

from gm_provenance.service import ProvenanceService


def provision_publisher(
    service: ProvenanceService,
    private_path: str | Path,
    public_path: str | Path,
    passphrase: str,
    publisher_id: str = "publisher-demo-001",
) -> dict[str, str]:
    """生成测试发布者密钥，并完成明确的登记/信任步骤。"""

    generated = service.keygen(private_path, public_path, passphrase)
    service.trust_key(public_path, publisher_id)
    return generated
