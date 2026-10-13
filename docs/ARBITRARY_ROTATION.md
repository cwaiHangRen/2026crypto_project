# 任意角度旋转来源恢复

本轮在 `feature/arbitrary-rotation-recovery` 开发，起点为 ma-zhiheng 仓库的 `feature/rotation-mirror-recovery`，提交 `520aa40d9e49a63def42f45035210431699ba639`。目标是让已经发布的 v1 图片在任意小数角度旋转后仍有机会恢复来源编号，并提供随机旋转演示。本文记录本地验证，远端提交与 CI 状态需独立核对。

首次完整功能保存为 `29f8a42`；提交 PR 前再整合主仓库 `bf2d03e` 的 v2 residual、发布自检、密钥信任、管理员认证和受限同步。下方 169 项回归、624/624 矩阵及其耗时属于首次功能版本，不能直接当作整合后默认 scalar 模式的耗时。整合版的检查单独记录在本文末尾。

## 实现与语义

`detect_image(data)` 只读取当前图片字节，不接收原图、原尺寸、角度、镜像标记、ContentID、sidecar 或登记库。先完成原有八方向检测，再根据输入的纯色或透明边框产生最多 8 个几何候选，每个候选都检测八个方向。基础及几何候选共同去重；不同有效编号返回 `CONFLICT`，不会在第一个成功候选处提前结束。

斜转候选在原图像素坐标中校正，再压到 512×512。矩形图片先压成方形再旋转会破坏正确的块位置，因此没有采用这个顺序。检测保留 v1 的嵌入强度、载荷、RS(53,21)、CRC、重复次数与发布格式，旧发布图片可以直接使用增强后的检测器。整合版保留上游 DCT scalar/batch 上下文控制及默认 scalar，不改变 v2 residual/feedback 的发布和诊断算法。

完整画布用前景凸包的最小面积矩形估计方向、尺寸和中心；公开阈值为 4/12/24/48，估计尺寸尝试 0/-1 像素修正，候选去重。透明边框使用 alpha，纯色边框使用四个角点的背景色估计。残余角度小于 0.02° 时不进行斜转搜索；几何输入最多 16MP、最长边 8192 像素，候选输出同样受像素限制。SciPy 几何后端不可用时，继续原有正交方向检测，诊断字段会明确标记后端不可用。

对于保持原画布大小而截断图片的 `expand=False`，尝试从残留斜边估计方向，并把输入画布尺寸及宽高交换作为原尺寸假设。可见度掩码随校正一起变换；只让完整可见的重复块投票。某位四个重复块均不可见时，对应 RS 字节标为擦除；超过 32 字节直接拒绝。最终恢复仍需正确版本、RS 解码与 CRC，几何信号自身不能构成有效来源编号。

`details.rotation_recovery` 记录后端、候选上限和实际几何假设，`details.matches` 中的几何字段记录有效候选。校正角度只描述检测器的估计，不认证图片的真实传播历史。恢复出的水印只提供来源线索；旋转改变字节后，真实签名可以通过，但当前文件摘要仍应不匹配，结论为 `ORIGIN_HINT_ONLY`。

`direct_detection()` 用独立上下文保留历史单方向 D0。发布前 `assert_publishable` 与受限同步的初始 D0 在该上下文中运行；任意角度传播恢复不能放宽发布门槛，也不会增加同步 profile 的无预算搜索。异常和嵌套退出会恢复上下文。普通图片验证使用完整旋转搜索；同步 profile 仍显式选择。

## 演示与接口

启动已有本地服务后，在“传播变换对比”选择“任意角度旋转”，输入小数角度或点击“随机角度”，可选先水平镜像，再点击“执行变换并验证”。上传已登记的发布图片才能演示凭证检索与签验。界面显示前后缩略图和后端真实结果；随机角度在 0～360° 内。

整合版保留上游管理员认证：按 README 在本地初始化管理员令牌后，在网页密码输入框填写，登记、变换预览和撤销写请求才发送 Bearer 授权。令牌只存在当前页面内存，不写入日志或结果；文件验证是公开读取接口，默认 `full`，缺少可信状态快照时状态保持 `UNKNOWN`。完全相同的文件在仅 hard/recover 范围内按上游返回 `VERIFIED_BINDING_ONLY`，不能仅凭摘要和签名声称状态完备。

```powershell
./.venv/Scripts/python.exe -m uvicorn gm_provenance.api:app --host 127.0.0.1 --port 8000
```

`POST /api/transform-preview?operation=rotate&angle=37.5&mirror=true` 的角度只用于生成变换文件，后续检测只读取文件字节。角度允许有限小数、负值及超过 360° 的值并归一化；布尔角度、非有限值与非法镜像值拒绝。旋转采用 BICUBIC、展开完整画布，RGB 白色填充，RGBA 保留透明填充。输入与输出均限制 20MB/16MP，禁止覆盖来源文件或它的硬链接。既有 JPEG/缩放路径有字节一致性回归。

命令行也支持 `transform input.png output.png --operation rotate --angle 137.125 --mirror`，随后对输出执行 `verify output.png --mode recover`；镜像后旋转也可使用 `mirror_rotate`。不将角度传给验证器。

## 验证与复现

`scripts/validate_arbitrary_rotation.py` 将载体和角度用固定 seed 冻结，ContentID 使用安全随机源并保存在逐条记录中。原图恢复失败的载体不会从统计中删除。旧检测器与新检测器接收完全相同的变换字节；真值只供评测器核对。额外有损或裁切正例独立统计，全部负例必须拒绝，检测异常会使脚本验收失败。

产物目录为 `results/arbitrary-rotation-20261013/run-002/`，由 Git 忽略；`full-validation/` 保存所有原图、嵌入图、变换输入、来源清单、逐条观测、源码 SHA256 与汇总。此前取消的探索数据仍在 `run-001/`；几何专项实验在 `results/arbitrary-rotation-design/`。不覆盖或删除失败证据。

以下命令使用新的输出目录；基线源码必须取自 `520aa40`，真实照片复用已有本地文件，脚本不下载新图片。

```powershell
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Path results/arbitrary-rotation-repeat-01 -ErrorAction Stop
git show 520aa40:src/gm_provenance/watermark.py | Set-Content -Encoding utf8 results/arbitrary-rotation-repeat-01/baseline-watermark.py
./.venv/Scripts/python.exe scripts/validate_arbitrary_rotation.py --output results/arbitrary-rotation-repeat-01/matrix --sample-count 10 --real-image-dir results/arbitrary-rotation-20261013/run-001/real-images --baseline-source results/arbitrary-rotation-repeat-01/baseline-watermark.py --angles 0.1 44.65 89.9 179.9 269.9 --random-angle-count 3 --extra-angles 13.7 44.65 --extra-sample-count 2
./.venv/Scripts/python.exe -m pytest -q --basetemp results/arbitrary-rotation-repeat-01/test-tmp
```

真实照片为已存在的 COCO 2017 样本 139/285/632，来源、输入哈希与原有许可状态清单随结果原样保留。这三张照片仅用于本地机制检查，不是独立的大规模评测集，不进入提交包。额外 JPEG、`expand=False` 和四周裁去各 10% 的套件按输入顺序选前两个合成及前两个真实载体，不按结果挑选。

## 已执行检查

正式矩阵退出码 **0**（`run-002/exit.txt`），13/13 原图检出，全部 current/baseline 检测异常为 0，三个检测源码的首尾 SHA256 一致。载体为 10 个固定合成图加 3 个既有真实照片；主组角度为 0.1/44.65/89.9/179.9/269.9° 和固定 seed 生成的 129.3068/84.665069/245.513376°，每张图均覆盖镜像开关和黑/白/透明边框。共 1632 个检测输入，所有原始字节及观测保留。

| 项目 | 新检测器 | 原八方向检测器 |
|---|---:|---:|
| 主组：完整画布 PNG 正例 | 624/624 | 264/624 |
| 其中合成载体 | 480/480 | 240/480 |
| 其中真实照片 | 144/144 | 24/144 |
| 黑/白/透明边框各自 | 各 208/208 | 见逐条记录 |
| 全部无水印负例 | 816/816，0 误报 | 816/816，0 误报 |
| 额外 JPEG Q95 正例 | 48/48 | 0/48 |
| 额外 JPEG Q85 正例 | 48/48 | 0/48 |
| 保持原画布的 expand=False 正例 | 42/48 | 0/48 |
| 四周各裁去 10% 的正例 | 24/48 | 0/48 |

额外裁切合计 30 个真实失败未删除：`expand=False` 的合成组 24/24、真实照片组 18/24；额外中心裁切两组各 12/24。上述表格描述有限冻结输入，不代表对所有随机角度或自然图像的统计保证。

主组正例平均 **292.798 ms**、P95 **597.653 ms**、最长 **888.065 ms**，旧实现平均 60.086 ms；负例平均 305.371 ms、P95 613.932 ms。计时覆盖图片解码与全部候选水印检测，不含服务签验/登记查询，未做系统负载隔离；每检测器排除一次预热，旧实现先测、新实现后测，P95 用线性分位数。任意角度搜索明显增加耗时，**本轮未达到 P95≤100ms 目标**。下一步优化需保留完整候选冲突检查，不能靠命中即停或过滤失败样本降低耗时。矩阵墙钟耗时 767.086 秒，工件约 514MB。

本机为 Windows、Python 3.13.11；NumPy 2.5.4、Pillow 12.3.0、SciPy 1.18.1、reedsolo 1.7.0。完整原始统计及分组耗时见 `full-validation/summary.json`，可读汇总见 `full-validation/REPORT.md`；`full-validation/integrity.json` 核对全部 1632 个输入和 26 个原始/嵌入源文件的哈希、真值与编号唯一性，均通过。

- 完整回归 **169 passed**，退出码 0，72.40 秒；`full-tests.log/xml` 与 `full-exit.txt` 保存真实输出。存在一条原有 Starlette/httpx 弃用警告，无跳过。
- 实际双编号图片在 33.3° 旋转并镜像后仍返回 `CONFLICT`；真实 RS 擦除测试覆盖只有一个重复块可见、32 字节上限、33 字节拒绝及 CRC-only 回退拒绝。
- 新演示与四组评测测试 **29 passed**，退出码 0；`demo-tests/full-second.*`。真实服务及上传 API 的任意角度恢复、SM2 签验、摘要不匹配、无水印负例、发布文件保护均验证。
- 首轮演示测试 **27 passed、1 failed**，退出码 1，保留在 `demo-tests/full-first.*`。近白色底图与白色边框太接近，使原 24/48 阈值估错原图尺寸；补 4/12 阈值后，四个原失败输入不经重生成均恢复正确编号，且新增回归通过。
- 本地浏览器检查实际选择任意旋转、随机生成小数角度、切换镜像控件；布局正常，未观察到控制台错误。来源恢复闭环由真实 API 测试验证。
- 命令行真实旋转 137.125° 并镜像后，恢复编号与保存真值一致，退出码 0；`nan` 角度拒绝、退出码 1且未生成输出。证据为 `cli-transform.log`、`cli-recover.json`、`cli-invalid.log`、对应退出码及 `cli-checks.json`。该 CLI 检查使用独立空登记库，只证明水印恢复；签验与登记检索由上述服务/API 测试验证。

## 适用边界

完整图片加纯色或透明填充边框是主要范围。没有可辨识边界、原图边缘颜色与填充完全一致、强 JPEG 重压缩、过小尺寸、长边超限或旋转后的大量裁切可能失败。几何假设与纠错只能利用仍保留的信息；宽高比很大的图片大角度裁切会删除超过 RS 容量的载荷。

任意角度不是对所有图片、所有插值和所有后续编辑的成功保证。已确认 896×640 在 89.9° 保持原画布裁切时可失败；宽图大角裁切也存在失败。失败返回 `NOT_FOUND`，不等于内容恶意。完整恢复的性能应按本轮矩阵实测，不把八方向耗时当作任意角度耗时，也不宣称比赛 95% 或 P95≤100ms 已达标。Linux/Python 3.12 CI 未在本次本地任务执行。

## 提交前主分支兼容验证

在 `29f8a42` 的功能实现上整合主仓库 `bf2d03e`，不回退上游新增的 v2 residual/feedback、发布自检、密钥显式信任、管理员 Bearer 授权、`VERIFIED_BINDING_ONLY`/状态 `UNKNOWN` 语义及受限 sync profile。

- 环境检查退出码 0；整合后的完整回归 **286 passed、2 warnings**，退出码 0，396.54 秒。日志/XML/退出码位于 `pr-integration/full-first.*`；保留既有 Starlette/httpx 与 Pillow I-mode 弃用警告，无跳过。
- 补充 v2 反馈直接观测隔离后，反馈与 profile 定向回归 **9 passed**，退出码 0，33.80 秒；`pr-integration/feedback-final.*`。新增真实旋转正例能被普通验证恢复，但不能被发布前直接 D0 自检或 v2 反馈直接观测接受，且上下文退出后普通验证恢复正常。
- v2 任意角度、scalar/batch DCT、裁切擦除及直接检测上下文定向 **10 passed**，退出码 0；20 个上游嵌入/残差/诊断/DCT 辅助函数的源文本保留审计在 `pr-integration/watermark-tests/final-source-audit.json`。
- 授权/错误令牌拒绝、显式可信密钥、完整状态语义、上传限读及图片发布文件/硬链接保护均由整合测试覆盖。实验图片、原始日志、私钥、令牌和数据库只保存在本地，不进入 Git 提交。
- 整合版复验 24 个既有冻结输入（3 个载体、0.1/129.3068°、白/透明边框、12 正例/12 负例），scalar/batch 共 48 次观测全部符合原真值，模式间状态与编号一致；输入 SHA256 校验通过，检测源码首尾一致，退出码 0。记录为 `pr-integration/frozen-input-validation.json` 与逐输入 JSONL。
- 上述小组平均/P95：默认 scalar **722.968/776.916 ms**，batch **234.808/305.360 ms**，各排除一次预热、未隔离系统负载。这是整合版的有限混合输入描述性计时，与首次版本的 624 个正例统计不同，均未达到 100ms 目标。

旧 PR #1 的旋转镜像功能包含在本次分支中；新 PR 以当前主仓库 `main` 为目标，不依赖旧 PR 先合并。旧 PR 的状态不由本轮自动变更。
