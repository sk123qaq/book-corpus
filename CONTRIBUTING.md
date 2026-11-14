# 改代码

给要改这个仓的人。克隆与日常用法看 `README.md`；本机实例的账与约束看 `AGENTS.md`，那些数字不对克隆成立。

## 先跑起来

```bash
uv sync --frozen
uv run --frozen pytest
```

Python 3.11+，SQLite 要带 FTS5。测试默认只跑 `tests/`，不碰真库；`tests/acceptance/` 用临时根和 `pi` stub，离线可跑。

需要 Chrome、真实模型或真实会话的验收在 `tests/manual/`，不进 CI，跑法与前置条件见 `tests/manual/README.md`。ops 的健康接口是 `/api/status`（没有 `/api/health`），验收按等价执行。

## 目录约定

- `apps/ops/`：操作台服务与 web。`apps/demo/`：检索演示与回答。两个应用都从实例根读数据（操作台 `--root`，演示页 `BOOKS_ROOT`），代码路径按自身位置推导。
- `src/bookcorpus/scripts/`：全部可复跑脚本。批次的一次性脚本不进本仓，放数据仓的批次目录。
- `tests/acceptance/`：pytest 收集的离线验收。`tests/manual/`：手工验收。测试产物写临时目录，不写仓库。
- `docs/design/`：方案与决策记录。`docs/briefs/`：已完成的任务书。`docs/检索/`：检索层口径与踩坑账。`reports/`：一次性审计与证据。
- 仓库里没有 `工作区/`。它是实例根下的运行时布局（本机 `~/书库/工作区/`：批次账、缓存、虚拟环境），由 `book-corpus init` 生成，不进任何仓。

## 改动规则

- 一个账本一个写者。人账 `tables/ledger.db` 的已有行只由人改，脚本只追加新行；新书走 `book-corpus ingest`，不手填。
- 写文件用临时文件加 `os.replace`；不要截断式原地写，库里的文件可能与快照共享 inode。
- 搬文件用 `git mv`，保留历史。改路径时同一波改完所有调用方，不留兼容别名。
- 不顺手改行为。搬目录、改引用、删可再生成的产物可以一个提交；逻辑变化单独提交，并带能判红绿的测试。
- 可再生成的 HTML/PNG/缓存不提交；留生成脚本，并在脚本头写一行怎么重跑。
- 文档里的路径必须能落到磁盘。历史文档保留原文时，在开头加布局说明，不要留下看似可用的旧路径。

## 提交

本地提交信息用 `type(scope): 中文摘要`，例如 `refactor(apps): 运维与演示从工作区搬到 apps/`。push、PR、合并要单独授权；没有授权只做本地提交。

提交前至少跑：

```bash
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python tools/ruff_strict_new.py
uv run --frozen mypy
uv run --frozen book-corpus docs check
git status --short
```

这六步也可以交给 git hook：

```bash
uv sync                       # 装上 pre-commit
uv run pre-commit install     # 之后每次提交自动跑
```

hook 跑五道闸：`ruff check`（规则见 `pyproject.toml` 的 `[tool.ruff]`）、`ruff format`、`ruff-strict`、`mypy`、`docs check`。
CI（`.github/workflows/ci.yml`）的 `lint` job 跑同一套，`test` job 在 3.11/3.12/3.13 上跑 pytest 与命令行冒烟：本地闸门过不了的，push 上去必红。

### ruff 严格档：新代码不许 os.path 拼接和 % 格式化

存量代码里 `os.path.join` 和 `"%s" % x` 有几千处，一次性改风险太大，所以按文件做增量治理：

- `pyproject.toml` 里 `extend-ignore = ["PTH", "UP031"]`，老代码照常能跑。
- `.ruff-legacy-files.txt` 是存量文件清单，里面的文件不受严格档约束。
- 不在清单里的文件（新加的文件、或已经迁干净的文件）必须通过 `ruff-strict.toml`：用 `pathlib` 而不是 `os.path`，用 f-string 而不是 `%`。
- 迁移一个老文件时，改干净后把它从 `.ruff-legacy-files.txt` 删掉，它就永久进严格档。
- 确有必要时写 `# noqa: PTH118 理由是……`，noqa 必须带中文理由。
- 紧急绕过：在命令前加 `SKIP=ruff-strict` 环境变量即可只跳过严格档。

单独跑严格档：`uv run python tools/ruff_strict_new.py <文件...>`（不带参数则检查全仓里所有不在清单中的文件）。

### mypy：只查核心包

类型检查的边界在 `pyproject.toml` 的 `[tool.mypy]`：`files = ["src/bookcorpus"]`，默认档，不追 `--strict`（严格档在这个仓库会报四千多条，绝大多数是 `Any` 传播）。

- 跑法：`uv run mypy`（不带参数，按配置查 112 个源文件）。
- `apps/`、`tools/`、`tests/` 不纳入：大量 `sys.path` 拼接与动态导入，噪音远大于收益。
- 确有必要时写 `# type: ignore[错码]  # 中文理由`，但先想有没有真的改对类型。

改了结构或脚本再跑 `book-corpus doctor` 与 `book-corpus verify`；改了检索层 `book-corpus verify --box 4`；有自己的检索题集时再跑 `book-corpus run check_p1 --gold <题集>/gold.tsv`。判据见 `README.md` 的「改完跑什么」。
