# 书库首页

首页是书库的唯一入口：用户先看到三张卡（找一段 / 翻开书 / 往期日课）、右栏最新一期和页脚管理，
点哪张就进哪个服务。目标没在跑时首页负责拉起它，拉不起来就把原因、日志和命令摆出来。

## Sub-features

- `home-open` 打开即用：只读 `books/`、`日课/` 与仓库模板，不依赖别的服务在线；`books.toml` 必需。
- `home-entry-search` 「找一段」卡。
- `home-entry-reading` 「翻开书」卡。
- `home-entry-daily` 「往期日课」卡。
- `home-entry-ops` 页脚「管理」，新标签打开。
- `home-keyboard` 按 `1` `2` `3` 走同一跳转。
- `home-today` 右栏最新一期：当天有日课标「今日日课」；有期但不是当天标「最近一期」；**一期都没有时仍标「今日日课」并显示「尚无」**（`apps/home/overview.py:110-118`，`tests/test_home_overview.py:116` 钉的就是这一支）。带期号、书名与标题。
- `home-link-mode` 目标地址是默认值时卡片指 `/go/<key>`，改动过就直连目标 URL。判据两边不一样：**卡片**比完整 URL（`apps/home/server.py:225`），**`/go/<key>` 拉起**只比端口（`apps/home/launcher.py:94`），所以 `--ops-url http://localhost:8770` 会得到「卡片直连、/go 仍会拉起」的混合态，别把两者当等价。
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

- 工作区按 [README](./README.md) 建好。
- 本轮起三个首页实例，都指向同一个 `$ROOT`：
  - `--port 18773` 带 `--no-start` 且显式传 `--search-url http://127.0.0.1:18765 --reading-url http://127.0.0.1:18771 --daily-url http://127.0.0.1:18772 --ops-url http://127.0.0.1:18770`；
  - `--port 18775` 不带任何 `--*-url`、不带 `--no-start`（跑默认地址）；
  - `--port 18774 --no-start` 也不带 `--*-url`（只用来验 503）。
- 18765 / 18771 / 18772 在跑；验 503 那一步（18774 点「管理」）要 8770 没在跑，验拉起那一步（18775 点「管理」）要 8770 空着。
- **验默认跳转前先确认 8765 上坐着操作员的实例。** 18775 带自动拉起：8765 空着时点「找一段」会在 8765 起一个指向 `$ROOT` 的演示页，那是本轮进程，收尾要按端口找 PID 杀；而且它的会话写进共享会话库。宁可只读 302 的 `redirect_url`、不跟随。

- **首页本身。** `curl -s http://127.0.0.1:18773/ | grep -c 'data-key="[0-9]'` 输出 `3`（内联脚本里还有一个 `data-key="${event.key}"`，不带数字过滤会数到 4），页面同时含「找一段」「翻开书」「往期日课」。
- **最新一期。** 写一期非当天的日课（样张见 [daily-lessons.md](./daily-lessons.md)），再取首页。`curl -s http://127.0.0.1:18773/ | grep -c '最近一期'` 输出 `1`，同一段含 `第 001 期`、书名与 `headline`、日期 `2026.11.10`；当天日课存在时这个标签是「今日日课」，所以别把「今日日课」写死进断言。
- **自定义目标直连。** `curl -s http://127.0.0.1:18773/ | grep -o '<a class="row" href="[^"]*"'` 输出三条 `http://127.0.0.1:18765` / `…71` / `…72`，主机后没有尾斜杠，说明改过地址后卡片不再走 `/go/`。
- **默认目标跳转。** `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:18775/go/search` 输出 `302 http://127.0.0.1:8765/`。
- **拉起。** 在 18775 上点「管理」（8770 没在跑，拉起的命令带 `--root $ROOT`）。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18775/go/ops` 输出 `302`，几秒内 `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8770/` 变成 `200`；`ss -ltnp | grep :8770` 拿到的进程命令行带 `--root $ROOT`，父进程是 18775 那个首页。收尾把 PID 记进 `$ROOT/run/autostart.pid` 再杀。
- **不拉起 + 503。** 在 18774（`--no-start`、默认地址、8770 没在跑）上点「管理」。`curl -s -o /tmp/home-ops.html -w '%{http_code}\n' http://127.0.0.1:18774/go/ops` 输出 `503`，标题「没有自动启动」，含「重试」「回首页」两个链接和日志路径，**没有**「命令：」段。要跑的命令只出现在拉起**失败**的 503 上。
- **键盘。** 在页面上按 `1`。等价命令 `curl -s -o /dev/null -w '%{redirect_url}\n' http://127.0.0.1:18775/go/search` 的地址与点卡片一致；键盘只在浏览器里按键时才成立，用 CDP 才拿得到 `keydown` 证据，本面记 `ui-unverified`。
- **Host。** `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: example.com' http://127.0.0.1:18773/` 输出 `403`。
- **离线回归。** `uv run --frozen pytest -q tests/test_home_*.py` 全过。

## Gotchas

- 卡片指不指 `/go/<key>` 取决于目标地址是否等于默认值。改了端口之后卡片是普通链接，拉起和 503 都不会出现；要验拉起必须用默认地址。
- 默认目标里 8765 / 8771 / 8772 被操作员的实例占着，`/go/reading` 之类会直接跳到操作员实例上，验证时不要点。8765 若空着，`/go/search` 会在 8765 起一个本轮实例——别在 8765 上验「跳转」。
- 唯一能安全验拉起的默认端口是 8770：它空着，且拉起命令会带首页自己的 `--root`，所以起的是临时根的操作台。
- `--no-start` 下目标没在跑返回 `503`，不是 `502`，也不是空白页。
- 503 有两种：`--no-start` 的那种只解释「被关掉了」并给日志路径（**没有命令段**）；拉起失败的那种才把原因、日志和命令都摆出来。
- 拉起失败的原因、日志路径和命令都在 503 页里；日志落在 `$XDG_CACHE_HOME/book-corpus/home/<key>.log`（默认 `~/.cache`）。
- 拉起演示页时不传 `--root`，改写 `BOOKS_ROOT`；这也是演示页唯一的数据根开关。
- 首页要读 `books.toml` 与仓库模板：`books.toml` 缺失或 `books/` 不在时进程直接退出（exit 2），不是渲染成空页。`--root` 要指到含这两样的工作区。
