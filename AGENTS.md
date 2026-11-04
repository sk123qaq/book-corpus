# 书库项目

`~/文档/books` 是个人电子书库。2026-10 按 `docs/design/设计方案-书目结构.md` 重构完毕。书、拆章 md、阅读版 html 收进每本书一个目录，奖项降为字段，SQLite 做可重建索引。

新会话先读本文件，再按「文件地图」与「脚本」按需深入。方案 §8 开头有两段执行修订，正文里写死的原型数字按修订块读。

本文件记录作者本机的书库实例。批次账与现状数字只对那份语料成立；新克隆的仓库按 `README.md` 从零搭建。

## 这台机器上的两处目录（2026-11-03 起单实例）

| 目录 | 角色 | 有无 `.git` |
|---|---|---|
| `~/文档/book-corpus` | 工具仓（公开）：代码、文档、模板、操作台与演示应用 | 有，跟 origin/main |
| `~/文档/book-corpus-data` | **唯一工作区实例**（私有）：`books/`（book.json + 正文与源文件）、`collections/`、`tables/`、`build/`、`工作区/`（批次机账、运维、中间态）、`archive/`、`books.toml` | 有，私有仓；只跟账，正文与索引在 `.gitignore` 里 |

`~/文档/books` 是便捷软链，指向 `book-corpus-data`。旧批次里 `~/文档/books/库/...`
是 v1 时代写死的记录，v2 没有 `库/`，软链救不了；要解析用 `tables/对照表.csv`
与批次里的 `对照表-0002.csv`，不要把它们当活路径。

2026-11-02 收尾把 `book-corpus-new` 改名成 `book-corpus-local`，旧 v1 树删掉。2026-11-03 把 local 实例合并进数据仓，`book-corpus-local` 下线，单实例一根。
迁移账与验收读数见数据仓 `工作区/批次/2026-11-03-合并单实例与账统一/说明.md`。

**单实例纪律**：数据仓 `.git` 与 41G 未跟踪正文同树。`git clean -fdx` 会删正文；批次写操作集中在 `工作区/` 下，别在根下跑；`git add -A` 依赖 `.gitignore`，
动 `.gitignore` 前先想清楚。

## 现状

<!-- numbers:start -->
As of 2026-11-03：1,954 本书，1,821 本有 md，133 本待拆章（125 pdf、5 txt、2 djvu、1 other；存档 125：图册 83、非中英文 42，政策不转不删）；1,009 条奖项记录，视图 2,140 条软链（奖项 1,009、主题 1,131）；坑账 7 类。
<!-- numbers:end -->

**存档政策（2026-11-03 定）**：非中英文书不处理、不分章、不 OCR，只存不删；图册/艺术设定集只存不删不处理。判定来源：非中英文 = `工作区/批次/pdf-ocr-2026-10-19/defer/lang.tsv`（lang=ja），图册 = 普查与 ops 批次的 `skip_list.tsv`（verdict 不转-记图册）。操作台显示为「存档·非中英文」「存档·图册」（接口值仍是 `ja`、`gallery`），顶栏「待转」已扣除存档（现为 8：5 txt + 2 djvu + 1 csv）。存量已全部归入存档：10-19 普查的 73 本图册、10-26 新入账的 10 本图册与艺术设定集、42 本日文原版。5 txt 与 2 djvu 是中文书，仍属可转，只是不在当前批次范围。数字随转换变化，跑 `book-corpus numbers --write` 重写上面的块；`book-corpus catalog` 末行打印「未匹配 N 条」。

2026-10-21 冷宫：唐诗学书系（B1359，11,619 章）移出处理流程，原书留在 `books/_冷宫/唐诗学书系/`，md 已删，人账与检索层同步移除。

## 文件地图

| 路径 | 内容 |
|---|---|
| `books/<uid>/` | 一本书一个目录：`book.json`、`source/`、可选 `md/`、`html/`。目录名就是 uid |
| `books/_收件箱/` | 新书先落这里，入库走 `book-corpus ingest`：`scan` 出计划，`apply` 落文件与账；换源用 `replace` |
| `books/_冷宫/` | 移出处理流程的书，只留原书文件。无 `book.json`、不在人账、不进检索与扩展。规则与账见 `books/_冷宫/README.md` |
| `collections/<Cuid>/source/` | 拆书前的合集原书；书的 `book.json` 用 `sources[].collection` 引用 |
| `tables/ledger.db` | **唯一人账**（SQLite）：`分类` 1,954 行（uid、一级、二级、作者层、保留、正式书名、备注、updated_at）、`概念` 127 条、`概念broad` 10 条（宽词层实验，未并入生产）、`动作词` 15 条、`ledger_meta`。人在操作台「账表」页或直接 SQL 改；活账 CSV 已归档 `archive/账切换-2026-11-03/` |
| `tables/系列/` | 21 份合集的卷序说明 |
| `tables/对照表.csv`、`搬迁计划.csv`、`标注样本.csv`、`书目录清单.csv`、`源文件清单.csv` | v1 目录结构与 L0–L2 的过程账，历史留档 |
| `build/library.db` | 全库书目，SQLite。产物，可重建。查询用 `lq.py` 只读打开 |
| `build/书目总表.csv` 与 `.xlsx` | 从 DB 导出的书目，含 md 状态、主源文件、路径 |
| `build/检索/index.db` | 检索层索引。段落倒排（FTS5 bigram）+ 概念剖面 + 章级扩展文本。可重建 |
| `build/检索/expand/<uid>.md` | 章级主题扩展的人读账，一本一份 |
| `build/views/奖项/<奖项>/<书名>`、`build/views/主题/<概念>/` | 指向 `books/<uid>/` 的软链树，随时可删可重建 |
| `build/阅读版索引.html` | 有阅读版 html 的书的总索引 |
| `src/bookcorpus/` | 包与脚本。入口 `book-corpus`，`book-corpus list` 列全部 |
| `docs/检索/` | 检索层方案、口径与踩坑账。工具仓文档，不在工作区 |
| `.agents/skills/` | 随仓库提交的 pi 技能：`book-corpus-qa`、`ebook-to-md`、`chapter-html-versions`、`mineru`、`typesafe-ai` |
| `apps/ops/` | 统一操作台服务、web 与运维文档，127.0.0.1:8770；验收在 `tests/acceptance/ops/` 与 `tests/manual/ops/` |
| `apps/demo/` | 检索流程演示页、回答与会话实现，127.0.0.1:8765；验收在 `tests/acceptance/demo/` 与 `tests/manual/demo/` |
| `docs/design/` | 权威方案、决策记录与流程图 |
| `docs/briefs/` | 已完成任务书，标注完成日期 |
| `工作区/批次/` | 只在实例与数据仓；工具仓不再保留 |
| `工作区/中间态/` | 只在实例磁盘上，派生或可再生，不进任何仓 |

`book.json` 是源元数据投影。md 与 html 状态只在 `library.db` 的 `derived` 表里，由 `book-corpus library` 按磁盘生成，不写回 `book.json`。

## 脚本

| 脚本 | 用途 |
|---|---|
| `lib_common.py` | 共享工具。路径常量、书名与作者清洗、`book_relpath`、md 目录计数。`ROOT` 由脚本位置推导，`BOOKS_ROOT` 可覆盖；`skill_dir()` 先找仓库 `.agents/skills/`，再找 `~/.agents/skills/` |
| `build_library.py` | 重建 `build/library.db`（读 `books/**/book.json`）。`--emit-json` 同时同步每本书的 `book.json` |
| `ingest.py` | 收件箱入库：`scan` 出计划（sha256 去重、书名匹配、add/replace/conflict，add 调 pi 的 deepseek-v4.1-flash 预填 subjects 与置信度），`apply` 落文件、写 `book.json` 与人账行、记 journal，再重建 DB。`--root` 可指到临时目录做测试，`--no-classify` 跳过分类 |
| `lq.py` | 只读查询 DB。`lq.py [-H] "SELECT ..."`，字段用制表符分隔 |
| `gen_book_catalog.py` | 出 `build/书目总表.xlsx` 与 `.csv`，并核对每条记录能落到磁盘。有未匹配时只报错，不覆盖旧目录 |
| `build_index.py` | 重建每本书的 `html/index.html` 与 `build/阅读版索引.html` |
| `verify_phase.py` | 四箱门槛检查，对任何新库成立、没有「跳过」：奖项 resolve、顶层结构与 book.json（含源文件在盘）、从零重建一致、检索层（`--box 4`）。迁移基线、奖项批次、旧路径对照表都不在门槛里 |
| `defpass.py` | 段落级概念检索。`defpass.py <md目录> -g 词1,词2 -g 词3 -n 5`，或 `-c 概念键` 走概念表；`-w` 调段落宽度 |
| `layout.py` | v2 树形的唯一定义：`books/`、`collections/`、`tables/`、`build/`。别的脚本不许再拼旧名字 |
| `migrate.py` | v1 → v2 迁移：`book-corpus migrate`，默认只打印，`--mode move\|link\|copy`，写对照表 |
| `remap.py` | 按对照表改写索引里的 `expand.path` 并重建 `expand_fts`：`book-corpus remap` |
| `classify_books.py` | 模型分类（`opencode-go/deepseek-v4.1-flash`，默认；`--backend typesafe` 保留旧路），结果落 `分类缓存.jsonl`，`--report` 只看统计。`--merge` 以人账为底合并：已有行一个字段都不动，只追加 `--add-new` 里人账没有的 uid；清单里未入账的 uid 默认跳过 |
| `scan_library.py` | L1 扫描旧布局。旧目录已不在，只作留档；扫到 0 个源文件时报错退出，不覆盖 `tables/` 任何账 |
| `split_md_batch.py` | 拆章批次驱动。读 DB 出待拆清单、转 EPUB、拆章、verify、audit、原子进库。verify 或 audit 不过时自动叫 pi agent 修（每本 2 次、默认不设整批上限、每次 600 秒），修完重跑检查再决定是否落地。`--force` 重拆，`--recheck` 只复核暂存区已有产物（不重拆，过了就进库），`--report` 只看状态；`worklist.tsv` 存在时即批次范围 |
| `redo_compare.py` | 重拆验收：旧 md 与暂存产物的逐页锚点覆盖率差。下降的书不落地 |
| `install_collection.py` | 把 split_collection 的逐册产物装进各书 md/，旧版改名留档，写 manifest |
| `collection_coverage.py` | 合集重拆的覆盖率验收（源书逐页锚点在给定旧/新 md 目录里的命中数） |
| `md_review_report.py` | 只读取证：正文字数、图片路径字数、跨文件重复片段、自链、spine 未收录页 |
| `corpus_stats.py` | 现测书库规模与结构：md 数、章节数、体积、未收录项、长书与系列分布。只读 |
| `audit_old_md.py` | 旧 md 文本量审计。逐页锚点比对源书，报「缺页」并按 README 未收录与卷首卷尾分类。只读 |
| `md_link_health.py` | 全库链接健康：章节文件自链数、README 目录段是否全部成链接、目录首行缩进。只读。装了 commonmark 按渲染结果判，没装按 CommonMark 链接目标规则判 |
| `backfill_readme.py` | 给缺 README 的 md 目录补目录页，格式同拼装器。默认只打印，`--apply` 写盘。只写 README.md，已有 README 的目录不碰 |
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
| `build_para_index.py` | 建 `build/检索/index.db`：段落倒排（FTS5 bigram）+ `book_concept` 概念剖面。只读 `books/`，`--rebuild` 全量且顺带重算概念剖面；`--concepts` 只补剖面 |
| `concept_expand.py` | 概念表展开成同义词组与排除正则，`--audit` 报零命中的别名 |
| `concept_route.py` | 问句路由到概念表 3 个键，缓存 `build/检索/route_cache/`（键含概念表内容指纹）。`qsearch --route` 与评测 L/M 档依赖它 |
| `query_expand.py` | 查询侧扩展：一次模型调用扩 4 条短语再融合，缓存 `build/检索/expand_queries/`，`--phrases-only` 只打短语 |
| `book_score.py` | 书级打分与八种方案，默认 `best_support`；`book_score.py "词" -n 10 --explain` |
| `book_query.py` | 书级查询默认入口：B2.5 两段词 + 5 轮词法循环；触发时默认弹 5-8 张书卡，`--no-cards` 关；`--expand-on-demand` 才跑 B4 |
| `book_readme.py` | README 目录解析、章标题/卷名/首末章、选章；B3 用 |
| `book_expand.py` | 按需扩展 L0-L4；默认档 3 轮、60 秒预算；默认关闭 |
| `book_cards.py` | B5 书卡：触发、5-8 张卡、选书/排除类型/换一批、反馈；`--gate`/`--perf` |
| `qsearch.py` | 词法检索：段落倒排 + 组覆盖度重排，`--concept` 走概念表，`--book` 出书级聚合（`--pool low\|medium\|all` 取数 400/2000/不限，`--book-score mix\|sum` 书级打分） |
| `qsearch_plan.py` | 按检索计划跑：多组词 + 概念/宽词，章级 RRF；`broad` 只在计划显式给出时展开，默认不启用 |
| `book_rank_bench.py` / `pool_bench.py` / `book_score_bench.py` | 书级排序的三个对比台：打分方案、取数档位、正解名次。只读 |
| `build_expand.py` | 文档扩展（`--kind prefix\|queries`）：主题前缀或伪查询写进 `index.db` 的 `expand` 表与 `build/检索/expand/<uid>.md`。`--provider commandcode\|mimo\|opencode` 默认 commandcode，mimo 有内容风控没进主流程。`--rebuild-md` 按库重写账文件。`--only-file` 按清单只扩指定章；`--reindex-fts` 重建 expand_fts；`--prune-stub` 备份并剪空正文章行。空正文章（图片页/标题页）跳过不生成，质量账见 qa-检索批次的《质量审计-2026-10-21》 |
| `sem_index.py` | 段落向量（`bge-m3`）。**待建，向量层当前不做** |
| `build_views.py` | 重建 `build/views/主题/<概念>/` 与 `奖项/<奖项>/` 的相对软链与清单，排序与 `qsearch --book` 一致 |
| `eval_report.py` | 把 `eval-*.tsv` 汇总成 README §2.2 的两张表 |
| `check_p1.py` | P1 题集验收：2 字词、原词类不低于裸 grep、查询边界回归。要 gold.tsv 与同目录的 eval-baseline/eval-index 读数，缺了退出码 2（没跑） |

路径与技能解析：`BOOKS_ROOT` 换数据根，`BOOKS_SKILLS_DIR` 换技能根，`BOOKS_RG` 指定 `rg`，`PI_BIN` 指定 `pi`。默认值都由脚本位置推导，不写死绝对路径。

## 流程

- 开工第一天就 `git init`，先提交账本、脚本与方案，不跟正文。
- 每个验证单元结束时在回复里给一行状态：完成了什么、下一个是什么、卡在哪。
- 界面或交互改动先有能点、能截图、能断言的验收，再交付。判据是操作人不能在一分钟内点出问题。

## 写入规则

- 改已有书的分类、书名、保留策略改人账 `tables/ledger.db` 的 `分类` 表（操作台「账表」页，或 `lib_common.category_put`），然后跑 `build_library.py --emit-json` 重建 DB 与 `book.json`。新书不要手填人账，走 `ingest.py` 的 `add` 或 `apply`。v2 的目录名是 uid，改书名不动目录。2026-11-03 起活账可用 CSV 已停：`分类表.csv/概念表.tsv` 等只在 `archive/账切换-2026-11-03/` 留档，脚本读 CSV 的路全下线。
- 写文件用 tmp 加 `os.replace`。库里的旧文件与 `../books.snapshot-2026-10-18` 共享 inode，截断式原地写会同时改掉快照。
- 一个账本一个写者。人账的已有行只由人改；新行由 `ingest.py` 与 `classify_books.py --add-new` 追加，写 `tables/ledger.db`（写者走 `.writer.lock` 互斥）。脚本写产物（`library.db`、`book.json`、`tables/` 下的导出），批次机账只追加。
- 拆章产物落 `books/<uid>/md/` 才算落地，`v_pending_md` 视图只认这个目录。
- 把一本书移出流程：删 `md/`，`book.json` 改名 `book.json.归档`，书目录移入 `books/_冷宫/`，从人账 `tables/ledger.db` 删行，跑 `build_library.py --emit-json`，再重建 `index.db` 与 `build/views/`。步骤与理由见 `books/_冷宫/README.md`。

## Git

2026-10-25 起是 Git 仓库，分支 `main`。只跟结构与账，不跟书的正文。

- 跟：每本书的 `book.json`、`tables/` 的人工账与小型过程账、`src/` 的脚本、`apps/` 的应用与测试、`docs/` 的方案与任务书。
- 不跟：`books/**/source/`、`md/`、`html/`、`books/_收件箱/`、`books/_冷宫/`（只留 README）、`build/检索/`（只留 `README.md` 与 `踩坑记录.md`）、`build/views/`、批次 `work/ out/ live/ tmp/ drill/`、模型与虚拟环境、运行态。
- 规则在根 `.gitignore`。`git status` 里书目录的变化应当只有 `book.json` 与账本；出现正文或索引文件就是规则漏了。
- 批次账不存本仓。每一批转换、评测、下载的机账在私有数据仓 `book-corpus-data` 的 `工作区/批次/` 下，随语料走：worklist、uid、页数、sha256 只对那份语料成立，克隆者无法复核。要复跑某一批，从数据仓拿；重跑入口在 `src/bookcorpus/`，不要在本仓再放一份批次脚本。
- 操作人提出的方向性异议必须落成带检查命令的目标态判据并写进批次说明；会话收尾输出「开场诉求 vs 终态」对照表，对不上不许结。

## 查书

书级默认入口（2026-10-23）：`book_query.py`。默认 `best_support` + B2.5 5 轮词法循环；触发时默认弹 5-8 张书卡，`--no-cards` 关闭；按需扩展默认关，`--expand-on-demand` 才跑 B4。`qsearch.py --book` 仍是旧 `mix` 口径，只作对照；`book_score.py` 是打分实验台。

```bash
cd ~/文档/book-corpus-data
# 找书（书名或作者），把 波特 换成要查的词
book-corpus run lq -H "SELECT uid,title,author,path FROM book WHERE title LIKE '%波特%' OR author LIKE '%波特%'"
# 拿某本书的 md 目录，把 乡土中国 换成书名
book-corpus run lq "SELECT 'books/'||path||'/md' FROM book WHERE title='乡土中国'"
```

内容问答用 `book-corpus-qa` 技能，语料边界与实测数据在 `~/.agents/skills/book-corpus-qa/references/corpus-map.md`。只有 epub、没有 md 的书搜不到正文，先看 `build/书目总表.csv` 的 `md状态` 列。

## 改完跑什么

| 改了什么 | 命令 | 通过判据 |
|---|---|---|
| 人账 `tables/ledger.db` | `book-corpus library --emit-json` | 输出里 `conflicts=0`，`books` 等于人账行数 |
| 任何代码 | `uv run --frozen pytest`（在工具仓） | 全部通过 |
| 结构、脚本、html | `book-corpus verify` | 末行「全过：N 项」 |
| 书目或人账 | `book-corpus catalog` | 末行「未匹配 0 条」 |
| 自己的检索题集（可选） | `book-corpus run check_p1 --gold <题集>/gold.tsv` | 「全过：P1 N 项」；没有题集返回 2，算没跑 |
| 书级默认值或书卡 | `python3 ~/文档/book-corpus-data/工作区/批次/qa-检索-2026-10-22-书级融合/probe_b6.py --lanes --gate --perf` | 末行 `ALL PASS`；gate 证据为 `drop`，默认卡是产品决定 |
