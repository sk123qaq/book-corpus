---
name: verify-book-corpus
description: >
  用一次性工作区启动并驱动 book-corpus 的五个本地页面（8773 书库首页、8765 找一段、8771 分层读、
  8772 往期日课、8770 操作台），以及 CLI 的 `book-corpus embed`、`search`、`query`；验证用户可见行为、
  留下可复核的证据，然后清理实例。找一段包含 SSE 流式与暂停续答。
  当需要验证 book-corpus 的一个改动、复现别人报的页面问题、或按功能地图跑端到端检查时使用。
  Drive the disposable stack, never the operator's live library on 8765/8771/8772/8773.
argument-hint: "[feature] [run-id]"
---

# 验证 book-corpus

这套服务是本地 stdlib Python 进程，全部读写一个「工作区」目录（`books/` + `build/` + `tables/` + `日课/`）。
验证的前提是：**自己造工作区、自己起服务、自己杀**。操作员有一个 41G 的真实实例常驻在
8765 / 8771 / 8772 / 8773 上，指向操作员的真实库 `~/书库`；那不是验证目标，误连会写进真库。

功能地图在本技能目录的 `features/`：先读 [features/README.md](features/README.md)，再按被验功能的
文件拿逐条命令。本文件只讲启动、体检、证据与清理。

## Launch

工作区（空目录到可用，约 0.5 秒）：

```
cd "$(git rev-parse --show-toplevel)"   # 在 book-corpus 仓库里执行
RUN_ID=${1:-$(date +%H%M%S)}
ROOT=/tmp/bc-verify-$RUN_ID
mkdir -p "$ROOT/run"
export BOOK_CORPUS_ENV="$ROOT/run/env"   # init 写这里，不碰仓库 .env
unset BOOKS_ROOT BOOK_CORPUS_DATA        # find_root() 先看环境变量，不清会盖掉 --root
uv run --frozen book-corpus init "$ROOT" --no-pdf-venv
uv run --frozen book-corpus sample --root "$ROOT"
```

- `init` 先打印「工作区建好」，再把 `BOOKS_ROOT` 记到 `$BOOK_CORPUS_ENV`（未设时写仓库 `.env`，会污染本仓），随后是两行 venv 未建警告（`--no-pdf-venv` 的预期结果）、书目库与检索索引。
- `sample` 装内置样书：阿Q正传 `B0001`、9 章、检索索引 123 段（`sample` 自己报 22881 字，分层读 `/api/shelf` 报 21362 正文字符，两处口径不同，别拿一个去验另一个）。没有它，书架、章页、检索都只能验空状态。

服务按需起，端口固定用私有的一套，PID 落盘：

```
setsid env BOOKS_ROOT="$ROOT" \
  python3 apps/home/server.py --root "$ROOT" --port 18773 --no-start --quiet \
  --search-url http://127.0.0.1:18765 --reading-url http://127.0.0.1:18771 \
  --daily-url http://127.0.0.1:18772 --ops-url http://127.0.0.1:18770 \
  >>"$ROOT/run/home.log" 2>&1 & echo $! > "$ROOT/run/home.pid"

setsid env BOOKS_ROOT="$ROOT" \
  uv run --frozen python apps/reading/server.py --root "$ROOT" --port 18771 \
  --no-annotate --quiet >>"$ROOT/run/reading.log" 2>&1 & echo $! > "$ROOT/run/reading.pid"

setsid env BOOKS_ROOT="$ROOT" \
  python3 apps/daily/server.py --root "$ROOT" --port 18772 \
  --reading-url http://127.0.0.1:18771 \
  >>"$ROOT/run/daily.log" 2>&1 & echo $! > "$ROOT/run/daily.pid"

setsid env BOOKS_ROOT="$ROOT" BOOKS_EMBED_URL=http://127.0.0.1:18780 \
  python3 apps/demo/server.py --root "$ROOT" --port 18765 \
  --reading-url http://127.0.0.1:18771 --home-url http://127.0.0.1:18773 \
  >>"$ROOT/run/demo.log" 2>&1 & echo $! > "$ROOT/run/demo.pid"

setsid env BOOKS_ROOT="$ROOT" \
  python3 apps/ops/server.py --root "$ROOT" --port 18770 \
  >>"$ROOT/run/ops.log" 2>&1 & echo $! > "$ROOT/run/ops.pid"
```

就绪判据是每个服务打印的那一行，不是进程存在：

| 服务 | 端口 | 就绪行 | 备注 |
|---|---|---|---|
| 首页 | 18773 | `书库首页 http://127.0.0.1:18773 · root=<ROOT>` | 只读 `books/`、`日课/` 与仓库模板；`books.toml` 必需，缺了 exit 2。`Library` 仍优先读 `BOOKS_ROOT`，启动时显式带上，否则仓库 `.env` 的活库路径会盖掉 `--root`。可以最先起 |
| 分层读 | 18771 | `阅读工作台 8771 · root=<ROOT> · http://127.0.0.1:18771/` | 不带 `--quiet` 时 stderr 还会打「配置提示：…」，是云端 key 常态，不是故障。章页展示完整正文工作台与工具栏，`/api/chapter` 带知识地图与容量预检；`--no-annotate` 只是兼容开关，不关伴读/地图的模型调用。同样受 `BOOKS_ROOT` 优先影响 |
| 日课 | 18772 | `http://127.0.0.1:18772` | 单行输出，没有别的就绪信号。`--reading-url` 决定 `/api/feed` 的 `reading_url` 与「在分层读打开」链接 |
| 找一段 | 18765 | `书库检索演示：http://127.0.0.1:18765` | 接 `--root`，会话、terms、answers、related、运行记录都落 `<root>/工作区/检索/`；启动必打印 `启动恢复：N 轮…`（0 也打）。`BOOKS_RUNLOG_ROOT` 仍可单独覆盖运行记录 |
| 操作台 | 18770 | `统一操作台：http://127.0.0.1:18770  root=<ROOT>` | `--port 0` 会打印实际端口 |

- 分层读要读 `build/library.db`，必须在 `sample` 之后起。
- 日课要验「有内容」时，先按 [features/daily-lessons.md](features/daily-lessons.md) 手写一期 `$ROOT/日课/<date>.json`；`book-corpus daily --dry-run` 打选书、模型/思考/工具、`----- 提示词 -----` 与提示词四块，不落盘。
- 首页若用默认地址起（不带 `--*-url`），点卡片会拉起目标服务，拉起命令由 `src/bookcorpus/app_links.py` 生成，带目标服务的默认 `--port` 与本轮 `--root`，会话/运行记录随根隔离。默认地址都占用时，用 `--search-url` 等显式指到 18765/18771/18772/18770，`/go` 会直接 302，不触发自动拉起；要验 503 必须让某个默认端口空着，别停操作员的常驻实例。
- 拆服务时就别并行起：同一工作区只能有一个写者（`.writer.lock`）。

收尾见 Cleanup；中途放弃也要走一遍。

## Doctor

每次开跑前、或任何一步「看起来不对」时，先跑这三条只读检查：

```
uv run --frozen book-corpus doctor --root "$ROOT"
curl -s http://127.0.0.1:18765/api/health
ss -ltnp | grep -E ':(18765|18770|18771|18772|18773)\b'
```

- `doctor` 逐行 `ok`/`warn`。允许的 `warn` 只有两类：环境类（`PDF 环境`、`文档环境`）与云端 key 缺失（`配置·MinerU OCR`、`配置·PaddleOCR 云端 OCR`、`配置·MiMo 扩展生成`）。落在工作区、清单、账、库上的 `warn` 才是问题，先解释再继续。
- 演示页健康必须 `{"ok": true, "db": "ok", ...}` 且 `meta.books` 为 `1`、`meta.paras` 为 `123`。三种降级形状要分清：索引没建是 `db: missing`，索引为空是 `db: empty`，书目库没建是 `library: missing`；后两种 `meta` 里的计数可能是 `null`。`meta.embed` 必须存在，字段是 `vectors` / `server` / `ready` / `chunks` / `books` / `url`；`ready:false` 是混合检索降级，不是页面坏。
- 端口清单里每个监听者的命令行必须带 `--root $ROOT` 或 `BOOKS_ROOT=$ROOT`；不属于本轮的监听者停：可能是上一轮没清干净。不要按 PID 比：`uv run --frozen python` 起的服务，监听的是它派生的子进程，不是 `run/*.pid` 里那个包装进程。
- 页面根路径回 403 说明 Host 头不是 `127.0.0.1`/`localhost`，不是服务坏了。

值得驱动 = 工作区根对上 `$ROOT` **且** 就绪行对上端口 **且** 健康检查 `db: ok`。三条里有一条不成立，就不要往里写数据。

起服务前多问一句「这跑的是哪份代码」：读读服务的时间不代表二进制的时间。`stat -c %y` 看进程启动时间，`git rev-parse HEAD` 看当前提交；进程比 HEAD 老，就可能跑的是改造前的老代码（2026-11-12 有个常驻实例跑了七小时的前一代代码，所有新行为都不存在）。要验新行为，一律自己起。

## Drive

四种驱动方式，按功能文件里写的用，不要自创：

- **curl** 验服务端渲染页骨架与接口契约：取 HTML 断言稳定标记，取 JSON 断言字段。`features/library-home.md`、`features/daily-lessons.md`、`features/manage-library.md` 走这条；`features/layered-reads.md` 的接口也走 curl，但章页/记录页是客户端渲染，用户可见状态用 `--dump-dom`。
- **pytest** 验离线契约：`uv run --frozen pytest tests/test_home_*.py`、`tests/test_reading_*.py`、`tests/test_daily.py tests/acceptance/daily/test_feed.py`。
- **直接跑脚本**验端到端：`python3 tests/acceptance/ops/check_ops.py`（末行 `168 项检查，失败 0 项` 与 `ALL PASS`）。它不是 pytest 模块（零个 `def test_`），用 `pytest` 跑会 `no tests ran` 并以退出码 5 失败，那是跑法错不是产品坏。
- **CLI** 验用户命令：`book-corpus embed` 在一次性根里跑，断言 stdout/stderr、退出码与磁盘产物，逐条见 `features/embed.md`；`book-corpus search` / `query` 的词法、混合、降级与书卡逐条见 `features/search-query.md`。两者都只认 `--root` 指到的一次性根；`embed` 默认 `--server` 与 `search/query` 默认 `BOOKS_EMBED_URL` 都是操作员的 18080，必须改 18780。
- **headless Chrome** 验客户端渲染页、点击、键盘、截图。`--dump-dom` 取渲染后的 DOM（分层读章页、记录页）；章页模板已移到 `assets/chapter.js`，DOM 里不再有内联模板字符串，剥不剥 `<script>` 都不会多算，交互与截图才用仓库自带的 CDP 脚本；没有 Playwright。默认找 `google-chrome-stable`；本机有（2026-11-14 实测 155.0.8059.79），不设 `CHROME_BIN` 也能跑。`/usr/bin/chromium` 与 `/usr/bin/microsoft-edge-stable` 都不存在；要显式指定时用 `/usr/bin/google-chrome-stable` 或 `/usr/bin/microsoft-edge`。
  - `google-chrome-stable --headless=new --disable-gpu --no-sandbox --user-data-dir="$ROOT/run/chrome-chapter" --virtual-time-budget=8000 --dump-dom http://127.0.0.1:18771/chapter/B0001/01 > "$ROOT/run/chapter-dom.html"`，数 `id="btn-map"`、`id="wb-toolbar"`、`blk-B01`，并断言 `id="depth-panel"` 不在；模板在外链 `assets/chapter.js` 里，DOM 不含模板字符串。详细片段见 `features/layered-reads.md`。
  - 跨页定位链：先取 `/open?uid=B0001&path=...&line=...&q=...&from=search` 的 302，再 `--dump-dom` 跟随章页，断言 `id="arrival-banner"`、`mark.hl-hit`、`reader-block hit` 与 `id="selection-bar"`；检索页用 `?q=...&books=B0001&mode=trace`，断言 `trace-hit-card` 与 `/go/reading?to=` 链接，且不出现回答轮询。
  - `python3 tests/manual/demo/shot.py --url 'http://127.0.0.1:18765/classic?q=...&instant=1&top=1'`，末行 `CONSOLE ERROR 0`；流式回答会先截到半截正文，`answer ready=False` 不等于失败。
  - `python3 .agents/skills/verify-book-corpus/scripts/drive_sse_pause.py --base http://127.0.0.1:18765 --out "$EV/sse-pause-resume.json"`，末行 `RESUMED ... done_ok=True`，exit 0；它真打一次模型，先读完离线合同再决定跑不跑。
  - `python3 tests/manual/demo/check_session_ui.py --base http://127.0.0.1:18765`，有匹配的 3 轮/多书会话时末行 `ALL PASS`；没有时止于 `没有找到可用的 3 轮会话`，这是样本缺失不是页面坏（2026-11-14 本机 11 条会话仍没有 3 轮样本，就属于这一支）。
  - `PYTHONPATH=$PWD/tests/acceptance/ops python3 tests/manual/ops/shot_ops.py`，自己起临时根；末行 `SHOT PASS`、`CONSOLE ERROR 0`，八个页签含账表（2026-11-13 实测）。
- **本地模型 stub** 验分层读的 SSE 与知识地图生成：`python3 .agents/skills/verify-book-corpus/scripts/mock_reading_model.py --port 18790`，阅读服务带 `OPENCODE_GO_API_KEY=sk-mock`，并把 `READING_MODEL_URL` 指向这个 stub 起。它只返回可渲染的固定形状，不评价模型质量；逐条命令见 `features/layered-reads.md`。

硬规则：

- 每个功能文件里的命令逐字复制，不改引号与参数。
- 页面动作和接口断言是两种证据，别用后者冒充前者。只验了接口没走页面，记 `ui-unverified`；客户端渲染页只做 curl 断言，同样记 `ui-unverified`，不能算页面通过。
- 知识地图生成与划词伴读会打模型（默认 `opencode-go/deepseek-v4.1-flash`）；`--no-annotate` 是兼容开关，不关任何模型调用。没有环境 key 也不等于离线：`ModelClient` 还会读 `~/.pi/agent/auth.json` 的 opencode-go 登录。模型分支用 `scripts/mock_reading_model.py` 本地 stub；只有决定花真调用时才打真模型，并在证据里记下这次调用。
- 演示页传 `--root` 时，会话库在 `<root>/工作区/检索/sessions/`，与操作员隔离；不传 `--root` 才落仓库内 `apps/demo/缓存/sessions/`，写进去就是写进操作员的会话列表。验证一律传 `--root`。

## Evidence

证据落在仓库的 `artifacts/verify/<RUN_ID>/<feature>/`（已 gitignore），按功能分子目录。一个目录里至少要有：

- `commands.txt`：逐条命令 + 退出码 + 关键输出（原样，不摘写）。
- 页面证据：服务端渲染页的 `curl` HTML 片段、客户端渲染页的剥脚本后 `--dump-dom` HTML、或 CDP 截图；截图里要能认出是哪个页面。
- 接口证据：JSON 的完整响应，或至少被断言的字段原值。
- 变更类动作：动作前的状态、动作后的状态，外加一次独立的只读复核（例如再取一次 `/api/feed`）。

判定标准：走的是真实用户路径，不是测试专用入口；同时留下动作与结果；副作用（文件、账、队列）要单独看一眼。跑红了一项就把末行连同失败的条目一起存，别只存「差不多」。

## Cleanup

按记录的 PID 杀，不按进程名杀。先删本轮在 `<root>/工作区/检索/sessions/` 建的会话（没传 `--root` 的老实例才落仓库）：

```
curl -s -X POST http://127.0.0.1:18765/api/sessions/delete \
  -H 'Content-Type: application/json' -d '{"ids":["<session-id>"]}'
for f in "$ROOT"/run/*.pid; do kill "$(cat "$f")" 2>/dev/null; done
```

- 拉起过的服务（首页自动起的操作台）不在 `run/*.pid` 里：`ss -ltnp | grep :8770` 拿到 PID，记进 `$ROOT/run/autostart.pid` 再杀。
- 杀完确认 `ss -ltnp | grep -E ':(18765|18770|18771|18772|18773|18774|18775)\b'` 为空；`uv run` 与首页拉起的那类子进程会随父进程一起退出，但端口空才是判据。
- embed 的 llama-server 在 `18780`：`ss -ltnp | grep ':18780 '` 为空，且 `pgrep -af "$ROOT"` 无输出，才算收干净。跑过 embed 的轮次必查。
- 删工作区：`rm -rf "$ROOT"`。**不要**删 `artifacts/`，清理只删实例与临时根。
- 确认仓库 `.env` 没被本轮改写：先记下它原有的 `BOOKS_ROOT`（操作员实例可能就指向 `~/书库`），只检查它没有变成 `$ROOT`；`init` 一律带 `BOOK_CORPUS_ENV=$ROOT/run/env`，不要动 `.env`。
- 删完 `ls artifacts/verify/<RUN_ID>/<feature>/` 还在，才算这轮收干净。
- 绝不碰 8765 / 8771 / 8772 / 8773 上的任何进程，哪怕它看起来卡住了。

## Helpers

启动、体检、驱动、清理的命令都内联在上面或功能文件里，直接复制执行。功能性驱动优先复用仓库自带的 `tests/manual/**` 与 `tests/acceptance/**`；唯一随技能附带的脚本是 `scripts/drive_sse_pause.py`，因为 SSE + 暂停 + 续答需要同时读两条流并在一段中途发暂停请求，curl 拼不出来。

## Feature map

五个页面各一份：`features/library-home.md`、`find-a-passage.md`、`layered-reads.md`、`daily-lessons.md`、`manage-library.md`；
CLI 的 `features/embed.md` 与 `features/search-query.md` 是第六、第七份。
地图是维护目标：行为变了先改地图，再改这条验证；`features/README.md` 记着当前已知红与未映射的 CLI 面。分层读的章页与记录页是客户端渲染，地图里的页面断言以 `--dump-dom` 为准。

改动本文件后，在 Pi 里 `/reload` 让技能重新加载；Pi 只在从仓库目录（或其子目录）启动时才发现项目 `.agents/skills/`。
