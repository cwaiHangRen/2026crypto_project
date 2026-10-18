# 裁剪同步模块与 Round9 规模基线交接

本文件面向队友集成当前封版接口。默认图片嵌入为 `watermark-v1`，普通来源检测为 D0 `dct-difference-v1`；用户主动选择裁剪/偏移恢复时使用 S0 `decoder-v2-sync`；v3 `decoder-v3-sync` 只作为显式实验选项。PDF 仍走 v1/D0，S0/v3 对 PDF 返回 `UNSUPPORTED`。

Round9 已用 `data/round8-scale-1000-final2/` 完成 800 图片 + 200 PDF 的默认 v1 exact 规模基线：1000 次发布尝试、992 次通过 gate；D0 对 992 个最终发布字节 992/992 找回预期 ID，独立 G0 无水印编码控制组 0/992 误报。该清单以内部合成样本为主，AI 样本和外部真实 PDF 仍有缺口，完整限制和结果见 [`ROUND9_SCALE_REPORT.md`](ROUND9_SCALE_REPORT.md)。

## 当前共享版本与集成状态

本次整合工作区基于远端 `origin/main` 的 HEAD `416f18e54c68560bb002042f83af0a45af2ad79f`，以独立 worktree 保留原工作区的全部未提交 Round9 修改；本轮不自动 commit/push。可共享源码只包含 `src/`、`tests/`、必要的 `scripts/`/`configs/`、README/文档、`pyproject.toml`、`requirements.txt`、`.gitignore` 和小型测试夹具。`data/`、`results/`、`.venv/`、SQLite、私钥、token、截图和大型归档不在共享包中。


### 2026-10-15 旋转合并整合验收

- 整合版本为 `origin/main@416f18e54c68560bb002042f83af0a45af2ad79f` 加入本地 Round9 评测/导入修改；原工作区未被覆盖、未 reset、未 stash。
- 定向旋转与裁剪回归：`150 passed`，1 条既有 Starlette/httpx 弃用警告；正交小样本脚本 8/8 正例、24 条无水印负例 0 误报、16/16 服务检查；任意角度小样本（1 个合成 + 3 个已核验本地 COCO 图片，33.3°，镜像组合）24/24 正例、24/24 负例、0 个 detector error。
- 隔离真实素材演示使用既有本地 pilot 的 1 张 JPEG 和 1 份 9 页 PDF：图片/PDF 发布与 full 均 `VERIFIED_EXACT`；90°、180°、270°、左右镜像和 33.3° 变换均由普通 D0 找回来源线索并标为 `ORIGIN_HINT_ONLY`；中心 95% 裁剪使用 S0 找回来源线索，搜索完整且停止原因为 `search_exhausted`，带原 manifest 的 full 仍 `content_hash_match=FAIL`；无水印负例 `NOT_FOUND`；撤销后 full `REJECTED`（content revocation `FAIL`）。PDF 逐页 9/9 `FOUND`，仅表示页面来源线索。
- API 隔离验收脚本：`9/9 PASS`。以上结果均写入本 worktree 的 `results/rotation-integration-20261015/`，其中密钥、SQLite 和状态文件只用于本地验收，不进入源码包。
三个模块的状态分别是：

| 模块 | 当前状态 | 集成边界 |
|---|---|---|
| 裁剪/偏移 S0 | ready | 调用现有 `media.detect`，只返回水印来源线索；不负责信任、签名、Registry 或完整性 |
| 旋转/镜像 | `integrated` | 已随 `origin/main@416f18e` 接入；普通图片 D0 收到变换后的实际字节，保留方向/几何候选、范围、耗时和停止原因，不自动叠加 S0 |
| PDF/实际页面截图 | `integration_pending` | 当前仅有 PDF v1/D0、PDFium 页面渲染和明确标注的模拟截图；没有队友真实阅读器截图代码，不宣称已联调 |

公共调用示例（队友分别点击，不建设总搜索器）：

```python
from gm_provenance import media

# 裁剪模块：输入变换后的实际图片字节；公开预算由 decoder-v2-sync 固定配置管理。
result = media.detect(image_bytes, "image/jpeg", decoder_profile="decoder-v2-sync")
# 保留 result["status"]、result["content_ids"] 和 result["details"] 中的停止/范围/耗时字段。

# 方向模块接入后先做方向候选，再单独调用普通 D0；不自动叠加 S0。
d0 = media.detect(oriented_image_bytes, "image/jpeg", decoder_profile="dct-difference-v1")

# PDF 页面或实际截图按图片范围检测；FOUND 只表示该页面/截图的来源线索。
page_hint = media.detect(screenshot_bytes, "image/png", decoder_profile="dct-difference-v1")
```

服务层仍负责发布者信任、授权、SM2/SM3、父链、撤销和状态；SM3 覆盖用户上传的实际字节。方向校正图、截图选区和裁剪重建画布只用于读水印，页面必须展示实际 detector、检查范围、候选状态、停止原因和耗时。

当前可审阅修改清单：

- 评测隔离：`src/gm_provenance/development_eval.py` 仅在配置请求 G1 时生成 signed-only 对照，并将其写入独立 `g1-registry`；G3 发布 gate 失败不生成 G1/G3 签名产物。
- 回归覆盖：`tests/integration/test_development_eval.py` 验证 G1/G3 归属和 gate 失败无副作用；`tests/unit/test_import_external_materials.py` 验证本地媒体校验、待审查状态和重复哈希。
- 本地素材入口：`scripts/import_external_materials.py` 只做离线格式、大小/页数、SHA-256/SM3、来源/许可和重复核验，不触网、不签名、不入 Registry。
- 既有 Round9 评测脚本、配置、报告和历史未提交修改均保留；没有执行 reset、restore、清理、commit 或 push。

## 环境与启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data init-admin
.\.venv\Scripts\python.exe -m uvicorn gm_provenance.api:app --host 127.0.0.1 --port 8000
```

命令会在本地生成管理员令牌；令牌只保存在本地 `data/admin-token` 或进程环境变量中。发布前仍需显式生成并信任发布密钥：

```powershell
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data keygen --private data\publisher-private.pem --public data\publisher-public.pem
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data trust --public data\publisher-public.pem --publisher publisher-demo-001
```

使用独立的本地测试根目录、密钥、管理员令牌和结果目录；不要复用队友的 SQLite、私钥或 token。数据集、图片/PDF、大型评测结果和密钥不会随源码分发。

## 无网络时的本地素材导入

网络不可用时，把素材和一个 `manifest.json` 或 `manifest.jsonl` 放入独立 incoming 目录（默认 `data/incoming-materials/`），每行至少提供 `filename`、`relative_path`、`local_path` 三者之一。AI 图片建议填写 `generator_tool`、`source_record`/`generation_record`、`source_date`、`source_url` 或可核对的来源记录、`usage_basis`、`license_status`、`source_family_id`、`used_for_tuning`；PDF 建议填写 `source_url`、`license_evidence_url` 或 `usage_basis`、`license_status`、`page_count`（脚本会以实际 PDF 读取结果为准）和 `source_family_id`。运行：

```powershell
.\.venv\Scripts\python.exe scripts\import_external_materials.py `
  --incoming data\incoming-materials `
  --manifest data\incoming-materials\manifest.json `
  --out results\local-import-<run-id>
```

脚本只读取本地字节，检查图片/PDF格式和项目大小/页数限制，输出 SHA-256、SM3、重复引用、媒体检查、来源/许可审查状态以及 `network=not_used`、`registry=false`、`signatures=false` 副作用声明。`license_status=review` 或缺少来源家族不会被提升为已批准；`validated` 也只表示字节可解析，仍需人工核对来源和再决定是否进入评测。默认会扫描 `data` 下历史清单，重复哈希只报告，不覆盖旧记录。

格式示例（仅说明字段，不代表已取得素材）：

```json
{
  "records": [
    {
      "source_id": "<unique-id>",
      "filename": "<file-name>.png",
      "generator_tool": "<tool-or-dataset>",
      "source_record": "<auditable-generation-or-source-record>",
      "source_date": "<YYYY-MM-DD>",
      "source_url": "<source-page-or-local-record-reference>",
      "usage_basis": "<license-or-permission-evidence>",
      "license_status": "review",
      "source_family_id": "<family-id>",
      "used_for_tuning": false
    }
  ]
}
```

导入结果中的 `validated` 只代表媒体字节可解析；`review_status=pending`、缺来源、缺许可或缺 source family 时必须保留，不能自动进入正式评测。

## 调用方式

CLI 验证命令如下，上传文件的摘要仍按实际文件字节计算：

```powershell
# D0：普通精确检测
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data verify input.jpg --mode recover --decoder-profile dct-difference-v1

# S0：用户主动选择的有界裁剪/偏移恢复
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data verify input.jpg --mode recover --decoder-profile decoder-v2-sync

# v3：显式实验恢复
.\.venv\Scripts\python.exe -m gm_provenance.cli --root data verify input.jpg --mode recover --decoder-profile decoder-v3-sync
```

API 使用 `POST /api/verify?mode=recover&decoder_profile=...` 上传 `file` 字段；可选值为 `dct-difference-v1`、`decoder-v2-sync`、`decoder-v3-sync`。响应中的 `decoder_profile`、`watermark_status`、`recovered_content_ids`、`search_complete`、`search_stop_reason`、`checked_scope`、`watermark_details` 和 `timings` 必须原样展示。`FOUND` 也只代表来源线索；裁剪后找回 ID 通常是 `ORIGIN_HINT_ONLY`，不等于当前文件的精确绑定通过。`TIME_BUDGET_EXHAUSTED`、`CONFLICT`、`ERROR` 和 `UNSUPPORTED` 必须分别展示，不能转换成成功。

发布接口和 `media.assert_publishable` 始终使用 D0 自检。S0/v3 不能绕过发布 gate，也不能为了通过 gate 更换 ContentID。几何重建只用于读取水印，SM3 仍绑定上传原始字节。

## 模块边界

同步模块负责图片的有界几何候选、候选预算、停止原因和来源线索；它不负责签名、信任、Registry、父链、撤销、状态快照或原文件完整性。队友的旋转/镜像实验应把变换后的实际字节交给现有 `media.detect(data, media_type, decoder_profile=...)`，并保留一次调用的检测器、预算、耗时和状态。PDF 截图模块应沿用 PDF v1/D0 路径；不要把图片同步结果用于 PDF。

多人协作时，修改 `src/gm_provenance/media.py`、`service.py`、`api.py`、`watermark.py`、`watermark_sync.py`、`ui/index.html` 前先在 PR 或群组中锁定文件归属。评测脚本和报告只读取冻结媒体与结果，不要改写原始 `results/round7-*` 目录。

## 演示案例

以下均为本地评测精选案例，只用于展示字段和状态，不代表总体恢复率：

- 成功：`results/round7-challenge-marked/20261011T-round7-challenge-marked/samples/wikimedia-apollo10-earthrise/v1-final.bin`，可演示 D0/S0/v3 的 exact 或 JPEG 结果。
- 裁剪来源线索：使用同目录的 v1 文件执行 `crop_center_area_0.95`，界面应显示恢复状态、候选数、搜索是否完成和约秒级耗时。
- 失败：`results/crop-handoff-closeout/internal-mixed-20261014-r2/mixed_ids.json` 中的三个内部 signed mixed-ID 拼接样本，D0/S0 为 `NOT_FOUND`，v3 为 `TIME_BUDGET_EXHAUSTED`；配原始 sidecar 时 `content_hash_match=FAIL`，它们属于内部补充诊断，不是原外部缺口的完成证明。

页面可以把来源线索、当前文件完整性、状态新鲜度和耗时分栏展示。不要把候选 ID 出现解释成签名验证通过，也不要把 v3 显示成一定优于 S0。

## Round9 规模运行入口

默认规模配置为 `configs/round9-default-scale.json`，只用于 v1/D0/G0/G3 exact 主层；不把 v2/v3 或历史 26 项传播矩阵混入默认分母。裁剪封版子集为 `configs/round9-crop-subset.json`，固定使用已发布 v1 字节的 80 张图片和 `crop_center_area_0.95`，仅用于观察 S0 的来源恢复线索。运行产物写入 `results/`，不提交源码库。

队友接入时请原样保存以下字段：`decoder_profile`、`watermark_status`、`recovered_content_ids`、`search_complete`、`search_stop_reason`、`checked_scope`、`watermark_details`、`timings`。普通图片请求默认 D0；只有用户明确选择裁剪/偏移恢复才调用 S0；v3 不作为自动 fallback。`FOUND` 只代表水印来源线索，`TIME_BUDGET_EXHAUSTED`、`CONFLICT`、`ERROR`、`UNSUPPORTED` 不得转成成功。

本地演示和评测必须使用独立的测试数据库、密钥、管理员 token 和结果目录；不得把任何真实私钥、token、数据集、截图或大型结果复制到仓库。大型清单和结果的状态以 `docs/ROUND9_SCALE_REPORT.md` 及其引用的 JSON 摘要为准。

## 本轮证据

默认组合补测目录为 `results/crop-handoff-default-s0/20261014T-crop-handoff-s0/`：10 个外部 v1 通过 gate 的媒体、26 个冻结变换、260/260 行完成；v1+D0 为 93/260，v1+S0 为 200/260。完整 Round8 汇总见 [`ROUND8_FINAL_REPORT.md`](ROUND8_FINAL_REPORT.md)，外部失败和原 mixed-ID 缺口仍按原始范围单独报告。

