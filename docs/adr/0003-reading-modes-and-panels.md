# 书型读法与面板：模式不改正文

状态：accepted · 2026-11-11

## 背景

三档分层读解决了“读多深”，但没有解决“这本书该用哪种读法”。文学、论著、
实用书与图册共用 assumption/boundary/evidence 的深读脚手架，小说被读成论证，
图册没有图版层。书库 1,955 本里 1,417 本是文学，这个问题不是边角。

## 决策

- 读法（mode）是书级默认 + 章级覆写的 `narrative / argument / practical /
  reference`，不是第四个档位。档位仍由读者按精力与兴趣自选。
- 书级默认存 `tables/ledger.db` 的 `读法` 表（`chapter='*'`），由
  `build_library.py --emit-json` 派生 `books/<uid>/reading.json`；章级覆写必须
  带 `source_sha256`，源文变化后覆写失效。
- 模式只改注解类型、面板与建议入口，不改正文覆盖率、块 ID 或锚点。
- 面板（panels）是每章一次抽取的结构层，插在 model 与 skim 之间；每条 item
  必须有块锚点，人名/年份/数字/图注必须能在原文找到。空结构允许 `panels: []`，
  渲染提示，不阻塞正文。
- 深读注类型按模式分派：叙事 scene/character/motif/turn/setup_payoff；论证
  assumption/boundary/evidence/counter/intertext；实用 step/check/failure/
  decision/example；资料 plate/term/version/caption/provenance。未知类型报错。
- 叙事速览走 `kind: scenes`，3–10 个有效场景（目标 5–10），不要求命题链；
  论证/实用/资料仍走 claims。claims 每节 2–4 条；只有不超过 2 个 unit 的短节
  允许 1 条，避免长章被模型过分割后为凑比例注水。
- 标准档节导语先读 `chapter_model.sections[].lead`，为空时确定性回退到首个
  core unit 首句，截到 80 字；不重跑 chapter_model。

## 后果

- `DEPTH_LAYERS = ("model", "panels", "skim", "standard", "deep")`；缺少
  panels 的旧包判 stale。
- B0017/03 叙事、B0042/07 论证、B1848/03 实用、B0641 资料样本作为首批验收；
  B0641 只抽一个 H2 子样本，不整本渲染 752 图。
- 旧 deep 注解没有 `mode` 的包需要补 mode；类型与新 mode 不兼容时重跑，不静默
  降级为 argument。
- 面板与模式化注解仍是生成层；读者看到的正文与锚点不变。

## 备选方案

- **把读法做成第四档**：拒绝。它回答的是“怎么读”，不是“读多深”，会把档位
  选择变成书型选择。
- **面板按需即时生成**：拒绝。一次抽取、随包缓存，翻页与导出才能稳定复现。
- **空面板阻塞整章**：拒绝。面板是增强层，正文必须先可读。
