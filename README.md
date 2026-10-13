# gm-provenance

[![Tests](https://github.com/cwaiHangRen/2026crypto_project/actions/workflows/ci.yml/badge.svg)](https://github.com/cwaiHangRen/2026crypto_project/actions/workflows/ci.yml)

面向 AI 生成内容的密码水印与可验证溯源系统原型。项目将 SM2/SM3、内容凭证、媒体水印、状态登记、版本派生和验证接口组合为一个可复现的本地服务。

## 项目范围

- 密码协议：SM2 签名、SM3 摘要、JCS 正文规范化、内容 ID 和状态快照。
- 媒体处理：JPEG、PNG 和 PDF 的来源水印与恢复验证；图片可显式选择兼容的
  `watermark-v1`、实验性 `watermark-v2-residual` 或有限反馈候选 `watermark-v2-feedback`，PDF 当前固定为 v1；水印只提供来源线索。
- 盲几何同步：图片可手动选择 `decoder-v2-sync` 做有界裁剪/偏移来源恢复；它默认关闭，预算耗尽和候选冲突必须按不确定结果处理，不能放宽发布 gate。Round 6 的冻结评测和性能结果见 [`docs/ROUND6_SYNC_FINAL_REPORT.md`](docs/ROUND6_SYNC_FINAL_REPORT.md)。
- 发布安全：登记和派生会对最终编码字节做盲恢复自检，只有恢复本次 ContentID 才进入摘要、签名和 Registry。
- 服务与演示：FastAPI 本地服务、中文演示页面、登记、验证、变换预览、历史和撤销接口。
- 验证与安全：单元、集成和攻击测试，以及 PDF 安全检查和离线数据集 pilot 验证。
- 数据集：数据集、图片/PDF 样本及评测结果均留在本地，不随 Git 仓库上传；clone 后需另行下载或导入并审核许可。

系统验证的是签名、摘要、状态和媒体处理结果，不证明事实真实性、版权归属或内容一定由 AI 生成。

## 快速开始

建议使用 Python 3.12（最低支持 Python 3.11）。项目依赖的唯一来源是 `pyproject.toml`；`requirements.txt` 只是兼容入口：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
# 也可以使用：.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

需要可执行的 OpenSSL（支持 SM2/SM3），通过 PATH 或 `GM_OPENSSL` 指定；Windows 也会探测 `C:/msys64/ucrt64/bin/openssl.exe`。先运行下方环境检查和 `doctor`，确认实际后端。

运行快速演示：

```powershell
.\.venv\Scripts\python.exe scripts\run.py --root results\smoke doctor
.\.venv\Scripts\python.exe scripts\run.py --root results\smoke demo --output results\demo
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

运行测试：

```powershell
$testTemp = ".pytest-tmp-" + [guid]::NewGuid().ToString("N")
.\.venv\Scripts\python.exe -m pytest -q --basetemp $testTemp
```

`--basetemp` 会清空目标目录：每次使用新的专用目录，不指向已有数据、密钥或结果。

Round 3 准备脚本依赖 `data/external-pilot-20261006/` 的六个本地 pilot 文件，clone 不包含它们。先按 `scripts/download_dataset_pilot.py` 和数据集文档准备并核验来源与许可，再运行开发基线（默认串行 PDF）：

```powershell
  .\.venv\Scripts\python.exe scripts\prepare_round3_dataset.py --output data\round3-development-v3
.\.venv\Scripts\python.exe scripts\run_round3_evaluation.py --stage 100 --workers 1
```

每次运行都会在 `results/pilot-round3/<run_id>/` 新建 `run_manifest.json`、样本/攻击/计时/质量 JSONL、`summary.json`、`report.md` 和可视化对照文件；运行器会先校验样本 SHA-256，并在没有实际 `reedsolo` 后端时拒绝正式评测。

检查当前解释器和关键依赖：

```powershell
.\.venv\Scripts\python.exe scripts\check_environment.py
```

启动本地 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn gm_provenance.api:app --host 127.0.0.1 --port 8000
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
.\.venv\Scripts\python.exe scripts\download_all_datasets.py --status
```

开始下载缺失或未完成资源：

```powershell
.\.venv\Scripts\python.exe scripts\download_all_datasets.py
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
data/                    本地样本、密钥、数据库和清单；不随仓库上传
results/                 本地验证结果；不作为源代码发布
```

## 重要约束

- 签名顺序必须是：随机 ContentID → 嵌入 → 最终编码字节 → SM3 → JCS 正文 → SM2 → 事务登记。
- 签名后禁止修改发布字节。
- 摘要不匹配不等于恶意；未知状态不得报告为未撤销。
- 未执行的检查必须报告为 `NOT_CHECKED`。
- 默认不访问凭证 URL，不记录私钥和令牌。

更多设计和验证证据见 [`docs/`](docs/)、[`docs/PROGRESS.md`](docs/PROGRESS.md) 和 [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md)。

### 本轮安全边界
`keygen` 只生成密钥，不授予信任；需显式运行 `python -m gm_provenance.cli --root data trust --public <公钥> --publisher <发布者>`。API 写操作需要先运行 `init-admin` 并通过 `Authorization: Bearer` 传入令牌。`/api/verify` 默认使用 `full` 模式，也可显式传 `hard`、`recover`。

Round 2 隔离验收：`python scripts/run_round2_acceptance.py --output results/round2-acceptance`。脚本使用临时登记库和测试密钥，输出 `summary.json`、`report.md`，不修改项目 `data/`。

## 本地初始化与图片/PDF 操作

以下命令按当前 CLI 参数提供；密钥口令交互输入，不放入命令行。管理员初始化会输出一次令牌，请保存在本地，勿录入日志或提交。API 使用同一默认 `data` 根目录，网页写操作需提供该管理员令牌。

```powershell
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data keygen --private data\publisher-private.pem --public data\publisher-public.pem
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data trust --public data\publisher-public.pem --publisher publisher-demo-001
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data init-admin
# input.png 换为本地有权使用的 PNG/JPEG 或 PDF；记录输出的 media_path 与 manifest_path
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data register input.png --private data\publisher-private.pem --publisher publisher-demo-001
# 以下尖括号为上一步实际输出，执行前替换
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data verify "<media_path>" --manifest "<manifest_path>" --mode hard
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data verify "<media_path>" --mode recover
```

`hard` 检查凭证与实际字节绑定；`recover` 恢复来源线索；`full` 的当前状态结论还需要新鲜的签名状态快照，参见协议文档。图片受限裁剪恢复需显式增加 `--decoder-profile decoder-v2-sync`，默认关闭，不保证任意裁剪、旋转或镜像恢复；PDF 固定 v1。`decoder-v3-sync` 和 Round7 文件属于显式实验材料，不表示旋转镜像、完整外部矩阵或队友后续 PDF 截图工作已经完成。
