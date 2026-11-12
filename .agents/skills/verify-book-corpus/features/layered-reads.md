# 分层读

分层读把一本书读成可读的读本：先给这本书定一个读法，再按三档（速览 / 标准 / 深读）生成，
生成后在读本页按五层（结构模型 / 面板 / 速览 / 标准 / 深读）逐层看这一章是怎么被读出来的。
「四读」与「研究档」是旧名字：研究档已退役，旧链接 `?tier=research` 会被 302 到深读。

## Sub-features

- `read-shelf` 书架：可读的书、接着读、目录树。
- `read-catalog` 书目：书名/作者/分类/章名搜索，学科筛选，已出片标记。
- `read-book` 书页：章列表、当前章、书级读法票（只读，生成入口不在这一页）。
- `read-profile` 书级读法：叙事 / 论证 / 实用 / 参考。未确认时章页生成按钮禁用，生成接口回 `409 reading_profile_required`。
- `read-chapter` 章页：读法选择（跟随书级 / 本章覆盖 / 回到跟随）、三档勾选、生成、出片清单。
- `read-tiers` 三档读本：速览 / 标准 / 深读。
- `read-depth` 五层：结构模型 / 面板 / 速览 / 标准 / 深读，逐层状态与费用估价。
- `read-path` 路径标记：深度路径 / 旧路径 / 未生成。
- `read-reader` 读本页：`/reader/<uid>/<n>` 与 `/reader/<uid>/<n>/depth`。
- `read-records` 生成记录：队列、历史、出片。
- `read-export` 导出某一章的读本。
- `read-legacy` 旧入口：`?tier=research` → `302 ?tier=deep`；`/api/export/<uid>/<n>/research` → `410`。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18771/`，这是书架。
- 点书墙上的「接着读」卡片。
- 顶部搜索框搜书名、作者、分类或章名；或点「书目」。
- 在书页看这本书的读法票（只读），然后点某一章进章页。
- 在章页先确认读法（没有书级读法时生成按钮是灰的），再勾档位、点「按「<读法>」生成读本 →」。
- 生成完点读本链接进 `/reader/<uid>/<n>`，再切深度层。
- 点顶部「记录」看队列与历史。

## Driving it with curl + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，样书 `B0001` 在 `books/` 下。
- 分层读由本轮起：`uv run --frozen python apps/reading/server.py --root $ROOT --port 18771 --no-annotate --quiet`，启动两行：`分层读 8771 · root=<ROOT> · http://127.0.0.1:18771/`，随后一行「配置提示：缺少配置：MinerU OCR…；PaddleOCR…；MiMo…」。第二行是云端 key 缺失的常态，不是故障。
- `--no-annotate` 只关模型标注；**生成与提交任务照样调模型**，离线只能验到「任务被受理」这一层。
- 尚未生成任何读本，用来验「未生成」这一侧。

- **书架。** 打开首页。`curl -s http://127.0.0.1:18771/api/shelf` 里 `stats.readable` 为 `1`、`chapters` 为 `9`、`chars` 为 `21362`，`books[0].uid` 为 `B0001`，`recent[0].n` 为 `01`。
- **书目。** 点「书目」。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/catalog` 输出 `200`；`curl -s 'http://127.0.0.1:18771/api/catalog?q=%E9%98%BFQ'` 的 `total` 为 `1`，命中 `B0001`。
- **书页。** 点一本。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/book/B0001` 输出 `200`，标题是 `分层读 · 书`；这一页没有生成控件：`curl -s http://127.0.0.1:18771/book/B0001 | grep -c '生成读本'` 输出 `0`。
- **章页。** 点第一章。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/chapter/B0001/01` 输出 `200`；页面含读法选择器 `id="mode-root"`、`data-mode=`、`id="mode-follow"`、`id="mode-reset"`，以及被禁用的 `按「…」生成读本 →`。
- **未生成的章。** `curl -s http://127.0.0.1:18771/api/chapter/B0001/01` 里 `path` 为 `none`、`fallback_reason` 为 `尚未生成`；`tiers` 是三条数组（`key` 为 `skim`/`standard`/`deep`），`exists` 全是 `false`；`depth.state` 为 `missing`。
- **三档与五层。** 同一响应里 `depth.layers` 五层都在：`model`/`panels`/`skim`/`standard`/`deep`，各带 `estimate.tokens` 与 `estimate.usd`。样书第一章：`model` 5129 token、0.0074 美元；`panels` 1795、0.0026；`skim` 1890、0.0023；`deep` 3077、0.0044；`standard` 0 token、0 美元。
- **读法闸门（新前置）。** 没有书级读法时，生成入口一律 409。`curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/01 -H 'Content-Type: application/json' -d '{"layers":["skim"]}' -w '\n%{http_code}\n'` 输出 `{"error": "这本书还没有读法，请先确认书级默认读法", "code": "reading_profile_required", "uid": "B0001"}` 与 `409`。
- **定读法。** 在章页选「论证」并设为书级默认，等价于 `curl -s -X PUT http://127.0.0.1:18771/api/chapter/B0001/01/reading -H 'Content-Type: application/json' -d '{"action":"book_default","mode":"argument"}'`，返回 200，字段 `book_mode: "argument"`、`chapter_override: null`、`effective_mode: "argument"`、`effective_source: "book"`，另带一条规则建议 `suggestion`（样书建议 `narrative`，`confidence` 0.79，`reason` 含「文学类；对话占比 12%」）。
- **读法落库后的读法。** 再取 `/api/chapter/B0001/01`：`reading.book_mode` 为 `argument`、`reading.effective_source` 为 `book`；顶层 `book_mode` 仍是 `null`（旧字段，别拿它断言）。
- **读本页在生成前不存在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01` 输出 `404`；`curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -o '<title>[^<]*'` 输出 `<title>404 · 分层读`。
- **旧档位被改写而不是报错。** `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' 'http://127.0.0.1:18771/reader/B0001/01?tier=research'` 输出 `302 http://127.0.0.1:18771/reader/B0001/01?tier=deep`；`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/export/B0001/01/research` 输出 `410`。
- **深度层只在 POST 上。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/depth/B0001/01` 输出 `405`，响应 `{"error": "该路由不支持 GET"}`。
- **记录页。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/records` 输出 `200`；`curl -s http://127.0.0.1:18771/api/jobs` 返回 `{"jobs": [...]}`，每条带 `id`/`kind`/`state`/`stage`/`steps`。
- **提交一档（只到受理）。** 定完读法后重发上面那条 POST，返回 `202` 与一条任务 `{"id": "r-…", "kind": "depth", "state": "queued"}`。本轮实测：任务最终 `state: failed`、`error: "Remote end closed connection without response"`，章页仍 `path: none`、`tiers.*.exists` 全 `false`。离线的验收止于 202，别承诺读本落地。
- **离线回归。** `uv run --frozen pytest -q tests/test_reading_*.py tests/test_depth_acceptance.py` 全过。
- **形式化。** `uv run --frozen python verification/chapter_path/check_paths.py --root "$ROOT"` 输出 `checked 96 disk states; 16 reachable by the model`、`P1 AdvertisedDepthIsReadable: no violation`、`P2 AuditPathMatchesService: no violation`，exit `0`。

## Gotchas

- **409 是前置，不是接口坏了。** 书级读法没定，生成按钮和两个 POST 都拒绝；先在章页选读法或 PUT `book_default`，不要急着报 bug。
- 顶层 `book_mode` 在新接口下一直是 `null`，读法状态在 `reading` 块里（`reading.book_mode` / `reading.effective_mode`）。
- `?tier=research` 是 302 不是 404 也不是 400。按 URL 猜档位会误判成「研究档还在」，要用 `-L` 或断言 `redirect_url`。
- `--no-annotate` 只关标注。生成与提交任务仍然打模型，离线环境下任务会 `failed`（`Remote end closed connection without response`），这不是页面问题。
- 样书第一章 `standard` 估价是 0 token / 0 美元；别拿这一条推别的书或别的章。
- 8771 是操作员常驻实例的端口。验证必须换端口起自己的，看到 `root` 不是 `$ROOT` 就是打错了实例。
- 章号是两位字符串（`01`），来自文件名前缀；书里没有 md 的章不进章节表。
- 生成会覆盖同档旧读本，页面上先弹确认；自动化里点不到确认框，任务不会开始。
- 生成记录里同一章可能同时有多个档位的任务，按任务的 `tiers` 区分，不要按章号去重。
