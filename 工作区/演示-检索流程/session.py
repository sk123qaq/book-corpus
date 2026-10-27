#!/usr/bin/env python3
"""多轮会话：一个会话一串 Turn，落盘到 缓存/sessions/<id>.json。

turn 结构见 SESSION-BRIEF.md 的「数据形状」。这里只管读写、校验与拼上一轮的
上下文；检索与回答由 trace / answer 各自负责。

会话 id 是 12 位小写 hex，load 先校验再拼路径，校验不过当没有这个会话。
"""
import json
import os
import re
import secrets
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import trace as tracer  # noqa: E402
from answer import HISTORY_CHARS_MULTI, HISTORY_CHARS_SINGLE  # noqa: E402,F401

SESS_DIR = os.path.join(HERE, "缓存", "sessions")
ID_RE = re.compile(r"^[a-f0-9]{12}$")

MAX_CHAPTERS = 12
MAX_PARAGRAPHS = 8
PARA_CHARS = 900
PARA_BEFORE = 300
EARLIER_TURNS = 2
EARLIER_ANSWER_CHARS = 300

HEAD = "【同一会话的上一轮，先读这一节】"
FOOT = ("这一问如果是在接着上面问，就用这些事实定位，不要从零重判；"
        "如果另起话题，忽略这一节。")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_id():
    return secrets.token_hex(6)


def create():
    stamp = now()
    return {"id": new_id(), "created_at": stamp, "updated_at": stamp, "turns": []}


def valid_id(sid):
    return isinstance(sid, str) and bool(ID_RE.match(sid))


def _path(sid):
    return os.path.join(SESS_DIR, sid + ".json") if valid_id(sid) else ""


def load(sid):
    """读会话。路径不合法、文件坏、结构不对都返回 None，不抛。"""
    path = _path(sid)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            sess = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(sess, dict) or sess.get("id") != sid:
        return None
    turns = sess.get("turns")
    if not isinstance(turns, list):
        return None
    sess["turns"] = [t for t in turns if isinstance(t, dict)
                     and isinstance(t.get("n"), int)]
    sess["turns"].sort(key=lambda t: t["n"])
    return sess


def save(sess):
    path = _path(sess.get("id") if isinstance(sess, dict) else "")
    if not path:
        raise ValueError("会话 id 不合法")
    sess["updated_at"] = now()
    os.makedirs(SESS_DIR, exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sess, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def find(sess, n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    for turn in (sess or {}).get("turns") or []:
        if turn.get("n") == n:
            return turn
    return None


def public(sess):
    out = []
    for turn in (sess or {}).get("turns") or []:
        ans = turn.get("answer")
        lead = ans.get("lead") if isinstance(ans, dict) else ""
        out.append({"n": turn.get("n"),
                    "question": turn.get("question") or "",
                    "related": turn.get("related"),
                    "answer_lead": str(lead or "")})
    return {"id": (sess or {}).get("id") or "", "turns": out}


# ---- 上下文 ---------------------------------------------------------

def _rel(path):
    raw = str(path or "").strip().replace("\\", "/")
    if not raw.startswith("库/") or not raw.endswith(".md"):
        return ""
    return raw


def _book_of(book, path):
    book = str(book or "").strip()
    if book:
        return book
    path = _rel(path)
    return path[2:].split("/md/")[0] if "/md/" in path else ""


def _book_name(book):
    return str(book or "").strip().rsplit("/", 1)[-1]


def _prev_answer(turn):
    """上一轮的答案正文与是否答完；没答完退 trace.result.text。"""
    ans = turn.get("answer")
    if isinstance(ans, dict):
        body = str(ans.get("answer") or "").strip()
        if body:
            return body, True
    trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
    result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
    return str(result.get("text") or "").strip(), False


def _chapter_candidates(turn):
    """上一轮用到的章节：citations 在前，选章次之，最后由 result 兜底。"""
    rows, seen = [], set()

    def add(book, title, path, line):
        path = _rel(path)
        try:
            line = max(1, int(line or 1))
        except (TypeError, ValueError):
            line = 1
        if not path or (path, line) in seen:
            return
        seen.add((path, line))
        rows.append({"book": _book_of(book, path), "title": str(title or "").strip(),
                     "path": path, "line": line})

    ans = turn.get("answer")
    if isinstance(ans, dict):
        for cite in ans.get("citations") or []:
            if isinstance(cite, dict):
                add(cite.get("book"), cite.get("chapter") or cite.get("title"),
                    cite.get("path"), cite.get("line"))
    trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), dict) else {}
    for group in plan.get("chapters") or []:
        if not isinstance(group, dict):
            continue
        for sel in group.get("selected") or []:
            if isinstance(sel, dict):
                add(group.get("book"), sel.get("chapter_title"),
                    sel.get("chapter_path"), sel.get("line"))
    result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
    add(result.get("book"), result.get("chapter") or result.get("title"),
        result.get("path"), result.get("line") or result.get("hit_line"))
    return rows[:MAX_CHAPTERS]


def _anchor_index(anchor, chapters):
    """anchor 落在上一轮用到的章节里时返回下标：先认同一行，再认同一个文件。"""
    if not isinstance(anchor, dict):
        return -1
    path = _rel(anchor.get("path"))
    try:
        line = int(anchor.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    if not path or line <= 0:
        return -1
    for i, item in enumerate(chapters):
        if item["path"] == path and item["line"] == line:
            return i
    for i, item in enumerate(chapters):
        if item["path"] == path:
            return i
    return -1


def _span(info):
    """一段原文在文件里的起止行：命中行 + 该段后面的行数。"""
    lines = str(info.get("text") or "").splitlines()
    start = int(info.get("line") or 1)
    hit = info.get("hit_text") or ""
    offset = 0
    if hit:
        for i, line in enumerate(lines):
            if line == hit:
                offset = i
                break
    return start, start + max(0, len(lines) - 1 - offset)


def _fill(chapters, budget):
    """按章节顺序读原文段落，逐段加到够预算或满 8 段为止。返回段落与渲染块。"""
    paragraphs, blocks, seen, used = [], [], set(), 0
    for item in chapters:
        if len(paragraphs) >= MAX_PARAGRAPHS:
            break
        key = (item["path"], item["line"])
        if key in seen:
            continue
        seen.add(key)
        try:
            info = tracer.read_chapter(item["path"], item["line"],
                                       chars=PARA_CHARS, before=PARA_BEFORE)
        except (ValueError, OSError):
            continue
        text = str(info.get("text") or "").strip()
        if not text:
            continue
        line = int(info.get("line") or item["line"])
        start, end = _span(info)
        paragraphs.append({"book": item["book"], "title": item["title"],
                           "path": item["path"], "line": line, "text": text})
        span = "%s:%d-%d" % (item["path"], start, end) if end > start \
            else "%s:%d" % (item["path"], start)
        blocks.append("《%s》 %s\n%s" % (_book_name(item["book"]), span, text))
        used += len(text)
        if used >= budget:
            break
    return paragraphs, blocks


def _earlier(turns):
    rows = []
    for turn in turns[-EARLIER_TURNS:]:
        body, _done = _prev_answer(turn)
        rows.append("第 %d 问：%s / 答：%s"
                    % (turn["n"], " ".join(str(turn.get("question") or "").split()),
                       " ".join(body.split())[:EARLIER_ANSWER_CHARS] or "（未完成）"))
    return rows


def _render(n, question, answer, finished, chapters, blocks, earlier):
    parts = [HEAD, "第 %d 问：%s" % (n, question)]
    if answer:
        head = ("上一轮回答（已经给过用户，不要整段重复）：" if finished
                else "上一轮回答未完成，下面是它停下的那段原文：")
        parts.append(head + "\n" + answer)
    if chapters:
        rows = []
        for c in chapters:
            cells = ["《%s》" % _book_name(c["book"])] if c["book"] else []
            if c["title"]:
                cells.append(c["title"])
            cells.append("%s:%d" % (c["path"], c["line"]))
            rows.append("- " + " · ".join(cells))
        parts.append("上一轮用到的章节：\n" + "\n".join(rows))
    if blocks:
        parts.append("上一轮命中的原文：\n" + "\n\n".join(blocks))
    if earlier:
        parts.append("更早的轮次：\n" + "\n".join(earlier))
    parts.append(FOOT)
    return "\n".join(p for p in parts if p)


def build_context(turns, budget, anchor=None):
    """拼上一轮的上下文。turns 是当前问之前的全部轮次，最后一个是上一轮。

    超出预算从尾部丢：更早的轮次 → 原文段落 → 章节 → 上一轮答案 → 上一轮问题。
    """
    try:
        budget = max(0, int(budget or 0))
    except (TypeError, ValueError):
        budget = 0
    out = {"prev": 0, "budget": budget, "chars": 0, "text": "",
           "chapters": [], "paragraphs": [], "anchor_used": False, "truncated": 0}
    rows = [t for t in (turns or []) if isinstance(t, dict)
            and isinstance(t.get("n"), int)]
    rows.sort(key=lambda t: t["n"])
    if not rows:
        return out
    prev = rows[-1]
    n = prev["n"]
    question = " ".join(str(prev.get("question") or "").split())
    answer, finished = _prev_answer(prev)
    chapters = _chapter_candidates(prev)
    index = _anchor_index(anchor, chapters)
    if index >= 0:
        chapters.insert(0, chapters.pop(index))
    paragraphs, blocks = _fill(chapters, budget)
    earlier = _earlier(rows[:-1])

    dropped = 0
    text = _render(n, question, answer, finished, chapters, blocks, earlier)
    while len(text) > budget:
        if earlier:
            earlier.pop()
        elif blocks:
            blocks.pop()
            paragraphs.pop()
        elif chapters:
            chapters.pop()
        elif answer:
            over = len(text) - budget
            answer = answer[:max(0, len(answer) - over - 2)].rstrip() + "…"
            if not answer.strip("…"):
                answer = ""
        elif question:
            over = len(text) - budget
            question = question[:max(0, len(question) - over - 2)].rstrip() + "…"
            if not question.strip("…"):
                question = ""
        else:
            break
        dropped += 1
        text = _render(n, question, answer, finished, chapters, blocks, earlier)
    if len(text) > budget:
        text = text[:budget]

    out.update({"prev": n, "chars": len(text), "text": text,
                "chapters": chapters, "paragraphs": paragraphs,
                "anchor_used": index >= 0, "truncated": dropped})
    return out
