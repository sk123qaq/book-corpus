# 往期日课

日课每天从书库里挑一章，生成一期短文：书名、章名、正文、一句原文引用，加一段「为什么挑它」。
往期页把 `<root>/日课/` 下的期按日期倒排列出来，点一期读全文。

## Sub-features

- `daily-list` 往期列表：按期倒排，带期号、日期、书名。
- `daily-issue` 单期页：正文、引用、为什么挑它、研究记录。
- `daily-empty` 空目录的空状态。
- `daily-skip-bad` 坏文件跳过，不整页失败。
- `daily-contract` 文件名必须等于文件里的 `date`。
- `daily-api` `/api/feed` 与 `/api/issue/<date>`。
- `daily-cache` 响应不缓存。
- `daily-generate` 生成一期（要模型，`--dry-run` 只出提示词）。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18772/`，这是往期列表。
- 点某一期读全文。
- 从首页「往期日课」卡进来是同一页。

## Driving it with curl + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好。
- 日课服务由本轮起：`python3 apps/daily/server.py --root $ROOT --port 18772`，启动只打印 `http://127.0.0.1:18772` 一行。
- 没有现成日课可抄：`book-corpus daily --dry-run` 只把提示词打到 stdout，不落盘。要验「有内容」就手写一期，键名照下面这份：

```json
{
  "version": 1,
  "date": "2026-11-10",
  "kind": "fiction",
  "kindLabel": "小说",
  "subject": "文学",
  "book": {"uid": "B0001", "title": "阿Q正传", "author": "鲁迅"},
  "chapter": {"index": 1, "title": "第一章 序", "path": "books/B0001/md/01_第一章序.md"},
  "headline": "未庄有人姓赵了",
  "body": ["第一段。", "第二段。", "第三段。"],
  "quote": {"text": "本章原文逐字摘录", "line": 1},
  "why": "为什么挑这一章",
  "research": {"chapters": "查了第 1 章", "web": 0, "queries": []}
}
```

- 文件名必须是 `<date>.json`，`date` 与文件名不一致会进 `errors`，不进 `issues`。

- **空目录。** 先不写任何期。`curl -s http://127.0.0.1:18772/api/feed` 输出 `{"today": "...", "dir": "$ROOT/日课", "issues": [], "errors": []}`，页面 `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18772/` 输出 `200`，展示空状态。
- **有内容。** 写入上面那期后 `curl -s http://127.0.0.1:18772/api/feed` 的 `issues` 长度变 `1`，带 `headline` 与 `book.title`。
- **单期。** `curl -s http://127.0.0.1:18772/api/issue/2026-11-10` 返回同一期；`curl -s http://127.0.0.1:18772/api/issue/2000-01-01` 返回未找到。
- **坏文件。** 放一个 `2026-11-11.json` 但把 `date` 写成 `2026-11-12`，再取 `/api/feed`：`issues` 仍是 `1`，`errors` 多一条带文件名的原因。
- **不缓存。** `curl -s -D - -o /dev/null http://127.0.0.1:18772/api/feed` 的响应头含 `Cache-Control: no-store` 与 `X-Content-Type-Options: nosniff`。
- **Host。** `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: example.com' http://127.0.0.1:18772/` 输出 `403`。
- **离线回归。** `uv run --frozen pytest tests/test_daily.py tests/acceptance/daily/test_feed.py` 全过。
- **生成。** `uv run --frozen book-corpus daily --root "$ROOT" --dry-run` 把提示词打到 stdout、exit `0`、不写文件；去掉 `--dry-run` 才生成一期（打模型，本次未跑）。

## Gotchas

- `--dry-run` 不产生任何文件，不能拿它当 seed，也不能用它证明列表能渲染。
- 期号与文件名绑定：`date` 和 `<date>.json` 不一致时整期被跳过，页面看起来只是「少了一期」。
- 目录里只有坏文件时页面是空状态加错误，不是 500；断言要看 `errors`，别只看 `issues.length`。
- 引用里的 `line` 由程序按原文复核改正，手写 seed 填错行不会让整期失效。
- 日课只认 `127.0.0.1` / `localhost` 的 Host 头，`--port` 改了也一样。
- 页面是静态壳，`/` 之外没有 `app.js` 之外的静态路由；别去请求 `/issue/2026-11-10` 这类页面路径。
