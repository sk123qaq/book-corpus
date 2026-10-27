#!/usr/bin/env python3
"""检索流程演示页的本地服务。

  python3 工作区/演示-检索流程/server.py
  http://127.0.0.1:8765

只读 index.db / library.db 与 库/**.md；不写库、目录、脚本。
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
import trace as tracer  # noqa: E402

WEB = os.path.join(HERE, "web")
MAX_BODY = 16 * 1024
LOCK = threading.Lock()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


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
                self.serve_file("index.html", "text/html; charset=utf-8")
            elif route == "/style.css":
                self.serve_file("style.css", "text/css; charset=utf-8")
            elif route == "/app.js":
                self.serve_file("app.js", "application/javascript; charset=utf-8")
            elif route == "/api/health":
                self.api_health()
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
                     _flag(payload.get("fresh")))

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
                        use_cache=not _flag(payload.get("fresh")))

    # ---- API -------------------------------------------------------
    def api_health(self):
        try:
            info = tracer.meta()
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"ok": False, "db": "error", "error": str(exc)})
            return
        self.send_json(200, {"ok": True, "db": "ok", "meta": info})

    def api_run(self, question, select, thinking=None, fresh=False):
        if not LOCK.acquire(timeout=120):
            self.fail(503, "服务忙，稍后重试")
            return
        try:
            data = tracer.build(question, select, thinking=thinking, fresh=fresh)
            self.send_json(200, data)
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
                   thinking=None, use_cache=True):
        try:
            info = answerer.answer_question(question, book, path, line, terms,
                                            groups=groups, sources=sources,
                                            thinking=thinking, use_cache=use_cache)
            self.send_json(200, info)
        except ValueError as exc:
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))


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
