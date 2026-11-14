# 会话追问 · 后端实施单


状态：historical · 2026-10-27
> **布局说明（2026-11-02）**：`<uid>` 是书目录占位，例如 `B0024`。v2 下书目录是 `books/<uid>/`，索引是 `build/检索/index.db`，书目库是 `build/library.db`，人账在 `tables/`。本文示例已按 v2 改写。


给 `apps/demo/` 加多轮会话。前端页面这轮不动，只做接口与回答阶段。
只读 `books/<uid>/`、`build/检索/index.db`、`build/library.db`。只写 `缓存/sessions`、`缓存/related`、`缓存/answers`。

## 数据形状

一个会话是一串 Turn，落盘到 `缓存/sessions/<id>.json`。

```json
{
  "id": "a1b2c3d4e5f6",
  "created_at": "2026-10-27T15:00:00+0800",
  "updated_at": "2026-10-27T15:02:00+0800",
  "turns": [
    {
      "n": 1,
      "question": "…",
      "thinking": "high",
      "at": "…",
      "related": null,
      "anchor": null,
      "trace": { "…": "tracer.build 的原样结果，可裁剪 ranking/chapters 但要留 result" },
      "answer": { "…": "answerer.answer_question 的原样结果" },
      "context": null
    },
    {
      "n": 2,
      "related": {"related": true, "reason": "…", "against": [1], "ms": 2100, "source": "model", "thinking": "low"},
      "anchor": {"path": "books/<uid>/…/06_差序格局.md", "line": 120, "title": "差序格局", "quote": "愈推愈远，也愈推愈薄"},
      "context": {
        "prev": 1, "budget": 5000, "chars": 4123, "text": "…拼好的提示词片段…",
        "chapters": [{"book": "…", "title": "差序格局", "path": "books/<uid>/…", "line": 120}],
        "paragraphs": [{"book": "…", "title": "…", "path": "books/<uid>/…", "line": 120, "text": "…"}],
        "anchor_used": true, "truncated": 0
      }
    }
  ]
}
```

会话 id 是 12 位小写 hex，必须用 `^[a-f0-9]{12}$` 校验后再拼路径。

## 新模块 `related.py`

```python
def check(question, previous, use_cache=True, thinking="low"):
    """previous: [{"n":1,"question":"…","answer":"…"}]，answer 传前 300 字即可。
    返回 {"related": bool, "reason": str, "against": [1], "ms": float,
          "source": "model"|"cache"|"fallback", "thinking": "low"}。"""
```

- 调 `pi -p --no-tools --no-session --no-context-files --no-skills --no-extensions --model opencode-go/deepseek-v4.1-flash --thinking low`，超时 60 秒。
- 提示词只回一行 JSON `{"related":true|false,"reason":"不超过 40 字"}`。
- 判据写清：新问题依赖上一轮的问题或答案才能问得通，或明确要求就上一轮的人、事、说法、章节继续展开、比较、举例，算 related。另起主题、换书、同一本书里换一件不相干的事，算 unrelated。
- 缓存键：`sha1(question + 每个 previous 的 n/question/answer + prompt 版本)`，落 `缓存/related/<key>.json`，原子写。
- 模型不可用或输出解析不了时走词面兜底：新问题与上一轮问题+答案取 2 字组，去掉「的是了和与在什么怎么为什么」这些停用字，交集 ≥ 3 判 related，否则 unrelated。`source="fallback"`。
- 只比较最后 3 个 previous。

## 新模块 `session.py`

```python
SESS_DIR = HERE / "缓存" / "sessions"
HISTORY_CHARS_SINGLE = 5000
HISTORY_CHARS_MULTI = 10000


def new_id(): ...  # secrets.token_hex(6)
def create(): ...  # {"id","created_at","updated_at","turns":[]}
def load(sid): ...  # dict | None；坏文件返回 None，不抛
def save(sess): ...  # 原子写，tmp + os.replace
def find(sess, n): ...  # turn | None
def public(sess): ...  # {"id","turns":[{"n","question","related","answer_lead"}]}


def build_context(turns, budget, anchor=None):
    """turns 是当前会话里当前问之前的所有 turn，最后一个是上一轮。
    返回上面的 context dict，text 是拼好的提示词片段，chars=len(text) 必须 <= budget。"""
```

`build_context` 的取舍顺序，超预算就从尾部丢：

1. `anchor` 指向的段落优先（如果有，且能在上一轮的 citations/chapters 里对上）。
2. 上一轮的问题全文。
3. 上一轮的答案全文（`answer.answer`，没有就退 `trace.result.text`）。
4. 章节位置：上一轮 `answer.citations` 的 `path/line/quote` 加 `trace.plan.chapters[].selected[]`，按 `(path,line)` 去重，最多 12 条。每条带书名与章名。
5. 原文段落：按第 4 步的顺序，用 `trace.read_chapter(path, line, chars=900, before=300)` 取 `text`，按 `(path,line)` 去重，最多 8 段，逐段加到预算为止。
6. 更早的轮次（上一轮之前的最后 2 轮）：各给「第 n 问：… / 答：前 300 字」，放在最后。

`text` 的格式：

```
【同一会话的上一轮，先读这一节】
第 1 问：…
上一轮回答（已经给过用户，不要整段重复）：
…
上一轮用到的章节：
- 《乡土中国》· 差序格局 · books/<uid>/…/06_差序格局.md:120
上一轮命中的原文：
《乡土中国》 books/<uid>/…/06_差序格局.md:110-150
…
更早的轮次：
第 1 问：… / 答：…
这一问如果是在接着上面问，就用这些事实定位，不要从零重判；如果另起话题，忽略这一节。
```

上一轮没有 answer 时（用户没等回答就追问），用 `trace.result.text` 当答案，章节用 `trace.result.path/line`，`text` 里注明「上一轮回答未完成」。

## `answer.py` 改动

- 常量：`HISTORY_CHARS_SINGLE=5000`、`HISTORY_CHARS_MULTI=10000`（session.py 可从这里 import，别两处各写一份）。
- `PROMPT` 和 `MULTI_PROMPT` 在「问题/书」之后加 `{history}` 占位，空字符串时什么都不多。
- `build_prompt(question, book, windows, chapters, history="")`，`limit = PROMPT_MAX + (HISTORY_CHARS_SINGLE if history else 0)`；`build_multi_prompt(..., history="")`，`limit = PROMPT_MAX_MULTI + (HISTORY_CHARS_MULTI if history else 0)`。
- `cache_key(..., history="")` 与 `multi_cache_key(..., history="")`：有 history 时把 `sha1(history)[:16]` 放进 payload 的 `"h"`，没有就不放。`PROMPT_VERSION` 不动，旧缓存继续有效。
- `answer_question(..., history="")` → `_answer_single` / `_answer_multi` 透传 `history`。
- 只在 history 非空时改提示词，空时与现在逐字一致。

## `server.py` 改动

### `/api/run`

请求多三个可选字段：`session_id`（str）、`turn`（int）、`anchor`（`{path,line,title,quote}`）。

- 无 `session_id`：新建会话，`n=1`，`related=null`。
- 有：`load`，找不到回 404 `{"ok":false,"error":"会话不存在"}`。`n = turn` 或 `len(turns)+1`。`1 <= n <= len(turns)+1`，否则 400。
- `related = related.check(question, 最近 3 轮)`。第 1 问跳过，直接 `None`。
- `trace = tracer.build(...)`。
- 写 turn：`n <= len(turns)` 时替换第 n 轮并截断后面的轮次（重跑同一问），否则追加。
- `anchor` 校验：path 必须在 `books/<uid>/` 下且 `.md`，line 是正整数；不合法就忽略。
- 返回：trace 原字段 + `session_id`、`turn`、`related`、`session`（`public(sess)`）。

### `/api/answer`

请求多两个可选字段：`session_id`、`turn`。

- 带上时 `load` + `find`；找不到就按无会话处理，不报错（旧页面不传也能用）。
- 若该 turn 的 `related.related` 为真：`budget = HISTORY_CHARS_MULTI if len(sources)>=2 else HISTORY_CHARS_SINGLE`，`ctx = session.build_context(turns[:turn], budget, anchor=turn["anchor"])`，`history=ctx["text"]`。
- 否则 `history=""`，`ctx=None`。
- 调 `answerer.answer_question(..., history=history)`。
- 把 `answer` 与 `context` 写回 turn，`save`。
- 返回 answer 原字段 + `session_id`、`turn`、`related`、`history`：`{"used":bool,"prev":n,"chars":int,"budget":int,"chapters":int,"paragraphs":int}`。

### 并发

- `api_run` 已有全局 `LOCK`，会话写在里面。
- `api_answer` 的读-改-写用新的 `SESSION_LOCK` 包住，别拿 `LOCK`（回答 240 秒会把检索堵死）。

## 验收 `验收/check_session.py`

假设服务已在 `http://127.0.0.1:8765`。用 urllib，风格照 `验收/check_demo.py`。

1. 健康检查。
2. 第 1 问「乡土中国 差序格局」，`thinking=low`。断言 `turn==1`、`session_id` 匹配 12 hex、`related is None`。
3. 用第 1 问 trace 的 `result.path/line` 与 `extract.terms/groups` 调 `/api/answer`，拿到 citations；取第一条当 anchor。
4. 第 2 问「那礼治秩序和差序格局是什么关系？」，带同一 `session_id`、`turn=2`、上一步的 anchor。断言 `related.related is True`；answer 返回 `history.used is True`、`history.budget==5000`、`history.chars>0`。
5. 第 3 问「《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球」，`turn=3`。断言 `related.related is False`；answer 返回 `history.used is False`。
6. 读 `缓存/sessions/<id>.json`：3 轮，related 依次 `None/true/false`，第 2 轮 context 有 text 且 `chars<=5000`。
7. 直接调 `session.build_context` 造一个两本书的假会话，`budget=10000`，断言 `budget==10000` 且 `chars<=10000`。不调模型。
8. 旧页面回归：`/api/run` 不带 `session_id` 仍返回五个阶段，`/` 仍能打开。
9. 末行 `ALL PASS` 或 `FAIL n`。

## 不做

- 不改 `web/` 的页面与脚本。这轮只做后端。
- 不改 `trace.py` 的 `build`，会话信息由 server 包在外面。
- 不动 `books/<uid>/`、`tables/`、`src/bookcorpus/scripts/`。
