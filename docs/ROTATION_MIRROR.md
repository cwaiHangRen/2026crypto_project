# 图片旋转镜像来源恢复

本功能在 `feature/rotation-mirror-recovery` 本地分支开发，起点为 `main` 的 `93d5489`。未推送、未合并。恢复的是来源编号与登记凭证；文件字节发生变化后，来源恢复不代表当前文件完整性通过。

## 实现范围

- 支持 0/90/180/270 度旋转，以及左右镜像后这四种旋转，共八种方向；包含上下镜像、对角转置与反对角转置。
- PNG、JPEG 图片输入，包含矩形和灰度载体；基础几何变换输出 PNG，JPEG 重新压缩单独统计。
- 不需要原图、期望编号、真实角度、镜像标签或附带凭证。登记服务仍需保存原凭证，以便通过恢复编号检索。
- 本轮不承诺任意角度旋转、旋转叠加裁剪、强压缩或真实图像数据集识别率；失败样本必须保留。
- 沿用已发布的 `dct-difference-v1` 水印格式、RS/CRC 校验和公开嵌入参数，发布字节、签名协议、状态语义均保持原有规则。

`detect_image` 解码并归一化一次，依次检查灰度矩阵的八个方向。块 DCT 批量处理仅最后两个空间轴，读取顺序和重复多数票不变。所有有效编号去重：零个为 `NOT_FOUND`，一个为 `FOUND`，多个为 `CONFLICT`。不会首个成功即返回，防止遗漏方向间冲突。

`details.orientations_checked` 和 `details.matches` 是附加诊断字段，顶层返回结构兼容原调用方。方向名称表示检测时施加的校正，不是原始传播操作的认证记录。

## 自动化测试

- `tests/unit/test_rotation_mirror.py`：八种像素方向、PNG/JPEG、横竖/正方形/灰度载体、无水印负例、镜像旋转后 JPEG 保存、非法图片、NumPy DCT 路径。
- 冲突测试使用两份实际嵌入不同编号的图片，把相反方向的水印放入互不重叠的上下区域；真实检测必须返回 `CONFLICT`。同编号的同类组合必须去重。
- `tests/integration/test_rotation_mirror_service.py`：真实 SM2/SM3 登记、无 sidecar 恢复、硬绑定不匹配、冲突拒绝、现有上传 API。
- `tests/integration/test_benchmark.py`：七个新增变换的四组实际运行、错误/额外编号的评测拒绝、无 PyYAML 时的配置读取。

初次定向测试因两个测试模块同名在收集阶段失败（退出码 2），已将服务测试更名为 `test_rotation_mirror_service.py`。原失败日志仍保存在本次结果目录；没有删除缓存或隐藏失败。

## 复现

在项目根目录运行。已经配置好 `.venv` 时直接使用该解释器；首次配置按 README 创建环境并安装 `.[test]`。下面的输出目录必须不存在，重跑时换一个新目录。

```powershell
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Path results/rotation-mirror-repeat-01 -ErrorAction Stop
git show 93d5489:src/gm_provenance/watermark.py | Set-Content -Encoding utf8 results/rotation-mirror-repeat-01/baseline-watermark.py
./.venv/Scripts/python.exe scripts/validate_rotation_mirror.py --output results/rotation-mirror-repeat-01/matrix --sample-count 30 --baseline-source results/rotation-mirror-repeat-01/baseline-watermark.py
./.venv/Scripts/python.exe -m pytest -q --basetemp results/rotation-mirror-repeat-01/test-tmp
```

验证脚本每轮产生 30 个固定种子合成载体（来源 PNG/JPEG 各 15 个），每个执行八种方向、无损像素保存/JPEG Q95/JPEG Q85 三种编码，且同时检测带水印与不带水印副本。总计 1440 条图片记录，含 240 条基础正例、720 条负例和 480 条额外 JPEG 正例。载体含平滑纹理、图形文字、横竖图与灰度图，不是外部自然图像数据集。

评测逐条比较唯一编号与真实嵌入编号。基线源码单独加载并读取完全相同的输入字节；真值只进入评测记录，不进入检测函数。合成内容和变换参数可复现，编号由安全随机源重新生成并保存在本轮记录中。原方向失败的载体不会被过滤。脚本通过条件是：原方向全部可检测、240 条基础正例全部正确、720 条负例全部拒绝、16 条真实服务检查全部正确。额外 JPEG 正例独立统计，不隐瞒失败。

耗时为本机单次顺序检测的 `perf_counter` 结果，每检测器排除一次预热，固定先旧后新；P95 使用线性分位数。它只描述本机该合成集，不是比赛性能达标证明。

## 本次验证记录

本次产物位于 `results/rotation-mirror-20261013/run-001/`，由 `.gitignore` 排除。`matrix/REPORT.md`、`matrix/summary.json`、`matrix/samples.jsonl` 和 `matrix/service.json` 分别保存可读汇总、环境/源码指纹/统计、逐图片原始记录和服务完整字段。测试日志、JUnit XML 与退出码也保存在同一目录。

环境为 Windows、Python 3.13.11、OpenSSL 3.0.18；NumPy 2.5.4、Pillow 12.3.0、SciPy 1.18.1、reedsolo 1.7.0。依赖能力检查及 SM3 已知答案/SM2 签验均通过。Linux/Python 3.12 CI 没有在本次本地任务中执行。

| 项目 | 实测结果 | 退出码或说明 |
|---|---|---|
| 改动前完整测试 | 43 passed，57.52 秒 | 0；`baseline-tests.log/xml` |
| 首次定向测试 | 收集阶段因测试模块重名失败 | 2；`focused-tests.log` 保留 |
| 更名后定向测试 | 58 passed，24.31 秒 | 0；`focused-02-tests.log/xml` |
| 首轮完整回归 | 96 passed，41.28 秒 | 0；`full-tests.log/xml` |
| 最终完整回归 | 97 passed，39.15 秒 | 0；`final-tests.log/xml` |
| 原方向可检出载体 | 30/30 | 未过滤载体 |
| 无损八方向正例 | 240/240；旧实现 30/240 | 验证脚本退出 0 |
| 额外 JPEG Q95 正例 | 240/240；旧实现 30/240 | 独立统计 |
| 额外 JPEG Q85 正例 | 240/240；旧实现 30/240 | 独立统计 |
| 无水印负例 | 720 个，0 误报 | 八方向及三种编码 |
| 实际服务恢复字段检查 | 16/16 | 不传 sidecar，恢复正确编号与有效签名 |
| 命令行恢复 | `FOUND`、正确编号、签名 PASS、摘要 FAIL、`ORIGIN_HINT_ONLY` | 0；`cli-recover.json` |
| 四组新增几何评测 | 28/28 | 0；`four-groups-02/` |
| 数据集离线状态检查 | 检查完成；样本/全集归档未下载 | 0；本轮没有下载数据集 |

完整测试均有一条与基线相同的 Starlette/httpx 弃用警告，保留在日志中；没有测试跳过。基础正例的新检测平均 40.108 ms、P95 48.846 ms，旧实现平均 47.730 ms、P95 65.310 ms；负例新检测平均 36.535 ms、P95 74.433 ms。计时包含图片解码与完整八方向水印搜索，不包含服务签验或登记查询，不能当作完整验证耗时。

首次四组记录 `four-groups/` 有六条 `handled=false`：四条裁剪恢复失败，以及两条无元数据 PNG 清除元数据后字节未改变造成的评分误判。没有删除首次记录。评分修正后的 `four-groups-02/` 仍保留四条真实裁剪失败（G2/G3，各两个裁剪参数）；`existing-transform-failures.json` 对照确认旧检测器在同样裁剪输入上也未恢复编号。没有把裁剪写成已支持。

实际执行的关键命令如下；复验时按上面的方式使用新目录，避免覆盖证据：

```powershell
./.venv/Scripts/python.exe scripts/validate_rotation_mirror.py --output results/rotation-mirror-20261013/run-001/matrix --sample-count 30 --baseline-source results/rotation-mirror-20261013/run-001/baseline-watermark.py
./.venv/Scripts/python.exe -m pytest -q --basetemp results/rotation-mirror-20261013/run-001/final-tmp --junitxml=results/rotation-mirror-20261013/run-001/final-tests.xml
./.venv/Scripts/python.exe scripts/run.py --root results/rotation-mirror-20261013/run-001/matrix/service-registry verify results/rotation-mirror-20261013/run-001/matrix/service-detector-005.bin --mode recover
./.venv/Scripts/python.exe scripts/run.py --root results/rotation-mirror-20261013/run-001/cli-benchmark-02-runtime benchmark --output results/rotation-mirror-20261013/run-001/four-groups-02 --config configs/attacks.yaml --samples 1
./.venv/Scripts/python.exe scripts/download_all_datasets.py --status
```

`pytest` 验证包含真正的协议、PDF、安全及状态回归；上传 API 用 TestClient 请求真实服务层，没有声称采集浏览器截图。私钥未写入统计或文档，评测脚本服务密钥使用工程内临时目录，结束后清理其本轮临时密钥。
