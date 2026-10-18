# v2.1 可选编解码能力复核

`scripts/probe_media_codecs.py` 会在当前机器上探测 `ffmpeg` 和 `ffprobe`，并写出隐私安全的 `logs/v2.1/codec-capability.json`。报告只记录工具是否在 `PATH`、版本首行和能力分类，不保存绝对路径、媒体字节、私钥或令牌。

本机复核结果以生成的 JSON 为准。没有 `ffmpeg`/`ffprobe` 时，MP3、FLAC、MP4 状态为 `BLOCKED_DEPENDENCY`，相关重采样、转码、音量变化、片段截取、元数据清除和视频抽帧均为 `NOT_RUN`。这不是代码通过，也不是失败样本被跳过；它是明确的环境阻塞。若工具存在但鲁棒水印适配器尚未实现，状态为 `CODE_NOT_IMPLEMENTED`。

图片、PDF 和 PCM16 WAV 仍使用离线适配器。`tests/v2_1/test_codec_capability.py` 使用确定性 MP3/FLAC/MP4 头部 fixture，确认适配器安全拒绝伪造输入；fixture 不生成假音频或假视频，也不把拒绝标成验证通过。提交电脑如需完成真实编解码测试，应安装固定版本的 FFmpeg，并保存 `ffmpeg -version`、`ffprobe -version`、输入文件摘要、命令、返回码和完整日志。

