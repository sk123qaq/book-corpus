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
import sqlite3
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import trace as tracer  # noqa: E402
from answer import HISTORY_CHARS_MULTI, HISTORY_CHARS_SINGLE  # noqa: E402,F401

SESS_DIR = os.path.join(HERE, "缓存", "sessions")
LIBRARY_DB = tracer.LIB_DB
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

# 一轮回答的状态。pending 是刚跑完检索、还没人答；running 有任务在答。
# incomplete 与 run_error 只由旧会话派生：有 trace 没回答算没答完，连 trace
# 都没有说明那一轮没跑成。新写入的 turn 不用这两个值。
ANSWER_STATES = ("pending", "running", "ready", "error", "interrupted")
LEGACY_ANSWER_STATES = ("incomplete", "run_error")
INTERRUPTED_ERROR = "服务重启，上次回答已中断"

# 状态函数都要 load → 改 → save 一整套，串行化才不会被并发写盖掉。
STATE_LOCK = threading.RLock()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_id():
    return secrets.token_hex(6)


def new_run_token():
    """一次 /api/run 的标记。重跑同一问也换新的，旧任务凭它对不上号。"""
    return secrets.token_hex(8)


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
    for turn in sess["turns"]:
        _turn_defaults(turn)
    return sess


def _turn_defaults(turn):
    """补齐回答状态字段。旧会话没有这些字段，按已有答案与 trace 派生。"""
    if not isinstance(turn.get("run_token"), str):
        turn["run_token"] = ""
    if not isinstance(turn.get("answer_owner"), str):
        turn["answer_owner"] = ""
    turn["answer_error"] = str(turn.get("answer_error") or "")
    try:
        turn["answer_attempt"] = max(0, int(turn.get("answer_attempt") or 0))
    except (TypeError, ValueError):
        turn["answer_attempt"] = 0
    turn["answer_started_at"] = turn.get("answer_started_at") or None
    turn["answer_finished_at"] = turn.get("answer_finished_at") or None
    if turn.get("answer_state") in ANSWER_STATES + LEGACY_ANSWER_STATES:
        return
    ans = turn.get("answer")
    if isinstance(ans, dict):
        turn["answer_state"] = answer_state_for(ans)
        if turn["answer_state"] == "error" and not turn["answer_error"]:
            turn["answer_error"] = str(ans.get("error") or "回答失败")
    elif isinstance(turn.get("trace"), dict):
        turn["answer_state"] = "incomplete"
    else:
        turn["answer_state"] = "run_error"


def answer_state_for(info):
    """答案 dict 对应的状态：ok 明确为假才算 error。"""
    return "ready" if isinstance(info, dict) and info.get("ok") is not False else "error"


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
    with STATE_LOCK:
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


# ---- 回答状态 -------------------------------------------------------

def _turn_meta(sid, turn):
    return {"session_id": sid, "n": turn.get("n"),
            "run_token": turn.get("run_token") or "",
            "answer_state": turn.get("answer_state") or "pending",
            "answer_error": turn.get("answer_error") or "",
            "answer_attempt": turn.get("answer_attempt") or 0,
            "answer_owner": turn.get("answer_owner") or "",
            "answer_started_at": turn.get("answer_started_at"),
            "answer_finished_at": turn.get("answer_finished_at"),
            "answered": isinstance(turn.get("answer"), dict)}


def turn_meta(sid, n):
    """一轮的状态快照；会话或轮次不在时返回 None。只读。"""
    with STATE_LOCK:
        turn = find(load(sid), n)
        return _turn_meta(sid, turn) if turn is not None else None


def _token_matches(turn, token):
    return token is None or (turn.get("run_token") or "") == token


def begin_answer(sid, n, attempt, owner, token=None):
    """标成 running。轮次不在、token 已换（重新 /api/run）、attempt 旧了都返回 False。"""
    with STATE_LOCK:
        sess = load(sid)
        turn = find(sess, n)
        if turn is None or not _token_matches(turn, token):
            return False
        try:
            attempt = int(attempt)
        except (TypeError, ValueError):
            return False
        if attempt <= 0 or (turn.get("answer_attempt") or 0) > attempt:
            return False
        turn["answer_attempt"] = attempt
        turn["answer_state"] = "running"
        turn["answer_owner"] = str(owner or "")
        turn["answer_started_at"] = now()
        turn["answer_error"] = ""
        turn["answer_finished_at"] = None
        save(sess)
        return True


def finish_answer(sid, n, attempt, info, ctx=None, token=None):
    """写回答。只认当前 attempt（与 token，给了的话）；旧任务返回 False。"""
    with STATE_LOCK:
        sess = load(sid)
        turn = find(sess, n)
        if turn is None or not _token_matches(turn, token):
            return False
        if (turn.get("answer_attempt") or 0) != _attempt(attempt):
            return False
        info = dict(info) if isinstance(info, dict) else {}
        if not info:
            info = {"ok": False, "error": "回答为空"}
        turn["answer"] = info
        turn["context"] = ctx
        turn["answer_state"] = answer_state_for(info)
        turn["answer_error"] = ("" if turn["answer_state"] == "ready"
                                else str(info.get("error") or "回答失败"))
        turn["answer_finished_at"] = now()
        turn["answer_owner"] = ""
        save(sess)
        return True


def fail_answer(sid, n, attempt, message, token=None):
    """回答过程抛错。同样只认当前 attempt；不动已有的 answer。"""
    with STATE_LOCK:
        sess = load(sid)
        turn = find(sess, n)
        if turn is None or not _token_matches(turn, token):
            return False
        if (turn.get("answer_attempt") or 0) != _attempt(attempt):
            return False
        turn["answer_state"] = "error"
        turn["answer_error"] = str(message or "回答失败")
        turn["answer_finished_at"] = now()
        turn["answer_owner"] = ""
        save(sess)
        return True


def _attempt(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _owner_alive(owner):
    """owner 是一个仍存活的进程 id 时为真；解析不出、不算进程都当死。"""
    try:
        pid = int(str(owner).strip())
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def recover_interrupted():
    """启动时把上一个进程留下的 running 标成 interrupted。返回改动轮数。

    owner 是别的仍存活进程时不动（可能是另一个实例正在答）；owner 为空、
    等于当前进程（上一个同 pid 的进程已经没了）或已死才标。
    """
    me = str(os.getpid())
    count = 0
    with STATE_LOCK:
        for sid in _session_ids():
            sess = load(sid)
            if sess is None:
                continue
            changed = False
            for turn in sess["turns"]:
                if turn.get("answer_state") != "running":
                    continue
                owner = turn.get("answer_owner") or ""
                if owner and owner != me and _owner_alive(owner):
                    continue
                turn["answer_state"] = "interrupted"
                turn["answer_error"] = INTERRUPTED_ERROR
                turn["answer_finished_at"] = now()
                turn["answer_owner"] = ""
                changed = True
                count += 1
            if changed:
                save(sess)
    return count


def _session_ids():
    try:
        names = os.listdir(SESS_DIR)
    except OSError:
        return []
    return sorted(name[:-5] for name in names
                  if name.endswith(".json") and valid_id(name[:-5]))


def delete(sid):
    """删一个会话文件。返回 "deleted" 或 "missing"；id 不合法抛 ValueError。"""
    if not valid_id(sid):
        raise ValueError("会话 id 不合法")
    with STATE_LOCK:
        try:
            os.unlink(_path(sid))
        except FileNotFoundError:
            return "missing"
        return "deleted"


def public(sess):
    out = []
    for turn in (sess or {}).get("turns") or []:
        ans = turn.get("answer")
        lead = ans.get("lead") if isinstance(ans, dict) else ""
        out.append({"n": turn.get("n"),
                    "question": turn.get("question") or "",
                    "related": turn.get("related"),
                    "answer_state": turn.get("answer_state") or "pending",
                    "answer_error": turn.get("answer_error") or "",
                    "answer_lead": str(lead or "")})
    return {"id": (sess or {}).get("id") or "", "turns": out}


def _book_names(turn):
    """一轮涉及的书名，最多 3 个，去重。"""
    names, seen = [], set()

    def add(value):
        name = _book_name(value)
        if not name or name in seen:
            return
        seen.add(name)
        names.append(name)

    trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), dict) else {}
    for row in plan.get("ranking") or []:
        if isinstance(row, dict):
            add(row.get("title") or row.get("book"))
    if not names:
        ans = turn.get("answer")
        if isinstance(ans, dict):
            for cite in ans.get("citations") or []:
                if isinstance(cite, dict):
                    add(citation_title(turn, cite))
        if not names:
            result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
            add(result.get("book_title") or result.get("book"))
    return names[:3]


def summaries(limit=None):
    """历史会话摘要，按 updated_at 倒序。只读。

    跳过没有 turn 的文件、坏文件、非法 id。limit 为正整数时截断。
    """
    try:
        cap = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        cap = 0
    try:
        names = os.listdir(SESS_DIR)
    except OSError:
        names = []
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        sid = name[:-5]
        if not valid_id(sid):
            continue
        sess = load(sid)
        if sess is None:
            continue
        turns = sess.get("turns") or []
        if not turns:
            continue
        stamp = sess.get("updated_at") or turns[-1].get("at") or ""
        rows.append({"id": sid, "updated_at": str(stamp or ""),
                     "turns": len(turns),
                     "question": str(turns[0].get("question") or ""),
                     "last_question": str(turns[-1].get("question") or ""),
                     "answer_state": turns[-1].get("answer_state") or "pending",
                     "answer_error": turns[-1].get("answer_error") or "",
                     "books": _book_names(turns[-1])})
    rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    return rows[:cap] if cap > 0 else rows


# ---- 上下文 ---------------------------------------------------------

def _rel(path):
    raw = str(path or "").strip().replace("\\", "/")
    if not raw.startswith("books/") or not raw.endswith(".md"):
        return ""
    return raw


def _book_of(book, path):
    book = str(book or "").strip()
    if book:
        return book
    path = _rel(path)
    if path.startswith("books/") and "/md/" in path:
        return path.split("/")[1]
    return path[2:].split("/md/")[0] if "/md/" in path else ""


def _book_name(book):
    return str(book or "").strip().rsplit("/", 1)[-1]


def library_title(book):
    """build/library.db 的 book 表按 uid 取书名；查不到返回空串。"""
    book = str(book or "").strip()
    if not book:
        return ""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % LIBRARY_DB, uri=True)
        try:
            row = con.execute("SELECT title FROM book WHERE path = ?",
                              (book,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return str(row[0]).strip() if row and row[0] else ""


def book_title(turn, book):
    """从 turn 的 trace/answer 里找这本书的显示名，找不到退回 book。"""
    trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), dict) else {}
    for row in plan.get("ranking") or []:
        if isinstance(row, dict) and row.get("book") == book:
            title = str(row.get("title") or "").strip()
            if title:
                return title
    result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
    if not book or result.get("book") == book:
        title = str(result.get("book_title") or "").strip()
        if title:
            return title
    ans = turn.get("answer") if isinstance(turn.get("answer"), dict) else {}
    for src in ans.get("sources") or []:
        if isinstance(src, dict) and src.get("book") == book:
            title = str(src.get("title") or "").strip()
            if title:
                return title
    return str(book or "")


def citation_title(turn, cite):
    """一条引文的书名：已有值 → 本轮 trace → build/library.db → uid。"""
    book = _book_of(cite.get("book"), cite.get("path"))
    title = str(cite.get("book_title") or "").strip()
    if not title and book:
        title = book_title(turn, book)
    if (not title or title == book) and book:
        title = library_title(book) or title
    return title or book


def fill_citations(turn):
    """就地给 turn 的 answer.citations 补 book 与 book_title。

    其余字段（quote/line/path/verified）与顺序都不动。"""
    ans = turn.get("answer") if isinstance(turn, dict) else None
    cites = ans.get("citations") if isinstance(ans, dict) else None
    if not isinstance(cites, list):
        return turn
    for cite in cites:
        if not isinstance(cite, dict):
            continue
        book = _book_of(cite.get("book"), cite.get("path"))
        if book:
            cite["book"] = book
        cite["book_title"] = citation_title(turn, cite)
    return turn


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
