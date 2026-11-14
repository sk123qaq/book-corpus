#!/usr/bin/env python3
"""书库日课的只读本地服务。

  python3 apps/daily/server.py
  http://127.0.0.1:8772

只读 <root>/日课/*.json 与 apps/daily/web 下的三个静态文件。
不写书、不写账、不缓存到磁盘。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent

# 数据根先从仓库根 .env 的 BOOKS_ROOT 读；没装包时退回 shell 环境。
if str(HERE.parent.parent / "src") not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent / "src"))
try:
    from bookcorpus import envfile
except ImportError:
    envfile = None
WEB = HERE / "web"
ALLOWED_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::\d{1,5})?", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "BookDaily/1.0"
    protocol_version = "HTTP/1.1"

    root: Path

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def send(self, code: int, body: bytes | dict | list | str, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        if code >= 400:
            self.close_connection = True
        self.send_response(code)
        if code >= 400:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def fail(self, code: int, message: str):
        self.send(code, {"error": message})

    def host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        return bool(ALLOWED_HOST.fullmatch(host))

    def serve_static(self, name: str, ctype: str):
        path = WEB / name
        try:
            body = path.read_bytes()
        except OSError:
            self.fail(404, "静态文件不存在。")
            return
        self.send(200, body, ctype)

    def do_GET(self):
        if not self.host_ok():
            self.fail(403, "只接受 127.0.0.1 或 localhost。")
            return

        route = unquote(self.path)
        if route in STATIC:
            self.serve_static(*STATIC[route])
            return
        if route == "/api/feed":
            self.send(200, self.feed())
            return
        if route.startswith("/api/issue/"):
            self.issue(route.removeprefix("/api/issue/"))
            return
        self.fail(404, "没有这个页面。")

    def feed(self):
        return load_feed(self.root)

    def issue(self, date: str):
        if not DATE_RE.fullmatch(date):
            self.fail(404, "没有这一期。")
            return
        for item in self.feed()["issues"]:
            if item["date"] == date:
                self.send(200, item)
                return
        self.fail(404, "没有这一期。")


def load_feed(root: Path):
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from feed import load_feed as read_feed

    return read_feed(root)


def main(argv=None):
    if envfile is not None:
        envfile.load()
    parser = argparse.ArgumentParser(description="书库日课的只读本地服务")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument(
        "--root", default=os.environ.get("BOOK_CORPUS_DATA") or os.environ.get("BOOKS_ROOT") or "~/书库"
    )
    args = parser.parse_args(argv)

    Handler.root = Path(args.root).expanduser().resolve()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    print(f"http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
