#!/usr/bin/env python3
"""书库首页：把检索、四读、日课收在一页。

默认 http://127.0.0.1:8773。页面服务端渲染，只读书目与日课；
点击入口时若目标服务没起，代它起进程，等端口就绪后再跳转。
"""

from __future__ import annotations

import argparse
import contextlib
import html
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "src"
for path in (str(SRC), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from launcher import (
    DEFAULT_TARGETS,
    SERVICES,
    START_TIMEOUT,
    ensure,
    is_local_target,
    same_target,
)
from overview import WEB, build_overview, render_page

from bookcorpus.reading.library import Library

ALLOWED_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::\d{1,5})?", re.I)
STYLE_PATH = WEB / "style.css"
TEMPLATE_PATH = WEB / "index.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "BookHome/0.1"
    protocol_version = "HTTP/1.1"

    library: Library
    root: Path
    urls: dict[str, str]
    targets: dict[str, str]
    template: str
    quiet = False
    allow_start = True
    start_timeout = START_TIMEOUT

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    def send(self, status: int, body: str, content_type: str):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)

    def fail(self, status: int, message: str):
        self.send(status, message, "text/plain; charset=utf-8")
        self.close_connection = True

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        return bool(ALLOWED_HOST.fullmatch(host))

    def do_GET(self):
        if not self.host_ok():
            self.fail(403, "仅限本机访问。")
            return
        route = unquote(urlsplit(self.path).path)
        if route in ("/", "/index.html"):
            self.serve_home()
            return
        if route == "/style.css":
            self.serve_style()
            return
        if route.startswith("/go/"):
            self.serve_go(route[4:].strip("/"))
            return
        self.fail(404, "没有这个页面。")

    def serve_go(self, key: str):
        if key not in SERVICES:
            self.fail(404, "没有这个入口。")
            return
        target = self.targets.get(key) or DEFAULT_TARGETS[key]
        # 自定义到别的地址时只跳转，不替它起本地进程。
        if not is_local_target(key, target):
            self.redirect(target)
            return
        result = ensure(
            key,
            self.root,
            timeout=self.start_timeout,
            allow_start=self.allow_start,
        )
        if result["state"] in ("already", "started"):
            self.redirect(target)
            return
        self.serve_go_error(key, result)

    def serve_go_error(self, key: str, result: dict):
        service = SERVICES[key]
        if result["state"] == "disabled":
            title = "没有自动启动"
            lead = f"首页被 --no-start 关掉了自动启动。先把「{service.name}」起起来，再回来点一次。"
        else:
            title = "服务没有起来"
            lead = str(result.get("error") or "请查看启动日志。")
        details = []
        if result.get("log"):
            details.append(f"日志：<code>{html.escape(str(result['log']))}</code>")
        if result.get("command"):
            details.append(f"命令：<code>{html.escape(' '.join(result['command']))}</code>")
        detail_html = "".join(f'<p class="detail">{item}</p>' for item in details)
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#F3F0E7;color:#181817;font:16px/1.8 "Noto Serif CJK SC",serif}}
main{{max-width:640px;margin:18vh auto 0;padding:0 28px}}
h1{{font-size:30px;font-weight:600;margin:0 0 14px}}
p{{color:#6E675C;margin:0 0 12px}}.detail{{font-size:13px;line-height:1.7;word-break:break-all}}
code{{font-family:ui-monospace,monospace;font-size:12px;color:#6B2B2B}}
a{{color:#A8442F;margin-right:22px}}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<p>{html.escape(lead)}</p>
{detail_html}
<p style="margin-top:28px"><a href="/go/{html.escape(key)}">重试</a><a href="/">回首页</a></p>
</main></body></html>"""
        self.send(503, body, "text/html; charset=utf-8")
        self.close_connection = True

    def serve_home(self):
        try:
            overview = build_overview(self.library, self.root)
            page = render_page(self.template, overview, self.urls)
        except Exception:
            traceback.print_exc()
            self.fail(500, "首页数据读取失败。")
            return
        self.send(200, page, "text/html; charset=utf-8")

    def serve_style(self):
        try:
            body = STYLE_PATH.read_text(encoding="utf-8")
        except OSError:
            traceback.print_exc()
            self.fail(500, "样式文件读取失败。")
            return
        self.send(200, body, "text/css; charset=utf-8")


def _default_root() -> str:
    return os.environ.get("BOOK_CORPUS_DATA") or os.environ.get("BOOKS_ROOT") or "~/书库"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="书库统一入口首页；读书目与日课，按需代起页面服务。")
    parser.add_argument("--port", type=int, default=8773, help="端口，默认 8773")
    parser.add_argument("--root", default=_default_root(), help="实例根，含 books.toml")
    parser.add_argument("--search-url", default=DEFAULT_TARGETS["search"], help="「找一段」跳转地址")
    parser.add_argument("--reading-url", default=DEFAULT_TARGETS["reading"], help="「翻开书」跳转地址")
    parser.add_argument("--daily-url", default=DEFAULT_TARGETS["daily"], help="「往期日课」跳转地址")
    parser.add_argument("--ops-url", default=DEFAULT_TARGETS["ops"], help="页脚「管理」跳转地址")
    parser.add_argument("--no-start", action="store_true", help="服务没起时不代起，直接报错")
    parser.add_argument(
        "--start-timeout",
        type=float,
        default=START_TIMEOUT,
        help=f"等待服务就绪的秒数，默认 {START_TIMEOUT:g}",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印访问日志")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        library = Library(args.root)
    except Exception as exc:
        print(f"书库首页：{exc}", file=sys.stderr)
        return 2
    if not Path(library.layout.books).is_dir():
        print(f"书库首页：根下没有 books/：{library.layout.books}", file=sys.stderr)
        return 2
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"书库首页：读取模板失败：{exc}", file=sys.stderr)
        return 2

    targets = {
        "search": args.search_url,
        "reading": args.reading_url,
        "daily": args.daily_url,
        "ops": args.ops_url,
    }
    # 默认地址走 /go 代起；显式改到别处时按原地址直达，不擅自起本地服务。
    urls = {
        key: (f"/go/{key}" if same_target(target, DEFAULT_TARGETS[key]) else target) for key, target in targets.items()
    }

    Handler.library = library
    Handler.root = Path(library.root)
    Handler.targets = targets
    Handler.urls = urls
    Handler.template = template
    Handler.quiet = args.quiet
    Handler.allow_start = not args.no_start
    Handler.start_timeout = max(0.1, args.start_timeout)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"书库首页：{exc}", file=sys.stderr)
        return 2
    server.daemon_threads = True
    port = server.server_address[1]
    print(f"书库首页 http://127.0.0.1:{port} · root={library.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
