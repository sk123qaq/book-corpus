---
name: verify-book-corpus
description: >
  用一次性工作区启动并驱动 book-corpus 的五个本地页面（8773 书库首页、8765 找一段、8771 分层读、
  8772 往期日课、8770 操作台），验证用户可见行为、留下可复核的证据，然后清理实例。
  当需要验证 book-corpus 的一个改动、复现别人报的页面问题、或按功能地图跑端到端检查时使用。
  Drive the disposable stack, never the operator's live library on 8765/8771/8772/8773.
argument-hint: "[feature] [run-id]"
---

# 验证 book-corpus

这套服务是本地 stdlib Python 进程，全部读写一个「工作区」目录（`books/` + `build/` + `tables/` + `日课/`）。
验证的前提是：**自己造工作区、自己起服务、自己杀**。操作员有一个 41G 的真实实例常驻在
8765 / 8771 / 8772 / 8773 上，指向操作员的真实库 `~/书库`（`~/文档/book-corpus-data` 是旧路径，本机已不存在）；那不是验证目标，误连会写进真库。

功能地图在本技能目录的 `features/`：先读 [features/README.md](features/README.md)，再按被验功能的
文件拿逐条命令。本文件只讲启动、体检、证据与清理。

## Launch

工作区（空目录到可用，约 0.5 秒）：

```
cd "$(git rev-parse --show-toplevel)"   # 在 book-corpus 仓库里执行
RUN_ID=${1:-$(date +%H%M%S)}
ROOT=/tmp/bc-verify-$RUN_ID
mkdir -p "$ROOT/run"
uv run --frozen book-corpus init "$ROOT" --no-pdf-venv
uv run --frozen book-corpus sample --root "$ROOT"
```

- `init` 打印「工作区建好」「书目库：空库已建好」「检索索引：空库已建好」，随后两行 venv 未建警告是 `--no-pdf-venv` 的预期结果。
- `sample` 装内置样书：阿Q正传 `B0001`、9 章、检索索引 123 段（`sample` 自己报 22881 字，分层读 `/api/shelf` 报 21362 正文字符，两处口径不同，别拿一个去验另一个）。没有它，书架、章页、检索都只能验空状态。

服务按需起，端口固定用私有的一套，PID 落盘：

```
setsid python3 apps/home/server.py --root "$ROOT" --port 18773 --no-start --quiet \
  --search-url http://127.0.0.1:18765 --reading-url http://127.0.0.1:18771 \
  --daily-url http://127.0.0.1:18772 --ops-url http://127.0.0.1:18770 \
  >>"$ROOT/run/home.log" 2>&1 & echo $! > "$ROOT/run/home.pid"

setsid uv run --frozen python apps/reading/server.py --root "$ROOT" --port 18771 \
  --no-annotate --quiet >>"$ROOT/run/reading.log" 2>&1 & echo $! > "$ROOT/run/reading.pid"

setsid python3 apps/daily/server.py --root "$ROOT" --port 18772 \
  >>"$ROOT/run/daily.log" 2>&1 & echo $! > "$ROOT/run/daily.pid"

setsid env BOOKS_ROOT="$ROOT" python3 apps/demo/server.py --port 18765 \
  >>"$ROOT/run/demo.log" 2>&1 & echo $! > "$ROOT/run/demo.pid"

setsid python3 apps/ops/server.py --root "$ROOT" --port 18770 \
  >>"$ROOT/run/ops.log" 2>&1 & echo $! > "$ROOT/run/ops.pid"
```

就绪判据是每个服务打印的那一行，不是进程存在：

| 服务 | 端口 | 就绪行 | 备注 |
|---|---|---|---|
| 首页 | 18773 | `书库首页 http://127.0.0.1:18773 · root=<ROOT>` | 只读 `books/`、`日课/` 与仓库模板；`books.toml` 必需，缺了 exit 2。可以最先起 |
| 分层读 | 18771 | `分层读 8771 · root=<ROOT> · http://127.0.0.1:18771/` | 紧跟一行「配置提示：缺少配置：MinerU…；PaddleOCR…；MiMo…」，是云端 key 常态，不是故障。`--no-annotate` 只关标注，生成仍打模型；行里的 `8771` 是固定标签，实际端口看 URL |
| 日课 | 18772 | `http://127.0.0.1:18772` | 单行输出，没有别的就绪信号 |
| 找一段 | 18765 | `书库检索演示：http://127.0.0.1:18765` | 不接 `--root`，只认 `BOOKS_ROOT`；迎宾前可能多一行 `启动恢复：…` |
| 操作台 | 18770 | `统一操作台：http://127.0.0.1:18770  root=<ROOT>` | `--port 0` 会打印实际端口 |

- 分层读要读 `build/library.db`，必须在 `sample` 之后起。
- 日课要验「有内容」时，先按 [features/daily-lessons.md](features/daily-lessons.md) 手写一期 `$ROOT/日课/<date>.json`；`book-corpus daily --dry-run` 只打印提示词、不落盘。
- 首页若用默认地址起（不带 `--*-url`），点卡片会拉起目标服务，拉起命令带首页自己的 `--root`。唯一能安全验这条的是 8770：它空着。8765 若空着，`/go/search` 会在 8765 起一个本轮实例——验跳转时只读 302，别跟随。
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
- 演示页健康必须 `{"ok": true, "db": "ok", ...}` 且 `meta.books` 为 `1`、`meta.paras` 为 `123`。答 `db: missing` 说明没设 `BOOKS_ROOT`，进程落到了 cwd。
- 端口清单里每个监听者的命令行必须带 `--root $ROOT` 或 `BOOKS_ROOT=$ROOT`；不属于本轮的监听者停：可能是上一轮没清干净。不要按 PID 比：`uv run --frozen python` 起的服务，监听的是它派生的子进程，不是 `run/*.pid` 里那个包装进程。
- 页面根路径回 403 说明 Host 头不是 `127.0.0.1`/`localhost`，不是服务坏了。

值得驱动 = 工作区根对上 `$ROOT` **且** 就绪行对上端口 **且** 健康检查 `db: ok`。三条里有一条不成立，就不要往里写数据。

起服务前多问一句「这跑的是哪份代码」：读读服务的时间不代表二进制的时间。`stat -c %y` 看进程启动时间，`git rev-parse HEAD` 看当前提交；进程比 HEAD 老，就可能跑的是改造前的老代码（2026-11-12 有个常驻实例跑了七小时的前一代代码，所有新行为都不存在）。要验新行为，一律自己起。

## Drive

三种驱动方式，按功能文件里写的用，不要自创：

- **curl** 验页面骨架与接口契约：取 HTML 断言稳定标记，取 JSON 断言字段。`features/library-home.md`、`features/daily-lessons.md`、`features/layered-reads.md` 走这条。
- **pytest** 验离线契约：`uv run --frozen pytest tests/test_home_*.py`、`tests/test_reading_*.py tests/test_depth_acceptance.py`、`tests/test_daily.py tests/acceptance/daily/test_feed.py`。
- **直接跑脚本**验端到端：`python3 tests/acceptance/ops/check_ops.py`（末行 `168 项检查，失败 0 项` 与 `ALL PASS`）。它不是 pytest 模块（零个 `def test_`），用 `pytest` 跑会 `no tests ran` 并以退出码 5 失败，那是跑法错不是产品坏。
- **headless Chrome + CDP** 只用于点击、键盘、截图，脚本是仓库自带的，没有 Playwright。先设 `CHROME_BIN`（本机 `/usr/bin/chromium`），默认名 `google-chrome-stable` 在这台机器上不存在：
  - `python3 tests/manual/demo/shot.py --url 'http://127.0.0.1:18765/classic?q=...&instant=1&top=1'`，末行 `CONSOLE ERROR 0`。
  - `python3 tests/manual/demo/check_session_ui.py --base http://127.0.0.1:18765`，末行 `ALL PASS`；它依赖既有会话，临时根上也能命中（会话库共享）。
  - `PYTHONPATH=$PWD/tests/acceptance/ops python3 tests/manual/ops/shot_ops.py`，自己起临时根；末行 `SHOT PASS`、`CONSOLE ERROR 0`，八个页签含账表（2026-11-13 实测）。

硬规则：

- 每个功能文件里的命令逐字复制，不改引号与参数。
- 页面动作和接口断言是两种证据，别用后者冒充前者。只验了接口没走页面，记 `ui-unverified`。
- 生成读本、跑真实作答都会打模型（`opencode-go/deepseek-v4.1-flash`）；`--no-annotate` 只关标注，不关生成。离线可验的部分先跑完，再决定要不要花这次调用。
- 演示页的会话库在仓库内 `apps/demo/缓存/sessions/`，不随 `BOOKS_ROOT` 隔离，写进去就是写进操作员的会话列表。

## Evidence

证据落在仓库的 `artifacts/verify/<RUN_ID>/<feature>/`（已 gitignore），按功能分子目录。一个目录里至少要有：

- `commands.txt`：逐条命令 + 退出码 + 关键输出（原样，不摘写）。
- 页面证据：`curl` 下来的 HTML 片段或 CDP 截图；截图里要能认出是哪个页面。
- 接口证据：JSON 的完整响应，或至少被断言的字段原值。
- 变更类动作：动作前的状态、动作后的状态，外加一次独立的只读复核（例如再取一次 `/api/feed`）。

判定标准：走的是真实用户路径，不是测试专用入口；同时留下动作与结果；副作用（文件、账、队列）要单独看一眼。跑红了一项就把末行连同失败的条目一起存，别只存「差不多」。

## Cleanup

按记录的 PID 杀，不按进程名杀：

```
for f in "$ROOT"/run/*.pid; do kill "$(cat "$f")" 2>/dev/null; done
```

- 拉起过的服务（首页自动起的操作台）不在 `run/*.pid` 里：`ss -ltnp | grep :8770` 拿到 PID，记进 `$ROOT/run/autostart.pid` 再杀。
- 杀完确认 `ss -ltnp | grep -E ':(18765|18770|18771|18772|18773|18774|18775)\b'` 为空；`uv run` 与首页拉起的那类子进程会随父进程一起退出，但端口空才是判据。
- 删工作区：`rm -rf "$ROOT"`。**不要**删 `artifacts/`，清理只删实例与临时根。
- 删完 `ls artifacts/verify/<RUN_ID>/<feature>/` 还在，才算这轮收干净。
- 绝不碰 8765 / 8771 / 8772 / 8773 上的任何进程，哪怕它看起来卡住了。

## Helpers

本技能不额外附脚本：启动、体检、驱动、清理的命令都内联在上面或功能文件里，直接复制执行。功能性驱动复用仓库自带的 `tests/manual/**` 与 `tests/acceptance/**`，不另造一份。

## Feature map

五个用户面各一份：`features/library-home.md`、`find-a-passage.md`、`layered-reads.md`、`daily-lessons.md`、`manage-library.md`。
地图是维护目标：行为变了先改地图，再改这条验证；`features/README.md` 记着当前已知红与未映射的 CLI 面。

改动本文件后，在 Pi 里 `/reload` 让技能重新加载；Pi 只在从仓库目录（或其子目录）启动时才发现项目 `.agents/skills/`。
