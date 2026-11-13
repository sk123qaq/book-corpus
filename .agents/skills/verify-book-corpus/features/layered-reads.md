# 分层读

分层读把一本书读成可读的读本：先给这本书定一个读法，再按三档（速览 / 标准 / 深读）生成，
生成后在读本页按五层（结构模型 / 面板 / 速览 / 标准 / 深读）逐层看这一章是怎么被读出来的。
「四读」与「研究档」是旧名字：研究档已退役，旧链接 `?tier=research` 会被 302 到深读。

## Sub-features

- `read-shelf` 书架：可读的书、接着读、目录树。
- `read-catalog` 书目：书名/作者/分类/章名搜索，学科筛选，已出片标记。
- `read-book` 书页：章列表、当前章、书级读法票（只读，生成入口不在这一页）。
- `read-profile` 书级读法：叙事 / 论证 / 实用 / 资料·图册（短名「资料」，代码里 `reference` 的标签就是这个，不是「参考」）。未确认时章页生成按钮禁用，生成接口回 `409 reading_profile_required`。
- `read-chapter` 章页：读法选择（跟随书级 / 本章覆盖 / 回到跟随）、三档勾选、生成、出片清单。
- `read-tiers` 三档读本：速览 / 标准 / 深读。
- `read-depth` 五层：结构模型 / 面板 / 速览 / 标准 / 深读，逐层状态与费用估价。
- `read-path` 路径标记：深度路径 / 旧路径 / 未生成。
- `read-reader` 读本页：`/reader/<uid>/<n>` 与 `/reader/<uid>/<n>/depth`。
- `read-records` 生成记录：队列、历史、出片。错误类别显示中文（`quality` → 「未过硬门槛」），原值留在 `title` 里。
- `read-quality` 速览质量门：正文汉字必须落在源文的 15%–25%，越界只返修一次；返修仍不过就整层失败，`error_kind` 是 `quality`。
- `read-failure` 失败现场：每个失败任务落一份 JSON，记下 error、stats 与模型原始输出。
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
- **三档与五层。** 同一响应里 `depth.layers` 是**对象**不是数组，键就是五层：`model`/`panels`/`skim`/`standard`/`deep`，每值带 `status`、`updated_at`、`usage`、`tokens`、`usd`、`error`、`estimate`（`{"tokens", "usd"}`）。按 `for layer in depth["layers"]` 只会拿到五个字符串键。样书第一章：`model` 5129 token、0.0074 美元；`panels` 1795、0.0026；`skim` 1890、0.0023；`deep` 3077、0.0044；`standard` 0 token、0 美元。
- **读法闸门（新前置）。** 没有书级读法时，生成入口一律 409。`curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/01 -H 'Content-Type: application/json' -d '{"layers":["skim"]}' -w '\n%{http_code}\n'` 输出 `{"error": "这本书还没有读法，请先确认书级默认读法", "code": "reading_profile_required", "uid": "B0001"}` 与 `409`。
- **定读法。** 在章页选「论证」并设为书级默认，等价于 `curl -s -X PUT http://127.0.0.1:18771/api/chapter/B0001/01/reading -H 'Content-Type: application/json' -d '{"action":"book_default","mode":"argument"}'`，返回 200，字段 `book_mode: "argument"`、`chapter_override: null`、`effective_mode: "argument"`、`effective_source: "book"`，另带一条规则建议 `suggestion`（样书建议 `narrative`，`confidence` 0.79，`reason` 含「文学类；对话占比 12%」）。
- **读法落库后的读法。** 再取 `/api/chapter/B0001/01`：`reading.book_mode` 为 `argument`、`reading.effective_source` 为 `book`；顶层**没有** `book_mode` 这个键（实测 2026-11-12：不是在不在，是压根不存在），旧地图写的「仍是 null」会让人拿 `data.get("book_mode") is None` 去断言，两个情况长得像但不能互相代替。
- **读本页在生成前不存在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01` 输出 `404`；`curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -o '<title>[^<]*'` 输出 `<title>404 · 分层读`。**深读页不是这个 URL**：生成后它在 `/reader/<uid>/<n>/depth`，实测 `404` → `200` 且 `<title>阿Q正传 · 第一章 序 · 深读`；裸 URL 仍 `404`，因为它对应「标准档导出」不是深读本。按裸 URL 断言会把成功读成失败。
- **旧档位被改写而不是报错。** `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' 'http://127.0.0.1:18771/reader/B0001/01?tier=research'` 输出 `302 http://127.0.0.1:18771/reader/B0001/01?tier=deep`；`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/export/B0001/01/research` 输出 `410`。
- **深度层只在 POST 上。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/depth/B0001/01` 输出 `405`，响应 `{"error": "该路由不支持 GET"}`。
- **记录页。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/records` 输出 `200`；`curl -s http://127.0.0.1:18771/api/jobs` 返回 `{"jobs": [...]}`，每条带 `id`/`kind`/`state`/`phase`/`layer`/`steps`/`progress`/`layers_ready`/`skipped`/`failed_layer`/`error_kind`/`attempts`/`elapsed_ms`，失败任务另带 `failure_artifact`（现场文件的相对路径，可能是 `null`）与 `failure_artifact_error`（写现场失败时的原因，成功时为 `null`）（`apps/reading/jobs.py:368-379`、`:439-447`）。页面把 `error_kind` 译成中文再显示，`quality` → 「未过硬门槛」、`unexpected` → 「未分类异常」、`provider` → 「提供方失败」、`transport_other` → 「连接异常」（`apps/reading/web/assets/app.js:167-190`），原值只进 `title`（`records.html:225`）。这段是客户端模板，`curl` 拿到的 HTML 只有模板源码，看不到「未过硬门槛」；验证渲染要上 CDP，或直接核 `RD.errorKindName` 的映射表。单条任务的原始账：`curl -s http://127.0.0.1:18771/api/jobs/<id>/events` 返回 `{"job", "path", "events"}`，`events` 是 JSONL 解出的数组，`path` 指向 `$ROOT/工作区/阅读/logs/<id>.jsonl`。2026-11-12 实测一条流式任务里能看到 `http_start`（`stream: true`、`bytes_out`）与两条 `http_response`（`first_byte_ms` 1778 / 1478、`first_delta_ms` 74641 / 114415、`chunks` 499 / 138、`peer` 是 IPv6）。
- **提交一档（只到受理）。** 定完读法后重发上面那条 POST，返回 `202` 与一条任务 `{"id": "r-…", "kind": "depth", "state": "queued"}`。**服务会自行插入结构层**：请求 `{"layers":["skim"]}` 落账后 `layers` 是 `["model","skim"]`。
- **两条 POST 分工不同，别拿一条推另一条。** `/api/depth/<uid>/<n>` 尊重请求里的 `layers`（但会自动补上 `model`）；`/api/jobs`（章页「生成读本」按钮走的那条）在深度默认开启且没有旧 manifest 时，会把请求改写成**五层全排**的 depth 任务，请求里的 `tiers` 直接丢掉（`READING_DEPTH` 默认 `1`，`apps/reading/server.py:361-383`）。所以新章上勾「速览」再点生成，得到的是五层任务，不是单档读本。
- **深度生产面板。** 章页下半部分有逐层面板（`apps/reading/web/chapter.html:335-400`）：每层一个状态 pill 与「估算 … tokens / $… · 实际 …」，带「只生成此层」/「重试」/「只重建结构模型」与「生成所选层（保留 N 层）」；它打的是 `/api/depth`，不是 `/api/jobs`。默认只勾缺失/过期/失败层，所以「重跑」通常意味着「只补缺的那几层」。
- **导出只读旧三档目录。** `/api/export/<uid>/<n>/<tier>` 读 `pack_dir/export/<tier>.html`，深度任务不写这个目录，纯深度的章会 404「还没有这一档」；要验导出去走旧三档路径。
- **一次生成真的会成（2026-11-12 实测）。** 同一条 `layers: ["skim"]` 的任务 `r-009ebedee5` 最终 `state done`、`progress 100`、`elapsed_ms 201521`、`layers_ready ["model","skim"]`、`pack_id B0001/ch/01`；六步为 结构 done / 面板 pending / 速览 done / 标准 pending / 深读 pending / 渲染 done。章页随之 `path: depth`、`depth.state: ready`、`tiers.skim.exists: true`，`/reader/B0001/01/depth` 200。盘上 `$ROOT/工作区/阅读/B0001/ch/01/depth/state.json` 是 schema 2，带 `layer_sha`/`layer_model_sha`/`layer_mode`/`layer_request`/`layer_updated_at`（只有真写过的层有时间戳，实测 `{model: …19:10:01, skim: …19:11:58}`），`layer_degraded` 只在面板退化时出现，`layers_ready` **不落盘**，由读时按文件 + sha 算。
- **续跑只补缺层。** 再发 `{"layers":["standard"]}` → 任务 `r-15aedb0177` 用 `elapsed_ms 12` 结束，`skipped` 为空（`model` 不在请求里，不算 skip），`layers_ready` 变 `["model","skim","standard"]`，日志里三条 `layer_commit`、零次模型调用，`tiers.standard.exists` 转 `true`。要验「已完成层不被重做」，这 12 ms 就是证据。
- **质量门与失败现场。** 速览层要求正文汉字（lead + claims.text + turn，`detail_index` 不计）占源文的 15%–25%（`src/bookcorpus/reading/depth/skim.py` 的 `MIN_RATIO`/`MAX_RATIO`），提示词里带 `budget{source_han, body_min, body_max, unit}`，源文 10,150 字就是 1,523–2,537。比例越界且不是叙事档时只返修一次，回灌上次 JSON 与缺口；再不过就整层失败，`error_kind: "quality"`，`error` 形如「连续 2 次未通过速览校验：正文汉字 1340 字（13.2%）越界（15%–25%）：下限 1523 字、上限 2537 字」。失败现场落 `$ROOT/工作区/阅读/failures/<job>-<layer>.json`，字段 `job_id`/`uid`/`n`/`layer`/`error_kind`/`error`/`stats`/`raw_payload`/`failed_at`，只留最新 200 份；`raw_payload` 超 200 KB 时置 `null` 并记 `payload_omitted: true`。结构审计失败也是 `quality`，但 `stats` 为空、不回灌原文，不要拿比例读数去解释它。
- **离线回归。** `uv run --frozen pytest -q tests/test_reading_*.py tests/test_depth_acceptance.py` 全过。
- **形式化。** `uv run --frozen python verification/chapter_path/check_paths.py --root "$ROOT"` 输出 `checked 128 disk states; 24 reachable by the model`、`P1 AdvertisedDepthIsReadable: no violation`、`P2 AuditPathMatchesService: no violation`，exit `0`。这是 P2 之后的值（`partial` 进了状态机）；旧地图的 96 / 16 是加状态之前的样子，别拿它当回归。

## Gotchas

- **409 是前置，不是接口坏了。** 书级读法没定，生成按钮和两个 POST 都拒绝；先在章页选读法或 PUT `book_default`，不要急着报 bug。
- 顶层没有 `book_mode` 键（不是 `null`），读法状态全在 `reading` 块里（`reading.book_mode` / `reading.effective_mode`）。
- `?tier=research` 是 302 不是 404 也不是 400。按 URL 猜档位会误判成「研究档还在」，要用 `-L` 或断言 `redirect_url`。
- `--no-annotate` 只关标注。生成与提交任务仍然打模型。没有可用模型 key 时任务会 `failed`，但那不是默认结局：有 key 时实测 `done`（见上）。失败时 `error_kind` 是分类值：`quality`（内容没过速览质量门）、`unexpected`（未分类异常）、`provider` / `transport_closed` / `transport_timeout` / `transport_other` / `auth` / `protocol` / `truncated` / `config` / `rate_limited` / `http_5xx` / `http_4xx`（模型与传输层），以及 `restart` / `cancelled`（服务重启、人工取消）。记录页把这些译成中文，`quality` 显示「未过硬门槛」——它表示模型写得太短或太长，不是程序崩了。按 `error_kind` 判因，别按 `error` 文本猜。
- 样书第一章 `standard` 估价是 0 token / 0 美元；别拿这一条推别的书或别的章。
- 8771 是操作员常驻实例的端口。验证必须换端口起自己的，看到 `root` 不是 `$ROOT` 就是打错了实例。
- 章号是两位字符串（`01`），来自文件名前缀；书里没有 md 的章不进章节表。
- 生成会覆盖同档旧读本，页面上先弹确认；自动化里点不到确认框，任务不会开始。这条只对旧三档 `/api/jobs` 路径成立：`/api/depth` 提交**没有**覆盖确认，靠「只勾缺层」与 `fresh()` 跳过已完成层，所以逐层重跑在自动化里能直接跑起来。
- `quality` 不是崩溃，重跑前先看 `failure_artifact`：现场文件里的 `raw_payload` 能直接告诉你模型写了多少字、哪几节偏短。拿 `error` 里的百分比去解释一个 `stats: {}` 的审计失败会误判。速览比例只认 lead/claims/turn，把 detail_index 也算进去会把 13% 算成 30%。
- 生成记录里同一章可能同时有多个档位的任务，按任务的 `tiers` 区分，不要按章号去重。
