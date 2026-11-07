#!/usr/bin/env python3
"""8771 四读 本地服务。只绑 127.0.0.1，只用标准库。"""

import argparse
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

from bookcorpus.reading.editorial import EditorialError
from bookcorpus.reading.model import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS
from bookcorpus.reading.service import (
    JobConflictError,
    PackExistsError,
    ReadingService,
    RebuildRequiredError,
)
from bookcorpus.reading.store import UID_RE

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 1024 * 1024
HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")
ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
ROUTES = [
    (re.compile(r"^/api/shelf$"), "shelf"),
    (re.compile(r"^/api/catalog$"), "catalog"),
    (re.compile(r"^/api/book/([A-Za-z0-9_-]+)$"), "book"),
    (re.compile(r"^/api/chapter/([A-Za-z0-9_-]+)/(\d+)$"), "chapter"),
    (re.compile(r"^/api/records$"), "records"),
    (re.compile(r"^/api/jobs$"), "jobs"),
    (re.compile(r"^/api/jobs/([A-Za-z0-9_-]+)$"), "job"),
    (re.compile(r"^/api/jobs/([A-Za-z0-9_-]+)/cancel$"), "cancel"),
    (re.compile(r"^/api/export/([A-Za-z0-9_-]+)/(\d+)/([a-z]+)$"), "export"),
    (re.compile(r"^/api/asset$"), "asset"),
    (re.compile(r"^/api/editorial/([A-Za-z0-9_-]+)/(\d+)$"), "editorial"),
    (re.compile(r"^/api/edition/([A-Za-z0-9_-]+)/(\d+)$"), "edition"),
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
            html = _page_404(message)
            self._send(status, html, "text/html; charset=utf-8", head)

    def _guard(self):
        if not local_ok(self):
            self._json(403, {"error": "只允许本机访问"})
            return False
        return True

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length 非法") from exc
        if length <= 0:
            raise ValueError("请求体为空")
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

    # ---- 路由 ---------------------------------------------------------
    def do_GET(self):
        self._handle("GET", head=False)

    def do_HEAD(self):
        self._handle("GET", head=True)

    def do_POST(self):
        self._handle("POST", head=False)

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
                if name == "jobs":
                    self._post_job()
                    return
                if name == "cancel":
                    job = self.service.queue.cancel(match.group(1))
                    if not job:
                        self._json(404, {"error": "任务不存在"})
                    else:
                        self._json(200, job)
                    return
                if name == "editorial":
                    try:
                        clean = self.service.save_editorial(match.group(1), int(match.group(2)), self._body())
                    except EditorialError as exc:
                        self._json(400, {"error": str(exc)})
                    except (FileNotFoundError, ValueError) as exc:
                        self._json(400, {"error": str(exc)})
                    else:
                        self._json(200, clean)
                    return
                if name == "edition":
                    try:
                        data = self._body()
                        model = str(data.get("model") or DEFAULT_MODEL).strip()
                        effort = str(data.get("effort") or "low").strip().lower()
                        overwrite = data.get("overwrite", False)
                        if not isinstance(overwrite, bool):
                            raise ValueError("overwrite 必须是布尔值")
                        if not model or len(model) > 80:
                            raise ValueError("模型名非法")
                        if effort not in EFFORTS:
                            raise ValueError("reasoning_effort 只能是 %s" % "/".join(EFFORTS))
                        job = self.service.submit_edition(
                            match.group(1), int(match.group(2)), model=model, effort=effort, overwrite=overwrite
                        )
                    except JobConflictError as exc:
                        self._json(
                            409, {"error": "该章已有任务在排队或运行", "code": "job_active", "conflicts": exc.conflicts}
                        )
                    except PackExistsError:
                        self._json(409, {"error": "这一章已有视觉读本", "code": "edition_exists"})
                    except FileNotFoundError:
                        self._json(404, {"error": "找不到章节"})
                    except (TypeError, ValueError) as exc:
                        self._json(400, {"error": str(exc)})
                    else:
                        self._json(202, job)
                    return
                self._json(405, {"error": "该路由不支持 POST"})
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
        match = re.match(r"^/reader/([A-Za-z0-9_-]+)/(\d+)/agent$", path)
        if match:
            self._serve_agent_reader(match.group(1), int(match.group(2)), head)
            return
        match = re.match(r"^/reader/([A-Za-z0-9_-]+)/(\d+)/edition$", path)
        if match:
            self._serve_edition(match.group(1), int(match.group(2)), head)
            return
        match = re.match(r"^/reader/([A-Za-z0-9_-]+)/(\d+)$", path)
        if match:
            self._serve_reader(match.group(1), int(match.group(2)), head)
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
                self._json(200, self.service.records(), head)
            elif name == "jobs":
                self._json(200, {"jobs": self.service.queue.list(100)}, head)
            elif name == "job":
                job = self.service.queue.get(match.group(1))
                if not job:
                    self._json(404, {"error": "任务不存在"}, head)
                else:
                    self._json(200, job, head)
            elif name == "export":
                self._serve_export(match.group(1), int(match.group(2)), match.group(3), head)
            elif name == "asset":
                self._serve_asset_query(query, head)
            elif name == "editorial":
                data = self.service.editorial(match.group(1), int(match.group(2)))
                if data is None:
                    self._json(404, {"error": "还没有编辑层"}, head)
                else:
                    self._json(200, data, head)
            else:
                self._json(405, {"error": "该路由不支持 GET"}, head)
            return
        self._error(404, "没有这个页面或接口", api=path.startswith("/api/"))

    def _post_job(self):
        try:
            data = self._body()
            uid = str(data.get("uid") or "")
            n = int(data.get("n"))
            tiers = data.get("tiers") or ["standard"]
            model = str(data.get("model") or DEFAULT_MODEL).strip()
            effort = str(data.get("effort") or DEFAULT_EFFORT).strip().lower()
            overwrite = data.get("overwrite", False)
            rebuild = data.get("rebuild", False)
            if not isinstance(overwrite, bool):
                raise ValueError("overwrite 必须是布尔值")
            if not isinstance(rebuild, bool):
                raise ValueError("rebuild 必须是布尔值")
            if not UID_RE.match(uid):
                raise ValueError("uid 非法")
            if not isinstance(tiers, list) or not all(isinstance(item, str) for item in tiers):
                raise ValueError("tiers 必须是字符串数组")
            if any(item not in ("skim", "standard", "deep", "research") for item in tiers):
                raise ValueError("未知档位")
            if not model or len(model) > 80:
                raise ValueError("模型名非法")
            if effort not in EFFORTS:
                raise ValueError("reasoning_effort 只能是 %s" % "/".join(EFFORTS))
            job = self.service.submit(uid, n, tiers, model=model, effort=effort, overwrite=overwrite, rebuild=rebuild)
        except FileNotFoundError:
            self._json(404, {"error": "找不到章节"})
            return
        except JobConflictError as exc:
            self._json(
                409,
                {
                    "error": "该章已有任务在排队或运行",
                    "code": "job_active",
                    "conflicts": exc.conflicts,
                },
            )
            return
        except RebuildRequiredError as exc:
            self._json(
                409,
                {
                    "error": "未勾选档位无法保留",
                    "code": "rebuild_required",
                    "tiers": exc.tiers,
                    "reason": exc.reason,
                },
            )
            return
        except PackExistsError as exc:
            self._json(
                409,
                {
                    "error": "该档位已有读本",
                    "code": "pack_exists",
                    "tiers": exc.tiers,
                },
            )
            return
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(202, job)

    # ---- 静态与产物 ---------------------------------------------------
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

    def _serve_agent_reader(self, uid, n, head=False):
        path = os.path.join(self.service.store.pack_dir(uid, n), "agent-standard.html")
        if not os.path.exists(path):
            self._error(404, "这一章还没有 Agent 标准档")
            return
        with open(path, encoding="utf-8") as fh:
            self._send(200, fh.read(), "text/html; charset=utf-8", head)

    def _serve_edition(self, uid, n, head=False):
        path = os.path.join(self.service.store.edition_dir(uid, n), "index.html")
        if not os.path.exists(path):
            self._error(404, "这一章还没有视觉读本")
            return
        with open(path, encoding="utf-8") as fh:
            self._send(200, fh.read(), "text/html; charset=utf-8", head)

    def _serve_reader(self, uid, n, head=False):
        path = self.service.store.chapter_html_path(uid, n)
        if not os.path.isfile(path):
            self._error(404, "这一章还没有生成读本", api=False, head=head)
            return
        with open(path, "rb") as fh:
            body = fh.read()
        if body and self.service.store.edition(uid, n) is not None:
            text = body.decode("utf-8")
            link = '<a class="edition-link" href="/reader/%s/%d/edition">视觉读本</a>' % (uid, n)
            if "<!--EDITION_LINK-->" in text:
                text = text.replace("<!--EDITION_LINK-->", link)
            elif "edition-link" not in text:
                text = text.replace("</div></header>", link + "</div></header>", 1)
            body = text.encode("utf-8")
        else:
            body = body.replace(b"<!--EDITION_LINK-->", b"")
        self._send(200, body, "text/html; charset=utf-8", head)

    def _serve_export(self, uid, n, tier, head=False):
        if tier not in ("skim", "standard", "deep", "research"):
            self._json(404, {"error": "未知档位"}, head)
            return
        path = self.service.store.export_path(uid, n, tier)
        if not os.path.isfile(path):
            self._json(404, {"error": "还没有这一档"}, head)
            return
        with open(path, "rb") as fh:
            body = fh.read()
        if body and self.service.store.edition(uid, n) is not None:
            text = body.decode("utf-8")
            link = '<a class="edition-link" href="/reader/%s/%d/edition">视觉读本</a>' % (uid, n)
            if "<!--EDITION_LINK-->" in text:
                text = text.replace("<!--EDITION_LINK-->", link)
            elif "edition-link" not in text:
                text = text.replace("</div></header>", link + "</div></header>", 1)
            body = text.encode("utf-8")
        else:
            body = body.replace(b"<!--EDITION_LINK-->", b"")
        self._send(200, body, "text/html; charset=utf-8", head)

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
<title>404 · 四读</title><style>
body{background:#F3F0E7;color:#181817;font:16px/1.8 "Noto Serif CJK SC",serif;margin:0;
display:grid;place-items:center;min-height:100vh}
main{max-width:560px;padding:40px;border-top:3px solid #A8442F}
h1{font-size:28px;margin:0 0 12px}a{color:#1E3A36}
</style><main><h1>没有找到</h1><p>%s</p>
<p><a href="/">← 回书架</a> · <a href="/records">生成记录</a></p></main></html>""" % (
        message.replace("&", "&amp;").replace("<", "&lt;")
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="8771 四读 本地服务")
    parser.add_argument("--root", default=os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/书库"))
    parser.add_argument("--port", type=int, default=8771)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--annotate",
        dest="annotate",
        action="store_true",
        default=os.environ.get("READING_ANNOTATE", "1") != "0",
        help="调用模型标注（默认开启）",
    )
    group.add_argument("--no-annotate", dest="annotate", action="store_false", help="离线确定性草稿，仅用于测试")
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
    print("四读 8771 · root=%s · http://127.0.0.1:%d/" % (service.library.root, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
