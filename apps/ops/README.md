# 统一操作台（127.0.0.1:8770）

失效源：apps/ops/** src/bookcorpus/scripts/ops_status.py

书库日常操作的本地控制台。页面读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调 `src/bookcorpus/scripts/` 里的脚本；人账编辑与上传在服务进程内原子写盘。正文落地、人账保存与退库在服务端自动接力增量同步（元数据 → FTS5 → 异步向量队列），日常操作后不需要手工全量重建。

- 现行设计方案：[操作台重构-事件驱动全自动同步方案](../../docs/design/操作台重构-事件驱动全自动同步方案-2026-11-19.md)
- 用户可见行为与验证命令：[.agents/skills/verify-book-corpus/features/manage-library.md](../../.agents/skills/verify-book-corpus/features/manage-library.md)
- 早期并行施工契约（历史）：[OPS-CONTRACT-2026-10-24.md](../../docs/briefs/OPS-CONTRACT-2026-10-24.md)

## 起与停

```bash
cd <工作副本根目录>
uv run --frozen python apps/ops/server.py                 # 127.0.0.1:8770
uv run --frozen python apps/ops/server.py --port 0        # 随机端口（测试用，stdout 打印实际端口）
uv run --frozen python apps/ops/server.py --root /tmp/x   # 换数据根，代码仍指本文件所在处
```

Ctrl-C 停服务。服务退出不杀其它页面服务，也不杀接手前已经在跑的子进程。

## 文件

| 路径 | 内容 |
|---|---|
| `apps/ops/server.py` | 服务端逻辑：`ops.db`、任务队列与锁、门槛判定、自动增量同步接力、HTTP 接口 |
| `apps/ops/web/index.html`、`app.js`、`style.css` | 六个主模块加六个兼容子页签的原生 JS 页面，无框架无 CDN |
| `工作区/运维/ops.db` | 任务与步骤（首次启动自建，可重建） |
| `工作区/运维/jobs/<id>/` | `log.txt`、`commands.json`、`pid`、`plan.json`、`result.json`、`dry-run.json`、`report.json`、`thumbs/*.png` |
| `工作区/运维/.writer.lock` | 写任务互斥与批次账读改写用的 `fcntl.flock` 文件 |
| `工作区/运维/.reader.lock` | 只读任务共用锁（最多两个并行，与写任务互不阻塞） |
| `工作区/运维/last-audit.json` | 审计缓存，由 `ops_status.py --deep` 写，页面只读 |
| `tests/acceptance/ops/check_ops.py` | 服务与任务离线自检，末行 `ALL PASS` |
| `tests/manual/ops/shot_ops.py` | headless Chromium 交互与截图自检，末行 `SHOT PASS` |

## 三条只读数据源

1. `src/bookcorpus/scripts/ops_status.py --json --root <root>`：顶栏、总览与 `sync_delta` 读数；脚本不存在时回落 `OPS_STATUS_FIXTURE`。
2. `ops.db`：任务、步骤、日志路径与状态计数。
3. `build/library.db`（只读打开）与各批次的 `worklist.tsv`、`logs/<uid>.json`、`triage_final.csv`、`skip_list.tsv`、`defer/lang.tsv`。

## 页面（六大模块）

页面文案面向用操作台管书的人，除日志诊断页外不暴露脚本名、命令行参数、文件路径与内部代号。

- **1. 总览（`#overview`）**：一句话状态、流水线漏斗、下一步待办、多端服务状态栏与最近任务。
- **2. 新书入库（`#import`）**：上传到 `books/_收件箱/`、扫描出计划、逐行改书名/分类/动作、确认入库（自动建库、出总表，文档格式自动接力 `docs.run`）。
- **3. 书目与分类（`#ledger`）**：含子页签 `ledger`（人账编辑与批量确认，保存即自动同步）、`retire`（待退库与删书 dry-run/apply 三道闸）、`cold`（冷宫只读专区）。
- **4. 正文生产（`#split`）**：含子页签 `split`（电子书拆章/重拆/重检/查看问题/受理）、`docs`（文档转 md 进度与补转）、`pdf`（批量分诊、额度、转写与受理；子页 `#pdf-list` 为多维筛选清单）、`manual`（DJVU/CSV 手工放好后一键扫描落位）。
- **5. 同步与维护（`#maintain`）**：`sync_delta` 偏差面板、「复核并增量对齐」、按需 LLM 扩展、低频高级运维（首次向量构建、零推理压缩、全量重建、深度体检与清理）。
- **6. 任务与日志（`#logs`）**：任务队列、待确认批次、步骤进度、脱敏日志、取消、重跑与重启接管。

兼容子页签 `#retire` / `#cold` / `#docs` / `#pdf` / `#pdf-list` / `#manual` 均可直接 hash 打开，不再占左侧主导航。

## HTTP

POST 收 JSON（上传接口除外），返回 `{ok, ...}` 或 `{ok:false, error}`。GET 与 POST 均校验本机 `Host` / `Origin`，否则 403。本节路由清单由 `tools/check_docs_sync.py` 对 `apps/ops/server.py` 自动对账。

```text
GET  /api/status                       # 状态脚本 + sync_delta + ops.db 计数 + 锁状态；?refresh=1 / ?deep=1
GET  /api/jobs?state=&limit=           # 任务表（带 steps）
GET  /api/jobs/<id>                    # 任务详情：steps、commands、log_tail、产物路径
GET  /api/jobs/<id>/log?offset=        # {offset,next,size,eof,text}，text 已脱敏
POST /api/jobs/<id>/cancel             # SIGTERM 进程组，10 秒后 SIGKILL
POST /api/jobs/<id>/rerun              # 用 jobs/<id>/commands.json 再排一个
POST /api/jobs/clean                   # 清理旧日志与缩略图
GET  /api/inbox                        # 收件箱清单
GET  /api/import/latest                # 最近一份可用计划（源文件仍在收件箱）
POST /api/import/upload                # 裸 body，X-Filename（URL 编码），上限 2 GB
POST /api/import/scan                  # ingest.py scan
POST /api/import/plan                  # {job_id, items}：把编辑后的计划写回 plan.json
POST /api/import/apply                 # {plan_path}
GET  /api/pending                      # v_pending_md 列表（只读 library.db）
GET  /api/split/books?batch=           # 批次的 logs/<uid>.json 汇总
POST /api/split/run                    # {uids, workers, replace} 或 {only, batch}
POST /api/split/recheck                # {uid, batch}：复核暂存区产物
POST /api/split/inspect                # {uid, batch}：只读，出问题清单
POST /api/split/accept                 # 受理拆章暂存产物并触发增量同步
GET  /api/docs/report                  # 文档转 md 的最近报告与待补转清单
POST /api/docs/run                     # 触发文档格式（txt/docx/xlsx/xls/pptx/md）转 md
GET  /api/pdf/queue                    # PDF 账与旧批次进度分解
GET  /api/pdf/books                    # 待转 PDF 清单（四类处置、进度、页数、建议与今日额度）
GET  /api/pdf/triage-info?uid=         # 单本分诊结果与缩略图路径
POST /api/pdf/triage                   # {uid}：单本分诊（验收与命令行兼容入口）
POST /api/pdf/triage-batch             # {uids?}：批量分诊未分诊 PDF
POST /api/pdf/settle                   # {items:[{uid, route}]}：改处置或按建议确认
POST /api/pdf/confirm                  # {uid, route, reason, model, job_id}
POST /api/pdf/run                      # {uids, limit, workers, force}：按 route 转写
POST /api/pdf/accept                   # 受理 PDF 暂存产物并触发增量同步
POST /api/manual/rescan                # 扫描手工放入 books/<uid>/md/ 的正文并同步
GET  /api/ledger                       # 全库人账与待复核分类表
POST /api/ledger/row                   # 修改单行分类/书名/保留策略并自动同步
POST /api/ledger/rows/confirm          # 批量确认「待复核」分类行并自动同步
POST /api/ledger/rows/delete           # 批量删除指定人账行（兼容入口）
GET  /api/retire/drop                  # retention=drop 的待退库清单
GET  /api/retire/search?q=             # 按 uid 或书名查删书预览对象
POST /api/delete/dry-run               # {uid, reason}：七层退库预览与 plan_hash
POST /api/delete/apply                 # {uid, reason, plan_hash, confirm_uid}：执行退库并清理检索层
GET  /api/cold                         # 冷宫只读清单（books/_冷宫/ 与 retired.tsv）
POST /api/sync/reconcile               # 复核并增量对齐（元数据 / FTS5 / 向量 / 体检）
POST /api/sync/expand                  # 按需 LLM 章级主题扩展
POST /api/maintain                     # {steps:[db,index,awards,audit,verify,catalog,doc]}
POST /api/maintain/advanced            # 低频高级运维（向量首次构建、压缩、全量重建等）
GET  /api/settings                     # 读取自动同步开关等操作台设置
POST /api/settings                     # 保存操作台设置
GET  /api/services                     # 首页/检索/阅读/日课多端服务健康状态
POST /api/services/start               # 按 key 代起对应本地服务
GET  /api/search/health                # 代理查 8765 /api/health
POST /api/search/start                 # 代起检索演示页（8765）
GET  /api/file?path=...                # 白名单文件下载（文本产物脱敏，png/xlsx 原样）
```

## 任务、锁与取消

- 状态流转：`queued` → `running` → `succeeded` / `failed` / `cancelled`；扫描与分诊等需人工确认的任务停在 `awaiting_review`；重启后 PID 核对不上的 `running` 标 `orphaned`。
- 写互斥：写任务与 HTTP 账表写操作共用 `工作区/运维/.writer.lock`（`LOCK_EX`），有写任务在跑时接口返 409。
- 读共享：只读任务共用 `工作区/运维/.reader.lock`（`LOCK_SH`），最多两个并行，不阻塞写任务。
- 取消：对子进程组发 `SIGTERM`，10 秒后仍有活口则 `SIGKILL`；已结束与 `awaiting_review` 任务不可取消（409）。

## 验收

```bash
uv run --frozen python tests/acceptance/ops/check_ops.py
PYTHONPATH=$PWD/tests/acceptance/ops uv run --frozen python tests/manual/ops/shot_ops.py
```

- `check_ops.py` 造临时根与 stub 脚本，不碰真库，末行 `ALL PASS`。
- `shot_ops.py` 用 headless Chromium 走完六模块、兼容子页签、导入/分诊/删书弹窗与 1440x900 / 390x844 两档布局，末行 `CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`（浏览器读 `CHROME_BIN`，缺省自动探测 `chromium`）。
- 轻量导航回归亦可用 `.agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py`。

## 已知限制

- 无登录、只绑 `127.0.0.1`；远程访问前必须自行加鉴权与 TLS。
- 上传上限 2 GB；更大文件请直接放进 `books/_收件箱/`。
- 全量索引重建无百分比进度，只提供日志尾部与耗时。
- 只读打开 `build/library.db`（WAL 模式）时会由 SQLite 自动生成 `library.db-shm`，不写数据。
