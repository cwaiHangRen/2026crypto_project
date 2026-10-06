# 图片与 PDF 水印基线

当前基线用于恢复来源线索，不能替代最终发布字节的 SM3 摘要、Manifest 或签名。

## 图片

`embed_image(data, content_id, output_format)` 严格接受 32 位小写十六进制 ContentID。图片在内部归一化到 512×512，按 8×8 DCT 块在 `(2,3)/(3,2)` 系数差中写入版本（1）、128 bit ContentID 和 CRC32；Reed-Solomon RS(53,21) 提供 32 字节纠错，每一位写入 4 个块。发布后恢复原始宽高，默认采用 PNG，JPEG 输出质量为 95。

输入上限为 20 MB、16 MP。检测不需要原图、真值或 ContentID，成功返回 `FOUND` 和恢复的候选；无候选返回 `NOT_FOUND`。当前单个候选实现不会制造冲突，未来扩展多候选时必须保留 `CONFLICT` 语义。

运行环境安装 `reedsolo` 时使用真实 RS(53,21) 纠错；离线运行时会明确在 profile 标记 `crc-only-fallback (RS unavailable)`，此模式只校验 CRC，不把它报告为纠错能力。

该实现是公开参数的鲁棒基线，缩放、轻度 JPEG 重编码属于实验范围；裁剪、强压缩和截图结果需要通过实际测试确认，不能据此宣称达标。

## PDF

PDF 使用 pypdfium2 以 120 DPI 渲染每一页，再将每页水印 PNG 通过 ReportLab 重建为新 PDF。这样会丢弃原文字层、书签及交互对象；这是明确的媒体适配限制。最多 10 页、输入 20 MB。检测逐页执行并保留页面详情；页面恢复出多个不同 ContentID 时返回 `CONFLICT`。

`inspect_media` 返回媒体类型、尺寸或页数。所有解析失败、超限和不支持类型都返回明确异常，调用层应将其作为输入错误处理。
