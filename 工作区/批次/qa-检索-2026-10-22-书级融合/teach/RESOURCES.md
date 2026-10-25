# 书库检索 B5/B6 与审计 Resources

## Knowledge

- [审计报告-2026-10-23.md](../审计报告-2026-10-23.md)
  脚本/方案/验证审计的总报告：P0、B5 等宽、B6 性能基线、B4 缓存与停止线、B2 开发集。Use for: 任何「这个读数还能不能用」的问题。
- [live/B6/gate.tsv](../live/B6/gate.tsv)
  60 题逐题的 auto/loop/card、top1/top3/top5/top6 命中。Use for: 复算等宽口径，找「+19 从哪来」。
- [live/B6/perf.tsv](../live/B6/perf.tsv)
  qsearch mix / book_query mix / 默认，两遍交错。Use for: 看 B6 性能基线的差别。
- [live/B4/perf.txt](../live/B4/perf.txt)
  B4 的冷/热跑与缓存命中行。Use for: 0.038 ms 与 11-37 秒的口径差。

- [单元报告-B5.md](../单元报告-B5.md)
  B5 的结论、门槛、五张卡、反馈行与发现。Use for: 任何 B5 读数与决定的入口。
- [方案 §B5](../../../方案/书级融合与按需扩展-2026-10-22.md)
  触发规则、三动作、反馈字段、门槛的原始定义。Use for: 规则该不该这么定。
- [book_cards.py](../../../脚本/book_cards.py)
  实现：`query_hints`、`trigger`、`build_cards`、`apply_action`、`gate_verdict`。Use for: 某个数到底怎么算。
- [live/B5/gate.tsv](../live/B5/gate.tsv)
  60 题逐题读数：触发、auto/loop/card 命中、长书占比、排除类型后的读数。Use for: 复算汇总、找反例。
- [live/B5/trigger-20.tsv](../live/B5/trigger-20.tsv)
  20 题人工触发清单。Use for: 触发规则的边界与误触发。
- [live/B5/lane-1.json](../live/B5/lane-1.json)
  五张卡的原始 JSON。Use for: 卡片字段、`uncertainty`。
- [单元报告-B2.5.md](../单元报告-B2.5.md)
  循环基线（`loop_hit`）的来源。Use for: 为什么门槛要和循环比，而不是和首轮比。

- [课 3：同一把尺子](lessons/0003-same-ruler.html)
  审计的因果链：旧 +19 为什么是宽度差、B6 性能基线、B4 缓存与停止线、P0。
- [课 4：三条线](lessons/0004-three-paths.html)
  auto / loop / card 的分叉图，以及当前默认到底看几本。
- [审计速查](reference/audit-checklist.html)
  一页表：旧读数、审计后、还能不能用。

## Wisdom (Communities)

- 本项目没有外部社区；验收人是操作人。Use for: 规则取舍、默认值与是否 opt-in。

## Gaps

- 没有真实用户选择记录；`card_hit` 是「目标书在卡里且用户选中」的上限，不是用户读数。
- 没有前端 UI；书卡只以 CLI 与 JSON 形态存在。
