#!/usr/bin/env python3
"""8771 阅读工作台 本地服务。只绑 127.0.0.1，只用标准库。"""

import argparse
import contextlib
import json
import mimetypes
import os
import posixpath
import re
import sys
import urllib.parse

# 允许直接 ``python3 apps/reading/server.py``。
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bookcorpus import envfile, preflight
from bookcorpus.preflight import MissingConfigError
from bookcorpus.reading.model import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS
from bookcorpus.reading.preflight import SourceTooLarge
from bookcorpus.reading.service import ASSIST_ACTIONS, MissingProfileError, ReadingService
from bookcorpus.reading.store import UID_RE

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 1024 * 1024
HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")
ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
ROUTES = [
    (re.compile(r"^/api/shelf$"), "shelf"),
    (re.compile(r"^/api/catalog$"), "catalog"),
    (re.compile(r"^/api/book/([A-Za-z0-9_-]+)$"), "book"),
    (re.compile(r"^/api/book/([A-Za-z0-9_-]+)/reading$"), "book_reading"),
    (re.compile(r"^/api/chapter/([A-Za-z0-9_-]+)/(\d+)$"), "chapter"),
    (re.compile(r"^/api/chapter/([A-Za-z0-9_-]+)/(\d+)/reading$"), "reading"),
    (re.compile(r"^/api/assist$"), "assist"),
    (re.compile(r"^/api/assist/pin$"), "assist_pin"),
    (re.compile(r"^/api/assist/pin/([A-Za-z0-9_-]+)/(\d+)/([A-Za-z0-9_-]+)$"), "assist_unpin_full"),
    (re.compile(r"^/api/assist/pin/([A-Za-z0-9_-]+)$"), "assist_unpin"),
    (re.compile(r"^/api/chapter-map/([A-Za-z0-9_-]+)/(\d+)$"), "chapter_map"),
    (re.compile(r"^/api/records$"), "records"),
    (re.compile(r"^/api/asset$"), "asset"),
]


def local_ok(handler):
    host = handler.headers.get("Host", "")
    if not HOST_RE.match(host):
        return False
    origin = handler.headers.get("Origin")
    return not (origin is not None and not ORIGIN_RE.match(origin))


class Handler(BaseHTTPRequestHandler):
    service = None
    quiet = False

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- 基础 ---------------------------------------------------------
    def _send(self, status, body, content_type="application/json; charset=utf-8", head=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status >= 400:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, status, payload, head=False):
        self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8", head)

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()

    def _sse_event(self, event_name, payload):
        frame = f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        self.wfile.write(frame)
        self.wfile.flush()

    def _redirect(self, location, head=False):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _error(self, status, message, api=True, head=False):
        if api:
            self._json(status, {"error": message}, head)
        else:
            self._send(status, _page_404(message), "text/html; charset=utf-8", head)

    def _guard(self):
        if not local_ok(self):
            self._json(403, {"error": "只允许本机访问"})
            return False
        return True

    def _body(self, required=True):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length 非法") from exc
        if length <= 0:
            if required:
                raise ValueError("请求体为空")
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体超过 1MB")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体必须是 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ---- HTTP 入口 ----------------------------------------------------
    def do_GET(self):
        self._handle("GET", head=False)

    def do_HEAD(self):
        self._handle("GET", head=True)

    def do_POST(self):
        self._handle("POST", head=False)

    def do_PUT(self):
        self._handle("PUT", head=False)

    def do_DELETE(self):
        self._handle("DELETE", head=False)

    def _handle(self, method, head=False):
        if not self._guard():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if method == "POST":
            for pattern, name in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                if name == "assist":
                    self._post_assist()
                    return
                if name == "assist_pin":
                    self._post_assist_pin()
                    return
                if name == "chapter_map":
                    self._post_chapter_map(match.group(1), int(match.group(2)))
                    return
                self._json(405, {"error": "该路由不支持 POST"})
                return
            self._error(404, "没有这个接口")
            return

        if method == "PUT":
            for pattern, name in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                if name == "reading":
                    self._put_reading(match.group(1), int(match.group(2)))
                    return
                if name == "book_reading":
                    self._put_book_reading(match.group(1))
                    return
                self._json(405, {"error": "该路由不支持 PUT"})
                return
            self._error(404, "没有这个接口")
            return

        if method == "DELETE":
            for pattern, name in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                if name == "assist_unpin_full":
                    self._delete_assist_pin(match.group(1), int(match.group(2)), match.group(3))
                    return
                if name == "assist_unpin":
                    uid = (query.get("uid") or [""])[0]
                    n_raw = (query.get("n") or [""])[0]
                    if not uid or not n_raw:
                        body = self._body(required=False)
                        uid = uid or str(body.get("uid") or "")
                        n_raw = n_raw or str(body.get("n") or "")
                    if not uid or not str(n_raw).isdigit():
                        self._json(400, {"error": "请提供 uid 与章号 n"})
                        return
                    self._delete_assist_pin(uid, int(n_raw), match.group(1))
                    return
                self._json(405, {"error": "该路由不支持 DELETE"})
                return
            self._error(404, "没有这个接口")
            return

        # 页面
        if path == "/" or path == "/index.html":
            self._serve_page("shelf.html", head)
            return
        if path == "/catalog":
            self._serve_page("catalog.html", head)
            return
        if path == "/records":
            self._serve_page("records.html", head)
            return
        match = re.match(r"^/book/([A-Za-z0-9_-]+)$", path)
        if match:
            self._serve_page("book.html", head)
            return
        match = re.match(r"^/chapter/([A-Za-z0-9_-]+)/(\d+)$", path)
        if match:
            target = self.service.library.redirect_target(match.group(1), int(match.group(2)))
            if target:
                self._redirect("/chapter/%s/%02d" % (match.group(1), target), head)
                return
            self._serve_page("chapter.html", head)
            return
        if path.startswith("/assets/"):
            self._serve_asset_file(path, head)
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon", head)
            return

        # API
        for pattern, name in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            if name == "shelf":
                self._json(200, self.service.shelf(), head)
            elif name == "catalog":
                q = (query.get("q") or [""])[0]
                subject = (query.get("subject") or [""])[0]
                self._json(200, self.service.catalog(q, subject), head)
            elif name == "book":
                data = self.service.book(match.group(1))
                if data is None:
                    self._json(404, {"error": "没有这本书"}, head)
                else:
                    self._json(200, data, head)
            elif name == "chapter":
                data = self.service.chapter(match.group(1), int(match.group(2)))
                if data is None:
                    self._json(404, {"error": "没有这一章"}, head)
                else:
                    self._json(200, data, head)
            elif name == "records":
                uid = (query.get("uid") or [None])[0] or None
                action = (query.get("action") or [None])[0] or None
                status = (query.get("status") or [None])[0] or None
                try:
                    limit = max(1, min(1000, int((query.get("limit") or ["300"])[0])))
                except ValueError:
                    self._json(400, {"error": "limit 必须是整数"}, head)
                    return
                self._json(200, self.service.records(uid=uid, action=action, status=status, limit=limit), head)
            elif name == "asset":
                self._serve_asset_query(query, head)
            else:
                self._json(405, {"error": "该路由不支持 GET"}, head)
            return
        self._error(404, "没有这个页面或接口", api=path.startswith("/api/"), head=head)

    # ---- POST / PUT / DELETE 处理器 -----------------------------------
    def _post_assist(self):
        try:
            data = self._body()
            uid = str(data.get("uid") or "").strip()
            if not UID_RE.match(uid):
                raise ValueError("uid 非法")
            n = int(data.get("n"))
            action = str(data.get("action") or "").strip().lower()
            if action not in ASSIST_ACTIONS:
                raise ValueError("action 必须是 %s" % "/".join(ASSIST_ACTIONS))
            block_ids = data.get("block_ids") or []
            if not isinstance(block_ids, list) or not all(isinstance(x, str) for x in block_ids):
                raise ValueError("block_ids 必须是字符串数组")
            selected_text = str(data.get("selected_text") or "")
            question = str(data.get("question") or "")
            model = str(data.get("model") or DEFAULT_MODEL).strip()
            effort = str(data.get("effort") or DEFAULT_EFFORT).strip().lower()
            stream = data.get("stream", True)
            if not isinstance(stream, bool):
                raise ValueError("stream 必须是布尔值")
            if not model or len(model) > 80:
                raise ValueError("模型名非法")
            if effort not in EFFORTS:
                raise ValueError("reasoning_effort 只能是 %s" % "/".join(EFFORTS))
            # 先过存在性与凭据门控，错误仍回 404/400 JSON。
            if not self.service.library.book(uid) or not self.service.library.read_chapter(uid, n):
                raise FileNotFoundError("找不到章节")
            self.service._require_model_client()
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
            return
        except MissingConfigError as exc:
            payload = exc.as_payload()
            if "fix" not in payload and payload.get("missing"):
                payload["fix"] = payload["missing"][0].get("fix", "")
            self._json(400, payload)
            return
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return

        if not stream:
            try:
                result = self.service.assist(
                    uid,
                    n,
                    action,
                    block_ids=block_ids,
                    selected_text=selected_text,
                    question=question,
                    model=model,
                    effort=effort,
                )
            except FileNotFoundError:
                self._json(404, {"error": "找不到章节"})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(502, {"error": str(exc)})
            else:
                self._json(200, result)
            return

        self._sse_start()

        def emit_delta(chunk):
            with contextlib.suppress(OSError):
                self._sse_event("delta", {"text": chunk})

        try:
            result = self.service.assist(
                uid,
                n,
                action,
                block_ids=block_ids,
                selected_text=selected_text,
                question=question,
                model=model,
                effort=effort,
                on_delta=emit_delta,
            )
        except Exception as exc:
            with contextlib.suppress(OSError):
                self._sse_event("error", {"error": str(exc)})
            return

        try:
            if result.get("diagram_html"):
                self._sse_event(
                    "diagram",
                    {"html": result["diagram_html"], "figure": result.get("figure")},
                )
            self._sse_event("done", result)
        except OSError:
            pass

    def _post_assist_pin(self):
        try:
            data = self._body()
            uid = str(data.get("uid") or "").strip()
            if not UID_RE.match(uid):
                raise ValueError("uid 非法")
            n = int(data.get("n"))
            saved = self.service.pin_assist_note(uid, n, data)
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        else:
            self._json(200, saved)

    def _delete_assist_pin(self, uid, n, note_id):
        try:
            if not UID_RE.match(uid or ""):
                raise ValueError("uid 非法")
            removed = self.service.unpin_assist_note(uid, int(n), note_id)
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
            return
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        if removed is None:
            self._json(404, {"error": "旁批不存在"})
            return
        self._json(200, removed)

    def _post_chapter_map(self, uid, n):
        try:
            if not UID_RE.match(uid or ""):
                raise ValueError("uid 非法")
            data = self._body(required=False)
            force = data.get("force", False)
            if not isinstance(force, bool):
                raise ValueError("force 必须是布尔值")
            model = str(data.get("model") or DEFAULT_MODEL).strip()
            effort = str(data.get("effort") or DEFAULT_EFFORT).strip().lower()
            if not model or len(model) > 80:
                raise ValueError("模型名非法")
            if effort not in EFFORTS:
                raise ValueError("reasoning_effort 只能是 %s" % "/".join(EFFORTS))
            result = self.service.generate_chapter_map(uid, n, force=force, model=model, effort=effort)
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
        except SourceTooLarge as exc:
            self._json(422, exc.to_dict())
        except MissingConfigError as exc:
            self._json(400, exc.as_payload())
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(502, {"error": str(exc)})
        else:
            self._json(200, result)

    def _put_reading(self, uid, n):
        try:
            data = self._body()
            action = str(data.get("action") or "").strip()
            mode = data.get("mode")
            result = self.service.set_reading(uid, n, action, mode)
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
        except MissingProfileError as exc:
            self._json(409, {"error": str(exc), "code": "reading_profile_required", "uid": exc.uid})
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        else:
            self._json(200, result)

    def _put_book_reading(self, uid):
        try:
            data = self._body()
            result = self.service.set_book_reading(uid, data.get("mode"))
        except FileNotFoundError:
            self._json(404, {"error": "找不到这本书"})
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        else:
            self._json(200, result)

    # ---- 静态与原文附件 -----------------------------------------------
    def _serve_page(self, name, head=False):
        path = os.path.join(WEB, name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._error(404, "页面文件缺失", api=False, head=head)
            return
        self._send(200, body, "text/html; charset=utf-8", head)

    def _serve_asset_file(self, path, head=False):
        rel = path[len("/assets/") :]
        safe = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.realpath(os.path.join(WEB, "assets", safe))
        root = os.path.realpath(os.path.join(WEB, "assets"))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            self._error(404, "资源不存在", api=False, head=head)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, head)

    def _serve_asset_query(self, query, head=False):
        uid = (query.get("uid") or [""])[0]
        rel = (query.get("rel") or [""])[0]
        if not UID_RE.match(uid) or not rel:
            self._json(400, {"error": "uid/rel 非法"}, head)
            return
        md_dir = os.path.realpath(self.service.library.layout.md(uid))
        full = os.path.realpath(os.path.join(md_dir, rel))
        if not full.startswith(md_dir + os.sep) or not os.path.isfile(full):
            self._json(404, {"error": "资源不存在"}, head)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, head)


def _page_404(message):
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>404 · 阅读工作台</title><style>
body{background:#F3F0E7;color:#181817;font:16px/1.8 "Noto Serif CJK SC",serif;margin:0;
display:grid;place-items:center;min-height:100vh}
main{max-width:560px;padding:40px;border-top:3px solid #A8442F}
h1{font-size:28px;margin:0 0 12px}a{color:#1E3A36}
</style><main><h1>没有找到</h1><p>%s</p>
<p><a href="/">← 回书架</a> · <a href="/records">操作日志</a></p></main></html>""" % (
        message.replace("&", "&amp;").replace("<", "&lt;")
    )


def main(argv=None):
    envfile.load()
    parser = argparse.ArgumentParser(description="8771 阅读工作台 本地服务")
    parser.add_argument("--root", default=os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/书库"))
    parser.add_argument("--port", type=int, default=8771)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--annotate",
        dest="annotate",
        action="store_true",
        default=os.environ.get("READING_ANNOTATE", "1") != "0",
        help="保留启动参数兼容（伴读与地图按需调用模型）",
    )
    group.add_argument("--no-annotate", dest="annotate", action="store_false", help="仅测试用途")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        service = ReadingService(args.root, annotate=args.annotate)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    if not os.path.isdir(service.library.layout.books):
        parser.error("根下没有 books/：%s" % args.root)
    Handler.service = service
    Handler.quiet = args.quiet
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("阅读工作台 8771 · root=%s · http://127.0.0.1:%d/" % (service.library.root, args.port), flush=True)
    warning = preflight.summary(service.library.root)
    if warning:
        print("配置提示：" + warning + "；book-corpus doctor 看全部", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
