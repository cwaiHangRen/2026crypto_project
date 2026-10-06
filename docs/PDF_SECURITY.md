# PDF 结构与水印安全证据

本页记录 `media.embed` 与 `media.detect` 对 PDF 页级攻击的实际边界。发布端将 PDF 每页按 120 DPI 栅格化后嵌入同一个 ContentID，再生成新的 PDF；因此检测结果以渲染出的页为范围单位。它不会保留原 PDF 的文字层、书签或交互对象。

## 可复现实验

在项目根目录执行：

```text
python scripts/pdf_qa.py --output results/pdf-qa
```

脚本使用项目已有的 ReportLab、pypdf、pypdfium2、Pillow 和 NumPy，输出 `results/pdf-qa/pdf-qa.json` 及 `artifacts/`。JSON 中每个案例都包含期望状态、实际状态、ContentID 和页级结果；页面编号从 1 开始。测试代码在 `tests/security/test_pdf_attacks.py`，可在依赖齐备的环境执行 `pytest tests/security/test_pdf_attacks.py`。

## 结果语义

| 输入 | 文件状态 | 页级范围 |
| --- | --- | --- |
| 所有页面只有同一个可校验 ContentID | `FOUND` | 每个页面为 `FOUND`，`pages` 保留原输出顺序 |
| 页面没有水印，或同一 PDF 中存在未找到水印的页面 | `NOT_FOUND` | 未找到的页明确列为 `NOT_FOUND`；已找到的 ID 仍列在文件级 `content_ids` |
| 页面中校验出多个不同 ContentID | `CONFLICT` | 每页列出自己的 `content_ids`，文件级列出去重后的有序集合 |
| 截图 PNG/JPEG | 图片级 `FOUND`/`NOT_FOUND` | 图片检测没有 `pages`；截图不会伪造 PDF 页范围 |

页删除和页重排只改变 `pages` 的数量或顺序，保留下来的页仍应为 `FOUND`。将中间页替换为未加水印页面会得到文件级 `NOT_FOUND`，并在该页显示 `NOT_FOUND`。把一个页面替换为第二个 ContentID 会得到 `CONFLICT`。

## 截图边界

`screenshot-actual-pdfium-render.png` 是 PDFium 对已发布 PDF 的实际页面渲染；它验证水印在页面像素中可恢复。`screenshot-simulated-pillow-viewport.png` 是同一渲染结果经过 Pillow 缩放得到的模拟视口截图，用于隔离缩放影响，不能当作操作系统截图或屏幕取证。两者都按图片检测，结果没有 PDF 页范围。

## 输入限制

- 文件总大小超过 20 MiB：`inspect_media` 在解析前拒绝。
- PDF 超过 10 页：拒绝。
- 页面按 120 DPI 栅格化后超过 16 MP：发布/栅格化路径拒绝（`inspect_media` 只读取页面尺寸，不提前渲染）。

`detect` 对超大或无法解析的 PDF 返回 `NOT_FOUND` 并保留错误信息；20 MiB 的硬上限拒绝证据来自 `inspect_media`，16 MP 页面上限由 `embed` 的栅格化路径验证，因为检测接口需要把解析错误统一映射为检测状态。上限测试使用了 11 页、3000×3000 point 页面和 20 MiB 加 1 byte 的输入。

## 限制与未覆盖范围

当前证据是本地离线可复现的结构攻击矩阵，不是跨阅读器的完整兼容性声明。PDF 发布采用栅格化策略，因而无法证明原始文字对象、字体、注释、表单和脚本仍然存在。截图测试验证的是水印恢复，不证明截图来自可信设备。若运行环境没有 `reedsolo`，水印 profile 会标记 `crc-only-fallback`；这只能提供 CRC 校验，不能宣称 Reed-Solomon 纠错能力。媒体检测仍需与 Manifest 的最终字节摘要、签名和信任状态联合使用。
