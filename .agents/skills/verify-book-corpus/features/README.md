# book-corpus 验证地图

这份地图是 book-corpus 用户可见行为的验证源：先读本索引，再按对应功能文件开跑。
它只写本仓库现在真实能跑的命令，不写「应该有」的工具。

## 基线前置

所有命令都在 git 仓库根执行（先 `cd "$(git rev-parse --show-toplevel)"`），工作区根用一次性的。

```
ROOT=/tmp/bc-verify-$RUN_ID
mkdir -p "$ROOT/run"
export BOOK_CORPUS_ENV="$ROOT/run/env"   # init 写这里，不碰仓库 .env
export BOOKS_ROOT="$ROOT"                # 无 --root 的子命令与默认回退看它；显式 --root 优先于它
uv run --frozen book-corpus init "$ROOT" --no-pdf-venv
uv run --frozen book-corpus sample --root "$ROOT"
uv run --frozen book-corpus doctor --root "$ROOT"
```

- `init` 约 0.2 秒，先打印「工作区建好」，再把 `BOOKS_ROOT` 记到 `$BOOK_CORPUS_ENV`（未设时写仓库 `.env`，会污染本仓），随后两行 venv 未建警告（`--no-pdf-venv` 的预期结果）、书目库与检索索引。
- `sample` 装内置样书：阿Q正传 `B0001`，9 章，检索索引 123 段。字数两处口径不同：`sample` 报 22881 字（含标题等），分层读 `/api/shelf` 报 21362 正文字符。
- `doctor` 只读，逐行 `ok`/`warn`。允许的 `warn` 只有两类：**环境**（`PDF 环境`、`文档环境`、`精排环境`）与**外部配置缺失**（`配置·*`，如 `配置·书库流水线的模型` / `配置·阅读工作台伴读/地图模型` / `配置·MinerU OCR` / `配置·PaddleOCR 云端 OCR` / `配置·MiMo 扩展生成` / `配置·扩展生成的 pi 账号`）。落在工作区、清单、账、库上的 `warn` 才算问题，先解释再开跑。
- 端口用本轮私有的一套：演示 18765、分层读 18771、日课 18772、首页 18773、操作台 18770。503 与自动拉起另起 18774 / 18775，且只在默认端口空着的隔离机跑；本机默认端口都被常驻实例占着，记 `unexercised`。
- 8765 / 8771 / 8772 / 8773 是操作员的常驻实例，跑在操作员的真实库 `~/书库` 上（各服务 `--root` 默认为 `None`，未传时由 `envfile.resolve_books_root()` 依次读 `BOOKS_ROOT`、`$BOOK_CORPUS_ENV`/仓库 `.env`，不再硬编码回退 `~/书库`，也不会再回退 `BOOK_CORPUS_DATA`）。永不驱、永不杀。仓库 `.env` 的 `BOOKS_ROOT` 常指向 `~/书库`；本轮仍传 `--root "$ROOT"` 并显式 `export BOOKS_ROOT="$ROOT"`，不改 `.env`。
- 演示页无论是否传 `--root`，都按 `resolve_books_root(args.root)` 解析工作区根，会话、terms、answers、related 与运行记录都落在该根的 `<root>/工作区/检索/`（传 `--root "$ROOT"` 即与操作员完全隔离）。
- 需要「有内容」才成立的验证，手写 `$ROOT/日课/<date>.json`（契约见 [daily-lessons.md](./daily-lessons.md)）。`book-corpus daily --dry-run` 打选书、模型/思考/工具、`----- 提示词 -----` 与提示词四块，不落盘，不能当 seed。
- embed 要 llama-server 与 GGUF 模型，软链进 `$ROOT/工作区/中间态/embed/`，用私有 18780；见 [embed.md](./embed.md)。

## 驱动约定

- 每个服务由本轮自己起、自己按 PID 杀，不按进程名杀。
- 服务端渲染页目前只有**首页**，可用 `curl` 取 HTML 断言稳定标记；其余应用页面（日课、操作台、找一段、分层读五页）均为客户端渲染薄壳，`curl` 只证明模板壳与静态资源在，接口一律 `curl` 取 JSON 断言字段，用户可见状态必须用 headless Chrome `--dump-dom` 取渲染后的 DOM；分层读章页模板已在外链 `assets/chapter.js`，DOM 不含模板字符串，剥不剥 `<script>` 都一样。点击、键盘才用 CDP 脚本。具体命令见 [layered-reads.md](./layered-reads.md)。
- CDP 脚本用 `CHROME_BIN` 指定浏览器；本机是 `/usr/bin/chromium`（2026-11-20 实测 155.0.8059.84），`google-chrome-stable` 已不存在。技能脚本、`tests/manual/ops/shot_ops.py` 与 `tests/manual/demo/shot.py`（自 `28d18cb`）均读 `CHROME_BIN` 并自动探测 `chromium`。
- 命令逐字复制，不改引号、不改参数。
- 证据写 `artifacts/verify/$RUN_ID/<feature>/`；清理只删实例与临时根，不删证据。
- 五个服务（首页 8773 / 分层读 8771 / 日课 8772 / 操作台 8770 / 演示页 8765）都只认 `127.0.0.1` / `localhost` 的 Host 头，别的 Host 回 403。演示页直到 2026-11-12 晚才补上这道检查（`apps/demo/server.py` 的 `ALLOWED_HOST`），旧脚本里「演示页不校验 Host」的说法已作废。

## 证明与跳过

- 2026-11-20 书架刷新 `RUN_ID=20261120-shelf-refresh`（`layered-reads`）：一次性根起 18771 与 18773，落地 `books/B0002`（book.json 加一章 md）后不重启，3 秒内 `/api/shelf` 从 1 本变 2 本，首页也从 1 本变 2 本；移走再移回同样跟随。`--dump-dom` 书架页含「新落地」与 B0002。`tests/test_reading_library.py` 11 项过，新增 6 项覆盖落地新书、改书名、删 md、磁盘不变不重建、索引 busy 保留旧列表与 TTL 门。证据在 `artifacts/verify/20261120-shelf-refresh/shelf-refresh/`。
- 2026-11-20 验证技能专项维护 `RUN_ID=20261120-verify-maint`（`/pstack:maintain-verification-skill`）：源码对照并修复 `manage-library`、`embed`、`SKILL.md` 与 `scripts/drive_ops_tabs.py`（补 `Runtime.consoleAPICalled` 错误/断言捕获）漂移后，在一次性隔离根重跑 live 实测全过：18770 验通更新后 `drive_ops_tabs.py`（`CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`）、`import.scan` 直落 `succeeded`、`POST /api/ledger/row` 触发后台 `last-audit.json` 自动刷新并令 `#maintain` 显示「体检全部通过」、SWR 缓存命中同 `at` 与 `?refresh=1` 重算、post-boot `staged-review` 待确认跳 `#logs?state=awaiting_review` 收口（409/404 校验）、106 行 `#ledger` 首屏 100 行分页 + 80ms 防抖筛选 + 「显示全部」；18780 验通 `embed --dry-run --json` 不建库、`--sync --allow-missing-engine`、`--books B0001 --dry-run` ETA 行、`--status`、`--verify` 空库、`--prune --json`、`--compact --json` 与单写者锁争用（真实 `llama-server` 向量推理未在本次重跑，前置需只读软链 `~/书库/工作区/中间态/embed/`，已由 `tests/test_embed_corpus.py`/`test_incremental_sync.py`/`test_ops_fixes.py` 与 `RUN_ID=20261120-skill` 覆盖）。证据在 `artifacts/verify/20261120-verify-maint/`。
- 2026-11-20 操作台三项修复与性能复核 `RUN_ID=20261120-ops-polish`（`manage-library`）：一次性隔离根 `18770` 实测导入上传（`测试新书.txt`）→ `import.scan` 直接落 `succeeded` → `import.apply` 自动接力 `docs.run` → `sync.fts` → 后台自动刷新 `last-audit.json`，`#maintain` 显示「体检全部通过」（无过期警告）；总览「1 个任务待确认」点击 `#logs?state=awaiting_review` 自动筛选并展开 `#job-review-box`（「去正文生产处理 / 确认已处理 / 标为失败 / 忽略关闭」+ 备注框），填写备注点「确认已处理」后转 `succeeded` 且总览待办清零；`drive_ops_tabs.py`（`CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`）、`check_ops.py` 171 项与 `shot_ops.py` 1440+390 双档全部通过。证据在 `artifacts/verify/20261120-ops-polish/manage-library/`。
- 2026-11-20 技能全面巡检 `RUN_ID=20261120-maint`（`/pstack:maintain-verification-skill`）：7 路只读源码对照 + 一次性工作区端到端实测全过。覆盖 `daily-lessons`（空目录/`reading_url`/错日期跳过/404/403/`--dump-dom` 与 `tests/test_daily.py`+`test_feed.py`）、`library-home`（三卡/`最近一期`/直连/`/go/reading` 302/404/400/403 与 `tests/test_home_*.py`）、`layered-reads`（五页/`/api/shelf` 21362 字/`/open` 302/短章门控与强制/stub 知识地图+缓存+SSE+旁批/退役 404/`--dump-dom` 与 `tests/test_reading_*.py`+`test_cross_app_linkage.py`）、`find-a-passage`（`/` 与 `/classic`/meta/`scope.books`/pause 边界/导出 400+404+cleanup/`runlog doctor`/trace `--dump-dom` 与 `tests/test_demo_*.py`）、`search-query`（`search`/`--book`/零命中/`--json`/`--expand`/`query` `bm25_only`+书卡+不弹卡与 `test_vec_search_hybrid.py`）、`embed`（`--dry-run --json` 不建库/`--dry-run` 34 块/`--status`/单写者锁与 `test_embed_corpus.py`）、`manage-library`（状态接口/`sync.reconcile` 4 步全过/删书 dry-run+apply 无快照目录落 15 列 `retired.tsv`+`/api/cold`+`books 1→0`、`drive_ops_tabs.py` 全过并已修 `/tmp/ops-tabs-*` 清理与 `check_ops.py` 168 项）。`verified-unreachable` 项及前置：首页默认端口 503/自动拉起（常驻实例占用 8765/8771/8772/8773）、`daily --dry-run`（当前沙箱无 `pi`）、演示页真模型 SSE 暂停续答与多书候选（外部模型配置与 ≥2 本书）、分层读容量 422（需 >5000 块章，由 `test_reading_preflight.py` 覆盖）、`embed` 18780 真实向量推理（需宿主机 `~/书库/工作区/中间态/embed/`，由 `test_embed_corpus.py` 与 `test_vec_search_hybrid.py` 覆盖）。证据在 `artifacts/verify/20261120-maint/`。
- 2026-11-20 删书去快照门槛复核 `RUN_ID=20261120-retire-cleanup`：在不再建库外快照目录的一次性工作区上启动 18770 操作台，走通 `POST /api/delete/dry-run`（`dry-run.json` 不含 `snapshot`/`snapshot_ok`，摘要无「快照」提示）→ `POST /api/delete/apply`（`阿Q正传` `B0001` 移入 `books/_冷宫/`，`retired.tsv` 15 列无 `snapshot` 列，`/api/cold` 返回 1 条，`/api/status` 书目 `1 → 0`）；同时重跑 `uv run --frozen python tests/manual/ops/shot_ops.py`，1440x900 与 390x844 两档删书弹窗 `legacyLeak=False`、`CONSOLE ERROR 0`、`overflow=false`、`SHOT PASS`。证据在 `artifacts/verify/20261120-retire-cleanup/manage-library/`。
- 2026-11-20 文档治理复核（`28d18cb` 之后）：实跑 `PYTHONPATH=$PWD/tests/acceptance/ops uv run --frozen python tests/manual/ops/shot_ops.py`，1440x900 与 390x844 两档六模块 + 五子页签 + 导入/分诊/删书弹窗全部通过（`CONSOLE ERROR 0` / `overflow=false` / `SHOT PASS`）；确认 `tests/manual/demo/shot.py` 自动解析 `CHROME=/usr/bin/chromium`、`README.md` 与 `apps/demo/README.md` 已统一 `uv run`。原三条已知红已关闭。
- 2026-11-20 技能维护轮 `RUN_ID=20261120-skill`：分层读验了七视角切换（literature→history 只改本地，不发请求）、蓝图与扩展（本地 stub）、钉住与刷新持久化、选择条与按选区出图、知识地图树跳转、缩放质检、主题/字号；操作台验了六模块 + 六兼容子页签（`scripts/drive_ops_tabs.py`：`CONSOLE ERROR 0` / `overflow=false` / 1440 与 390 无溢出）、`check_ops.py` 168 项全过、新 `status.sync_delta` 与「复核并增量对齐」5 步全过；首页三卡/键盘/`/go` 直连；日课 feed/单期/坏文件/Host 403；找一段 `/api/run` 范围下推、trace DOM、stateless、导出 md；embed dry-run → run → verify → hybrid → serve。真模型额度 429（`resets in 1d 10h`）、演示页 SSE/暂停续答与多书 scope 记 `unexercised`。证据在 `artifacts/verify/20261120-skill/`（分层读为 `reading/notes.md`，其余为 DOM/JSON/截图）。
- 2026-11-16 分层读局部复核 `RUN_ID=20261116-layered`：书架/书目/书页/章页/记录页走 curl + `--dump-dom`，并用 CDP 点了知识地图、模型设置与伴读抽屉；短章门控、stub 生成/缓存、SSE 图解/讲人话、旁批钉住/移除、Host 403 与退役路由 404 都有证据，`CONSOLE ERROR 0`。真模型未跑，模型分支由 `scripts/mock_reading_model.py` 本地 stub 驱动；容量 422 未 live 演练。证据在 `artifacts/verify/20261116-layered/layered-reads/`。下一轮跑完请就地更新这一行。
- 2026-11-17 跨页联动复核 `RUN_ID=20261117-linkage`：`find-a-passage` 验了 `/api/books/meta`、`scope.books` 排除范围外书名、`?q=&books=&mode=trace` 不触发回答、`/go/reading?to=` 302；`layered-reads` 验了 `/open` 到章页的 302、未知 uid 404、非章节 path 转书目、arrival banner/`mark.hl-hit`/`.hit`/selection bar；`daily-lessons` 验了 `reading_url` 与 `from=daily` 链接；`library-home` 验了 `/go` 直连 302、未知 key 404、坏 `to` 400；`manage-library` 验了 `/api/search/health` 仍探测 8765、`search/start` 返回 already。503/自动拉起未 live 演练（默认端口都被常驻实例占着，不能停操作员 8770），记 `unexercised`。证据在 `artifacts/verify/20261117-linkage/`。
- 2026-11-14 的 `20261114-mt2` 是旧口径：当时还有书级读法、三档/五层读本与 `/reader/<uid>/<n>/depth`，这些界面在 `ce30e6a` / `2b8bf2d` 已退役，不能按原记录复跑，只作历史。`20261114-lr-preflight` 的「章页是客户端渲染」仍然成立。
- 2026-11-15 书库检索局部复核 `RUN_ID=20261115-search-update`：找一段补 SSE / 暂停续答 / 混合检索（真答 34–42 秒、暂停 `partial_text` 3–5 字、resume `done.ok=true`；`shot.py` 截到流式半截正文与「暂停」按钮、`CONSOLE ERROR 0`、`overflow=False`）；CLI 补 `search`/`query` 词法与混合 (`hybrid, vec_hits=34`)、降级 `bm25_only`、书卡触发。证据在 `artifacts/verify/20261115-search-update/`。候选书多选与暂停按钮点击仍记 `ui-unverified`：样书只有 1 本，多选需 ≥2 本候选；暂停点击已由 API 层 SSE+pause+resume 证明。
- 2026-11-13 的 `20261113-mt` 已随 `artifacts/` 轮转清掉；旧轮次证据不保证还在，别把历史 RUN_ID 当可复核链接。

- 一次证明要同时留下动作与结果状态，不能只有最终画面。
- 只验了接口没走页面时记 `ui-unverified`，不算 UI 通过。
- 缺前置就报失败和原因，不把整条路径降级成「别处跑通了」。
- 已知红就是红，见下节。

## 本次已知红（2026-11-20 复核）

本表是全仓已知红的唯一登记处；每行声明「失效源」，对应文件改动时 `book-corpus docs check --since` 会自动点名复核。

| 条目 | 现状与判据 | 失效源 |
|---|---|---|
| `shot_ops.py` 截图标注落仓库 | `tests/manual/ops/shot_ops.py` 已内置 `sys.path`（不再需要 `PYTHONPATH`），并已实测 `SHOT PASS`；截图默认写回 `tests/manual/ops/总览-*.png`（被 `.gitignore` 忽略），非 `--out` | `tests/manual/ops/shot_ops.py` |
| `drive_ops_tabs.py` 清理与探测器 | 读 `CHROME_BIN`（缺省 `/usr/bin/chromium`）并在 `finally:` 自动清理 `/tmp/ops-tabs-*`；捕获 `exceptionThrown`、`Log.entryAdded` 与 `Runtime.consoleAPICalled`（`error`/`assert`），仅验 12 个导航 hash 与 `#overview` `390×844` 溢出，不覆盖逐行改账与台账分页 | `.agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py` |
| `check_paths.py` 跑法与口径 | 必须 `uv run --frozen python verification/chapter_path/check_paths.py` 且不传 `--root`（脚本自建临时根，不读 `sys.argv`）；`checked 128 disk states; 24 reachable by the model` 里 24 才是可服务组合数 | `verification/chapter_path/check_paths.py` |
| `shot.py` 25 秒等待上限 | `tests/manual/demo/shot.py` 只等 25 秒，流式单本回答常需 34–42 秒；脚本会先报 `answer ready=False cites=0` 但正文已在上屏，不能据此判失败，慢回答需轮询 `/api/session` 或看 SSE `done` | `tests/manual/demo/shot.py` |
| 演示页默认根与 `/classic` 无状态 | 传 `--root` 时会话落 `$ROOT/工作区/检索/sessions/`，不传时按 `BOOKS_ROOT`/`.env` 落操作员工作区；`/classic` 发 `stateless:true` 不建会话、不能导出，验导出或历史必须走 `/` | `apps/demo/server.py` |
| 分层读退役路由与模型调用 | 读法、三档/五层、读本页与工单已退役（`/reader/...`、`/api/depth/...`、`/api/jobs` 一律 404）；`--no-annotate` 是兼容开关不关模型，无环境 key 时仍会读 `~/.pi/agent/auth.json`，离线验证一律起 `scripts/mock_reading_model.py` | `apps/reading/server.py`, `src/bookcorpus/reading/model.py` |

## 功能入口契约

每个功能文件以 H1 标题 + 一段用户可见行为说明开头，之后严格四个 H2，顺序固定：

1. `Sub-features`：短 id + 一行行为。
2. `How to get to it (user POV)`：所有用户入口。
3. `Driving it with <harness>`：首行 `Preconditions:`，其余带标签的条目把「用户动作」对「逐字命令 + 可观察结果」。
4. `Gotchas`：会浪费或作废一次验证的坑。

## 功能

- [书库首页](./library-home.md)：8773 的三张入口卡、最新一期、拉起与 503。
- [找一段](./find-a-passage.md)：8765 的多轮会话、候选书、书目范围、思考等级、五阶段经典页，以及 `?q=&books=&mode=trace` 与 `/go/reading` 跨页定位。
- [分层读](./layered-reads.md)：8771 的书架、书目、书页、全章正文工作台、七种阅读视角与结构蓝图/扩展、`/open` 定位与到章高亮、按需知识地图与术语、划词伴读与旁批、钉住、主题/字号，以及短章门控、容量预检与缓存/过期。
- [往期日课](./daily-lessons.md)：8772 的日课列表、单期、坏文件跳过，以及 `chapter.path` 到分层读 `/open` 的链接。
- [管理](./manage-library.md)：8770 的六个模块与兼容子页签、导入、拆章、增量同步、账表、删书、冷宫、日志。
- [书库检索（CLI）](./search-query.md)：`book-corpus search` / `query` 的段检索、书级规划、混合检索、书卡与降级。
- [全库 embedding](./embed.md)：CLI 的 `book-corpus embed`，切块、断点续跑、单写者锁、状态与校验；没有浏览器面。

## 未映射

除 embed 与 search-query 外的 CLI 面还没有功能文件，以 `book-corpus list` 为准；操作员最常用的几条是
`book-corpus run <script>`、`verify`、`library`、`index`、`catalog`、`numbers`、
`stats`、`daily`、`status`、`ingest`、`audit_state`、`rerank-setup`、`rerank-serve`，以及演示页的运行记录 CLI
`uv run --frozen python apps/demo/runlog.py {list,timeline,bundle,replay,prune,doctor}`（`replay` 导入 `numpy`，需走 `uv run`）。它们没有浏览器面，驱法和上面五个不同，需要另一层 harness。
[embed.md](./embed.md) 是 CLI 面写法的一份样板：一次性根、私有端口、stdout/stderr 与退出码当断言。
v1 的 `remap`、`migrate` 已不在 `book-corpus list` 里，别再按旧文档找它们。
