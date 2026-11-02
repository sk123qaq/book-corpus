# 书库

本地电子书库的工具箱。每本书一个 `books/<uid>/` 目录，正文拆成章节 Markdown，另有可视化阅读版 HTML。SQLite 保存书目与检索索引，全库正文可以按词、按概念、按书检索，也可以交给 pi agent 按原文回答问题，答案带逐字引文。

仓库只放工具、文档与模板，不放任何人的书。克隆下来是**空的工具仓**；用 `book-corpus init` 建工作区，再把自己的书放进 `books/_收件箱/`。本机的书目账在私有数据仓 `book-corpus-data`，见下文。

## 能做什么

- 找书：按书名、作者、分类路径查 `build/library.db`。
- 查原文：段落级倒排检索，支持概念同义词组，返回 `books/<uid>/md/章节.md:行号`。
- 问书：`book-corpus query` 定位书与章。要成段回答并带逐字引文，用 `book-corpus-qa` 技能或演示页。
- 管书：入库、分类、拆章、出 HTML 阅读版、PDF 转 Markdown、删书与冷宫。
- 看库：本地操作台（8770）与检索演示页（8765），默认只绑 `127.0.0.1`。

## 工作区结构

`book-corpus init` 铺出来的树。一个目录一种东西，`build/` 整目录删掉能重建。

| 路径 | 内容 |
|---|---|
| `books/<uid>/` | 一本书一个目录，含 `book.json`、`source/`、可选 `md/` 与 `html/` |
| `books/_收件箱/` | 新书落地处，入库走 `book-corpus ingest` |
| `books/_冷宫/` | 移出处理流程的书，只留原书文件，不在人账，不进检索 |
| `collections/<Cuid>/source/` | 拆书前的合集原书，由书的 `book.json` 用 `collection` 引用 |
| `tables/分类表.csv` | 唯一人账，一行一本书。只有它允许人改 |
| `tables/概念表*.tsv` | 检索层的概念键与同义词组，人写人改 |
| `tables/系列/` | 书系说明，卷序看这里 |
| `build/library.db` | 全库书目，SQLite，产物，可重建 |
| `build/检索/index.db` | 段落倒排与概念剖面，产物，可重建 |
| `build/views/` | 指向 `books/<uid>/` 的奖项与主题软链树，产物 |
| `build/书目总表.csv` | 从 DB 导出的书目，产物 |
| `build/阅读版索引.html` | 有阅读版 HTML 的书的总索引，产物 |
| `工作区/` | 批次账、运维、演示与本地缓存；运行时可重建 |

`book.json` 只记源元数据（uid、书名、学科、sources、awards）。目录名就是 uid，`book.json` 不再存路径。md 与 html 状态在 `library.db` 的 `derived` 表里，由 `build_library.py` 按磁盘生成，不写回 `book.json`。

## 装

### 1. 系统依赖

| 依赖 | 必需性 | 用途 |
|---|---|---|
| Python 3.11+ | 必需 | 全部脚本。开发与验收用 3.13/3.14 |
| SQLite（带 FTS5） | 必需 | `library.db` 与 `index.db`。Python 自带 `sqlite3` 模块即可，需确认 FTS5 可用 |
| Git | 必需 | 版本管理 |
| ripgrep | 检索与验收需要 | 全文定位。`rg` 放到 PATH，或用 `BOOKS_RG` 指定 |
| Calibre 的 `ebook-convert` | 拆 epub、mobi、azw3 必需 | 先转 EPUB 再拆章 |
| poppler 的 `pdfinfo`、`pdfimages`、`pdftoppm` | PDF 普查与扫描件探针必需 | 页数、图片清单、抽页渲染 |
| `pi` CLI | 模型功能必需 | 分类、拆章修复、问答通过它调模型 |
| headless Chrome | 可选 | 操作台截图验收，`CHROME_BIN` 可换路径 |

Fedora：

```bash
sudo dnf install calibre poppler-utils ripgrep
```

Arch / Omarchy：

```bash
sudo pacman -S --needed calibre poppler ripgrep
```

Debian 与 Ubuntu：

```bash
sudo apt install calibre poppler-utils ripgrep
```

### 2. 装工具

用 uv，`uv.lock` 在仓里。

```bash
uv tool install .                  # 装成全局命令
book-corpus --version
book-corpus init ~/书库            # 建一个空工作区
```

改代码时走项目环境：

```bash
uv sync --frozen
uv run book-corpus --version
```

`book-corpus` 是唯一入口。`book-corpus list` 列出全部脚本，`book-corpus run <脚本>` 跑没有短名的那些。工作区根用 `--root`、`BOOKS_ROOT` 或工作区里的 `books.toml` 指定。

### 3. Python 环境

核心流程只用标准库。`qsearch.py`、`book_query.py`、`build_library.py`、`build_para_index.py`、`lq.py` 不需要装第三方包。

PDF 路线与重排实验共用一个虚拟环境 `工作区/.venv-pdf/`：

```bash
python3 -m venv 工作区/.venv-pdf
工作区/.venv-pdf/bin/pip install -r requirements.txt
# 只做重排实验时再装
工作区/.venv-pdf/bin/pip install -r requirements-rerank.txt
```

PDF 脚本必须用这个解释器跑。`pdf_md_batch.py` 启动时会检查当前解释器能否 `import pymupdf`，不能就直接退出。

`requirements.txt` 还包含 `beautifulsoup4`，`ebook-to-md` 技能解析 EPUB 要用。两个可选包按需装：`pip install commonmark lxml`。`commonmark` 让 `md_link_health.py` 按渲染结果判断链接，`lxml` 让 EPUB 解析更快。`book-corpus catalog` 想额外出 xlsx 时装 `openpyxl`，没装只出 CSV。

### 4. 模型与账号

多数脚本通过 pi 调模型。文档扩展直连 provider 接口，自己从 `~/.pi/agent/auth.json` 读 key。

| 默认模型 | 用在哪 | 怎么换 |
|---|---|---|
| `opencode-go/deepseek-v4.1-flash` | 书目分类、拆章失败修复、问答归纳 | `INGEST_CLASSIFY_MODEL`、`CLASSIFY_BOOKS_MODEL`、`MD_FIX_MODEL`、`ANSWER_MODEL` |
| `opencode-go/glm-5.3-flash` | pi 子代理的独立评审与验证 | `~/.pi/agent/settings.json` 的 `subagents.agentOverrides` |
| `deepseek/deepseek-v4.1-flash`（commandcode） | `build_expand.py` 默认 provider | `build_expand.py --provider` |
| `mimo-v2.6-flash` | `build_expand.py --provider mimo` | 密钥在 `~/.config/mimo/api_key` |

两个云 OCR 接口：

| 服务 | 凭据 | 用途与限制 |
|---|---|---|
| PaddleOCR | `~/.config/PaddleOCR/api_key` | 扫描件逐页 OCR。平台有每日页数额度，用尽返回 HTTP 403 code 12001 |
| MinerU | `~/.config/mineru/api_key` 或 `$MINERU_API_KEY` | OCR 兜底，读不出的可疑页补投。CLI 在 `.agents/skills/mineru/scripts/mineru.py` |

### 5. 技能

问答与转换依赖的 pi 技能已经放在仓库里，不需要另外安装。Pi 会自动发现项目下的 `.agents/skills/`。

| 技能 | 路径 | 谁在用 |
|---|---|---|
| `book-corpus-qa` | `.agents/skills/book-corpus-qa/` | `answer.py` 与问答流程，按原文回答并核对引文 |
| `ebook-to-md` | `.agents/skills/ebook-to-md/` | 拆章、合集拆分、覆盖率审计、产物校验 |
| `chapter-html-versions` | `.agents/skills/chapter-html-versions/` | 生成可视化阅读版 HTML，附 `verify_lite.py` 校验 |
| `mineru` | `.agents/skills/mineru/` | OCR 兜底 |
| `typesafe-ai` | `.agents/skills/typesafe-ai/` | 旧分类后端，默认已改用模型 |

脚本解析技能目录的顺序是：仓库内 `.agents/skills/`，其次 `~/.agents/skills/`。可以用 `BOOKS_SKILLS_DIR` 指定别的根。

### 6. 本地模型（可选）

`工作区/models/` 下的模型都不在 Git 里。只跑检索与问答不需要它们。重排实验需要能用的 GPU；`rerank.py` 的设备名写死 `cuda`，AMD 显卡用 ROCm 版 torch 也映射成这个名字。

## 准备语料

克隆下来是空工作区：`books/` 里只有 `_收件箱/` 与 `_冷宫/` 的 README，`tables/` 下只有表头与通用概念表。

1. 把书拷进 `books/_收件箱/`。
2. `book-corpus ingest scan` 出计划，`book-corpus ingest apply --apply` 落到 `books/<uid>/source/`，同时写 `book.json` 与 `tables/分类表.csv` 一行。
3. `book-corpus library` 建库，`book-corpus run split_md_batch --batch <批次名> --uids <uid>` 拆章，`book-corpus index --rebuild` 建检索索引。

```bash
book-corpus ingest scan --out plan.json
book-corpus ingest apply plan.json --apply
book-corpus library
```

`scan` 只读，出计划。`apply` 默认也只打印，加 `--apply` 才写盘。

入库只把源和 `book.json` 放进库，还没有 md。要能检索，先拆章：

```bash
book-corpus run split_md_batch --batch <批次名> --uids <uid>
```

`split_md_batch.py` 会转 EPUB、拆章、verify、audit，过了才把 md 原子落进 `books/<uid>/md/`。epub 不需要 Calibre；mobi、azw3、azw 需要先装 Calibre 的 `ebook-convert`。只想快速拆一本、不走批次账时，也可以用 `ebook-to-md` 技能：

```bash
python3 .agents/skills/ebook-to-md/scripts/ebook_to_md.py <书>.epub -o books/<uid>/md
python3 .agents/skills/ebook-to-md/scripts/verify_output.py books/<uid>/md
```

`split_md_batch.py` 从 `library.db` 找待拆书，所以刚 `ingest` 完要先跑一次 `book-corpus library`。它只在 `工作区/批次/<批次名>/` 里生成一次 `worklist.tsv`，重复运行以账为准。

建库与索引：

```bash
book-corpus library
book-corpus index --rebuild
```

`build_library.py` 读 `tables/分类表.csv` 与 `book.json`，重建 `build/library.db`。`build_para_index.py --rebuild` 全量重建检索索引，耗时随语料增长，几十分钟到几小时。

## 常用命令

### 找一本书

```bash
book-corpus run lq -H "SELECT uid,title,author,path FROM book WHERE title LIKE '%核心词%' OR author LIKE '%核心词%'"
```

`lq.py` 只读打开 `build/library.db`，输出制表符分隔。`book.path` 在 v2 里就是 uid。

### 查原文

```bash
book-corpus search '关键词 关键词' -n 5
book-corpus search --concept 概念键 -n 5
```

返回 `books/<uid>/md/xx.md:行号`、分数与命中段。`--book` 出书级聚合，`--concept` 走概念表。

### 问一本书

```bash
book-corpus query 关键词 关键词 --no-cards
```

默认先做书级定位，再选章。书卡默认开，触发时弹 5 到 8 张，`--no-cards` 关闭。按需扩展默认关，加 `--expand-on-demand` 才跑。

要成段回答并带引文，用 `book-corpus-qa` 技能，或起演示页。

### 起两个本地页面

在工具仓里跑，用 `--root` 指工作区：

```bash
python3 工作区/演示-检索流程/server.py --root ~/书库   # http://127.0.0.1:8765
python3 工作区/运维/server.py --root ~/书库             # http://127.0.0.1:8770
```

演示页只读索引与 `books/**.md`，回答缓存写到工作区的 `工作区/演示-检索流程/缓存/`。操作台读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调脚本。

### 重建产物

```bash
book-corpus library                  # 人账与 book.json -> build/library.db
book-corpus index --rebuild          # 段落倒排与概念剖面
book-corpus render                   # 每本书的 html 索引与阅读版总索引
book-corpus run build_views          # build/views/奖项 与 主题 软链
book-corpus catalog                  # build/书目总表.csv（装 openpyxl 时也出 xlsx）
```

### 迁移旧布局

```bash
book-corpus migrate --dest <新树> --mode link --out <对照表目录>   # 先打印
book-corpus migrate --dest <新树> --mode link --out <目录> --apply
book-corpus remap --map <对照表-0002.csv> --old-db <旧 index.db> --apply
```

`migrate` 读 v1 的 `库/`、`目录/`、`视图/`，落成 v2。`remap` 按对照表改写 `expand` 路径并重建 FTS；默认只打印。

## 改完跑什么

| 改了什么 | 命令 | 通过判据 |
|---|---|---|
| 任何代码 | `uv run --frozen pytest` | 全部通过 |
| 人账 `tables/分类表.csv` | `book-corpus library` | `conflicts=0`，`books` 等于人账行数 |
| 结构、脚本 | `book-corpus doctor` | 无 `fail` |
| 结构、检索层 | `book-corpus verify` | 末行「全过」，缺基线时部分箱子打印「跳过」 |
| 书目或人账 | `book-corpus catalog` | 末行「未匹配 0 条」 |
| 检索层 | `book-corpus run check_p1` 与 `book-corpus verify --box 6` | 前者「失败 0 项」或 `SKIP`，后者「全过」 |

`verify_phase.py`、`check_p1.py` 与 `gen_book_catalog.py` 是作者实例检查。缺基线、评测书时，前两个会打印 `[跳过]` 或 `SKIP`；`gen_book_catalog.py` 需要源文件在盘，克隆状态不会通过。

`工作区/运维/验收/` 与 `工作区/演示-检索流程/验收/` 的脚本已改成 v2 临时根。8 个离线入口（`check_ops`、`test_status`、`test_classify`、`test_doc_numbers`、`test_split`、`test_retire`、`test_pdf`、`test_answer_retry`）由 `tests/test_acceptance.py` 接进 pytest；需要 Chrome、真实模型或真实会话的 `check_demo`、`check_session*`、`check_result_ms`、`shot_ops`、`repro-*` 仍是手工验收。

## 路径与配置

- `BOOKS_ROOT`：工作区根（有 `books.toml` 的那层），默认从当前目录向上找。
- `BOOKS_SKILLS_DIR`：技能根，默认是仓库的 `.agents/skills/`。
- `BOOKS_RG`：`rg` 可执行文件路径，默认从 PATH 找。
- `PI_BIN`：`pi` 可执行文件路径，默认从 PATH 找。

脚本用工作区清单认根，不依赖当前工作目录，也不写死绝对路径。

## 写入规则

- 一个账本一个写者。人账的已有行只由人改。新行由 `ingest.py` 与 `classify_books.py --add-new` 追加，同一时刻只跑一个。
- 写文件用 tmp 加 `os.replace`。库里的旧文件可能与快照共享 inode，截断式原地写会同时改掉快照。
- 拆章产物落 `books/<uid>/md/` 才算落地。`v_pending_md` 视图只认这个目录。
- 把一本书移出流程：删 `md/`，`book.json` 改名 `book.json.归档`，书目录移入 `books/_冷宫/`，从 `tables/分类表.csv` 删行，再重建 DB、检索索引与视图。
- 批次机账只追加。`worklist.tsv` 一旦生成就以它为准，不重写。

## 已知限制

- 仓库不含书的正文。克隆后只有工具与模板，正文要自备。
- 只有 `md/` 存在的书能搜到正文。只有 epub 或 PDF 的书先看 `build/书目总表.csv` 的 `md状态` 列。
- 向量检索未建。重排只在实验台，`book_query.py` 不调用它。
- 演示页与操作台默认只绑 `127.0.0.1`，没有鉴权。改 `--host` 前先想清楚，不要暴露到局域网。

## 许可证

MIT，见 `LICENSE`。

## 更多文档

- `AGENTS.md`：作者本机实例的运行手册，含批次账与脚本表。新克隆的仓库没有那些批次数据。
- `工作区/方案/设计方案-书目结构.md`：权威方案，L0 到 L5 门槛与目标结构。
- `docs/检索/README.md`：检索层方案与实测基线。
- `docs/检索/踩坑记录.md`：检索层按类型分的踩坑账。改检索脚本前先查。
- `工作区/运维/README.md` 与 `工作区/运维/CONTRACT.md`：操作台的起停、接口与文件归属。
- `工作区/演示-检索流程/BRIEF.md`：演示页的任务书。
- `reports/运维验收-2026-10-25/`：七页交互审计与证据。
