# gm-provenance

[![Tests](https://github.com/cwaiHangRen/2026crypto_project/actions/workflows/ci.yml/badge.svg)](https://github.com/cwaiHangRen/2026crypto_project/actions/workflows/ci.yml)

面向 AI 生成内容的密码水印与可验证溯源系统原型。项目将 SM2/SM3、内容凭证、媒体水印、状态登记、版本派生和验证接口组合为一个可复现的本地服务。

## 项目范围

- 密码协议：SM2 签名、SM3 摘要、JCS 正文规范化、内容 ID 和状态快照。
- 媒体处理：JPEG、PNG 和 PDF 的来源水印与恢复验证；图片可显式选择兼容的
  `watermark-v1`、实验性 `watermark-v2-residual` 或有限反馈候选 `watermark-v2-feedback`，PDF 当前固定为 v1；水印只提供来源线索。
- PDF 真实页面流程：`gm-provenance pdf-raster` 使用 PDFium 固定 120 DPI、RGB/L 颜色空间和确定性尺寸渲染，逐页写出 rendered/final 页面图像及匿名结构化日志；最终 PNG/JPEG 编码字节必须盲恢复本次 ContentID 才允许报告 gate 通过。多页结果按页验证，并明确不把页面来源线索扩展为整本文档完整性证明。
- 输入质量门：图片及 PDF 页面支持不超过 16MP；超限样本安全拒绝，不生成发布字节。Round9 中 `wikimedia-artemis-earthrise` 与 `wikimedia-earth-from-space` 单列为 16MP 输入约束拒绝。
- 盲几何同步：图片可手动选择 `decoder-v2-sync` 做有界裁剪/偏移来源恢复；它默认关闭，预算耗尽和候选冲突必须按不确定结果处理，不能放宽发布 gate。Round 6 的冻结评测和性能结果见 [`docs/ROUND6_SYNC_FINAL_REPORT.md`](docs/ROUND6_SYNC_FINAL_REPORT.md)。
- 发布安全：登记和派生会对最终编码字节做盲恢复自检，只有恢复本次 ContentID 才进入摘要、签名和 Registry。
- 服务与演示：FastAPI 本地服务、中文演示页面、登记、验证、变换预览、历史和撤销接口。
- 验证与安全：单元、集成和攻击测试，以及 PDF 安全检查和离线数据集 pilot 验证。
- 数据集：数据集、图片/PDF 样本及评测结果均留在本地，不随 Git 仓库上传；clone 后需另行下载或导入并审核许可。无网络时可用 [`scripts/import_external_materials.py`](scripts/import_external_materials.py) 从本地 incoming 清单校验和去重，详细字段见 [`docs/ROUND8_HANDOFF.md`](docs/ROUND8_HANDOFF.md)。

系统验证的是签名、摘要、状态和媒体处理结果，不证明事实真实性、版权归属或内容一定由 AI 生成。

图片检测支持正交旋转、镜像及任意小数角度的自动校正和来源恢复。演示页可输入角度或随机旋转后验证；不同有效编号仍报告冲突。任意角度的适用边界、实测和复现见 [任意角度旋转恢复](docs/ARBITRARY_ROTATION.md)，原八方向验证见 [旋转镜像恢复](docs/ROTATION_MIRROR.md)。

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
- `POST /api/transform-preview`：比较 JPEG、缩放及任意角度/镜像旋转，可返回前后缩略图。
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

## v2 扩展（独立分支）

v2 保留 v1 稳定基线，新增确定性 Manifest v2、密钥生命周期、批量 Merkle proof、SM3 审计链、多媒体适配器、攻击矩阵和 Web 演示。v2 使用独立数据根和数据库，不改写 v1 registry。

安装与检查：

```powershell
python -m venv .venv-v2
.\.venv-v2\Scripts\python.exe -m pip install -e ".[test]"
.\.venv-v2\Scripts\python.exe -m gm_provenance.v2_cli --help
```

离线演示与导出：

```powershell
.\.venv-v2\Scripts\python.exe scripts\run_v2_demo.py
.\.venv-v2\Scripts\python.exe scripts\run_v2_pdf_demo.py
.\.venv-v2\Scripts\python.exe -m uvicorn gm_provenance.v2_api:app --host 127.0.0.1 --port 8001
.\.venv-v2\Scripts\python.exe -m pytest -q --basetemp tests\.tmp\v2 tests\v2
```

`run_v2_demo.py` 使用 `sample_data/v2-demo/` 中的 10 个固定 PNG，输出登记、批量 Merkle、攻击矩阵、审计导出和性能表；`run_v2_pdf_demo.py` 对 `sample_data/real_pdf_page1.pdf` 固定 120 DPI 栅格化并保存渲染页、最终 PNG、发布 PDF 和结构化日志。常用命令：`gm-provenance-v2 keygen`、`key-import`、`key-rotate`、`key-revoke`、`key-status`、`manifest-inspect`、`register`、`verify`、`batch-create`、`batch-proof`、`audit-verify`、`attack-run` 和 `export`。私钥只留在本地运行目录，不能进入报告、日志或提交包；MP3/FLAC/MP4 没有可选编解码器时显示 `UNAVAILABLE`。

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

真实 PDF 页面栅格化和截图/重编码验收（`--content-id` 使用本次测试或登记得到的 32 位十六进制编号）：

```powershell
.\.venv\Scripts\python.exe -m gm_provenance.cli pdf-raster sample.pdf --content-id 0123456789abcdef0123456789abcdef --output results\pdf-raster
```

命令会在输出目录保存 `source.pdf`、`pages/page-*-rendered.png`、最终编码页面和 `pdf-raster-log.json`。日志包含原始 PDF 摘要、页面尺寸、渲染/最终编码字节摘要、恢复出的 ContentID、gate、限制和验证回调结果；默认不写入用户名、私钥或令牌。需要将页面结果接入来源凭证检查时，调用 `gm_provenance.pdf_raster.process_pdf(..., verify_fn=...)`，在回调中执行服务层的 SM3、Manifest、SM2、版本链和撤销检查。

`hard` 检查凭证与实际字节绑定；`recover` 恢复来源线索；`full` 的当前状态结论还需要新鲜的签名状态快照，参见协议文档。图片受限裁剪恢复需显式增加 `--decoder-profile decoder-v2-sync`，默认关闭，不保证任意裁剪、旋转或镜像恢复；PDF 固定 v1。`decoder-v3-sync` 和 Round7 文件属于显式实验材料；旋转/镜像已在当前主分支整合，完整外部矩阵和队友后续 PDF 截图工作仍需单独验收。

### 裁剪模块封版交接

默认图片嵌入是 `watermark-v1`，普通来源检测是 D0 `dct-difference-v1`；用户主动选择深度裁剪恢复时使用 S0 `decoder-v2-sync`，v3 `decoder-v3-sync` 只可显式尝试。调用方应展示实际 decoder、`watermark_status`、候选 ID、`search_complete`、`search_stop_reason`、`checked_scope` 和耗时。恢复 ID 是来源线索，不能替代当前上传字节的 SM3、签名、父链、撤销和状态检查。队友接入命令与文件边界见 [`docs/ROUND8_HANDOFF.md`](docs/ROUND8_HANDOFF.md)；外部结果和缺口见 [`docs/ROUND8_FINAL_REPORT.md`](docs/ROUND8_FINAL_REPORT.md)。Round9 的 800 图片 + 200 PDF 默认规模基线、数据类别限制和未完成目标见 [`docs/ROUND9_SCALE_REPORT.md`](docs/ROUND9_SCALE_REPORT.md)；本轮新增 AI 图片和外部真实 PDF 实际为 0/20、0/10，不能写成 1030 条已完成样本。

\n