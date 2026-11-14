# 书库

本地电子书库的工具箱。每本书一个 `books/<uid>/` 目录，正文拆成章节 Markdown，另有可视化阅读版 HTML。SQLite 保存书目与检索索引，全库正文可以按词、按概念、按书检索，也可以交给 pi agent 按原文回答问题，答案带逐字引文。

仓库只放工具、文档与模板，不放任何人的书。克隆下来是**空的工具仓**；用 `book-corpus init` 建工作区，再把自己的书放进 `books/_收件箱/`。本机的书目账在私有工作区 `~/书库`，见下文。

## 能做什么

- 找书：按书名、作者、分类路径查 `build/library.db`。
- 查原文：段落级倒排检索，支持概念同义词组，返回 `books/<uid>/md/章节.md:行号`。
- 问书：`book-corpus query` 定位书与章。要成段回答并带逐字引文，用 `book-corpus-qa` 技能或演示页。
- 管书：入库、分类、拆章、出 HTML 阅读版、PDF 转 Markdown、删书与冷宫。
- 看库：项目默认只启动统一入口（8773）；分层读（8771）、操作台（8770）、检索演示页（8765）与日课页（8772）按需启动，全部只绑 `127.0.0.1`。

## 实例结构

`book-corpus init` 铺出来的树。一个目录一种东西，`build/` 整目录删掉能重建。

这是**实例**的布局；工具仓自己的代码与文档在 `apps/`、`docs/`、`src/`、`tests/`，见下节。

| 路径 | 内容 |
|---|---|
| `books/<uid>/` | 一本书一个目录，含 `book.json`、`source/`、可选 `md/` 与 `html/` |
| `books/_收件箱/` | 新书落地处，入库走 `book-corpus ingest` |
| `books/_冷宫/` | 移出处理流程的书，只留原书文件，不在人账，不进检索 |
| `collections/<Cuid>/source/` | 拆书前的合集原书，由书的 `book.json` 用 `collection` 引用 |
| `tables/ledger.db` | 唯一人账（`分类` 表一行一本书，`概念`/`概念broad`/`动作词` 是检索词表）。旧 CSV 只在 `archive/账切换-2026-11-03/` 留档，缺库时才兜底读 |
| `tables/系列/` | 书系说明，卷序看这里 |
| `build/library.db` | 全库书目，SQLite，产物，可重建 |
| `build/检索/index.db` | 段落倒排与概念剖面，产物，可重建 |
| `build/views/` | 指向 `books/<uid>/` 的奖项与主题软链树，产物 |
| `build/书目总表.csv` | 从 DB 导出的书目，产物 |
| `build/阅读版索引.html` | 有阅读版 HTML 的书的总索引，产物 |
| `工作区/` | 实例根下的运行时层（本机是 `~/书库/工作区/`）：批次账、缓存与虚拟环境；可重建，不进工具仓 |
| `日课/` | 每日一期的 JSON 与日志；页面只读，生成走 `book-corpus daily` |

`book.json` 只记源元数据（uid、书名、学科、sources、awards）。目录名就是 uid，`book.json` 不再存路径。md 与 html 状态在 `library.db` 的 `derived` 表里，由 `build_library.py` 按磁盘生成，不写回 `book.json`。

## 仓库结构

克隆者先读这份 README；改代码读 `CONTRIBUTING.md`；`AGENTS.md` 只记本机实例的约束与账。

| 路径 | 内容 |
|---|---|
| `apps/ops/` | 统一操作台服务与 web，按需启动，127.0.0.1:8770 |
| `apps/demo/` | 检索流程演示页与回答实现，按需启动，127.0.0.1:8765 |
| `apps/reading/` | 分层读书架、书目、正文与记录，按需启动，127.0.0.1:8771 |
| `apps/daily/` | 每日一章的只读阅读页，按需启动，127.0.0.1:8772 |
| `apps/home/` | 统一入口首页，默认启动项，服务端渲染，读 `books.toml`、`books/` 与 `日课/`，点入口按需代起四个服务，127.0.0.1:8773 |
| `src/bookcorpus/` | 命令行包；`scripts/` 是全部可复跑脚本 |
| `tests/acceptance/` | 离线验收，由 pytest 收集 |
| `tests/manual/` | 需要 Chrome、真实模型或真实会话的手工验收，清单见其 README |
| `docs/design/` | 设计方案、决策记录与流程图 |
| `docs/briefs/` | 已完成的任务书，标完成日期 |
| `docs/检索/` | 检索层方案、口径与踩坑账 |
| `reports/` | 验收审计与一次性报告 |

## 装

### 1. 系统依赖

| 依赖 | 必需性 | 用途 |
|---|---|---|
| Python 3.11+ | 必需 | 全部脚本。开发与验收用 3.13/3.14 |
| SQLite（带 FTS5） | 必需 | `library.db` 与 `index.db`。Python 自带 `sqlite3` 模块即可，需确认 FTS5 可用 |
| Git | 必需 | 版本管理 |
| ripgrep | 检索与验收需要 | 全文定位。`rg` 放到 PATH，或用 `BOOKS_RG` 指定 |
| Calibre 的 `ebook-convert` | 拆 epub、mobi、azw3 必需 | 先转 EPUB 再拆章 |
| markitdown（装在 `工作区/.venv-docs`） | docx/xlsx/xls/pptx 转 md 必需 | `init`/`venv` 自动装，不污染主环境 |
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

工具本身用 uv 装，`uv.lock` 在仓里。

#### 先装 uv

Arch / Omarchy 从官方仓库装：

```bash
sudo pacman -S --needed uv
```

其它 Linux 发行版与 macOS 用 Astral 的安装脚本：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv --version` 有输出才算装好。安装脚本把 uv 放进 `~/.local/bin`，脚本最后会打印需要加进 PATH 的那一行；照它做，或者重开一个终端。

#### 再装 book-corpus

```bash
uv tool install .                  # 装成全局命令
book-corpus --version
book-corpus init ~/书库            # 建一个空工作区
```

`init` 顺带建 `工作区/.venv-pdf` 与 `工作区/.venv-docs` 并装依赖。第一次要联网：uv 按需下载解释器与包，网慢时六七分钟；装过一次之后重跑是十秒级。离线时加 `--no-pdf-venv`（两个环境都跳过），之后跑 `book-corpus venv` 补。

改代码时走项目环境：

```bash
uv sync --frozen
uv run book-corpus --version
```

`book-corpus` 是唯一入口。`book-corpus list` 列出全部脚本，`book-corpus run <脚本>` 跑没有短名的那些。工作区根用 `--root`、`BOOKS_ROOT` 或工作区里的 `books.toml` 指定。

### 3. Python 环境

核心流程基本只用标准库；EPUB 拆章走 `ebook-to-md`，需要 `beautifulsoup4`，已列进 `pyproject.toml` 的核心依赖。`qsearch.py`、`book_query.py`、`build_library.py`、`build_para_index.py`、`lq.py` 不需要装第三方包。

PDF 路线与重排实验共用一个虚拟环境 `工作区/.venv-pdf/`。`book-corpus init` 已经建好并装好了 `requirements.txt`，不用再动手。要查它在不在、包全不全：

```bash
book-corpus venv --check
```

删了或换机器后重建，在旧工作区里跑一条命令：

```bash
book-corpus venv
```

`book-corpus init` 重建工作区时也会把它带上；只想重建目录与模板、不碰 Python 时加 `--no-pdf-venv`。

只做重排实验时再补：

```bash
工作区/.venv-pdf/bin/pip install -r requirements-rerank.txt
```

PDF 脚本必须用这个解释器跑。`pdf_md_batch.py` 启动时会检查当前解释器能否 `import pymupdf`，不能就直接退出。

`requirements.txt` 也包含 `beautifulsoup4`，给 PDF 环境里的 `ebook-to-md` 用；主环境已由核心依赖安装。两个可选包按需装：`pip install commonmark lxml`。`commonmark` 让 `md_link_health.py` 按渲染结果判断链接，`lxml` 让 EPUB 解析更快。`book-corpus catalog` 想额外出 xlsx 时装 `openpyxl`，没装只出 CSV。

### 4. 模型与账号

分类、拆章修复、问答归纳都要调模型，调用都经由 pi。项目用的模型写在 `books.toml`：

```toml
[model]
provider = "opencode-go"
model = "deepseek-v4.1-flash"
thinking = "high"
```

看当前生效的值，以及每一项是从哪里来的（文件、环境变量还是内置默认）：

```bash
book-corpus model
```

#### 用 pi 里已经登好的 provider

先在 pi 里选 provider、配 key、选模型：进 pi 后用 `/login` 选 provider 并粘 API key，`/model` 选模型，`/thinking` 选思考等级。然后让项目跟它一致：

```bash
book-corpus model set --provider opencode-go --model deepseek-v4.1-flash --thinking high
book-corpus model check
```

`book-corpus model` 会拿这几项去对 `pi --list-models`，provider 或模型名写错当场就能看见。`model check` 真发一句「只回复两个字：ok」，120 秒内没回算失败，key 在输出里会换成 `****`。

#### 用自建端点（自己的 base_url 和 key）

```bash
book-corpus model set --provider acme --model my-model \
  --base-url https://api.example.com/v1 --api-key-env ACME_API_KEY
book-corpus model setup-pi
export ACME_API_KEY=sk-...
book-corpus model check
```

`model setup-pi` 把这套端点写进 `~/.pi/agent/models.json`（已有的文件先备份成 `models.json.bak.<时间>`）。key 在文件里只写 `$ACME_API_KEY` 这个引用，不落明文；`model check` 从环境变量读出 key 传给 pi。接口格式用 `--api` 选，默认 `openai-completions`，也可 `anthropic-messages`。

思考等级取 `off`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`，默认 `high`。网页上只暴露关、低、中、高四档，其余档位仍可通过接口传。没有显式传等级的调用一律用 `books.toml` 里的值。

这六项都能用环境变量临时覆盖，优先级高于 `books.toml`：`BOOKS_MODEL_PROVIDER`、`BOOKS_MODEL`、`BOOKS_THINKING`、`BOOKS_BASE_URL`、`BOOKS_API_KEY_ENV`、`BOOKS_MODEL_API`。

| 默认模型 | 用在哪 | 怎么换 |
|---|---|---|
| `opencode-go/deepseek-v4.1-flash` | 书目分类、拆章失败修复、问答归纳 | `book-corpus model set` |
| `opencode-go/glm-5.3-flash` | pi 子代理的独立评审与验证 | `~/.pi/agent/settings.json` 的 `subagents.agentOverrides` |
| `deepseek/deepseek-v4.1-flash`（commandcode） | `build_expand.py` 默认 provider | `build_expand.py --provider` |
| `mimo-v2.6-flash` | `build_expand.py --provider mimo` | 密钥在 `~/.config/mimo/api_key` |

单个脚本仍可用环境变量单独改：`INGEST_CLASSIFY_MODEL`、`CLASSIFY_BOOKS_MODEL`、`MD_FIX_MODEL`、`ANSWER_MODEL`、`OPS_TRIAGE_MODEL`。

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
| `depth-swap` | `.agents/skills/depth-swap/` | 改分层读 `Store.save_depth` 或 `ReadingService` 启动流程后跑崩溃窗口检查 |
| `mineru` | `.agents/skills/mineru/` | OCR 兜底 |
| `sync-docs` | `.agents/skills/sync-docs/` | 迭代后对账活文档：刷新事实表、审计并修路径/端口/命令漂移 |
| `typesafe-ai` | `.agents/skills/typesafe-ai/` | 旧分类后端，默认已改用模型 |
| `verify-book-corpus` | `.agents/skills/verify-book-corpus/` | 一次性工作区起五个页面服务，验证用户可见行为并留证据 |

脚本解析技能目录的顺序是：工作区 `.agents/skills/`、源码仓 `.agents/skills/`、`~/.agents/skills/`。可以用 `BOOKS_SKILLS_DIR` 指定别的根。

### 6. 本地模型（可选）

`工作区/models/` 下的模型都不在 Git 里。只跑检索与问答不需要它们。重排实验需要能用的 GPU；`rerank.py` 的设备名写死 `cuda`，AMD 显卡用 ROCm 版 torch 也映射成这个名字。

## 准备语料

克隆下来是空工作区：`books/` 里只有 `_收件箱/` 与 `_冷宫/` 的 README，`tables/` 下只有表头与通用概念表。

想先看效果、不想自己备书，装一本公有领域样书：

```bash
book-corpus sample
book-corpus search 你那里配姓赵
```

`sample` 把《阿Q正传》的源文与拆好的九章 md 复制进工作区，分配 uid、写 `book.json` 与人账 `tables/ledger.db` 一行，再建库建索引。重复运行不会进第二本。文本来源、版本与许可见 `src/bookcorpus/sample/阿Q正传/README.md`。

1. 把书拷进 `books/_收件箱/`。
2. `book-corpus ingest scan` 出计划，`book-corpus ingest apply --apply` 落到 `books/<uid>/source/`，同时写 `book.json` 与 `tables/ledger.db` 的 `分类` 表一行。
3. `book-corpus library` 建库，`book-corpus run split_md_batch --batch <批次名> --uids <uid>` 拆章，`book-corpus index --rebuild` 建检索索引。

```bash
book-corpus ingest scan --out plan.json
book-corpus ingest apply plan.json --apply
book-corpus library
```

`scan` 只读，出计划。`apply` 默认也只打印，加 `--apply` 才写盘。

入库只把源和 `book.json` 放进库，还没有 md。收件箱收 `epub`、`mobi`、`azw3`、`azw`、`pdf`、`txt`、`md`、`docx`、`xlsx`、`xls`、`pptx`；`.doc`、`.ppt`、`.djvu`、`.csv` 等其他格式不列入库清单。

`txt`/`docx`/`xlsx`/`xls`/`pptx`/`md` 入库后，操作台的「新书导入」页会自动接一个 `docs.run`：每本整本转成 `md/<书名>.md`，不分章，配 `README.md`、`images/` 与 `.manifest.json`；源文件是 md 的原样复制。`epub`/`mobi` 等仍走拆章，PDF 走 PDF 管线：

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

`build_library.py` 读 `tables/ledger.db` 与 `book.json`，重建 `build/library.db`。`build_para_index.py --rebuild` 全量重建检索索引，耗时随语料增长，几十分钟到几小时。

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

### 启动（默认只起统一入口）

在工具仓里跑。默认只启动入口页一个进程：

```bash
python3 apps/home/server.py --root <实例根>       # http://127.0.0.1:8773
```

`<实例根>` 是有 `books.toml` 的那层。

其他页面按需再起，不随默认启动：

```bash
BOOKS_ROOT=<实例根> python3 apps/demo/server.py   # http://127.0.0.1:8765
python3 apps/ops/server.py --root <实例根>        # http://127.0.0.1:8770
python3 apps/reading/server.py --root <实例根>    # http://127.0.0.1:8771
python3 apps/daily/server.py --root <实例根>      # http://127.0.0.1:8772
```

演示页从 `BOOKS_ROOT` 认根，其余页面用 `--root`。首页读 `books.toml`、`books/` 与 `日课/`；点入口时如果对应服务没起，会代起并等端口就绪，已经在跑就直接跳转。页脚的「管理」也走同一条路，进入 8770。演示页只读索引与 `books/**.md`，回答缓存写到 `apps/demo/缓存/`（运行时可重建，已 ignore）。操作台读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调 `src/bookcorpus/scripts/` 里的脚本。日课页只读 `日课/*.json`，一期一份，文件名就是日期。生成用 `book-corpus daily --root <实例根>`，每天 07:00 的定时器用 `book-corpus daily install-timer` 安装。

### 出视觉读本

分层读是逐段读本；视觉读本是同一章的第二形态——xerox 复印稿风格的单页，含 hero、概览卡、图版、图表和 takeaways，正文仍逐字取自原文。章节工单里的「生成视觉读本」会调用模型，约 30–60 秒；文学、短章、超长章与图册会自动退回分层读。

```bash
uv run --frozen python - <<'PY'
from bookcorpus.reading.service import ReadingService
ReadingService('/home/colaqaq/书库').generate_edition('B0003', 6, effort='low')
PY
```

判决、字段、门禁与产物结构见 [docs/视觉读本.md](docs/视觉读本.md)。

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

视觉读本相关：`uv run --frozen pytest -q tests/test_reading_edition.py`。

| 改了什么 | 命令 | 通过判据 |
|---|---|---|
| 任何代码 | `uv run --frozen pytest` | 全部通过 |
| 人账 `tables/ledger.db` | `book-corpus library` | `conflicts=0`，`books` 等于人账行数 |
| 结构、脚本 | `book-corpus doctor` | 无 `fail` |
| 结构、检索层 | `book-corpus verify` | 末行「全过：N 项」 |
| 文档、端口、脚本清单 | `book-corpus docs check --data-root <实例根>` | 末行「全过」；改端口或脚本后先 `book-corpus docs write` |
| 书目或人账 | `book-corpus catalog` | 末行「未匹配 0 条」 |
| 自己的检索题集（可选） | `book-corpus run check_p1 --gold <题集>/gold.tsv` | 「全过：P1 N 项」；没有题集返回 2，算没跑 |

`verify` 是四个箱子：奖项 resolve、顶层与 `book.json`、从零重建一致、检索层（`--box 4`）。四个箱子对任何新库都成立，所以它没有「跳过」，末行不是「全过」就是失败。
脚本的退出码统一成：`0` 通过，`1` 有断言失败，`2` 前置缺失（没跑，比如没有题集）。`check_p1` 属于第三种：它验的是你自己的检索题集，新库没有题集就不该把它塞进收尾流程，一键收尾默认也不跑它。

`tests/acceptance/` 的 8 个离线入口（`check_ops`、`test_status`、`test_classify`、`test_doc_numbers`、`test_split`、`test_retire`、`test_pdf`、`test_answer_retry`）由 pytest 收集；需要 Chrome、真实模型或真实会话的 `check_demo`、`check_session*`、`check_result_ms`、`shot_ops`、`repro-*` 在 `tests/manual/`，清单与跑法见 `tests/manual/README.md`。

## 配置与密钥

所有 key、端点和开关都写在仓库根的 `.env`（已被 git 忽略）。先复制模板，再按需填：

```bash
cp .env.example .env
chmod 600 .env
```

真实环境变量永远优先；配置文件只读第一个存在的：仓库根 `.env` > 当前目录 `.env` > `~/.config/book-corpus/.env`。
`BOOK_CORPUS_ENV=/path/to/file` 可以整个换成另一个文件（指了就不回退）。
改完 `.env` 要重启对应服务（8771 分层读、8773 首页）才会读到。

### 缺配置会怎样

提交任务前按场景预检，缺了直接报「缺少配置：…」，写清缺哪个变量、当前值是什么、该写在哪。
分层读现在只保留速览、标准、深读三档，生成只检查模型 key；原研究层与引用层的
检索/抓页配置已随阶段 1 退役，不再有“确认后降级”的入口。

看全部能力的当前状态：

```bash
book-corpus doctor
```

脚本（`ingest`、分类、拆章修复、问答归纳、`mineru_book.py` 等）请走 `book-corpus run <name>`：
它会先读 `.env`、按场景预检，再启动。直接 `python src/bookcorpus/scripts/xxx.py` 绕过这一步。

| 能力 | 什么时候要 | 变量 / 文件 | 缺了会怎样 |
|---|---|---|---|
| 分层读·标注/结构模型 | 标注章节、生成或重建深读结构层 | `OPENCODE_GO_API_KEY`，或 pi 已登录 `opencode-go`（`~/.pi/agent/auth.json`） | 拒绝提交，提示配 key |
| 书库·模型 | `ingest`、分类、拆章修复、问答归纳 | 工作区 `books.toml` 的 `[model]` + 可执行的 `pi` | 脚本启动前报缺 |
| MinerU | PDF 读不出字、OCR 兜底 | `MINERU_API_KEY`，或 `~/.config/mineru/api_key` | `doctor` 标缺；OCR 任务自己也会报 |
| PaddleOCR | 扫描件逐页 OCR | `~/.config/PaddleOCR/api_key` | 跑之前报缺 |
| MiMo | `build_expand.py --provider mimo` | `~/.config/mimo/api_key` | 跑之前报缺 |
| pi 账号 | `build_expand.py` 用 commandcode / opencode | `~/.pi/agent/auth.json` 里有对应登录 | 跑之前报缺 |

### 分层读的模型 key

```bash
# .env
OPENCODE_GO_API_KEY=sk-...
```

不填也行，只要 pi 里 `/login` 选过 `opencode-go`——项目会读 `~/.pi/agent/auth.json`。
`READING_MODEL_NAME`、`READING_MODEL_URL`、`READING_MODEL_EFFORT`、`READING_MODEL_TIMEOUT`、
`READING_MODEL_MAX_TOKENS` 可以覆盖默认模型与端点。


### OCR 与扩展生成的 key

这两个脚本只读文件，不读 `.env`（历史行为，保持不变）：

```bash
printf '%s' '<token>' > ~/.config/PaddleOCR/api_key
printf '%s' '<key>'   > ~/.config/mimo/api_key
```

MinerU 两种写法都认：`MINERU_API_KEY`（可以放 `.env`）或 `~/.config/mineru/api_key`。

## 路径与配置

- `BOOKS_ROOT`：工作区根（有 `books.toml` 的那层）。优先级是 `--root` > shell 里 export 的 `BOOKS_ROOT` > 仓库根 `.env` 里的 `BOOKS_ROOT` > 从当前目录向上找 `books.toml`。`book-corpus init <目录>` 建完工作区会把根写进仓库根 `.env` 的这一行，所以一个仓只服务一个实例时不必再设环境变量。
- `BOOKS_SKILLS_DIR`：技能根，默认是仓库的 `.agents/skills/`。
- `BOOKS_RG`：`rg` 可执行文件路径，默认从 PATH 找。
- `PI_BIN`：`pi` 可执行文件路径，默认从 PATH 找。

脚本用工作区清单认根，不依赖当前工作目录，也不写死绝对路径。

## 写入规则

- 一个账本一个写者。人账的已有行只由人改。新行由 `ingest.py` 与 `classify_books.py --add-new` 追加，同一时刻只跑一个。
- 写文件用 tmp 加 `os.replace`。库里的旧文件可能与快照共享 inode，截断式原地写会同时改掉快照。
- 拆章产物落 `books/<uid>/md/` 才算落地。`v_pending_md` 视图只认这个目录。
- 把一本书移出流程：删 `md/`，`book.json` 改名 `book.json.归档`，书目录移入 `books/_冷宫/`，从 `tables/ledger.db` 删行（`book-corpus run retire_book` 会做这一步），再重建 DB、检索索引与视图。
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
- `docs/视觉读本.md`：视觉读本的判决、流水线、接口与产物；改 edition 前先看。
- `docs/design/设计方案-书目结构.md`：权威方案，L0 到 L5 门槛与目标结构。
- `CONTRIBUTING.md`：改代码、跑测试与提交的约定。
- `docs/检索/README.md`：检索层方案与实测基线。
- `docs/检索/踩坑记录.md`：检索层按类型分的踩坑账。改检索脚本前先查。
- `apps/ops/README.md` 与 `apps/ops/CONTRACT.md`：操作台的起停、接口与文件归属。
- `docs/briefs/BRIEF.md`：演示页的任务书。
- `reports/运维验收-2026-10-25/`：七页交互审计与证据。
