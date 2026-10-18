# 完整数据集下载状态

更新时间：2026-10-06

用户已授权下载完整数据集后，本轮先核对官方归档大小并启动了可断点下载。由于当前网络对公开归档响应体的速度约为 43–64 KB/s，完整下载不能在本次执行窗口内可靠完成；项目没有把部分文件标记为完整数据集。

## 已固定的完整归档

| 数据集 | 归档 | 官方响应大小 |
|---|---|---:|
| COCO 2017 | `train2017.zip` | 19,336,861,798 bytes |
| COCO 2017 | `val2017.zip` | 815,585,330 bytes |
| COCO 2017 | `annotations_trainval2017.zip` | 252,907,541 bytes |
| DocLayNet | `DocLayNet_core.zip` | 30,012,083,650 bytes |
| DocLayNet | `DocLayNet_extra.zip` | 8,008,878,198 bytes |

官方入口：[COCO](https://cocodataset.org/)、[DocLayNet README](https://github.com/DS4SD/DocLayNet)。这些归档合计约 58.4 GB，目标目录为 `D:\gm-provenance-datasets\full-20261006\archives`。

## 当前本地状态

本轮已按用户要求中止下载进程，并保留已生成的部分文件。当前目录中只有下载探测、一个未完成的 COCO train 文件，以及 COCO 标注归档的部分 Range 片段：

- `train2017.zip`：5,058,560 bytes（约 4.8 MiB），远小于官方大小，未完成；
- `annotations_trainval2017.zip.parts\`：61 个分块文件（合计约 239.13 MiB），其中两个分块带 `.partial` 后缀，尚未拼接成最终 ZIP；
- `range-test.bin` 与 `doclaynet-range-test.bin`：各 1 MiB 的传输探测文件，不属于数据集；
- COCO/DocLayNet 各官方入口已通过响应头检查；
- 1 MiB Range 响应测试已成功，文件体可读取；
- 没有生成完整归档的 SHA-256、ZIP 清单或完整数据测试结果，也没有生成完整归档清单 `data/external-full-20261006/manifest.json`。

## 可复现下载命令

脚本：[download_full_selected.py](../scripts/download_full_selected.py)

```powershell
# 继续 COCO train/val/annotations；部分文件会自动续传
.\.venv\Scripts\python.exe scripts\download_full_selected.py --dataset COCO

# 下载 DocLayNet core/extra；部分文件会自动续传
.\.venv\Scripts\python.exe scripts\download_full_selected.py --dataset DocLayNet

# 明确要求重新请求完整归档
.\.venv\Scripts\python.exe scripts\download_full_selected.py --dataset all --refresh
```

下载完成后脚本会检查固定响应大小、计算 SHA-256、打开 ZIP 中央目录并读取一个样本成员，结果写入 `data/external-full-20261006/manifest.json`。只有五个归档全部通过后，才可以把完整归档标记为可测试。

## 尚未完整下载的来源

- **GenImage**：官方全集为百万级图像，公开镜像体量达到数百 GB，当前网络和本地工作目录不适合在本轮完整落地；现有 GenImage 文件仍只是官方示例资产。
- **arXiv**：不是一个有限、统一许可证的单一数据集；应按明确许可证筛选论文，而不是下载全部 arXiv 内容。现有 PDF 只是多页处理样本。

因此，当前项目状态应标记为“完整归档下载未完成、下载脚本已准备、存在可续传部分文件”，不能标记为完整数据集已落地或已完成大规模测试。
