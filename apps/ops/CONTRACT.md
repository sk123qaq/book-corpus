# 统一操作台实现契约（2026-10-24）

先读 `docs/design/统一操作台-方案-2026-10-24.md`。本文件只定接口、文件归属与测试边界。方案与本文件冲突时，以本文件为准，并在报告里指出。

## 1. 文件归属

并行施工，每个 agent 只改自己名下的文件。

| agent | 只改 |
|---|---|
| status | `src/bookcorpus/scripts/ops_status.py`（新）、`src/bookcorpus/scripts/audit_state.py`、`tests/acceptance/ops/test_status.py`（新） |
| classify | `src/bookcorpus/scripts/classify_model.py`（新）、`src/bookcorpus/scripts/ingest.py`、`src/bookcorpus/scripts/classify_books.py`、`tests/acceptance/ops/test_classify.py`（新） |
| split | `src/bookcorpus/scripts/split_md_batch.py`、`tests/acceptance/ops/test_split.py`（新） |
| pdf | `src/bookcorpus/scripts/ops_pdf_triage.py`（新）、`src/bookcorpus/scripts/pdf_apply_review.py`、`src/bookcorpus/scripts/pdf_md_batch.py`、`tests/acceptance/ops/test_pdf.py`（新） |
| retire | `src/bookcorpus/scripts/retire_book.py`（新）、`tests/acceptance/ops/test_retire.py`（新） |
| console | `apps/ops/server.py`（新）、`apps/ops/web/`、`apps/ops/README.md`、`tests/acceptance/ops/check_ops.py`（新）、`tests/manual/ops/shot_ops.py`（新） |

不改 `src/bookcorpus/scripts/` 里别人名下的文件。不改 `lib_common.py`、`qsearch.py`、`book_query.py`、`build_*.py`、`gen_book_catalog.py`。不改 `库/`、`目录/`、`视图/` 的任何真实文件。不改 `apps/demo/`。

备份在 `工作区/运维/backup-2026-10-24/`，只读。

## 2. 禁止

- 不跑真实入库、真实拆章、真实 OCR、真实删书。测试用 `--root` 临时根或 fixture。
- 不调真实模型。测试用 `PI_BIN` 指向 stub，stub 回固定 JSON。
- 不写 `目录/分类表.csv`、`目录/library.db`、`book.json`、`目录/检索/index.db`。
- 不重建真实索引。测试只检查命令行与 JSON，不跑 6.69 GB 重建。
- 不启动 8770 以外的常驻服务。测试服务用随机端口，结束就关。
- 不删 `工作区/运维/backup-2026-10-24/`。

## 3. ops.db

路径 `工作区/运维/ops.db`。

```sql
CREATE TABLE IF NOT EXISTS job (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  argv TEXT NOT NULL,
  state TEXT NOT NULL,
  pid INTEGER,
  created TEXT NOT NULL,
  started TEXT,
  ended TEXT,
  exit_code INTEGER,
  summary TEXT,
  log_path TEXT,
  batch_dir TEXT,
  plan_hash TEXT
);
CREATE TABLE IF NOT EXISTS step (
  job_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT,
  started TEXT,
  ended TEXT,
  PRIMARY KEY (job_id, seq)
);
```

任务状态：`queued`、`running`、`awaiting_review`、`succeeded`、`failed`、`cancelled`、`orphaned`。

成功判据：脚本输出门槛，不是退出码 0。导入看 `conflicts=0` 与 `未匹配 0 条`，拆章看 `--report-json` 的
`pending_review`（非空就停在 `awaiting_review`），查看问题看 `result.json` 里有这本书的记录，收尾看
FAIL 0 与全过。`split.inspect` 是只读任务，不进写任务互斥。

## 4. HTTP

只绑 `127.0.0.1:8770`。POST JSON，返回 `{ok, job_id}` 或 `{ok:false, error}`。

```text
GET  /api/status
GET  /api/jobs?state=...
GET  /api/jobs/<id>
GET  /api/jobs/<id>/log?offset=...
POST /api/import/upload            裸 body，X-Filename
POST /api/import/scan
POST /api/import/apply             {plan_path}
POST /api/split/run                {uids, workers, replace} 或 {only, batch}（单本路径带 --force）
POST /api/split/recheck            {uid, batch}：不重拆，只复核暂存区已有产物
POST /api/split/inspect            {uid, batch}：只读，出这本书的问题清单
POST /api/pdf/triage               {uid}
POST /api/pdf/triage-batch         {uids?}：不给就分诊所有未分诊的书，一个任务 N 步
POST /api/pdf/settle               {items:[{uid, route}]}：改处置，只写台账不出正文
POST /api/pdf/confirm              {uid, route, reason, model}
POST /api/pdf/run                  {uids, limit, workers, force}
POST /api/docs/run                  {uids?}：不给就补转全库；docx/xlsx/xls/pptx/txt → md
GET  /api/docs/report[?job_id=]     最近一次 docs.run 的 progress 与 report（没跑过 404）
POST /api/maintain                 {steps:[...]}
POST /api/delete/dry-run           {uid, reason}
POST /api/delete/apply             {uid, reason, plan_hash, confirm_uid}
GET  /api/search/health
POST /api/search/start
GET  /api/file?path=...
GET  /api/ledger                        # tables/ledger.db 全量：ledger/concepts/broad/actions + ledger_db 路径
POST /api/ledger/row                    # {row} upsert 人账一行；新 uid 拒收；写锁 .writer.lock
POST /api/ledger/rows/delete            # {uids} 删人账行（不动机书目录）
POST /api/ledger/rows/confirm            # {uids} 备注里的「待复核」换成「分类确认 日期」，其余字不动；已确认/不在账上的行进 skipped
POST /api/ledger/concept                # {which: main|broad|action, row} upsert 词表条目
POST /api/ledger/concept/delete         # {which, key} 删词表条目
GET  /api/pdf/books
```

`POST /api/pdf/run` 的 `force`：选中的书里含存档书（gallery/ja，显示名存档·图册/存档·非中英文）时必须为 true，服务端先把它们改判成
`OCR`（写批次 `triage.csv` 与 `review/<uid>.json`，跑 `pdf_apply_review.py`）再转；未分诊的书只投
`pdf.triage`，不出正文，只选了未分诊时返回 `job_id: null`。`GET /api/pdf/books` 是清单页的数据源，
返回 `{books, counts:{convert, gallery, ja, untriaged}, today:{used, cap}, untriaged}`，只读，
不写任何账；`books` 的行带 `disposition`/`progress`/`pages`/`why`/`last`/`recheck`，未分诊的行另带
`suggestion:{route, confidence, reason}` 与 `suggest_job`。`GET /api/pdf/queue` 的 `batch` 另带
`partition:{landed, failed, staged, untouched, left}`，给旧批次的一行摘要用。

`POST /api/pdf/settle` 只收 `OCR`、`本地抽文本`、`不转-记图册`、`日文暂缓`（`人工确认` 不是一个处置，回
400）。它复用二次确认改判那一段：备一行分诊行（分诊产物 → 批次账 → DB 现造）写进当天批次的
`triage.csv`，写 `review/<uid>.json`，再排一步 `pdf_apply_review.py`；返回
`{ok, job_id, batch, routes:{route: n}}`。`POST /api/pdf/triage-batch` 起一个 `pdf.triage` 任务，
标题 `批量分诊 N 本`，每本一步，输出落 `jobs/<id>/<uid>/`，终态 `awaiting_review`，摘要写本数与建议数。
不给 `uids` 时只分诊还没出建议的书（已有建议的跳过，模型不重复烧）。
页面不再用单本分诊面板，`/api/pdf/triage` 与 `/api/pdf/confirm` 留着给验收与命令行。

`POST /api/docs/run` 是写任务（进写锁），不给 `uids` 就是全库补转：扫书库里所有 doc 格式的书，
`docx/xlsx/xls/pptx/txt` 转成 md，源文件已经是 md 的原样复制，已有最新 md 的跳过。
`import.apply` 成功后自动接一个 `docs.run`，它的任务 id 在 `GET /api/jobs/<id>` 的 `docs_job` 字段；
前端导入页据此把「文档转 md」卡片切到这个任务。`GET /api/docs/report` 不给 `job_id` 取最近一次
`docs.run`，没跑过 404；返回 `{ok, job_id, state, progress:{phase, done, total, current, updated_at},
report:{summary:{total, done, skipped, failed}, scope:{new, backfill}, items:[...]}}`，`report` 只有跑完才有。

文件白名单：`工作区/运维/jobs/<id>/` 下的日志与缩略图，`目录/书目总表.xlsx`、`目录/书目总表.csv`、`目录/分类预填.csv`。其它 404。

## 5. ops_status JSON

`ops_status.py --json` 至少给这些字段：

```json
{
  "at": "...",
  "books": 1693,
  "md_done": 1571,
  "pending": {"pdf": 115, "txt": 5, "djvu": 2},
  "inbox": {"files": 3, "items": []},
  "ledger": {"rows": 1693, "mtime": "..."},
  "library": {"db": "...", "ok": true},
  "catalog": {"mtime": "...", "unmatched": 0, "ok": true},
  "index": {"built_at": "...", "paras": 2593173, "chapters": 87644,
            "books": 1693, "lag_books": 0, "lag_chapters": 0,
            "extra_books": 0, "extra_chapters": 0},
  "views": {"awards": 1009, "topics": 1034, "broken": 0, "ok": true},
  "audit": {"fail": 0, "warn": 3, "items": [], "at": "...", "ran": true, "stale": false},
  "ocr": {"remaining_books": 0, "remaining_pages": 0, "quota": "20000/天"},
  "jobs": {"running": 0, "awaiting_review": 0, "last": null}
}
```

`--root` 指向临时库时所有路径跟着换。`--deep` 额外跑审计并写 `工作区/运维/last-audit.json`。

`GET /api/status` 在三态契约上再包一层：

- `state`：`ok` 表示读数完整、没有 problems、且体检已跑；`degraded` 表示读到了，但缺核心库、有 problems，或 `audit.at` 为空；`unknown` 表示状态脚本没跑成或 payload 不完整，页面上的数字都是占位。
- `complete`：`source` 是 `script` 或 `fixture` 时为 true；`stale`：`audit.at` 为空，或 `audit.stale` 为 true；`missing`：`library`/`catalog`/`index`/`views` 里 `ok:false` 的段名。
- 各段带 `ok`，`audit` 另带 `ran` 与 `stale`。`ran` 表示跑过；`stale` 表示跑过但之后 `build/library.db`、`build/检索/index.db` 或 `build/书目总表.csv` 又变了，旧结果不能担保现在，`problems` 会点名。`ok`/`error` 仍是「脚本进程有没有出错」的旧语义，旧消费者不受影响。
- `index.extra_books`/`extra_chapters` 是索引比书库多出的量；非 0 时 `problems` 记「需重建」，页面显示「比书库多 N 本」，不再显示负的「落后」。
- 页面只在 `state:"ok"` 时使用「库是干净的」「索引已同步」「全部通过」；`degraded` 显示「状态不完整」「未运行」「未知」，`unknown` 显示「读不到」；某个段 `ok:false` 时该段计数显示 `—`，`problems` 原文作为 fail 行列出。

## 6. 脚本接口

### ingest.py

- 计划 JSON 字段不变。页面只改 `title`、`subjects`、`retention`、`action`、`uid`。
- 分类走 `classify_model.classify(titles, authors, model, timeout)`。
- 缓存条目加 `model`、`prompt_version`，旧条目仍可读。
- `--no-classify` 帮助文字改成“跳过 deepseek 分类预填”。

`classify_model.classify` 返回：

```json
[{"level1":"文学","level2":"类型小说","confidence":0.93,"reason":"..."}]
```

### classify_books.py

- `--backend model|typesafe`，默认 `model`。
- `--backend model` 一次给两级，不读 TypeSafe。
- `--merge` 已有行一个字段都不动。`--add-new` 只追加人账没有的 uid。
- `分类预填.csv` 写 `一级模型`、`二级模型`、`模型置信`、`模型依据`。
- 缓存每条带 `backend`、`model`、`prompt_version`。

### split_md_batch.py

- `--uids B001,B002`：新批次且 worklist 不存在时只写这些 uid。
- `--report-json`：stdout 最后一行是 JSON，含 `status_counts`、`books`、`batch`、`pending_review`。
  `pending_review` 是页面门槛的唯一依据；驱动里的状态表 `STATUS` 决定哪些算完成。
- `--recheck`：不重拆，只跑 verify 与 audit、重写 `.manifest.json`，过了就进库。与 `--force` 互斥。
- 不新增 `--archive-old`。现有 `land()` 已把旧 md 改名到 `<stage>.prev-<stamp>` 并把路径写进 `result["previous"]`。`--replace` 与 `--force` 沿用现有行为，页面读 `previous`。
- `logs/<uid>.json` 不改字段；页面读现有 `status`、`verify_rc`、`audit_rc`、`audit_ratio`、`fix`、`previous`、`dest`。

### pdf_md_batch.py

- `--uids B001,B002`：新批次且 worklist 不存在时只写这些 uid。批次已有 `worklist.tsv` 时 `--uids` 不生效。
- `--triage <path>`：可选，指向新批次的 `triage_final.csv`。不传时仍用全局 `triage_final.csv`。
- `--report-json`：stdout 最后一行是 JSON，含 `route`、`queued`、`landed`、`remaining_books`、`remaining_pages`、`status_counts`。
- 不改旧批次任何文件。
- 同目录有 `skip_list.tsv` 时优先用它，否则用全局那份；`--uids` 里的 uid 在 skip 表里则直接退出。
  所以 `api_pdf_run` 二次确认改判的书要先过 `pdf_apply_review.py` 改写出新 `triage_final.csv`
  与 `skip_list.tsv`，再跑转 md。

### pdf_apply_review.py

- 读 `review/<uid>.json` 的 `model` 对象。
- 写 `triage_final.csv` 四列：`model_route`、`model_confidence`、`model_reason`、`model_at`。
- `route` 只由现有 verdict 逻辑决定，模型不改 `route`。
- `skip_list.tsv` 列不变。

review JSON 形状：

```json
{"uid":"B1234","verdict":"文字书","evidence":"...","route":"OCR","reason":"人工理由",
 "model":{"route":"OCR","confidence":0.86,"reason":"...","at":"..."}}
```

### ops_pdf_triage.py

```text
python3 ops_pdf_triage.py --uid B1234 --out 工作区/运维/jobs/<id> [--root .] [--no-model]
```

输出 `result.json`：

```json
{"uid":"B1234","title":"...","path":"...","pages":320,"class":"C 扫描件(无文本层)",
 "metrics":{"text_page_share":0.0,"ink":0.42,"bands":2,"image_share":1.0},
 "thumbs":["thumbs/001.png","thumbs/160.png","thumbs/320.png"],
 "model":{"route":"不转-记图册","confidence":0.9,"reason":"..."},
 "prompt_hash":"..."}
```

`--no-model` 时 `model` 为 null。模型调用：

```text
pi -p --no-tools --no-session --no-context-files --no-skills --no-extensions \
  --model opencode-go/deepseek-v4.1-flash "@001.png" "@160.png" "@320.png" "<prompt>"
```

### retire_book.py

```text
python3 src/bookcorpus/scripts/retire_book.py --uid B1234 --reason "..." --dry-run --json [--root .]
python3 src/bookcorpus/scripts/retire_book.py --uid B1234 --reason "..." --plan-hash <sha> --apply [--root .]
```

dry-run JSON 至少含：

```json
{"uid":"B1234","title":"...","path":"...","book_dir":"库/...",
 "sources":[{"path":"...","sha256":"...","bytes":0,"shared_with":0,"action":"move|hardlink"}],
 "delete":["md/","html/","book.json"],"ledger":true,"db":true,
 "awards":[],"views":[],"series_docs":[],"md_chapters":0,"md_bytes":0,
 "expand_rows":0,"index_rows":0,"cold_path":"库/_冷宫/.../source/...",
 "snapshot":"../books.snapshot-2026-10-24","snapshot_ok":true,"plan_hash":"..."}
```

`--apply` 重算预览，`plan_hash` 不一致拒绝。快照门槛默认开。共享源 `os.link`，独占源移动。冷宫只留 `source/`，不写 json、md、html。追加 `工作区/批次/移出-<日期>/retired.tsv` 与 `库/_冷宫/README.md`。

## 7. 测试

- 每个脚本的测试放自己名下的 `tests/acceptance/ops/test_*.py`。
- 临时根用 `tempfile.mkdtemp()`，必要时造最小的 `目录/`、`库/`、`工作区/`。
- 模型调用一律 `PI_BIN` stub。stub 是 `tests/acceptance/fixtures/pi_stub.py`，只回预先写好的 JSON，不联网。
- 测试不写真实库。测试结束删临时根。
- 报告：改了哪些文件、跑了哪些命令、结果、与契约的偏差、已知风险。

## 8. 控制台

`server.py` 用标准库 `ThreadingHTTPServer`，零依赖。`web/` 用原生 JS。样式参考 `apps/demo/web/style.css` 的纸色与朱红，流程轨参考 `docs/design/流水线全景-2026-10-23.html` 的阶段与状态词。

页面七块：总览、新书导入、电子书出正文、PDF 出正文、维护、删书、日志。顶栏有搜索入口与 8765 健康点。
PDF 页是三块：四格状态带（可转 / 未分诊 / 存档·图册 / 存档·非中英文，各带本数、页数与一句说明）加一行
「今天额度」与旧批次摘要，再加两张动作卡（批量分诊、确认后转 md），最后是最近的分诊与转 md。
另有 PDF 的子页待转 PDF 清单（`#pdf-list`），不进左侧导航，由 PDF 页的入口与状态带进入，
列出四类处置，带两行筛选（处置 / 进度 / 只看复看 / 一级分类 / 关键字 / 排序）、建议与行内改处置、勾选与转写入口。

服务启动时接管能核对 PID 的 `running` 任务，核对不上标 `orphaned`，不杀进程。

写任务互斥，读任务可并行且与写任务互不阻塞。写任务与批次账读改写用 `工作区/运维/.writer.lock` 的
`fcntl.flock(LOCK_EX)`；只读任务用 `工作区/运维/.reader.lock` 的 `fcntl.flock(LOCK_SH)`，只写自己的 `jobs/<id>/`。

上传裸 body，上限 2 GB，写 `库/_收件箱/<name>.part` 后 `os.replace`。文件名去路径。
只收 `.epub .pdf .mobi .azw3 .azw .txt .md .docx .xlsx .xls .pptx`，其余格式 400 + 中文原因。

`check_ops.py` 用随机端口与临时根，测 `/api/status`、任务生命周期、文件白名单、路径穿越拒绝、stub 导入与分诊解析；PDF 段另测批量分诊的步数与输出目录、建议与出建议的任务、改处置与按建议确认的 400 与写盘结果、今天额度。末行 `ALL PASS`。

`shot_ops.py` 用 CDP，1440×900 与 390×844，报 `CONSOLE ERROR n` 与 `overflow=`。PDF 页另查：没有编号
输入框、有批量分诊按钮与旧批次摘要、有去清单的入口，清单页能打开、有两行筛选（进度三个勾与只看复看）、
行末有「改处置」或「按建议确认」、改处置弹窗里四个处置，切页后勾选与关键字不丢，390 宽不横向溢出。
