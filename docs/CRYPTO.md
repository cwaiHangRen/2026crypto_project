# 密码实现

密码后端由 `OpenSSLCrypto` 调用本机 OpenSSL 3.x，所有外部进程均使用参数数组，不经过 shell。`doctor()` 会执行 SM3 `abc` 已知答案和 SM2 真实往返签验。

- SM3：`sm3(data)` 返回小写 64 位十六进制。
- 密钥：`generate_key` 生成 SM2 PKCS#8 AES-256-CBC 加密私钥和 SPKI PEM 公钥；口令通过临时子进程环境变量传递，不写日志或命令行。`key_id` 是 SPKI DER 的 SM3。
- 签名：`pkeyutl -rawin -digest sm3`，`distid:1234567812345678` 显式参与 ZA，结果是 DER。
- 验签：拒绝非 SM2 命名曲线 SPKI；签名不匹配返回 `False`，OpenSSL 环境和输入密钥错误抛 `CryptoEnvironmentError`/`CryptoError`。

进程启动和文件 I/O 包含在操作耗时内。私钥路径应由调用方控制；适配器不自动设置跨平台 ACL。
