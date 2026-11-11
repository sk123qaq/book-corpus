# 四读

四读把一本书拆成可读的四档读本：用户从书架、书目或书页进到某一章，挑一档生成，
然后在读本页按「结构模型 / 速览 / 标准 / 深读 / 研究 / 引用」逐层看这一章是怎么被读出来的。

## Sub-features

- `read-shelf` 书架：可读的书、接着读、目录树。
- `read-catalog` 书目：书名/作者/分类/章名搜索，学科筛选，已出片标记。
- `read-book` 书页：章列表、当前章、开始读。
- `read-chapter` 章页：四档勾选、生成、覆盖确认、出片清单。
- `read-tiers` 四档读本：速览 / 标准 / 深读 / 研究。
- `read-depth` 深度层：结构模型 / 速览 / 标准 / 深读 / 研究 / 引用，逐层状态与费用估算。
- `read-path` 路径标记：深度路径 / 旧路径 / 未生成。
- `read-reader` 读本页：`/reader/<uid>/<n>` 与 `/reader/<uid>/<n>/depth`。
- `read-records` 生成记录：队列、历史、出片。
- `read-export` 导出某一章的读本。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18771/`，这是书架。
- 点书墙上的「接着读」卡片。
- 顶部搜索框搜书名、作者、分类或章名；或点「书目」。
- 在书页点某一章，进章页勾档位生成。
- 生成完点读本链接，进 `/reader/<uid>/<n>`，再切深度层。
- 点顶部「记录」看队列与历史。

## Driving it with curl + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，样书 `B0001` 在 `books/` 下。
- 四读由本轮起：`python3 apps/reading/server.py --root $ROOT --port 18771 --no-annotate --quiet`，启动行是 `四读 8771 · root=<root> · http://127.0.0.1:18771/`。
- `--no-annotate` 关掉模型标注，草稿是确定性输出，离线可验；开标注会在生成时打模型。
- 尚未生成任何读本，用来验「未生成」这一侧；生成后再验「已生成」那一侧。

- **书架。** 打开首页。`curl -s http://127.0.0.1:18771/api/shelf` 里 `stats.readable` 为 `1`、`chapters` 为 `9`、`chars` 为 `21362`，`books[0].uid` 为 `B0001`，`recent[0].n` 为 `01`。
- **书目。** 点「书目」。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/catalog` 输出 `200`；`curl -s 'http://127.0.0.1:18771/api/catalog?q=%E9%98%BFQ'` 返回 `total` 与命中的 `B0001`。
- **书页。** 点一本。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/book/B0001` 输出 `200`；`curl -s http://127.0.0.1:18771/api/book/B0001` 的 `book.chapters` 为 `9`。
- **章页。** 点第一章。`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/chapter/B0001/01` 输出 `200`。
- **未生成的章。** `curl -s http://127.0.0.1:18771/api/chapter/B0001/01` 里 `path` 为 `none`、`fallback_reason` 为 `尚未生成`，四档 `tiers.*.exists` 全是 `false`，`depth.state` 为 `missing`。
- **深度层与估价。** 同一响应里 `depth.layers` 六层都在：`model` / `skim` / `standard` / `deep` / `research` / `citations`，带 `estimate.tokens` 与 `estimate.usd`（样书第一章 `skim` 约 1890 token、0.0023 美元，`deep` 约 3077 token、0.0044 美元）。
- **读本页在生成前不存在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01` 输出 `404`，页面标题是 `404 · 四读`，正文「没有找到」加一句「这一章还没……」。
- **只在 POST 上的接口。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/depth/B0001/01` 输出 `405`，响应 `{"error": "该路由不支持 GET"}`；生成只能从章页发起。
- **记录页。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/records` 输出 `200`；`curl -s http://127.0.0.1:18771/api/jobs` 返回队列。
- **生成一档。** 在章页勾「标准」再点生成。这一步起后台任务、打模型，本次未跑；跑完 `/api/chapter/B0001/01` 的 `tiers.standard.exists` 变 `true`，`/reader/B0001/01` 从 `404` 变 `200`。
- **离线回归。** `uv run --frozen pytest tests/test_reading_server.py tests/test_depth_acceptance.py` 全过。
- **形式化。** `uv run --frozen python verification/chapter_path/check_paths.py` 输出 `checked 96 disk states; 16 reachable by the model`，`P1 AdvertisedDepthIsReadable: no violation`、`P2 AuditPathMatchesService: no violation`。

## Gotchas

- 8771 是操作员常驻四读实例的端口。验证必须换端口起自己的，看到 `root` 不是 `$ROOT` 就是打错了实例。
- 不带 `--no-annotate` 时生成会打模型；离线验证先确认启动参数里有关掉标注。
- `/reader/<uid>/<n>` 在没有读本时是 `404`，不是空页；「章节存在」不等于「读本存在」。
- 深度层只在 POST 上，GET `/api/depth/...` 是 `405`，别把 405 当接口缺失。
- 章号是两位字符串（`01`），来自文件名前缀；书里没有 md 的章不进章节表。
- 生成会覆盖同档旧读本，页面上会先弹确认；自动化里如果点不到确认框，任务不会开始。
- 生成记录里同一章可能同时有多个档位的任务，按 `tiers` 字段区分，不要按章号去重。
