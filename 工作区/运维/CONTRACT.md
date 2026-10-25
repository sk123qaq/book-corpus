# 统一操作台实现契约（2026-10-24）

先读 `工作区/方案/统一操作台-方案-2026-10-24.md`。本文件只定接口、文件归属与测试边界。方案与本文件冲突时，以本文件为准，并在报告里指出。

## 1. 文件归属

并行施工，每个 agent 只改自己名下的文件。

| agent | 只改 |
|---|---|
| status | `工作区/脚本/ops_status.py`（新）、`工作区/脚本/audit_state.py`、`工作区/运维/验收/test_status.py`（新） |
| classify | `工作区/脚本/classify_model.py`（新）、`工作区/脚本/ingest.py`、`工作区/脚本/classify_books.py`、`工作区/运维/验收/test_classify.py`（新） |
| split | `工作区/脚本/split_md_batch.py`、`工作区/运维/验收/test_split.py`（新） |
| pdf | `工作区/脚本/ops_pdf_triage.py`（新）、`工作区/脚本/pdf_apply_review.py`、`工作区/脚本/pdf_md_batch.py`、`工作区/运维/验收/test_pdf.py`（新） |
| retire | `工作区/脚本/retire_book.py`（新）、`工作区/运维/验收/test_retire.py`（新） |
| console | `工作区/运维/server.py`（新）、`工作区/运维/web/`、`工作区/运维/README.md`、`工作区/运维/验收/check_ops.py`（新）、`工作区/运维/验收/shot_ops.py`（新） |

不改 `工作区/脚本/` 里别人名下的文件。不改 `lib_common.py`、`qsearch.py`、`book_query.py`、`build_*.py`、`gen_book_catalog.py`。不改 `库/`、`目录/`、`视图/` 的任何真实文件。不改 `工作区/演示-检索流程/`。

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

成功判据：脚本输出门槛，不是退出码 0。导入看 `conflicts=0` 与 `未匹配 0 条`，拆章看 verify 与 audit，收尾看 FAIL 0 与全过。

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
POST /api/split/run                {uids, workers, replace}
POST /api/pdf/triage               {uid}
POST /api/pdf/confirm              {uid, route, reason, model}
POST /api/pdf/run                  {uids, limit, workers}
POST /api/maintain                 {steps:[...]}
POST /api/delete/dry-run           {uid, reason}
POST /api/delete/apply             {uid, reason, plan_hash, confirm_uid}
GET  /api/search/health
POST /api/search/start
GET  /api/file?path=...
```

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
  "catalog": {"mtime": "...", "unmatched": 0},
  "index": {"built_at": "...", "paras": 2593173, "chapters": 87644,
            "books": 1693, "lag_books": 0, "lag_chapters": 0},
  "views": {"awards": 1009, "topics": 1034, "broken": 0},
  "audit": {"fail": 0, "warn": 3, "items": []},
  "ocr": {"remaining_books": 0, "remaining_pages": 0, "quota": "20000/天"},
  "jobs": {"running": 0, "awaiting_review": 0, "last": null}
}
```

`--root` 指向临时库时所有路径跟着换。`--deep` 额外跑审计并写 `工作区/运维/last-audit.json`。

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
- `--report-json`：stdout 最后一行是 JSON，含 `status_counts`、`books`、`batch`。
- 不新增 `--archive-old`。现有 `land()` 已把旧 md 改名到 `<stage>.prev-<stamp>` 并把路径写进 `result["previous"]`。`--replace` 与 `--force` 沿用现有行为，页面读 `previous`。
- `logs/<uid>.json` 不改字段；页面读现有 `status`、`verify_rc`、`audit_rc`、`audit_ratio`、`fix`、`previous`、`dest`。

### pdf_md_batch.py

- `--uids B001,B002`：新批次且 worklist 不存在时只写这些 uid。
- `--triage <path>`：可选，指向新批次的 `triage_final.csv`。不传时仍用全局 `triage_final.csv`。
- `--report-json`：stdout 最后一行是 JSON，含 `route`、`queued`、`landed`、`remaining_books`、`remaining_pages`、`status_counts`。
- 不改旧批次任何文件。

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
python3 工作区/脚本/retire_book.py --uid B1234 --reason "..." --dry-run --json [--root .]
python3 工作区/脚本/retire_book.py --uid B1234 --reason "..." --plan-hash <sha> --apply [--root .]
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

- 每个脚本的测试放自己名下的 `工作区/运维/验收/test_*.py`。
- 临时根用 `tempfile.mkdtemp()`，必要时造最小的 `目录/`、`库/`、`工作区/`。
- 模型调用一律 `PI_BIN` stub。stub 是 `工作区/运维/验收/fixtures/pi_stub.py`，只回预先写好的 JSON，不联网。
- 测试不写真实库。测试结束删临时根。
- 报告：改了哪些文件、跑了哪些命令、结果、与契约的偏差、已知风险。

## 8. 控制台

`server.py` 用标准库 `ThreadingHTTPServer`，零依赖。`web/` 用原生 JS。样式参考 `工作区/演示-检索流程/web/style.css` 的纸色与朱红，流程轨参考 `工作区/方案/流水线全景-2026-10-23.html` 的阶段与状态词。

页面七块：总览、新书导入、电子书出正文、PDF 出正文、维护、删书、日志。顶栏有搜索入口与 8765 健康点。

服务启动时接管能核对 PID 的 `running` 任务，核对不上标 `orphaned`，不杀进程。

写任务互斥。读任务可并行。用 `工作区/运维/.writer.lock` 的 `fcntl.flock`。

上传裸 body，上限 2 GB，写 `库/_收件箱/<name>.part` 后 `os.replace`。文件名去路径。

`check_ops.py` 用随机端口与临时根，测 `/api/status`、任务生命周期、文件白名单、路径穿越拒绝、stub 导入与分诊解析。末行 `ALL PASS`。

`shot_ops.py` 用 CDP，1440×900 与 390×844，报 `CONSOLE ERROR n` 与 `overflow=`。
