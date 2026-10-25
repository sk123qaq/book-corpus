# 书库项目

`~/文档/books` 是 22 GB 的个人电子书库。2026-10 按 `工作区/方案/设计方案-书目结构.md` 重构完毕。书、拆章 md、阅读版 html 收进每本书一个目录，奖项降为字段，SQLite 做可重建索引。

新会话先读本文件，再按「文件地图」与「脚本」按需深入。方案 §8 开头有两段执行修订，正文里写死的原型数字按修订块读。

## 现状（2026-10-24 实测）

1693 本书，1571 本有 md，122 本待拆章（115 pdf、5 txt、2 djvu）；1009 条奖项记录；视图 1009 条软链。待拆章里 73 本 pdf 是「图册不转」（理由在 `目录/分类表.csv` 备注与 `工作区/批次/pdf-普查-2026-10-19/skip_list.tsv`），42 本 pdf 是日文原版暂缓（账在 `工作区/批次/pdf-ocr-2026-10-19/defer/lang.tsv`），待转 pdf 已清零（最后 1 本 B1556 于 2026-10-24 用 MinerU 整本落地）；5 txt 与 2 djvu 不在当前范围。数字随转换变化，重取用 `python3 工作区/脚本/gen_book_catalog.py`，它末行打印「未匹配 N 条」。

2026-10-21 冷宫：唐诗学书系（B1359，11,619 章）移出处理流程，原书留在 `库/_冷宫/唐诗学书系/`，md 已删，人账与检索层同步移除。书 1694→1693，有 md 的书 1379→1378，奖项与视图各 1010→1009。当天 OCR 续跑同时在往库里装书，md 本数继续变（10:05 为 1387），实时数以 `build_library.py` 输出为准。

2026-10-19 把 primary 源是 PDF 的 540 本分诊后开转（口径见 `工作区/批次/pdf-普查-2026-10-19/普查说明.md`）：文本层 55 本（`method=pdf-text`，13 本按目录切章、42 本单文件）与扫描件首波 105 本（`method=paddle-ocr`，全为单文件，4 本走 `overrides/` 受理）已入库。扫描件当日 16:23 撞平台每日 20,000 页额度停批。

2026-10-20 按操作人要求把日文原版暂缓（中文译本照做），语种由 `工作区/脚本/pdf_lang_probe.py` 探页按内容判，账落批次目录 `defer/lang.tsv`；当天再入库 69 本（`paddle-ocr`，1 本 B1321 走 `overrides/` 受理），14:47 又撞日额度。范围内（非日文）还剩 193 本 85,196 页，其中 6 本有半成品（补完约 380 页额度）；续跑照 `工作区/批次/pdf-ocr-2026-10-19/续跑计划.md`，额度用尽时接口回 HTTP 403 code 12001，不是 429。

2026-10-18 深夜把 11 本旧 md 重拆替换（4 本单 owner + 渡边淳一合集 7 本），其余旧 md 经逐本覆盖率比对与当前转换器产物逐字节相同，未动。账与结论见 `工作区/批次/旧md重拆-2026-10-18/重拆报告.md`。DB 的 `derived` 表（chapters/bytes/method）与 `目录/书目总表.csv` 已随之重建，`book-corpus-qa` 的 `corpus-map.md` 数字也用 `corpus_stats.py` 重测。

## 文件地图

| 路径 | 内容 |
|---|---|
| `库/` | 唯一权威内容区。一本书一个目录，含 `book.json`、`source/`、可选 `md/` 与 `html/`。`_` 开头的目录不是账上的书 |
| `库/_收件箱/` | 新书先落这里，入库走 `工作区/脚本/ingest.py`：`scan` 出计划，`apply` 落文件与账；换源用 `replace` |
| `库/_冷宫/` | 移出处理流程的书，只留原书文件。无 `book.json`、不在人账、不进检索与扩展。规则与账见 `库/_冷宫/README.md` |
| `目录/分类表.csv` | 唯一人账，一行一本书。uid、一级、二级、作者层、保留、正式书名、备注。只有它允许人改 |
| `目录/library.db` | 全库索引，SQLite。产物，可重建。查询用 `lq.py` 只读打开 |
| `目录/书目总表.csv` 与 `.xlsx` | 从 DB 导出的书目，含 md 状态、主源文件、路径 |
| `目录/对照表.csv` | L3 搬迁的旧路径到新路径映射，任何路径改写的依据 |
| `目录/` 其余 csv 与 jsonl | 分类预填、分类缓存、标注样本、搬迁计划、书目录清单、源文件清单、重复、损坏、路径冲突。L0 到 L2 的过程账，留档 |
| `目录/系列/` | 21 份合集的卷序说明 |
| `目录/概念表.tsv` | 检索层的概念键与同义词组，126 条。人写人改 |
| `目录/概念表-broad.tsv` | 宽词层实验表，10 个宽词。独立于 126 主表，未并入生产 |
| `目录/检索/README.md` | 检索层方案。P0 到 P5 的门槛、目标结构、实测基线 |
| `目录/检索/index.db` | 检索层索引，5.86 GB。段落倒排（FTS5 bigram）+ 概念剖面 + 章级扩展文本。可重建 |
| `目录/检索/踩坑记录.md` | 按类型分的踩坑账，六类四列（现象、根因、别再这么干、正确做法）。改检索脚本前先查 |
| `目录/检索/expand/<uid>.md` | 章级主题扩展的人读账，一本一份 |
| `目录/阅读版索引.html` | 有阅读版 html 的书的总索引 |
| `视图/奖项/<奖项>/<书名>` | 指向 `库/` 的软链树，随时可删可重建 |
| `工作区/方案/设计方案-书目结构.md` | 权威方案。L0 到 L5 门槛、目标结构、转换接口 |
| `工作区/演示-检索流程/` | 检索流程演示页。读真实 trace，五阶段展示从提问到原文，结果区接 book-corpus-qa 回答；`python3 工作区/演示-检索流程/server.py` 起 127.0.0.1:8765，`验收/check_demo.py` 自检 |
| `工作区/方案/决策记录.tsv` | 每个决定的理由与证据，执行期主账 |
| `工作区/脚本/` | 全部脚本，见下表 |
| `工作区/批次/奖项下载-2026-10-18/` | 奖项下载机账。`下载结果总表.csv`、`待下载清单.csv`、`work/` |
| `工作区/批次/md拆分-2026-10-18/` | 656 本 epub/mobi/azw3 拆章的机账。范围清单 `worklist.tsv`（只写一次）、`logs/<uid>.json`、`overrides/` |
| `工作区/批次/旧md文本量审计-2026-10-18/` | 拆章前 494 本旧 md 的文本量审计机账。`results.jsonl`、`summary.json`、`drill/`。重拆后的现状看 `旧md已重拆-说明.md` |
| `工作区/批次/旧md重拆-2026-10-18/` | 旧 md 重拆机账。`worklist.tsv`（162 单本）、`collections.tsv`（21 合集源/329 本）、`comparison.tsv` 与逐页明细、`overrides/`、`out/`（暂存与改前留档）、`重拆报告.md` |
| `工作区/批次/pdf-普查-2026-10-19/` | PDF 分诊与权威数字。`普查说明.md`（路线/门槛/成本）、`triage_final.csv`（540 行定稿路线）、`skip_list.tsv`（73 本不转）、`census.jsonl`、`text_probe.jsonl` |
| `工作区/批次/pdf-text-2026-10-19/` | 文本层 PDF 抽 md 的机账：`worklist.tsv`（56 本）、`logs/`、`说明.md`。落地 55 本 |
| `工作区/批次/pdf-ocr-2026-10-19/` | 扫描件 OCR 批次：`说明.md`、`worklist.tsv`（411 本 138,002 页）、`work/<uid>/`、`out/`、`logs/`、`overrides/`、`review/`、`tests/`、`续跑计划.md`、`wave_supervisor.sh` |
| `工作区/批次/qa-检索-2026-10-22-书级融合/` | 书级融合 B0-B6：书级打分、60 题 gold、B2.5 查询规划、B3 选章、B4 按需扩展、B5 书卡、B6 默认值；另含 B2 heldout 20 题（`book_gold-heldout.tsv`、`B2-heldout-说明.md`）。读数在 `live/`，报告是 `单元报告-B*.md`，`probe_b6.py --lanes/--gate/--perf` 可复跑 |
| `工作区/批次/qa-检索-2026-10-19/` | 检索层 QA 与 P3 批次账。`gold.tsv` 60 题、`eval-*.tsv` 读数、`P3-文献调研`/`P3-重排实测`/`P3-第1步-伪查询实验`/`质量审计-2026-10-21` 四份报告、`expand-stub-rows.jsonl`（空正文章剪除备份，别删）。查生成质量与剪除处置、查这批评测读数、恢复被剪的 expand 行，都进这里 |
| `工作区/中间态/` | 迁移前基线（`verify_phase.py` 依赖，别删）与 MinerU 中间产物，产物可删 |
| `工作区/废弃/` | `duplicates/`、`corrupt/`、`L4-前/` 改写前副本 |
| `../books.snapshot-2026-10-18/` | L0 硬链接快照，47916 个文件。搬错删错时的回滚副本 |

## 脚本

| 脚本 | 用途 |
|---|---|
| `lib_common.py` | 共享工具。路径常量、书名与作者清洗、`book_relpath`、md 目录计数 |
| `build_library.py` | 重建 `目录/library.db`（读 `库/**/book.json`）。`--emit-json` 同时同步每本书的 `book.json` |
| `ingest.py` | 收件箱入库：`scan` 出计划（sha256 去重、书名匹配、add/replace/conflict，add 调 pi 的 deepseek-v4.1-flash 预填 subjects 与置信度），`apply` 落文件、写 `book.json` 与人账行、记 journal，再重建 DB。`--root` 可指到临时目录做测试，`--no-classify` 跳过分类 |
| `lq.py` | 只读查询 DB。`lq.py [-H] "SELECT ..."`，字段用制表符分隔 |
| `gen_book_catalog.py` | 出 `目录/书目总表.xlsx` 与 `.csv`，并核对每条记录能落到磁盘。有未匹配时只报错，不覆盖旧目录 |
| `build_index.py` | 重建每本书的 `html/index.html` 与 `目录/阅读版索引.html` |
| `verify_phase.py` | L3 与 L5 的六箱门槛检查。源文件 inode、md 章节数、奖项 resolve、顶层结构、DB 重建一致、检索层（`--box 6`） |
| `defpass.py` | 段落级概念检索。`defpass.py <md目录> -g 词1,词2 -g 词3 -n 5`，或 `-c 概念键` 走概念表；`-w` 调段落宽度 |
| `migrate_layout.py` | L3 搬迁脚本（`--plan`、`--apply`、`--reverse`、`--views`）。搬迁已在 2026-10-18 执行完毕 |
| `l2_finalize.py` | L2 收尾。同名冲突加限定词、同步作者层，幂等 |
| `classify_books.py` | 模型分类（`opencode-go/deepseek-v4.1-flash`，默认；`--backend typesafe` 保留旧路），结果落 `分类缓存.jsonl`，`--report` 只看统计。`--merge` 以人账为底合并：已有行一个字段都不动，只追加 `--add-new` 里人账没有的 uid；清单里未入账的 uid 默认跳过 |
| `scan_library.py` | L1 扫描旧布局。旧目录已不在，只作留档；扫到 0 个源文件时报错退出，不覆盖 `目录/` 任何账 |
| `split_md_batch.py` | 拆章批次驱动。读 DB 出待拆清单、转 EPUB、拆章、verify、audit、原子进库。verify 或 audit 不过时自动叫 pi agent 修（每本 1 次、整批 3 次、900 秒），修完重跑检查再决定是否落地。`--report` 只看状态；`worklist.tsv` 存在时即批次范围 |
| `redo_md_scope.py` | 从库内旧 md 目录生成重拆范围账本（单 owner 写 worklist，合集写 collections）。只读库，`--apply` 才写批次目录 |
| `redo_compare.py` | 重拆验收：旧 md 与暂存产物的逐页锚点覆盖率差。下降的书不落地 |
| `install_collection.py` | 把 split_collection 的逐册产物装进各书 md/，旧版改名留档，写 manifest |
| `collection_coverage.py` | 合集重拆的覆盖率验收（源书逐页锚点在给定旧/新 md 目录里的命中数） |
| `md_review_report.py` | 只读取证：正文字数、图片路径字数、跨文件重复片段、自链、spine 未收录页 |
| `corpus_stats.py` | 重测 `book-corpus-qa` 的 corpus-map.md 里那批数字 |
| `audit_old_md.py` | 旧 md 文本量审计。逐页锚点比对源书，报「缺页」并按 README 未收录与卷首卷尾分类。只读 |
| `drill_md_gap.py` | 单本下钻：源书每页在 md 里能找到多少，定位丢的是哪些页。只读 |
| `md_link_health.py` | 全库链接健康：章节文件自链数、README 目录段是否全部成链接、目录首行缩进。只读。装了 commonmark 按渲染结果判，没装按 CommonMark 链接目标规则判 |
| `pdf_lang_probe.py` | 探页判语种，账落 `工作区/批次/pdf-ocr-2026-10-19/defer/lang.tsv`。`--force` 重探不覆盖已判定语种；额度用尽即停止派发 |
| `pdf_census.py` | PDF 普查：页数、字体、逐页文本量、图片清单与分辨率。出 `census.jsonl`。`--uid`/`--limit` 子集跑必须给 `--out` |
| `pdf_scan_probe.py` | 扫描件探针：抽页渲染 70dpi 算 ink 与文字行带数，给分诊用 |
| `pdf_text_probe.py` | 文本层探针：书签、逐页文本分布、乱码率、字号种类 |
| `pdf_triage.py` / `pdf_apply_review.py` | 按指标分诊并并人人眼复核结论，出 `triage.csv`/`triage_final.csv` 与 `skip_list.tsv` |
| `pdf_text_to_md.py` | 文本层 PDF → crate（自带可用目录的按目录切章，其余单文件），`--structure auto\|single\|chapters` |
| `pdf_audit_volume.py` | 文本层路线的 PDF 侧审计：字数比 + 逐页锚点覆盖率 |
| `pdf_ocr_to_md.py` | 逐页 md → crate 的拼装器（`--structure single\|chapters`，默认 single）。README/manifest 生成器被 `pdf_text_to_md.py` 复用 |
| `paddleocr_pilot.py` | PaddleOCR-VL 单段流水线：切片、提交、轮询、下载、`--job` 续跑。token 在 `~/.config/PaddleOCR/api_key` |
| `pdf_md_batch.py` | PDF 批次驱动（`--route ocr\|text`）：切片、提交/续跑、合并、拼装、体检、原子落地、logs、report。失败分 `quota/queue-full/transport/fatal/other` 五类。Paddle 读出 0 字而纸面有字迹的页自动交 MinerU 补投一次，读不出就按跳过受理（账 `skipped-pages.tsv`）。MinerU 没跑（CLI 缺/额度封顶/切片超限）不算跳过受理，可疑页留人工复核 |
| `eval_retrieval.py` | 四档检索基线：A `grep -rlF`、B `rg -l -F`、C `defpass --concept`、D 近义词组 grep。出 `eval-baseline.tsv` 与 `eval-summary.tsv`（recall@10/@20、MRR、无原词进前 20） |
| `lib_fts_cjk.py` | CJK bigram 索引与查询的同一份切分，`build_para_index.py` 与 `qsearch.py` 共用 |
| `build_para_index.py` | 建 `目录/检索/index.db`：段落倒排（FTS5 bigram）+ `book_concept` 概念剖面。只读 `库/`，`--rebuild` 全量 |
| `concept_expand.py` | 概念表展开成同义词组与排除正则，`--audit` 报零命中的别名 |
| `concept_route.py` | 问句路由到概念表 3 个键，缓存 `目录/检索/route_cache/`（键含概念表内容指纹）。`qsearch --route` 与评测 L/M 档依赖它 |
| `query_expand.py` | 查询侧扩展：一次模型调用扩 4 条短语再融合，缓存 `目录/检索/expand_queries/`，`--phrases-only` 只打短语 |
| `fix_old_md_links.py` | 修旧 md 的自链与 README 裸目标（加尖括号），tmp+replace。`--dry-run` 与 `--apply` 互斥 |
| `readme_link_fix_check.py` | 核对 README 目录链接修复的读数；重跑前备份目录不在时 `--verify` 报错退出 |
| `restore_worklist.py` | 按批次 `logs/<uid>.json` 重建被冲掉的 `worklist.tsv`，与 logs 交叉核对不通过不写盘 |
| `book_score.py` | 书级打分与八种方案，默认 `best_support`；`book_score.py "词" -n 10 --explain` |
| `book_query.py` | 书级查询默认入口：B2.5 两段词 + 5 轮词法循环；触发时默认弹 5-8 张书卡，`--no-cards` 关；`--expand-on-demand` 才跑 B4 |
| `book_readme.py` | README 目录解析、章标题/卷名/首末章、选章；B3 用 |
| `book_expand.py` | 按需扩展 L0-L4；默认档 3 轮、60 秒预算；默认关闭 |
| `book_cards.py` | B5 书卡：触发、5-8 张卡、选书/排除类型/换一批、反馈；`--gate`/`--perf` |
| `qsearch.py` | 词法检索：段落倒排 + 组覆盖度重排，`--concept` 走概念表，`--book` 出书级聚合（`--pool low\|medium\|all` 取数 400/2000/不限，`--book-score mix\|sum` 书级打分） |
| `qsearch_plan.py` | 按检索计划跑：多组词 + 概念/宽词，章级 RRF；`broad` 只在计划显式给出时展开，默认不启用 |
| `book_rank_bench.py` / `pool_bench.py` / `book_score_bench.py` | 书级排序的三个对比台：打分方案、取数档位、正解名次。只读 |
| `build_expand.py` | 文档扩展（`--kind prefix\|queries`）：主题前缀或伪查询写进 `index.db` 的 `expand` 表与 `目录/检索/expand/<uid>.md`。`--provider commandcode\|mimo\|opencode` 默认 commandcode，mimo 有内容风控没进主流程。`--rebuild-md` 按库重写账文件。`--only-file` 按清单只扩指定章；`--reindex-fts` 重建 expand_fts；`--prune-stub` 备份并剪空正文章行。空正文章（图片页/标题页）跳过不生成，质量账见 qa-检索批次的《质量审计-2026-10-21》 |
| `sem_index.py` | 段落向量（`bge-m3`）。**待建，向量层当前不做** |
| `build_topic_views.py` | 重建 `视图/主题/<概念>/` 相对软链与每概念清单，排序与 `qsearch --book` 一致 |
| `eval_report.py` | 把 `eval-*.tsv` 汇总成 README §2.2 的两张表 |
| `check_p1.py` | P1 验收：2 字词、译名墙案例、原词类不低于裸 grep |

## 写入规则

- 改已有书的分类、书名、保留策略改 `目录/分类表.csv`，然后跑 `build_library.py --emit-json` 重建 DB 与 `book.json`。新书不要手填人账，走 `ingest.py` 的 `add` 或 `apply`。目录名不会自动跟着改，路径是 `分类表.csv` 的投影，改名的规则与未决项见方案 §4 与 §10。
- 写文件用 tmp 加 `os.replace`。库里的旧文件与 `../books.snapshot-2026-10-18` 共享 inode，截断式原地写会同时改掉快照。
- 一个账本一个写者。人账的已有行只由人改；新行由 `ingest.py` 与 `classify_books.py --add-new` 追加，两者写同一个文件，同一时刻只跑一个。脚本写产物（`library.db`、`book.json`、`目录/` 下的导出），批次机账只追加。
- 拆章产物落 `库/<一级>/<书名>/md/` 才算落地，`v_pending_md` 视图只认这个目录。
- 把一本书移出流程：删 `md/`，`book.json` 改名 `book.json.归档`，书目录移入 `库/_冷宫/`，从 `分类表.csv` 删行，跑 `build_library.py --emit-json`，再重建 `index.db` 与 `视图/`。步骤与理由见 `库/_冷宫/README.md`。

## Git

2026-10-25 起是 Git 仓库，分支 `main`。只跟结构与账，不跟书的正文。

- 跟：每本书的 `book.json`、`目录/` 的人工账与小型过程账、`工作区/` 的脚本与方案、操作台代码与测试、批次的小型机账（worklist、说明、triage、logs、review）。
- 不跟：`库/**/source/`、`md/`、`html/`、`库/_收件箱/`、`库/_冷宫/`（只留 README）、`目录/检索/`（只留 `README.md` 与 `踩坑记录.md`）、`视图/`、批次 `work/ out/ live/ tmp/ drill/`、模型与虚拟环境、运行态。
- 规则在根 `.gitignore`。`git status` 里书目录的变化应当只有 `book.json` 与账本；出现正文或索引文件就是规则漏了。

## 查书

书级默认入口（2026-10-23）：`book_query.py`。默认 `best_support` + B2.5 5 轮词法循环；触发时默认弹 5-8 张书卡，`--no-cards` 关闭；按需扩展默认关，`--expand-on-demand` 才跑 B4。`qsearch.py --book` 仍是旧 `mix` 口径，只作对照；`book_score.py` 是打分实验台。

```bash
cd ~/文档/books
# 找书（书名或作者），把 波特 换成要查的词
python3 工作区/脚本/lq.py -H "SELECT uid,title,author,path FROM book WHERE title LIKE '%波特%' OR author LIKE '%波特%'"
# 拿某本书的 md 目录，把 乡土中国 换成书名
python3 工作区/脚本/lq.py "SELECT '库/'||path||'/md' FROM book WHERE title='乡土中国'"
```

内容问答用 `book-corpus-qa` 技能，语料边界与实测数据在 `~/.agents/skills/book-corpus-qa/references/corpus-map.md`。只有 epub、没有 md 的书搜不到正文，先看 `目录/书目总表.csv` 的 `md状态` 列。

## 改完跑什么

| 改了什么 | 命令 | 通过判据 |
|---|---|---|
| 人账 `分类表.csv` | `python3 工作区/脚本/build_library.py --emit-json` | 输出里 `conflicts=0`，`books` 等于人账行数 |
| 结构、脚本、html | `python3 工作区/脚本/verify_phase.py` | 末行「全过」 |
| 书目或人账 | `python3 工作区/脚本/gen_book_catalog.py` | 末行「未匹配 0 条」 |
| 检索层（索引、概念表、视图/主题） | `python3 工作区/脚本/check_p1.py` 与 `python3 工作区/脚本/verify_phase.py --box 6` | 前者「失败 0 项」，后者「全过」 |
| 书级默认值或书卡 | `python3 工作区/批次/qa-检索-2026-10-22-书级融合/probe_b6.py --lanes --gate --perf` | 末行 `ALL PASS`；gate 证据为 `drop`，默认卡是产品决定 |
