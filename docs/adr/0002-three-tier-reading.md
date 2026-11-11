# 三档分层读：research 与 citations 退役

状态：accepted · 2026-11-11

## 背景

`docs/adr/0001-four-reads-two-body.md` 让标准、深读、研究共用一份逐字正文，研究档
由提示词 + 代码裁决的检索支撑。实际落地后，研究档用固定配额产出判断矩阵、笔记与
问题，对一章 1,900 字的正文可以写出 2,300 字元文本，并且渲染在正文之前；它不回答
独立的阅读问题，在小说上退化成文本内部校对。`citations` 层又依赖 research 的矩阵
行生成查询，B0042/07 的 provider 额度失败仍被渲染成“已检索”。

## 决策

- 读者只保留三档：`skim / standard / deep`。`research` 从 reader、工单、导出、
  生成链、测试与用户文档退役；`citations` 一起退役，reader 不再出现来源面板。
- 旧 `?tier=research` 302 到 `?tier=deep`；`/api/export/.../research` 返回 410。
- legacy 导出只删除 `research.html`，保留 `skim / standard / deep`。
- 删除前先打 tar + manifest，落 `工作区/归档/`，并在批次目录留可复核的说明与
  sha256；研究档中有价值的至多 3 个问题迁入 `deep.json` 的 `open_questions`。
- 书目差异不再用第四档表达；书型读法与面板是阶段 2 的独立工作，不改变正文
  覆盖率、块 ID 或锚点。
- 外部核验如果以后要做，是独立产物，不再挂在阅读档位上。

## 后果

- `TIER_ORDER = ("skim", "standard", "deep")`，`DEPTH_LAYERS` 只到 `deep`；
  `reading.search` / `reading.fetch` 预检项、citations 测试与搜索配置退出主流程。
- 现有 depth pack 的 research/citations 文件被删除；B0017/03、B0042/07 的正文与
  锚点必须保持不变。
- 本 ADR 只取代 ADR 0001 中研究档与检索的部分；“标准/深读共用逐字正文”继续有效。

## 备选方案

- **保留 research 档但改配额**：拒绝。问题不是条数，而是它不回答阅读问题。
- **把矩阵降为附录**：拒绝。仍会继续生产文本内部的“研究”内容。
- **保留 citations 做来源面板**：拒绝。三档正文没有 claim 列表可供它挂载；以后
  的外部核验另立产物。
