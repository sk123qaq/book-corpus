# 02 默认页与多书选择 · 实施单

两件事一起做：把 02 提成 `/`，旧五阶段页移到 `/classic`；在 02 里暴露检索已经给出的书卡，让用户选一本或多本，回答按选中的书走。

读 `SESSION-UI-BRIEF.md` 的页面与接口形状，这份只写新增部分。

## 路由

- `/` 与 `/index.html` 服务 `web/session.html`。
- `/classic` 与 `/classic.html` 服务旧 `web/index.html`。
- `/session` 与 `/session.html` 仍是 02 的别名，测试可继续用，但页面自己把 URL 归一到 `/`。
- 新页页头 `完整流程 ↗` 指 `/classic`；品牌指 `/`。
- 旧页品牌指 `/classic`；页头链接改 `研究台 →`，指 `/`。
- `session.js` 的 `setUrl` 用当前 `location.pathname`，若为 `/session` 则归一到 `/`。会话 URL 形如 `/?session=<12hex>`。

## 数据形状

一页回答的范围是一个 Source 数组，挂在 turn 上：

```js
source = {book, title, chapter, path, line}
turn.sources = [source, ...]        // 目前勾选的范围
turn.answerSources = [source, ...] // 发起这次回答时的快照，用来判断是否过期
MAX_SOURCES = 8
```

`book` 是库内相对书键，如 `人文社科/人类学与考古/乡土中国（2）`；`title` 是书名；`chapter` 是显示用的章名；`path` 以 `库/` 开头。

来源三种：

- 落点：`turn.anchor`，`path/line` 用锚点，`book = bookKey(path)`，`title = bookOf(anchor)`，`chapter = anchor.title`。
- 检索首选：`trace.result`，`book = result.book`，`title = result.book_title || result.title`，`chapter = result.chapter || result.title`，`path/line` 用 result。
- 书卡：`trace.cards.cards[]`，`book = c.book`，`title = c.title`，`chapter = c.best_chapter_title`，`path = c.best_chapter`，`line = c.line`。若 `c.book === result.book`，path/line/chapter 用 result 的命中。

默认范围：

- 有 `turn.anchor` 时只选落点那一本。
- 否则选 `trace.result` 那一本。
- 没有 result 时选第一张可用书卡。

书卡只在 `trace.cards.trigger` 为真时提供候选；没触发就仍是单书，不造候选。

## 候选集合

`bookChoices(turn)` 返回带角色的候选，按这个顺序，按 `book` 去重：

1. `turn.sources` 里已选的书，source 用当前精确 path/line。角色按 book 与锚点/首选比对，得到 `anchor | top | card`。
2. 锚点那一本（若还没出现），角色 `anchor`。
3. 首选那一本（若还没出现），角色 `top`。
4. `trace.cards.cards` 的书卡，角色 `card`。

每项：

```js
{source, role: "anchor"|"top"|"card", locked: role === "anchor"}
```

- `locked` 的书卡不能点掉；点它弹 `落点那本固定入选，先取消落点再换`。
- 同一本书只出现一次。已选的 source 优先，所以保存的 multi 回答里那条精确 path/line 不会被书卡的 best_chapter 覆盖。
- 最多 8 项可选中。候选超过 8 时，第 9 项起显示为禁用并带 `最多 8 本`。
- 候选少于 2 项时不显示选择器。

## 页面结构

选择器放在五阶段窄条与落点行之间：

```html
<section class="books">
  <div class="books-head">
    <span class="t">候选书 · 7 本</span>
    <span class="h">已选 2 本</span>
    <button data-books-all>全选</button>
  </div>
  <div class="books-picks">
    <button class="book-pick is-on is-locked" data-book-pick="book-key">
      <span class="bp-title">《乡土中国（2）》</span>
      <span class="bp-tag">落点</span>
    </button>
    <button class="book-pick is-on" data-book-pick="book-key">
      <span class="bp-title">《杨庆祥 新时代文学写作景观》</span>
      <span class="bp-tag">首选</span>
    </button>
    ...
  </div>
  <div class="books-note">选中 2 本以上时，回答跨书，上下文预算 10000 字。</div>
  <div class="books-act">
    <span class="books-note">当前回答按 1 本生成</span>
    <button class="btn btn-primary" data-reanswer>用这 2 本重新回答</button>
  </div>
</section>
```

- 书卡标题最多约 18 个汉字，超出省略，`title` 属性放完整书名与章名。
- 选中的 chip 用红边与浅红底；`落点` / `首选` 是静态小标签，不是新按钮。
- 头部 `已选 N/M`，M 是候选数。全部选中时按钮文案变 `清空`，清空回到默认那一本（有落点回到落点，否则回首选）。
- `books-note` 只在候选超过 8 或选中 2 本以上时出现，写清预算。
- `books-act` 只在回答过期且是最后一轮时出现；按钮文案 `用这 N 本重新回答`。
- 不是最后一轮的轮次，选择器只读：chips 加 `disabled`，头部右侧显示 `历史轮次 · 书单只读`，没有 `全选` 和 `books-act`。旧轮改书单会让后面轮的上下文失真，不做。

## 交互

- 点书卡切换选中。已选再点取消；只剩一本且没有落点时不能取消，弹 `至少留一本`。落点那本不能取消。
- 加书时若已有 8 本，弹 `最多 8 本`。
- `全选` 取候选前 8 本；全选状态下再点变 `清空`，回到默认一本。
- 任何选择变化只改 `turn.sources`，不自动发请求。选择器下方出现 `books-act`，让用户确认。
- 点 `用这 N 本重新回答` 调 `answerTurn(turn, {sources: turn.sources, fresh: false})`。回答回来后 `turn.answerSources` 与 `turn.sources` 一致，`books-act` 消失。
- 选择变化时如果回答还在 `answering`，chips 可点，`books-act` 的按钮禁用；回答回来后显示过期条。
- 清空/取消到默认一本时，`books-act` 仍出现，因为当前回答可能按多本生成。
- 首次 `/api/run` 后自动用默认范围回答，不需要用户先确认。默认一本，行为和现在一致。

## 回答请求

`answerTurn(turn, opts)`：

1. `sources = (opts.sources || turn.sources || 默认)`，normalize，按 path 校验，slice 0..8。
2. `turn.sources = sources`；`turn.answerSources = clone(sources)`；`turn.answer = null`；`turn.context = null`；`turn.status = "answering"`。
3. `primary = sources[0]`。
4. 请求体：

```js
{
  question, book: primary.book, path: primary.path, line: primary.line,
  terms, groups, sources: sources.map(({book, title, path, line}) => ({book, title, path, line})),
  session_id, turn, thinking, fresh?
}
```

- 一本也照常送 `sources` 数组；服务端 `sources` 少于 2 时仍走单书，`book/path/line` 用 primary。
- 两本以上走多书，服务端上下文预算 10000。
- 回答的 `mode/sources` 原样保存到 `turn.answer`。
- 单书回答的响应没有 `sources`，页面以 `turn.answerSources` 为准。
- 重试回答用当前 `turn.sources`，带 `fresh: true`。

## 刷新恢复

`turnFromSaved(turn)` 先还原 `turn.sources`：

1. 回答是 multi 且有 `answer.sources`：用回答的 sources（每条带 book/title/path/line，`chapter` 从 path 补）。
2. 否则有 `turn.anchor`：用落点一本。
3. 否则用 `trace.result` 一本。
4. 否则用第一条 verified 引文的 path/line。
5. 都没有就空。

若 `turn.anchor` 存在但不在还原的 sources 里，把落点插到最前面，`turn.sources` 与 `answerSources` 因此不同，过期条会出现，让用户重答。`answerSources` 保存还原时的回答范围。

`turn.sources` 为空时不渲染选择器，也不发回答。

## 左栏与元信息

- `scopeSources(turn)`：有 multi 回答用 `answer.sources`；否则有 `answerSources` 用快照；最后才用 `turn.sources`。它描述“回答实际用了哪几本”，不是“现在勾了哪几本”。
- 轮次范围 chip：`scopeSources.length > 1` 显示 `N 本`，否则 `《书名》`。
- 五阶段第 5 格：多书显示 `N 本 · 跨书`，单书仍 `《书名》· 章`。
- 回答元信息：多书回答加 `N 本书`；上下文仍写 `接上一问 · 上下文 X/10,000 字`。
- 选择器头部显示的是 `turn.sources` 的 N，过期条对比的是 `answerSources`，两者可以不同。

## 样式

- `.books` 用现有 `--soft` 底、`--line-soft` 边、8px 圆角，放在 strip 下、anchor 上。
- `.books-head` 一行，左 `候选书`，中 `已选`，右 `全选/清空`。
- `.books-picks` flex wrap，chip 高约 26px，字号 11.5，书名省略。
- `.book-pick.is-on` 红边浅红底；`.is-locked` 的 `落点` 标签，不额外变灰；`:disabled` 只读时降透明度。
- `.books-act` 右对齐，`btn-primary` 小号；只在过期时出现。
- 390：`.books-picks` 改横向滚动，`flex-wrap: nowrap`，chip `flex: 0 0 auto`；头部可换行；`books-act` 的按钮换行在说明下。页面不许横向溢出。
- 不新增颜色，不加外部字体/CDN。

## 测试

### 快速 UI（无真实模型）

`验收/check_session_ui.py` 改到 `/`，并加：

- `/classic` 用 urllib 取一次，断言 200 且含 `ask-form`；`/` 含 `session.js`。
- 载入 `7f0f8d8ba4c4`，点第 2 轮：
  - `.books` 存在，`.book-pick.is-on` 2，`.book-pick.is-locked` 1。
  - 左栏范围 chip 有 `2 本`；回答元信息有 `2 本书` 与 `9,311/10,000`；第 5 格 `2 本 · 跨书`。
  - 第 2 轮不是最后一轮，chips 禁用，无 `data-books-all`，有 `历史轮次 · 书单只读`。
- 载入 `e4120ff4977d`，最后一轮第 2 轮：
  - `.books` 存在，选中 1（落点），`data-books-all` 存在。
  - 点一张未选中的非 locked 书卡，`.books-act` 出现，文案 `用这 2 本重新回答`，请求未发。
  - 用假 `window.fetch` 接 `/api/answer`，捕获 body，返回一个 `mode:"multi"`、`sources` 两本、`citations` 两本书的假回答，`history.used=true, chars=3625, budget=10000`。
  - 点 `data-reanswer`，断言请求 `sources.length===2`，第一条是落点 07_差序格局，第二条是新增书卡；`session_id` 与 `turn` 正确。
  - 页面变为 `2 本`、`2 本书`、`3,625/10,000 字`，chips 2 个，过期条消失。
  - 1440 截图 `验收/会话-multi-1440x900.png`；再把同一页设成 390x844，断言无横向溢出、composer 可见，截图 `验收/会话-multi-390x844.png`。
- 控制台 0 错误，无外部请求。

### 真实两问与多书（父级跑）

`验收/check_session_live.py` 改到 `/`，在原来的第 2 轮单书回答之后：

- 第 2 轮 trace 有 6 张卡，选一张非落点书卡，出现过期条。
- 点重新回答，等真实 `/api/answer`。断言 `/api/session` 的第 2 轮：
  - `answer.mode === "multi"`，`len(answer.sources) >= 2`。
  - `context.budget === 10000`，`0 < context.chars <= 10000`。
  - `related.related === true`。
- 页面左栏 `N 本`，元信息 `N 本书` 与 `/10,000 字`，chips N 个。
- 1440 与 390 截图，390 无横向溢出、composer 可见，控制台 0 错误。

### 旧页与接口

- `验收/check_demo.py` 的旧页入口改 `/classic`，断言 `ask-form`；另取 `/` 断言含 `session.js`。旧页多书全选、缓存、fresh 检查不动。
- `验收/check_session.py` 的旧页入口改 `/classic`，另取 `/` 断言 02 入口；会话 GET 与三问不动。
- `验收/shot.py` 的 `DEFAULT_URL` 改 `/classic`。

## 边界

- 只改 `工作区/演示-检索流程/` 下的 `server.py`、`web/index.html`、`web/session.html`、`web/session.css`、`web/session.js`、`验收/check_session_ui.py`、`验收/check_session_live.py`、`验收/check_demo.py`、`验收/check_session.py`、`验收/shot.py`，新增本文件。`SESSION-UI-BRIEF.md` 的路由段同步改。
- 不碰 `库/`、`目录/`、`answer.py`、`related.py`、`trace.py`、`web/app.js`、`web/style.css`。
- 不调真实 `/api/run` / `/api/answer` 做自测。用 `7f0f8d8ba4c4`、`e4120ff4977d` 和假 `fetch`。
- 8765 正在跑，别动。自测起 `--port 8799`，测完杀掉。
- 不提交；父级审 diff 后分两个 commit。

## 自测报告

写清改了哪些文件、每条命令与读数、与实施单的偏差、没做的事。不要用「应该可以」。
