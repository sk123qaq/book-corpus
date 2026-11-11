# 视觉读本 术语表（GLOSSARY）

配合 `docs/视觉读本.md` 与 `docs/design/视觉读本-决策记录-2026-11-07.md` 使用。本文只定义视觉读本链路的术语；
分层读、注释层、编辑层等概念沿用既有文档，这里仅在易混处澄清。

## 载体与档位

**视觉读本（visual edition）**
每章独立生成的"结构增强 HTML 页"，正文逐字保留，叠加目录、要点、图示、
流程、回看等编辑组件。产物固定为一个目录 `pack/edition/`，入口是 `edition/index.html`，
路由 `/reader/<uid>/<n>/edition`。不替换分层读任何档位。

**分层读（tiered reading）**
频道体系 `skim / standard / deep`，走 `tiers.py` 投影。原 `research` 档与
`citations` 层已于 2026-11-11 退役，不再出现在 reader、工单、导出与生成链。
视觉读本是**独立第五产物**，不是第四档：不进分层读任务、不进档位勾选框。

**读法（reading mode）**
书级默认 + 章级覆写的 `narrative / argument / practical / reference`。读法只决定
注解类型、面板类型与建议入口，不是档位；三个档位仍由读者按精力与兴趣自选。
书级默认存 `tables/ledger.db` 的 `读法` 表（`chapter='*'`），`build_library.py --emit-json`
派生 `books/<uid>/reading.json`；章级覆写必须带 `source_sha256`，源文变化后失效。

**面板（panels）**
每章一次抽取的结构层，按读法产出人物、时间线、场景节拍、论证图、证据表、步骤、
检查清单、图版、术语等卡片。每条 item 必须落到块锚点；人名、年份、数字、图注
必须能在原文找到。空章节允许 `panels: []` 并显示提示，不阻塞正文。

**场景速览（scene skim）**
叙事书的速览形态：`skim.json` 的 `kind` 为 `scenes`，3–10 个有效场景节拍（目标
5–10），每个 scene 有块锚点；正文合计不超过源文 25%，不要求命题链。论证/实用/
资料书仍走 `claims` 速览。

**节导语（section lead）**
标准档每节开头的一句摘要。生成器先读 `chapter_model.sections[].lead`，为空时确定性
回退到该节首个 core unit 的首句（再退到首个 unit 的首句），截到 80 字；不重跑
chapter_model。无源注的节保持 0 条标准注。

**Agent 标准档（agent standard）**
`agent_standard.py` 的直出 HTML 实验入口（`/reader/<uid>/<n>/agent`）。
视觉读本上线后它保持实验性质，不作为正式入口，也不合并。

## 内容与结构

**SourceDocument（源文档模型）**
统一的章节解析产物：front matter + 有序块 + 正文全文。
所有入库走 `books/<uid>/md/` 的 Markdown，由解析器成块
（`paragraph / heading / image / quote / list / table / code / footnote`），
块 ID沿用分层读的 `B%02d` 稳定编号。视觉读本与分层读共享同一解析结果，
保证 `content.json` 的锚点与分层读的锚点可互查。

**结（section）**
两个二级标题（H2，`## `）之间的连续块序列，含首个 H2 前的开头块。
长章折页的单位。结有 ID `S%02d`，由块 ID 推导，不单独落盘。
通篇无 H2 的章节视为单"结"，不打折页。

**content.json（视觉内容 JSON）**
视觉读本的编辑层数据，per-chapter 存 `pack/edition/content.json`。
只包含"叠加物"（章节要点、结引言、图示 spec、流程/对照/回看等），
**不含正文**。渲染时正文由 SourceDocument 按 `anchor`（结或块锚点）
插入，保证逐字与分层读一致。结构沿用分层读编辑层 `editorial.json` 的写法
（`beats / visuals / takeaways / claims`），并在其上扩展段落级结位。

**图示（plate）**
HTML/SVG 生成的示意、流程、对照、图表组件，对应 editorial 的 `kind`。
"图 N" 标题遵循分层读 `fig-kicker/fig-cap` 合并规则。

## 生成链路

**简报（brief）**
生成 `content.json` 前的结构摘要：章节题材信号、结构形态、图版候选。
仅作模型输入与日志，不落 pack 目录（区别于 editorial 的 brief）。

**模板适配（template fit）**
信号式模板选择：按题材/结构/图类型/阅读模式/推荐确定
视觉方向（目前仅 `xerox` 可用，其余为参考）。
分数在阶段日志展示并确认，未通过的不生成。

**视觉任务（generation job）**
`JobQueue` 中的 `kind=edition` 任务：brief+适配 → content 生成 →
门禁 → 渲染 `edition/index.html`。与分层读任务分列，允许并发。

**门禁（gates）**
发布前检查集合：G1–G9（分层读原有）+ V 系列（视觉读本）：
- V1: 无外部资源/脚本/样式；
- V2: 原文覆盖（每个块的文本在页面字面出现，含表格行、脚注序）；
- V3: 结标题次序正确、折页控制条无 JS 不渲染；
- V4: content.json 可解析、锚点合法，拿不到SourceDocument的锚点拒绝。

## 状态词

**结构不变（structural）**
解析到的块序与块 ID 在书库更新后不变，视觉读本的内容锚点继续有效。
分块稳定性由块 ID 决定，折页控制条不替换原文文字。
