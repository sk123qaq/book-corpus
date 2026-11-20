---
name: sync-docs
description: >
  对照仓库代码变更与活文档（README.md、AGENTS.md、CONTRIBUTING.md、GLOSSARY.md、
  apps/*/README.md、docs/、reports/、.agents/skills/），自动生成事实表、
  按 `失效源：` 定位并修复路径/端口/命令/退役词/已知红漂移。
  当完成一轮功能迭代、重构目录、增删脚本或技能、或定期做文档巡检时使用。
argument-hint: "[since-rev]"
---

# 同步与对账仓库文档（sync-docs）

本技能负责消除代码与文档之间的漂移（Doc Drift）。执行时严格遵守根目录 `AGENTS.md` 定义的**文档权威顺序**：

1. 代码与测试（`src/`、`apps/`、`tests/`、`pyproject.toml`）
2. 生成物（`docs/reference/facts.md`、`docs/INDEX.md`、`AGENTS.md`「现状」块）
3. 验证地图（`.agents/skills/verify-book-corpus/features/*.md`：产品现在做什么、怎么证明）
4. `docs/adr/` 中 `accepted` 的架构决定
5. 活文档正文（`README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`GLOSSARY.md`、`apps/*/README.md`、`docs/检索/README.md`）
6. `docs/design/` 与 `docs/briefs/`（按状态头读）
7. 记录（复盘、`reports/`、`artifacts/verify/`、踩坑账）

**核心原则**：事实不手抄，历史正文不回改；冲突时永远以高层为准修正低层，或给过期方案标记 `superseded` / `historical` 并登记 `docs/reference/retired.toml`。

## Step 1: 确定变更范围

在仓库根目录执行：

```bash
cd "$(git rev-parse --show-toplevel)"
SINCE_REV="${1:-$(cat docs/.last-doc-sync 2>/dev/null || git rev-parse HEAD~10)}"
git log --oneline "$SINCE_REV"..HEAD
git diff --name-status "$SINCE_REV"..HEAD
```

若 `docs/.last-doc-sync` 不存在或传入了显式 commit/tag，以传入参数为准。

## Step 2: 刷新自动生成快照

先运行仓库自带的生成命令，消除机械性数字与清单漂移：

```bash
uv run --frozen book-corpus docs write
```

该命令重写 `docs/reference/facts.md`（版本、Python 要求、应用默认端口、脚本与别名）与 `docs/INDEX.md`（扫描 `docs/**` 除 `reference/`/`agents/` 与 `reports/**` 的状态索引）。

若当前环境可访问实例库（如 `~/书库`），按需刷新 `AGENTS.md` 现状块（无真库时跳过）：

```bash
# uv run --frozen book-corpus numbers --root ~/书库 --write
```

## Step 3: 运行静态门禁并拿到点名清单

```bash
uv run --frozen book-corpus docs check --since "$SINCE_REV"
uv run --frozen python tools/check_docs_sync.py --strict
```

不需要手工猜测改了哪些文档——每份活文档、应用手册、`verify-book-corpus/features/*.md` 与 `features/README.md` 已知红表都自带 `失效源：` 声明。逐条处理两类输出：

- **`[since]` 警告**：逐条打开被点名的文档或已知红表行，核对代码变动是否改变了用户可见行为、接口、参数或已知红状态；改了就同步更新文档（已知红已修复就实跑验证并关闭），未受影响也要核对确认。
- **门禁报错**（`[status]` / `[tokens]` / `[links]` / `[ports]` / `[skills]` / `[paths]` / `[skill-links]` / `[ops-routes]` / `[cli]` / `[retired]`）：直接修对应活文档；若命中 `[retired]` 说明活文档回流了已退役术语或路由，按 `docs/reference/retired.toml` 给出的替代链接改写。

## Step 4: 治理过期的 `docs/` 与 `reports/`

- **严禁重写 `historical` / `superseded` / `implemented` 的历史正文**。
- 方案已完整落地：状态行改为 `状态：implemented · YYYY-MM-DD`，补 `验收：<命令或测试路径>`。
- 方案被新实现推翻：状态行改为 `状态：superseded · YYYY-MM-DD`，补 `替代：<路径>`，并在同一提交把退役名词或路由追加到 `docs/reference/retired.toml`。
- 新增活参考文档（如 `docs/检索/` 下）：状态行写 `状态：living · YYYY-MM-DD`，同时写 `失效源：<glob 路径...>`。
- 改完状态行后重跑 `uv run --frozen book-corpus docs write` 刷新 `docs/INDEX.md`。

## Step 5: 复核并记录同步水位线

```bash
uv run --frozen book-corpus docs check
uv run --frozen python tools/check_docs_sync.py --strict
uv run --frozen ruff check tools/
git rev-parse HEAD > docs/.last-doc-sync
```

在最终回复中输出《文档对齐摘要表》：自动刷新的生成文件、处理过的 `[since]` 点名项、手工修正的漂移条目、以及状态变更或新入 `retired.toml` 的条目。
