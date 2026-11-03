# 改代码

给要改这个仓的人。克隆与日常用法看 `README.md`；本机实例的账与约束看 `AGENTS.md`，那些数字不对克隆成立。

## 先跑起来

```bash
uv sync --frozen
uv run --frozen pytest
```

Python 3.11+，SQLite 要带 FTS5。测试默认只跑 `tests/`，不碰真库；`tests/acceptance/` 用临时根和 `pi` stub，离线可跑。

需要 Chrome、真实模型或真实会话的验收在 `tests/manual/`，不进 CI，跑法与前置条件见 `tests/manual/README.md`。

## 目录约定

- `apps/ops/`：操作台服务与 web。`apps/demo/`：检索演示与回答。两个应用都从实例根读数据（操作台 `--root`，演示页 `BOOKS_ROOT`），代码路径按自身位置推导。
- `src/bookcorpus/scripts/`：全部可复跑脚本。批次的一次性脚本不进本仓，放数据仓的批次目录。
- `tests/acceptance/`：pytest 收集的离线验收。`tests/manual/`：手工验收。测试产物写临时目录，不写仓库。
- `docs/design/`：方案与决策记录。`docs/briefs/`：已完成的任务书。`docs/检索/`：检索层口径与踩坑账。`reports/`：一次性审计与证据。
- 仓库里没有 `工作区/`。`工作区/` 是实例的运行时布局（批次账、缓存、虚拟环境），由 `book-corpus init` 生成，不进任何仓。

## 改动规则

- 一个账本一个写者。人账 `tables/分类表.csv` 的已有行只由人改，脚本只追加新行。
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
git status --short
```

改了结构或脚本再跑 `book-corpus doctor` 与 `book-corpus verify`；改了检索层加 `book-corpus run check_p1` 与 `book-corpus verify --box 6`。判据见 `README.md` 的「改完跑什么」。
