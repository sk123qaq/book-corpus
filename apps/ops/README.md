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

## 验收用的可注入点

搜索地址与演示页脚本可注入：环境变量 `BOOK_CORPUS_SEARCH_URL`、`BOOK_CORPUS_DEMO_SERVER`，或 `Env(search_url=..., demo_server=...)` 构造参数。默认值就是本机 `http://127.0.0.1:8765` 和仓库里的 `apps/demo/server.py`，不设时真实运行行为不变。离线验收靠它把两者指向假服务：`check_ops.py` 每轮都跑「已在跑 → 幂等 / 不在跑 → 启动 / 演示页脚本缺失 → 报错」三条分支，既不依赖跑测试的机器上有没有常驻 8765，也不改名仓库里的真实文件。

## 文件

| 路径 | 内容 |
|---|---|
| `apps/ops/server.py` | 服务端逻辑：`ops.db`、任务队列与锁、门槛判定、自动增量同步接力、HTTP 接口 |
| `apps/ops/web/index.html`、`app.js`、`style.css` | 六个主模块加六个兼容子页签的原生 JS 页面，无框架无 CDN，支持宣纸日间 / 墨夜主题切换；桌面端左侧导航固定吸顶（`<=820px` 窄屏回落顶部横栏），顶栏移除重复搜索按钮并与正文同宽（`1200px`）水平居中，左右顶部分隔线与满页末卡底端对齐（后端 `/api/search/health` 与 `/api/search/start` 接口保留） |
| `工作区/运维/ops.db` | 任务与步骤（首次启动自建，可重建） |
| `工作区/运维/jobs/<id>/` | `log.txt`、`commands.json`、`pid`、`plan.json`、`result.json`、`dry-run.json`、`report.json`、`thumbs/*.png` |
| `工作区/运维/.writer.lock` | 写任务互斥与批次账读改写用的 `fcntl.flock` 文件 |
| `工作区/运维/.reader.lock` | 只读任务共用锁（最多两个并行，与写任务互不阻塞） |
| `工作区/运维/last-audit.json` | 审计缓存，由写入后后台自动刷新（或 `ops_status.py --deep`），页面只读 |
| `tests/acceptance/ops/check_ops.py` | 服务与任务离线自检，末行 `ALL PASS` |
| `tests/acceptance/ops/test_sidebar_sticky.py` | 桌面端左侧栏吸顶、宽屏居中去搜索按钮、顶底对齐与窄屏回落回归验收，末行 `ALL PASS` |
| `tests/manual/ops/shot_ops.py` | headless Chromium 交互与截图自检，末行 `SHOT PASS` |

## 三条只读数据源

1. `src/bookcorpus/scripts/ops_status.py --json --root <root>`：顶栏、总览与 `sync_delta` 读数；脚本不存在时回落 `OPS_STATUS_FIXTURE`。
2. `ops.db`：任务、步骤、日志路径与状态计数。
3. `build/library.db`（只读打开）与各批次的 `worklist.tsv`、`logs/<uid>.json`、`triage_final.csv`、`skip_list.tsv`、`defer/lang.tsv`。

## 页面（六大模块）

页面文案面向用操作台管书的人，除日志诊断页外不暴露脚本名、命令行参数、文件路径与内部代号。

- **1. 总览（`#overview`）**：当前焦点判定与可读率进度条、流水线六环节、下一步待办列表、多端服务状态栏；任务统一在「任务与日志」查看，本页不重复展示最近任务。
- **2. 新书入库（`#import`）**：自上而下全宽流（全宽投递条 → 收件箱清单 → 入库核对清单，可逐行改书名/分类/动作并确认入库）；文档转换进度与明细移至「正文生产 · 文档转正文」。
- **3. 书目与分类（`#ledger`）**：含子页签 `ledger`（两行清爽工具栏 + 全库书目台账，去除磁盘路径与文件名，保存即自动同步）、`retire`（按编号/书名搜索移出 + 已标「不留」清单 + 原生确认弹窗，不放最近任务）、`cold`（退库台账 + 冷宫实物核验：表格带「实物状态」三态，页面「刷新」会按 `books/_冷宫` 实际文件校正台账）。
- **4. 正文生产（`#split`）**：含子页签 `split`（电子书拆章/重拆/重检/查看问题/受理）、`docs`（文档转正文四格概览、补转、失败重试与最近一批折叠明细）、`pdf`（四格状态筛查、今日额度、批量分诊、内嵌待转清单与改处置弹窗；子页 `#pdf-list` 为完整多维筛选清单）、`manual`（手工格式扫描落位）；各子页均不重复展示最近任务卡片。
- **5. 同步与维护（`#maintain`）**：四格同步健康总览（书目与视图 / 全文检索 / 语义检索 / 体检状态）、自动同步开关与「复核并增量对齐」、体检提醒折叠详情、章节主题增强、导出书目总表与低频高级运维重建；不重复展示最近同步记录。
- **6. 任务与日志（`#logs`）**：全库后台任务的唯一查看与人工收口中心，含状态快速分段筛选、待确认提示条、步骤表、脱敏日志、取消、重跑与重启接管；从各模块点击详情或去确认直达并滚动定位 `#job-detail`。

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
POST /api/jobs/<id>/resolve            # {resolution, note}：待确认任务人工收口
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
POST /api/delete/apply                 # {uid, reason, plan_hash, confirm_uid, confirm}：执行退库并清理检索层
GET  /api/cold                         # 退库台账 × books/_冷宫 实物核验（items 带 state，另有 counts/checked_at）
POST /api/cold/reconcile               # 按 books/_冷宫 实际文件校正 retired.tsv（保留缺失行、补登未登记实物），返回同 GET 视图 + changed/backups
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

- 状态流转：`queued` → `running` → `succeeded` / `failed` / `cancelled`；仅真正需人工裁决的任务（暂存区待复核、`adopt()` 接管后退出码未知）停在 `awaiting_review`，可通过操作台「去处理 / 确认已处理 / 标为失败 / 忽略关闭」（`POST /api/jobs/<id>/resolve`）收口；扫描计划与分诊建议直接落 `succeeded`；重启后 PID 核对不上的 `running` 标 `orphaned`。
- 自动体检刷新：入库、拆章、文档/PDF 落位、词法同步、退库与人账保存后，服务端自动合并队列在后台刷新 `工作区/运维/last-audit.json`，保持「同步与维护 → 体检提醒」不误亮过期警告。
- 写互斥：写任务与 HTTP 账表写操作共用 `工作区/运维/.writer.lock`（`LOCK_EX`），有写任务在跑时接口返 409。
- 读共享：只读任务共用 `工作区/运维/.reader.lock`（`LOCK_SH`），最多两个并行，不阻塞写任务。
- 取消：对子进程组发 `SIGTERM`，10 秒后仍有活口则 `SIGKILL`；已结束与 `awaiting_review` 任务不可取消（409）。

## 验收

```bash
uv run --frozen python tests/acceptance/ops/check_ops.py
uv run --frozen python tests/acceptance/ops/test_sidebar_sticky.py
PYTHONPATH=$PWD/tests/acceptance/ops uv run --frozen python tests/manual/ops/shot_ops.py
```

- `check_ops.py` 造临时根与 stub 脚本，不碰真库，末行 `ALL PASS`。
- `test_sidebar_sticky.py` 校对 `html`/`body` `overflow-x: clip` 与 `.sidebar` sticky 规则，并用 headless Chromium 验 `1440x700` / `1440x900` 侧栏吸顶与末卡贴底、`1920x1080` 去顶栏搜索按钮 / `1200px` 限宽居中 / 顶部分隔线齐平与短页自然排布，以及 `390x844` 窄屏回落，末行 `ALL PASS`。
- `shot_ops.py` 用 headless Chromium 走完六模块、兼容子页签、导入/分诊/删书弹窗与 1440x900 / 390x844 两档布局，末行 `CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`（浏览器读 `CHROME_BIN`，缺省自动探测 `chromium`）。
- 轻量导航回归亦可用 `.agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py`。

## 已知限制

- 无登录、只绑 `127.0.0.1`；远程访问前必须自行加鉴权与 TLS。
- 上传上限 2 GB；更大文件请直接放进 `books/_收件箱/`。
- 全量索引重建无百分比进度，只提供日志尾部与耗时。
- 只读打开 `build/library.db`（WAL 模式）时会由 SQLite 自动生成 `library.db-shm`，不写数据。
