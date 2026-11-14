# 事实（生成）

脚本、别名、端口与版本都从代码读；本文件由 `book-corpus docs write` 重写。


<!-- facts:start -->
- `book-corpus` 版本：`0.2.0`
- Python：`>=3.11`
- 本块由 `book-corpus docs write` 生成，`docs check` 对账；不要手改。

## 应用与默认端口

| 应用 | 默认端口 | 入口 |
|---|---|---|
| `daily` | 8772 | `python3 apps/daily/server.py` |
| `demo` | 8765 | `python3 apps/demo/server.py` |
| `home` | 8773 | `python3 apps/home/server.py` |
| `ops` | 8770 | `python3 apps/ops/server.py` |
| `reading` | 8771 | `python3 apps/reading/server.py` |

## 脚本与别名

| 脚本 | 别名 | 说明 |
|---|---|---|
| `audit_old_md.py` | — | 旧 md 的文本量审计（批次 md拆分-2026-10-18 之前就有的那 494 本）。 |
| `audit_state.py` | — | 只读状态审计：人账、库、library.db、index.db、expand、视图是否互相对得上。 |
| `backfill_readme.py` | — | 给缺 README 的 md 目录补目录页。只写 README.md，不动正文与 .manifest.json。 |
| `book_cards.py` | — | B5 书卡与用户反馈：首轮之后按触发规则弹 5 到 8 张卡，用户选书/排除类型/换一批。 |
| `book_expand.py` | — | 按需扩展：只给选中的章做摘要，渐进披露，不整章投喂。 |
| `book_query.py` | `query` | 查询规划：两套词、两段检索、最多 5 轮词法循环（B2.5）。 |
| `book_rank_bench.py` | — | 书级排序方案对比台。同一概念，八种书级打分，看前 10 名怎么变。 |
| `book_readme.py` | — | README 选章：把 md/README.md 当章节目录读，按检索分挑前三章。 |
| `book_score.py` | — | 书级打分：把段落命中聚成书，按多种书级方案排序。 |
| `book_score_bench.py` | — | 书级打分 sum vs mix 的对比（EXPECT 里 11 个概念，正解书未入库的自动略过）。 |
| `book_title.py` | — | 书名解析：问句里点到哪本书，给出 exact / ambiguous / missing。 |
| `build_expand.py` | — | P1.5 文档扩展：给每章生成「主题前缀」，写进独立索引，不碰正文。 |
| `build_index.py` | `render` | 扫描 books/**/html/，给每本有阅读版的书重建 html/index.html，并重建 build/阅读版索引.html。 |
| `build_library.py` | `library` | L1/L5 索引构建。人账 + 机账 + 磁盘 -> 目录/library.db（可随时重建）。 |
| `build_para_index.py` | `index` | 建 目录/检索/index.db：段落倒排（CJK bigram FTS5）+ 概念剖面。零第三方依赖。 |
| `build_views.py` | — | 重建 build/views/：主题按概念、奖项按奖项名，各建一棵指向 books/<uid> 的软链树。 |
| `check_p1.py` | — | P1 验收：2 字词可查且有序、原词类不低于裸 grep、查询边界回归。 |
| `classify_books.py` | — | 给书做学科分类（一级 + 二级），批量提问，带缓存。 |
| `classify_model.py` | — | 走 pi 的一次性书目分类：一次调用给出一级、二级、置信与理由。 |
| `collection_coverage.py` | — | 合集重拆后的覆盖率验收：源书每页的 10 个 20 字 CJK 锚点在给定 md 目录里命中多少。 |
| `concept_expand.py` | — | 概念表展开：键 → 同义词组 + 排除正则。defpass 用它；qsearch 自己解析一遍概念表（见 qsearch.load_concept）。 |
| `concept_route.py` | — | 把一句人话路由到 概念表.tsv 的键，再交给 qsearch。 |
| `corpus_stats.py` | `stats` | 现测书库规模与结构：书目数、md 数、章节数、体积、未收录项、长书与系列分布。只读。 |
| `daily.py` | `daily` | 日课：每天从书库里挑一章，生成一期 300 到 500 字的稿子。 |
| `defpass.py` | — | 定义/段落级检索：两阶段。 |
| `doc_numbers.py` | `numbers` | AGENTS.md 现状数字块：现取读数、渲染成固定一段、比对或重写。 |
| `docs_to_md.py` | — | 把 docx/xlsx/xls/pptx/txt/md 变成书库的 md 契约：README.md + images/ + .manifest.json。 |
| `embed_corpus.py` | `embed` | 全库 md 正文 embedding：llama.cpp Vulkan 服务 + float16 向量库。 |
| `eval_report.py` | — | 把 eval-*.tsv 汇总成 §2.2 那张表。只读，缺档就留空。 |
| `eval_retrieval.py` | `eval` | 四档基线评测：A grep 字面 / B rg 字面 / C defpass 概念组 / D grep 近义词组。 |
| `gen_book_catalog.py` | `catalog` | 从 build/library.db 出书目总表，并核对每条记录能落到磁盘。 |
| `ingest.py` | `ingest` | 收件箱入库：把 库/_收件箱 的文件变成人账一行、book.json 与 source/ 下的主源。 |
| `install_collection.py` | — | 把 split_collection.py 的逐册产物装进 库/<path>/md/，旧版本改名留在批次区。 |
| `layout.py` | — | 工作区树形。树只在这里命名一次，别的模块不许再拼 库/ 目录/ 视图/。 |
| `lib_fts_cjk.py` | — | CJK bigram 索引与查询的同一份切分。索引侧与查询侧都从这里拿。 |
| `lq.py` | — | 只读查询 目录/library.db。默认每行一条记录，字段用制表符分隔。 |
| `md_link_health.py` | — | 全库 md 链接健康检查（只读，不改任何文件）。 |
| `md_review_report.py` | — | 待查 md 的证据汇总：把 verify/audit 的报警落到可判断的事实上。 |
| `mineru_book.py` | — | 整本走 MinerU 的 OCR 路线：切片 → 提交 → 逐页回填 → 与 Paddle 路线同一套拼装与门槛。 |
| `ops_pdf_triage.py` | — | 单本 PDF 分诊：普查 + 文本层/纸面探针 + 3 张缩略图 + 模型建议。只读源文件。 |
| `ops_status.py` | `status` | 只读状态：操作台顶栏与总览要的数字，一次采集。 |
| `paddleocr_pilot.py` | — | PaddleOCR-VL 云端试跑：切片、提交、轮询、下载，每页 md 与图片落盘。只读库，写批次目录。 |
| `pdf_apply_review.py` | — | 把人工复核的 verdict 与图片占比并回分诊表，出最终路线表 triage_final.csv 与不转清单 skip_list.tsv。 |
| `pdf_audit_volume.py` | — | 文本层路线的 PDF 侧审计：md 正文占源书文本层字数的比例，加逐页锚点覆盖率。 |
| `pdf_census.py` | — | PDF 普查：逐本测文本层、字体、扫描图，只读。输出一行一本书的 JSONL。 |
| `pdf_lang_probe.py` | — | 探页定语种：给批次里还没有 md 的 PDF 各抽 1-3 页做 OCR，按假名/汉字/拉丁比例判语种。 |
| `pdf_md_batch.py` | — | 批量把 primary 源是扫描 PDF 的书 OCR 成 md（方案 §7 的转换接口）。 |
| `pdf_ocr_to_md.py` | — | 把逐页 OCR markdown 拼成书库的 md 契约：README.md + images/ + .manifest.json。 |
| `pdf_scan_probe.py` | — | 扫描件的纸面性质探针：抽样页看是「文字页」还是「图片页」，只读。 |
| `pdf_text_probe.py` | — | 文本层 PDF 的深测：目录书签、逐页文本分布、乱码率、字号分布。只读。 |
| `pdf_text_to_md.py` | — | 把纯文本层 PDF 抽成库内的 md 契约：README.md + images/ + .manifest.json。 |
| `pdf_triage.py` | — | 把 census 与两个探针合成一张分诊表：每本书走哪条转换路线。只读输入，写 triage.csv。 |
| `pool_bench.py` | — | 取数上限（pool）的档位标定台。 |
| `qsearch.py` | `search` | 词法检索：段落倒排 + 组覆盖度重排，书级聚合。 |
| `qsearch_plan.py` | — | 按检索计划跑查：多组词分开检索，章级 RRF 融合，再用组覆盖与原词加权。 |
| `query_expand.py` | — | 查询侧扩展：把用户的口语问法换成 4 条书里可能出现的话，再并回段落倒排。 |
| `reading_index.py` | — | 分层读的章表索引：看状态或整库重建。 |
| `redo_compare.py` | — | 重拆批次的验收闸门：逐个比「旧 md」与「暂存新 md」的源页覆盖率。 |
| `rerank.py` | — | 交叉编码器精排封装：Reranker().score(query, texts) 按入参顺序返回打分。 |
| `retire_book.py` | — | 移出一本书：原书进 books/_冷宫/<书名>/source/，md、html、json 删除，账本同步。 |
| `scan_library.py` | — | L1 扫描旧布局（原书/ 原书pdf/ 书md/ 等顶层池）。旧目录已不在，只作留档。 |
| `split_md_batch.py` | — | 批量把 source 是 ebook 的书拆成 md（方案 §7 的转换接口）。 |
| `verify_phase.py` | `verify` | 收尾门槛：四个箱子，对任何新库都成立，没有「跳过」。 |
<!-- facts:end -->
