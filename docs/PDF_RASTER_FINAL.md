# PDF 真实页面栅格化与截图验收

本轮新增 `src/gm_provenance/pdf_raster.py`。它使用 PDFium 按固定 120 DPI 逐页渲染，统一到 RGB 或 L 颜色空间，以固定背景处理 alpha，并按页面尺寸推断输出像素。渲染后的 PNG 页面进入现有 `watermark-v1`，之后可执行 PNG/JPEG 编码与尺寸变化。检测器只接收最终编码字节；只有 `recovered_content_ids == [content_id]` 且状态为 `FOUND` 时，页面 gate 才通过。

`process_pdf` 的结果包含原始 PDF SHA-256、页面编号、渲染尺寸、渲染字节摘要、最终编码字节摘要、恢复 ID、gate、页面验证回调和限制。日志中的 artifact 路径只保存相对页面路径，避免把本地用户名写入提交材料。多页 PDF 逐页执行 gate 和回调，结果明确标记“未声明整本文档身份或语义”。

真实验收脚本为 `scripts/run_pdf_raster_acceptance.py`。它从本地真实 PDF 提取第一页，在隔离 Registry 中登记并以 hard 模式验证 SM3、Manifest、SM2、发布者信任、版本链和授权字段，再执行栅格化和最终字节盲恢复。实际结果保存在 `submission/logs/pdf-raster/`：`status=PASS`、`registration_allowed=true`、`page_count=1`，并保存 rendered/final 页面图和 `pdf-raster-log.json`。`submission/figures/` 中的三张 PDF 页面图来自该运行。

PDF 页面摘要只绑定页面编码字节；原始 Manifest 绑定完整发布 PDF。页面回调因此同时记录页面 SM3 和完整 PDF 凭证验证字段，不把页面摘要冒充整份 PDF 摘要。PDF 栅格发布会丢失文字层、书签和部分链接语义，报告中按质量日志单列该限制。
