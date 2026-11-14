# 会话历史 + V3 阶段区 · 真实实现单


状态：historical · 2026-10-28
> **布局说明（2026-11-02）**：`<uid>` 是书目录占位，例如 `B0024`。v2 下书目录是 `books/<uid>/`，索引是 `build/检索/index.db`，书目库是 `build/library.db`，人账在 `tables/`。本文示例已按 v2 改写。


把 `原型-会话历史` 里已批准的 V3 方案接进生产会话页。前后端都做。
原型目录只读参考，不改。生产文件在 `apps/demo/` 下。

## 只改这些

- `web/session.html`：左栏加「本次会话 / 历史会话」两个页签与两个面板；初始页加「接着上次」卡。
- `web/session.css`：页签、历史列表、V3 阶段面板、回答标题字号。其他样式尽量沿用。
- `web/session.js`：历史会话状态、历史 API、打开会话、初始页历史默认、V3 阶段面板、阶段进度动画。
- `server.py`：新增 `GET /api/sessions`。只加路由与一个方法。
- `session.py`：新增 `summaries(limit=None)`，从 `缓存/sessions/*.json` 生成历史摘要。
- `验收/check_session.py`：加 `/api/sessions` 的断言。
- `验收/check_session_ui.py`：把旧的 `.cells` 断言换成 V3 阶段行，加历史页签、初始页、阶段结束静止的断言。
- 新增 `REAL-IMPL-REPORT.md`（实施记录，见最后一节）。

不改 `books/<uid>/`、`tables/`、`answer.py`、`related.py`、`trace.py`、`web/app.js`、`web/index.html`、`web/style.css`。
不动 8765 上跑的旧服务，自测用 `--port 8798`。不动 8799 上的原型静态服务。

## 已定设计

- 横条保持现状：一行五步，7px 圆点。未开始空心，进行中红点 + `ring` 扩散环，完成实心墨点。
  **不要**再给 `last-child` 写死红色。结果阶段在回答生成期间保持 active，回答落地后变 done。
- 横条下面是 V3 单卡手风琴：一个外框，五行发丝线。收起行 `01 理解问题 5 组词 0.2 ms ⌄`，
  展开体软底，带标签。参考 `原型-会话历史/proto.css` 的 V3 段与 `proto.js` 的 `v3Body`。
- 回答落地后自动收起：`stripOpen=false`，`stageOpen` 清空，横条只留五个实心点。
- 初始页左栏默认历史页签。主区是首屏 + 「接着上次」一张卡。当前会话在历史里置顶。
- 回答正文里的 `h2/h3` 字号改成 `1em`，跟正文同字号；颜色、字重、上边线不动。

## 后端

### `session.py`

加两个函数，不改现有读写与上下文逻辑。

```python
def _book_names(turn):
    """上一轮涉及的书名，最多 3 个，去重。"""
    # trace.plan.ranking[].book -> 取最后一段
    # 退路：answer.citations[].book / path -> /md/ 前最后一段
    # 再退路：trace.result.book_title


def summaries(limit=None):
    """历史会话摘要，按 updated_at 倒序。只读。
    返回 [{id, updated_at, turns, question, last_question, books}]
    """
```

- 跳过没有 turn 的文件、坏文件、非法 id。
- `updated_at` 用 `sess.get("updated_at")`，没有就用最后一轮的 `at`，再没有就用空串。
- `question` 是第一问，`last_question` 是最后一问，`turns` 是轮数。
- `books` 最多 3 个，书名，不含路径。
- `limit` 为正整数时截断；`limit=None` 全给。57 个文件，别加缓存。

### `server.py`

`do_GET` 加在 `/api/session` 之前或之后均可，精确匹配：

```python
elif route == "/api/sessions":
    self.api_sessions(parse_qs(parsed.query))
```

```python
def api_sessions(self, query):
    try:
        limit = int((query.get("limit") or ["0"])[0])
    except ValueError:
        limit = 0
    rows = session.summaries(limit if 1 <= limit <= 500 else None)
    self.send_json(200, {"ok": True, "count": len(rows), "sessions": rows})
```

不加锁。`session.load` 是原子读，`save` 用 `os.replace`，列表读到的要么旧要么新。

## 前端数据形状

### 会话对象（现有）

`state = {sessionId, turns, viewN, selected, shown, drawer, stats, busy, elapsed, timer, error, epoch, stripOpen}`。
本轮加：

```js
state.rail = 'history' | 'current';   // 左栏当前页签
state.history = [];                   // /api/sessions 的摘要
state.historyQuery = '';
state.historyAll = false;             // 是否展开「更早的」
state.historyError = '';
```

`turn` 加三个客户端字段，不落盘：

```js
turn.stageStates = ['pending'|'active'|'done' ... 5 个];
turn.stageOpen = new Set();           // 展开的行号
turn.stripOpen = false;               // 整块阶段面板是否展开
```

`turnFromSaved` 一律设 `['done','done','done','done','done']`、空 Set、false。
新建 turn 设 `['active','pending','pending','pending','pending']`、`new Set([0])`、true。

### 阶段详情 `stageDetails(turn)`

返回 5 行，字段 `{value, lead, meta, field, raw}`：

1. 理解问题
   - value：`${terms.length} 组词`
   - lead：`${terms.length} 组词 · ${terms.slice(0,3).join(' · ')}`
   - meta：`${terms.length} 个检索词 · 命中切词缓存`
   - field：无（lead 已经列了词）
   - raw：`trace.extract`
2. 词法循环
   - value：`${rounds.length} 轮`
   - lead：`${rounds.length} 轮 · ${STOP[plan.stop] || '—'}`
   - meta：`plan.best_round ? '取第 ' + plan.best_round + ' 轮' : ''`
   - field：label `轮次`，chips 每轮 `${kindLabel} · ${fmtInt(hits)} 段`，
     `n === best_round` 的追加 ` · 采用`
   - raw：`{terms, nouns, rounds, stop, best_round, ms}`
3. 打分与选章
   - value：`评 ${bookCount} 本 · 选 ${chapters.length} 章`
   - lead：`top1 ${topBookName} · ${score.toFixed(2)} 分 · 取 ${chapters.length} 本书的章`
     （没有 top1 时 `没有命中`）
   - meta：`书级打分 ${plan.scheme || 'best_support'} · 分数相对第一名`
   - field：label `排序`，chips 前 3 名 `${i+1}. ${bookName} ${score.toFixed(2)}`
   - raw：`{scheme, ranking, chapters}`
4. 书卡
   - value：`cards.trigger ? ${cardList.length} 张 : '未触发'`
   - lead：`cards.trigger ? ${cardList.length} 张书卡 · 触发 : '未触发 · 直接取 top1'`
   - meta：`cards.trigger ? '不确定时，把候选递给你' : '书级第一名够稳，不弹卡'`
   - field：label `候选`，chips 书卡标题
   - raw：`trace.cards`
5. 结果
   - `scope = scopeSources(turn)`
   - value：`scope.length > 1 ? ${scope.length} 本 · 跨书 : 《${resultBook}》· ${resultChapter}`
   - lead：`《${resultBook}》· ${resultChapter} · ${cites.length} 条引文`
   - meta：`读窗口 ${fmtMs(trace.stage_ms.result)}` +
     `turn.answer?.ms ? ' · 回答 ' + fmtMs(ms) : ' · 回答中'`
   - field：label `命中`，quote 用 `result.text` 截 80 字，退 `cites[0].quote`
   - raw：`{result, answer: turn.answer || null}`，JSON 截 4096 字

没有 trace 时给占位：value `—`、lead 空、meta 空、field 空、raw null。
ms 用 `fmtMs`（新加，`<10` 一位小数，`<1000` 取整，否则秒），不要再用 `fmtSec` 显示阶段耗时。

### V3 面板结构

```html
<section class="stages is-open">
  <div class="strip">
    <button class="strip-bar" aria-expanded="true">
      <span class="steps">五个 .step</span>
      <span class="strip-toggle">展开/收起 ⌄</span>
    </button>
  </div>
  <div class="stage-stack v3">
    <article class="stage-row v3-row is-done" data-i="0">
      <button class="stage-row-head v3-head" aria-expanded="true">
        <span class="v3-num mono">01</span>
        <span class="v3-name stage-name">理解问题</span>
        <span class="v3-val">5 组词</span>
        <span class="v3-ms mono">0.2 ms</span>
        <span class="scaret">⌃</span>
      </button>
      <div class="stage-row-body v3-body">
        <div class="stage-body-done">
          <p class="v3-lead">5 组词 · 礼治 · 礼治秩序 · 差序格局</p>
          <p class="v3-meta">5 个检索词 · 命中切词缓存</p>
          <!-- 词法循环/打分/书卡/结果才有 v3-field -->
          <div class="v3-field"><span class="v3-k">命中</span><span class="v3-v">…</span></div>
          <p class="v3-waiting"><i class="blink-dot"></i>正在读原文、组织回答…</p>
        </div>
        <p class="stage-loading"><i class="blink-dot"></i>正在理解这句话…</p>
        <details class="raw"><summary>原始数据</summary><pre>…</pre></details>
      </div>
    </article>
    <!-- 02–05 -->
  </div>
</section>
```

CSS 从原型 V3 段照搬，类名保持 `v3-*` / `stage-*`。`v3-waiting` 只在
`turn.status === 'answering'` 且第 5 行 active 时显示；其他行不显示。
`stage-loading` 只在没有 trace 的第 1 行显示（此时 `is-loading` 隐藏 `stage-body-done`）。
`raw` 在 `is-loading` 时也隐藏，和原型一致。

点击行为：
- 点 `.strip-bar` 切 `turn.stripOpen`，调 `renderMain()`。
- 点 `.stage-row-head` 切 `turn.stageOpen` 里的行号，调 `renderMain()`。
- pending 行 `disabled`，不响应。
- 自动动画期间每次切 active 时把 `stageOpen` 设成只有当前行；回答落地后清空。

## 阶段动画

常量 `REPLAY_MS = 900`。函数：

```js
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function replayStages(turn) {
  const epoch = state.epoch;
  for (let i = 0; i < 4; i += 1) {
    if (epoch !== state.epoch) return false;
    turn.stageStates = [0,1,2,3,4].map((k) => k < i ? 'done' : k === i ? 'active' : 'pending');
    turn.stageOpen = new Set([i]);
    turn.stripOpen = true;
    render();
    await sleep(REPLAY_MS);
  }
  if (epoch !== state.epoch) return false;
  turn.stageStates = ['done','done','done','done','active'];
  turn.stageOpen = new Set([4]);
  turn.stripOpen = true;
  render();
  return true;
}
```

`runTurn` 顺序：

1. `turn.status='retrieving'`，`stageStates=['active',...]`，`stageOpen={0}`，`stripOpen=true`，
   `startClock()`，`render()`。此时没有 trace，第一行显示 loading。
2. `POST /api/run`。拿到 trace 后填 `turn.trace`、`related`、`sources`、`session_id`，
   `render()`，然后 `if (!(await replayStages(turn))) return;`，再 `await answerTurn(turn)`。
3. 出错：`status='run-error'`，`stageStates` 全 pending，`stageOpen` 空，`stripOpen=false`，
   `render()`。

`answerTurn`：

- 开始：`turn.status='answering'`，确保 `stageStates=['done','done','done','done','active']`、
  `stageOpen={4}`、`stripOpen=true`；`startClock()`；`render()`。重试/换书重答也走这里。
- 成功/失败：`status` 定稿后，`stageStates` 全 done，`stageOpen` 清空，`stripOpen=false`，
  `render()`。第 5 点的红点在回答期间闪，落地立刻变黑。
- 缓存秒回也照此，最多闪一下。

`newSession` / `retryRun` / 切历史会话前要 `state.epoch += 1`，让旧 replay 与 answer 回滚。
`replayStages` 里每步检查 epoch。`answerTurn` 已有 epoch 检查，保留。

`renderMain` 里先存 `mainEl.scrollTop`，重建后恢复，避免阶段切换把读者位置顶走。

## 左栏历史

### HTML

```html
<aside class="rail">
  <div class="rail-tabs" role="tablist">
    <button id="tab-current" role="tab" aria-selected="true">本次会话 <b id="turn-count">0</b></button>
    <button id="tab-history" role="tab" aria-selected="false">历史会话 <b id="history-count">0</b></button>
  </div>
  <div class="rail-panels">
    <section id="panel-current" role="tabpanel">
      <p class="rail-sub" id="rail-sub">0 问</p>
      <div class="rail-scroll">
        <div class="rail-empty" id="rail-empty">还没有开始。…</div>
        <ol class="rounds" id="rounds"></ol>
      </div>
      <button class="rail-foot" id="new-session">+ 新会话</button>
    </section>
    <section id="panel-history" role="tabpanel" hidden>
      <div class="hist-search"><span class="mag">⌕</span><input id="hist-filter" placeholder="搜问题或书名"></div>
      <div class="rail-scroll hist-scroll" id="hist-list"></div>
      <p class="hist-note">打开历史会话会替换当前会话；当前会话已自动保存。</p>
    </section>
  </div>
</aside>
```

CSS 从原型 rail 段照搬，适配生产 `.rail` 的 18px 内边距与现有 `.round` 样式。
1320 以上 `.rail { overflow: hidden }`，面板内部滚动。1320 以下保留现有横向 `.rounds`；
历史列表改成横向卡片，隐藏搜索、分组、底部说明。

### 状态与渲染

- `state.rail` 默认 `'history'`。`boot` 无 `session`/`q` 时保持 history；有 `session` 打开后
  `'current'`；`ask` 时 `'current'`；`newSession` 设 `'history'`。
- `renderRail()`：页签 class/aria、两个面板 hidden、`#turn-count`、`#history-count`、
  `#rail-sub`、`#rail-empty`、`#rounds`、`#hist-list`。`render()` 调它。
- `#turn-count` = turn 数。`#history-count` = `state.history.length + (当前会话不在 history 里 ? 1 : 0)`。
- `#rail-sub`：0 问 `还没有开始`；否则 `${n} 问 · 正在看第 ${viewN} 问`。
- `#rail-empty`：`state.turns.length===0` 时显示，否则 hidden。
- 当前会话摘要：`state.turns.length` 时用 live 数据（第一问、最后一问、轮数、`last.at`、
  当前 sources/citations 的书名）。否则用 `state.history` 里同 id 的摘要。置顶在「当前打开」。
- 日期分组：`今天 / 昨天 / 更早`。时间戳 `updated_at` 解析成 `YYYY-MM-DD` 与 `HH:MM`
  （`+0800` 格式，按本机时区读，不做时区换算）。今天显示 `HH:MM`，昨天 `昨天 HH:MM`，
  更早 `MM-DD HH:MM`。
- 当前会话 id 从日期分组里排除，避免重复。
- 每行：时间、`N 问`、问题（两行截断）、`《书名》`，当前行加红色竖线与「当前」。
- 搜索：`input` 事件只更新 `state.historyQuery` 与 `renderHistory()`，不整页重渲。
  匹配第一问、最后一问、书名。无结果显示「没有匹配的会话」。搜索时不分页，全列匹配。
- 分页：`HISTORY_PAGE = 10`（含置顶行）。`historyAll=false` 时显示最近 10 条与
  `更早的 N 个 ↓`；点开全列，末尾给 `收起`。有搜索词时不显示分页。
- 点击历史行：
  - 当前会话：`state.rail='current'`，`viewN=last.n`，render。
  - 其他：`state.busy` 时 toast「这一问还在跑，落地后再切」，不切。
    否则 `openSession(id)`。
- `openSession(id)` 复用现有逻辑，补：`state.rail='current'`、`state.historyAll=false`、
  `state.epoch += 1`（取消旧动画）、`setUrl`、`render`、`showCitation(defaultRef(last))`。
- 每次 `/api/run` 保存会话后、`/api/answer` 落地后，异步 `loadSessions()` 刷新历史与「接着上次」。
  失败只记 `state.historyError`，不影响主流程。
- `loadSessions()` 打 `/api/sessions`，设 `state.history`，调 `renderRail()`；
  若当前没有 turn，`renderMain()` 让「接着上次」出现。

### 初始页

`emptyHTML()` 改成原型首屏：

```html
<section class="start">
  <p class="start-kicker mono">会话追问 · 书库检索</p>
  <h1>问一句，<br>找到原文</h1>
  <p class="start-lead">先问第一问，…</p>
  <div class="start-samples">三个 chip</div>
  <div class="start-last">
    <span class="lbl mono">接着上次</span>
    <button class="last-card" data-open-session="<id>">
      <span class="last-when mono">昨天 21:32</span>
      <span class="last-q">乡土中国 差序格局</span>
      <span class="last-meta">3 问 · 《乡土中国（2）》《死神永生》</span>
      <span class="last-go">→</span>
    </button>
  </div>
</section>
```

- 「接着上次」用 `state.history[0]`；没有历史就不渲染这块。
- 点击 `[data-open-session]` -> `openSession(id)`。
- CSS 从原型 `.start` / `.last-card` 段照搬，适配生产 `.main` 宽度。
- 首屏仍走底部 composer；`ask` 时切 `state.rail='current'`。

## 回答标题

`web/session.css` 的 `.answer h2, .answer h3` 现在 `font-size: 13.5px`，改 `1em`。
生产正文 `.answer` 是 20px，1320 以下 16px，560 以下 14px，标题跟着走。
颜色、字重、上边线、margin 不变。不要改 `.answer` 自己的字号。

## 测试

### `验收/check_session.py`

在会话落盘检查之后加：

```python
status, data = get_json("/api/sessions")
rows = data.get("sessions") or []
check(
    "会话列表接口",
    status == 200 and data.get("ok") is True and any(r.get("id") == sid for r in rows),
    "rows=%d" % len(rows),
)
row = next((r for r in rows if r.get("id") == sid), {})
check(
    "会话摘要字段",
    row.get("turns") == 3
    and bool(row.get("question"))
    and bool(row.get("updated_at"))
    and isinstance(row.get("books"), list),
    str(row)[:180],
)
```

### `验收/check_session_ui.py`

`SNAP` 删 `.cells`，加：

```js
stageRows: qa('.stage-row').length,
stageValues: qa('.stage-row .v3-val').map(e => e.textContent.trim()),
stageTexts: qa('.stage-row').map(e => e.textContent.replace(/\s+/g,' ').trim()),
stageOpen: qa('.stage-row.is-open').length,
stageActive: qa('.strip .step.is-active').length,
stageDone: qa('.strip .step.is-done').length,
stripOpen: q('.stages') ? q('.stages').classList.contains('is-open') : false,
historyRows: qa('.hist-row').length,
historyCount: q('#history-count') ? q('#history-count').textContent.trim() : '',
tabHistoryOn: q('#tab-history') ? q('#tab-history').classList.contains('is-on') : false,
tabCurrentOn: q('#tab-current') ? q('#tab-current').classList.contains('is-on') : false,
h2Size: q('.answer h2') ? getComputedStyle(q('.answer h2')).fontSize : '',
pSize: q('.answer p') ? getComputedStyle(q('.answer p')).fontSize : '',
```

改旧断言：

- 「五阶段可展开」：点 `.strip-bar`，断言 `.stage-row` 5 条且 `stripOpen`。
- 「五阶段第 5 格是跨书」：`stageTexts[4]` 含 `2 本 · 跨书`。
- 加「历史页签有列表」：`historyRows > 0`、`historyCount` 非空。
- 加「回答标题与正文同字号」：`h2Size === pSize`，1440 与 390 都查。
- 加「初始页默认历史」：导航 `base + "/"`，等 `historyRows > 0`，断言
  `tabHistoryOn`、`tabCurrentOn` 为假、`.start` 存在、`.last-card` 存在；
  点 `.last-card`，等 `rounds > 0` 且 `tabCurrentOn`，断言历史行能打开会话。
- 加「阶段结束静止」：FAKE 追问后等回答落地，断言
  `stageActive===0 && stageDone===5 && !stripOpen && stageOpen===0`。
  FAKE 的 answer 是 10ms，但要等 replay 4×900ms，wait 上限给 20 秒。
- 390：新增初始页 `base + "/"` 的截图 `会话-历史-390x844.png`，断言无横向溢出、
  `hist-row` 横向可滚（`flex-wrap: nowrap` 或 `overflow-x: auto`）。
- 旧截图路径保留。新增 `会话-历史-1440x900.png`、`会话-历史-390x844.png`、
  `会话-V3-阶段-1440x900.png`（点开阶段后的 1440 截图）。

### 验收命令

```bash
cd ~/文档/book-corpus/apps/demo
python3 -m py_compile server.py session.py
nohup python3 server.py --port 8798 > /tmp/session-8798.log 2>&1 &
python3 验收/check_session_ui.py --base http://127.0.0.1:8798
```

UI 检查必须全过。`check_session.py` 会真调模型三次，跑一次；若时间不够，
至少用 curl 验证 `/api/sessions` 并报告。完成后杀掉 8798，不要动 8765。

## 报告

写 `reports/演示-会话历史-V3-实施记录-2026-10-28.md`：

- 改了哪些文件，每个文件做了什么。
- V3 的字段映射与动画时序。
- 历史 API 的摘要字段与当前会话置顶逻辑。
- 跑过的命令与结果，截图路径。
- 没做完的、有疑问的、需要父级决定的。

不要写长篇设计复述。不要动 git，不要提交。
