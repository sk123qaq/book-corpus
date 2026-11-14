---
name: sync-docs
description: >
  对照仓库代码变更与活文档（README.md、AGENTS.md、CONTRIBUTING.md、GLOSSARY.md、
  apps/*/README.md、apps/ops/CONTRACT.md、docs/、.agents/skills/），自动生成事实表、
  审计并修复路径/端口/命令/契约漂移。
  当完成一轮功能迭代、重构目录、增删脚本或技能、或定期做文档巡检时使用。
argument-hint: "[since-rev]"
---

# 同步与对账仓库文档（sync-docs）

本技能负责消除代码与文档之间的漂移（Doc Drift）。执行时严格遵守根目录 `AGENTS.md` 定义的**文档权威顺序**：

1. 代码与测试（`src/`、`apps/`、`tests/`、`pyproject.toml`）
2. 自动生成的事实快照（`docs/reference/facts.md`、`docs/INDEX.md`）
3. `docs/adr/` 中 `accepted` 的架构决定
4. 活文档正文（`README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`GLOSSARY.md`、`apps/*/README.md`、`apps/ops/CONTRACT.md`、`.agents/skills/**/SKILL.md` 与 `features/*.md`）
5. `docs/design/` 与 `docs/briefs/` 中的方案与实施单（按状态头区分现行与历史）

**核心原则**：事实不手抄，历史正文不回改；冲突时永远以代码为准修正文档，或给过期方案标记 `superseded` / `historical`。

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

该命令会从代码 AST 与 `pyproject.toml` 重写：

- `docs/reference/facts.md`（版本号、Python 要求、`apps/*/server.py` 默认端口、`src/bookcorpus/scripts/*.py` 脚本清单与 `cli.py` 别名）
- `docs/INDEX.md`（扫描 `docs/{adr,design,briefs}` 的状态与日期排序表）

若当前环境可访问实例库（如 `~/书库`），同步刷新 `AGENTS.md` 现状块（无真库时跳过）：

```bash
# 仅在需要更新本机实例统计时执行
# uv run --frozen book-corpus numbers --root ~/书库 --write
```

## Step 3: 运行静态一致性门禁

依次执行既有文档门禁与仓库级活文档校验脚本：

```bash
uv run --frozen book-corpus docs check --since "$SINCE_REV"
uv run --frozen python tools/check_docs_sync.py --strict
```

重点处理两类输出：

- `[since]` 警告：某份 `docs/{adr,design,briefs}` 声明的 `失效源：` 匹配到了本轮改动的文件，需人工核对方案正文是否仍成立。
- `[skills]` / `[paths]` / `[skill-links]` / `[ops-contract]` / `[cli]` 报错：直接定位失效的仓库路径、漏列的 `.agents/skills/*` 技能、过期端口、已下线 CLI 子命令或与 `apps/ops/server.py` 不符的 API 路由。

## Step 4: 按变更路径做语义对齐

根据 Step 1 的 `git diff --name-only` 结果，逐项核对受影响的活文档：

| 改动的代码路径 | 必须核对并更新的文档 | 核对要点 |
|---|---|---|
| `src/bookcorpus/cli.py` 或 `scripts/*.py` | `README.md`、`CONTRIBUTING.md`、`AGENTS.md` | 命令行示例、短别名、退出码说明是否与现行实现一致 |
| `apps/ops/server.py` 或 `apps/ops/web/*` | `apps/ops/CONTRACT.md`、`apps/ops/README.md`、`.agents/skills/verify-book-corpus/features/manage-library.md` | HTTP 路由表、任务状态枚举、页面页签与验收断言 |
| `apps/reading/**` 或 `src/bookcorpus/reading/**` | `GLOSSARY.md`、`docs/视觉读本.md`、`.agents/skills/verify-book-corpus/features/layered-reads.md` | 读法（reading mode）、三档分层读、视觉读本渲染契约与门禁 |
| `apps/demo/**` 或检索脚本 | `docs/检索/README.md`、`docs/检索/踩坑记录.md`、`.agents/skills/verify-book-corpus/features/find-a-passage.md` | 接口参数、Host 校验规则、会话与缓存目录说明 |
| `apps/daily/**` 或 `apps/home/**` | `apps/daily/README.md`、`apps/home/README.md`、`.agents/skills/verify-book-corpus/features/{daily-lessons,library-home}.md` | 启动参数、环境变量、代起服务行为 |
| `.agents/skills/**` | `AGENTS.md`（「文件地图」）、`README.md`（「5. 技能」表） | 新增/删除/改名的技能目录是否已同步到两处总表 |
| `pyproject.toml`、`ruff-strict.toml`、`.pre-commit-config.yaml` | `CONTRIBUTING.md`、`README.md` | 依赖列表、系统工具要求、提交流程与 lint 闸门说明 |

## Step 5: 治理过期的 `docs/design/` 与 `docs/briefs/`

- **严禁直接重写已归档历史文档的正文**。
- 若某份 `draft` / `proposed` / `accepted` 方案已被新实现推翻：
  - 将其首屏状态行改为 `状态：superseded · YYYY-MM-DD`
  - 紧跟一行写明 `替代：<新文档或新 ADR 路径>`
  - 再次运行 `uv run --frozen book-corpus docs write` 刷新 `docs/INDEX.md`
- 若某份 `accepted` / `proposed` 方案已完整落地：
  - 将其状态行改为 `状态：implemented · YYYY-MM-DD`
  - 补齐 `验收：<pytest 路径或验收命令>`

## Step 6: 复核并记录同步水位线

- 再次运行全部文档与代码格式检查，确认零报错：

```bash
uv run --frozen book-corpus docs check
uv run --frozen python tools/check_docs_sync.py --strict
uv run --frozen ruff check tools/
```

- 更新同步基线 commit，供下次增量巡检使用：

```bash
git rev-parse HEAD > docs/.last-doc-sync
```

- 在最终回复中输出《文档对齐摘要表》，列出：
  - 自动刷新的生成文件
  - 手工修正的漂移条目（文件路径 + 修正前/修正后）
  - 标记为 `superseded` / `implemented` 的设计文档
