# 管理

操作台把书库的维护动作收在六个模块：总览、新书入库、书目与分类、正文生产、同步与维护、任务与日志。
`doc` / `pdf` / `pdf-list` / `manual` / `retire` / `cold` 是兼容子页签（`EXTRA_TABS`），hash 直达仍可用，但不再占左侧主导航。
批次任务走队列，真正干活的是 `src/bookcorpus/scripts/` 下的脚本；账表改行（`category_put`/`category_put_many`）与上传在服务进程内直接写，不走队列。
2026-11-19 起日常入库、改分类、拆章、OCR 与退库会在服务端自动接力增量同步（元数据 → FTS → 向量），页面上的「同步与维护」用于外部命令行改动后的偏差兜底。

## Sub-features

- `ops-overview` 总览：书目数、待分诊、收件箱、体检提醒、一键收尾。
- `ops-import` 新书入库：扫描、计划、执行、上传到 `books/_收件箱/`。
- `ops-split` 正文生产：子页签 `split` 电子书、`docs` 文档（TXT/Office）、`pdf` PDF、`manual` 手工格式（djvu/csv）；`pdf-list` 是 PDF 的隐藏子页签。
- `ops-maintain` 同步与维护：六行库状态（检索索引/浏览入口/体检/书目总表/上次同步）+ 「复核并增量对齐」（5 步，完事 toast「5 步全过」）+ 元数据/FTS/向量/LLM 扩展四行 + 低频重建按钮（压缩向量库/全量重建 index.db/全量重嵌向量/深度体检+四箱验收/清理旧记录）。日常正文落地已自动同步，这页是外部改动与异步失败的兜底。
- `ops-ledger` 书目与分类：子页签 `ledger` 分类与全库台账、`retire` 待退库与删书、`cold` 冷宫（只读专区）。人账 `tables/ledger.db` 的浏览与改行在 `ledger`。
- `ops-retire` 删书：现在是「书目与分类」的子页签，不再是主导航；dry-run 与 apply 两步，apply 侧还有三道闸（uid 复核、`plan_hash`、勾确认）。
- `ops-logs` 任务与日志：待确认的批次、取消与重跑；每个任务一份 log、命令、PID、结果，队列也在这页看。
- `ops-sync` 增量同步：`/api/status` 的 `status.sync_delta` 给出 `fts`（`has_db/has_tracking/added/modified/removed/unchanged/drift_count`）、`embed`（`ok/initialized/engine_ready/added/modified/orphan/chunks/garbage/garbage_ratio/compact_recommended`）、`expand`（`covered_books/uncovered_books`）与 `meta`（`ledger_stale/catalog_missing/views_missing`）。作业种类是 `sync.fts`、`sync.embed`、`sync.reconcile`、`sync.expand`；入库/拆章等作业结束会 `_chain_auto_sync`，向量库未初始化时跳过 embed。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18770/`，标题是「书库操作台」。
- 点左侧竖排导航的六个模块切换（不是顶部横排）：总览 / 新书入库 / 书目与分类 / 正文生产 / 同步与维护 / 任务与日志。
- 模块内的子页签是横排 `.subtabs`；`#retire`、`#cold`、`#docs`、`#pdf`、`#pdf-list`、`#manual` 仍可直接 hash 打开，但主导航里没有。
- 在「新书入库」选文件上传，或在「正文生产」勾几本再执行。
- 在「任务与日志」看待确认的批次，取消或重跑；「同步与维护」点「复核并增量对齐」看 5 步结果。

## Driving it with tests/acceptance/ops/check_ops.py + scripts/drive_ops_tabs.py

Preconditions:

- 工作区按 [README](./README.md) 建好；`check_ops.py` 自己造临时根，不需要样书。
- 操作台起在本轮端口：`BOOKS_ROOT=$ROOT python3 apps/ops/server.py --root $ROOT --port 18770`，启动行是 `统一操作台：http://127.0.0.1:18770  root=<ROOT>`；`--port 0` 会自己挑一个空闲端口并打印实际值。**产品默认端口是 8770**（`apps/ops/server.py:3871`；`:3869` 是 description），18770 只是本技能传 `--port` 的结果；操作员的常驻实例在 8770，不要照抄 18770 去连他。
- CDP 脚本要一个 Chrome 系浏览器：用 `CHROME_BIN` 指定，本机是 `/usr/bin/chromium`（2026-11-20 实测 155.0.8059.84）；`google-chrome-stable` 已不存在。本技能的 `scripts/drive_ops_tabs.py` 读 `CHROME_BIN`，缺省 `/usr/bin/chromium`。
- 只对临时根操作：上传写 `books/_收件箱/`，任务写 `jobs/<id>/`，状态写 `ops.db`。绝不指向 `~/书库`。

- **离线全量。** `python3 tests/acceptance/ops/check_ops.py` 逐项打「过/败」，末两行是 `168 项检查，失败 0 项` 与 `ALL PASS`（项数随功能增加，别把 168 当常量）。
- **页面骨架。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/` 输出 `200`；标题 `书库操作台`；HTML 只有约 900 字节（2026-11-14 为 900），是 JS 壳，页签文字不在里面。
- **接口在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/api/status` 输出 `200`；`/api/jobs`、`/api/inbox`、`/api/pending`、`/api/ledger`、`/api/pdf/books` 都在；`/api/maintain` 只收 POST，GET 是 `404`。
- **状态的形状。** `/api/status` 顶层有 `ok`、`state`（空库里是 `degraded`）、`complete`、`stale`、`missing`、`source`、`error`、`status`、`ops`、`state_counts`、`at`；`status` 下有 `books/md_done/md_chapters/md_indexable`、`pending`、`pending_groups`、`inbox`、`ledger`、`catalog`、`index`、`views`、`audit`、`ocr`、`jobs`、`problems`、`library`、`embed`、`sync_delta`。样书根常见 `state=degraded`、`missing=["catalog"]`、`problems=["没有 build/书目总表.csv"]`；跑完「复核并增量对齐」后 `stale` 转 `false`，`catalog_missing` 仍在。**没有 `status.health`**：健康检查在独立接口 `GET /api/search/health`。8765 没起时它回 `{"ok":true,"running":false,"url":"http://127.0.0.1:8765","pid":null,"meta":null,"error":"[Errno 111] Connection refused"}`；`error` 非空，不是 `null`，`ok` 仍是 `true`（只表示接口答得出，不表示搜索在跑）。8765 起着时 `running:true`，并可能多出 `degraded`/`db`/`message`。`extra_books` 是磁盘有、索引没有的书，别把它读成书目总数。
- **主导航与子页签都点得开。** `python3 .agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py --base http://127.0.0.1:18770 --out "$EV"` 起自己的 headless Chromium，切 `overview/import/ledger/split/maintain/logs` 与 `retire/cold/docs/pdf/pdf-list/manual` 十二个 hash，断言每个有 `<h1>/<h2>`、无横向溢出、无控制台错误，出 `ops-overview-1440x900.png`，末三行 `CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`。`apps/ops/web/app.js:3-10` 的 `TABS` 是六条，`EXTRA_TABS` 是 `docs` / `pdf` / `pdf-list` / `manual` / `retire` / `cold`；`#nav` 只有六条链接。
- **旧的 `shot_ops.py` 已跟不上页面。** `PYTHONPATH=$PWD/tests/acceptance/ops python3 tests/manual/ops/shot_ops.py` 仍按八页签流程点 `retire`，在 1440x900 会死在 `RETIRE_MODAL`：`RuntimeError: … TypeError: Cannot set properties of null (setting 'value')`。这是产品测试漂移（`retire` 现在是「书目与分类」的子页签），不是页面坏；主导航回归改用上面的 `drive_ops_tabs.py`，别再拿 `SHOT PASS` 当判据。
- **上传。** 在「新书导入」选一个文件上传。`curl -s http://127.0.0.1:18770/api/inbox` 里出现该文件，磁盘上落在 `$ROOT/books/_收件箱/`。
- **删书两步。** 先 dry-run 再 apply。`curl -s -X POST http://127.0.0.1:18770/api/delete/dry-run ...` 只出计划，`apply` 才动树；本次未跑（会删临时根里的书）。

## Gotchas

- 页面是 JS 壳，`curl` 拿到的 HTML 只有约 900 字节，搜不到页签文字；页签断言要靠 CDP，不是 `grep`。
- `tests/manual/ops/shot_ops.py` 的裸命令会 `ModuleNotFoundError: No module named 'check_ops'`，必须带 `PYTHONPATH=$PWD/tests/acceptance/ops`；但它现在按旧八页签流程点 `retire`，会死在上面的 `RETIRE_MODAL`，别再把它当主导航回归。
- `google-chrome-stable` 已不在本机；本技能脚本用 `CHROME_BIN`（缺省 `/usr/bin/chromium`）。要显式指定就 `CHROME_BIN=/usr/bin/chromium`，别去装 Chrome。
- `drive_ops_tabs.py` 自己起 Chromium、自己关；失败会留下 `/tmp/ops-tabs-*` 配置目录，验证完清掉。它只断言 heading / 控制台 / 横向溢出，不覆盖逐行改账这类写入动作；账表数据仍验 `/api/ledger`。
- `check_ops.py` 与 `drive_ops_tabs.py` 用的都是临时根；真实队列会调 `src/bookcorpus/scripts/` 里的东西，用临时根跑才安全。
- 点「复核并增量对齐」会在临时根里真跑 5 步（元数据 → FTS → 向量 → 体检等），完事 toast `5 步全过`；它会写 `$ROOT` 的索引与 `工作区/ops`，不要对操作员的 8770 点。
- 直接跑 `tests/acceptance/ops/test_status.py` 或 `test_pdf.py` 时，`REAL_ROOT`/`INSTANCE` 在未设 `BOOKS_ROOT` 时默认 `~/书库`（`test_status.py:22`、`test_pdf.py:26`），会读真实账与索引；要跑就先 `export BOOKS_ROOT=$ROOT`。`test_status.py` 没有 `def test_`，pytest 不会收集它，只能当脚本跑。
- `/api/search/health` 仍写死探测 `http://127.0.0.1:8765`（`apps/ops/server.py` 的 `SEARCH_URL`），不随 `--root` 或 `--port` 变；临时根的 18770 也会探操作员的 8765，返回 `running:true` 是正常的。`/api/search/start` 改走 `app_links.ensure("search", root)`，命令带 `--port 8765 --root $ROOT`，但仍会占默认 8765；本机 8765 被常驻实例占着，这条分支记 `unexercised`，别在临时实例上点启动。
- 上传是唯一往库里写文件的入口，目标固定是 `_收件箱/`；其余动作只改账本与 `jobs/`。
- 同一份工作区同时只能有一个写者（`.writer.lock`）；两个验证进程并行跑会互相等锁，看起来像卡住。
- 用裸 `python3 apps/ops/server.py` 起时没有 `bookcorpus` 可导入，`main()` 里那段 `envfile.load()` 是空操作，仓库 `.env` 的 `BOOKS_ROOT` 也不会生效；本技能因此显式传 `--root`。若改用 `uv run --frozen python` 或设 `PYTHONPATH=src`，`.env` 会被加载，但只影响不传 `--root` 时的默认值；显式 `--root` 仍优先。起服务前显式 `export BOOKS_ROOT=$ROOT`，不要改仓库 `.env`。
- `--port 0` 时实际端口只在启动行里，不要假设 8770。
