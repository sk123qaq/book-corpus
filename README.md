# 书库

本地电子书库的工具箱。每本书一个目录，正文拆成章节 Markdown，另有可视化阅读版 HTML。SQLite 保存书目与检索索引，全库正文可以按词、按概念、按书检索，也可以交给 pi agent 按原文回答问题，答案带逐字引文。

仓库只跟结构、脚本与账，不跟书的正文。`库/**/source/`、`md/`、`html/` 都在 `.gitignore` 里。克隆后 `库/` 里只有书目元数据（`book.json`），没有正文。

## 能做什么

- 找书：按书名、作者、分类路径查 `目录/library.db`。
- 查原文：段落级倒排检索，支持概念同义词组，返回 `书目录/章节.md:行号`。
- 问书：`book_query.py` 定位书与章。要成段回答并带逐字引文，用 `book-corpus-qa` 技能或演示页。
- 管书：入库、分类、拆章、出 HTML 阅读版、PDF 转 Markdown、删书与冷宫。
- 看库：本地操作台（8770）与检索演示页（8765），默认只绑 127.0.0.1。

## 目录结构

| 路径 | 内容 |
|---|---|
| `库/` | 唯一权威内容区。一本书一个目录，含 `book.json`、`source/`、可选 `md/` 与 `html/` |
| `库/_收件箱/` | 新书落地处，入库走 `ingest.py` |
| `库/_冷宫/` | 移出处理流程的书，只留原书文件，不在人账，不进检索 |
| `目录/分类表.csv` | 唯一人账，一行一本书。只有它允许人改 |
| `目录/library.db` | 全库索引，SQLite，产物，可重建 |
| `目录/书目总表.csv` | 从 DB 导出的书目，含 md 状态、主源文件、路径，产物 |
| `目录/概念表.tsv` | 检索层的概念键与同义词组，人写人改 |
| `目录/检索/` | 检索索引与扩展账，`index.db` 可重建 |
| `目录/阅读版索引.html` | 有阅读版 HTML 的书的总索引，产物 |
| `视图/奖项/`、`视图/主题/` | 指向 `库/` 的软链树，随时可删可重建 |
| `.agents/skills/` | 随仓库提交的 pi 技能，见下文 |
| `工作区/脚本/` | 全部脚本 |
| `工作区/方案/` | 方案与决策记录 |
| `工作区/批次/` | 每批转换、评测、下载的机账 |
| `工作区/运维/` | 统一操作台服务与验收 |
| `工作区/演示-检索流程/` | 检索流程演示页服务与验收 |

`book.json` 只记源元数据（uid、书名、学科、sources、awards）。md 与 html 状态在 `library.db` 的 `derived` 表里，由 `build_library.py` 按磁盘生成，不写回 `book.json`。

## 从零搭起来

### 1. 系统依赖

| 依赖 | 必需性 | 用途 |
|---|---|---|
| Python 3.10+ | 必需 | 全部脚本。开发与验收用 3.14 |
| SQLite（带 FTS5） | 必需 | `library.db` 与 `index.db`。Python 自带 `sqlite3` 模块即可，需确认 FTS5 可用 |
| Git | 必需 | 版本管理 |
| ripgrep | 检索与验收需要 | 全文定位。`rg` 放到 PATH，或用 `BOOKS_RG` 指定 |
| Calibre 的 `ebook-convert` | 拆 epub、mobi、azw3 必需 | 先转 EPUB 再拆章 |
| poppler 的 `pdfinfo`、`pdfimages`、`pdftoppm` | PDF 普查与扫描件探针必需 | 页数、图片清单、抽页渲染 |
| `pi` CLI | 模型功能必需 | 分类、拆章修复、问答通过它调模型 |
| headless Chrome | 可选 | 操作台截图验收，`CHROME_BIN` 可换路径 |

Fedora 装系统工具：

```bash
sudo dnf install calibre poppler-utils ripgrep
```

Debian 与 Ubuntu：

```bash
sudo apt install calibre poppler-utils ripgrep
```

`pi` 由 npm 包 `@earendil-works/pi-coding-agent` 提供。装好后在 `~/.pi/agent/auth.json` 里配置至少一个模型 provider。

### 2. Python 环境

核心流程只用标准库。`qsearch.py`、`book_query.py`、`build_library.py`、`build_para_index.py`、`lq.py` 不需要装第三方包。

PDF 路线与重排实验共用一个虚拟环境 `工作区/.venv-pdf/`：

```bash
python3 -m venv 工作区/.venv-pdf
工作区/.venv-pdf/bin/pip install -r requirements.txt
# 只做重排实验时再装
工作区/.venv-pdf/bin/pip install -r requirements-rerank.txt
```

PDF 脚本必须用这个解释器跑。`pdf_md_batch.py` 启动时会检查当前解释器能否 `import pymupdf`，不能就直接退出。

`requirements.txt` 还包含 `beautifulsoup4`，`ebook-to-md` 技能解析 EPUB 要用。两个可选包按需装：`pip install commonmark lxml`。`commonmark` 让 `md_link_health.py` 按渲染结果判断链接，`lxml` 让 EPUB 解析更快。

### 3. 模型与账号

多数脚本通过 pi 调模型。文档扩展直连 provider 接口，自己从 `~/.pi/agent/auth.json` 读 key。

| 默认模型 | 用在哪 | 怎么换 |
|---|---|---|
| `opencode-go/deepseek-v4.1-flash` | 书目分类、拆章失败修复、问答归纳 | `INGEST_CLASSIFY_MODEL`、`CLASSIFY_BOOKS_MODEL`、`MD_FIX_MODEL`、`ANSWER_MODEL` |
| `opencode-go/glm-5.3-flash` | pi 子代理的独立评审与验证 | `~/.pi/agent/settings.json` 的 `subagents.agentOverrides` |
| `deepseek/deepseek-v4.1-flash`（commandcode） | `build_expand.py` 默认 provider | `build_expand.py --provider` |
| `mimo-v2.6-flash` | `build_expand.py --provider mimo` | 密钥在 `~/.config/mimo/api_key` |

`build_expand.py` 还支持 `--provider opencode`，复用 opencode-go 的授权。它直连接口，不走 pi。

两个云 OCR 接口：

| 服务 | 凭据 | 用途与限制 |
|---|---|---|
| PaddleOCR | `~/.config/PaddleOCR/api_key` | 扫描件逐页 OCR。平台有每日页数额度，用尽返回 HTTP 403 code 12001 |
| MinerU | `~/.config/mineru/api_key` 或 `$MINERU_API_KEY` | OCR 兜底，读不出的可疑页补投。CLI 在 `.agents/skills/mineru/scripts/mineru.py` |

### 4. 技能

问答与转换依赖的 pi 技能已经放在仓库里，不需要另外安装。Pi 会自动发现项目下的 `.agents/skills/`。

| 技能 | 路径 | 谁在用 |
|---|---|---|
| `book-corpus-qa` | `.agents/skills/book-corpus-qa/` | `answer.py` 与问答流程，按原文回答并核对引文 |
| `ebook-to-md` | `.agents/skills/ebook-to-md/` | 拆章、合集拆分、覆盖率审计、产物校验 |
| `chapter-html-versions` | `.agents/skills/chapter-html-versions/` | 生成可视化阅读版 HTML，附 `verify_lite.py` 校验 |
| `mineru` | `.agents/skills/mineru/` | OCR 兜底 |
| `typesafe-ai` | `.agents/skills/typesafe-ai/` | 旧分类后端，默认已改用模型 |

脚本解析技能目录的顺序是：仓库内 `.agents/skills/`，其次 `~/.agents/skills/`。可以用 `BOOKS_SKILLS_DIR` 指定别的根。如果本机 `~/.agents/skills/` 里有同名技能，Pi 交互会话可能加载其中一份；脚本固定优先用仓库内的。`typesafe-ai` 不附带密钥。旧后端读 `.agents/skills/typesafe-ai/.env` 里的 `TYPESAFE_API_KEY`，或用 `TYPESAFE_ENV` 指向别的文件。

### 5. 本地模型（可选）

`工作区/models/` 下的模型都不在 Git 里。只跑检索与问答不需要它们。

| 模型 | Hugging Face 模型 | 放到 | 状态 |
|---|---|---|---|
| `bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` | `工作区/models/bge-reranker-v2-m3` | `rerank.py` 在用，做交叉编码器精排。只在实验台里，未接入 `book_query.py` |
| `bge-m3` | `BAAI/bge-m3` | `工作区/models/bge-m3` | 段落向量方案的原定模型。`sem_index.py` 还没写，向量层当前不做 |
| `Qwen3-Embedding-0.6B` | `Qwen/Qwen3-Embedding-0.6B` | `工作区/models/Qwen3-Embedding-0.6B` | 向量备选，未使用 |

下载示例：

```bash
huggingface-cli download BAAI/bge-reranker-v2-m3 \
  --local-dir 工作区/models/bge-reranker-v2-m3
```

重排实验需要能用的 GPU。`rerank.py` 的设备名写死 `cuda`，AMD 显卡用 ROCm 版 torch 也映射成这个名字。

### 6. 准备语料

仓库带着作者的书目账（`目录/分类表.csv` 与各书的 `book.json`），正文不在 Git 里。要用自己的书，有两条路：

- 在本仓库的 `库/` 里继续加书，走 `ingest.py`。
- 另起一个数据根，设 `BOOKS_ROOT` 指过去。新根至少要有 `库/`、`目录/分类表.csv`（表头 `uid,一级,二级,作者层,保留,正式书名,备注`）、`目录/概念表.tsv` 与 `目录/系列/`。

入库流程：

```bash
python3 工作区/脚本/ingest.py scan --out plan.json
python3 工作区/脚本/ingest.py apply plan.json --apply
```

`scan` 只读，出计划。`apply` 默认也只打印，加 `--apply` 才写盘。

入库只把源和 `book.json` 放进库，还没有 md。要能检索，先拆章：

```bash
python3 工作区/脚本/split_md_batch.py --batch <批次名> --uids <uid>
```

`split_md_batch.py` 会转 EPUB、拆章、verify、audit，过了才把 md 原子落进 `库/.../md/`。epub 不需要 Calibre；mobi、azw3、azw 需要先装 Calibre 的 `ebook-convert`。只想快速拆一本、不走批次账时，也可以用 `ebook-to-md` 技能：

```bash
python3 .agents/skills/ebook-to-md/scripts/ebook_to_md.py <书>.epub -o 库/<path>/md
python3 .agents/skills/ebook-to-md/scripts/verify_output.py 库/<path>/md
```

`split_md_batch.py` 从 `library.db` 找待拆书，所以刚 `ingest` 完要先跑一次 `build_library.py`。它只在 `工作区/批次/<批次名>/` 里生成一次 `worklist.tsv`，重复运行以账为准。

建库：

```bash
python3 工作区/脚本/build_library.py --emit-json
python3 工作区/脚本/build_para_index.py --rebuild
```

`build_library.py` 读 `分类表.csv` 与 `book.json`，重建 `目录/library.db`。`build_para_index.py --rebuild` 全量重建检索索引，耗时随语料增长，几十分钟到几小时。

## 常用命令

### 找一本书

```bash
cd <仓库根>
python3 工作区/脚本/lq.py -H "SELECT uid,title,author,path FROM book WHERE title LIKE '%核心词%' OR author LIKE '%核心词%'"
```

`lq.py` 只读打开 `目录/library.db`，输出制表符分隔。

### 查原文

```bash
python3 工作区/脚本/qsearch.py '关键词 关键词' -n 5
python3 工作区/脚本/qsearch.py --concept 概念键 -n 5
```

返回 `库/…/md/xx.md:行号`、分数与命中段。`--book` 出书级聚合，`--concept` 走概念表。

### 问一本书

```bash
python3 工作区/脚本/book_query.py 关键词 关键词 --no-cards
```

默认先做书级定位，再选章。书卡默认开，触发时弹 5 到 8 张，`--no-cards` 关闭。按需扩展默认关，加 `--expand-on-demand` 才跑。

要成段回答并带引文，用 `book-corpus-qa` 技能，或起演示页。

### 起两个本地页面

```bash
python3 工作区/演示-检索流程/server.py   # http://127.0.0.1:8765
python3 工作区/运维/server.py             # http://127.0.0.1:8770
```

演示页只读索引与 `库/**.md`，回答缓存写到 `工作区/演示-检索流程/缓存/`。操作台读 `ops_status.py` 的 JSON 与 `ops.db`，按钮排队调脚本。

### 重建产物

```bash
# 改完人账后重建 library.db 与每本书的 book.json
python3 工作区/脚本/build_library.py --emit-json

# 重建检索索引
python3 工作区/脚本/build_para_index.py --rebuild

# 重建每本书的 html 索引与阅读版总索引
python3 工作区/脚本/build_index.py

# 重建视图/主题软链
python3 工作区/脚本/build_topic_views.py
```

## 改完跑什么

| 改了什么 | 命令 | 通过判据 |
|---|---|---|
| 人账 `目录/分类表.csv` | `python3 工作区/脚本/build_library.py --emit-json` | `conflicts=0`，`books` 等于人账行数 |
| 结构、脚本、html | `python3 工作区/脚本/verify_phase.py` | 末行「全过」，缺基线时部分箱子打印「跳过」 |
| 书目或人账 | `python3 工作区/脚本/gen_book_catalog.py` | 末行「未匹配 0 条」 |
| 检索层 | `python3 工作区/脚本/check_p1.py` 与 `python3 工作区/脚本/verify_phase.py --box 6` | 前者「失败 0 项」或 `SKIP`，后者「全过」 |
| 操作台 | `python3 工作区/运维/验收/check_ops.py` | 末行 `ALL PASS` |
| 演示页 | 先起演示页，再跑 `python3 工作区/演示-检索流程/验收/check_demo.py` | 全部「过」，缺三体时打印「[跳过]」 |

`verify_phase.py`、`check_p1.py`、`check_demo.py` 与 `gen_book_catalog.py` 是作者实例检查。缺基线、评测书、三体或凡人语料时，前三个会打印 `[跳过]` 或 `SKIP`；`gen_book_catalog.py` 需要源文件在盘，克隆状态不会通过。只验证部署本身时跑 `check_ops.py`。

## 路径与配置

- `BOOKS_ROOT`：数据根，默认是仓库根。把代码与语料分开放时用它。
- `BOOKS_SKILLS_DIR`：技能根，默认是仓库的 `.agents/skills/`。
- `BOOKS_RG`：`rg` 可执行文件路径，默认从 PATH 找。
- `PI_BIN`：`pi` 可执行文件路径，默认从 PATH 找。

脚本用自身位置推导仓库根，不依赖当前工作目录，也不写死绝对路径。

## 写入规则

- 一个账本一个写者。人账的已有行只由人改。新行由 `ingest.py` 与 `classify_books.py --add-new` 追加，同一时刻只跑一个。
- 写文件用 tmp 加 `os.replace`。库里的旧文件可能与快照共享 inode，截断式原地写会同时改掉快照。
- 拆章产物落 `库/<一级>/<书名>/md/` 才算落地。`v_pending_md` 视图只认这个目录。
- 把一本书移出流程：删 `md/`，`book.json` 改名 `book.json.归档`，书目录移入 `库/_冷宫/`，从 `目录/分类表.csv` 删行，再重建 DB、检索索引与视图。
- 批次机账只追加。`worklist.tsv` 一旦生成就以它为准，不重写。

## 已知限制

- 仓库不含书的正文。克隆后只有书目账，正文要自备。
- 只有 `md/` 存在的书能搜到正文。只有 epub 或 PDF 的书先看 `目录/书目总表.csv` 的 `md状态` 列。
- 向量检索未建。重排只在实验台，`book_query.py` 不调用它。
- 演示页与操作台默认只绑 `127.0.0.1`，没有鉴权。改 `--host` 前先想清楚，不要暴露到局域网。

## 许可证

MIT，见 `LICENSE`。

## 更多文档

- `AGENTS.md`：作者本机实例的运行手册，含批次账与脚本表。新克隆的仓库没有那些批次数据。
- `工作区/方案/设计方案-书目结构.md`：权威方案，L0 到 L5 门槛与目标结构。
- `目录/检索/README.md`：检索层方案与实测基线。
- `目录/检索/踩坑记录.md`：检索层按类型分的踩坑账。改检索脚本前先查。
- `工作区/运维/README.md` 与 `工作区/运维/CONTRACT.md`：操作台的起停、接口与文件归属。
- `工作区/演示-检索流程/BRIEF.md`：演示页的任务书。
