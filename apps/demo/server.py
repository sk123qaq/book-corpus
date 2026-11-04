#!/usr/bin/env python3
"""检索流程演示页的本地服务。

  python3 apps/demo/server.py
  http://127.0.0.1:8765

只读 build/检索/index.db、build/library.db 与 books/**.md；不写书、账、脚本。
"""
import argparse
import copy
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import answer as answerer  # noqa: E402
import export_session  # noqa: E402
import related  # noqa: E402
import session  # noqa: E402
import trace as tracer  # noqa: E402

WEB = os.path.join(HERE, "web")
MAX_BODY = 16 * 1024
LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()

# 同一轮（会话 + 轮次 + run_token）只跑一个回答任务。第一个请求负责调模型，
# 同时进来的第二个请求等它的 Event，拿到同一份结果，不再调一次模型。
ANSWER_JOBS = {}
ANSWER_JOBS_LOCK = threading.Lock()
ANSWER_WAIT_SECONDS = 300
ANSWER_JOB_TTL = 60
MAX_DELETE = 100


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def answer_job_key(sid, n, run_token):
    """同轮回答的键。重新 /api/run 会换 run_token，自然另起一个任务。"""
    return (str(sid or ""), int(n or 0), str(run_token or ""))


def _purge_answer_jobs(at):
    for key in [k for k, job in ANSWER_JOBS.items()
                if job["event"].is_set() and job["expires"] and job["expires"] <= at]:
        ANSWER_JOBS.pop(key, None)


def claim_answer_job(key, attempt=0, fresh=False):
    """连同轮任务。返回 (job, owner)；owner 为真时由调用方跑模型并 publish。
    已有任务且还没结束时一律加入；已结束的任务只有 fresh 才重开，
    否则普通重复请求复用上一次的结果。"""
    with ANSWER_JOBS_LOCK:
        _purge_answer_jobs(time.time())
        job = ANSWER_JOBS.get(key)
        if job is not None:
            if not job["event"].is_set() or not fresh:
                return job, False
            # 用户点了「重试回答」：旧任务只留给还拿着它引用的等待者，
            # 这次重新调模型，不能把上一次的错误或结果当成这一次的回答。
            ANSWER_JOBS.pop(key, None)
        job = {"event": threading.Event(), "result": None, "error": None,
               "attempt": attempt, "expires": 0.0}
        ANSWER_JOBS[key] = job
        return job, True


def publish_answer_job(job, result=None, error=None):
    """写结果、开 Event。job 再保留 ANSWER_JOB_TTL 秒，迟到请求还能复用。"""
    job["result"] = result
    job["error"] = error
    job["expires"] = time.time() + ANSWER_JOB_TTL
    job["event"].set()


def _session_id(value):
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _turn_no(value):
    """请求里的 turn：缺省、布尔或非法值一律当没给。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _clean_anchor(value):
    """anchor 只在 books/ 下的 .md + 正整数行号时采用，其余忽略。"""
    if not isinstance(value, dict):
        return None
    try:
        real = tracer.safe_md_path(value.get("path"))
    except (ValueError, OSError):
        return None
    try:
        line = int(value.get("line"))
    except (TypeError, ValueError):
        return None
    if line <= 0:
        return None
    title = value.get("title")
    quote = value.get("quote")
    return {"path": os.path.relpath(real, tracer.ROOT).replace(os.sep, "/"),
            "line": line,
            "title": title.strip() if isinstance(title, str) else "",
            "quote": quote.strip() if isinstance(quote, str) else ""}


def _clean_focus(value):
    """请求里的 focus 只留 status/book/title/best_path/best_line/candidates。

    前端（或上一轮 trace）传什么对象都不直接往下传，只取这几个字段。
    """
    if not isinstance(value, dict):
        return None

    def text(src, key, limit=200):
        raw = src.get(key)
        return raw.strip()[:limit] if isinstance(raw, str) else ""

    try:
        best_line = max(1, int(value.get("best_line") or 1))
    except (TypeError, ValueError):
        best_line = 1
    out = {"status": text(value, "status", 20), "book": text(value, "book", 40),
           "title": text(value, "title"), "best_path": text(value, "best_path", 500),
           "best_line": best_line}
    candidates = []
    rows = value.get("candidates")
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or len(candidates) >= answerer.MAX_SOURCES:
            continue
        book = text(item, "book", 40)
        if not book:
            continue
        try:
            line = max(1, int(item.get("line") or 1))
        except (TypeError, ValueError):
            line = 1
        candidates.append({"book": book, "title": text(item, "title"),
                           "path": text(item, "path", 500), "line": line})
    if candidates:
        out["candidates"] = candidates
    if not (out["status"] or out["book"]):
        return None
    return out


def _focus_path(focus):
    """focus 里能落到具体文件的那一条：先 best_path，再看与 book 同号的候选。"""
    if not focus or focus.get("status") != "exact":
        return "", 1
    if focus.get("best_path"):
        return focus["best_path"], focus.get("best_line") or 1
    for item in focus.get("candidates") or []:
        if item.get("book") == focus.get("book") and item.get("path"):
            return item["path"], item.get("line") or 1
    return "", 1


def _previous(sess, n):
    """第 n 问之前最近 3 轮，answer 传全文（related 需要读到回答尾部）。"""
    rows = []
    for turn in sess.get("turns") or []:
        if turn.get("n", 0) >= n:
            continue
        ans = turn.get("answer")
        body = ans.get("answer") if isinstance(ans, dict) else ""
        if not body:
            trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
            result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
            body = result.get("text") or ""
        rows.append({"n": turn["n"], "question": turn.get("question") or "",
                     "answer": str(body or "")})
    return rows[-3:]


def _book_title(turn, book):
    """从 trace/answer 里找这本书的显示名，找不到退回 book。"""
    return session.book_title(turn, book)


def _fill_citations(payload):
    """就地把响应顶层 citations 补齐 book/book_title，其它字段不动。"""
    if isinstance(payload, dict) and isinstance(payload.get("citations"), list):
        trace = payload if isinstance(payload.get("plan"), dict) else {"result": payload}
        session.fill_citations({"answer": payload, "trace": trace})
    return payload


def _prev_turn(sess, n):
    """第 n 问之前最近的一轮；没有就返回 None。"""
    turns = [t for t in sess.get("turns") or [] if t.get("n", 0) < n]
    return max(turns, key=lambda t: t.get("n", 0)) if turns else None


def _previous_source(sess, n):
    """第 n 问之前最近一轮最可信的落点：citation → 选章 → result。
    返回 {"book","book_title","title","path","line"} 或 None。"""
    prev = _prev_turn(sess, n)
    if prev is None:
        return None
    rows = session._chapter_candidates(prev)
    if not rows:
        return None
    src = rows[0]
    book = str(src.get("book") or "")
    return {"book": book, "book_title": _book_title(prev, book),
            "title": src.get("title") or "", "path": src.get("path"),
            "line": src.get("line")}


def _pin_rows(prev, book):
    """上一轮 plan 里同书的 ranking / chapters 行（深拷贝）；没有就是 None。"""
    trace = prev.get("trace") if isinstance(prev.get("trace"), dict) else {}
    plan = trace.get("plan") if isinstance(trace.get("plan"), dict) else {}
    out = []
    for key in ("ranking", "chapters"):
        row = None
        for item in plan.get(key) or []:
            if isinstance(item, dict) and item.get("book") == book:
                row = copy.deepcopy(item)
                break
        out.append(row)
    return out[0], out[1]


def _pin_followup(data, sess, n, rel, anchor):
    """related 且没点 anchor 时，把 trace.result 换回上一轮的书。
    不满足条件、找不到落点、读章节失败时原样返回 data（同一对象）。
    这一问已经点名了新的书（focus exact/ambiguous，或带了 query_title）时不钉：
    钉回旧书会和点名冲突。"""
    focus = data.get("focus")
    if isinstance(focus, dict) and (
            focus.get("status") in ("exact", "ambiguous") or focus.get("query_title")):
        return data
    if not (isinstance(rel, dict) and rel.get("related")) or anchor:
        return data
    src = _previous_source(sess, n)
    if not src:
        return data
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    try:
        same = (result.get("path") == src["path"]
                and int(result.get("line") or 0) == src["line"])
    except (TypeError, ValueError):
        same = False
    if same:
        return data
    try:
        window = tracer.read_chapter(src["path"], src["line"], tracer.STAGE_CHARS)
    except (ValueError, OSError):
        return data
    info = {"book": src["book"], "book_title": src["book_title"],
            "chapter": src["title"] or result.get("title") or result.get("chapter") or "",
            "card": 0}
    data["result"] = dict(window, **info)
    prev = _prev_turn(sess, n)
    rank_seed, chapter_seed = _pin_rows(prev, src["book"]) if prev else (None, None)
    plan = data.get("plan")
    if isinstance(plan, dict):
        for key, seed in (("ranking", rank_seed), ("chapters", chapter_seed)):
            rows = plan.get(key)
            rows = rows if isinstance(rows, list) else []
            hit = [r for r in rows
                   if isinstance(r, dict) and r.get("book") == src["book"]]
            rest = [r for r in rows
                    if not (isinstance(r, dict) and r.get("book") == src["book"])]
            if not hit:
                if seed is None:
                    if key == "ranking":
                        seed = {"book": src["book"],
                                "title": src["book_title"] or src["book"]}
                    else:
                        seed = {"book": src["book"],
                                "title": src["book_title"] or src["book"],
                                "selected": [{"chapter_path": src["path"],
                                              "chapter_title": src["title"],
                                              "line": src["line"], "score": 0}]}
                seed = dict(seed, pinned=True)
                hit = [seed]
            plan[key] = hit + rest
    data["pin"] = {"book": src["book"], "path": src["path"], "line": src["line"],
                   "from_turn": prev.get("n") if prev else None}
    return data


def _inherit_focus(data, sess, n, rel):
    """related 的追问没点名时，把上一轮点名的书抄过来（标 inherited）。

    当前已经是 exact 的不动；上一轮不是 exact、或没有 trace.focus 时也不动。
    """
    if not (isinstance(rel, dict) and rel.get("related")):
        return data
    cur = data.get("focus") if isinstance(data.get("focus"), dict) else {}
    # 只有“纯这本书”的追问才继承：当前没点名（missing 且 query_title 为空）。
    # 当前 exact/ambiguous，或点名了一个书库没有的标题，都不能拿上一轮的书顶替。
    if cur and (cur.get("status") != "missing" or cur.get("query_title")):
        return data
    prev = _prev_turn(sess, n)
    if prev is None:
        return data
    trace = prev.get("trace") if isinstance(prev.get("trace"), dict) else {}
    old = trace.get("focus") if isinstance(trace.get("focus"), dict) else {}
    if old.get("status") != "exact" or not old.get("book"):
        return data
    data["focus"] = dict(old, inherited=True)
    return data


def _write_turn(sess, n, question, data, rel, anchor):
    """n 已经存在就替掉它并截断后面的轮次（重跑同一问），否则追加。

    每个 turn 带一个新的 run_token：旧回答任务即使晚到也认不上这个 token。
    """
    turns = sess.setdefault("turns", [])
    turn = {"n": n, "question": question, "thinking": data.get("thinking") or "",
            "at": session.now(), "related": rel, "anchor": anchor,
            "trace": data, "answer": None, "context": None,
            "run_token": session.new_run_token(), "answer_state": "pending",
            "answer_error": "", "answer_started_at": None,
            "answer_finished_at": None, "answer_attempt": 0,
            "answer_owner": ""}
    index = next((i for i, t in enumerate(turns) if t.get("n") == n), -1)
    if index >= 0:
        turns[index:] = [turn]
    else:
        turns.append(turn)
    return turn


def _flag(value):
    """请求里的布尔开关：接受 true/false、"1"/"0" 与数字，别的算 false。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "BookTrace/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ---------------------------------------------------
    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if code >= 400:
            # 出错路径可能没读尽请求体（404 未知接口、400 超大 JSON）。HTTP/1.1
            # keep-alive 下残余 body 会被下一条请求当请求行解析，断开才不失步。
            self.close_connection = True
        if isinstance(body, (dict, list)):
            body = json_bytes(body)
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        if code >= 400:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, val in (extra or {}).items():
            self.send_header(key, val)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, code, value):
        self.send(code, value)

    def fail(self, code, message):
        self.send_json(code, {"ok": False, "error": message})

    def serve_file(self, name, ctype):
        path = os.path.join(WEB, name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.fail(404, "static file missing: " + name)
            return
        self.send(200, body, ctype)

    # ---- GET -------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        try:
            if route in ("/", "/index.html"):
                self.serve_file("session.html", "text/html; charset=utf-8")
            elif route in ("/classic", "/classic.html"):
                self.serve_file("index.html", "text/html; charset=utf-8")
            elif route in ("/session", "/session.html"):
                self.serve_file("session.html", "text/html; charset=utf-8")
            elif route == "/style.css":
                self.serve_file("style.css", "text/css; charset=utf-8")
            elif route == "/session.css":
                self.serve_file("session.css", "text/css; charset=utf-8")
            elif route == "/app.js":
                self.serve_file("app.js", "application/javascript; charset=utf-8")
            elif route == "/session.js":
                self.serve_file("session.js", "application/javascript; charset=utf-8")
            elif route == "/api/health":
                self.api_health()
            elif route == "/api/sessions":
                self.api_sessions(parse_qs(parsed.query))
            elif route == "/api/session":
                self.api_session(parse_qs(parsed.query))
            elif route == "/api/export":
                self.api_export(parse_qs(parsed.query))
            elif route == "/api/chapter":
                self.api_chapter(parse_qs(parsed.query))
            elif route == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
            elif route.startswith("/api/"):
                self.fail(404, "unknown api")
            else:
                self.fail(404, "not found")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))

    def do_POST(self):
        route = unquote(urlparse(self.path).path)
        if route not in ("/api/run", "/api/answer", "/api/sessions/delete"):
            self.fail(404, "unknown api")
            return
        payload = self.read_payload()
        if payload is None:
            return
        if route == "/api/run":
            self.handle_run(payload)
        elif route == "/api/answer":
            self.handle_answer(payload)
        else:
            self.handle_sessions_delete(payload)

    def handle_sessions_delete(self, payload):
        ids = payload.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DELETE:
            self.fail(400, "ids 必须是 1 到 %d 个会话 id 的数组" % MAX_DELETE)
            return
        clean = []
        for sid in ids:
            if not session.valid_id(sid):
                self.fail(400, "会话 id 不合法")
                return
            if sid not in clean:
                clean.append(sid)
        deleted, missing = [], []
        for sid in clean:
            try:
                outcome = session.delete(sid)
            except OSError as exc:  # noqa: BLE001
                traceback.print_exc()
                self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))
                return
            (deleted if outcome == "deleted" else missing).append(sid)
        self.send_json(200, {"ok": True, "deleted": deleted, "missing": missing,
                             "count": len(deleted)})

    def read_payload(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self.fail(400, "请求体为空或过大")
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.fail(400, "请求体不是合法 JSON")
            return None
        if not isinstance(payload, dict):
            self.fail(400, "请求体必须是 JSON 对象")
            return None
        return payload

    def handle_run(self, payload):
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            self.fail(400, "question 为空")
            return
        question = question.strip()
        if len(question) > 300:
            self.fail(400, "问题太长（上限 300 字）")
            return
        select = payload.get("select") or 0
        try:
            select = int(select)
        except (TypeError, ValueError):
            select = 0
        self.api_run(question, select, payload.get("thinking"),
                     _flag(payload.get("fresh")),
                     session_id=_session_id(payload.get("session_id")),
                     turn=_turn_no(payload.get("turn")),
                     anchor=_clean_anchor(payload.get("anchor")))

    def handle_answer(self, payload):
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            self.fail(400, "question 为空")
            return
        question = question.strip()
        if len(question) > 300:
            self.fail(400, "问题太长（上限 300 字）")
            return
        path = payload.get("path")
        path = path.strip() if isinstance(path, str) else ""
        try:
            line = int(payload.get("line") or 1)
        except (TypeError, ValueError):
            line = 1
        book = payload.get("book")
        book = book.strip() if isinstance(book, str) else ""
        terms = payload.get("terms") or []
        if isinstance(terms, str):
            terms = [terms]
        if not isinstance(terms, list):
            terms = []
        terms = [t.strip() for t in terms if isinstance(t, str) and t.strip()][:12]
        groups = payload.get("groups") or []
        if not isinstance(groups, list):
            groups = []
        groups = [g for g in groups if isinstance(g, dict) and isinstance(g.get("variants"), list)]
        sources = payload.get("sources")
        if sources is None:
            sources = []
        if not isinstance(sources, list) or len(sources) > answerer.MAX_SOURCES:
            self.fail(400, "sources 必须是数组，最多 %d 本" % answerer.MAX_SOURCES)
            return
        focus = _clean_focus(payload.get("focus"))
        clean = []
        for item in sources:
            if not isinstance(item, dict):
                self.fail(400, "sources 每项必须是对象")
                return
            spath = item.get("path")
            sline = item.get("line")
            stitle = item.get("title")
            if not isinstance(spath, str) or not spath.strip():
                self.fail(400, "sources 每项要有 path")
                return
            if not isinstance(sline, int) or isinstance(sline, bool):
                self.fail(400, "sources 每项的 line 必须是整数")
                return
            if stitle is not None and not isinstance(stitle, str):
                self.fail(400, "sources 每项的 title 必须是字符串")
                return
            clean.append({"path": spath.strip(), "line": sline,
                          "title": (stitle or "").strip()})
        if clean:
            path, line = clean[0]["path"], clean[0]["line"]
        elif not path:
            path, line = _focus_path(focus)
            if not path:
                self.fail(400, "path 为空")
                return
            book = book or focus["book"]
        fresh = _flag(payload.get("fresh"))
        self.api_answer(question, book, path, line, terms, groups, clean,
                        focus=focus, thinking=payload.get("thinking"),
                        use_cache=not fresh, fresh=fresh,
                        session_id=_session_id(payload.get("session_id")),
                        turn=_turn_no(payload.get("turn")))

    # ---- API -------------------------------------------------------
    def api_health(self):
        try:
            info = tracer.meta()
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"ok": False, "db": "error", "error": str(exc)})
            return
        if info["index"] != "ok":
            message = "未建索引" if info["index"] == "missing" else "索引为空"
            self.send_json(200, {"ok": True, "db": info["index"], "degraded": True,
                                 "message": message, "meta": info})
            return
        if info["library"] != "ok":
            self.send_json(200, {"ok": True, "db": "missing", "degraded": True,
                                 "message": "未建书目库", "meta": info})
            return
        self.send_json(200, {"ok": True, "db": "ok", "meta": info})

    def api_sessions(self, query):
        try:
            limit = int((query.get("limit") or ["0"])[0])
        except ValueError:
            limit = 0
        rows = session.summaries(limit if 1 <= limit <= 500 else None)
        self.send_json(200, {"ok": True, "count": len(rows), "sessions": rows})

    def api_session(self, query):
        sid = (query.get("id") or [""])[0]
        if not session.valid_id(sid):
            self.fail(400, "会话 id 不合法")
            return
        sess = session.load(sid)
        if sess is None:
            self.fail(404, "会话不存在")
            return
        for turn in sess.get("turns") or []:
            session.fill_citations(turn)
        self.send_json(200, {"ok": True, "session": sess})

    def api_export(self, query):
        sid = (query.get("id") or [""])[0]
        if not session.valid_id(sid):
            self.fail(400, "会话 id 不合法")
            return
        sess = session.load(sid)
        if sess is None:
            self.fail(404, "会话不存在")
            return
        fmt = ((query.get("format") or ["md"])[0] or "md").strip().lower()
        if fmt not in ("md", "docx"):
            self.fail(400, "format 只支持 md 或 docx")
            return
        for turn in sess.get("turns") or []:
            session.fill_citations(turn)
        body, ctype, headers = export_session.response(sess, sid, fmt)
        self.send(200, body, ctype, headers)

    def api_run(self, question, select, thinking=None, fresh=False,
                session_id="", turn=None, anchor=None):
        if not LOCK.acquire(timeout=120):
            self.fail(503, "服务忙，稍后重试")
            return
        try:
            sess, n = None, 1
            if session_id:
                sess = session.load(session_id)
                if sess is None:
                    self.fail(404, "会话不存在")
                    return
                total = len(sess.get("turns") or [])
                n = turn or total + 1
                if not 1 <= n <= total + 1:
                    self.fail(400, "turn 超出会话范围")
                    return
            data = tracer.build(question, select, thinking=thinking, fresh=fresh)
            _fill_citations(data)
            rel = None
            if sess and n > 1:
                rel = related.check(question, _previous(sess, n))
                data = _pin_followup(data, sess, n, rel, anchor)
                data = _inherit_focus(data, sess, n, rel)
            if sess is None:
                sess = session.create()
                _write_turn(sess, n, question, data, rel, anchor)
                session.save(sess)
            else:
                # 重新读一遍再写：这期间可能刚完成一个回答，不能拿旧快照盖掉它。
                with session.STATE_LOCK:
                    current = session.load(sess["id"])
                    if current is None:
                        self.fail(404, "会话不存在")
                        return
                    _write_turn(current, n, question, data, rel, anchor)
                    session.save(current)
                sess = current
            self.send_json(200, dict(data, session_id=sess["id"], turn=n,
                                     related=rel, session=session.public(sess)))
        except ValueError as exc:
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))
        finally:
            LOCK.release()

    def api_chapter(self, query):
        path = (query.get("path") or [""])[0]
        try:
            line = int((query.get("line") or ["1"])[0])
        except ValueError:
            line = 1
        try:
            chars = int((query.get("chars") or ["900"])[0])
        except ValueError:
            chars = 900
        chars = max(200, min(chars, 4000))
        try:
            before = int((query.get("before") or ["700"])[0])
        except ValueError:
            before = 700
        before = max(0, min(before, 3000))
        try:
            info = tracer.read_chapter(path, line, chars, before)
        except ValueError as exc:
            self.fail(400, str(exc))
            return
        except OSError as exc:
            self.fail(404, "读取失败：%s" % exc.__class__.__name__)
            return
        info["ok"] = True
        self.send_json(200, info)

    def api_answer(self, question, book, path, line, terms, groups, sources=None,
                   focus=None, thinking=None, use_cache=True, session_id="",
                   turn=None, fresh=False):
        sid, n, rel, ctx = "", 0, None, None
        if session_id:
            sid, n, rel, ctx = self.session_context(session_id, turn, sources)
        meta = session.turn_meta(sid, n) if sid and n else None
        if meta is None:
            # 没带会话、或会话里没这一轮：老的同路无状态路径。
            self.answer_once(question, book, path, line, terms, groups, sources,
                             focus, thinking, use_cache, ctx, sid, n, rel)
            return
        key = answer_job_key(sid, n, meta["run_token"])
        attempt = (meta["answer_attempt"] or 0) + 1
        job, owner = claim_answer_job(key, attempt, fresh=fresh)
        if not owner:
            if not job["event"].wait(ANSWER_WAIT_SECONDS):
                self.fail(504, "回答仍在生成，请稍后刷新")
                return
            if job["error"]:
                self.fail(*job["error"])
            elif job["result"] is None:
                self.fail(500, "回答任务没有结果")
            else:
                self.send_json(200, job["result"])
            return
        token = meta["run_token"]
        if not session.begin_answer(sid, n, attempt, os.getpid(), token=token):
            message = "这一轮已重跑，本次回答作废"
            self.fail(409, message)
            publish_answer_job(job, error=(409, message))
            return
        try:
            info = answerer.answer_question(question, book, path, line, terms,
                                            groups=groups, sources=sources,
                                            thinking=thinking, use_cache=use_cache,
                                            history=ctx["text"] if ctx else "",
                                            focus=focus)
            _fill_citations(info)
            if not session.finish_answer(sid, n, attempt, info, ctx, token=token):
                # 这一轮在这期间被重跑了，结果不再落地，也不发给等待者。
                message = "这一轮已重跑，本次回答作废"
                publish_answer_job(job, error=(409, message))
                self.fail(409, message)
                return
            body = dict(info, session_id=sid, turn=n, related=rel,
                        history=self.history_brief(ctx), run_token=token,
                        answer_state=session.answer_state_for(info))
            publish_answer_job(job, result=body)
            self.send_json(200, body)
        except ValueError as exc:
            session.fail_answer(sid, n, attempt, str(exc), token=token)
            publish_answer_job(job, error=(400, str(exc)))
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            message = "%s: %s" % (exc.__class__.__name__, exc)
            session.fail_answer(sid, n, attempt, message, token=token)
            publish_answer_job(job, error=(500, message))
            self.fail(500, message)

    def answer_once(self, question, book, path, line, terms, groups, sources,
                    focus, thinking, use_cache, ctx, sid, n, rel):
        """不带会话状态的回答：照旧调一次模型并把结果发回去。"""
        try:
            info = answerer.answer_question(question, book, path, line, terms,
                                            groups=groups, sources=sources,
                                            thinking=thinking, use_cache=use_cache,
                                            history=ctx["text"] if ctx else "",
                                            focus=focus)
            _fill_citations(info)
            self.send_json(200, dict(info, session_id=sid, turn=n, related=rel,
                                     history=self.history_brief(ctx)))
        except ValueError as exc:
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))

    @staticmethod
    def history_brief(ctx):
        return {"used": bool(ctx),
                "prev": ctx["prev"] if ctx else 0,
                "chars": ctx["chars"] if ctx else 0,
                "budget": ctx["budget"] if ctx else 0,
                "chapters": len(ctx["chapters"]) if ctx else 0,
                "paragraphs": len(ctx["paragraphs"]) if ctx else 0}

    def session_context(self, session_id, turn, sources):
        """定位这一问，related 为真时拼上一轮上下文。会话找不到就当没带。"""
        with SESSION_LOCK:
            sess = session.load(session_id)
            if sess is None:
                return "", 0, None, None
            turns = sess.get("turns") or []
            n = turn or len(turns)
            current = session.find(sess, n)
            if current is None:
                return "", 0, None, None
            rel = current.get("related")
            if not (isinstance(rel, dict) and rel.get("related")):
                return sess["id"], n, rel, None
            budget = (session.HISTORY_CHARS_MULTI if len(sources or []) >= 2
                      else session.HISTORY_CHARS_SINGLE)
            ctx = session.build_context([t for t in turns if t.get("n", 0) < n],
                                        budget, anchor=current.get("anchor"))
            return sess["id"], n, rel, ctx

    def store_answer(self, sid, n, info, ctx):
        """兼容旧调用：写一轮回答，不带 attempt 时写当前轮。"""
        meta = session.turn_meta(sid, n)
        if not meta:
            return
        session.finish_answer(sid, n, meta["answer_attempt"], info, ctx,
                              token=meta["run_token"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    try:
        count = session.recover_interrupted()
        print("启动恢复：%d 轮上次未完成的回答已标为 interrupted" % count,
              flush=True)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print("书库检索演示：http://%s:%d" % (args.host, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
