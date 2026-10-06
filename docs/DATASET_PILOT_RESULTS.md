# 数据集小样本下载与初步测试结果

测试日期：2026-10-06

本轮按“先下载少量样本，再接入现有协议流程”的方式执行，没有下载 COCO、GenImage 或 DocLayNet 的完整压缩包。

## 下载内容

下载目录：[data/external-pilot-20261006](../data/external-pilot-20261006)

| 来源 | 样本 | 状态 | 说明 |
|---|---:|---|---|
| COCO | 3 张 JPEG | 已下载 | 官方 `val2017` 图片地址；当前环境对该域名出现证书主机名不匹配，清单记录了受限 TLS 回退 |
| GenImage | 1 个 PNG | 已下载 | 官方仓库 `Examples/visulization.png`，是官方示例资产，不是原始 benchmark 行 |
| DocLayNet | 1 个 PNG | 已下载 | 官方仓库 README 使用的示例页，不是 28 GiB core archive |
| arXiv | 1 个 9 页 PDF | 已下载 | DocLayNet 论文 PDF，用于多页 PDF 管线测试；论文许可证仍标为 review |

统一清单：[manifest.json](../data/external-pilot-20261006/manifest.json)

清单记录了来源 URL、响应类型、下载时间、字节数、SHA-256、SM3、文件头、图片尺寸、PDF 页数和许可证状态。所有 6 条记录的 `license_status` 仍为 `review`，这表示已经保留证据入口，但还没有完成逐文件许可证批准。

## 初步测试

测试报告：[report.json](../results/dataset-pilot-20261006/report.json)

图片质量诊断：[quality.json](../results/dataset-pilot-20261006/quality.json)

使用当前项目的媒体适配、SM2/SM3 注册、内容凭证、水印恢复和验证服务进行本地联调：

- 6/6 文件通过基础媒体检查和项目注册；
- 6/6 文件通过 `hard` 模式精确验证，内容哈希和签名结果为 `PASS`；
- 6/6 文件通过水印恢复，恢复到唯一 ContentID；
- 5 个图片样本完成 JPEG 重编码后恢复，结果为 `ORIGIN_HINT_ONLY`，符合项目对传播副本的语义；
- 1 个 PDF 样本识别为 9 页，完成逐页栅格化、发布、精确验证和恢复；
- PDF 样本没有执行图片专用的 JPEG 派生操作，因为当前服务的 `derive` 变换只支持 PNG/JPEG。

对 5 个图片样本还计算了发布前后的全局 RGB PSNR 和诊断用全局 SSIM。当前样本 PSNR 范围为 24.87–33.10 dB、全局 SSIM 范围为 0.9789–0.9952；这些数值只描述这 5 个样本的本地水印质量，不构成项目质量阈值或完整数据集结论。PDF 因为发布时栅格化为新文档，没有与原始文字层做像素级质量对照。

可复现命令：

```powershell
.\.venv\Scripts\python.exe scripts\download_dataset_pilot.py
.\.venv\Scripts\python.exe scripts\test_dataset_pilot.py
.\.venv\Scripts\python.exe scripts\dataset_pilot_quality.py
```

下载脚本现在默认复用已有文件并重新计算 SHA-256/SM3，避免重跑时因远端文件变化导致结果漂移。需要明确重新请求远端时使用 `--refresh`；只做本地复核、不允许任何网络访问时使用 `--offline`：

```powershell
# 只校验当前目录中的 6 个样本（不会访问网络）
python scripts\download_dataset_pilot.py --offline

# 明确重新下载这 6 个 pilot 样本（仍然不会下载完整数据集）
python scripts\download_dataset_pilot.py --refresh
```

每条清单记录包含 `retrieval_mode`（`existing_local`、`network` 或 `network_refresh`）、`verified_at`、`expected_sha256`/`expected_sm3`、`hash_match`/`sm3_match`、响应状态和内容类型。下载失败、HTTP 错误、本地文件缺失或哈希不一致都会写入清单，而不是静默跳过。`license_status=review` 仍只表示许可证证据已保留，不能视为许可批准。

上述命令中，带 `--refresh` 的下载需要网络访问；`--offline` 与后两条测试命令不访问网络。

## 结果边界

这次结果证明的是：项目可以接收这些媒体形态的小样本，并完成文件检查、注册、签名验证、水印恢复和图片传播提示。它不证明完整数据集已经下载，也不证明外部数据集的逐文件许可证已经批准。

尤其需要注意：GenImage 和 DocLayNet 当前下载的是官方仓库示例资产；arXiv 文件是用于多页 PDF 管线的论文样本。下一阶段如果要建立正式基准，必须从各自的原始数据发布入口固定版本、拆分规则和逐文件许可记录，再把真实 benchmark 行写入清单。
