# 统一操作台（127.0.0.1:8770）

书库日常操作的本地控制台。页面读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调
`src/bookcorpus/scripts/` 里的脚本。页面不写 `目录/分类表.csv`、`library.db`、`book.json`、
`目录/检索/index.db`，除「上传」把文件放进 `库/_收件箱/` 外不碰 `库/`。

方案见 `docs/design/统一操作台-方案-2026-10-24.md`，接口与文件归属见
`apps/ops/CONTRACT.md`。

## 起与停

```bash
cd <工作副本根目录>
python3 apps/ops/server.py                 # 127.0.0.1:8770
python3 apps/ops/server.py --port 0        # 随机端口（测试用，stdout 打印实际端口）
python3 apps/ops/server.py --root /tmp/x   # 换数据根，代码仍指本文件所在处
```

Ctrl-C 停服务。服务退出不杀检索演示页（8765），也不杀接手前已经在跑的子进程。

## 文件

| 路径 | 内容 |
|---|---|
| `server.py` | 全部服务端逻辑：`ops.db`、任务队列与锁、门槛判定、HTTP 接口 |
| `web/index.html` `web/app.js` `web/style.css` | 七个页签加一个 PDF 清单子页（`#pdf-list`）的原生 JS 页面，无框架无 CDN |
| `ops.db` | 任务与步骤（首次启动自建，可重建，丢了只是任务历史没了） |
| `jobs/<id>/` | `log.txt`、`commands.json`、`pid`、`plan.json`、`result.json`、`dry-run.json`、`thumbs/*.png` |
| `.writer.lock` | 写任务互斥与批次账读改写用的 `fcntl.flock` 文件，内容为空 |
| `.reader.lock` | 只读任务共用锁的 `fcntl.flock` 文件，内容为空；与写任务互不阻塞 |
| `search.log` `search.pid` | 检索演示页的子进程输出与 PID |
| `last-audit.json` | 审计缓存，由 `ops_status.py --deep` 写，页面只读 |
| `验收/check_ops.py` | 服务与任务自检，末行 `ALL PASS` |
| `验收/shot_ops.py` | headless Chrome 截图与浏览器自检，末行 `SHOT PASS` |
| `验收/总览-1440x900.png` `验收/总览-390x844.png` | 两个断点的截图 |

## 三条只读数据源

1. `src/bookcorpus/scripts/ops_status.py --json --root <root>`：顶栏与总览的读数。脚本不存在时回落到
   `OPS_STATUS_FIXTURE` 指向的 JSON 文件，两者都没有就显示空状态并在顶栏标「状态：缺」。
2. `ops.db`：任务、步骤、日志路径、状态计数。
3. `目录/library.db`（只读打开）与各批次的 `worklist.tsv`、`logs/<uid>.json`、
   `triage_final.csv`、`skip_list.tsv`、`defer/lang.tsv`：待拆列表、删书搜索、PDF 队列。

## 页面

页面写给用操作台管书的人，不写脚本名、参数、文件路径与内部代号。这些只留在这份 README、
`CONTRACT.md` 和脚本注释里。状态词用普通话（正常 / 待处理 / 需处理），不用通、半、缺、外，
也不用 S1、G3 这类代号。

顶栏：藏书、可读、待转、索引状态、收件箱，右侧阅读搜索入口与健康点。

- **总览**：一句话状态与主行动、六格流水线（收书 / 入账 / 上架 / 出正文 / 可检索 / 体检）、
  下一步、最近 4 个任务。三本账与断点表已收进各自页面，总览不再重复。
- **新书导入**：可点击或拖放的上传区（裸 body 流式写 `库/_收件箱/<名>.part` 再 `os.replace`）、
  收件箱表、生成入库清单、逐行改书名/分类/保留/动作、写回计划、确认入库（自动接
  `build_library` 与 `gen_book_catalog`）。门槛 `conflicts=0` 且 `未匹配 0 条`，只进任务判定与日志，
  不进页面文案。低置信行标黄，表下写「黄色行建议再核对一遍」。页面加载时自动带出最近一份可用计划
  （add/replace 的源文件还在收件箱），刷新不用重扫。
- **电子书**：待拆 epub/mobi/azw3/azw 勾选开批次；单本状态读 `logs/<uid>.json` 的
  `status`/`verify_rc`/`audit_rc`/`audit_ratio`/`chapters`/`fix`/`previous`。批次卡片每行三个动作：
  「重拆这本」用同一批次 `--only` 并带 `--force`，从头再拆；「重新检查」带 `--recheck`，只复核暂存区里
  已有的正文，重跑 verify 与 audit，过关就直接进库；「查看问题」是只读任务，跑 `md_review_report.py`
  列出审计比、正文字数、图片路径字数、跨文件重复片段、自链、未收录的页与原因、结构报警。
  「重拆已有 Markdown」走 `--replace`，旧 md 路径由脚本写进 `previous`，这些选项收在「更多选项」里。
- **PDF**：三块。一是状态带，四格（可转 / 未分诊 / 只记图册 / 日文暂缓）带本数与页数，图册格附
  「其中 N 本标了复看」、日文格附「N 本上次失败」，每格点进去是清单里对应的那一类；带下一行写今天已用
  额度（X / 20,000 页）与旧批次的一行摘要（411 本里 369 落地、42 未落地，拆成失败 / 待复核 / 未开跑），
  「看明细」直接跳到清单里日文暂缓且失败的那批。二是两张动作卡：左「批量分诊」写未分诊多少本、多少本文本层
  会建议直接抽取、多少本扫描件会建议识别，跑起来按钮禁用、下面出进度条（分诊中 x / N 本，任务还在排队时
  写「排队」）；进度读的是任务记录里每个 uid 的步数，刷新页面或切走再回来都还在，任务收尾才撤并提示一次。右「确认后转 md」是已选
  摘要、去清单选书、最多转几本、同时处理与开始转写。三是最近的分诊与转 md。单本分诊面板已删，分诊改成批量：
  出的建议在清单里逐行确认。
  页面上的处理方式是中文显示（扫描识别、直接抽取、只记图册、日文暂缓），
  写回接口的 value 仍是 `OCR`、`本地抽文本`、`不转-记图册`、`日文暂缓`。
- **待转 PDF 清单**（PDF 页的子页 `#pdf-list`，不在左侧导航）：待转的 PDF 一本书一行，处置分四类
  （可转 / 只记图册 / 日文暂缓 / 未分诊），进度分四档（未开始 / 在跑 / 已落地 / 失败），页数缺的由
  `工作区/.venv-pdf/bin/python` 子进程读。两行筛选：第一行是编号或书名、处置复选、一级分类、排序；
  第二行是进度三选（未开始 / 在跑 / 失败）与「只看复看」。筛选顺序是处置 → 进度 → 只看复看 →
  一级分类 → 关键字 → 排序。有未确认建议的行在处置后写「建议 扫描识别 0.95」；备注列图册写
  不转的理由、失败行写上次停在哪种状态、有建议的写建议的理由；行末只有一格动作，有建议是「按建议确认」，
  否则是「改处置」，弹窗里四个处置选一个提交，写完只记台账不出正文。底部条另有一个「按建议确认 N 本」，
  只算选中且带建议的行，N 为 0 时置灰。
  勾选跨页保留，提交后清空，刷新页面清空。转写卡不再有编号输入框，只显示已选摘要与「去清单选书」；
  只记图册与日文暂缓默认不转，勾上之后要再确认一次（服务端把它们改判成扫描识别再跑），
  未分诊的只投分诊任务，不出正文。
- **维护**：状态条（检索索引、浏览入口、体检、书目表、上次收尾）、体检提醒（逐条列出，标成需处理或提醒）、一键收尾（书目数据库与总表、
  检索索引、入口、体检与验收共 8 步，跑完刷新体检缓存）、「只想跑其中几步」里按步勾选、
  三份下载、清理旧记录、最近收尾记录。页面只写任务名与做什么，脚本名与参数不进页面，
  要查命令行看日志详情。
- **删书**：uid 或书名搜索，`retire_book.py --dry-run` 的七层预览（书目录与要删的、源与共享动作、
  人账与 DB、expand、奖项、视图、书目表与系列文档）、冷宫目标、回滚副本与校验码；应用要输入 uid 一致、
  勾确认、快照就绪。页面把快照与计划哈希叫作回滚副本与校验码。
- **日志**：任务表（状态筛选、耗时、摘要）、详情（步骤、收起后的完整命令行、日志尾部 200 行、下载、
  重跑、停止）。类型与步骤状态显示中文。

## HTTP

POST 收 JSON，返回 `{ok, job_id}` 或 `{ok:false, error}`。GET 与 POST 都校验 `Host` 与 `Origin` 都是本机，不对就 403。4xx 一律断开连接（出错路径可能没读尽请求体，keep-alive 会失步）。

```text
GET  /api/status                       # 状态脚本 + ops.db 计数 + 锁状态；?refresh=1 绕过缓存，?deep=1 另跑深度审计并刷新 last-audit.json
GET  /api/jobs?state=&limit=           # 任务表（带 steps）
GET  /api/jobs/<id>                    # 任务详情：steps、commands、log_tail、产物路径
GET  /api/jobs/<id>/log?offset=        # {offset,next,size,eof,text}，text 已脱敏
POST /api/jobs/<id>/cancel             # SIGTERM 进程组，10 秒后组里还有活口就 SIGKILL
POST /api/jobs/<id>/rerun              # 用 jobs/<id>/commands.json 再排一个
POST /api/jobs/clean                   # 日志 90 天 / 200 个任务；分诊任务在缩略图 30 天保留期内整本跳过
POST /api/import/upload                # 裸 body，X-Filename（URL 编码），上限 2 GB
POST /api/import/scan                  # ingest.py scan
POST /api/import/plan                  # {job_id, items}：把编辑后的计划写回 jobs/<id>/plan.json
POST /api/import/apply                 # {plan_path}
POST /api/split/run                    # {uids, workers, replace} 或 {only, batch}（单本带 --force）
POST /api/split/recheck                # {uid, batch}：只复核暂存区产物，进库算写任务
POST /api/split/inspect                # {uid, batch}：只读，出这本书的问题清单
POST /api/pdf/triage                   # {uid}（页面上已不用，接口留着给验收与命令行）
POST /api/pdf/triage-batch             # {uids?}：不给就分诊所有未分诊的书，一个任务 N 步；已有建议的书默认跳过，不重复烧模型
POST /api/pdf/settle                   # {items:[{uid, route}]}：改处置，只写台账不出正文；route 不收人工确认
POST /api/pdf/confirm                  # {uid, route, reason, model, job_id}
POST /api/pdf/run                      # {uids, limit, workers, force}：可转的按 route 跑；图册与日文要 force 才改判成 OCR；只选未分诊时只投分诊、job_id 为 null
POST /api/maintain                     # {steps:[db,index,topics,awards,audit,verify,p1,catalog,doc]}
POST /api/delete/dry-run               # {uid, reason}
POST /api/delete/apply                 # {uid, reason, plan_hash, confirm_uid}
GET  /api/search/health                # 代理查 8765 /api/health，避开浏览器跨域
POST /api/search/start                 # sys.executable apps/demo/server.py
GET  /api/file?path=...                # 白名单文件；文本产物走脱敏，png/xlsx 原样
GET  /api/inbox                        # 收件箱清单
GET  /api/import/latest                # 最近一份可用计划（源文件还在收件箱）
GET  /api/pending                      # v_pending_md 列表（只读 library.db）
GET  /api/split/books?batch=           # 批次的 logs/<uid>.json 汇总
GET  /api/pdf/queue                    # 三本 PDF 账 + 批次进度（batch.partition 是落地与未落地的分解）
GET  /api/pdf/books                    # 待转 PDF 清单：一本书一行的处置、进度、页数、备注、复看与建议 + 四类计数 + today + 未分诊的文本层分解
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
| `split.run` | 全 0 且读到 `--report-json` 的 `pending_review` 清单；清单非空时停在 `awaiting_review`（`staged`、`no-stage`、`staged-review` 都在里面） |
| `split.inspect` | 退出码 0 且 `result.json` 有这本书的记录；只读任务，判 `succeeded` |
| `pdf.triage` | `result.json` 有指标与缩略图，停在 `awaiting_review` |
| `pdf.confirm` | 退出码 0（review 由页面先写盘） |
| `pdf.run` | 有 `remaining_books`/`landed` 统计；`quota` 停在 `awaiting_review`，有 `failed` 判失败 |
| `maintain` | 每步退出码 0 且出现该步门槛：审计 `FAIL 0 项`、验收 `^全过$`、检索验收 `失败 0 项`、书目表 `未匹配 0 条` |
| `delete.dry-run` | 退出码 0 且拿到带 `plan_hash` 的 JSON，判 `succeeded` |
| `delete.apply` | 退出码 0，脚本不报 `ok:false` |

服务跑的命令（argv 列表，`shell=False`，cwd 为数据根）：

```text
python3 src/bookcorpus/scripts/ingest.py scan --json --out 工作区/运维/jobs/<id>/plan.json --root <root>
python3 src/bookcorpus/scripts/ingest.py apply <plan> --apply --root <root>
python3 src/bookcorpus/scripts/build_library.py --emit-json
python3 src/bookcorpus/scripts/gen_book_catalog.py
python3 src/bookcorpus/scripts/split_md_batch.py --batch split-ops-<ts> --uids B1,B2 --workers N --report-json [--replace]
python3 src/bookcorpus/scripts/split_md_batch.py --batch <batch> --only B0001 --workers 1 --report-json
python3 src/bookcorpus/scripts/split_md_batch.py --batch <batch> --only B0001 --workers 1 --report-json --force
python3 src/bookcorpus/scripts/split_md_batch.py --batch <batch> --only B0001 --workers 1 --report-json --recheck
python3 src/bookcorpus/scripts/md_review_report.py <batch> B0001 --json 工作区/运维/jobs/<id>/result.json
python3 src/bookcorpus/scripts/build_library.py --emit-json     # 拆章与 PDF 任务跑完后接
python3 src/bookcorpus/scripts/gen_book_catalog.py              # 拆章与 PDF 任务跑完后接
python3 src/bookcorpus/scripts/ops_pdf_triage.py --uid B1234 --out 工作区/运维/jobs/<id> --root <root>
python3 src/bookcorpus/scripts/pdf_apply_review.py --batch pdf-ocr-ops-<日期> \
        --triage 工作区/批次/pdf-ocr-ops-<日期>/triage.csv \
        --out-dir 工作区/批次/pdf-ocr-ops-<日期> \
        --review 工作区/批次/pdf-ocr-ops-<日期>/review    # 清单里二次确认的图册/日文也走这条，排在转 md 前一步
python3 src/bookcorpus/scripts/pdf_md_batch.py --batch pdf-ocr-ops-<日期> --route ocr|text --uids ... --workers N \
        --triage 工作区/批次/pdf-ocr-ops-<日期>/triage_final.csv --report-json [--limit N]
python3 src/bookcorpus/scripts/build_library.py --emit-json     # PDF 任务跑完后接
python3 src/bookcorpus/scripts/gen_book_catalog.py              # PDF 任务跑完后接
python3 src/bookcorpus/scripts/retire_book.py --uid U --reason R --dry-run --json --root <root>
python3 src/bookcorpus/scripts/retire_book.py --uid U --reason R --plan-hash H --apply --root <root>
python3 src/bookcorpus/scripts/ops_status.py --json --root <root>
```

脚本不存在时任务立刻判失败，日志里写「[缺] 脚本不存在：<路径>」，不会静默当成功。

## 锁、并发与取消

- 写任务同时只跑一个：写任务与 HTTP 批次账读改写都在 `工作区/运维/.writer.lock` 上 `flock(LOCK_EX)`；
  写任务的入口在收到请求时就检查是否有写任务在跑，有就回 409，避免排队两个改库的任务。
- 只读任务（扫描、分诊、查看问题、删书预览）共用 `工作区/运维/.reader.lock` 的 `LOCK_SH`，最多两个并行，
  **与写任务互不阻塞**——拆章在跑时点分诊照跑，分诊在跑时点写任务也不再等锁。
- 读写不打架靠两条约定：只读任务只写自己的 `jobs/<id>/`，写任务对共享文件一律 tmp 加 `os.replace`
  （见库根 AGENTS.md 写入规则）。新增任务 kind 时按这两条定 `readonly`，标错了锁就白分了。
- 取消：给子进程组发 SIGTERM，10 秒后组里还有活口（比如吞了 SIGTERM 的孙进程）就整组 SIGKILL。拆章与 PDF 驱动幂等，重跑会跳过已落地产物。
  已结束与 `awaiting_review` 的任务不能取消，接口回 409。
- 接管：启动时对每个 `running` 任务读 `pid`，比 `/proc/<pid>/cmdline` 里的脚本名；对得上就接着盯，
  进程退出后把剩下的步骤补跑完再收尾，对不上标 `orphaned`。僵尸进程按已退出处理。不自动杀任何进程。
  接管来的那一步拿不到退出码，步骤表记 `unknown`，任务停在 `awaiting_review` 并写明是哪一步。

## 验收

```bash
cd <工作副本根目录>
python3 -m py_compile apps/ops/server.py tests/acceptance/ops/check_ops.py tests/manual/ops/shot_ops.py
python3 tests/acceptance/ops/check_ops.py        # 随机端口 + 临时根 + stub 脚本，末行 ALL PASS
python3 tests/manual/ops/shot_ops.py         # 1440x900 与 390x844，报 CONSOLE ERROR n 与 overflow=
```

`check_ops.py` 自己造临时根：`src/bookcorpus/scripts/` 下写一套 stub（`ingest.py`、`split_md_batch.py`、
`pdf_md_batch.py`、`ops_pdf_triage.py`、`retire_book.py` 等），`目录/library.db` 造五本书的最小表（含三本待转 PDF），
`OPS_STATUS_FIXTURE` 给状态。它验这些东西：状态字段、静态页、任务生命周期与门槛、写互斥（真去
`flock` 探一次）、取消、上传与文件名、文件白名单与路径穿越、跨站与 Host 拒绝、计划写回、stub 导入、
stub 分诊、最近计划的复用与失效（源文件不在就不给计划）、PDF 分组、PDF 清单的处置与二次确认（先改判再转、
未分诊只投分诊）、批量分诊（步数等于未分诊本数、每步输出目录、停在待确认、摘要写本数与建议）、
按建议确认与改处置（先写 review 再跑 `pdf_apply_review.py`、改完那本书算可转、人工确认被拒、今天额度），
拆章的单本重拆与重新检查与查看问题（驱动收到的 `--force`/`--recheck` 都核过）、
页面状态词与驱动状态表对齐、收尾门槛、删书预览与哈希、重跑、
清理、搜索健康、重启接管与孤儿。中途不碰真库，也不调模型（`PI_BIN` 指向 `验收/fixtures/pi_stub.py`）。

`shot_ops.py` 复用同一套临时根，起 headless Chrome 走 CDP：把七个页签点一遍收
`Runtime.exceptionThrown` / `console.error` / `Log.error`，再量 `scrollWidth > innerWidth`，
出两张截图。导入页另查三件事：加载后有计划、点「生成入库清单」后换成新计划、刷新后计划还在。
维护页另查：一键收尾能跑完、高级勾选跨渲染保留、页面上不出现脚本名与参数。PDF 页另查：
没有编号输入框、有批量分诊按钮与旧批次摘要、有「去清单选书」，点它之后停到 `#pdf-list`，清单页有两行
筛选（进度三个勾与只看复看）、行末有「改处置」或「按建议确认」、改处置弹窗里四个处置，切页再回来
勾选与关键字都在，390 宽不横向溢出。除日志页外，
每页再扫一遍可见文案，出现 `.py`、`--`、`ops.db`、`route`、`worklist`、`S1`、`G3` 这类
实现细节就判失败；日志页保留命令行与门槛原文，属于诊断面。
这台机器上 8765 常常已经在跑，健康检查会照实报 `running=True`。

## 偏差与已知限制

与方案 §8 的接口清单相比多了几个接口（见上），原因：契约没有下发「写回编辑后的计划」「取消」
「重跑」「清理」「只读列表」的入口，而页面缺了它们就是空壳。

- `split_md_batch.py` 不新增 `--archive-old`（契约为准）。页面读 `logs/<uid>.json` 的 `previous`
  拿旧 md 路径，不再自己猜目录。
- 「查看问题」只能查还留在暂存区的书：`md_review_report.py` 只看 `staged-review`、`error`、
  `convert-failed`、`split-failed`、`staged` 这几种状态。已进库的书没有暂存正文可查，批次里没有
  这本书的记录时任务判失败并提示先重拆。
- 「重新检查」的文本量：mobi/azw3 先找转换时留下的中间 EPUB，找不到就用源书再转一次；还转不出来就不入库，
  留待复核（`audit_rc` 留空，页面显示「—」，不算内容检查过关）。
- 拆章的 agent 修复预算改成每本 2 次、默认不设整批上限（`--agent-fix-total 0`）；以前是每本 1 次、
  整批 3 次。
- 需要 `--root` 才换根的脚本（`ingest.py`、`ops_pdf_triage.py`、`retire_book.py`、`ops_status.py`）
  一律显式带 `--root <root>`；在真库上等于默认值，在临时根上才指得对。`build_library.py`、
  `gen_book_catalog.py`、`split_md_batch.py`、`pdf_md_batch.py`、收尾那批脚本不认 `--root`，
  所以临时根里放的是 stub，真库上跑的是真脚本。
- PDF 确认不再假定全局 review 目录。`api_pdf_confirm` 先按 uid 找到分诊 job，把它的 `triage.csv` 按 uid 合并进 `pdf-ocr-ops-<日期>/triage.csv`，把 `census.jsonl`、`text.jsonl`/`scan.jsonl` 按 uid 合并进批次目录，再写 `review/<uid>.json`，最后调 `pdf_apply_review.py --triage <批次>/triage.csv --out-dir <批次> --review <批次>/review`。同一天确认多本会累积在同一批次。确认成功后，原来的分诊任务从 `awaiting_review` 回写为 `succeeded`。
- `GET /api/jobs/<id>/log` 回 JSON（`text`/`next`/`size`/`eof`），不是裸文本；契约没定格式。
- `GET /api/import/latest` 只在计划里的 add/replace 源文件还在收件箱时返回。应用之后源文件已经搬进
  书目录，计划自动失效，刷新不会把旧计划拿回来重放。等待任务的页面把 `awaiting_review` 当终态，
  因为进程已经退出，只是产物等人确认。
- 待转 PDF 清单是 PDF 页的子页（`#pdf-list`），故意不进左侧导航：它能靠 `#pdf-list` 直接打开，
  但左侧仍是七个页签，避免把「清单」当成第八个大页面。
- 清单里的页数不在服务端 import pymupdf，走 `工作区/.venv-pdf/bin/python` 的子进程一次批量读，
  进程内按 uid 加文件 mtime 与大小缓存；venv 或文件缺了就显示「—」，不影响其它列。
- `POST /api/pdf/run` 的 `force` 只把只记图册与日文暂缓改判成扫描识别：先写当天批次的 `triage.csv`
  与 `review/<uid>.json`，再跑 `pdf_apply_review.py`，最后才排转 md，人工 route 会短路图片占比规则。
  `--uids` 只在批次还没有 `worklist.tsv` 时收窄范围，同一天二次改判要另开一天批次。
- 「改处置」与「按建议确认」走同一个 `POST /api/pdf/settle`，也是这条路径：先写当天批次的
  `triage.csv` 与 `review/<uid>.json`（不吃 `人工确认`），再跑 `pdf_apply_review.py`。`/api/pdf/run`
  的二次确认改判调的就是这一个函数，行为没变。
- 单本分诊面板从前端删了（`POST /api/pdf/triage` 与 `POST /api/pdf/confirm` 保留，验收与命令行在用）：
  建议不再直接改处置，只在清单里逐行确认。建议的来源只看 `awaiting_review` 与 `succeeded` 两个状态的
  分诊任务，新到旧取第一条命中；单本任务读 `jobs/<id>/result.json`，批量任务读
  `jobs/<id>/<uid>/result.json`。
- 旧批次的一行摘要读 `pdf/queue` 的 `batch.partition`：`landed` 把 landed / skipped-has-md / accepted /
  replaced 算落地，`failed` 是 api-fatal 与 failed，`staged` 是 staged-review，`untouched` 是名单减掉
  三者，`left` 是后三者之和。批次 `landed` 字段照旧不动，两个数字在 replaced 上可能差一本。
- 今天额度只算当天 `<批次>/logs/*.json` 里 `status != skipped-has-md` 的 `pages` 之和，不跨批次累加；
  没开过当天批次就是 0。`cap` 写死 20000。
- 批量分诊卡里的「多少本文本层、多少本扫描件」不是普查账的读数：页数子进程顺手抽首页、中页、末页的文本，
  有字算文本层。没有 `.venv-pdf` 或书读不到时只写本数与总页数，不编分解。
- 队列计数条来自普查账，清单页的四类计数来自待转集与各批次的 ops 分诊表，两个口径不同，数字可以不相等。
- `awaiting_review` 的含义定为「产物等人确认」：扫描与分诊跑完停在这里，确认动作由新的任务接手，
  父任务状态不再回写。
- 状态脚本没就绪时页面显示 0 与「状态：缺」，不假装有数。真库实测字段与 `ops_status.py` 现状一致。
- 无登录、只绑本机。任何远程访问要先加鉴权与 TLS。
- 上传上限 2 GB；更大的文件用文件管理器放进 `库/_收件箱/`。
- 索引重建没有百分比进度，只有日志尾部与耗时。
- 目录名仍是分类表的投影：改分类后要手工 `mv`，页面只提示。
- 读 `目录/library.db` 用 `mode=ro`，WAL 库会顺带留下一只 `library.db-shm`（SQLite 自己重建成，
  不写任何数据）。要连这个也不留，就得开 `immutable=1`，但那样库被重建时可能读到旧的账，不划算。
