# 管理

失效源：apps/ops/** src/bookcorpus/scripts/ops_status.py

操作台把书库的维护动作收在七个模块：总览、新书入库、书目与分类、正文生产、同步与维护、任务与日志、移动互联。
`docs` / `pdf` / `pdf-list` / `manual` / `retire` / `cold` 是兼容子页签（`EXTRA_TABS`），hash 直达仍可用，但不再占左侧主导航。
批次任务走队列，真正干活的是 `src/bookcorpus/scripts/` 下的脚本；账表改行（`category_put`/`category_put_many`）与上传在服务进程内直接写，不走队列。移动互联（`/api/mobile/*`）仅操作 `工作区/运维/mobile_gateway.*`、`工作区/阅读/mobile_outbox.json`、`工作区/阅读/mobile_inbox/` 与 `books/_收件箱/`，不入 `WRITE_ROUTES` 也不触碰 `.writer.lock`。
2026-11-19 起日常入库、改分类、拆章、OCR 与退库会在服务端自动接力增量同步（元数据 → FTS → 向量），并在后台自动刷新 `last-audit.json` 体检缓存，同步完成后「体检提醒」不留过期提示。
仅真正需要人工裁决的任务（暂存区待复核书目、服务重启接管导致退出码未知）停在 `awaiting_review`；扫描计划与分诊建议直接落 `succeeded`。

## Sub-features

- `ops-overview` 总览：三大精简区块——纯展示台账首卡（核心判断、全文索引与政策存档/无正文绘本口径、三色可读率条）、一体式六环节工序轨道板与底沿联动提醒条（无待处理项时自动收起，有待确认任务时与 `#logs?state=awaiting_review` 联动跳转）、以及隐藏端口号的书库应用入口（含第 5 张「移动网关」状态卡，点击直达 `#mobile`）；取消「下一步待办」卡并统一术语为「正文生产」「待转」「已有正文/可读」「政策存档」；去掉重复「最近任务」卡。顶栏右侧放「统计于 HH:MM」、刷新状态 `#btn-status-refresh` 与单颗宣纸/墨夜切换 `#theme-toggle`（移出侧栏底与移动端双份开关）。
- `ops-import` 新书入库：自上而下全宽流（投递新书条 → 收件箱清单 → 入库核对清单），去掉底部文档转换卡。
- `ops-split` 正文生产：子页签 `split` 电子书、`docs` 文档转正文（四格状态卡与最近一批折叠明细）、`pdf` PDF 分诊与转写（四格可点筛查、今日额度、批量分诊与待转清单）、`manual` 手工格式（djvu/csv）；`pdf-list` 是 PDF 的完整多维筛选子页签。
- `ops-maintain` 同步与维护：四格同步健康总览（书目与视图 / 全文检索 / 语义检索 / 体检状态）+ 一键对齐按钮（`sync_delta.has_drift` 为真时文案为「补齐偏差（增量同步）」，无偏差时为「复核并增量对齐」；未建向量库时跑 4 步：`db`/`awards`/`fts sync`/`audit`，向量库已有块/孤儿或 `force_embed` 时含 `embed sync` 共 5 步）+ 折叠 `#status-issues` 体检提醒 + 章节主题增强 + 导出下载 + 低频高级运维重建；去掉「最近的同步与维护记录」卡。
- `ops-ledger` 书目与分类：子页签 `ledger` 全库书目台账（两行工具栏，去除路径文字，默认分批渲染前 100 行并带 `#ledger-q` 80ms 防抖筛选、`全部书目 / 只看待复核` 分段切换、`加载更多 100 行`/`显示全部`）、`retire` 待退库与删书、`cold` 退库台账与冷宫实物核验（表格带「实物状态」列：在库 / 缺失 / 未登记，页面「刷新」会按 `books/_冷宫` 实际文件校正台账）。
- `ops-retire` 删书：现在是「书目与分类」的子页签，不再是主导航；dry-run 与 apply 两步（原生 `<dialog>` 弹窗），apply 侧还有三道闸（uid 复核、`plan_hash`、勾确认）。
- `ops-logs` 任务与日志：全站唯一查看排队/待确认/已完成批次的中心，支持快速分段筛选与 `#logs?state=awaiting_review` 过滤直达并自动展开首条待确认任务详情；从各模块或「任务与日志」表格行点击「详情」打开 `#job-detail`、高亮当前行（`tr.active`）并滚动定位到 `#job-detail`；待确认任务行提供「去处理」及详情页 `#job-review-box`（「去正文生产处理」/「去 PDF 清单处理」+ 备注输入 `#job-resolve-note` + 「确认已处理 / 标为失败 / 忽略关闭」，调用 `POST /api/jobs/<id>/resolve`），重跑与日志下载；只有 `queued`/`running` 可取消（`awaiting_review` 取消回 409）。
- `ops-mobile` 移动互联：独立主导航 `#mobile`，管理移动网关（默认端口 `8780`，可用 `BOOK_CORPUS_MOBILE_PORT` 注入），提供启停/重启、局域网/Tailscale 对外地址选择、离线白底 SVG 二维码（BanRead 配对码 `bc-pair://v1` 与手机浏览器 `/m` 快传码切换）、密钥轮换原生 `<dialog>` 二次确认、推书队列（Outbox）与 `.bcbook` 下载、多端阅读进度分片总览、拉取手机 BanRead 局域网分享（9721 + 6 位配对码）以及手机收件暂存区一键投递到收件箱。
- `ops-sync` 增量同步：`/api/status` 的 `status.sync_delta` 给出 `fts`（`has_db/has_tracking/added/modified/removed/unchanged/drift_count`）、`embed`（`ok/initialized/engine_ready/added/modified/orphan/chunks/garbage/garbage_ratio/compact_recommended`）、`expand`（`covered_books/uncovered_books`）与 `meta`（`ledger_stale/catalog_missing/views_missing`）。作业种类是 `sync.fts`、`sync.embed`、`sync.reconcile`、`sync.expand`；入库/拆章等作业结束会 `_chain_auto_sync`，向量库未初始化时跳过 embed。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18770/`，标题是「书库操作台」。
- 点左侧竖排导航的七个模块切换（不是顶部横排）：总览 / 新书入库 / 书目与分类 / 正文生产 / 同步与维护 / 任务与日志 / 移动互联。
- 模块内的子页签是横排 `.subtabs`；`#retire`、`#cold`、`#docs`、`#pdf`、`#pdf-list`、`#manual` 仍可直接 hash 打开，但主导航里没有。
- 在「新书入库」选文件上传，或在「正文生产」勾几本再执行。
- 在「任务与日志」查看待确认批次、人工收口或停止运行中任务；「同步与维护」点「复核并增量对齐」/「补齐偏差（增量同步）」看 4 步（无向量库）或 5 步结果。

## Driving it with tests/acceptance/ops/check_ops.py + scripts/drive_ops_tabs.py

Preconditions:

- 工作区按 [README](./README.md) 建好；`check_ops.py` 自己造临时根，不需要样书。
- 操作台起在本轮端口：`BOOKS_ROOT=$ROOT uv run --frozen python apps/ops/server.py --root $ROOT --port 18770`，启动行是 `统一操作台：http://127.0.0.1:18770  root=<ROOT>`；`--port 0` 会自己挑一个空闲端口并打印实际值。**产品默认端口是 8770**（`apps/ops/server.py` 里 `--port` 参数写的是 `default=8770`，模块 docstring 与 argparse description 也标着 8770；这里按符号指路、不写行号，行号会随文件增长漂移），18770 只是本技能传 `--port` 的结果；操作员的常驻实例在 8770，不要照抄 18770 去连他。
- CDP 脚本要一个 Chrome 系浏览器：用 `CHROME_BIN` 指定，本机是 `/usr/bin/chromium`（2026-11-20 实测 155.0.8059.84）；`google-chrome-stable` 已不存在。本技能的 `scripts/drive_ops_tabs.py` 读 `CHROME_BIN`，缺省 `/usr/bin/chromium`。
- 只对临时根操作：上传写 `books/_收件箱/`，任务写 `jobs/<id>/`，状态写 `ops.db`。绝不指向 `~/书库`。

- **离线全量。** `uv run --frozen python tests/acceptance/ops/check_ops.py` 逐项打「过/败」，末两行是 `179 项检查，失败 0 项` 与 `ALL PASS`（项数随功能增加，别把 179 当常量）。
- **搜索入口探活不依赖常驻 8765。** 两条命令就是完整证据：`uv run --frozen python tests/acceptance/ops/check_ops.py` 跑 `search_checks()` 的逐项检查，`uv run --frozen pytest tests/test_ops_search_inject.py -q` 锁两个注入点的优先级。`search_checks()` 每轮自建假 search 服务与假演示脚本：`BOOK_CORPUS_SEARCH_URL` 指向已在跑的假服务时 `/api/search/health` 回注入的 `url`、`running:true`、`meta.fake=search`，`POST /api/search/start` 回 `already:true`；停掉假服务后 `health` 回 `running:false` 与非空 `error`，此时 `start` 拉起 `BOOK_CORPUS_DEMO_SERVER` 的脚本（argv 是 `--port <注入 URL 的端口> --root <root>`，健康探通后写 `工作区/运维/search.pid`）；脚本路径不存在时 `start` 回 `ok:false` 与 `error="演示页脚本不存在：<相对路径>"`，不写 pid。构造参数 `Env(search_url=..., demo_server=...)` 优先于两个环境变量，环境变量优先于默认的本机 `http://127.0.0.1:8765` 与 `apps/demo/server.py`。本机没有常驻 8765 时照此手动复核：起操作台前同时导出 `BOOK_CORPUS_SEARCH_URL=http://127.0.0.1:<空闲端口>` 与 `BOOK_CORPUS_DEMO_SERVER=<不存在的路径>`，`/api/search/health` 的 `url` 就是注入值，「启动」得到上面那条报错；要验成功拉起，先重启操作台，再把 `check_ops.py` 里的 `FAKE_DEMO_SERVER` 写进 `$ROOT` 并令 `BOOK_CORPUS_DEMO_SERVER` 指向它（假脚本要认 `--port`/`--root` 并答 `GET /api/health`）。
- **页面骨架。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/` 输出 `200`；标题 `书库操作台`；HTML 只有约 900 字节（磁盘 890 字节，注入 `__V__` 后响应 900 字节），是 JS 壳，页签文字不在里面。
- **接口在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/api/status` 输出 `200`；`/api/jobs`、`/api/inbox`、`/api/pending`、`/api/ledger`、`/api/pdf/books` 都在；`/api/maintain` 只收 POST，GET 是 `404`。
- **状态的形状与 SWR 缓存。** `/api/status` 顶层有 `ok`、`state`（空库里是 `degraded`）、`complete`、`stale`、`missing`、`source`（用包内脚本时进程内直采仍记 `"script"`）、`error`、`status`、`ops`（含 `writer/readers/active/audit_refreshing`）、`state_counts`、`at`；`status` 下有 `books/md_done/md_chapters/md_indexable/books_readable/stats_chapters/stats_chars/stats_built_at`、`md_canonical`（含 `total/canonical/pending/coverage_pct/batches` 规范化覆盖概览）、`pending`、`pending_groups`、`inbox`、`ledger`、`catalog`、`index`、`views`、`audit`、`ocr`、`jobs`、`problems`、`library`、`embed`、`sync_delta`。`STATUS_TTL=15s`、`STATUS_SWR_TTL=60s`：15 秒内重复 `GET /api/status` 复用缓存（顶层 `at` 不变），`?refresh=1` 绕过缓存强制重算（新 `at`）。样书根常见 `state=degraded`、`missing=["catalog"]`、`problems=["没有 build/书目总表.csv"]`；跑完「复核并增量对齐」或自动刷新后 `stale` 转 `false`，`catalog_missing` 仍在。四个计数键 `books` 藏书 / `books_readable` 可读 / `stats_chapters` 章 / `stats_chars` 字 + `stats_built_at` 统计于来自统一快照（`corpus_snapshot.py`），8765 / 8771 同源同值；可读不含 0 章 md（图片书），旧键 `md_done` / `md_chapters` / `md_indexable` 保留原口径供对照。缺库时这四个键保持 `null`（`library.ok=false`），前端显示「—」，不拿 0 冒充读数；`stats_built_at` 是三库数据戳的最新者，不是计算时刻。**没有 `status.health`**：健康检查在独立接口 `GET /api/search/health`。8765 没起时它回 `{"ok":true,"running":false,"url":"http://127.0.0.1:8765","pid":null,"meta":null,"error":"[Errno 111] Connection refused"}`；`error` 非空，不是 `null`，`ok` 仍是 `true`（只表示接口答得出，不表示搜索在跑）。8765 起着时 `running:true`，并可能多出 `degraded`/`db`/`message`。`extra_books` 是索引里有、磁盘活库已不在的书（如已移出流程的书），别把它读成书目总数；`index.lag_books`/`index.lag_chapters` 结合 `indexed_book` 基线并扣除 `front_back`/`notes` 水章（记入 `filtered_chapters`）与纯图无正文书（记入 `empty_books`），全库同步一致时归零。
- **自动刷新体检缓存。** 写入（如 `POST /api/ledger/row` 或 `POST /api/import/apply` 自动接力 `docs.run` → `sync.fts`）后后台自动合并刷新 `工作区/运维/last-audit.json`：等待 `GET /api/status` 中 `ops.audit_refreshing == false`，断言 `status.audit.ran == true`、`status.audit.stale == false`、`status.problems == []`；无头 Chrome 打开 `#maintain`，`#status-issues` 显示「体检全部通过」（不留过期提醒）。
- **收窄后的待确认与人工收口。** `POST /api/import/scan` 与 `POST /api/pdf/triage` 任务完成后状态直接落 `succeeded`（不进 `awaiting_review`）。在运行态操作台的 `ops.db` 写入一条 `split.run`（`staged-review`）的 `awaiting_review` 任务并 `?refresh=1` 后，无头 Chrome 打开 `#overview` 可见「1 个任务待确认」与 `#logs?state=awaiting_review` 链接；点击后 `#log-filter` 自动切到 `awaiting_review` 且详情展开 `#job-review-box`（含 `#job-resolve-note` 与「确认已处理 / 标为失败 / 忽略关闭」）；填写备注并点击「确认已处理」（`POST /api/jobs/<id>/resolve` 返回 `200` 且 `state="succeeded"`，再次调同一接口回 `409`，传非法 `resolution` 回 `400`，未知 `id` 回 `404`），`state_counts.awaiting_review` 归零且总览待办消失。
- **任务详情直达与页内定位。** 构造长任务表令 `#job-detail` 位于首屏之下后，从 `#overview` / `#maintain` 点任一任务「详情」跳 `#logs`、清空跨页残留筛选并立即滚动使 `#job-detail` 进入视口且对应行高亮 `tr.active`；在 `#logs` 滚回顶部后先后点不同任务行与同一任务行的「详情」，`#job-detail` 标题与 `#job-meta` 切换到新任务并再次滚动定位到 `#job-detail`（页内切换保留 `#log-filter`）。
- **台账分片渲染与防抖筛选。** 当人账超过 100 行时，无头 Chrome 打开 `#ledger` 首屏 `tbody tr` 为 `100` 行并显示 `#ledger-pager`（「加载更多 100 行」/「显示全部」）；在 `#ledger-q` 键入关键词约 80ms 后过滤生效且输入框不丢焦点；清空并点击「显示全部」渲染全部行且 `#ledger-pager` 隐藏。
- **主导航与子页签都点得开。** `uv run --frozen python .agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py --base http://127.0.0.1:18770 --out "$EV"` 起自己的 headless Chromium，切 `overview/import/ledger/split/maintain/logs/mobile` 与 `retire/cold/docs/pdf/pdf-list/manual` 十三个 hash（其中 `390x844` 溢出只在 `#overview` 检测），捕获控制台异常与 `Runtime.consoleAPICalled` 错误，断言每个有 `<h1>/<h2>`、无横向溢出、无控制台错误，出 `ops-overview-1440x900.png`，末三行 `CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`（结束时自动清理 `/tmp/ops-tabs-*`）。`apps/ops/web/app.js` 的 `TABS` 是七条，`EXTRA_TABS` 是 `docs` / `pdf` / `pdf-list` / `manual` / `retire` / `cold`；`#nav` 有七条链接。
- **左侧导航固定吸顶与桌面布局对齐。** `TMPDIR=/tmp uv run --frozen python tests/acceptance/ops/test_sidebar_sticky.py` 先校对 `apps/ops/web/style.css` 与 `GET /style.css` 中 `html`/`body` 为 `overflow-x: clip`（非 `hidden`）、`.sidebar` 为 `position: sticky; top: 0;`，再用无头 Chromium 验证：桌面 `1440×700` / `1440×900` 在可滚动长页（展开详情的 `#maintain`）滚到底部后 `.sidebar` 贴顶满屏（`abs(top) <= 1`、`abs(bottom - innerHeight) <= 1`、`.brand` 与末项导航留在视口内）、末张卡片下端保留底边距（`0 <= vh - lastCardBottom <= 45`），且页底直接点击左侧导航可切回 `#overview`；`1920×1080` 宽屏无顶栏搜索按钮（`#search-btn`/`#search-start` 不在 DOM），`main` 与 `.topbar-inner` 按 `2080px` 限宽水平居中且左缘对齐、`.brand` 与 `.topbar` 底部分隔线齐平（`<= 1px`）、`#stats` 文字垂直居中（上下留白差 `<= 2px`），不满一屏页面（`#manual`）保持自然排布不拉到底端；窄屏 `390×844` 保持 `position: static` 且顶栏 `#theme-toggle` 可见、滚下后顶部导航正常滚走、零横向溢出，控制台 0 错误，末行 `ALL PASS`。
- **全量浏览器交互验收。** `uv run --frozen python tests/manual/ops/shot_ops.py`（已内置 `sys.path` 无需 `PYTHONPATH`，适配七模块、`RETIRE_MODAL` 与 `#job-review-box`），自建临时根跑完七模块、五子页签、导入/分诊/删书弹窗与 1440x900 / 390x844 两档布局，末三行 `CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`。
- **冷宫三态与校正（`#cold`）。** `GET /api/cold` 是只读核验：`items[]` 每条带 `state`（`in_cold` 台账有记录且 `books/_冷宫` 实物在 / `missing` 记录在实物没了 / `unregistered` 实物在台账没记录），另有 `counts.{in_cold,missing,unregistered,backfilled,total}`、`checked_at`、`cold_dir_exists`、`batches_exists`；旧 `retired.tsv` 没有 `state`/`last_checked` 列也照常按实物推断，不报错。`POST /api/cold/reconcile` 才写盘：实物在记 `in_cold`，实物没了记 `missing` 并写 `last_checked`（行保留，不删审计），实物有台账没有就补登一行（`reason=补登`、`note=补登：刷新时发现实物无台账`，目录名形如 `B9123 书名` 就解析出 uid，否则留空）；新列只会追加在表头末尾，首次写某份 tsv 前在同目录留 `retired.tsv.bak-YYYYmmdd-HHMMSS`（最多 3 份）。连调两次第二次 `changed=0`、`backups=[]`。页面证据：无头 Chrome 打开 `#cold`，`<th>实物状态</th>` 与 `.stats` 汇总条（在库 / 缺失 / 补登 / 未登记，校正后多一项本次改动）都在；`books/_冷宫` 被清空时台账行仍是「缺失 ✓行保留」而不会被静默隐掉。
- **上传。** 在「新书导入」选一个文件上传。`curl -s http://127.0.0.1:18770/api/inbox` 里出现该文件，磁盘上落在 `$ROOT/books/_收件箱/`。
- **删书两步。** 先 dry-run 再 apply（不再要求库外快照目录）。`curl -s -X POST http://127.0.0.1:18770/api/delete/dry-run -H 'Content-Type: application/json' -d '{"uid":"B0001","reason":"验证移出"}'` 产出 `dry-run.json`（含 `plan_hash`，不含 `snapshot` / `snapshot_ok`），预览弹窗不显示已退役的回滚副本行；`POST /api/delete/apply` 带 `uid`、`reason`、`plan_hash`、`confirm_uid`、`confirm:true` 落地后，原书进入 `books/_冷宫/`、`retired.tsv` 落 15 列无 `snapshot` 列，`/api/cold` 返回该条记录，`/api/status` 书目减 1（本轮实测 `1 → 0`）。

## Gotchas

- 页面是 JS 壳，`curl` 拿到的 HTML 只有约 900 字节，搜不到页签文字；页签断言要靠 CDP，不是 `grep`。
- `tests/manual/ops/shot_ops.py` 默认把截图写进 `tests/manual/ops/总览-*.png`（已被 `.gitignore` 忽略），自建临时根后会自清。
- `google-chrome-stable` 已不在本机；本技能脚本用 `CHROME_BIN`（缺省 `/usr/bin/chromium`）。要显式指定就 `CHROME_BIN=/usr/bin/chromium`，别去装 Chrome。
- `drive_ops_tabs.py` 自己起 Chromium、自己关并在 `finally:` 清理 `/tmp/ops-tabs-*`。它只断言 12 页签 heading / 控制台错误（含 `console.error`）与 `#overview` 手机宽度溢出，不覆盖逐行改账与台账分页这类写入/大数据动作；账表数据仍验 `/api/ledger` 与 CDP 实操。
- `check_ops.py` 与 `drive_ops_tabs.py` 用的都是临时根；真实队列会调 `src/bookcorpus/scripts/` 里的东西，用临时根跑才安全。
- 点「复核并增量对齐」/「补齐偏差（增量同步）」会在临时根里真跑 4 步（无向量库时跳过 `embed sync`；已有向量库或 `force_embed` 时跑 5 步），完事 toast `4 步全过`（或 `5 步全过`）；它会写 `$ROOT` 的索引与 `工作区/运维`，不要对操作员的 8770 点。
- 直接跑 `tests/acceptance/ops/test_status.py` 或 `test_pdf.py` 时，`REAL_ROOT`/`INSTANCE` 在未设 `BOOKS_ROOT` 时默认 `~/书库`（`test_status.py:22`、`test_pdf.py:26`），会读真实账与索引；要跑就先 `export BOOKS_ROOT=$ROOT`。`test_status.py` 没有 `def test_`，pytest 不会收集它，只能当脚本跑。
- `/api/search/health` 与 `/api/services` 默认探测 `127.0.0.1` 的 `8765/8773/8771/8772`；搜索一组可注入，`BOOK_CORPUS_SEARCH_URL` / `BOOK_CORPUS_DEMO_SERVER`（或 `Env` 构造参数）设了之后 `/api/search/health` 与 `/api/search/start` 只碰注入值，不再走 `app_links.ensure("search", root)`；`/api/services*` 没有注入点，本机常驻实例占着默认端口时切勿在临时实例上点服务页签的「启动/打开服务」。
- 状态缓存（SWR）：`GET /api/status` 在 `STATUS_TTL=15s` / `STATUS_SWR_TTL=60s` 内可命中缓存；若在操作台之外直接跑 CLI 改了 `$ROOT` 文件，断言新状态必须带 `?refresh=1`。
- 后台体检异步时序：写入后 `schedule_audit_refresh()` 在后台线程更新 `last-audit.json`，断言 `audit.stale == false` 前需轮询等 `ops.audit_refreshing == false`。
- 启动收口：`App.__init__` 会自动跑 `reconcile_stale_reviews()` 把遗留 `import.scan`/`pdf.triage` 或已落地的 `split.run` 从 `awaiting_review` 收敛成 `succeeded`；要做人工确认闭环测试，需构造真实 `staged-review` 批次或在服务启动后写入 `ops.db`。
- 写入边界：`POST /api/import/upload` 是往库正目录唯一落原始书的入口（`_收件箱/`）；人账保存（`POST /api/ledger/row` / `confirm`）会进程内经 `sync_metadata_fast()` 同步改写 `tables/ledger.db`、`book.json`、`library.db`、`build/views/` 并触发后台体检；批次受理/分诊确认写 `工作区/批次/`；队列任务改写 `books/` 与 `build/`。
- 同一份工作区同时只能有一个写者（`.writer.lock`）；两个验证进程并行跑会互相等锁，看起来像卡住。
- `apps/ops/server.py` 启动时 `_envfile_module()` 会把 `src/` 加进 `sys.path`，因此即使裸 `python3` 也会加载仓库 `.env`；但子脚本（如 `gen_book_catalog.py` 导 `.xlsx`、`split_md_batch.py` 调 `pimodel`）需要 `.venv` 依赖，建议统一用 `uv run --frozen python` 起服务，并显式 `export BOOKS_ROOT=$ROOT`，不要改仓库 `.env`。
- `--port 0` 时实际端口只在启动行里，不要假设 8770。
- `apps/ops/web/style.css` 的 `html`/`body` 必须用 `overflow-x: clip` 而非 `overflow-x: hidden`；否则按 CSS Overflow 规范 `overflow-y` 会计算为 `auto`，令 `body` 成为包裹整页高度的非滚动容器、导致 `.sidebar` 的 `position: sticky` 失效。
