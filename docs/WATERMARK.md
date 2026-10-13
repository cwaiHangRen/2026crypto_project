# 图片与 PDF 水印基线

当前基线用于恢复来源线索，不能替代最终发布字节的 SM3 摘要、Manifest 或签名。

## 图片

`embed_image(data, content_id, output_format)` 严格接受 32 位小写十六进制 ContentID。图片在内部归一化到 512×512，按 8×8 DCT 块在 `(2,3)/(3,2)` 系数差中写入版本（1）、128 bit ContentID 和 CRC32；Reed-Solomon RS(53,21) 提供 32 字节纠错，每一位写入 4 个块。发布后恢复原始宽高，默认采用 PNG，JPEG 输出质量为 95。

输入上限为 20 MB、16 MP。检测不需要原图、真值或 ContentID，成功返回 `FOUND` 和恢复的候选；无候选返回 `NOT_FOUND`。当前单个候选实现不会制造冲突，未来扩展多候选时必须保留 `CONFLICT` 语义。

运行环境安装 `reedsolo` 时使用真实 RS(53,21) 纠错；离线运行时会明确在 profile 标记 `crc-only-fallback (RS unavailable)`，此模式只校验 CRC，不把它报告为纠错能力。

该实现是公开参数的鲁棒基线，缩放、轻度 JPEG 重编码属于实验范围；裁剪、强压缩和截图结果需要通过实际测试确认，不能据此宣称达标。

## decoder-v2-sync 盲几何同步

图片检测可以显式选择 `decoder-v2-sync`。它先执行 v1 的 D0 快速检测，未找到时在固定面积比例与偏移网格内，把当前上传字节重建到 512×512 工作画布，再执行相同的 RS/CRC 载荷解码。同步检测只接收图片字节和公开 `SyncConfig`，不读取原图、ContentID、路径、Registry 或 Manifest；重建画布只用于读取水印，不参与用户实际上传字节的 SM3。

该 profile 默认关闭，最多 128 个候选、128 次解码、1,000 ms 候选间软时间预算和 256 MB 估算内存预算。返回值必须同时查看 `search_complete`、`stop_reason`、候选数和 `checked_scope`；预算耗尽或未穷尽搜索不得解释为穷尽范围内没有来源。找到裁剪来源时，服务仍将实际上传字节摘要与签名验证保持独立，通常只能得到 `ORIGIN_HINT_ONLY`。

Round 6 的完整冻结评测、失败归因和性能结果见 [`ROUND6_SYNC_FINAL_REPORT.md`](ROUND6_SYNC_FINAL_REPORT.md)。当前结论支持把同步保留为手动开启的实验性来源恢复路径，不支持把它改成默认发布或完整性 gate。

## Round 7 `decoder-v3-sync` 几何候选

Round 7 对 Round6 的 882 条 `oracle` 正确而 S0 未正确恢复记录做了逐条几何诊断。旧 oracle 会按原生画布重建，S0 则把观测图像先压到方形工作域；两条路径的平均工作图 MSE 为 619.068876。新增 `decoder-v3-sync` 使用观测宽高和公开面积假设推断原生宽高，在固定灰度 128 画布中放置观测区域，再以 Lanczos 一次重采样到 512 工作域，诊断平均 MSE 降至 56.781891。函数 `reconstruct_work_gray` 不接收 ContentID、Registry、原图尺寸或真实裁剪框；旧 oracle 仍保留用于独立对照。

v3 沿用 S0 的 128 候选、128 次解码、1,000 ms 软搜索预算、256 MB 估算内存和最多 8 个返回 ID。Round7 全矩阵使用相同发布字节、ContentID、变换和判定规则，v3 相对 S0 挽回 545 条但产生 244 条严格退步，因此只作为手动显式实验候选保留。默认仍为 v1，PDF 仍为 v1，发布 gate 仍是直接 D0 检测；同步画布不参与用户上传字节的 SM3、Manifest 或签名。

真实外部挑战结果和性能证据见 [`ROUND7_FINAL_REPORT.md`](ROUND7_FINAL_REPORT.md)。12 张许可依据可追踪的新图片原图盲测未出现误检；通过 gate 的标记 exact 配对 27/27 成功，但高分辨率图片上的完整 22 变换外部矩阵因运行成本中止，不能将本轮描述为完整真实世界验证。

## watermark-v2-residual 候选

图片接口另外提供 `watermark-v2-residual` 候选。它沿用 v1 的 payload、
Reed–Solomon、512×512 工作域、DCT 位置、重复次数和强度参数，但先在工作域
计算未截断的有符号残差，再上采样回原图分辨率，最后只在原分辨率做裁剪和量化。
因此 PNG 的无水印控制可以保持原始像素，避免 v1 的固定工作尺寸往返成为主要
质量损失来源。v1 仍是默认兼容路径，PDF 发布仍固定为 v1；v2 当前只接受
无 alpha 的 L/RGB 图像，并且必须以相同的 paired 评测记录其鲁棒性，不能把
单纯的 A/C endpoint 改善当成全面替代结论。

v2 的工作域诊断会记录未截断目标、修改范围、编码前后 bit/byte error、ECC
恢复结果和失败位置，见 `diagnose_image_residual` 及 Round 4 结果目录。

## watermark-v2-feedback 候选

`watermark-v2-feedback` 在 v2 residual 的基础上增加有限反馈：每轮从新编码后的最终字节盲读
DCT 系数，只对低于 margin 的位置追加固定步长修正；每轮都从原始图像和累计浮点 residual
重新编码，避免 JPEG 累积损失。默认上限为 3 轮，并有均值、峰值和裁剪比例预算。它只用于
实验评测，PDF 不启用；发布服务会在登记前调用最终字节自检，只有盲检测唯一恢复本次
ContentID 才允许写入摘要、签名和 Registry。

## PDF

PDF 使用 pypdfium2 以 120 DPI 渲染每一页，再将每页水印 PNG 通过 ReportLab 重建为新 PDF。这样会丢弃原文字层、书签及交互对象；这是明确的媒体适配限制。最多 10 页、输入 20 MB。检测逐页执行并保留页面详情；页面恢复出多个不同 ContentID 时返回 `CONFLICT`。

PDF 检测结果的范围是当前发布文件中观察到的渲染页面。ContentID 只能作为当前页的来源提示；它不证明原始页面集合、页号、顺序或整份文件完整性。调用方应区分当前页数、页面来源提示、整文件摘要检查和原始页面身份检查；后两项没有 Manifest/状态证据时必须保持 `NOT_CHECKED`。

本地 pilot 的质量脚本使用图片 A/B/C 三组：A 为原图，B 为与发布输出相同编码但不嵌水印的基线，C 为完整水印结果；报告 A/B、A/C、B/C 的 PSNR 和 11×11 RGB 局部窗口 SSIM，并记录水印 512×512 工作尺寸及恢复到源尺寸的上采样。PDF 则在同一 DPI 下做原始页渲染与发布页渲染的 A/C 对照，B 组明确为不适用。

`inspect_media` 返回媒体类型、尺寸或页数。所有解析失败、超限和不支持类型都返回明确异常，调用层应将其作为输入错误处理。
