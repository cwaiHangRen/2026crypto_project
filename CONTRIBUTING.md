# 协作说明

## 分支和合并

1. 从 `main` 创建一个短期功能分支。
2. 保持提交主题清楚，避免把数据集、缓存和本地密钥加入提交。
3. 提交前运行离线状态检查和与改动相关的测试。
4. 通过 Pull Request 说明改动、验证命令和已知限制，再合并到 `main`。

## 数据和运行产物

完整数据集放在本机 D 盘或其他外部目录。使用 `scripts/download_all_datasets.py --status` 检查本地状态，不要把归档、解压数据和私钥上传到 GitHub。

## 安全要求

- 不提交私钥、令牌、证书、`.env` 文件或包含凭证的日志。
- 默认只使用 `127.0.0.1` 监听本地服务。
- 不自动访问凭证 URL。
- 摘要不匹配只表示验证失败，不能单独推断恶意行为。

## 提交前检查

```powershell
python scripts\download_all_datasets.py --status
python -m pytest -q --basetemp results\validation-installed\tmp
git status --short
```
