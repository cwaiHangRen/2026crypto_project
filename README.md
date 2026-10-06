# gm-provenance

面向 AI 生成内容的密码水印与可验证溯源系统原型。项目将 SM2/SM3、内容凭证、媒体水印、状态登记、版本派生和验证接口组合为一个可复现的本地服务。

## 项目范围

- 密码协议：SM2 签名、SM3 摘要、JCS 正文规范化、内容 ID 和状态快照。
- 媒体处理：JPEG、PNG 和 PDF 的来源水印与恢复验证；水印只提供来源线索。
- 服务与演示：FastAPI 本地服务、中文演示页面、登记、验证、变换预览、历史和撤销接口。
- 验证与安全：单元、集成和攻击测试，以及 PDF 安全检查和离线数据集 pilot 验证。
- 数据集：项目内保留小型可复现实验样本；完整 COCO/DocLayNet 归档默认放在 D 盘，不进入 Git 仓库。

系统验证的是签名、摘要、状态和媒体处理结果，不证明事实真实性、版权归属或内容一定由 AI 生成。

## 快速开始

建议使用 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

运行快速演示：

```powershell
python scripts\run.py --root results\smoke doctor
python scripts\run.py --root results\smoke demo --output results\demo
python scripts\smoke_test.py
```

运行测试：

```powershell
python -m pytest -q --basetemp results\validation-installed\tmp
```

启动本地 API：

```powershell
python -m uvicorn gm_provenance.api:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/`。服务默认只监听本机回环地址。

主要接口包括：

- `POST /api/register-demo`：登记并发布演示内容。
- `POST /api/verify`：验证文件和内容凭证。
- `POST /api/transform-preview`：比较 JPEG、缩放等传播变换。
- `GET /api/history/{content_id}`：查看版本链和状态。
- `POST /api/revoke`：显式确认后撤销内容。
- `GET /api/benchmark`：运行真实 benchmark。

## 数据集下载

统一入口是 [`scripts/download_all_datasets.py`](scripts/download_all_datasets.py)。它会检查已有文件，验证通过的文件直接跳过，部分归档断点续传，缺失归档才下载。

先做离线检查：

```powershell
python scripts\download_all_datasets.py --status
```

开始下载缺失或未完成资源：

```powershell
python scripts\download_all_datasets.py
```

完整归档默认写入 `D:\gm-provenance-datasets\full-20261006\archives`。可通过 `--full-root` 或环境变量 `GM_PROVENANCE_FULL_DATASETS` 更换位置。下载状态写入 `data/dataset-downloads-20261006/manifest.json`，该目录属于运行产物，不提交到 Git。

GenImage 和 arXiv 在本项目中保留的是官方示例/论文样本。它们没有一个固定大小、统一许可的一键全集，因此脚本会明确报告完整全集不可固定下载，不会把样本误标为完整数据集。

## 协作和提交

建议使用私有 GitHub 仓库进行早期协作。提交代码、测试、文档和配置，不提交以下内容：

- 数据集、归档、解压目录和大型 benchmark 输出；
- `.venv`、缓存、构建产物和本地数据库；
- 私钥、令牌、证书、个人配置和包含敏感信息的日志。

推荐流程：从 `main` 创建短期分支，完成测试后提交 Pull Request；不要直接把未审阅的实验产物推到 `main`。

## 目录说明

```text
src/gm_provenance/       核心协议、媒体、水印、状态和 API
scripts/                 演示、测试、数据集检查和下载入口
tests/                   单元、集成和安全测试
docs/                    协议、威胁模型、数据集和验证记录
configs/                 benchmark 和攻击配置
data/                    小型样本及运行时清单；完整数据默认在 D 盘
results/                 本地验证结果；不作为源代码发布
```

## 重要约束

- 签名顺序必须是：随机 ContentID → 嵌入 → 最终编码字节 → SM3 → JCS 正文 → SM2 → 事务登记。
- 签名后禁止修改发布字节。
- 摘要不匹配不等于恶意；未知状态不得报告为未撤销。
- 未执行的检查必须报告为 `NOT_CHECKED`。
- 默认不访问凭证 URL，不记录私钥和令牌。

更多设计和验证证据见 [`docs/`](docs/)、[`docs/PROGRESS.md`](docs/PROGRESS.md) 和 [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md)。
