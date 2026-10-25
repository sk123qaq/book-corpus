# curl 验收摘要

服务：`python3 工作区/演示-检索流程/server.py`（127.0.0.1:8765）

## /api/health

```bash
curl -s localhost:8765/api/health
```

```json
{
 "ok": true,
 "db": "ok",
 "meta": {
  "books": 1693,
  "paras": 2519767,
  "chapters": 87396,
  "index_built_at": "2026-10-23T18:29:40+0800"
 }
}
```

## 内卷和教育之间是什么关系

```bash
curl -s -X POST localhost:8765/api/run -H 'content-type: application/json' \
  -d '{"question":"内卷和教育之间是什么关系"}'
```

- 切词：`内卷 / 教育 / 教育竞争 / 应试教育 / 文凭`（source=cache，0.1 ms）
- rounds：3；stop=exhausted；best_round=1；plan.ms=356.2
  - 第1轮 noun：terms=内卷、教育、教育竞争、应试教育、文凭；hits=2000；top1=文学/中国新教育风暴（22.89）；分差=12.9%；A/B/C=False/True/False；stop=False；136.5 ms
  - 第2轮 noun+concept：terms=内卷、教育、教育竞争、应试教育、文凭、内卷化、过密化、involution；hits=2000；top1=文学/以日为鉴 衰退时代生存指南（30.30）；分差=24.6%；A/B/C=False/True/False；stop=False；140.8 ms
  - 第3轮 drop-generic：terms=内卷、教育竞争、应试教育、文凭；hits=651；top1=文学/中国新教育风暴（14.96）；分差=2.3%；A/B/C=False/False/False；stop=False；24.1 ms
- 书卡：trigger=True；reasons=['long', 'concept']；cards=6；next=6；cards.ms=164.2
  - 首卡：中国新教育风暴（文学，1 章，score=22.89，line=3994）
- ranking：8 条；首条=中国新教育风暴（22.89）
- chapters：3 本；首本=中国新教育风暴，1 章
- 结果：中国新教育风暴 / 中国新教育风暴；path=库/文学/中国新教育风暴/md/中国新教育风暴.md:3994；825 字；html=False；stage_ms={'understand': 0.1, 'rounds': 301.4, 'rank': 54.8, 'cards': 164.2, 'result': 1.8}

## 乡土中国 差序格局

```bash
curl -s -X POST localhost:8765/api/run -H 'content-type: application/json' \
  -d '{"question":"乡土中国 差序格局"}'
```

- 切词：`乡土中国 / 差序格局 / 熟人社会 / 团体格局 / 礼治`（source=cache，0.1 ms）
- rounds：3；stop=exhausted；best_round=1；plan.ms=89.3
  - 第1轮 noun：terms=乡土中国、差序格局、熟人社会、团体格局、礼治；hits=200；top1=人文社科/人类学与考古/乡土中国（28.50）；分差=0.0%；A/B/C=False/False/False；stop=False；9.9 ms
  - 第2轮 noun+concept：terms=乡土中国、差序格局、熟人社会、团体格局、礼治、差序、涟漪格局、熟悉社会、人情社会、团体社会、西洋格局、礼俗、教化、传统规矩、礼制；hits=1356；top1=人文社科/人类学与考古/乡土中国（2）（41.66）；分差=0.1%；A/B/C=False/False/False；stop=False；60.7 ms
  - 第3轮 drop-generic：terms=差序格局、熟人社会、团体格局、礼治；hits=91；top1=人文社科/人类学与考古/乡土中国（2）（26.89）；分差=0.0%；A/B/C=False/False/False；stop=False；4.5 ms
- 书卡：trigger=False；reasons=[]；cards=0；next=1；cards.ms=24.8
- ranking：8 条；首条=乡土中国（28.50）
- chapters：3 本；首本=乡土中国，3 章
- 结果：乡土中国 / 差序格局；path=库/人文社科/人类学与考古/乡土中国/md/06_差序格局.md:9；718 字；html=False；stage_ms={'understand': 0.1, 'rounds': 75.1, 'rank': 14.2, 'cards': 24.8, 'result': 0.2}

## 礼治社会靠什么维持秩序

```bash
curl -s -X POST localhost:8765/api/run -H 'content-type: application/json' \
  -d '{"question":"礼治社会靠什么维持秩序"}'
```

- 切词：`礼治 / 礼俗 / 教化 / 长老统治 / 传统规矩`（source=cache，0.1 ms）
- rounds：3；stop=exhausted；best_round=1；plan.ms=114.0
  - 第1轮 noun：terms=礼治、礼俗、教化、长老统治、传统规矩；hits=886；top1=人文社科/人类学与考古/乡土中国（25.05）；分差=0.4%；A/B/C=False/False/False；stop=False；36.7 ms
  - 第2轮 noun+concept：terms=礼治、礼俗、教化、长老统治、传统规矩、礼制、长老权力、教化权力；hits=1180；top1=人文社科/人类学与考古/乡土中国（2）（30.96）；分差=0.1%；A/B/C=False/False/False；stop=False；47.9 ms
  - 第3轮 drop-generic：terms=礼治、礼俗、长老统治、传统规矩；hits=159；top1=人文社科/人类学与考古/乡土中国（2）（22.07）；分差=9.7%；A/B/C=False/False/False；stop=False；6.8 ms
- 书卡：trigger=True；reasons=['margin', 'long', 'concept']；cards=6；next=6；cards.ms=59.4
  - 首卡：乡土中国（社科，17 章，score=25.05，line=21）
- ranking：8 条；首条=乡土中国（25.05）
- chapters：3 本；首本=乡土中国，3 章
- 结果：乡土中国 / 无讼；path=库/人文社科/人类学与考古/乡土中国/md/11_无讼.md:21；649 字；html=False；stage_ms={'understand': 0.1, 'rounds': 91.4, 'rank': 22.6, 'cards': 59.4, 'result': 0.2}

