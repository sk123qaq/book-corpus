# 书库检索（CLI）

两个命令回答「哪一段/哪一章/哪本书」：`book-corpus search` 直接给命中段，`book-corpus query`
先做书级规划再选章。两者都只读 `build/检索/index.db` 与 `build/library.db`，不写库；混合检索
在向量索引与 embedding 服务都可用时自动叠加，否则静默退回 BM25。

## Sub-features

- `search-hits` 段检索：位置参数按空白/逗号拆词组；CJK 三个字以上按相邻二元组短语匹配。每条一行 `path:line score` 加 150 字片段。
- `search-book` `--book` 把段命中按书汇总，输出 `uid +N 段 score best_path:line`。
- `search-options` `-n` 默认 20，`--book-score mix|sum`（默认 `mix=0.7*最好段+0.3*段落均值`），`--rerank` 加定义式/引号/卷首特征，`--pool` 默认 `medium`（2000 段），`--min-chars` 默认 40。
- `search-expand` `--expand` 并上 `build/检索/expand` 的扩展文本，`--expand-kind both|prefix|queries`。
- `query-plan` 书级规划：最多 5 轮词法循环，stop 为 `C` / `A+B` / `cap` / `exhausted`；默认 `--book-score best_support`、`-n 5`、`--max-chapters 3`、`--chapter-selector score`。
- `query-scope` 内部范围参数：`book_query.Planner(scope={uid,...})` 与 `book_cards.run(..., scope=...)` 在词法/向量命中合并前先过滤到书目集合，demo 的 `scope.books` 走这条；CLI 没有 `--scope` 开关，别在命令行找。
- `query-hybrid` 规划后跑向量检索：段、章、书三层 RRF（书 k=30、`w_vec=1.35`；章 k=5、最多 10 章）。成功时 JSON 顶层 `fusion:"hybrid"`、`vec_used:true`、`vec_ms`、`vec_hits`；失败/缺索引时 `fusion:"bm25_only"`、`vec_used:false`、`vec_hits:0`，章节仍由词法给出。
- `query-cards` 书卡：`--cards` 默认开，`--no-cards` 关；触发原因有 `margin`、`types`、`long`、`concept`、`title_ambiguous` 等。文本在 stderr 打 `书卡：N 张，原因 …` 或 `不弹卡：书名/作者/原句提示`，`--json` 顶层带 `cards`。
- `query-expand` `--expand-on-demand` 选中后跑 B4 按需扩展（默认关，会调模型）。
- `query-explain` `--json` 给 `ranking`、`chapters`、`rounds`、`timings`、`fusion` 等字段，`--explain` 打规划解释。

## How to get to it (user POV)

- 找原句或某段：`book-corpus search "你那里配姓赵" -n 5`。
- 只看某本书命中多少段：`book-corpus search "你那里配姓赵" --book`。
- 让工具先找书再选章：`book-corpus query 为什么 赵太爷 姓赵`。
- 要机器可读结果：`book-corpus query ... --json --no-cards`。
- 没有浏览器面；输出在 stdout，进度与书卡在 stderr。

## Driving it with CLI（一次性根 + 私有 embedding 端口）

Preconditions:

- 按 [README](./README.md) 建一次性根并装样书：
  `ROOT=/tmp/bc-verify-$RUN_ID; mkdir -p "$ROOT/run"; export BOOK_CORPUS_ENV="$ROOT/run/env"; unset BOOKS_ROOT BOOK_CORPUS_DATA; uv run --frozen book-corpus init "$ROOT" --no-pdf-venv; uv run --frozen book-corpus sample --root "$ROOT"`。
- **始终显式设** `BOOKS_EMBED_URL=http://127.0.0.1:18780`（或本轮 stub 端口）。缺省 `18080` 是操作员常驻的 llama-server，不设会打到真实语料/真实服务。
- 词法用例不需要向量索引；混合用例先按 [embed.md](./embed.md) 对 `B0001` 建索引并起 18780，或使用该文件的 stub 方案。仓库 `.env` 若有 `BOOKS_ROOT`，一次性根验证时同时 `export BOOKS_ROOT="$ROOT"`，不要只 `unset`，避免命令在不同入口读到活库路径。
- 所有命令都在工具仓根跑；`--root "$ROOT"` 指数据根。`book-corpus run` 会导出 `BOOKS_ROOT`，但 `search`/`query` 不是 `run` 的别名，直接给 `--root` 最稳。
- 证据目录先定：`EV="$PWD/artifacts/verify/$RUN_ID/search-query"; mkdir -p "$EV"`。

- **段检索。** `uv run --frozen book-corpus search 你那里配姓赵 --root "$ROOT" -n 5`。实测 stderr `你那里配姓赵  1 条  0 ms`，stdout ` 1 books/B0001/md/01_第一章序.md:19  5.55` 加片段。段数随合并策略变化时断言 `[1-9][0-9]* 条`，别死等 `1`。
- **按书汇总。** `uv run --frozen book-corpus search 你那里配姓赵 --root "$ROOT" --book` → ` 1 B0001  +1 段  5.55  books/B0001/md/01_第一章序.md:19`。
- **零命中不报错。** `uv run --frozen book-corpus search 不存在的词组 --root "$ROOT"; echo "exit=$?"` → stderr `不存在的词组  0 条  0 ms`，exit 0。空数据根也可跑：`cli._init_build` 会建空库，输出 `0 条` 而不是崩。
- **JSON 合同。** `uv run --frozen book-corpus search 你那里配姓赵 --root "$ROOT" --json -n 2 | jq '{n:(.hits|length), first:.hits[0]|{path,line,score,book}}'`。顶层只有 `query`/`ms`/`hits`；命中键是 `id`、`path`、`book`、`line`、`text`、`bm25`、`kana`、`coverage`、`score`；扩展档真的命中该章时，那条再多一个 `expand` 文本。
- **展开的陷阱。** `uv run --frozen book-corpus search 你那里配姓赵 --root "$ROOT" --expand --json` 在没有扩展行时仍会把所有分数改写成 RRF 小值（实测 `0.016667 ≈ 1/60`），且不警告。看到 `--expand` 后分数骤降不是排序坏了，是换了口径；要比较段落分数就别混跑。
- **书级规划（纯词法）。** `uv run --frozen book-corpus query 为什么 赵太爷 姓赵 --root "$ROOT" --json --no-cards > "$EV/query.json"`；`jq '{fusion,vec_used,vec_hits,stop,best_round}'` 在无向量时应为 `bm25_only/false/0/A+B/1`（stop 随问句变化，别写死）。`ranking[0]` 有 `book`、`title`、`best`、`hits`、`coverage`、`score`；章节在 `chapters[0].selected`。
- **书卡触发。** `uv run --frozen book-corpus query 为什么 赵太爷 姓赵 --root "$ROOT" --json > "$EV/cards.json"`；`jq '{trigger:.cards.trigger,reasons:.cards.reasons,n:(.cards.cards|length),first:.cards.cards[0].title}'` 实测 `true / ["concept"] / 1 / "阿Q正传"`。文本版 stderr 是 `书卡：1 张，原因 concept`。**1 本书时 1 张卡正常**；`MIN_CARDS=5` 只约束独立 `book-cards` CLI 的 `--cards`，不约束 `query`。
- **书名提示不弹卡。** `uv run --frozen book-corpus query 阿Q正传 --root "$ROOT" 2>&1 | grep '不弹卡'` → `不弹卡：书名/作者/原句提示`。
- **混合检索。** 先确保 `emb.db` + `vectors.f16` 存在且 18780 健康，再 `BOOKS_EMBED_URL=http://127.0.0.1:18780 uv run --frozen book-corpus query 为什么 赵太爷 姓赵 --root "$ROOT" --json --no-cards > "$EV/hybrid.json"`。断言 `fusion=="hybrid"`、`vec_used==true`、`vec_hits>0`（实测 34）、`ranking[0].hits>0`（实测 28）；命中向量的章带 `vec_score`，所有融合章带 `fused_score`（实测第一章 `fused_score=0.391667`）。**向量独有的章带 `hits:0`**；hybrid 融合后的词法章可能没有 `hits` 键，判断向量参与看 `vec_score`/`fused_score`。
- **向量降级。** `kill $(lsof -t -i:18780)` 后同一 query → `fusion=="bm25_only"`、`vec_used==false`、`vec_hits==0`、`vec_ms==0.0`，章节仍非空。再 `mv "$ROOT/build/检索/embeddings/emb.db" "$ROOT/run/emb.db.off"` 重跑，仍应 `bm25_only`；验完移回。静默降级的原因（无文件/服务死/空查询）接口不区分，查 `vec_search.health_info()`：
  `BOOKS_EMBED_URL=http://127.0.0.1:18780 BOOKS_ROOT="$ROOT" uv run --frozen python -c 'import sys,json;sys.path.insert(0,"src/bookcorpus/scripts");import vec_search;print(json.dumps(vec_search.health_info(),ensure_ascii=False))'` → `{"vectors":"ok","server":"offline","ready":false,...}`。
- **离线合同。** `uv run --frozen pytest -q tests/test_vec_search_hybrid.py` → `5 passed`（`test_rrf_fusion_book_chapter_passage`、`test_pick_passages_hybrid_and_fallback` 等），钉住融合与降级；不需要索引或模型。
- **独立书卡 CLI。** `uv run --frozen book-corpus run book_cards --help` 可见 `--cards` 5–8 的边界；stdout 非 TTY 时即使不给 `--json` 也输出 JSON（`src/bookcorpus/scripts/book_cards.py:998`），脚本断言要按 JSON 解。

## Gotchas

- `search` 与 `query` 的书级默认口径不同：`search --book-score` 默认 `mix`（旧口径），`query --book-score` 默认 `best_support`。同一问句两个命令的书序可能不同，不是其中一个坏了。
- `query --json` 的顶层没有 `books` 键：书在 `ranking`，章在 `chapters[0].selected`。旧脚本若读 `.books[0]` 会拿到 null。
- `query` 的 `--book-score` 有 `sum/mean/best/mix/best_support/best_top3_support/rrf5/best_support_length` 八个值；`search --book-score` 只有 `mix/sum`。别把两边参数互抄。
- 混合检索要求三件事同时成立：`emb.db` 存在、`vectors.f16` 非空、`POST $BOOKS_EMBED_URL/v1/embeddings` 在 5 秒内回 1024 维。任一不满足都静默 `bm25_only`；`vec_hits=0` 本身不是原因。
- `BOOKS_EMBED_URL` 不设就会用 `127.0.0.1:18080`，那是操作员的常驻服务。验证必须显式指到本轮端口或 stub；否则你测的是另一份索引/另一个模型。
- `--expand` 即使扩展档为空也会走 RRF 重打分，分数从 BM25 几百分变成 `1/(60+rank)` 量级，且没有警告。要判断扩展是否真的命中，看 `hits[].expand` 字段，不要只看分数。
- `book_cards` 的 5–8 张是独立 CLI 的 `--cards` 边界，不是 `query` 的保证。样书只有 `B0001` 时 `query` 出一张卡是正常的；要验证多卡/候选书多选，先往根里装第二本书。`scope` 是 demo 的内部下推参数，CLI 的 `search/query` 没有它；验多书范围要用 `/api/run`，不是命令行。
- 空数据根是合法的：`search`/`query` 会建空 `library.db`/`index.db` 并输出 `0 条`。别把空根当「索引坏了」；它和 `doctor` 的 `db: empty` 是两种状态。
- `--route` 会联网调模型，`--expand-on-demand` 也会；纯离线回归只跑 `search` 与 `query --no-cards`，或直接跑 `tests/test_vec_search_hybrid.py`。
- 结果片段里的 `path:line` 是 `books/<uid>/md/...` 相对路径；要打开原文先拼 `$ROOT/`，别按仓库根找。