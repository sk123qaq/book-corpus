# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **应用、阅读、检索与同步词汇**：`GLOSSARY.md`（五个本地应用、正文工作台、阅读视角、结构蓝图、视角扩展、按需知识地图、划词伴读、混合检索、事件驱动增量同步，以及已退役术语对照）。
- **书库数据与流程词汇**：`AGENTS.md` 的「文件地图」（book、uid、人账、机账、批次、拆章、存档、冷宫）。
- **用户可见行为**：`.agents/skills/verify-book-corpus/features/*.md`。
- **`docs/adr/`**：动手前先读碰到该区域的 ADR。

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

本仓是单工具仓、两类词汇：阅读层在根 `GLOSSARY.md`，书库数据层在 `AGENTS.md` 的「文件地图」；
决定只有一套 `docs/adr/`，没有按 `src/<context>/` 分的子 ADR。文档状态与索引由
`book-corpus docs check` 对账。

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
