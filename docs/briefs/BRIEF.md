# 检索流程演示页 · 设计说明


状态：已完成 · 2026-10-25
> **布局说明（2026-11-02）**：`<uid>` 是书目录占位，例如 `B0024`。v2 下书目录是 `books/<uid>/`，索引是 `build/检索/index.db`，书目库是 `build/library.db`，人账在 `tables/`。本文示例已按 v2 改写。


给 `apps/demo/` 实现一个本地网页 demo：用户问一句人话，页面按阶段展示书库怎么找到答案，最后落到某一本书的某一章那一段。数据全部来自真实检索，不许编。

## 目标与边界

- 观众：第一次接触这个书库的人。看完要能说出「它先切词，再换词检索，再给书打分，再选章，再弹书卡，最后给原文」。
- 形式：`python3 apps/demo/server.py` 起本地服务，打开 `http://127.0.0.1:8765`。
- 只用 Python 标准库；前端不用框架、不引 CDN、不联网取字体。
- 只读 `build/检索/index.db` 与 `build/library.db`，只读 `books/<uid>/**.md`。不写 `books/<uid>/`，不改现有脚本。
- 页面中文。桌面优先，手机能看。

## 五个阶段

1. **理解问题**：一句话 → 2 到 6 个检索词。由 `pi -p --no-tools --no-session --no-context-files --no-skills --no-extensions --model opencode-go/deepseek-v4.1-flash --thinking <档位>` 一次调用完成，提示词里给概念表（`tables/概念表.tsv` 的 126 个键与别名）与动作词表，要求只回 JSON `{"terms":["..."]}`。结果按问题与思考等级的 sha1 缓存到 `缓存/terms/<sha1>.json`。模型不可用时退回概念表包含匹配，再不行把整句当一个词，页面要如实标「降级」。
2. **词法循环**：`book_query.run(terms)` 的 `rounds`。每轮一张小卡：第几轮、换词类型、本轮词、命中段数、top1 书名、分差、A/B/C 三个判据、是否采用、耗时。
3. **打分与选章**：同一个 `Plan` 的 `ranking`（取前 8）与 `chapters`（取前 3 本，每本最多 3 章）。左半是书级分数条，右半或下方是章。这是真实打分，不重排。
4. **书卡**：`book_cards.run(terms, question=q, expand={})` 的 `trigger`、`reasons`、`cards`、`next`。`expand={}` 是必须的，否则会跑 B4 调模型，一次 60 秒还会写索引。
   弹卡且超过一张时，触发行右侧有一个「全选 N 本」开关：开着时每张卡都算已选，且第 5 阶段的回答改用书卡上的所有书；
   点某一张卡就退出全选、回到单书。
5. **结果**：默认选第一张卡（没弹卡就选 top1 书），显示它的最佳章开头原文。原文由 `/api/chapter` 从 `books/<uid>/` 的 md 按 `line` 附近截取，带 700 字前置与命中段整块高亮。结果区还有一张回答卡：检索层取同书多段原文，交给 `book-corpus-qa` 的 agent 归纳，引文回查后显示；回答完成后原文窗口自动切到第一条依据（见 `ANSWER-BRIEF.md`）。用户点别的卡，第 5 阶段跟着换。全选时回答一次盖住所有卡上的书，`/api/answer` 多带 `sources`，回答带 `mode`、`sources`，每条引文带书名（见 `ANSWER-BRIEF.md` 的「多书回答」）。

阶段耗时用真实读数：理解 = 切词调用耗时；循环 = `sum(r.ms)`；打分选章 = `plan.ms - sum(r.ms)`；书卡 = `cards.ms`；结果 = 读窗口耗时 + 该次回答的 `ms`（回答落地后加上，读取期间只显示读窗口那半）。回答是第 5 阶段最贵的一步，不能只算读文件。

## 页面与交互

**首屏（未提问）**
- 顶部一行小字：`书库检索`，右侧三个真实数字：1,693 本 / 2,519,767 段 / 87,396 章（启动时从 DB 读，别写死）。
- 中间一张像借书单的输入卡：大号衬线问题框，占位文案「问书库一个问题，例如：礼治社会靠什么维持秩序」，右边一个朱红按钮「检索」。
- 下面三个示例，点一下直接填入并开跑：
  - 礼治社会靠什么维持秩序
  - 内卷和教育之间是什么关系
  - 乡土中国 差序格局（这本不弹卡，用来展示直出）
- 底部一行小字：`本地演示 · 真实检索 · 不生成答案，只把原文递到你面前`。

**提问后**
- 输入卡收成顶部一条问题栏，左边可「换个问题」。
- 主区两栏。左栏 280px sticky 流程轨：01 理解问题 / 02 词法循环 / 03 打分与选章 / 04 书卡 / 05 结果。已完成的点实心，当前点朱红呼吸，未到灰色。窄屏时流程轨变成顶部五段进度条。
- 右栏是阶段卡片，当前阶段展开，完成的阶段先收成摘要行；五个阶段跑完后默认全部展开，点阶段头可收起。最后阶段结束后全部可回看。
- 提交后立刻进入工作区，第 1 阶段显示「正在理解这句话，切成检索词」的加载行（新问题要调一次模型）。

顶部右侧：`思考` 下拉、`重新检索`、`重播`、`跳过动画`。首屏同一份设置用四段按钮（关/低/中/高）。
`重新检索` 跳过切词与答案两处缓存，重跑五阶段并生成新回答。播放节奏最少每阶段 700ms，真实耗时照实标（`203 ms`），页脚注明「动画节奏为演示，耗时是实测」。
- URL 参数 `?q=...&instant=1&select=2&all=1&top=1&thinking=low` 供验收用：自动跑、不播动画、选第二张卡、全选所有卡、不自动滚到结果、指定思考等级。

**阶段内容**
1. 词块横排，名词前一个小圆点；被丢掉的动作词加删除线并标「动作词」；右上角标「模型切词 / 缓存 / 降级」。
2. 轮次卡横排；窄屏（≤700px）改成纵向堆叠，箭头朝下。采用的那轮加朱红左边框与「采用」角标。轮与轮之间用箭头连接，箭头上方小字写换词原因（名词集 → 同义组 → 去掉最泛的词）。下方一句判据解释，例如「C 成立：top1 书里有 5 段同时覆盖 3 个组」，用真实 `cover_paras`、`A/B/C` 生成。
3. 书级榜：名次、书名、类型、分数条（相对第一名）、覆盖 `3/3 组`、最佳段落两行。点书名展开完整段落。选章区按书分组，章名 + 分数 + 行号。
4. 顶部一条触发说明，把 `reasons` 翻成人话：`margin` → 前两名分差小于 10%；`types` → 前十名跨三个以上类型；`long` → 前十名里有 50 章以上的长书；`concept` → 概念型开放问题。卡片网格桌面三列、手机一列；每张卡：书名、类型、章节数、最佳章名、段落摘录（三行截断）、命中组小标签。整卡可点选，选中加朱红描边与勾。没弹卡时显示「书名精确命中，直接给章节」并跳过选择。
5. 结果卡：书名 + 章名 + 原文（约 700 字，从命中行开始），下面一行等宽小字给路径，右边「换一本」下拉（`next` 里的书）。原文里的检索词用浅朱红底标出（前端做，最多 20 处）。

**技术细节**：每个阶段卡片右下角一个「原始数据」开关，展开显示该阶段的 JSON（截断到 4KB），给 agent 和调试用，默认收起。

## 数据契约

服务端返回一份 trace，前端只认这一份：

```json
{
  "question": "礼治社会靠什么维持秩序",
  "thinking": "high",
  "extract": {"terms": ["礼治","秩序","法律"], "source": "model", "thinking": "high", "ms": 3200},
  "plan": {
    "terms": [], "nouns": [], "action_dropped": [],
    "rounds": [{"n":1,"kind":"noun","terms":[],"groups":[],"hits":2000,
                "top1":"人文社科/人类学与考古/乡土中国（2）","top1_score":27.88,
                "top2_score":27.59,"margin":0.0103,"cover_paras":5,
                "A":true,"B":false,"C":true,"stop":true,"ms":203.5}],
    "stop":"C","best_round":1,"ms":395.2,
    "ranking":[{"book":"...","title":"乡土中国（2）","type":"社科","chapters":18,
                "score":27.88,"coverage":1.0,"groups":["礼治","秩序","法律"],
                "best_path":"books/<uid>/.../12_无讼.md","best_line":16,
                "best_text":"◆法治秩序的建立……"}],
    "chapters":[{"book":"...","title":"乡土中国（2）","book_score":27.88,
                 "selected":[{"chapter_path":"books/<uid>/.../12_无讼.md","chapter_title":"无讼",
                              "line":16,"score":36.17,"snippet":"..."}]}]
  },
  "cards": {"trigger":true,"reasons":["margin","long","concept"],
            "hints":{"title":"","author":"","verbatim":false,"concept":true,"question":true},
            "cards":[{"book":"...","rank":1,"title":"乡土中国（2）","type":"社科",
                      "chapters":18,"best_chapter":"books/<uid>/.../12_无讼.md",
                      "best_chapter_title":"无讼","snippet":"...","matched_groups":[],
                      "score":27.88,"reason":"...","readme_status":"ok"}],
            "next":[{"book":"...","title":"...","chapters":18,"book_score":27.88,
                     "selected":[{"chapter_path":"...","chapter_title":"...","line":16,"score":36.17}]}],
            "ms":183.9},
  "stage_ms": {"understand":3200,"rounds":203.5,"rank":191.7,"cards":183.9,"result":2.1},
  "meta": {"books":1693,"paras":2519767,"chapters":87396,"index_built_at":"2026-10-23T18:29:40+0800"}
}
```

字段可以多，不能少；多余字段前端忽略。`ranking` 每条 `best_text` 截到 300 字，`scores` 不要带。`cards` 的全部保留。空结果（`ranking` 为空）要能渲染「没找到」状态，不许报错。

`GET /api/chapter?path=books/<uid>/.../12_无讼.md&line=16&chars=900` 返回 `{"ok":true,"path":"...","title":"无讼","text":"...","html":false}`。`path` 必须 realpath 在 `books/<uid>/` 下且以 `.md` 结尾，否则 400。`html` 表示同书是否有 `html/index.html`。

`GET /api/health` 返回 `{"ok":true,"db":"ok","meta":{...}}`，启动自检用。

## 视觉

- 纸感：底 `#f6f3ec`，卡片 `#fffdf8`，墨 `#1d1c1a`，次 `#6f6a60`，细线 `#d9d2c4`，朱红 `#a63d2f`，淡朱 `#f0e2dc`，成功绿 `#3f6b52`。
- 字：标题用衬线 `"Songti SC","Noto Serif CJK SC","Source Han Serif SC",serif`；正文 `"PingFang SC","Noto Sans CJK SC",system-ui,sans-serif`；数字与路径等宽 `ui-monospace,monospace`，`font-variant-numeric: tabular-nums`。
- 尺寸：h1 40–48，问题输入 22–24，阶段标题 19，正文 15，注 12。圆角 10，阴影只用 `0 1px 0 rgba(0,0,0,.04)` 这一档。
- 动效：进入 380ms `cubic-bezier(.2,.7,.2,1)`，位移 10px + 淡入；呼吸点 1.8s；分数条 600ms 长开。尊重 `prefers-reduced-motion`。
- 留白要够，阶段之间 28px，卡片内 20–24px。不要大面积渐变、不要玻璃拟态、不要彩色阴影。

## 验收

1. `python3 apps/demo/server.py` 起在 8765，`curl -s localhost:8765/api/health` 通。
2. `curl -s -X POST localhost:8765/api/run -H 'content-type: application/json' -d '{"question":"内卷和教育之间是什么关系"}'` 返回五阶段 trace，`rounds` 至少 2 轮，`cards.trigger` 为 true。
3. 同一个接口问「乡土中国 差序格局」，`cards.trigger` 为 false，`cards.cards` 为空，结果仍能出。
4. 浏览器验收（用 `google-chrome-stable --headless=new` 截图，存 `验收/`）：
   - 桌面 1440×900：`http://127.0.0.1:8765/?q=内卷和教育之间是什么关系&instant=1` 截到完整结果，能看到五阶段、轮次卡、书级榜、书卡、原文。
   - 手机 390×844：同一 URL，无横向滚动。
   - 首屏 1440×900：不戴 q 参数，截输入卡与示例。
   - 播放中一帧：`?q=...` 不戴 instant，3 秒后截，能看到当前阶段呼吸与已完成摘要。
5. 浏览器控制台无报错。至少用 `--dump-dom` 或 CDP 检查一次。
6. 点第二张书卡，结果区换成第二本书；用 `?q=...&instant=1&select=2` 可复现。

## 文件

```
apps/demo/
  server.py           HTTP 服务、/api/*、静态文件
  trace.py            切词缓存 + 跑 plan/cards + 组装 trace + 读章节
  answer.py           回答阶段：取窗、调 pi、回查引文、答案缓存
  thinking.py         思考等级白名单（页面、切词、回答共用）
  缓存/terms/         切词缓存（JSON，原子写，键含思考等级）
  缓存/answers/       回答缓存（JSON，原子写，键含思考等级）
  web/index.html
  web/style.css
  web/app.js
  README.md           起服命令、示例问题、接口、已知限制
  验收/               截图与 curl 输出
```

## 禁止

- 不改 `src/bookcorpus/scripts/` 下任何文件，不改 `books/<uid>/`，不写 `tables/`。
- 不在默认路径 import torch、不调 B4、不写 expand。
- 不引入 npm、CDN、外部字体、图表库。
- 不把耗时、数字、书名写死在前端；全部来自 trace 或 `/api/health`。
- 不生成「答案」文本；页面只呈现原文段落。

## 方向比较（写实现前定稿）

- **A 时间线 + 阶段卡（采用）**：问题在顶，五阶段自上而下展开，当前展开、完成收成摘要。观众按因果顺序读，回看容易，轮次卡与分数条都有地方放。
- **B 对话流**：提问、系统消息、结果像聊天。亲切，但阶段结构被聊天记录打散，五轮词法循环和书级榜放不进消息气泡，观众记不住管线。
- **C 仪表盘**：左问题右指标全程同屏。密度高，适合调参，但第一次看的人读不出先后与因果。

选 A：这个页面的观众是第一次接触书库的人，要的是「它怎么一步步找到」的顺序，不是同时看所有指标。B 与 C 的强项（对话感、密度）都不是这次的目标。
