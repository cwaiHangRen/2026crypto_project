# 状态快照与本地登记

`Registry` 使用本地 SQLite 保存发布者公钥、完整 Manifest 与媒体 BLOB，并在同一事务内写入。数据库和导出的 `media/`、`manifests/` 文件都属于本机状态，SQLite 本身不提供防篡改保证。

每次发布、撤销、信任关系或密钥变更都会递增状态序号。`accept_sequence` 将客户端已接受的最高序号持久化，拒绝低于高水位的序号；首次启动且高水位为零时，旧快照仍可能被重放，必须依赖可信初始状态或在线登记服务。

`state.sign_snapshot` 使用独立登记服务私钥，对固定域 `GM-PROVENANCE-STATE-v1\\x00` 加 JCS 规范化正文签名。正文完整包含 `schema_version`、`sequence`、`issued_at`、`expires_at`、`latest`、撤销列表及撤销记录。`verify_snapshot` 返回 `PASS`、`FAIL` 或 `UNKNOWN`：签名错误、结构错误、未来时间和已知回退为 `FAIL`；签名正确但过期为 `UNKNOWN`。普通系统时钟不是可信时间戳，状态不可用时不得报告“未撤销”。

## API 摘要

- `add_key(public_pem, publisher_id, trusted, crypto)` → 64 位十六进制 `key_id`
- `publish(envelope, media_bytes, manifest_hash)` → `content_id` 与物化路径
- `history(content_id)` 沿单父链最多 64 跳并检测循环
- `revoke(target_type, target_id, reason, issuer)` 记录可审计撤销事件
- `status()` 生成待签名状态正文；`read_media`、`get_manifest`、`find_by_hash` 提供查询

撤销授权应由服务层 CLI 或已认证 API 控制；本模块只记录调用者已授权的事件。

当前 CLI 通过 `state-snapshot` 签发本地快照，`verify --state-snapshot PATH --state-public PATH` 使用快照检查撤销状态和 asset 最新版本。快照缺失或过期时返回 `UNKNOWN`；本地 SQLite 不自动提供可信新鲜度。

验证模式的边界：`hard` 只在已定位 sidecar Manifest 时检查最终字节摘要、签名、信任和父链，水印与状态维度保持 `NOT_CHECKED`；`recover` 优先从水印恢复 ContentID 并查询本地登记，不宣称新鲜撤销状态；`full` 执行水印、凭证和状态快照检查，缺少有效新鲜快照时总体结论保持 `INDETERMINATE`，不会把未知状态当作未撤销。
