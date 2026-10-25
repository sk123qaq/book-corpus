# 统一操作台（127.0.0.1:8770）

书库日常操作的本地控制台。页面读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调
`工作区/脚本/` 里的脚本。页面不写 `目录/分类表.csv`、`library.db`、`book.json`、
`目录/检索/index.db`，除「上传」把文件放进 `库/_收件箱/` 外不碰 `库/`。

方案见 `工作区/方案/统一操作台-方案-2026-10-24.md`，接口与文件归属见
`工作区/运维/CONTRACT.md`。

## 起与停

```bash
cd ~/文档/books
python3 工作区/运维/server.py                 # 127.0.0.1:8770
python3 工作区/运维/server.py --port 0        # 随机端口（测试用，stdout 打印实际端口）
python3 工作区/运维/server.py --root /tmp/x   # 换数据根，代码仍指本文件所在处
```

Ctrl-C 停服务。服务退出不杀检索演示页（8765），也不杀接手前已经在跑的子进程。

## 文件

| 路径 | 内容 |
|---|---|
| `server.py` | 全部服务端逻辑：`ops.db`、任务队列与锁、门槛判定、HTTP 接口 |
| `web/index.html` `web/app.js` `web/style.css` | 七个页签的原生 JS 页面，无框架无 CDN |
| `ops.db` | 任务与步骤（首次启动自建，可重建，丢了只是任务历史没了） |
| `jobs/<id>/` | `log.txt`、`commands.json`、`pid`、`plan.json`、`result.json`、`dry-run.json`、`thumbs/*.png` |
| `.writer.lock` | 写任务互斥用的 `fcntl.flock` 文件，内容为空 |
| `search.log` `search.pid` | 检索演示页的子进程输出与 PID |
| `last-audit.json` | 审计缓存，由 `ops_status.py --deep` 写，页面只读 |
| `验收/check_ops.py` | 服务与任务自检，末行 `ALL PASS` |
| `验收/shot_ops.py` | headless Chrome 截图与浏览器自检，末行 `SHOT PASS` |
| `验收/总览-1440x900.png` `验收/总览-390x844.png` | 两个断点的截图 |

## 三条只读数据源

1. `工作区/脚本/ops_status.py --json --root <root>`：顶栏与总览的读数。脚本不存在时回落到
   `OPS_STATUS_FIXTURE` 指向的 JSON 文件，两者都没有就显示空状态并在顶栏标「状态：缺」。
2. `ops.db`：任务、步骤、日志路径、状态计数。
3. `目录/library.db`（只读打开）与各批次的 `worklist.tsv`、`logs/<uid>.json`、
   `triage_final.csv`、`skip_list.tsv`、`defer/lang.tsv`：待拆列表、删书搜索、PDF 队列。

## 页面

顶栏：书数、有 md 数、待转、索引落后、收件箱、书目表时间，右侧搜索入口与 8765 健康点。

- **总览**：六阶段流水线、三本账（人账 / 产物 / 机账）、断点 G3/G4/G5/G10/G11、下一步、最近 5 个任务。
- **新书导入**：上传（裸 body 流式写 `库/_收件箱/<名>.part` 再 `os.replace`）、收件箱表、扫描出计划、
  逐行改书名/一级二级/保留/动作、写回计划、应用入库（自动接 `build_library` 与 `gen_book_catalog`）。
  门槛 `conflicts=0` 且 `未匹配 0 条`。低置信行标黄。页面加载时自动带出最近一份可用计划
  （add/replace 的源文件还在收件箱），刷新不用重扫。
- **电子书**：待拆 epub/mobi/azw3/azw 勾选开批次；单本状态读 `logs/<uid>.json` 的
  `status`/`verify_rc`/`audit_rc`/`audit_ratio`/`chapters`/`fix`/`previous`；失败书可「只重跑这本」
  （同一批次 `--only`）；「重拆已有 md」走 `--replace`，旧 md 路径由脚本写进 `previous`。
- **PDF**：队列按 route 分组与本数页数、现有批次进度与额度、新 PDF 分诊（指标 + 3 张缩略图 +
  模型建议 route/置信/理由）、人工选 route 后确认、按 route 分组转 md。
- **维护**：状态条（检索索引、浏览入口、体检、书目表、上次收尾）、一键收尾（书目数据库与总表、
  检索索引、入口、体检与验收共 8 步，跑完刷新体检缓存）、「只想跑其中几步」里按步勾选、
  三份下载、清理旧记录、最近收尾记录。页面只写任务名与做什么，脚本名与参数不进页面，
  要查命令行看日志详情。
- **删书**：uid 或书名搜索，`retire_book.py --dry-run` 的七层预览（书目录与要删的、源与共享动作、
  人账与 DB、expand、奖项、视图、书目表与系列文档）、冷宫目标、快照与计划哈希；应用要输入 uid 一致、
  勾确认、快照就绪。
- **日志**：任务表（状态筛选、耗时、rc、摘要）、详情（步骤、完整命令行、日志尾部 200 行、下载、
  重跑、停止）。

## HTTP

POST 收 JSON，返回 `{ok, job_id}` 或 `{ok:false, error}`。POST 校验 `Host` 与 `Origin` 都是本机。

```text
GET  /api/status                       # 状态脚本 + ops.db 计数 + 锁状态；?refresh=1 绕过缓存，?deep=1 另跑深度审计并刷新 last-audit.json
GET  /api/jobs?state=&limit=           # 任务表（带 steps）
GET  /api/jobs/<id>                    # 任务详情：steps、commands、log_tail、产物路径
GET  /api/jobs/<id>/log?offset=        # {offset,next,size,eof,text}，text 已脱敏
POST /api/jobs/<id>/cancel             # SIGTERM 进程组，10 秒后 SIGKILL
POST /api/jobs/<id>/rerun              # 用 jobs/<id>/commands.json 再排一个
POST /api/jobs/clean                   # 日志 90 天 / 200 个任务，缩略图 30 天
POST /api/import/upload                # 裸 body，X-Filename（URL 编码），上限 2 GB
POST /api/import/scan                  # ingest.py scan
POST /api/import/plan                  # {job_id, items}：把编辑后的计划写回 jobs/<id>/plan.json
POST /api/import/apply                 # {plan_path}
POST /api/split/run                    # {uids, workers, replace} 或 {only, batch}
POST /api/pdf/triage                   # {uid}
POST /api/pdf/confirm                  # {uid, route, reason, model, job_id}
POST /api/pdf/run                      # {uids, limit, workers, route?}
POST /api/maintain                     # {steps:[db,index,topics,awards,audit,verify,p1,catalog,doc]}
POST /api/delete/dry-run               # {uid, reason}
POST /api/delete/apply                 # {uid, reason, plan_hash, confirm_uid}
GET  /api/search/health                # 代理查 8765 /api/health，避开浏览器跨域
POST /api/search/start                 # sys.executable 工作区/演示-检索流程/server.py
GET  /api/file?path=...                # 白名单文件
GET  /api/inbox                        # 收件箱清单
GET  /api/import/latest                # 最近一份可用计划（源文件还在收件箱）
GET  /api/pending                      # v_pending_md 列表（只读 library.db）
GET  /api/split/books?batch=           # 批次的 logs/<uid>.json 汇总
GET  /api/pdf/queue                    # 三本 PDF 账 + 批次进度
GET  /api/retire/search?q=             # uid 或书名，带源、md、奖项
```

契约里没有的六个：`/api/import/plan`、`/api/import/latest`、`/api/jobs/<id>/cancel`、
`/api/jobs/<id>/rerun`、`/api/jobs/clean`、以及五个只读列表接口（`inbox`、`pending`、
`split/books`、`pdf/queue`、`retire/search`）。页面需要它们，见文末「偏差」。

文件白名单只有三类：`工作区/运维/jobs/<id>/` 下的 `log.txt`、`plan.json`、`commands.json`、
`result.json`、`review.json`、`dry-run.json`、`status.json`、`review/<uid>.json`、`thumbs/*.png`；
`目录/书目总表.xlsx`、`目录/书目总表.csv`、`目录/分类预填.csv`。其它一律 404，路径接
`realpath` 后再比一次前缀。

## 任务与门槛

状态：`queued` → `running` → `succeeded` / `failed` / `cancelled`；扫描与分诊跑完停在
`awaiting_review`（产物等人确认，不占锁）；删书预览判 `succeeded` 并留下 `dry-run.json`；
重启后 PID 核对不上的 `running` 标 `orphaned`。

成功与否由任务自己的门槛判定，退出码只是证据之一：

| kind | 门槛 |
|---|---|
| `import.scan` | 退出码 0 且写出 `plan.json`，停在 `awaiting_review` |
| `import.apply` | 三步全 0，日志里有 `"conflicts": 0` 与 `未匹配 0 条` |
| `split.run` | 全 0 且有 `--report-json` 的 `status_counts`；有 `pending_review` 时停在 `awaiting_review` |
| `pdf.triage` | `result.json` 有指标与缩略图，停在 `awaiting_review` |
| `pdf.confirm` | 退出码 0（review 由页面先写盘） |
| `pdf.run` | 有 `remaining_books`/`landed` 统计；`quota` 停在 `awaiting_review`，有 `failed` 判失败 |
| `maintain` | 每步退出码 0 且出现该步门槛：审计 `FAIL 0 项`、验收 `^全过$`、检索验收 `失败 0 项`、书目表 `未匹配 0 条` |
| `delete.dry-run` | 退出码 0 且拿到带 `plan_hash` 的 JSON，判 `succeeded` |
| `delete.apply` | 退出码 0，脚本不报 `ok:false` |

服务跑的命令（argv 列表，`shell=False`，cwd 为数据根）：

```text
python3 工作区/脚本/ingest.py scan --json --out 工作区/运维/jobs/<id>/plan.json --root <root>
python3 工作区/脚本/ingest.py apply <plan> --apply --root <root>
python3 工作区/脚本/build_library.py --emit-json
python3 工作区/脚本/gen_book_catalog.py
python3 工作区/脚本/split_md_batch.py --batch split-ops-<ts> --uids B1,B2 --workers N --report-json [--replace]
python3 工作区/脚本/split_md_batch.py --batch <batch> --only B0001 --workers 1 --report-json
python3 工作区/脚本/build_library.py --emit-json     # 拆章与 PDF 任务跑完后接
python3 工作区/脚本/gen_book_catalog.py              # 拆章与 PDF 任务跑完后接
python3 工作区/脚本/ops_pdf_triage.py --uid B1234 --out 工作区/运维/jobs/<id> --root <root>
python3 工作区/脚本/pdf_apply_review.py --batch pdf-ocr-ops-<日期> \
        --triage 工作区/批次/pdf-ocr-ops-<日期>/triage.csv \
        --out-dir 工作区/批次/pdf-ocr-ops-<日期> \
        --review 工作区/批次/pdf-ocr-ops-<日期>/review
python3 工作区/脚本/pdf_md_batch.py --batch pdf-ocr-ops-<日期> --route ocr|text --uids ... --workers N \
        --triage 工作区/批次/pdf-ocr-ops-<日期>/triage_final.csv --report-json [--limit N]
python3 工作区/脚本/build_library.py --emit-json     # PDF 任务跑完后接
python3 工作区/脚本/gen_book_catalog.py              # PDF 任务跑完后接
python3 工作区/脚本/retire_book.py --uid U --reason R --dry-run --json --root <root>
python3 工作区/脚本/retire_book.py --uid U --reason R --plan-hash H --apply --root <root>
python3 工作区/脚本/ops_status.py --json --root <root>
```

脚本不存在时任务立刻判失败，日志里写「[缺] 脚本不存在：<路径>」，不会静默当成功。

## 锁、并发与取消

- 一个写任务同时只跑一个：`工作区/运维/.writer.lock` 上 `flock(LOCK_EX)`，子进程与页面同时受约束。
- 只读任务（扫描、分诊、删书预览）用 `LOCK_SH`，最多两个并行。
- 写任务的入口在收到请求时就检查是否有写任务在跑，有就回 409，避免排队两个改库的任务。
- 取消：给子进程组发 SIGTERM，10 秒没退再 SIGKILL。拆章与 PDF 驱动幂等，重跑会跳过已落地产物。
  已结束与 `awaiting_review` 的任务不能取消，接口回 409。
- 接管：启动时对每个 `running` 任务读 `pid`，比 `/proc/<pid>/cmdline` 里的脚本名；对得上继续跑并
  盯进程退出，对不上标 `orphaned`。僵尸进程按已退出处理。不自动杀任何进程。

## 验收

```bash
cd ~/文档/books
python3 -m py_compile 工作区/运维/server.py 工作区/运维/验收/check_ops.py 工作区/运维/验收/shot_ops.py
python3 工作区/运维/验收/check_ops.py        # 随机端口 + 临时根 + stub 脚本，末行 ALL PASS
python3 工作区/运维/验收/shot_ops.py         # 1440x900 与 390x844，报 CONSOLE ERROR n 与 overflow=
```

`check_ops.py` 自己造临时根：`工作区/脚本/` 下写一套 stub（`ingest.py`、`split_md_batch.py`、
`pdf_md_batch.py`、`ops_pdf_triage.py`、`retire_book.py` 等），`目录/library.db` 造两张书的最小表，
`OPS_STATUS_FIXTURE` 给状态。它验这些东西：状态字段、静态页、任务生命周期与门槛、写互斥（真去
`flock` 探一次）、取消、上传与文件名、文件白名单与路径穿越、跨站与 Host 拒绝、计划写回、stub 导入、
stub 分诊、最近计划的复用与失效（源文件不在就不给计划）、PDF 分组、收尾门槛、删书预览与哈希、重跑、
清理、搜索健康、重启接管与孤儿。中途不碰真库，也不调模型（`PI_BIN` 指向 `验收/fixtures/pi_stub.py`）。

`shot_ops.py` 复用同一套临时根，起 headless Chrome 走 CDP：把七个页签点一遍收
`Runtime.exceptionThrown` / `console.error` / `Log.error`，再量 `scrollWidth > innerWidth`，
出两张截图。导入页另查三件事：加载后有计划、点「扫描收件箱」后换成新计划、刷新后计划还在。
维护页另查：一键收尾能跑完、高级勾选跨渲染保留、页面上不出现脚本名与参数。
这台机器上 8765 常常已经在跑，健康检查会照实报 `running=True`。

## 偏差与已知限制

与方案 §8 的接口清单相比多了几个接口（见上），原因：契约没有下发「写回编辑后的计划」「取消」
「重跑」「清理」「只读列表」的入口，而页面缺了它们就是空壳。

- `split_md_batch.py` 不新增 `--archive-old`（契约为准）。页面读 `logs/<uid>.json` 的 `previous`
  拿旧 md 路径，不再自己猜目录。
- 需要 `--root` 才换根的脚本（`ingest.py`、`ops_pdf_triage.py`、`retire_book.py`、`ops_status.py`）
  一律显式带 `--root <root>`；在真库上等于默认值，在临时根上才指得对。`build_library.py`、
  `gen_book_catalog.py`、`split_md_batch.py`、`pdf_md_batch.py`、收尾那批脚本不认 `--root`，
  所以临时根里放的是 stub，真库上跑的是真脚本。
- PDF 确认不再假定全局 review 目录。`api_pdf_confirm` 先按 uid 找到分诊 job，把它的 `triage.csv` 按 uid 合并进 `pdf-ocr-ops-<日期>/triage.csv`，把 `census.jsonl`、`text.jsonl`/`scan.jsonl` 按 uid 合并进批次目录，再写 `review/<uid>.json`，最后调 `pdf_apply_review.py --triage <批次>/triage.csv --out-dir <批次> --review <批次>/review`。同一天确认多本会累积在同一批次。确认成功后，原来的分诊任务从 `awaiting_review` 回写为 `succeeded`。
- `GET /api/jobs/<id>/log` 回 JSON（`text`/`next`/`size`/`eof`），不是裸文本；契约没定格式。
- `GET /api/import/latest` 只在计划里的 add/replace 源文件还在收件箱时返回。应用之后源文件已经搬进
  书目录，计划自动失效，刷新不会把旧计划拿回来重放。等待任务的页面把 `awaiting_review` 当终态，
  因为进程已经退出，只是产物等人确认。
- `awaiting_review` 的含义定为「产物等人确认」：扫描与分诊跑完停在这里，确认动作由新的任务接手，
  父任务状态不再回写。
- 状态脚本没就绪时页面显示 0 与「状态：缺」，不假装有数。真库实测字段与 `ops_status.py` 现状一致。
- 无登录、只绑本机。任何远程访问要先加鉴权与 TLS。
- 上传上限 2 GB；更大的文件用文件管理器放进 `库/_收件箱/`。
- 索引重建没有百分比进度，只有日志尾部与耗时。
- 目录名仍是分类表的投影：改分类后要手工 `mv`，页面只提示。
- 读 `目录/library.db` 用 `mode=ro`，WAL 库会顺带留下一只 `library.db-shm`（SQLite 自己重建成，
  不写任何数据）。要连这个也不留，就得开 `immutable=1`，但那样库被重建时可能读到旧的账，不划算。
