#!/usr/bin/env python3
"""会话追问验收：第 1 问落盘、第 2 问带 anchor 续问、第 3 问换书、旧页面回归。

服务先起好：python3 server.py，再
  python3 验收/check_session.py [http://127.0.0.1:8765]

第 2 问预期判成 related（接着问礼治秩序与差序格局的关系），第 3 问预期判成
unrelated（换到三体），靠 related.check 的模型判定；这两问会真调模型。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import session  # noqa: E402

Q1 = "乡土中国 差序格局"
Q2 = "那礼治秩序和差序格局是什么关系？"
Q3 = "《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球"
ID_RE = re.compile(r"^[a-f0-9]{12}$")
fails = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def get(path, timeout=60):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def post(path, obj, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def get_json(path, timeout=60):
    """带状态码的 GET：4xx/5xx 也把 JSON 读回来。"""
    try:
        status, body = get(path, timeout=timeout)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8")
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {}


def loc_of(trace, question):
    """trace 的落点 + 切词组，拼成 /api/answer 的请求体。"""
    result = trace.get("result") or {}
    extract = trace.get("extract") or {}
    return {"question": question, "book": result.get("book", ""),
            "path": result.get("path", ""),
            "line": result.get("hit_line") or result.get("line") or 1,
            "terms": extract.get("terms") or [],
            "groups": extract.get("groups") or []}


def anchor_of(citations):
    for cite in citations or []:
        if cite.get("verified"):
            return {"path": cite.get("path", ""), "line": cite.get("line") or 1,
                    "title": cite.get("chapter") or "", "quote": cite.get("quote") or ""}
    return None


def cite_of(trace):
    """trace 的落点捏成一条引文，给造两本书的假会话用。"""
    result = trace.get("result") or {}
    return {"path": result.get("path", ""),
            "line": result.get("hit_line") or result.get("line") or 1,
            "quote": (result.get("hit_text") or "")[:40] or "原文引文",
            "chapter": result.get("chapter") or "",
            "verified": True}


status, body = get("/api/health")
health = json.loads(body)
check("健康检查", status == 200 and health.get("ok"),
      "books=%s" % (health.get("meta") or {}).get("books"))

# ---- 第 1 问：新会话 -------------------------------------------------
_, body = post("/api/run", {"question": Q1, "thinking": "low"})
t1 = json.loads(body)
sid = t1.get("session_id") or ""
check("第 1 问建会话", t1.get("turn") == 1 and bool(ID_RE.match(sid))
      and t1.get("related") is None,
      "turn=%s session=%s related=%s" % (t1.get("turn"), sid, t1.get("related")))
check("第 1 问有落点", bool((t1.get("result") or {}).get("path")),
      "%s" % (t1.get("result") or {}).get("path", "").split("/")[-1])

_, body = post("/api/answer", dict(loc_of(t1, Q1), session_id=sid, turn=1))
a1 = json.loads(body)
cites1 = a1.get("citations") or []
anchor = anchor_of(cites1)
check("第 1 问回答入库", a1.get("ok") is True and bool(anchor),
      "citations=%d anchor=%s" % (len(cites1), (anchor or {}).get("line")))

# ---- 第 2 问：带 anchor 续问 -----------------------------------------
_, body = post("/api/run", {"question": Q2, "session_id": sid, "turn": 2,
                            "anchor": anchor, "thinking": "low"})
t2 = json.loads(body)
rel2 = t2.get("related") or {}
check("第 2 问判成 related", t2.get("turn") == 2 and rel2.get("related") is True,
      "related=%s source=%s %s" % (rel2.get("related"), rel2.get("source"),
                                   rel2.get("reason")))

_, body = post("/api/answer", dict(loc_of(t2, Q2), session_id=sid, turn=2))
a2 = json.loads(body)
hist2 = a2.get("history") or {}
check("第 2 问带上一轮上下文", hist2.get("used") is True
      and hist2.get("budget") == session.HISTORY_CHARS_SINGLE
      and (hist2.get("chars") or 0) > 0,
      "used=%s budget=%s chars=%s" % (hist2.get("used"), hist2.get("budget"),
                                      hist2.get("chars")))
check("第 2 问上下文有章节", (hist2.get("chapters") or 0) >= 1
      and (hist2.get("paragraphs") or 0) >= 1,
      "chapters=%s paragraphs=%s" % (hist2.get("chapters"), hist2.get("paragraphs")))

# ---- 第 3 问：换书 ---------------------------------------------------
_, body = post("/api/run", {"question": Q3, "session_id": sid, "turn": 3,
                            "thinking": "low"})
t3 = json.loads(body)
rel3 = t3.get("related") or {}
check("第 3 问判成 unrelated", t3.get("turn") == 3 and rel3.get("related") is False,
      "related=%s source=%s %s" % (rel3.get("related"), rel3.get("source"),
                                   rel3.get("reason")))

_, body = post("/api/answer", dict(loc_of(t3, Q3), session_id=sid, turn=3))
a3 = json.loads(body)
hist3 = a3.get("history") or {}
check("第 3 问不带上一轮上下文", hist3.get("used") is False
      and (hist3.get("chars") or 0) == 0,
      "used=%s chars=%s" % (hist3.get("used"), hist3.get("chars")))

# ---- 落盘 -----------------------------------------------------------
path = os.path.join(session.SESS_DIR, sid + ".json")
turns = []
try:
    with open(path, encoding="utf-8") as fh:
        turns = (json.load(fh).get("turns") or [])
    read_error = ""
except (OSError, ValueError) as exc:
    read_error = exc.__class__.__name__
check("会话落盘 3 轮", len(turns) == 3 and [t.get("n") for t in turns] == [1, 2, 3],
      "%s %s" % (path, read_error))
flags = [(t.get("related") or {}).get("related") if t.get("related") else None
         for t in turns]
check("related 依次 None/true/false", flags == [None, True, False], str(flags))
ctx2 = (turns[1].get("context") if len(turns) > 1 else None) or {}
check("第 2 轮 context 落盘", bool(ctx2.get("text"))
      and (ctx2.get("chars") or 0) <= session.HISTORY_CHARS_SINGLE,
      "chars=%s budget=%s" % (ctx2.get("chars"), ctx2.get("budget")))
check("第 2 轮锚点落盘", bool(anchor) and len(turns) > 1
      and (turns[1].get("anchor") or {}).get("path") == anchor.get("path")
      and (turns[1].get("anchor") or {}).get("line") == anchor.get("line"),
      str((turns[1].get("anchor") if len(turns) > 1 else None) or {}))
check("第 1 轮与第 2 轮 answer 落盘", len(turns) > 1
      and bool((turns[0].get("answer") or {}).get("answer"))
      and bool((turns[1].get("answer") or {}).get("answer")),
      "%s/%s" % (len((turns[0].get("answer") or {}).get("answer") or "") if turns else 0,
                  len((turns[1].get("answer") or {}).get("answer") or "")
                  if len(turns) > 1 else 0))

# ---- GET /api/session：刷新页面靠它恢复 -------------------------------
status, data = get_json("/api/session?id=" + sid)
saved = (data.get("session") or {}).get("turns") or []
check("会话接口回完整会话", status == 200 and data.get("ok") is True
      and [t.get("n") for t in saved] == [1, 2, 3],
      "status=%s turns=%s" % (status, [t.get("n") for t in saved]))
check("会话接口带每轮 trace/answer/anchor/context",
      all(isinstance(t.get("trace"), dict) and isinstance(t.get("answer"), dict)
          for t in saved)
      and isinstance((saved[1].get("context") if len(saved) > 1 else None), dict)
      and bool((saved[1].get("anchor") if len(saved) > 1 else None)),
      "keys=%s" % sorted(saved[0].keys()) if saved else "没有轮次")
status, data = get_json("/api/session?id=../x")
check("非法会话 id 回 400", status == 400 and data.get("error") == "会话 id 不合法",
      "status=%s error=%s" % (status, data.get("error")))
status, data = get_json("/api/session?id=aaaaaaaaaaaa")
check("未知会话 id 回 404", status == 404 and data.get("error") == "会话不存在",
      "status=%s error=%s" % (status, data.get("error")))

# ---- build_context：上一轮跨两本书，budget 10000 ---------------------
if len(turns) >= 3:
    prev = {"n": 2, "question": Q3, "thinking": "low", "related": None,
            "anchor": None, "context": None, "trace": turns[2].get("trace"),
            "answer": {"answer": "上一轮的回答正文。" * 40, "lead": "上一轮的回答正文。",
                       "citations": [cite_of(turns[0].get("trace") or {}),
                                     cite_of(turns[2].get("trace") or {})]}}
    ctx = session.build_context([dict(turns[0], n=1), prev],
                                session.HISTORY_CHARS_MULTI)
    books = {p["book"] for p in ctx["paragraphs"]}
    check("跨两本书的上下文守 10000", ctx["budget"] == session.HISTORY_CHARS_MULTI
          and ctx["chars"] <= session.HISTORY_CHARS_MULTI,
          "budget=%s chars=%s chapters=%s paragraphs=%s"
          % (ctx["budget"], ctx["chars"], len(ctx["chapters"]),
             len(ctx["paragraphs"])))
    check("上下文真的取到两本书以上的原文", len(books) >= 2 and ctx["chars"] > 0,
          "books=%s" % sorted(b.rsplit("/", 1)[-1] for b in books))
else:
    check("跨两本书的上下文守 10000", False, "会话不足 3 轮，造不出两本书的假会话")

# ---- 旧页面回归 -----------------------------------------------------
_, body = post("/api/run", {"question": Q1, "thinking": "low"})
old = json.loads(body)
stages = set(old.get("stage_ms") or {})
check("旧请求仍需建会话", old.get("turn") == 1 and bool(ID_RE.match(old.get("session_id") or "")))
check("旧请求五阶段齐全",
      stages >= {"understand", "rounds", "rank", "cards", "result"}
      and bool((old.get("result") or {}).get("text")),
      "stages=%d" % len(stages))
_, html = get("/classic")
check("旧页仍能打开", "书库检索" in html and "ask-form" in html)
_, home = get("/")
check("/ 是 02 页", "session.js" in home and 'id="main"' in home)

# ---- 会话的拒绝路径 -------------------------------------------------
missing = "ffffffffffff"
try:
    post("/api/run", {"question": Q1, "session_id": missing})
    check("未知会话回 404", False)
except urllib.error.HTTPError as exc:
    check("未知会话回 404", exc.code == 404, "code=%d" % exc.code)
try:
    post("/api/run", {"question": Q1, "session_id": sid, "turn": 9})
    check("越界 turn 回 400", False)
except urllib.error.HTTPError as exc:
    check("越界 turn 回 400", exc.code == 400, "code=%d" % exc.code)
try:
    post("/api/run", {"question": Q1, "session_id": "../../etc/passwd"})
    check("运行接口非法会话 id 回 404", False)
except urllib.error.HTTPError as exc:
    check("运行接口非法会话 id 回 404", exc.code == 404, "code=%d" % exc.code)

print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
sys.exit(0 if not fails else 1)
