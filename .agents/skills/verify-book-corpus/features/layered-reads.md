# 分层读（正文伴读工作台与操作日志）

失效源：apps/reading/** src/bookcorpus/reading/**

8771 把一本书拆成可读的章，默认给 100% 原文；知识地图与划词伴读按需调用模型。
当前没有书级读法、三档/五层读本、读本页或工单队列，`/reader/...`、`/api/depth/...`、`/api/jobs` 等旧路由一律 404。
从检索页、日课或其它页面带来的 `/open?uid=&path=&line=&q=` 会落到对应章，并在章页顶部显示来源条、给命中段加高亮。
2026-11-19 起章页按书的一级与二级分类（`book_types.py:171-237` 的 `(一级, 二级)`）自动给一套阅读视角（7 类），其中 6 类每类三张结构蓝图、`visual` 类 1 张蓝图，各加一个扩展动作；视角可在章页切换，只影响本章伴读工具，不改人账。

## Sub-features

- `read-shelf` 书架：`/` 调 `/api/shelf`，渲染书墙、目录表与「最近阅读」。统计是 `readable` / `chapters` / `chars` / `packs` / `notes` / `maps`；`packs` 现在指有旁批或有知识地图的章数，不是旧读本包。`recent` 取最近 40 条操作按 `(uid,n)` 去重、最多 4 条；没有操作时回退到第一本书的第一章，动作写作「开始阅读」、`updated_at: null`。书架按 `READING_SHELF_TTL` 重新核对盘上，默认 2 秒；新落地或删掉的书自动出现或消失，不用重启，`0` 只用于调试。
- `read-catalog` 书目与搜索：`/catalog` 调 `/api/catalog?q=&subject=`，按书名、作者、分类、章名搜；结果分书、元数据、章节、作者几组。`q` 为空时 `metadata` / `chapters` / `authors` / `subjects` 都是空数组，只靠 `subject` 也能筛。页内写明「段落级全文检索不在本期」，别拿它当全库检索。
- `read-book` 书页：`/book/<uid>` 调 `/api/book/<uid>`，首屏是封面、作者、章数/字数、分类与「翻开第 NN 章」，下面是章表（每章分钟数、是否有导图、旁批数、直读全文）和「随文阅读工具」。书页没有阅读视角选择器，`/api/book` 也没有 mode 字段；阅读视角选择器只在章页（见 `read-profile`）。未知 uid 返回 404。
- `read-open` 跨页定位：`GET /open?uid=<uid>&path=<books/.../md/...>&line=<int>&q=<引用>&from=search|daily|reading&query=<原问题>` 解析到章，302 到 `/chapter/<uid>/<nn>?from=...&query=...&hl=...&blocks=B01#blk-B01`；`q` 用来高亮，`line` 用来选块；`path` 可省略（落本书第一章），给了就必须是本书 `md/` 下的章节文件。`line` 不是纯数字时静默当没给，不报 400。未知 uid 404；非章节 path 或 `read_chapter` 返回 `None` 时均 302 到 `/book/<uid>?notice=...`（`locator.py:208-210`），不返 404。
- `read-chapter` 章页：`/chapter/<uid>/<n>` 调 `/api/chapter/<uid>/<n>`，正文按块 100% 渲染。静态锚点有 `#top-q`、`#night`、`#crumb`、`#wb-root`、`#selection-bar`、`#fn-return-bar`、`#fn-tip-popover`；渲染后有 `#wb-toolbar`、`#btn-map`、`#btn-terms`、`#btn-toggle-pins`（内含 `#pin-count`）、`#btn-model-box`、`#btn-drawer`、`#model-box`、`#map-drawer`（左侧书脊内的地图面板，不是弹窗）、`#wb-grid`、`#reader-body`、`#assist-drawer`、`#drawer-diagram`、`#drawer-scope-bar`、`#tab-scope-chapter`、`#tab-scope-selection`、`#btn-pin`、`#profile-select`、`#wb-profile-strip`、`#btn-typo`、`#typo-popover`、`[data-theme-toggle]` 与 `#drawer-*`。钉住的旁批渲染在段后的 `.pinned-notes`；蓝图全览是 `#zoom-modal`（点 `[data-bp-zoom]` 打开），里面的质检徽标是 `#zoom-qa`。块是 `#blk-<id>` / `.reader-block`，`/api/chapter` 的每块还带 `start_line` / `end_line`（1-based）与 `source_file`；段尾触发是 `.blk-trigger`，钉住的旁批是 `.pin-card` / `.pin-del`。从 `/open` 进来时还会渲染 `#arrival-banner`（来源条，含「定位到 B01」「解释这段」「带原问题追问」「去书库重搜」）、`mark.hl-hit`（命中文字）和 `.reader-block.hit`（命中块）。没有 `#depth-panel`，也没有工单、三档、五层、读法提示这些 DOM；e80564c 已删掉 `app.css` 里的 `.depth-*`，`.rec-tiers` 与 `.path-badge-depth` 仍在别的样式文件里，不能拿 CSS 名字当页面证据。章页脚本已拆到 `apps/reading/web/assets/chapter.js`，断言 DOM，不要断言 HTML 里内联的函数名。
- `read-footnotes` 脚注交互：`mdparse.py` / `footnotes.py` 识别 EPUB/PDF 转出残留的各类行内注标（`[^[25]^]()`、`^[[14]]()^`、`^[1]^`、`^(1)^`、`^【1】^`、`^①^`、`[[1]]()`、`[^1]` 及带 `.md` 目标的外链注）与章内/章末注释定义，去掉原始转档噪声并在 `/api/chapter` 的块 `fn_refs` / `item_fn_refs` 与章级 `footnotes` 返回配对与跨文件状态（`local` / `external_chapter` / `external_candidate` / `unresolved`）。正文内渲染为可点上标 `.fn-ref`，本章节可定位的注点击后跳到对应 `.reader-footnote-item` 并高亮、同时在底部浮出 `#fn-return-bar`（`#btn-fn-return`「↩ 返回正文」）；注段右侧另有 `.fn-back-inline`「↩ 返回正文」，点击均返回原正文注释点并闪光提示。正文与注释分属不同文件或本章无对应注文时，`.fn-ref.is-external` 点击后在附近弹出 `#fn-tip-popover` 提示用户；对 `external_chapter` 与可定位 `external_candidate`，按钮文案为「新标签直达注 [...] ↗」（或「新标签直达注 [...]（第 NN 章）↗」），链接带 `?from=footnote&fn=...&src_n=...#blk-<id>` 在新标签页直跳目标注释块并用「来自正文注释」来源条与 `.fn-active-def` 高亮落点；仅当无法精确定位具体块时退回章节链接或全书目录按钮。
- `read-preflight` 章页门控：`/api/chapter` 的 `preflight.chapter_map` 恰好是 `{supported, reason, can_force, stats}`，`stats` 是 `{blocks, chars, subheadings, tables}`，没有 `han_chars` / `chunks`。短章门控有两条：`chars < 1200 且 subheadings < 2 且 tables == 0`，或 `blocks < 14 且 subheadings < 2 且 tables == 0 且 chars < 1800`；不支持的章节仍返回 `can_force: true`。`preflight.capacity` 是另一份 Gate，形状 `{ok, code, warnings, stats, remediation}`，`stats` 里才有 `chunks` / `max_chunk_blocks` / `estimated_output_tokens` / `estimated_seconds` / `limits`。容量超限只影响知识地图生成，不阻断直读正文。
- `read-profile` 阅读视角：`/api/chapter` 的 `reading_profile` 是 `resolve_read_profile(book.subjects)` 的结果，键有 `read_type/name/adler_category/goal/sub_kind/sub_kind_hint/boundary_hint/ledger_subject/blueprints/extensions/available_profiles`。7 类：`practical` 实用型书、`literature` 想象文学、`history` 历史书、`science` 科学与数学、`philosophy` 哲学书、`social` 社会科学、`visual` 图册与视觉；未分类或「拿不准」默认 `social` 并带 `boundary_hint`。章页 `#profile-select` 切换时写 localStorage `reading-profile:<uid>`，切回服务端默认值就删掉这个键；有覆盖时显示 `.override-dot`，服务端的文学子类提示 `sub_kind_hint` 仍保留。
- `read-blueprints` 结构蓝图：跨 7 类共 19 个 blueprint（除 `visual` 仅 1 张 `element_matrix` 外其余 6 类各 3 张），按渲染家族含 DAG、矩阵（含 `quadrant_matrix` / `element_matrix`）、时间线（`parallel_timeline`）与曲线；文学是 `tension_curve/relation_graph/spatial_route`，历史是 `parallel_timeline/power_hierarchy/spatial_route`，科学是 `derivation_dag/sensitivity_chart/system_flow`，哲学是 `argument_map/term_contrast/lineage_dag`，社科是 `causal_loop/concept_matrix/metric_chart`，实用是 `flow_swimlane/decision_tree/quadrant_matrix`，图册与视觉是 `element_matrix`。章页 `[data-run-blueprint]` 按钮带 `.is-cached` / `.is-uncached`；`scope:"chapter"` 的生成落 `工作区/阅读/<uid>/ch/<nn>/chapter_tools/<read_type>_diagram_<blueprint>.json`，`/api/chapter` 的 `cached_tools` 会列出，命中且源文件哈希未变时服务端直接回缓存（`cached:true`），不需要模型 key。
- `read-extensions` 视角扩展：7 个动作 `checklist/imagery/compare_context/boundary_check/sparring_dossier/method_audit/craft_notes`，走同一个 `POST /api/assist`，缓存键是 `<read_type>_<action>_<action>`（无 blueprint）。`[data-run-ext]` 触发；`imagery` 这类 dossier 动作回的是条目文本，不是 SVG，别拿 `<svg` 判失败。`compare_context` 与 `method_audit` 带 `cross_search:true`，工具条额外给 `[data-cross-book]`「跨书对照 ↗」，跳到 `/go/search?...&mode=related&exclude=<uid>`；其余视角没有这个按钮。
- `read-typography` 排版与主题：`#btn-typo` 打开 `#typo-popover`，字号 `16/18/20`（`data-font-size`）、行宽 `600/680/780`（`data-measure`），偏好写 localStorage `reading-font-size` / `reading-measure` 到根元素 CSS 变量；`#btn-focus-mode` 在弹层里，同时收起左侧书脊与右侧伴读抽屉（切换 `#wb-grid.hide-spine` 等）；`[data-theme-toggle]` 给 `html` 加/去 `.cold`，localStorage 键 `reading-theme`，`aria-pressed` 同步。
- `read-chapter-map` 按需知识地图：`POST /api/chapter-map/<uid>/<n>`，body 可选 `{force, model, effort}`。`force:false` 且命中未过期缓存时返回 `dict(cached, cached=true)`；未命中且短章门控不支持时返回 200 `{supported:false, can_force:true, reason, stats}`，不调模型；生成时返回 `{supported:true, forced, generated_at, model, usage, elapsed_ms, sections, terms, figures, stats}`。缓存落 `工作区/阅读/<uid>/ch/<nn>/chapter_map.json`；源文件哈希变了，读取时只加内存里的 `stale:true`，不写回。`sections` 给 UI 的形状是 `{id, title, summary, block_ids, nodes}`，不是模型文档里的 `unit_ids`。点 `#btn-map` 打开左侧 `#map-drawer`（书脊页签），生成后是 `.map-tree-wrap` / `.tree-root-row` 加 `.tree-leaf[data-jump]`，页头写「N 节 · M 术语」；`#btn-toc` 是另一页签，列章节目录。`#btn-gen-map` 只在抽屉打开且无缓存时出现，`#btn-gen-map-force` 是「↻ 更新」。容量阻断返回 422 `{error, code:"source_too_large", stats, remediation}`；模型缺失 400 `missing_config`；其他上游错误 502。
- `read-assist` 划词伴读：`POST /api/assist`，body `{uid,n,action,block_ids,selected_text,question,blueprint,read_type,sub_kind,scope,force,model,effort,stream}`。五个基础动作是 `diagram` 图解此段、`explain` 讲人话举例、`translate` 翻译原意、`critique` 拆解前提、`ask` 自由追问；`action` 还接受 7 个扩展动作（见 `read-extensions`）。`scope` 只接受 `selection` / `chapter`，缺省 `selection`；`blueprint` 只在 `diagram` 时生效，省略时用当前视角第一张蓝图；`read_type` / `sub_kind` 可临时覆盖视角；`force:true` 跳过全章缓存重新生成。`stream:true` 是默认值，HTTP 200 `text/event-stream`，事件只有 `delta`（`{text}`）、可选的 `diagram`（`{html, figure}`）、`done`（完整结果）和流内异常时的 `error`；**没有 `meta` 事件**。`stream:false` 返回完整 JSON，键含 `id/uid/n/action/action_label/block_ids/selected_text/question/content/diagram_html/figure/model/usage/tokens/elapsed_ms/op`。划词栏第一行放置 4 个基础文字动作（`explain`/`translate`/`critique`/`ask`）加 `search-related`「跨书找相关 ↗」，第二行放置视角蓝图与扩展动作；点开「跨书找相关 ↗」跳 `/go/search?...&mode=related&exclude=<uid>&src_path=...&src_line=...&threshold=0.45`，不是 `/api/assist` 的 action。无模型 key 时在开流前就 400 `{code:"missing_config", missing, fix}`；参数错误 400；章节不存在 404；开流后的上游错误仍是 HTTP 200，只在流里发 `error`；非流式上游错误 502。`diagram` 必须回严格 JSON 才渲染 SVG，模型回普通文字时 `diagram_html` 为空但不丢正文。
- `read-pins` 旁批：`POST /api/assist/pin` 写入 `工作区/阅读/<uid>/ch/<nn>/assist_notes.json`，body 是 `{uid,n,action,title,block_ids,selected_text,question,content,diagram_html}`；返回带 `id:"note-xxxxxxxx"` 的 note。旁批按最后一个 `block_ids` 锚在段后；源文件哈希变了，读取时只加 `stale:true`。`DELETE /api/assist/pin/<uid>/<n>/<id>` 返回 `{ok:true, deleted:id}`；不存在的 note 返回 404。旧兼容路由是 `DELETE /api/assist/pin/<id>?uid=<uid>&n=<n>`，新代码不要用它。每次钉住/移除都追加 `pin` / `unpin` 操作日志。
- `read-records` 操作日志：`/records` 调 `/api/records`，页面自己按 `uid` / 动作 / 状态做客户端筛选。接口支持 `?uid=&action=&status=&limit=`，`limit` 默认 300、夹在 1..1000；返回 `{logs, chapters, summary}`。`logs` 每条有 `id/ts/uid/n/book_title/chapter_title/action/block_ids/status/model/tokens/elapsed_ms/summary/error`，只有真跑过模型的记录才有 `model`，旁批记录留空。`summary` 是 `{total, ok, error, assist, chapter_map, pin, chapters}`，`pin` 统计 pin+unpin。`chapters` 按最近操作倒排，带 `ops` / `actions` / `note_count` / `has_map`。页面有「全部 / 知识地图 / 划词伴读 / 旁批变更 / 失败」五个页签，行尾「定位 →」跳到 `/chapter/<uid>/<n>#blk-<first_block>`。
- `read-guard` 边界：Host 不是 `127.0.0.1` / `localhost` 时页面和接口都 403；所有方法的请求只要带非本机 http `Origin` 都返回 403。响应带 `Cache-Control: no-store` 与 `X-Content-Type-Options: nosniff`。已知路由用错方法返回 405（例如 GET `/api/assist`、POST `/api/shelf`），未知接口 404 JSON，未知页面 404 HTML。`/api/asset?uid=&rel=` 服务 `books/<uid>/md/` 下的受限文件（不限于图片），缺参 400、越界或不存在 404。`/open` 的 404 是 HTML，不是 JSON；`/go/<未注册>` 404、`to=//evil.com` 400、目标未就绪 503，也都是 HTML。

## How to get to it (user POV)

- 从 8773 首页的「翻开书」卡进来，或直接打开 `http://127.0.0.1:18771/`。首屏是书墙，点书脊进书页；也可以用顶栏「书目」进 `/catalog`，用「操作日志」进 `/records`。
- 书页点「翻开第 NN 章」或章表里的「直读全文」进章页。章页默认就是完整正文，不点任何按钮也能读。
- 正文里的注释上标（如 `[25]`）可直接点击：章内有对应注文时滚到对应注段并高亮，点底部浮出的「↩ 返回正文」或注段右侧「↩ 返回正文」回到原注释点；正文与注释分属不同文件时弹出提示卡片，点击「新标签直达注 [...] ↗」在新标签页直接落到目标注释块并显示「来自正文注释」来源条。
- 书页与书目页的「去书库检索」出口走 `/go/search?...&mode=trace`，只到检索结果，不自动答题。
- 从找一段的命中卡、往期日课的「在分层读打开这一章」或任一 `/open?...` 链接进来，会直接落在对应章，顶部有来源条，命中段落高亮。
- 章页工具栏点「知识地图」展开按需生成抽屉；已有缓存时按钮写「知识地图（已缓存）」，抽屉里是「↻ 更新」。短章会显示「无需强行展开导图」和「仍要展开简图」。
- 在正文里拖选一段，或点段尾 ✦，浮出划词工具栏；选「图解此段 / 讲人话举例 / 翻译原意 / 拆解前提 / 自由追问」后右边抽屉流式出结果。抽屉底部可以继续追问；结果右上「钉住到段落」把旁批钉在原文后，旁批卡上的「移除」删除。
- 「模型」打开 `#model-box`（仅改浏览器 localStorage 里的模型名与推理档，默认 `deepseek-v4.1-flash` / `high`，不影响服务端默认值）。
- 左侧 `#left-spine` 底部的 `.spine-quick-links`（或对应页头出口）提供「本章日志 →」带 `?uid=` 进 `/records` 与「去书库检索」；记录页可按页签和关键字筛，再点「定位 →」跳回原段落。

## Driving it with curl + headless Chrome + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，并装了样书：`uv run --frozen book-corpus sample --root "$ROOT"`。本章命令默认样书 `B0001` 第一章是 `01`，15 个正文块、1,719 字；九章都高于短章门控。
- 证据目录先定：`RUN_ID=${RUN_ID:-20261116-layered}; EV="$PWD/artifacts/verify/$RUN_ID/layered-reads"; mkdir -p "$EV"`。
- 阅读服务由本轮起，端口必须是 18771，不能碰操作员的 8771：

```bash
cd "$(git rev-parse --show-toplevel)"
export BOOK_CORPUS_ENV="$ROOT/run/env"
: >"$ROOT/run/reading.log"
setsid env BOOKS_ROOT="$ROOT" uv run --frozen python apps/reading/server.py --root "$ROOT" --port 18771 \
  --no-start --search-url http://127.0.0.1:18765 --home-url http://127.0.0.1:18773 \
  --no-annotate --quiet >>"$ROOT/run/reading.log" 2>&1 & echo $! > "$ROOT/run/reading.pid"
until grep -q '^阅读工作台 8771 · root=' "$ROOT/run/reading.log"; do sleep 0.2; done
```

- 就绪行是 `阅读工作台 8771 · root=<ROOT> · http://127.0.0.1:18771/`，不是旧的 `分层读 8771`。不带 `--quiet` 时 stderr 还会打「配置提示：…」，那是云端 key 的常态，不是服务坏。
- `--no-annotate` 现在只是兼容开关，`ReadingService.annotate` 存了但没人读；它**不关**伴读或知识地图的模型调用。模型只在 `POST /api/assist` 与需要生成的知识地图里按需调用，能否调用只看 `ModelClient.available()`。
- 不要用「环境里没有 `OPENCODE_GO_API_KEY`」当离线保证：`ModelClient` 还会读 `~/.pi/agent/auth.json` 里的 opencode-go 登录。要离线验模型分支，起本地 stub，并把阅读服务指向它：

```bash
setsid python3 .agents/skills/verify-book-corpus/scripts/mock_reading_model.py --port 18790 \
  >"$ROOT/run/mock-model.log" 2>&1 & echo $! > "$ROOT/run/mock-model.pid"
: >"$ROOT/run/reading.log"
setsid env OPENCODE_GO_API_KEY=sk-mock BOOKS_ROOT="$ROOT" \
  READING_MODEL_URL=http://127.0.0.1:18790/v1/chat/completions \
  uv run --frozen python apps/reading/server.py --root "$ROOT" --port 18771 \
  --no-start --search-url http://127.0.0.1:18765 --home-url http://127.0.0.1:18773 \
  --no-annotate --quiet >>"$ROOT/run/reading.log" 2>&1 & echo $! > "$ROOT/run/reading.pid"
until grep -q '^阅读工作台 8771 · root=' "$ROOT/run/reading.log"; do sleep 0.2; done
```

- stub 只认本地 18790，返回固定的白话、流程图与按原文块拼的知识地图，不评价模型质量；`READING_MODEL_URL` 必须指向它，否则会去 opencode 真打。下面的模型分支命令都假设这个 stub 已起。

- **跨页定位 `/open`。** `curl -s -o /dev/null -w '%{http_code}\n%{redirect_url}\n' 'http://127.0.0.1:18771/open?uid=B0001&path=books/B0001/md/01_第一章序.md&line=9&q=%E6%88%91%E8%A6%81%E7%BB%99%E9%98%BFQ%E5%81%9A%E6%AD%A3%E4%BC%A0&from=search&query=...'` 应为 `302`，Location 含 `/chapter/B0001/01?from=search&query=...&hl=...&blocks=B01#blk-B01`。未知 uid 是 `404`；`path=books/B0001/README.md` 应 `302` 到 `/book/B0001?notice=...`。
- **页面骨架。** `curl -s -o /dev/null -w '%{http_code} %{content_type}\n'` 取 `/`、`/catalog`、`/book/B0001`、`/chapter/B0001/01`、`/records`，五个都是 `200 text/html`。`/chapter/B0001/1` 也返回 200（章号可写 1 或 01），只有被并入正文的旧前置页才会 302 到合并后的章号；样书没有这种合并页。
- **书架。** `curl -s http://127.0.0.1:18771/api/shelf` 的键是 `stats/books/recent/total`；`stats` 是 `readable/chapters/chars/packs/notes/maps`，样书为 `1/9/21362/0/0/0`（生成了地图或旁批后 `packs` 与对应计数会变）。`recent` 至少一条，没操作时是 `B0001/01/第一章 序/开始阅读`。
- **书目。** `curl -s 'http://127.0.0.1:18771/api/catalog?q=%E9%98%BFQ'` 的 `query` 是 `阿Q`、`readable` 至少 1 条、`books` 与 `readable` 同长；空 `q` 时 `metadata` / `chapters` / `authors` / `subjects` 都是 `[]`。
- **书页与章页 JSON。** `/api/book/B0001` 是 `{book, chapters}`，章项有 `n/note_count/has_map/ops_count/minutes`；`/api/chapter/B0001/01` 是 `{book, chapter, blocks, toolbar, reading_profile, preflight, assist_notes, chapter_map, cached_tools, footnotes}`，`blocks[0]` 带 `id/source_file/start_line/end_line`（样书 `B01` 是 `01_第一章序.md 11 11`），`toolbar` 五项的 `action` 顺序是 `explain/translate/diagram/critique/ask`；页面上还会多一个「跨书找相关 ↗」出站按钮。`reading_profile` 形态见下一条；`preflight.chapter_map` 的键只应有 `supported/reason/can_force/stats`，`stats` 只应有 `blocks/chars/subheadings/tables`；如果出现 `han_chars` 或 `chunks`，是拿错了旧的容量结构。
- **脚注跳转与跨文件提示。** 写入含章内 `[^[25]^]()` / `[[25]]()` 与跨文件 `^[[192]](02_注释.md)^` 的章后，打开章页应渲染可点 `.fn-ref` 上标且正文不含裸 `[^[`；点击章内上标跳到 `.reader-footnote-item` 并弹出 `#fn-return-bar`，点 `#btn-fn-return` 或注段 `.fn-back-inline` 回到原 `.fn-ref` 并触发 `.fn-return-flash`；点击跨文件可定位上标弹出 `#fn-tip-popover` 显示「新标签直达注 [...] ↗」且链接带 `?from=footnote&fn=...#blk-...`，打开新页面直落目标块并显示「来自正文注释」来源条与 `.fn-active-def` 高亮；未配对上标显示分属文件提示与目录按钮。
- **阅读视角下推。** `/api/chapter/B0001/01` 的 `reading_profile` 默认是 `read_type=literature / name=想象文学`，带 `blueprints`（`tension_curve/relation_graph/spatial_route`）、`extensions`（`imagery`，`cross_search=false`）与 `available_profiles` 七项。切视角不发请求：`#profile-select` 的选项来自 `apps/reading/web/assets/chapter.js:45-112` 的内嵌表，选中后写 `localStorage['reading-profile:B0001']`、重渲染 `.kicker-persp` 与工具条（`chapter.js:146-159`、`554-568`）；选 `history` 后 kicker 变「历史书」，蓝图换 `parallel_timeline/power_hierarchy/spatial_route`。要断言就 CDP `select.value='history'` 再派发 `change`，不能用 curl。
- **蓝图与扩展（要 stub）。** `[data-run-blueprint]` 点击后 `POST /api/assist`，body 带 `action="diagram"`、`blueprint`、`read_type` 与 `block_ids`（`chapter.js:1507-1571`、`1268-1293`）；默认 `tension_curve` 走曲线渲染给 `<div class="fig-bars">` 与 `figure.caption=="结构骨架"`，传 DAG 蓝图如 `relation_graph` 时 stub 回 3 节点 2 边的 DAG、`#drawer-diagram` 出 `<svg>`（`两步关系`），工具按钮加 `.is-cached`、`[data-bp-zoom]` 可开 `#zoom-modal`（标题「结构骨架（宽屏画布 · 点击节点直跳原文）」、`#zoom-qa` 含「宽屏质检」）。`[data-run-ext]` 的 `action="imagery"` 回 dossier 文本而不是 SVG。真模型 2026-11-20 因额度 429 未跑，模型质量记 `unexercised`。
- **钉住与选择条。** 有工具结果后 `#btn-pin` 才可用；点一次 `#pin-count` 加 1，`#blk-B01` 下出现 `.pinned-notes`，刷新后仍在（服务端落 `工作区/阅读/<uid>/ch/<nn>/assist_notes.json`）。在 `#reader-body` 里框选 `#blk-B03` 后 mouseup 出 `#selection-bar.show`：讲人话/举例/翻译原意/拆解前提/自由追问/跨书找相关 ↗（7 类视角均渲染）+ 当前视角的蓝图与扩展按钮；而工具条上的 `[data-cross-book]`「跨书对照 ↗」只在 `ext.cross_search` 为真的视角（如 `history`/`social`）出现。点蓝图后 `#drawer-scope-bar` 显示「已选 B03改按全章」，`#tab-scope-selection` 激活。
- **短章门控（不调模型）。** 样书九章都高于门控，先手写一个短章：

```bash
cat > "$ROOT/books/B0001/md/10_短章.md" <<'MD'
---
title: "第十章 短章"
author: "鲁迅"
language: "zh"
source: "source/阿Q正传.txt"
chapter: "10"
---

很短的一章。
MD
curl -s -X POST http://127.0.0.1:18771/api/chapter-map/B0001/10 \
  -H 'Content-Type: application/json' -d '{"force":false}' > "$EV/map-short-gated.json"
```

结果 `supported:false`、`can_force:true`、`stats` 是 `{blocks:1, chars:6, subheadings:0, tables:0}`，`reason` 里同时点出字数和块数；服务端没有模型调用。`/api/chapter/B0001/10` 的 `preflight.chapter_map` 也应给出同一份门控。
- **强制短章（stub）。** 同章 `-d '{"force":true}'` 返回 200 `supported:true`，`stats.units` 为 1；这一步会走 stub，不是真模型。
- **生成与缓存（stub）。** `POST /api/chapter-map/B0001/01` 加 `{"force":false}` 返回 `supported:true`，`stats.units` 为 15、`stats.sections` 为 1；再发一次同一请求，第二份是 `cached:true`。磁盘上应出现 `$ROOT/工作区/阅读/B0001/ch/01/chapter_map.json`，形如 `{"schema":1,"uid":"B0001","n":"01","source_sha256":"…","sections":[…],"stats":{…}}`。把源文件改一个字节后再取，`/api/chapter` 的 `chapter_map.stale` 变 `true`，但缓存文件本身不被重写。
- **划词伴读 SSE（stub）。** `curl -sS -N -X POST http://127.0.0.1:18771/api/assist -H 'Content-Type: application/json' -d '{"uid":"B0001","n":1,"action":"explain","block_ids":["B01"],"selected_text":"我要给阿Q做正传","stream":true}'` 以 `event: delta` 开头、以 `event: done` 收尾；`event: meta` 不应出现。`action:"diagram"`（缺省蓝图 `tension_curve` 时）发 `diagram` 后接 `done`（无 `delta`），`html` 为 `<div class="fig-bars">...`、`figure.caption` 为 `结构骨架`；加 `"blueprint":"relation_graph"` 走 DAG 才在 `diagram.html` 含 `<svg`、`done.figure.caption` 为 `两步关系`。
- **划词伴读非流式（stub）。** 同一请求体加 `"stream":false` 返回 200 JSON，`action` 是 `explain`，`content` 非空，`tokens` 是 30；`block_ids` 原样带回。
- **旁批钉住与移除（不调模型）。** `POST /api/assist/pin` 传 `{uid:"B0001", n:1, action:"explain", title:"测试旁批", block_ids:["B01"], selected_text:"我要给阿Q做正传", content:"一条可复核的旁批"}`，返回 `id` 以 `note-` 开头；`$ROOT/工作区/阅读/B0001/ch/01/assist_notes.json` 出现该 note，`GET /api/chapter/B0001/01` 的 `assist_notes` 长度变 1；`DELETE /api/assist/pin/B0001/01/<id>` 返回 `{ok:true, deleted:<id>}`，再取章节 `assist_notes` 为空。`DELETE` 不存在的 id 返回 404。
- **操作日志。** 做完旁批后 `curl -s http://127.0.0.1:18771/api/records` 的 `summary` 至少 `pin:1`，`logs` 第一项是 `unpin`（如果刚删）或 `pin`；`chapters[0]` 带 `ops` / `actions` / `note_count` / `has_map`。`?limit=1` 只回一条；`?status=error` 在无失败时是空数组，不要拿它证明日志写了。`/records?uid=B0001` 的页面筛选是客户端行为，接口的 `uid` 参数也支持。
- **错误与边界。** 不设模型 key、且 `~/.pi/agent/auth.json` 没有 opencode-go 登录时，`POST /api/assist` 是 400 `{code:"missing_config", fix:…}`；本机 pi 已登录时这条会变成真调用，别拿它当离线用例。`GET /api/assist` 是 405；`POST /api/shelf` 是 405；`Host: evil.example` 取 `/` 是 403；`/reader/B0001/01`、`/reader/B0001/01/depth`、`/api/depth/B0001/01`、`/api/jobs`、`/api/book/B0001/reading` 都是 404。`/api/asset` 缺参 400、路径越界或不存在 404。
- **headless Chrome 章页。** 模板已移到 `assets/chapter.js`，`chapter.html` 仅保留少量主题初始化脚本；`--dump-dom` 不会带出模板字符串，不必再靠剥内联脚本去重。仍可剥 `<script>` 后断言 `id="btn-map"`、`id="btn-terms"`、`id="btn-toggle-pins"`、`id="btn-model-box"`、`id="btn-drawer"`、`id="selection-bar"`、`id="assist-drawer"`、`id="pin-count"`、`blk-B01` 都在，正文含「100% 完整正文直出」；`id="depth-panel"` 一定不在，工单/三档/五层文本也不在。缓存地图生成后 `#btn-map` 的文本含「（已缓存）」，页面载入即带有 `#btn-gen-map-force`（`↻ 更新`）。
- **headless Chrome 定位条。** 跟随 `/open` 的 302 取章页 DOM，断言 `id="arrival-banner"`、`class="hl-hit"`、`reader-block hit` 都在，来源条含 `来自书库检索` 与「去书库重搜」；`#btn-map` 与 `#selection-bar` 仍在，`id="depth-panel"` 不在。
- **书页、书目、记录页。** 同样用 `--dump-dom`：书页断言 `#book-root`、`#ch-list`、`#book-notes-panel`、`#book-notes-summary`；书目断言 `#q`、`#results`、`#stats`、`#note`；记录页断言 `#stats`、`#chapters`、`#records`、`#tabs`、`#ops-q`、`#btn-refresh`。三页都是客户端渲染，`curl` 只证明模板在。
- **离线回归。** `uv run --frozen pytest -q tests/test_reading_footnotes.py tests/test_reading_mdparse.py tests/test_reading_server.py tests/test_reading_service.py tests/test_reading_preflight.py tests/test_reading_locator.py tests/test_reading_library.py tests/test_cross_app_linkage.py tests/test_reading_book_types_and_blueprints.py` 全过；服务端用例自己起临时进程与 mock upstream，不会碰 18771。`test_reading_footnotes.py` 钉各类脚注语法识别、章内配对回跳与跨文件元数据；`test_reading_locator.py` 钉行号、前置页合并、非章节路径与增量新书；`test_cross_app_linkage.py` 钉 `/open` 302、`/go/search`、`/api/books/meta` 与 scope 越界；`test_reading_book_types_and_blueprints.py` 钉七类阅读视角与 19 个蓝图配置。
- **容量 422 未 live 演练。** 要触发 `source_too_large` 需要造 >5000 块或 >1M 估算 token 的章节；`tests/test_reading_preflight.py` 证明 gate 与 payload 形状，路由的 422 只按源码核对，复跑时记 `unexercised`，不要伪装成页面通过。

## Gotchas

- 章号可以写 `1` 或 `01`，不会像旧文档说的那样一律 302 到两位章号；只有被并入正文的旧前置页（`body_n` 指向的章）才 302 到合并后的章号。样书没有这种合并页，别用 `/chapter/B0001/1` 验证重定向。
- `preflight.chapter_map` 不是容量预检。短章门控的 `stats` 是 `blocks/chars/subheadings/tables`；`chunks` / `estimated_*` / `limits` 在 `preflight.capacity.stats` 里。把两者混在一起会写出 `<800 汉字且 <5 块` 这种已经不存在的阈值。
- 短章门控不调模型，但「生成成功」一定调模型。样书九章都高于门控，想验门控必须手写一个短章；想验生成必须起 stub 或花一次真调用。`--no-annotate` 不是离线开关。
- `ModelClient.available()` 不只看 `OPENCODE_GO_API_KEY`，还看 `~/.pi/agent/auth.json` 的 opencode-go 登录。没有环境变量也可能真打模型；离线验证要么起 stub，要么把 HOME 指到没有 auth.json 的目录后重启服务。
- SSE 没有 `meta` 事件。实际顺序是若干 `delta`（或 `diagram` 动作直接发 `diagram`，无 `delta`）再 `done`；流内异常是 HTTP 200 加 `error` 事件，不是 500。`stream:false` 才是普通 JSON 200。
- `diagram` 的模型输出必须是严格 JSON 且带 `data`；回普通文字时 `diagram_html` 为空、`content` 保留文字，这不是接口坏。`done` 里可能有 `figure:null`，断言 `<svg` 前先看是否用了 DAG 家族蓝图。
- 旁批的 `stale` 只在读取时按源文件 sha 计算，不写回 `assist_notes.json`；改源文件后再读会看到 `stale:true`，重新钉住才会写新 sha。`note_count` / `packs` / `maps` 都是按磁盘产物现算的，不是账本字段。
- `sections` 的 UI 形状是 `{id,title,summary,block_ids,nodes}`，没有 `unit_ids`；`units` 数量只在 `stats.units` 里。按模型文档的 `unit_ids` 去断言会 KeyError。
- 页面是客户端渲染，`curl` 拿到的 HTML 只有薄壳；章页模板已在外链 `assets/chapter.js`，`--dump-dom` 不再带模板字符串，剥不剥脚本都不影响数 id。`#btn-gen-map` 只在无缓存时打开地图抽屉出现；有缓存时 `#btn-gen-map-force`（`↻ 更新`）在页面载入后即存在。
- 记录页的筛选是客户端行为，`/api/records?status=...` 是另一条接口路径；页面本身只取一次全量日志。`logs[].model` 在旁批记录里是空串，不是所有记录都有模型名。
- 操作日志 append-only，测试写入会留在 `工作区/阅读/ops_log.jsonl` 与 `logs/`。清理只删一次性根和进程，不要把证据目录或操作员的 `~/书库` 一起删。
- `/open` 的 `line` 是正文 1-based 行，前端置顶 H1 与 front matter 后仍用原行号；合并过卷首的章，索引行可能落在被并入的前置页，`/open` 能定位到章但高亮可能没有。断言先看 302 与 `blocks`，再看 `mark.hl-hit`，不要把「引文不在当前块」判成路由坏。
- `path` 只认本书 `md/` 下的章节文件；书名目录、`README.md`、旧 `库/...` 路径会转书目页，不会硬猜章节。`q` 只高亮块内第一个命中的片段，不是全文所有出现。
- 阅读服务的根解析是 `--root` 优先于 `BOOKS_ROOT` / `.env`（`apps/reading/server.py` 走 `envfile.resolve_books_root(args.root)`，`Library(root)` 调用 `find_root(root, ignore_env=True)`）；不传 `--root` 时才会回落到 `BOOKS_ROOT` / 仓库 `.env`。起 18771 时仍同时传 `--root "$ROOT"` 并 `export BOOKS_ROOT="$ROOT"`。
- `app.css` 已无 `.depth-*`；`.rec-tiers` 与 `.path-badge-depth` 仍在别的样式文件里，但模板里没有对应 DOM。`rg depth` 仍可能命中这些残留，不能据此说页面还有深度层。退役路由的 404 由 `tests/test_reading_server.py` 的可执行清单守着，改路由先跑它。
- 跨文件脚注直达靠在目标注释章内按注号匹配（并结合回链 `back_file` 区分各章从 1 重编的注号）；只有精确定位到目标块时卡片按钮才写作「新标签直达注 [...] ↗」（或「新标签直达注 [...]（第 NN 章）↗」）并带 `?from=footnote&fn=...&src_n=...#blk-<id>`，无法定位具体块时退回章节链接或全书目录按钮。打开带 `from=footnote` 的直达链接会即时滚落目标块（不走平滑滚动），顶部 `#arrival-banner` 显示「来自正文注释」与「回到第 NN 章」，不是检索来源条。
