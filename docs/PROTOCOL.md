# Manifest v1 协议

待签名字节为：`GM-PROVENANCE-MANIFEST-v1\x00 || RFC8785-JCS(body)`。正文使用固定 schema `1.0`、`SM3`、`SM2-SM3-DER` 与用户标识 `1234567812345678`。`metadata_hash` 是 `SM3(JCS(metadata))`，`manifest_hash` 是上述带域分离前缀的摘要；签名 envelope 使用 `base64-DER`。

正文只接受声明字段，不接受未知顶层字段；资源限制为 UTF-8 总长 1 MiB、深度 16、节点 10000、字符串 65536 字节、数组 1024、对象 256。JSON 重复键、非法 UTF-8、NaN/Infinity 及超安全整数会被拒绝。时间必须是 UTC 秒精度 `YYYY-MM-DDTHH:MM:SSZ`。根版本的两个父引用为 `null` 且 `version=1`，派生版本必须给出不同的父 ContentID 与父 Manifest 摘要。

`dumps` 返回规范化 envelope；`loads` 严格解析。由于本离线环境可能没有第三方 `rfc8785` 包，代码提供仅依赖标准库的 I-JSON/JCS 兼容实现，协议值使用整数、字符串和结构对象时与 RFC 8785 输出一致；部署时可安装维护的 `rfc8785` 包替换 fallback。
