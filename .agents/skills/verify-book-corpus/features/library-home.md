# 书库首页

失效源：apps/home/** src/bookcorpus/app_links.py src/bookcorpus/envfile.py src/bookcorpus/reading/library.py src/bookcorpus/scripts/layout.py

首页是书库的唯一入口：用户先看到三张卡（找一段 / 翻开书 / 往期日课）、右栏最新一期和页脚管理，
点哪张就进哪个服务。目标没在跑时首页负责拉起它，拉不起来就把原因、日志和命令摆出来。

## Sub-features

- `home-open` 打开即用：只读 `books/`、`日课/` 与仓库模板，不依赖别的服务在线；`books.toml` 必需。根解析分两步：`apps/home/server.py` 先用 `envfile.resolve_books_root(args.root)`（显式 `--root` > `BOOKS_ROOT` > `$BOOK_CORPUS_ENV`/仓库 `.env`，不再回退 `~/书库` 或已忽略的旧环境变量）；`Library(root)` 再调用 `find_root(root, ignore_env=True)`，因此显式 `--root` 优先级高于 `BOOKS_ROOT`。书架统计与书目直接来自 `Library.books()`，按 `READING_SHELF_TTL` 重新核对盘上（默认 2 秒），新落地或删掉的书不重启首页就会跟。
- `home-entry-search` 「找一段」卡。
- `home-entry-reading` 「翻开书」卡。
- `home-entry-daily` 「往期日课」卡。
- `home-entry-ops` 页脚「管理」，新标签打开。
- `home-keyboard` 按 `1` `2` `3` 走同一跳转。
- `home-today` 右栏最新一期：当天有日课标「今日日课」；有期但不是当天标「最近一期」；**一期都没有时仍标「今日日课」并显示「尚无」**（`apps/home/overview.py:107-118`，`tests/test_home_overview.py:116` 钉的就是这一支）。带期号、书名与标题。
- `home-link-mode` 目标地址是默认值时卡片指 `/go/<key>`，改动过就直连目标 URL。**卡片**比完整 URL（`apps/home/server.py:234`，先去掉尾斜杠）；**`/go/<key>` 拉起**除端口外还要求 scheme 是 `http` 或空、Host 是 `127.0.0.1`/`localhost`（`src/bookcorpus/app_links.py` 的 `is_local_target`）。所以 `--ops-url http://localhost:8770` 会得到「卡片直连、/go 仍会拉起」的混合态，别把两者当等价。
- `home-autostart` 默认目标没在跑时拉起它再跳。
- `home-degraded` 拉不起或 `--no-start`：503 页带原因、日志、重试。
- `home-host` 只认 `127.0.0.1` / `localhost` 的 Host 头。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18773/`（默认端口是 8773，18773 是本轮起来的那个）。
- 点三张卡中的任意一张，或在页面上按 `1` / `2` / `3`。
- 点页脚右侧「管理」。
- 看右栏的最新一期。

## Driving it with curl（本面还没有浏览器脚本）

Preconditions:

- 工作区按 [README](./README.md) 建好。传 `--root "$ROOT"` 时 `Library(root)` 会用 `ignore_env=True`，不再被 `BOOKS_ROOT` 盖掉；本轮仍统一 `export BOOKS_ROOT="$ROOT"` 并传 `--root "$ROOT"`，不改 `.env`。
- 本机只起 `uv run --frozen python apps/home/server.py --root "$ROOT" --port 18773 --no-start`，显式传 `--search-url http://127.0.0.1:18765 --reading-url http://127.0.0.1:18771 --daily-url http://127.0.0.1:18772 --ops-url http://127.0.0.1:18770`。503/自动拉起要另起 `18774`/`18775` 并让默认目标空着，本机不安全。
- 18765 / 18771 / 18772 在跑。**503 与自动拉起无法在本机安全 live 演练**：默认目标 8765/8771/8772/8770 都被常驻实例占着，不能停操作员实例；没有隔离机就记 `unexercised`，不要跑 18774/18775 的步骤。
- 只对显式 `--*-url` 的 18773 做直连检查；默认跳转与拉起只在隔离机测，绝不在 8765 上验。

- **首页本身。** `curl -s http://127.0.0.1:18773/ | grep -c 'data-key="[0-9]'` 输出 `3`（内联脚本里还有一个 `data-key="${event.key}"`，不带数字过滤会数到 4），页面同时含「找一段」「翻开书」「往期日课」。
- **最新一期。** 写一期非当天的日课（样张见 [daily-lessons.md](./daily-lessons.md)），再取首页。`curl -s http://127.0.0.1:18773/ | grep -c '最近一期'` 输出 `1`，同一段含 `第 001 期`、书名与 `headline`、日期 `2026.11.10`；当天日课存在时这个标签是「今日日课」，所以别把「今日日课」写死进断言。
- **自定义目标直连。** `curl -s http://127.0.0.1:18773/ | grep -o '<a class="row" href="[^"]*"'` 输出三条 `http://127.0.0.1:18765` / `…71` / `…72`，主机后没有尾斜杠，说明改过地址后卡片不再走 `/go/`。
- **自定义目标直连 `/go`。** 在 18773 上取 `/go/reading?to=<encoded /open?...>`，应为 `302` 到 18771 的 `/open...`；`/go/search?to=...` 应为 `302` 到 18765；`/go/nope` 应为 `404`；`/go/reading?to=//evil.com` 应为 `400`。这些分支不拉起服务，可安全跑。
- **默认跳转只读（`unexercised`）。** 隔离机上 18775 的 `/go/search` 应 `302`，响应头 `Location` 与 `curl` 的 `redirect_url` 都是 `http://127.0.0.1:8765/`（`DEFAULT_TARGETS["search"]` 本身不带尾斜杠，跳转拼接 `/` 后变成根路径斜杠）。本机 18775 不起，本轮不跑。
- **拉起与 503（`unexercised`）。** 需在默认目标空着的隔离机跑：18775 的 `/go/ops` 应 302 并在几秒内让 8770 变 200，进程命令行带 `--root $ROOT`；18774（`--no-start`）的 `/go/ops` 应 503，标题「没有自动启动」，含「重试」「回首页」和日志路径，**没有**「命令：」段。本机常驻实例占着默认端口，本轮不跑。
- **键盘。** 在 18773 页面上按 `1`，地址应与点「找一段」卡一致。键盘只在浏览器里按键时才成立，用 CDP 才拿得到 `keydown` 证据；只跑 curl 时记 `ui-unverified`。
- **Host。** `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: example.com' http://127.0.0.1:18773/` 输出 `403`。
- **离线回归。** `uv run --frozen pytest -q tests/test_home_*.py` 全过。

## Gotchas

- 卡片指不指 `/go/<key>` 取决于目标地址是否等于默认值。改了端口之后卡片是普通链接，拉起和 503 都不会出现；要验拉起必须用默认地址。
- 默认目标里 8765 / 8771 / 8772 被操作员的实例占着，`/go/reading` 之类会直接跳到操作员实例上，验证时不要点。8765 若空着，`/go/search` 会在 8765 起一个本轮实例——别在 8765 上验「跳转」。
- 拉起命令由 `src/bookcorpus/app_links.py` 生成，带目标服务的默认 `--port` 与首页的 `--root`；包括演示页在内的所有应用入口都以 `--root` 为最高优先级。本机 8770 已被操作员实例占用，不要为了验拉起去停它。
- `--no-start` 下目标没在跑返回 `503`，不是 `502`，也不是空白页。
- 503 有两种：`--no-start` 的那种只解释「被关掉了」并给日志路径（**没有命令段**）；拉起失败的那种才把原因、日志和命令都摆出来。
- 拉起失败的原因、日志路径和命令都在 503 页里；日志落在 `$XDG_CACHE_HOME/book-corpus/home/<key>.log`（默认 `~/.cache`），可用 `BOOK_CORPUS_HOME_LOG_DIR` 改到 `$ROOT/run/logs`，验证结束随 `$ROOT` 一起清（`src/bookcorpus/app_links.py`）。
- 拉起演示页时命令同时带 `--port` 与 `--root`，另外设 `BOOKS_ROOT`；会话与运行记录随 `--root` 落到 `$ROOT/工作区/检索/`。
- 首页要读 `books.toml` 与仓库模板：`books.toml` 缺失或 `books/` 不在时进程直接退出（exit 2），不是渲染成空页。`--root` 要指到含这两样的工作区。
- 显式 `--root` 确实是最高优先级：`apps/home/server.py:189`（`--root default=None`）与 `:209` 先走 `src/bookcorpus/envfile.py:111-116` 的 `resolve_books_root(args.root)`，`Library(root)` 再调用 `src/bookcorpus/scripts/layout.py:35-48` 的 `find_root(root, ignore_env=True)`，明确绕开 `BOOKS_ROOT`。不传 `--root` 时才依次读 `BOOKS_ROOT`、`$BOOK_CORPUS_ENV`/仓库 `.env`；已废弃的旧环境变量不再参与解析。本轮仍同时 `export BOOKS_ROOT="$ROOT"` 与传 `--root "$ROOT"`，避免直接跑其它脚本时撞到 `.env`。
