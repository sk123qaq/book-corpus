# 找一段

演示页拿一句人话去书库里找段落：先拆词、换词循环、给书打分，再把候选书和命中章递给用户，
最后给出带依据、流式上屏的回答。页面有两个：默认打开的多轮会话页，和保留五阶段过程的
`/classic`。回答可以随时暂停，再从 checkpoint 续答。结果里的书卡和命中卡可以带到分层读的
`/open`，直接落到原文章节与行。

## Sub-features

- `ask-open` 打开会话页；上一轮没跑完的回答在**服务启动时**标为 `interrupted`（文案「服务重启，上次回答已中断」），页面只负责显示。
- `ask-session` 新开会话、多轮追问、会话落盘；同一轮的迟到请求按 `(session_id, n, run_token)` 去重。
- `ask-history` 历史页签：按问题筛选、多选删除。
- `ask-scope` 候选书多选：选中后作为 `scope.books` 下推给 `/api/run` 的检索，不再只做结果后过滤；最多 16 本。
- `ask-books-meta` `GET /api/books/meta?uids=B0001,B0002` 用 `build/library.db` 补书名与作者，最多 16 本，缺的 uid 用 uid 当标题。
- `ask-cross-open` `?q=&books=&mode=trace` 只跑检索不生成回答；命中卡与书卡链到 `/go/reading?to=/open...`，`mode=prefill` 只预填问题。
- `ask-thinking` 思考等级（off / low / medium / high）在会话页 `/` 与经典检索页 `/classic` 均通过 `think-select` 下拉切换（持久化到 `localStorage['booksearch.thinking']`，前端默认 `low`；后端 `/api/answer` 缺省仍兼容 `high`）。
- `ask-trace` 依据与落点：回答里的书卡、章节锚点、命中段原文。
- `ask-classic` `/classic` 五阶段：理解问题 → 词法循环 → 打分与选章 → 书卡 → 结果；经典页发 `stateless:true`，不建会话，也不进历史。
- `ask-stream` `POST /api/answer` 带 `stream:true`（或 `Accept: text/event-stream`）时回 SSE：`stage`、`thinking`、`tool_start`、`tool_end`、`answer_delta`、`paused`、`error`、`done`；每 15 秒 `: ping`，300 秒截止后发一个 504 `error` 帧；客户端断开不取消后台作答，结果仍落盘。
- `ask-pause` 回答中显示「暂停」；暂停把 `partial_text`、原始消息和阶段写进进程内 checkpoint（TTL 1800 秒），卡片变「已暂停 · 点击「继续问答」从当前进度继续」，`resume:true` 从 checkpoint 续答。会话页按 `(session_id, turn)` 定位，经典页按 `stream_id`；会话页的 turn 在暂停时落 `answer_state:"paused"` + `answer_partial`，续答完成后回 `ready`；经典页没有 turn，不写状态。
- `ask-hybrid` 候选段由 BM25 + 向量 RRF 融合；`/api/run` 与 `/api/health` 暴露 `plan.fusion`（`hybrid` 或 `bm25_only`）、`plan.vec_used`、`plan.vec_ms`、`plan.vec_hits` 与 `meta.embed`（`vectors` / `server` / `ready` / `chunks` / `books` / `url`）。向量索引或 embedding 服务不可用时静默降级成 `bm25_only`。
- `ask-export` 导出单个会话为 MD 或 DOCX：`GET /api/export?id=<12hex>&format=md|docx`，下载名 `会话-<时间>-<问题前12字>.<ext>`；页面入口在历史会话的悬浮菜单。没有 zip 导出。
- `ask-retry` 同轮作答去重：键是 `(session_id, n, run_token)`，最多等 300 秒，结果再留 60 秒；`fresh:true` 重开，旧请求 409「这一轮已重跑，本次回答作废」，超时 504「回答仍在生成，请稍后刷新」。
- `ask-runlog` 运行记录：每次提交在磁盘写 `run.jsonl`（`answer_once`/`turn`/`service`），章读取、导出、删会话也写 `service.jsonl`；CLI `python3 apps/demo/runlog.py {list,timeline,bundle,replay,prune,doctor}` 可查。`bundle` 才产 zip，落 `exports/session-*.zip`；页面导出不走它。
- `ask-degrade` 没有可用模型时降级：默认直连 HTTP 后端报 `模型调用失败：…`，经典页标「降级」并改用直接词匹配；`ANSWER_BACKEND=pi` 才回退旧子进程，旧路径的错误才带 `rc=N`。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18765/`（多轮会话页）。
- 在输入框敲问题，按回车或点发送；回答会流式出现，右上角有「暂停」。
- 点「暂停」后卡片显示「已暂停」，点「继续问答」从当前进度续答。
- 想看过程：打开 `http://127.0.0.1:18765/classic`；**示例问句只在这个页面上**。
- 点结果里的书卡，或点历史页签翻旧会话；历史会话的悬浮菜单可导出 MD / DOCX 或删除。
- 带书目前提打开 `http://127.0.0.1:18765/?q=礼治社会靠什么维持秩序&books=B0001&mode=trace`，页面只停在检索命中，不发起回答；点命中卡的「在分层读中打开」跳到 8771 的章页。
- 从每日日课或分层读的「去书库重搜」也能带 `from=reading` / `from=daily` 回来，检索页保留来源。

## Driving it with curl + tests/manual/demo 的 CDP 脚本与技能脚本

Preconditions:

- 工作区按 [README](./README.md) 建好，`$ROOT/build/检索/index.db` 与 `$ROOT/build/library.db` 都存在。
- 演示页由本轮起：`BOOKS_ROOT=$ROOT python3 apps/demo/server.py --root "$ROOT" --port 18765 --reading-url http://127.0.0.1:18771 --home-url http://127.0.0.1:18773`，启动行是 `书库检索演示：http://127.0.0.1:18765`。
- `--root` 决定书目库、索引、会话、terms、answers、related 与运行记录的位置；不传时才按 shell `BOOKS_ROOT` → 仓库 `.env` → 向上找 `books.toml`。仓库 `.env` 常指向操作员的 `~/书库`，所以本轮既传 `--root` 又显式 `BOOKS_ROOT=$ROOT`。
- 启动时必打印 `启动恢复：N 轮…`（没有要恢复的也打 0）。健康降级有三种形状：`db: missing` 是索引没建，`db: empty` 是索引为空，`library: missing` 是书目库没建；后两种 `meta` 里的计数可能是 `null`。
- 真实作答要模型：默认走直连 HTTP（`books.toml` / 环境 → `~/.pi/agent/models.json` + `settings.json` → `~/.pi/agent/auth.json`），`ANSWER_MODEL` 可指定模型；`ANSWER_BACKEND=pi` 强制旧子进程。默认直连没有 `rc=N`，错误前缀是 `模型调用失败：`。
- 时间按本轮实测：单本 `low` 一次完整回答 34–42 秒（2026-11-15，`done.data.ms=34218.8/42227.8`），别再抄 225 秒；`apps/demo/README.md` 的 15-40 秒也未复核。SSE 的 300 秒是硬上限，直连单次调用 240 秒、chunk 读 90 秒；暂停 checkpoint 只活在进程内，重启即失效。
- 混合检索要向量索引与 embedding 服务：先按 [embed.md](./embed.md) 对 `B0001` 建索引并起 18780，再给演示页设 `BOOKS_EMBED_URL=http://127.0.0.1:18780`。没建索引或服务不在时 `meta.embed.ready=false`，`/api/run` 仍是 `bm25_only`，不是页面坏。
- 证据目录先定：`EV="$PWD/artifacts/verify/$RUN_ID/find-a-passage"; mkdir -p "$EV"`。

- **服务就绪。** `curl -s http://127.0.0.1:18765/api/health` 输出 `{"ok": true, "db": "ok", "meta": {"books": 1, "paras": 123, ..., "embed": {"vectors": "ok", "server": "ok", "ready": true, "chunks": 34, "books": 1, "url": "http://127.0.0.1:18780"}}}`。`vectors` / `server` 也可分别是 `missing` / `offline`，此时 `ready:false`。
- **两个页面都在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18765/` 与同一命令打 `/classic`，都是 `200`。
- **示例问句只在经典页。** `curl -s http://127.0.0.1:18765/ | grep -c 'class="sample"'` 输出 `0`；`curl -s http://127.0.0.1:18765/classic | grep -c 'data-q='` 输出 `3`，三条是 `礼治社会靠什么维持秩序`、`内卷和教育之间是什么关系`、`乡土中国 差序格局`。
- **五阶段骨架。** `curl -s http://127.0.0.1:18765/classic | grep -c 'data-stage='` 输出 `5`，五段标题依次是「理解问题」「词法循环」「打分与选章」「书卡」「结果」。
- **会话列表。** `curl -s http://127.0.0.1:18765/api/sessions` 返回 `{"ok": true, "count": N, "sessions": [...]}`；每条带 `id`、`turns`、`question`、`answer_state`、`answer_error`、`books`、`last_question`、`updated_at`。传 `--root` 的新根上 N 应为 0；本轮自己建的会话才会出现，磁盘落在 `$ROOT/工作区/检索/sessions/`。
- **书目元数据。** `curl -s 'http://127.0.0.1:18765/api/books/meta?uids=B0001,B0002'` 返回 `{"ok":true,"books":[{"uid":"B0001","title":"阿Q正传",...}]}`；只装样书时 B0002 也回一条、标题回退成 uid，不是 404。
- **书目范围下推。** `curl -s -X POST http://127.0.0.1:18765/api/run -H 'Content-Type: application/json' -d '{"question":"礼治社会靠什么维持秩序","scope":{"books":["B0001"]},"stateless":true}'`，响应 `scope.books==["B0001"]`、`focus.status=="exact"`、`result.uid=="B0001"`，`plan.chapters` 与 `cards` 里不出现别的书。多书 scope 要装第二本；只有样书时记 `ui-unverified`，原因写「只有 B0001，无法验多选范围」。
- **trace 不答题。** `google-chrome-stable --headless=new --disable-gpu --no-sandbox --user-data-dir="$ROOT/run/chrome-demo" --virtual-time-budget=12000 --dump-dom 'http://127.0.0.1:18765/?q=礼治社会靠什么维持秩序&books=B0001&mode=trace' > "$EV/trace-dom.html"`，剥脚本后数 `trace-hit-card` 与 `scope-pill` 各 1、`由模型归纳回答` 1、`在分层读中打开` ≥1、`answer-pending-note` 1，且没有回答轮询；链接形如 `/go/reading?to=%2Fopen...`。
- **跨页跳转。** 取上一步的 `/go/reading?to=...` 发给 18765，`curl -s -o /dev/null -w '%{http_code}\n%{redirect_url}\n'` 应为 `302`，Location 是 18771 的 `/open?uid=B0001&path=...&line=...&q=...`。`to=//evil.com` 应 `400`。
- **SSE 流式。** `curl -sN -X POST http://127.0.0.1:18765/api/answer -H 'Content-Type: application/json' -d '{"question":"阿Q为什么挨打","book":"B0001","path":"books/B0001/md/06_第六章从中兴到末路.md","line":75,"terms":["阿Q","精神胜利法","未庄","赵太爷","闲人"],"thinking":"low","stream":true,"stream_id":"probe-sse","fresh":true}'`。响应头应是 `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-store`、`X-Accel-Buffering: no`、`Connection: close`；事件流先 `stage` / `thinking`，再 `answer_delta`，最后 `done`（内层 `data.ok:true`、`data.answer`）。不落盘也能看到前几个 delta；断开后服务端仍会写完这次作答。
- **暂停与续答。** 用技能脚本跑完整一轮：`python3 .agents/skills/verify-book-corpus/scripts/drive_sse_pause.py --base http://127.0.0.1:18765 --out "$EV/sse-pause-resume.json"`。它读到第一个 `answer_delta` 就 `POST /api/answer/pause`，然后带 `resume:true` 再开一条流；末三行应是 `STREAM content_type=text/event-stream; charset=utf-8 ...`、`PAUSED partial_chars=<非0> pause_ok=True`、`RESUMED status=200 events=<N> done_ok=True final_chars=<N>`，exit 0。2026-11-15 两次实测 `partial_chars=3/5`、`done.data.ms=34218.8/42227.8`、resume 事件 369/384 条；这些数随模型输出变，断言只看非零与 `done_ok=True`。
- **暂停路由的边界。** `curl -s -X POST http://127.0.0.1:18765/api/answer/pause -H 'Content-Type: application/json' -d '{}'` → 400 `{"ok":false,"error":"缺少 session_id+turn 或 stream_id"}`；对一个不存在的 `stream_id` 发同一请求 → 200 `{"ok":true,"paused":false,"already_done":false}`。这两条不需要模型。
- **真实作答 UI。** `python3 tests/manual/demo/shot.py --url 'http://127.0.0.1:18765/classic?q=阿Q为什么挨打&instant=1&top=1' --scroll stage4 --out "$EV/classic-shot-1440x900.png"`。脚本只等 25 秒（`tests/manual/demo/shot.py:233`），流式期间就会截到「暂停」按钮、「正在生成回答正文…」和半截正文，并常先报 `answer ready=False cites=0 wait=25069 ms`；服务端随后才完成。末行仍应是 `CONSOLE ERROR 0`、`overflow=False`。2026-11-15 实测 `ready=False` 但流式正文已可见，别把 `ready=False` 判成页面坏。
- **导出。** 先拿一个已存在的 `id`（`/api/sessions` 任一 12 位 hex；没有就 `POST /api/run` 不带 `stateless` 建一个，验完删）。`curl -s -D- "http://127.0.0.1:18765/api/export?id=<id>&format=pdf"` → 400 `format 只支持 md 或 docx`；`format=md` → 200 `Content-Type: text/markdown; charset=utf-8`；`id` 不存在 → 404 `{"ok":false,"error":"会话不存在"}`。DOCX 走同一路由，`Content-Type` 是 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`。
- **运行记录。** `python3 apps/demo/runlog.py --root "$ROOT/工作区/检索/运行记录" doctor` 逐行 `[过] root` / `runs` / `index` / `blobs` / `refs`；`list` 在无记录时静默空输出。跑过一次后 `timeline <run-id> --full` 应含 `answer.request`、`prompt.built`、`model.start`、`answer.citations`、`answer.result`、`run.end`。传 `--root` 时默认目录就是 `$ROOT/工作区/检索/运行记录/`；不传 `--root` 的旧实例才落仓库 `apps/demo/运行记录/`。
- **历史 UI。** `python3 tests/manual/demo/check_session_ui.py --base http://127.0.0.1:18765` 逐项打「过/败」，末行 `ALL PASS`。它按 `/api/sessions` 里「3 轮会话」和「最后一轮带多书 sources」动态选样本；找不到时分别是 `没有找到可用的 3 轮会话；先用 check_session.py 生成一轮` 与 `没有找到带 2 本以上 sources 的多书会话…`（exit 1），这是样本缺失，不是页面坏了。
- **候选书多选。** 需要 ≥2 本候选书才会出现可多选的书卡；内置 `sample` 只有 `B0001`，所以本机临时根只验到单书结果。要做多选 UI，先往 `$ROOT` 装第二本书再跑；否则记 `ui-unverified`，原因写「样书 1 本，候选书多选前置不满足」。
- **不留外部请求。** `check_session_ui.py` 同时断言控制台 0 错误、页面不向外域发请求。
- **回答合同与 SSE/暂停回归。** `uv run --frozen pytest -q tests/test_demo_answer_contract.py` 覆盖 `test_server_api_answer_sse_and_json_compat`（527）、`test_server_api_answer_pause_and_resume_sse`（671）、`test_stream_citations_stage_emitted_once_after_answer_closes`（792）；`tests/test_demo_model_call.py` 钉流解析与工具事件。旧 `tests/acceptance/demo/test_answer_anchored.py` 已删，别再按它打命令。
- **混合检索。** 有索引与服务时，`curl -s -X POST http://127.0.0.1:18765/api/run -H 'Content-Type: application/json' -d '{"question":"阿Q为什么挨打","thinking":"low","stateless":true}' | jq '{session_id,plan:{fusion:.plan.fusion,vec_used:.plan.vec_used,vec_ms:.plan.vec_ms,vec_hits:.plan.vec_hits}, hit:.plan.chapters[0].selected[0].chapter_path}'` 应给 `session_id:""`、`fusion:"hybrid"`、`vec_used:true`、`vec_hits>0`、命中 `books/B0001/md/06_第六章从中兴到末路.md`。把 18780 停掉再打同一请求，应降为 `fusion:"bm25_only"`、`vec_used:false`、`vec_hits:0`，而 `plan.chapters` 仍非空。
- **Host。** `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: example.com' http://127.0.0.1:18765/` 输出 `403`（2026-11-12 晚补的 `ALLOWED_HOST`；旧文档的 200 已作废）。

## Gotchas

- 传 `--root` 时会话库在 `$ROOT/工作区/检索/sessions/`，与操作员隔离；不传 `--root` 的旧实例才落仓库 `apps/demo/缓存/sessions/` 并照样列出操作员的会话。验证一律传 `--root`，收尾只删**本轮自己建的**会话（`POST /api/sessions/delete`，一次 1–100 个 id）。
- 演示页的根解析是显式 `--root` 优先，不传才看 `BOOKS_ROOT`/仓库 `.env`/向上找 `books.toml`；它不经过 `Library`。分层读与首页相反，`Library`/`find_root()` 先看 `BOOKS_ROOT`，会盖掉 `--root`。本轮仍把两者都指到 `$ROOT`，避免不同服务读到不同根。
- `/classic` 现在发 `stateless:true`：`/api/run` 不建会话（`apps/demo/server.py:873`），`/api/answer` 落到 `answer_once`，用 `stream_id` 存暂停 checkpoint。旧 Gotcha「经典页建隐藏会话、turn 永远 pending」已作废；经典页的 turn 不进历史，也不能导出，这是设计。
- `tests/manual/demo/shot.py` 的 `--out` 写相对路径时会落在 `tests/manual/` 下，不是仓库根；截图后先确认文件在哪。25 秒的答案等待上限也在这里，流式时代会截到半截正文，不要据 `ready=False` 判失败。
- `turn["run"]` 只出现在单会话接口 `/api/session`，列表接口 `/api/sessions` 不返回它；要找某轮的 runlog id 得先拿会话。
- 传 `--root` 时 `terms/`、`answers/`、`related/` 都在 `$ROOT/工作区/检索/` 下，不再跨实例命中；不传 `--root` 才回落到仓库 `apps/demo/缓存/`。暂停 checkpoint 另在进程内（`PAUSED_CHECKPOINTS`，TTL 1800 秒），重启服务即丢。
- `tests/manual/demo/shot.py` 默认打 `http://127.0.0.1:8765/classic`，也就是操作员实例。必须显式 `--url` 指到本轮端口。
- `apps/demo/runlog.py` 在传 `--root` 的实例上默认读 `$ROOT/工作区/检索/运行记录/`；只有不传 `--root` 的旧实例才读仓库 `apps/demo/运行记录/` 并写进操作员共享记录。`BOOKS_RUNLOG_ROOT` 仍可单独覆盖，验证时不要对默认目录跑 `prune --apply`。
- `tests/manual/demo/check_session_ui.py` 的 `SESSION` / `SESSION_MULTI` 常量每次运行都会被覆盖，不是「优先用环境变量」；没有匹配会话时报的是「找不到会话」，不是页面坏了。
- 示例问句只在 `/classic`；在 `/` 上 `grep 'class="sample"'` 得 0，是正常的，不是模板没渲染。
- 没有模型 key 时，演示页**不会**报「配置错误」：默认直连后端记 `模型调用失败：…`，经典页标「降级」并改用直接词匹配。别拿「配置错误」这四个字做断言，也别看到 `rc=N` 就以为默认路径还在跑——它只在 `ANSWER_BACKEND=pi` 时出现。
- 旧会话可能出现 `incomplete`/`run_error`，这两个值只从旧数据派生；新写入是 `pending/running/ready/error/interrupted/paused`，中断文案是「服务重启，上次回答已中断」（`apps/demo/session.py:40-46`）。
- SSE 的 `done` 事件把结果放在**内层** `data`（`{"type":"done","data":{"ok":true,"answer":"…"}}`）；`paused` 可能先来一条扁平事件、再来一条带内层 `data` 的，解析时两种都取。`curl -N` 不加 `-s` 会在 stderr 打进度，别把它当响应体。
- `/classic` 的 `?q=&instant=1&top=1` 是自动跑一条问题的入口，链接一打开就花一次模型调用。
- 向量降级是静默的：`fusion:"bm25_only"` 可能因为索引缺失、embedding 服务离线或空查询，接口不告诉你原因；查 `meta.embed` 或 `vec_search.health_info()`，别只看 `vec_hits=0`。`BOOKS_EMBED_URL` 缺省是操作员常驻的 `127.0.0.1:18080`，验证必须显式指到本轮端口或 stub。