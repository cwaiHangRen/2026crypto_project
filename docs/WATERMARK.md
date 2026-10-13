# 图片与 PDF 水印基线

当前基线用于恢复来源线索，不能替代最终发布字节的 SM3 摘要、Manifest 或签名。

## 图片

`embed_image(data, content_id, output_format)` 严格接受 32 位小写十六进制 ContentID。图片在内部归一化到 512×512，按 8×8 DCT 块在 `(2,3)/(3,2)` 系数差中写入版本（1）、128 bit ContentID 和 CRC32；Reed-Solomon RS(53,21) 提供 32 字节纠错，每一位写入 4 个块。发布后恢复原始宽高，默认采用 PNG，JPEG 输出质量为 95。

输入上限为 20 MB、16 MP。检测不需要原图、真值或 ContentID。检测端自动搜索四种正交旋转与左右镜像后的四种旋转，共八种方向；成功返回 `FOUND` 和唯一编号，无候选返回 `NOT_FOUND`，不同方向出现不同可校验编号时返回 `CONFLICT`。相同编号去重，所有方向均检查，不在首个成功候选处提前结束。

图片仅解码一次，先检查归一化灰度矩阵的八个正交方向，再从纯色或透明边框估计任意角度几何校正候选。斜转校正在原图像素坐标中执行，随后才归一化到 512×512；不能先把矩形压成方形再反旋转。完整矩形和裁切假设的候选均有数量上限，所有有效编号一起去重/判冲突。DCT 按块批量执行，载荷与嵌入参数保持 v1 兼容。

对裁切假设，只让完整可见的重复块投票；某位的所有重复块均缺失时，将对应字节标为 RS 擦除，超过 32 字节则拒绝该候选。恢复仍需正确版本、RS 解码和 CRC 校验，几何边界信号本身不能产生 ContentID。原方向的多数票行为保持原有规则。

`details.orientations_checked` 记录八个基础方向，`details.rotation_recovery` 记录几何后端、候选上限及实际估计，`details.matches` 记录有效候选。角度是检测时的校正估计，不认证实际传播操作；没有有效水印时仍返回 `NOT_FOUND`。详细范围、实测与复现方法见 [ARBITRARY_ROTATION.md](ARBITRARY_ROTATION.md) 和 [ROTATION_MIRROR.md](ROTATION_MIRROR.md)。

运行环境安装 `reedsolo` 时使用真实 RS(53,21) 纠错；离线运行时会明确在 profile 标记 `crc-only-fallback (RS unavailable)`，此模式只校验 CRC，不把它报告为纠错能力。

该实现是公开参数的鲁棒基线，缩放、轻度 JPEG 重编码属于实验范围；裁剪、强压缩和截图结果需要通过实际测试确认，不能据此宣称达标。

## PDF

PDF 使用 pypdfium2 以 120 DPI 渲染每一页，再将每页水印 PNG 通过 ReportLab 重建为新 PDF。这样会丢弃原文字层、书签及交互对象；这是明确的媒体适配限制。最多 10 页、输入 20 MB。检测逐页执行并保留页面详情；页面恢复出多个不同 ContentID 时返回 `CONFLICT`。

`inspect_media` 返回媒体类型、尺寸或页数。所有解析失败、超限和不支持类型都返回明确异常，调用层应将其作为输入错误处理。
