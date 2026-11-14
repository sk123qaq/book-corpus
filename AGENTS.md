# 书库项目

`~/书库` 是个人电子书库的工作区实例。2026-10 按 `docs/design/设计方案-书目结构.md` 重构完毕。书、拆章 md、阅读版 html 收进每本书一个目录，奖项降为字段，SQLite 做可重建索引。

新会话先读本文件，再按「文件地图」与「脚本」按需深入。方案 §8 开头有两段执行修订，正文里写死的原型数字按修订块读。

本文件记录作者本机的书库实例。批次账与现状数字只对那份语料成立；新克隆的仓库按 `README.md` 从零搭建。

## 这台机器上的两处目录（2026-11-13 起）

| 目录 | 角色 | 有无 `.git` |
|---|---|---|
| `~/文档/book-corpus` | 工具仓（公开）：代码、文档、模板、操作台与演示应用 | 有，跟 origin/main |
| `~/书库` | **唯一工作区实例**（私有）：`books/`（book.json + 正文与源文件）、`collections/`、`tables/`、`build/`、`工作区/`（批次机账、运维、中间态）、`archive/`、`books.toml` | 有，2026-11-13 新初始化的私有仓；只跟账，正文与索引在 `.gitignore` 里 |

2026-11-13 用 `book-corpus init ~/书库` 重建工作区树，把当时的 `~/文档/book-corpus-data`
按项目逻辑整体 rename 进来，随后删除旧目录与便捷软链 `~/文档/books`。旧数据仓的 Git 历史
打成 bundle 留在 `工作区/归档/book-corpus-data-历史-2026-11-13.bundle`（全部 refs，HEAD `34a68c4`）。
旧批次与旧文档里的 `~/文档/books/...`、`book-corpus-data/...` 都是搬迁前的记录；`库/...`
是 v1 时代写死的路径，v2 没有 `库/`。要解析用 `tables/对照表.csv` 与批次里的
`对照表-0002.csv`，不要把旧路径当活路径。

2026-11-02 收尾把 `book-corpus-new` 改名成 `book-corpus-local`，旧 v1 树删掉。2026-11-03 把 local 实例合并进数据仓，`book-corpus-local` 下线，单实例一根。
迁移账与验收读数见工作区 `工作区/批次/2026-11-03-合并单实例与账统一/说明.md`。

**单实例纪律**：工作区 `.git` 与 41G 未跟踪正文同树。`git clean -fdx` 会删正文；批次写操作集中在 `工作区/` 下，别在根下跑；`git add -A` 依赖 `.gitignore`，
动 `.gitignore` 前先想清楚。

## 现状

<!-- numbers:start -->
As of 2026-11-14：1,956 本书，1,823 本有 md，133 本待拆章（125 pdf、5 txt、3 other；存档 125：图册 83、非中英文 42，政策不转不删）；1,008 条奖项记录，视图 2,139 条软链（奖项 1,008、主题 1,131）；坑账 7 类。
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
| `.agents/skills/` | 随仓库提交的 pi 技能：`book-corpus-qa`、`ebook-to-md`、`chapter-html-versions`、`mineru`、`typesafe-ai`、`verify-book-corpus` |
| `apps/ops/` | 统一操作台服务、web 与运维文档，按需启动，127.0.0.1:8770；验收在 `tests/acceptance/ops/` 与 `tests/manual/ops/` |
| `apps/demo/` | 检索流程演示页、回答与会话实现，按需启动，127.0.0.1:8765；验收在 `tests/acceptance/demo/` 与 `tests/manual/demo/` |
| `apps/reading/` | 四读与视觉读本的书架、书目、正文与记录，按需启动，127.0.0.1:8771；验收在 `tests/test_reading_*.py` |
| `apps/home/` | 统一入口首页，项目默认只启动这一项，127.0.0.1:8773；服务端渲染，读 `books.toml`、`books/` 与 `日课/`，点击时按需代起四个页面服务，验收在 `tests/test_home_*.py` |
| `docs/design/` | 权威方案、决策记录与流程图 |
| `docs/briefs/` | 已完成任务书，标注完成日期 |
| `工作区/批次/` | 只在工作区实例；工具仓不再保留 |
| `工作区/中间态/` | 只在实例磁盘上，派生或可再生，不进任何仓 |

`book.json` 是源元数据投影。md 与 html 状态只在 `library.db` 的 `derived` 表里，由 `book-corpus library` 按磁盘生成，不写回 `book.json`。

## 脚本

脚本清单与一行说明只有一处：`book-corpus list`（读每个脚本的 docstring）。机器可读快照在
`docs/reference/facts.md`，由 `book-corpus docs write` 生成；别在这里手抄，抄了就会过期。

高频入口：`ingest`（收件箱）、`library`（重建书目库）、`catalog`、`verify`、`numbers`、`daily`、
`search` / `query`。完整参数用 `book-corpus <脚本> --help`；批次规则与判据见 `docs/检索/`、
`docs/briefs/` 与各批次账。

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
- 批次账不存本仓。每一批转换、评测、下载的机账在私有工作区 `~/书库` 的 `工作区/批次/` 下，随语料走：worklist、uid、页数、sha256 只对那份语料成立，克隆者无法复核。要复跑某一批，从工作区拿；重跑入口在 `src/bookcorpus/`，不要在本仓再放一份批次脚本。
- 操作人提出的方向性异议必须落成带检查命令的目标态判据并写进批次说明；会话收尾输出「开场诉求 vs 终态」对照表，对不上不许结。

## 查书

书级默认入口（2026-10-23）：`book_query.py`。默认 `best_support` + B2.5 5 轮词法循环；触发时默认弹 5-8 张书卡，`--no-cards` 关闭；按需扩展默认关，`--expand-on-demand` 才跑 B4。`qsearch.py --book` 仍是旧 `mix` 口径，只作对照；`book_score.py` 是打分实验台。

```bash
cd ~/书库
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
| 任何代码（lint 四道闸） | `uv run --frozen pre-commit run --all-files` | 四行都 `Passed`；CI 的 `lint` job 跑同一套 |
| 结构、脚本、html | `book-corpus verify` | 末行「全过：N 项」 |
| 书目或人账 | `book-corpus catalog` | 末行「未匹配 0 条」 |
| 自己的检索题集（可选） | `book-corpus run check_p1 --gold <题集>/gold.tsv` | 「全过：P1 N 项」；没有题集返回 2，算没跑 |
| 书级默认值或书卡 | `python3 ~/书库/工作区/批次/qa-检索-2026-10-22-书级融合/probe_b6.py --lanes --gate --perf` | 末行 `ALL PASS`；gate 证据为 `drop`，默认卡是产品决定 |

## Agent skills

### Issue tracker

GitHub Issues in `sk123qaq/book-corpus` via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.
