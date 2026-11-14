# 书库首页

把「找一段」「翻开书」「往期日课」收在一页，页脚留一个小的「管理」入口。默认只绑本机，服务端渲染，读 `books/` 与 `日课/`；点入口时如果目标服务没起，会代起再跳转。

首页是项目默认启动项，其余页面按需启动。

## 起

```bash
python3 apps/home/server.py --root /home/colaqaq/书库
```

打开 `http://127.0.0.1:8773`。

`--root` 指有 `books.toml` 的实例根；不传时依次读 `BOOK_CORPUS_DATA`、`BOOKS_ROOT`，再退回 `~/书库`。四个跳转地址都可以改：

```bash
python3 apps/home/server.py --root <实例根> \
  --search-url http://127.0.0.1:8765 \
  --reading-url http://127.0.0.1:8771 \
  --daily-url http://127.0.0.1:8772 \
  --ops-url http://127.0.0.1:8770
```

`--quiet` 关掉访问日志；`--port` 改端口，默认 8773。`--no-start` 关闭自动启动，`--start-timeout` 改等待秒数，默认 12。

## 点击与代起

- 页面里的入口默认指向 `/go/search`、`/go/reading`、`/go/daily`、`/go/ops`。
- 请求先进首页：端口已在听就直接 `302` 到目标；没在听就按白名单命令起进程，等端口就绪后再 `302`。
- 四个命令固定在 `launcher.py`，不接受页面传来的任意命令。演示页用 `BOOKS_ROOT`，其余用 `--root`。
- 子进程 `start_new_session=True`，不随首页退出；输出写到日志。
- 日志目录读 `BOOK_CORPUS_HOME_LOG_DIR`，默认 `$XDG_CACHE_HOME/book-corpus/home/`，再退回 `~/.cache/book-corpus/home/`，每个服务一个 `<key>.log`。
- 起不来时返回 `503`，页面上有原因、日志、命令和「重试」；已经在跑的服务点第二次不会重复起。
- 用 `--search-url` 等改到别的地址时，入口直接跳过去，不代起本地进程。

## 数据从哪来

- 只读实例根：`Library.books()` 取 `books/` 下有 `md/` 章节的书，可读数、章数、字数都从这里来。
- 不读旧数据仓的叠加层书目，也不读 `READING_OVERLAY`；首页上的数字就是实例根里真实可读的书。
- 今日夹页：`<实例根>/日课/*.json`，复用 `apps/daily/feed.py` 的校验与排序，取最近一期。今天没有新期时标「最近一期」；一期都没有时显示空状态，不报错。
- 页面渲染不调用 8765/8771/8772 的 HTTP API，只读文件。其他页面没起时，首页仍能打开。

## 边界

- 只绑 `127.0.0.1`，Host 只接受 `127.0.0.1` 或 `localhost`。
- 首页不写 `books/`、`books.toml`、`日课/`、`工作区/`；只写自己的启动日志，目标服务自己的缓存由各自负责。
- 不引入外部字体、CDN 或前端构建。CSS 和模板都在 `web/`。
- 「管理」也走 `/go/ops`，需要时同样代起 8770。

## 测试

```bash
uv run --frozen pytest tests/test_home_overview.py tests/test_home_server.py tests/test_home_launcher.py
```

`test_home_overview.py` 用临时实例测取数、转义与无日课空状态；`test_home_server.py` 起真实子进程，检查本机绑定、Host 拒绝、CSS、页面计数与 `/go` 404；`test_home_launcher.py` 测命令拼装、端口探测与不启动分支。
