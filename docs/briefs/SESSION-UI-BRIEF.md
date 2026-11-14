# 02 会话页接线 · 实施单


状态：historical · 2026-10-27
> **2026-11-12 修订**：落点改为一次性（只影响下一问），检索有结果时结果优先；上下文改带上一轮要点，完整回答不再注入。以下为当时设计，行为以 `apps/demo/README.md` 为准。

> **布局说明（2026-11-02）**：`<uid>` 是书目录占位，例如 `B0024`。v2 下书目录是 `books/<uid>/`，索引是 `build/检索/index.db`，书目库是 `build/library.db`，人账在 `tables/`。本文示例已按 v2 改写。


把 `原型-追问/v2-split.html` 的三栏研究台接上已经跑通的会话后端。只做页面与一个只读接口，不改回答逻辑。

## 边界

- 只改 `apps/demo/` 下的 `web/`、`server.py`、`session.py`、`验收/check_session.py`，新增 `SESSION-UI-BRIEF.md`。
- 不碰 `books/<uid>/`、`tables/`、`answer.py`、`related.py`、`trace.py`、`web/app.js`、`web/style.css`、`web/index.html` 的既有结构（只加一个入口链接）。
- 页面无外部依赖、无 CDN、无构建步骤。中文界面，纸感皮肤沿用 `原型-追问/shared/tokens.css`。
- 1440×900 是一等公民；390 宽不许横向滚动，输入框必须在首屏可见。
- 服务 8765 已在跑旧代码，不要动它。自测用 `--port 8799` 临时服务，测完杀掉。

## 路由

- `/` 与 `/index.html` → `web/session.html`（02 研究台，默认页）。
- `/classic` 与 `/classic.html` → 旧版五阶段页 `web/index.html`。
- `/session` 与 `/session.html` 仍是 02 的别名，页面自己把 URL 归一到 `/`。
- `web/session.css`、`web/session.js` 由 02 页加载。
- 新页页头 `完整流程 ↗` 指 `/classic`，品牌指 `/`；旧页页头 `研究台 →` 指 `/`，品牌指 `/classic`。

## 新增只读接口

`GET /api/session?id=<12hex>`：

- `session.valid_id(sid)` 先校验，非法 400 `{"ok":false,"error":"会话 id 不合法"}`。
- `session.load` 找不到 404 `{"ok":false,"error":"会话不存在"}`。
- 成功 200 `{"ok":true,"session":<完整 session>}`。完整 session 含每轮 `trace`、`answer`、`context`、`anchor`、`related`，刷新页面靠它恢复。

`session.py` 加：

```python
def valid_id(sid):
    return isinstance(sid, str) and bool(ID_RE.match(sid))
```

## 已有数据形状

`POST /api/run` 返回 trace 顶层加：

```json
{"session_id":"a1b2c3d4e5f6","turn":2,"related":{"related":true,"reason":"…","against":[1],"ms":2100,"source":"model","thinking":"low"},"session":{"id":"…","turns":[{"n":1,"question":"…","related":null,"answer_lead":"…"}]}}
```

trace 关键字段：

- `extract.terms: string[]`，`extract.groups: [{variants: string[]}]`
- `plan.rounds`, `plan.best_round`, `plan.stop`, `plan.chapters`, `plan.ranking`, `plan.nouns`
- `stage_ms: {understand, rounds, rank, cards, result}`，单位毫秒
- `cards: {trigger, cards, reasons}`
- `result: {book, book_title?, chapter, title, path, line, text, hit_line}`
- `question`, `thinking`, `ms`

`POST /api/answer` 返回：

```json
{"ok":true,"mode":"single","answer":"…","lead":"…","sections":[{"heading":"…","body":"…"}],"dissent":"","gap":"","citations":[{"path":"books/<uid>/…/06_差序格局.md","line":120,"quote":"…","chapter":"差序格局","verified":true,"book":"…","book_title":"…"}],"confidence":"high","not_found":false,"error":"","ms":24300,"thinking":"high","cached":false,"session_id":"…","turn":2,"related":{…},"history":{"used":true,"prev":1,"chars":4110,"budget":5000,"chapters":10,"paragraphs":2}}
```

单书时没有 `sources` 与 `book` 字段也可用。引文 `book` 可能缺，按 `path` 推。

`GET /api/session` 的 turn：

```json
{"n":2,"question":"…","thinking":"high","at":"…","related":{…},"anchor":{"path":"books/<uid>/…","line":120,"title":"差序格局","quote":"…"},"trace":{…},"answer":{…},"context":{"prev":1,"budget":5000,"chars":4110,"text":"…","chapters":[…],"paragraphs":[…],"anchor_used":true,"truncated":0}}
```

注意：落盘的 `answer` 没有 `history` 字段，刷新后用 `turn.context` 还原 `used/chars/budget`。

`GET /api/chapter?path=&line=&chars=900&before=300` 返回 `text/title/hit_text/hit_line/hit_start/hit_end/html/line`。`text` 是从命中行往上 `before` 字、往下 `chars` 字的整段窗口；`hit_text` 是命中行原文。

## 页面状态

一个 `state`，渲染只读它。不要散落布尔。

```js
state = {
  sessionId: "",
  turns: [],          // Turn[]
  viewN: 0,           // 当前查看第几问
  selected: null,     // 下一问的落点 {citation, fromN, cite}；取消为 null
  shown: null,        // 右栏正在看的引文 {citation, fromN, cite}
  drawer: {seq: 0, loading: false, data: null, error: ""},
  stats: null,
  busy: false,
  elapsed: 0,
  timer: null,
  error: ""
}
```

Turn：

```js
{
  n, question, thinking, trace, related, anchor, answer, context,
  status: "retrieving" | "answering" | "ready" | "run-error" | "answer-error",
  runError: "", answerError: ""
}
```

`busy = turns.some(t => t.status === "retrieving" || t.status === "answering")`。同一时刻只有一个在跑。

## 行为

### 首屏与首问

- 页头统计用 `/api/health` 的 `meta.books/paras/chapters`，千分位。失败显示 `—`，不挡页面。
- 无会话时中栏是空态：`问一句，找到原文`、一句说明、三个示例按钮（点一下直接问）。
- 底部 composer 始终在。空态占位 `问一句，从书里找原文`。
- 问第一问：`POST /api/run {question, thinking:"high"}`，没有 `session_id`，`turn` 不送。
- 拿到 `session_id` 后 URL 用 `history.replaceState` 换成 `?session=<id>`。刷新能恢复。
- 支持 `?session=<id>` 直接载入完整会话；`?q=<问题>` 直接发问（测试与分享用）。发问后把 `q` 从 URL 去掉。

### 追问与落点

- 新一问的 `n = 最后一轮.n + 1`。永远追加，不替换旧轮。
- 送 `POST /api/run {question, session_id, turn:n, anchor?, thinking:"high"}`。
- `anchor` 的形状 `{path, line, title, quote}`，`title` 用引文的 `chapter`，`quote` 用引文 `quote`。
- 选依据：点本轮依据行、引文 `[n]` 或右栏原文，`selected` 设为该引文；再点同一处取消。`selected` 同时是高亮状态与下一问的落点。
- 问出去时把 `selected` 写进 `turn.anchor`；提交后保留 `selected`，所以下一问的 hint 仍显示这个落点，和 02 原型一致。用户点「取消」后 `selected=null`，下一问不带落点；已经答过的轮次顶部那条落点行不消失。
- 已答轮次的落点行由 `turn.anchor` 渲染，不由 `selected` 渲染。取消只影响下一问。
- 轮次没有 anchor 时，中栏显示虚线范围行 `没有选依据 · 这一问不带落点框`，不是实线落点框。
- 判定为不相关（`related.related === false`）时，范围行写 `独立问题 · 没有参考上一问`；若同时有 anchor，补一句 `判定为独立问题，落点未用于上下文`。
- 点左栏旧轮只切换查看，不偷偷改 `selected`。要看旧轮的落点，顶部落点行已经是它。

### 回答

- `/api/run` 成功后立刻 `POST /api/answer`：
  `{question, book: trace.result.book, path: trace.result.path, line: trace.result.line, terms: 去重(extract.terms + plan.nouns).slice(0,12), groups: extract.groups, sources: [], session_id, turn:n, thinking:"high"}`。
- 02 页只做单书回答。多书的 `sources` 留给旧页的全选。
- 回答成功：`turn.answer = data`，`status="ready"`。如果 `data.history.used`，在回答元信息里写 `接上一问 · 上下文 <chars>/<budget> 字`。
- 回答失败：`status="answer-error"`，保留 trace，显示错误与 `重试回答`。重试只重发 `/api/answer`，带 `fresh:true`，不重新检索。
- 检索失败：`status="run-error"`，显示错误与 `重试`。重试重新发同一问；如果上一跑没写会话，重试会新建。
- 加载态要动：`正在检索，通常 20 到 60 秒 · 已用 12s`，每秒更新；回答阶段 `正在组织回答，通常 40 到 90 秒 · 已用 24s`。不要假装阶段进度。

### 中栏

- 顶部 kicker：`第 N 问`、关系 chip（`首问` / `承接 01 [2]` / `承接上一问 · 无落点` / `独立问题`）、右侧 `检索 1.8s · 回答 24.3s`。
- 问题标题。
- 五阶段窄条，名称固定 `理解问题 / 词法循环 / 打分与选章 / 书卡 / 结果`，点开显示五格：
  1. 理解问题：`<terms.length> 组词`，下面列前 3 个词。
  2. 词法循环：`<rounds.length> 轮`，`plan.stop` 转成中文（C 到线 / A+B 到线 / 跑满 5 轮 / 词集用尽）。
  3. 打分与选章：`<unique books> 本 · <chapters.length> 章`。
  4. 书卡：`trigger ? cards.length + " 张" : "未触发"`。
  5. 结果：`《<book_title||book>》· <chapter>`。
  每格右上角是 `stage_ms` 的秒数。拿不到就 `—`。
- 落点行：实线框，`追问落点 · 来自 0N`、`[k]`、引文前 28 字、`书 · 章 →`。
- 无落点：虚线范围行。
- 回答正文：`lead` 一段，`sections` 每节 `h3 + 段落`；没有 sections 就按 `answer` 的空行拆段。`dissent`/`gap` 用小字附在最后。不要渲染 `##`/`**` 标记。
- 回答元信息：置信、`N 条引文已回查`、上下文、`缓存命中`、耗时、模型、思考档。`error` 是「引文回查不足」这类提示，不是整页失败。
- 依据列表：每条一个按钮，`[k]`、`书 · 章`、引文、右侧 `原文`/`落点`。`selected` 匹配时 `.is-on`；本轮 `turn.anchor` 匹配的引文加静态 `落点` 小标签，但不要因为取消 `selected` 就消失。
- 没有引文：显示 `未找到可引用的原文`，保留回答与 gap。

### 左栏

- 标题 `本次会话`，右侧 `N 问`。每轮：
  - 编号 `01`，当前轮加 `· 当前`。
  - 关系行：`承接 01` + `[k]` + 落点短句；或 `承接上一问 · 没有选落点`；或 `独立问题 · 没有参考上一问`；首问没有。
  - 问题原文。
  - 范围 chip：`《书名》`；下面 `[1][2]…` 是本轮引文号，被后续轮当落点用的号标红。
- 正在检索/回答的轮显示 caret 与 `检索中`/`回答中`；失败轮显示 `检索失败`/`回答失败`。
- 点一轮切到那一轮。底部 `+ 新会话` 清空状态回到空态，不删旧会话文件。

### 右栏

- 默认显示当前查看轮的第一条引文；该轮有 anchor 就显示 anchor 那条。
- 头部：`[k]`、`《书名》`、章节；若这条是 `selected`，加 `下一问的落点`；若是 `turn.anchor`，加 `这一问的落点`。
- 正文用 `/api/chapter?path=&line=&chars=900&before=300` 取窗口：
  - 标题 `原文`。
  - `hit_text` 所在行渲染成 `.current`；引文 `quote` 若能在 `hit_text` 里找到，用 `<mark>` 包住。前后行渲染成 `.ctx`，每行一段。
  - 不要用模型生成的摘要替换原文。
- `‹ 上一条依据` / `下一条依据 ›` 在本轮引文之间切换，只换查看，不改 `selected`。
- 底部显示 `path` 与 `复制路径` 按钮。`navigator.clipboard` 不可用时选中文本提示手动复制。
- 390 宽：右栏折到中栏下面，默认收起，点依据打开；再点 `收起` 关闭。

### 底部 composer

- 有 `selected`：`下一问挂在 [k] <短句> · 取消`；占位 `就 [k] 这一段接着问`。
- 没有：`不选依据就是整轮追问 · ⏎ 发送`；占位 `接着问，不选依据也能问`。
- 三个建议 chip，通用追问：`还有别的例子吗？`、`这个说法有反例吗？`、`作者在别的章节怎么讲？`。点一下直接发问；busy 时禁用。
- Enter 发送；busy 时按钮 `检索中…` 或 `回答中…` 并禁用。
- 输入为空不发送。发送后清空输入，焦点留在输入框。

## 文件与样式

- `web/session.html`：页头、三栏骨架、composer、右栏容器，不写内联内容数据。
- `web/session.css`：以 `原型-追问/shared/tokens.css` 加 `原型-追问/v2-split.html` 的 `<style>` 为底，删掉原型专用标注；补：
  - `.empty`、`.empty h1`、`.empty p`、`.empty-samples`
  - `.loading-card`、`.dot-pulse`、`.elapsed`
  - `.answer h2/h3`、`.answer-meta`、`.answer-error`、`.answer-loading`、`.answer-lead`
  - `.ev.is-anchor`、`.ev .pin-tag`
  - `.toast`
  - `.rail-foot` 作为按钮的样式
  - `.masthead .nav` 链接
- 长引文、长路径、回答正文加 `overflow-wrap: anywhere`，390 不横向滚动。
- `web/session.js`：普通 ES2020，无框架。事件用 `document` 委托。所有 API 走一个 `fetchJSON`，错误消息取 `data.error`。
- 不要在 JS 里拼未转义的用户文本或引文；先 `esc()`。
- 不要写阶段旁白型注释；只在非显然的「为什么」处写一句。

## 自测（子代理跑）

1. `python3 -m py_compile server.py session.py`
2. `node --check web/session.js`（没装 node 就说明，不装包）
3. 起临时服务 `python3 server.py --port 8799`，确认：
   - `/session` 200 且含 `id="rounds"`、`id="main"`、`session.js`
   - `/session.css`、`/session.js` 200
   - `/api/session?id=7f0f8d8ba4c4` 200 且 `len(turns)==3`
   - `/api/session?id=../x` 400；`/api/session?id=aaaaaaaaaaaa` 404
   - `/` 仍是旧页，`ask-form` 在
4. 杀掉临时服务。
5. 不要调真实 `/api/run` 或 `/api/answer`，端到端由父级跑。

## 交付报告

写清楚：改了哪些文件、每条自测命令与读数、与这份实施单的偏差、还没做的事。不要用「应该可以」结尾。

## 验收判据（父级跑）

- 1440×900：`?q=乡土中国 差序格局` 出 1 轮，左栏 1 条，五阶段可展开，回答有段落，依据可点，右栏有原文；点依据出现 `下一问的落点`，再点取消。
- 选一条依据追问 `那礼治秩序和差序格局是什么关系？`：第 2 轮 related=true，左栏关系行 `承接 01`，回答元信息出现 `接上一问 · 上下文 …/5000 字`，`/api/session` 里第 2 轮 `context.chars <= 5000`。
- 载入 `?session=7f0f8d8ba4c4`，点第 3 轮：中栏是虚线范围行，没有实线 `.anchor` 落点框；第 2 轮有 `.anchor`。
- 390×844：无横向滚动，composer 可见，右栏可展开/收起。
- 控制台 0 错误，无外部请求。
