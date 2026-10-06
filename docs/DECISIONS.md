# 技术决策

* 使用 OpenSSL 3 CLI 适配 SM2/SM3：系统已具备国密实现，避免在 Python 3.14 上假定 gmssl API；代价是进程启动开销被计入 doctor/验证计时。
* 采用 sidecar Manifest + 本地 SQLite：先明确文件完整字节，避免将嵌入 ContentID 与摘要形成循环依赖。
* JCS 规范化优先调用 RFC 8785 实现；离线环境缺包时使用项目内受限 fallback，并在 doctor 记录后端能力。
* 第一版水印使用 DCT 基线和纠错，报告实际边界，不把 CRC 当作密码认证。
* PDF 先栅格化发布，保证页面像素有水印机会；文字层保留列为未支持能力。
