# 会话历史 + V3 阶段区 · 实施记录

> **布局说明（2026-11-02）**：`<uid>` 是书目录占位，例如 `B0024`。v2 下书目录是 `books/<uid>/`，索引是 `build/检索/index.db`，书目库是 `build/library.db`，人账在 `tables/`。本文示例已按 v2 改写。


单子：`REAL-IMPL-BRIEF.md`。参考原型：`原型-会话历史/`（只读，未改）。
状态：**已落地，UI 验收全过**（56 条，0 败）；后端验收 `check_session.py` 全过（29 条，真调模型 3 轮）。

## 1. 改了哪些文件

| 文件 | 做了什么 |
|---|---|
| `session.py` | 新增 `_book_names(turn)` 与 `summaries(limit=None)`。只读 `缓存/sessions/*.json`，跳过非法 id、坏文件、无 turn 的会话；按 `updated_at` 倒序；`limit` 1..N 时截断。现有读写与 `build_context` 一行未动 |
| `server.py` | `do_GET` 加一条 `elif route == "/api/sessions"`；新增 `api_sessions(query)`（`limit` 解析 + `session.summaries()`），回 `{ok, count, sessions}`。只加了 8 行方法体，没加锁、没动 `/api/session` |
| `web/session.html` | 左栏换成 `rail-tabs`（本次会话 / 历史会话）+ `rail-panels`（两个 `role=tabpanel`），历史面板含搜索框、列表、底部说明。删掉 `rail-head` |
| `web/session.css` | 新增：rail 页签、`rail-panel/rail-sub/rail-scroll/rail-empty`、整套 `hist-*`、V3 阶段面板（`stages/strip/step/stage-row/stage-*/v3-*/raw`）、初始页（`start-*/last-card`）；`[hidden]{display:none!important}`；`.answer h2,h3` 改 `1em`。删掉旧的 `.cells/.cell*`、`rail-head`、`.empty-samples` 与 `.step:last-child .dot{red}` 写死红点 |
| `web/session.js` | `state` 加 `rail/history/historyQuery/historyAll/historyError`；`turn` 加 `stageStates/stageOpen/stripOpen`；新增 `fmtMs`、`stageDetails`、`stagesHTML`、`replayStages`、`sleep`、`renderRail`、`renderHistory`、`loadSessions`、`openSession` 改造、初始页 `emptyHTML` 重写；`runTurn/answerTurn` 接阶段动画；点击派发加页签、历史行、`.strip-bar`、`.stage-row-head`、`更早的/收起`；`renderMain` 存还 `scrollTop` |
| `验收/check_session.py` | 落盘检查之后加「会话列表接口」「会话摘要字段」两条断言 |
| `验收/check_session_ui.py` | SNAP 去掉 `.cells`，加 17 个字段（stageRows/Values/Texts/Open/Active/Done、stripOpen、historyRows/Count、tabHistoryOn/tabCurrentOn、h2Size/pSize、starts/lastCards、histScroll/histWrap、histFirstGroup/histFirstCurrent/panelHistoryHidden）。旧断言改写 + 新增 11 条（见 §5） |
| `REAL-IMPL-REPORT.md` | 本文件 |

没动：`books/<uid>/`、`tables/`、`answer.py`、`related.py`、`trace.py`、`web/app.js`、`web/index.html`、`web/style.css`、`原型-会话历史/`。没有 `git add`/`commit`/任何改状态的 git 命令。

## 2. V3 字段映射

每行头：`01` + 阶段名 + `value` + `fmtMs(stage_ms[key])` + 展开箭头。
`fmtMs`：`<10` 一位小数 ms，`<1000` 取整 ms，否则 `x.x s`；无值 `—`。阶段耗时不再用 `fmtSec`。

| 行 | value | lead | meta | field（标签 + 内容） |
|---|---|---|---|---|
| 01 理解问题 | `N 组词` | `N 组词 · 词1 · 词2 · 词3` | `N 个检索词 · 命中切词缓存` | 无（lead 已列词） |
| 02 词法循环 | `N 轮` | `N 轮 · <STOP 文案>` | `取第 k 轮`（无 best_round 为空） | `轮次` → 每轮 chips：`名词/名词+概念/去泛词 · 302 段[ · 采用]` |
| 03 打分与选章 | `评 X 本 · 选 Y 章` | `top1 书名 · 34.97 分 · 取 Y 本书的章`（无 ranking 时「没有命中」） | `书级打分 best_support · 分数相对第一名` | `排序` → 前三名 `1. 乡土中国（2） 34.97` |
| 04 书卡 | `N 张` / `未触发` | `N 张书卡 · 触发` / `未触发 · 直接取 top1` | `不确定时，把候选递给你` / `书级第一名够稳，不弹卡` | `候选` → `《书名》` chips |
| 05 结果 | `N 本 · 跨书` 或 `《书》· 章` | `《书》· 章 · N 条引文` | `读窗口 0.3 ms · 回答 28.0 s`（回答未落地时 `· 回答中`） | `命中` → `「result.text 截 80 字」`（退 `cites[0].quote`） |

`raw`：01 `trace.extract`；02 `{terms,nouns,rounds,stop,best_round,ms}`；03 `{scheme,ranking,chapters}`；04 `trace.cards`；05 `{result,answer}`；JSON 截 4096 字。没有 trace 时五行 `value=—`、`lead/meta` 空、`field/raw` 无。

首屏无 trace 的检索期：第 1 行 `is-loading`，显示「正在理解这句话…」，`stage-body-done` 与 `raw` 隐藏（实测 `loadingVisible=flex, doneVisible=none, rawCount=0`）。

## 3. 阶段动画时序

`REPLAY_MS = 900`。

1. `ask()` 建 turn：`stageStates=[active,pending×4]`、`stageOpen={0}`、`stripOpen=true`，无 trace → 第 1 行 loading。
2. `/api/run` 返回：填 `trace/related/sources/session_id`，`render()`，异步 `loadSessions()`。
3. `replayStages()`：i=0..3，每步 `stageStates` 前 i 行 done、第 i 行 active，`stageOpen={i}`，`render()`，`sleep(900)` → **3.6 s**。
4. 收尾：`[done,done,done,done,active]`、`stageOpen={4}`、`stripOpen=true` → 进入 `answerTurn`。
5. `answerTurn` 开始：`status=answering`，同样 5 行状态，第 5 行显示 `v3-waiting`「正在读原文、组织回答…」，横条第 5 点红点 + `ring` 扩散环。
6. `/api/answer` 落地（成功、失败、无来源都走）：`stageStates` 全 done、`stageOpen` 清空、`stripOpen=false` → **五个实心墨点、零 active、面板收起**。
7. `run-error`：全 pending、`stageOpen` 空、`stripOpen=false`。
8. `state.epoch` 在 `newSession` / `retryRun` / `openSession` 前 +1，`replayStages` 每步与 `answerTurn` 前后都验，旧动画与新回答不会互相打架。

实测（假 fetch，`/api/answer` 延迟 4 s）：

```
t1.2  step = [done, active, pending, pending, pending], stages.is-open=true, 打开行 1
t5.0  step = [done, done, done, done, active], stages.is-open=true, 打开行 1, v3-waiting=true, is-loading 行 0
t10.0 step = [done×5], stages.is-open=false, 打开行 0, .answer 已出
```

## 4. 历史 API 与置顶

`GET /api/sessions[?limit=N]` → `{ok, count, sessions:[{id, updated_at, turns, question, last_question, books}]}`。

- `updated_at` = `sess.updated_at`，缺失退最后一轮 `at`，再缺空串。
- `books` 最多 3 个书名（不含路径）：`trace.plan.ranking[].book` 取最后一段；空时退 `answer.citations[].book|path`（`/md/` 前最后一段）；再空退 `trace.result.book_title`。
- 排序 `(updated_at, id)` 倒序；跳过非法 id、坏 JSON、`turns` 为空。
- `limit` 只认 1..500，其余与缺失一律全给（`limit=abc`、`limit=501` 都是 59 条）。
- 不加缓存：59 个文件每次全读，实测一次调用无感。

左栏：`#history-count` = `history.length + (当前会话不在列表 ? 1 : 0)`；当前会话摘要优先用页面上的 live turn（第一问、最后一问、轮数、`last.at`、当前 sources/citations 书名），无 turn 时用 `history` 里同 id 行；它固定排在 `当前打开` 组，同 id 从 今天/昨天/更早 里排除。日期分组 / 时间文案按 `updated_at` 的 `+0800` 字面值解析，不做时区换算；`今天 HH:MM`、`昨天 HH:MM`、`MM-DD HH:MM`。分页 `HISTORY_PAGE = 10`（含置顶行），有搜索词时不分页、全列匹配。

实测：初始页 10 行 + 「更早的 49 个 ↓」；点开 59 行 + 「收起」；搜索「三体」10 行无分页；搜不到显示「没有匹配的会话。」；打开会话后历史首组是「当前打开」且该行 `.is-current`。

## 5. 验收

```bash
cd ~/文档/books/工作区/演示-检索流程
python3 -m py_compile server.py session.py 验收/check_session.py 验收/check_session_ui.py   # ok
node --check web/session.js                                                                  # ok
nohup python3 server.py --port 8798 > /tmp/session-8798.log 2>&1 &                           # 起在 8798
python3 验收/check_session_ui.py --base http://127.0.0.1:8798
#   56 过 / 0 败 / ALL PASS（exit 0）
python3 验收/check_session.py http://127.0.0.1:8798
#   29 过 / ALL PASS（真调模型：3 轮 run + 3 次 answer + related 判定）
```

新增/改写的 UI 断言（都在 8798 上跑过）：

- 五阶段可展开 → 点 `.strip-bar` 后 `.stage-row` 5 条且 `stripOpen`
- V3 阶段面板五行展开 → 五个 `v3-val` 都非空非 `—`（`5 组词 / 3 轮 / 评 8 本 · 选 3 章 / 6 张 / 2 本 · 跨书`）
- 五阶段第 5 格是跨书 → `stageTexts[4]` 含 `2 本 · 跨书`
- 阶段行可点开 → 点第 3 行头后 `stageOpen=1`
- 阶段结束后静止 → FAKE 追问落地后 `stageActive=0 && stageDone=5 && !stripOpen && stageOpen=0`
- 历史页签有列表 → 10 行、`#history-count` = 59
- 历史里当前会话置顶 → 首组「当前打开」、首行 `.is-current`、`#panel-history` 可见
- 初始页默认历史 → `tabHistoryOn=true`、`tabCurrentOn=false`、`.start` 1 个、`.last-card` 1 个
- 接着上次能打开会话 → 点 `.last-card` 后 `.round` > 0 且 `tabCurrentOn`
- 回答标题与正文同字号 → 1440 `h2=20px p=20px`；390 `h2=14px p=14px`
- 390 初始页历史可滚 → 无横向溢出、`#hist-list` `overflow-x:auto` + `flex-wrap:nowrap`

后端断言（`check_session.py` 里新加的两条，实测输出）：

```
[过] 会话列表接口 · rows=58
[过] 会话摘要字段 · {'id': '32d0aa08c03f', 'updated_at': '2026-10-28T11:22:21+0800', 'turns': 3,
                    'question': '乡土中国 差序格局', 'last_question': '《三体》里为什么程心…', 'books': [...3 个书名]}
```

curl 直查（8798）：

```
GET /api/sessions?limit=2   -> ok=true, count=2, 首条 turns=1、次条 turns=3 且 books 三个书名
GET /api/sessions           -> count=59，含 32d0aa08c03f 的六字段摘要
GET /api/sessions?limit=abc -> count=59（非法当没给）
GET /api/sessions?limit=501 -> count=59（超范围当没给）
```

## 6. 截图（`验收/`，*.png 已在 .gitignore）

- `会话-V3-阶段-1440x900.png` — 打开阶段面板并展开第 3 行（排序 chips + 原始数据）
- `会话-历史-1440x900.png` — 初始页：历史页签 + 今天/昨天分组 + 接着上次卡
- `会话-历史-390x844.png` — 390 初始页：历史横向卡片
- `会话-V3-回答中-1440x900.png`（额外）— 回答生成期间：前四点墨、第 5 点红 + ring、`v3-waiting`
- 旧截图按原路径重生成：`会话-1440x900.png`、`会话-390x844.png`、`会话-multi-1440x900.png`、`会话-multi-390x844.png`

## 7. 没做的、有疑问的、要父级定的

1. **8765 上的旧进程需要重启**（我没动它）。它加载的还是改前的 `server.py`，没有 `/api/sessions` 路由，但静态文件是每次现读的——现在 8765 发出去的已经是新前端：`GET 8765/api/sessions` 回 `404 {"ok":false,"error":"unknown api"}`，历史面板会显示「历史会话加载失败：请求失败 404」，首屏也不会有「接着上次」。重启 8765 进程即恢复。8799 的原型静态服务未受影响。
2. **01 行 meta 的「命中切词缓存」是按单子写死的**。`trace.extract.source` 为 `model` 时（如 7f0f8d8ba4c4 第 3 问，切词花了 11.1 s）这句不成立。要不要按 `source` 分成「命中切词缓存 / 模型切词」，等你定，一行就能改。
3. **历史行显示的是最后一问**（`last_question`）。单子只写「问题（两行截断）」，我按「接着上次」的语义取了 last，搜索两者都匹配。若希望显示第一问，改 `histRowHTML` 一处即可。
4. `plan.rounds[].kind` 目前只见 `noun / noun+concept / drop-generic`，映射表外的新 kind 原样显示，不报错。
5. 收起状态下 `.stage-row` 仍在 DOM 里（`display:none`），所以 UI 检查的 `stageRows` 恒为 5；真正的「面板收没收起」用 `stripOpen` / `stageOpen` 断言，没有靠行数。
6. 未跑 `验收/check_session_live.py`（它按 8765 写死、且要多次真调模型，不在本单验收范围）；`check_demo.py` 与本单无关，未跑。
7. `git status` 里另有不是本单造成的改动：`D 工作区/演示-检索流程/README.md`、`M 目录/分类表.csv`、`M 工作区/批次/ingest/journal.tsv`。我一行没碰，留给你判断。
8. 8798 上的自测服务已按单子杀掉；8765、8799 未动。

## 8. 父级复核（2026-10-28）

子代理报告之后又收了四处，均已复跑通过：

- 历史行与「接着上次」标题改回第一问，和原型、会话标题一致；最后一问仍参与搜索。
- 01 行 meta 按 `trace.extract.source` 显示：`model` → 模型切词，`cache` → 命中切词缓存，其余 → 切词。
- 无来源的 `answer-error` 路径补 `stopClock()`，避免计时器泄漏。
- 初始页隐藏「接着问」建议条；桌面右栏补「原文」空态；窄屏空 drawer 不占位；
  窄屏 `.app` 补 `min-height` 与 `grid-template-rows`，首屏 composer 落到底部。

复跑：

```bash
python3 -m py_compile server.py session.py 验收/check_session.py 验收/check_session_ui.py   # ok
node --check web/session.js                                                                  # ok
python3 验收/check_session_ui.py --base http://127.0.0.1:8765
#   ALL PASS，控制台 0 错误，1440/390 无横向溢出
curl http://127.0.0.1:8765/api/sessions?limit=2                                              # ok
```

8765 已用新 `server.py` 重启，`/` 与 `/classic` 都 200；8798 自测服务已杀；8799 原型静态服务保留。
未重跑真调模型的 `验收/check_session.py`，子代理的 29/29 记录保留，后端未再改。
