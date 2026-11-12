# 检索流程演示页

> **布局说明（2026-11-02）**：本文写于 v1 布局。旧示例里的 `库/…` 现在对应 `books/<uid>/…`，`目录/library.db` 对应 `build/library.db`，`目录/检索/` 对应 `build/检索/`，`视图/` 对应 `build/views/`，`工作区/脚本/` 对应 `src/bookcorpus/scripts/` 或 `book-corpus` 子命令。正文保留当时写法，按此表读。


一句话问题进去，五个阶段出来，最后落到书库里某一章的原文段落；结果阶段再接一次「回答」：
检索层先取同书多段原文（命中段带 700 字前置上下文），交给 `book-corpus-qa` 技能的 agent 归纳，
服务端逐条回查引文行号后才显示。书卡阶段点「全选」时，回答改成一次盖住书卡上的所有书。
检索与原文全部来自真实数据，模型只做归纳，不做检索。

## 起

```bash
python3 apps/demo/server.py
```

打开 http://127.0.0.1:8765 。默认只监听 127.0.0.1:8765，`--port` 可换。

- 示例问题点一下就跑：礼治社会靠什么维持秩序 / 内卷和教育之间是什么关系 / 乡土中国 差序格局。
- 自由提问会先用 `pi` 调一次 `opencode-go/deepseek-v4.1-flash` 切词，约 3 到 5 秒；同问题同思考等级命中缓存。
- 回答是第二次调用（思考等级默认 `high`，页面可调 关/低/中/高）：单书约 15 到 40 秒，多书 65 到 87 秒（六本实测），
  同问题同档第二次命中 `缓存/answers/`，毫秒级。顶部「重新检索」跳过两处缓存，重新切词、检索并生成新回答。
- 页脚写明「动画节奏为演示，耗时是实测」：每个阶段的毫秒数来自真实调用。

## 五个阶段与数据来源

| 阶段 | 数据 |
|---|---|
| 理解问题 | `extract.terms`，模型切词，缓存 `缓存/terms/<sha1>.json` |
| 词法循环 | `book_query.Plan.rounds`（最多 5 轮，A/B/C 判据、分差、耗时） |
| 打分与选章 | 同一个 `Plan` 的 `ranking` 前 8 与 `chapters` 前 3 本 |
| 书卡 | `book_cards.CardSet`（`expand={}` 关掉 B4，不写索引）；书卡超过一张时右上角有「全选 N 本」 |
| 结果 | 选中书的最佳章：前置 700 字 + 命中整行 + 后置 900 字；回答另走 `POST /api/answer`，完成后原文窗口自动切到第一条依据。全选时回答用书卡上的所有书 |

全部跑完后五个阶段默认展开，点阶段头可收起；左栏流程轨可跳转。播放中「跳过动画」直接到终态。

## 回答阶段（设计见 `ANSWER-BRIEF.md`）

段、章、窗口的数量关系图解：`图解-段-章-窗口.html`（浏览器直接打开）。

1. 切词同一次调用还给出 3 到 5 组同义说法（`extract.groups`），随问题缓存。
2. `qsearch.search(..., 100, ...)` 取同书前 100 段，按章聚类出前 30 章候选地图。
3. 组覆盖二遍：在这 100 个同书候选段上，用 `defpass.score` 按 groups 打分，取前 4 段。
   只在前 100 候选上跑，不扫全库，避免 README 和无关章把候选冲上来。
4. 当前命中 + 组覆盖 + 章聚类 → `book_expand.make_packet(..., level=2, max_chars=3500)`
   取有界窗口；最多 5 窗、合计 18000 字。候选章地图单独给 agent。窗口参数的理由见
   `docs/design/窗口参数-决策记录-2026-11-02.md`。
5. 提示词带候选章地图、窗口和两个只读工具：
   `pi -p -e readonly_tools.ts --tools book_grep,read --skill ~/.agents/skills/book-corpus-qa/SKILL.md
   --model opencode-go/deepseek-v4.1-flash --thinking <档位> "<prompt>"`，超时 240 秒。
   `<档位>` 由页面选，默认 `high`，进答案缓存键。
   `book_grep` 只搜本书 md 目录；没有 bash、edit、write。
   单书提示词要求先判断这一问要什么，领句 1 到 4 句给判断，中间 1 到 6 个小标题（也可以没有），
   每节接住上一节留下的问题；分歧与缺口写在相关小节里，不另开段。全篇不超过 5000 字，事实少就写短。
6. 解析第一个括号配平的 JSON 对象（去代码块围栏）。单书与多书都读 `lead`、`sections`、`dissent`、`gap`
   并拼出 `answer`；全空时退回模型给的 `answer`，再空就是 `not_found=true`。
7. 回查引文：`quote` 去空白后的前 20 字，在对应章文件逐行找；找不到再整章找；
   行号一律改成文件实际行号，仍找不到就丢掉这条。`path` 必须 realpath 在 `库/` 下且 `.md`。
8. 通过 ≥ 2 条保留模型给的 `confidence`，0 到 1 条降到 `low` 并在 `error` 写「引文回查不足」。
   依据按「离命中行的距离」排序，只留 10 条；整份 Answer 缓存到 `缓存/answers/<sha1>.json`
   （键含 question、book、path、line、terms、groups、`PROMPT_VERSION`、思考等级），原子写。
9. 回答完成后，前端自动把原文窗口切到第一条 verified 依据，并高亮该依据；点其它依据仍可切换。

### 多书（全选）

书卡阶段点「全选 N 本」，第 5 阶段的回答就改成一次答完所有卡上的书。

1. 请求多带 `sources`：每本一项 `{book, title, path, line}`，最多 8 本，书从 path 推。
2. `pick_passages(..., limit=60)` 对每本书各跑一遍（单书仍是 100），每本取三段：命中段（`line > 1` 时）
   加组覆盖或章聚类的替代段；候选章地图每本取 4 章。
3. 点名书拿一半预算（9000 字），其他书平分剩下的，每本至少 2200 字；每本最多两窗。
   非点名书没有命中分、或最高分低于 2.0，就不给窗口，由提示词点名它没有相关原文；
   点名书不受这条限制，窗口照给。配额是上限，实际受候选数与窗口合并限制。
4. 一次提示词（上限 30000 字）装下所有书：库级覆盖、每本一行书名与 md 目录、每本候选章、按书分组的窗口。
   超长时从排名最后的书开始删最后一窗，从不删一本书的唯一窗口。
5. 规则要求按问题结构写，不按书排：领句 1 到 4 句，中间 1 到 6 个小标题（可无），每节接住上一节的
   问题；同一概念的多套口径先合并再并列，数字与定义不一致时在相关小节里并排写清；
   分歧与缺口不另开段。不写成文献综述，也不写成「甲说……乙说……」的清单。
   模型只输出一个 `answer` 字符串，服务端按 `##` 拆成 `lead/sections/dissent/gap`，外层 JSON 不嵌数组。
   库级覆盖由 `para_fts` 算，跳过命中超过 200 段的常见词，最多三行，写明「不是答案证据」。
6. 回查时 citation 的 path 必须 realpath 在 `库/` 下且推出的书在本次书单内，不做跨书同名文件兜底。
   排序是书单顺序（点名书在前），每本内按离该书命中行的距离，先每本一条再第二轮，最多 10 条。
   通过 ≥ 2 条但只盖住 1 本书时 `error` 写「引文只覆盖 1 本书」，`confidence` 不降。
7. 缓存键换成 `answer-v20-multi`，含书单顺序与点名书；单书（含点名一本）用 `answer-v14`。
   形状变了，旧缓存不再命中。
   `book_grep` 的搜索范围改由 `BOOK_MDS`（JSON 数组，一本一个 md 目录）给出，没给 `path` 时每本书分一份
   `max(5, limit // 本数)` 的配额，所以一本厚书吃不掉全部限额。

失败路径：`pi` 非 0、超时、JSON 解析失败都返回 `ok:false` 与可读的 `error`，页面显示错误与「重试」，
原文照常显示。

## 会话与回答状态

会话落盘在 `缓存/sessions/<12 位小写 hex>.json`，一轮一个 turn。除 trace 与 answer 外，
每个 turn 还带回答状态字段，刷新页面或重启服务后读同一份事实：

| 字段 | 含义 |
|---|---|
| `run_token` | 这一次 `/api/run` 的标记。每次写 turn 都换新的，重跑同一问也换；旧回答任务凭它对不上号 |
| `answer_state` | `pending`（检索完，还没人答）、`running`（有任务在答）、`ready`、`error`、`interrupted`（服务重启打断） |
| `answer_error` | 失败原因。只有真实错误，没有「未知错误」这种占位 |
| `answer_started_at` / `answer_finished_at` | 开始与结束时间 |
| `answer_attempt` | 这一轮第几次回答。`finish`/`fail` 只写当前 attempt，晚到的旧任务返回 `False` |
| `answer_owner` | 正在答的进程 pid。重启恢复靠它判断任务是否真的还在跑 |

旧会话没有这些字段，`session.load` 按已有内容补：有 `answer` 就是 `ready`，`ok:false` 就是 `error`
（错误取 answer 里的 `error`）；有 trace 没 answer 记 `incomplete`；连 trace 都没有记 `run_error`。
`/api/sessions` 的每一行与 `/api/session` 的每个 turn 都带 `answer_state`、`answer_error`。
`/api/session` 的每条 citation 另带 `book` 与 `book_title`，书名按 `build/library.db` 的 `book.path` 查，
查不到才退回 uid；`quote`、`line`、`path`、`verified` 不动。

历史会话每行右上角的「…」在悬停或聚焦时出现，菜单是 下载› / 多选 / 删除。
勾选框只在「多选」里出现，退出多选就收掉；窄屏与触屏常显「…」。

同轮去重：`/api/answer` 以 `(session_id, turn, run_token)` 为键，第一个请求调模型，
同时进来的第二个请求等它的 `threading.Event`（最多 300 秒）后复用同一份结果，不再调一次模型；
超时返回 504「回答仍在生成，请稍后刷新」。重新 `/api/run` 换了 `run_token` 就是新任务。
完成的任务保留 60 秒供迟到请求复用，之后清掉；普通重复请求（`fresh` 不传）直接复用已完成的结果，
前端点「重试回答」带 `fresh:true`，服务端会开新任务、重新调模型，同一 `run_token` 也重试。
任务还在跑时 `fresh:true` 也是加入等待，不并发起第二个模型调用。不带 `session_id` 的请求仍是原来的同步路径。

重启恢复：`server.main` 建 httpd 之前调 `session.recover_interrupted()`，把仍为 `running` 的 turn 标成
`interrupted`，`answer_error` 写「服务重启，上次回答已中断」，并打印恢复轮数。
`answer_owner` 是另一个仍存活的进程时不标（可能另一个实例正在答）；owner 为空、等于当前进程或已死才标。
恢复失败只打印堆栈，不阻止启动。

刷新后的答案来自会话文件，不是答案缓存。`缓存/answers/` 的键含 `PROMPT_VERSION`，
提示词版本变过之后旧缓存不会自动命中，别把「刷新还能看到回答」当成缓存修复。

## 接口

- `GET /api/health` → 书库规模与索引构建时间。索引未建时仍返 200，带 `degraded:true` 与 `message:"未建索引"`；此时 `meta.books/paras/chapters` 为 `null`，会话页与 `/classic` 页头显示 `—` 和「索引未建／为空，先跑索引」，不把占位 0 当规模。真正的库错误才返 500。
- `POST /api/run` `{"question":"...","thinking":"high","fresh":false}` → 五阶段 trace（结构见 `BRIEF.md` 的「数据契约」）。
  `thinking` 取 `off/minimal/low/medium/high/xhigh/max`（默认 `high`），进切词缓存键；
  `fresh=true` 跳过切词缓存重新调模型，本次结果仍写回。trace 顶层与 `extract` 都带 `thinking`。
  问题里点名书名时，trace 顶层多一个 `focus`：`status` 为 `exact` / `ambiguous` / `missing`。
  `exact` 会把该书的 `ranking[0]`、`chapters[0]` 和 `result` 钉在这本书上（`pinned:true`），
  `cards.trigger` 为 `false`；`ambiguous` 触发书卡；`missing` 不拿别的书冒充，继续普通检索。
  点名书没有可用正文时 `result.path` 为空、`result.focus_empty` 为 `true`。
- `GET /api/chapter?path=库/...md&line=16&chars=900&before=700` → 从 1-based 行起截原文，命中整行保留，前置 700 字，YAML 前置块跳过。
  路径必须 realpath 在 `库/` 下且以 `.md` 结尾，否则 400。返回除 `text`/`title`/`html`/`ms` 外还有：
  `hit_line`、`hit_start`、`hit_end`、`hit_text`、`before`（请求的前置预算）、`before_chars`（实际前置字数）、
  `total_lines`、`window_chars`，且恒有 `text[hit_start:hit_end] == hit_text`。
- `POST /api/answer`
  ```json
  {"question": "...", "book": "文学/类型小说/刘慈欣/三体/死神永生",
   "path": "库/.../03_第二部....md", "line": 1180,
   "terms": ["程心","执剑人","三体"],
   "groups": [{"name": "冰封封印", "variants": ["冰封","冰壁","封印","封魂咒"]}]}
  ```
  多书回答在同一份请求上多给 `sources`（最多 8 本，每项非空字符串 `path`、整数 `line`、可选字符串 `title`，
  否则 400）。点名书时再给 `focus`（`/api/run` 返回的对象）；`sources` 第一项应是点名书，
  其余书作为补充。所有书由同一个写手一次写成，点名书在前，其他书只补它没有的角度：
  ```json
  {"question": "...", "path": "库/...md", "line": 1180, "terms": [], "groups": [],
   "thinking": "high", "fresh": false,
   "sources": [{"book": "人文社科/教育/...", "title": "...", "path": "库/...md", "line": 31}]}
  ```
  可选 `thinking` 与 `fresh`：思考和切词共用同一套档位并进答案缓存键；`fresh=true` 跳过答案缓存重新调模型。
  返回 Answer：`ok`、`cached`、`ms`、`model`、`thinking`、`answer`、`citations[{path,line,quote,chapter,verified}]`、
  `passages[{path,start,end,chars,title}]`、`confidence`、`not_found`、`error`，另有
  `mode`（`single` 或 `multi`）、`focus` 与 `sources[{book,title,path,line,citations}]`；
  `recap` 是留给下一问定位的三条要点。点名书在前，`citations` 最多 10 条。
  老客户端仍可只读扁平 `lead`/`sections`/`answer`；旧会话里的 `parts` 仍按原样渲染。
   `lead`、`sections[{heading,body}]`、`dissent`、`gap` 单书与多书都会填；`answer` 是它们拼出来的纯文本。模型只给平铺 `answer` 时这四个字段为空。
  多书时每条 citation 多带 `book` 与 `book_title`。
  `citations` 只放回查通过的引文，`line` 是文件实际行号。
  只给 `book`/`path`/`line` 的老请求行为与返回形状不变，`mode` 为 `single`、`sources` 为空。
  带 `session_id` 时返回多 `run_token` 与 `answer_state`；同一轮正在生成时第二个请求等结果（最多 300 秒），
  重试带 `fresh:true` 才会重新调模型。
- `POST /api/sessions/delete` `{"ids":["0123456789ab", ...]}` → 删除 1 到 100 个会话文件。
  返回 `{"ok":true,"deleted":[...],"missing":[...],"count":N}`，`count` 是实际删掉的个数；
  不存在的 id 进 `missing`，不算错。`ids` 不是数组、超过 100 个或含非法 id（不是 12 位小写 hex）时 400。
- `GET /api/export?id=<12 位 hex>&format=md|docx` → 下载整条会话。`md` 返 `text/markdown`，
  `docx` 返 OOXML（标准库手写，不依赖第三方包）。每轮依次给问题、范围、回答、本轮依据；
  范围只认该轮 `sources`，没有就整行不写，和页面「范围」行同口径；依据带书名、章节、`path:line` 与引文。
  文件名 `会话-<时间>-<问题前 12 字>.<ext>`，同时给 ASCII 回退名。
  id 不合法 400，会话不存在 404，`format` 不是 `md`/`docx` 也 400。

调试参数：`?q=...&instant=1` 不播动画直接出结果；`&select=2` 选第二张书卡；`&all=1` 全选所有书卡；
`&top=1` 不自动滚到结果（截图用）；`&hit=1` 滚到命中段（截图用）。

## 前端

结果阶段从上到下：

1. 回答卡：加载时显示「正在检索原文并组织回答」+ 脉冲点；全选时换成「正在检索 N 本书的原文…」。
   失败显示错误与「重试」；成功时按 `lead` 与 `sections` 渲染，小节标题与正文；
   旧答案若有 `dissent`/`gap`，仍各显示一行小字。没有 `sections` 时按 `answer` 换行分段。
   另给一行小字（置信度、回查条数、缓存、耗时、模型）。
   `not_found` 时另标「未找到原文依据」。
2. 依据列表：最多 10 条，每条一行章名与行号 + 引文（两行截断），多书时上方还有一行书名；
   整条可点，点了用 `/api/chapter` 载入那段并刷新原文窗口；回答卡保留。小字报「依据 N 条，覆盖 M 本书」。
3. 原文区：命中段用 `<span class="hit-para">`（`display:block`）浅朱红底 + 左侧竖线包成块，
   块内检索词仍是 `<mark>`，不嵌套；上方是 700 字前置，下方是后置。脚注写
   `本章 2220 行 · 载入 1603 字 · 命中第 1180 行`。

第 4 阶段书卡触发且超过一张时，触发行右侧有一个「全选 N 本」小按钮。按下后每张卡都显示已选，
下方补一行小字说明第 5 阶段会用这 N 本书一起回答；第 5 阶段的加载文案变成「正在检索 N 本书的原文并
组织回答，最长约 2 分钟」，回答卡小字报「N 本书」与「覆盖 M 本」。再点一下取消全选，或者直接点某张卡，
就回到单书行为。`&all=1` 可复现，换问题、重播、返回首屏都会清掉全选。

首屏「模型思考」四档（关/低/中/高）与第 5 阶段顶部「思考」下拉是同一份设置：存 `localStorage`，
`?thinking=low` 可复现；第 01 阶段摘要与回答卡小字都报当前档位。顶部「重新检索」与回答卡右上角
「重新检索」是同一个动作：用当前档位重跑 `/api/run`（`fresh=true`），第 5 阶段再用 `fresh=true`
生成回答，切词与答案两处缓存都跳过；换档本身也换缓存键，所以同一问题在新档第一次跑是冷跑。

回答请求在结果阶段变为 active 时发起（`instant=1` 立即发起），带 `AbortController` 与序号；
换卡、换书、取消全选、返回都取消旧请求，缓存命中直接渲染。

## 只读与边界

- 只读 `目录/检索/index.db`、`目录/library.db`、`库/**.md`。
- 只写 `缓存/terms/`、`缓存/answers/` 与 `缓存/sessions/`，按 sha1 或会话 id 分文件，原子替换。
- 不跑 B4、不写 expand、不改 `库/` 与 `工作区/脚本/`。
- `/api/run` 有一把锁；`/api/answer` 不加整段锁，长回答不挡换卡与下一次检索；同一轮只在第一个请求里调模型。

## 验收

```bash
python3 tests/manual/demo/check_demo.py
```

自检健康检查、两个示例（一个弹卡、一个不弹卡）、章节截取、路径穿越拒绝、空问题拒绝，另加下面几条：

- 三体题 `/api/answer` 的 `answer` 同时含「威慑度」「百分之十」，至少一条 `verified` 引文行号是 1166 或 1172；
- `/api/chapter?before=700` 的前置含「你的威慑度在百分之十上下波动」，`hit_text` 含「这支舰队应该是五年前派出的」，
  且 `text[hit_start:hit_end] == hit_text`；
- 凡人题 answer 含「长老」或「阴罗宗」，verified 引文落到 0783 与 0790/0791；
- 库里没有的问题 `not_found=true` 或 `answer` 含「未找到原文依据」；
- 全选：拿「内卷和教育」那题的书卡拼出 `sources` 再问一次，`mode=multi`、`answer` 非空、
  `sections` 至少 2 节、`lead` 非空、以「《」开头的行不超过 2 行、正文 `《书名》` 不超过 2 处、
  `verified` 引文至少 4 条且盖住 3 本书以上、
  全部落在书单内，冷跑 150 秒内，第二跑缓存小于 1.5 秒。

末行 `ALL PASS`。截图与交互检查用 CDP（只依赖标准库）：

```bash
python3 tests/manual/demo/shot.py --width 1440 --height 900 --scroll stage4 --out 验收/回答-1440x900.png
python3 tests/manual/demo/shot.py --width 1440 --height 900 --scroll hit-center --out 验收/命中块-1440x900.png
python3 tests/manual/demo/shot.py --width 390 --height 844 --scroll stage4 --out 验收/手机-回答-390x844.png
python3 tests/manual/demo/shot.py --width 390 --height 844 --scroll hit-center --out 验收/手机-命中块-390x844.png
python3 tests/manual/demo/shot.py --width 1440 --height 900 --click-cite   # 另查点依据、横向溢出、控制台报错
python3 tests/manual/demo/shot.py --width 1440 --height 900 --click-all --question "内卷和教育之间是什么关系"
python3 tests/manual/demo/shot.py --width 1440 --height 900 --click-all --question "内卷和教育之间是什么关系" --scroll stage3 --out 验收/全选-书卡-1440x900.png
python3 tests/manual/demo/shot.py --width 390 --height 844 --click-all --question "内卷和教育之间是什么关系" --scroll stage4 --out 验收/全选-手机-390x844.png
```

`shot.py` 末行打印 `CONSOLE ERROR n`，并报 `overflow=`（横向溢出）与点依据前后的 `path:line`。
`--click-all` 先等单书回答就绪，再点第 4 阶段的「全选」，等新的多书回答出来（上限 120 秒），
打一行 `select-all: button=… selected=… multi=… books=…` 与 `SELECT ALL PASS|FAIL`；
没有全选按钮、回答没有变成多书、或引文盖不到 2 本书都算硬失败，进程退码 1。

`验收/` 里的图：

- `回答-1440x900.png`、`命中块-1440x900.png`
- `手机-回答-390x844.png`、`手机-命中块-390x844.png`
- `回答-加载中-1440x900.png`、`回答-乡土-1440x900.png`、`回答-凡人-1440x900.png`
- `首屏-思考-1440x900.png`、`问题栏-思考-1440x900.png`、`回答-思考-1440x900.png`
- `手机-首屏-思考-390x844.png`、`手机-问题栏-思考-390x844.png`、`手机-回答-思考-390x844.png`
- `全选-书卡-1440x900.png`、`全选-回答-1440x900.png`、`全选-手机-390x844.png`
- 五阶段的旧图（首屏、桌面结果、播放中、加载中、手机全页）保留作对照；旧结果图不含回答卡。

## 已知限制

- 回答依赖本机 `pi` 与 `opencode-go` 凭据；不可用时回答卡报错、可重试，检索与原文不受影响。
- 延迟随提供方波动（实测 11 到 49 秒）：`--thinking low` 保留低档思考，
  5 窗提示词、30 章地图和 agent 的工具轮数都会拉长时间。
- 模型偶尔改写引文，回查不过会被丢掉，可能只剩 1 条并标「引文回查不足」；`confidence` 也随之降到 `low`。
- 回答默认给 5 个窗口（≤ 18000 字）和 30 章候选地图；多书时点名书拿一半预算（9000 字），其他书平分剩下的，
  每本至少 2200 字、最多两窗；没有命中分或最高分低于 2.0 的非点名书不给窗口。一次提示词答完，不是每本各答一遍再拼。agent 可用只读 `book_grep`/`read` 继续搜，
  全选时 `book_grep` 同时搜所有在范围内的书。不做追问。
- 左栏流程轨在长页里没有真正吸顶（`html, body { overflow-x: hidden }` 让 sticky 失效），滚动截图里看不到它。
