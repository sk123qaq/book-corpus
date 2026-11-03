#!/usr/bin/env python3
"""检索流程演示页的本地服务。

  python3 apps/demo/server.py
  http://127.0.0.1:8765

只读 build/检索/index.db、build/library.db 与 books/**.md；不写书、账、脚本。
"""
import argparse
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import answer as answerer  # noqa: E402
import related  # noqa: E402
import session  # noqa: E402
import trace as tracer  # noqa: E402

WEB = os.path.join(HERE, "web")
MAX_BODY = 16 * 1024
LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


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


def _previous(sess, n):
    """第 n 问之前最近 3 轮，answer 只留前 300 字。"""
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
                     "answer": str(body or "")[:300]})
    return rows[-3:]


def _write_turn(sess, n, question, data, rel, anchor):
    """n 已经存在就替掉它并截断后面的轮次（重跑同一问），否则追加。"""
    turns = sess.setdefault("turns", [])
    turn = {"n": n, "question": question, "thinking": data.get("thinking") or "",
            "at": session.now(), "related": rel, "anchor": anchor,
            "trace": data, "answer": None, "context": None}
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
        if route not in ("/api/run", "/api/answer"):
            self.fail(404, "unknown api")
            return
        payload = self.read_payload()
        if payload is None:
            return
        if route == "/api/run":
            self.handle_run(payload)
        else:
            self.handle_answer(payload)

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
            self.fail(400, "path 为空")
            return
        self.api_answer(question, book, path, line, terms, groups, clean,
                        thinking=payload.get("thinking"),
                        use_cache=not _flag(payload.get("fresh")),
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
        self.send_json(200, {"ok": True, "session": sess})

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
            rel = None
            if sess and n > 1:
                rel = related.check(question, _previous(sess, n))
            if sess is None:
                sess = session.create()
            _write_turn(sess, n, question, data, rel, anchor)
            session.save(sess)
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
                   thinking=None, use_cache=True, session_id="", turn=None):
        sid, n, rel, ctx = "", 0, None, None
        try:
            if session_id:
                sid, n, rel, ctx = self.session_context(session_id, turn, sources)
            info = answerer.answer_question(question, book, path, line, terms,
                                            groups=groups, sources=sources,
                                            thinking=thinking, use_cache=use_cache,
                                            history=ctx["text"] if ctx else "")
            if sid and n:
                self.store_answer(sid, n, info, ctx)
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
        with SESSION_LOCK:
            sess = session.load(sid)
            turn = session.find(sess, n) if sess else None
            if turn is None:
                return
            turn["answer"] = dict(info)
            turn["context"] = ctx
            try:
                session.save(sess)
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
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
