"""书库检索、分层读、首页、日课跨应用联动契约与隔离测试。"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bookcorpus import app_links
from conftest import run_cli

REPO = Path(__file__).resolve().parents[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_app(script: str, args: list[str], ready_path: str = "/", env: dict[str, str] | None = None):
    port = _free_port()
    full_env = dict(os.environ)
    full_env.pop("BOOKS_ROOT", None)
    full_env.pop("BOOKS_RUNLOG_ROOT", None)
    full_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if env:
        full_env.update(env)
    proc = subprocess.Popen(
        [sys.executable, str(REPO / script), "--port", str(port), *args],
        cwd=str(REPO),
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"{script} exited with {proc.returncode}:\n{stderr}")
        try:
            with urllib.request.urlopen(base + ready_path, timeout=2) as resp:
                if resp.status == 200:
                    return proc, base
        except Exception:
            time.sleep(0.1)
    proc.terminate()
    proc.wait(timeout=5)
    raise AssertionError(f"{script} did not become ready")


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _get_no_redirect(url: str) -> tuple[int, dict[str, str], str]:
    try:
        with _NO_REDIRECT_OPENER.open(url, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, dict(exc.headers), body


def test_app_links_safe_rel_path_and_redirect():
    assert app_links.safe_rel_path("") == "/"
    assert app_links.safe_rel_path("/open?uid=B0001#blk-B02") == "/open?uid=B0001#blk-B02"
    assert app_links.safe_rel_path("//evil.com/x") == ""
    assert app_links.safe_rel_path("https://evil.com/x") == ""
    assert app_links.safe_rel_path("\\evil") == ""

    status, loc, _ = app_links.resolve_go_redirect(
        "reading",
        "/open?uid=B0001",
        root=".",
        targets={"reading": "http://127.0.0.1:18771", "home": "http://127.0.0.1:18773"},
    )
    assert status == 302
    assert loc == "http://127.0.0.1:18771/open?uid=B0001"

    bad_status, _, _ = app_links.resolve_go_redirect(
        "reading",
        "https://evil.com",
        root=".",
        targets={"reading": "http://127.0.0.1:8771"},
    )
    assert bad_status == 400

    disabled_status, _, html = app_links.resolve_go_redirect(
        "reading",
        "/open?uid=B0001",
        root=".",
        targets={"reading": "http://127.0.0.1:8771", "home": "http://127.0.0.1:8773"},
        allow_start=False,
        probe=lambda _p: False,
    )
    assert disabled_status == 503
    assert "8771" in html


def test_home_go_forwards_to_query_and_fixes_stale_copy(reading_root: Path):
    proc, base = _start_app(
        "apps/home/server.py",
        ["--root", str(reading_root), "--reading-url", "http://127.0.0.1:18771", "--quiet"],
    )
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert "速览、标准、深读三档" not in body
        assert "知识地图" in body

        encoded = urllib.parse.quote("/open?uid=B0001&path=books/B0001/md/001_第1节.md", safe="")
        status, headers, _ = _get_no_redirect(f"{base}/go/reading?to={encoded}")
        assert status == 302
        assert (
            urllib.parse.unquote(headers["Location"])
            == "http://127.0.0.1:18771/open?uid=B0001&path=books/B0001/md/001_第1节.md"
        )

        bad_status, _, _ = _get_no_redirect(f"{base}/go/reading?to=//evil.com")
        assert bad_status == 400
    finally:
        _stop(proc)


def test_reading_open_and_go_search_bridge(reading_root: Path):
    proc, base = _start_app(
        "apps/reading/server.py",
        [
            "--root",
            str(reading_root),
            "--search-url",
            "http://127.0.0.1:18765",
            "--no-annotate",
            "--quiet",
        ],
    )
    try:
        qs = urllib.parse.urlencode(
            {
                "uid": "B0001",
                "path": "books/B0001/md/001_第1节.md",
                "line": "3",
                "q": "灯下压着一封没有署名的信",
                "from": "search",
                "query": "信放在哪里",
            }
        )
        status, headers, _ = _get_no_redirect(f"{base}/open?{qs}")
        assert status == 302
        loc = headers["Location"]
        assert loc.startswith("/chapter/B0001/01?")
        assert loc.endswith("#blk-B01")
        parsed = urllib.parse.urlsplit(loc)
        params = urllib.parse.parse_qs(parsed.query)
        assert params["from"] == ["search"]
        assert params["query"] == ["信放在哪里"]
        assert params["blocks"] == ["B01"]

        with urllib.request.urlopen(f"{base}/chapter/B0001/01", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "/assets/chapter.js" in page
        with urllib.request.urlopen(f"{base}/assets/chapter.js", timeout=5) as resp:
            assert resp.status == 200

        search_to = urllib.parse.quote("/?q=铁笼&books=B0001&from=reading&mode=trace", safe="")
        s_status, s_headers, _ = _get_no_redirect(f"{base}/go/search?to={search_to}")
        assert s_status == 302
        assert (
            urllib.parse.unquote(s_headers["Location"])
            == "http://127.0.0.1:18765/?q=铁笼&books=B0001&from=reading&mode=trace"
        )
    finally:
        _stop(proc)


def test_demo_root_isolation_books_meta_and_scope(workspace: Path):
    env = {"BOOKS_ROOT": str(workspace)}
    assert run_cli("index", "--rebuild", env=env).returncode == 0
    assert run_cli("library", env=env).returncode == 0

    proc, base = _start_app(
        "apps/demo/server.py",
        [
            "--root",
            str(workspace),
            "--reading-url",
            "http://127.0.0.1:18771",
            "--quiet",
        ],
        ready_path="/api/health",
        env={"PI_BIN": "/bin/false"},
    )
    try:
        # 1. 元数据接口返回书名
        with urllib.request.urlopen(f"{base}/api/books/meta?uids=B0001,B0002", timeout=5) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        assert meta["ok"] is True
        assert [b["title"] for b in meta["books"]] == ["甲书", "乙书"]

        # 2. 跳板跳转到自定义 reading 地址
        read_to = urllib.parse.quote("/open?uid=B0001", safe="")
        r_status, r_headers, _ = _get_no_redirect(f"{base}/go/reading?to={read_to}")
        assert r_status == 302
        assert r_headers["Location"] == "http://127.0.0.1:18771/open?uid=B0001"

        # 3. 显式 scope.books 只返回指定书结果，且会话落在 <workspace>/工作区/检索/
        req = urllib.request.Request(
            f"{base}/api/run",
            data=json.dumps({"question": "礼治与铁笼", "scope": {"books": ["B0002"]}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            trace = json.loads(resp.read().decode("utf-8"))
        assert trace["scope"] == {"books": ["B0002"]}
        assert trace["focus"]["book"] == "B0002"
        assert all(row["uid"] == "B0002" for row in trace["plan"]["ranking"])
        assert trace["result"]["uid"] == "B0002"

        # 4. 多书 scope 下点名范围外的书，focus 与结果都不能越界
        req = urllib.request.Request(
            f"{base}/api/run",
            data=json.dumps({"question": "丙书里礼治与铁笼", "scope": {"books": ["B0001", "B0002"]}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            scoped = json.loads(resp.read().decode("utf-8"))
        assert scoped["focus"]["book"] != "B0003"
        assert scoped["focus"]["status"] != "exact"
        assert scoped["result"]["uid"] in {"B0001", "B0002"}
        assert not scoped["result"].get("focus_empty")

        sid = trace["session_id"]
        assert (workspace / "工作区" / "检索" / "sessions" / f"{sid}.json").is_file()
        assert (workspace / "工作区" / "检索" / "运行记录" / "index.jsonl").is_file()
    finally:
        _stop(proc)


def test_daily_feed_exposes_reading_url(reading_root: Path):
    day = dt.date.today()
    daily_dir = reading_root / "日课"
    daily_dir.mkdir(exist_ok=True)
    (daily_dir / f"{day.isoformat()}.json").write_text(
        json.dumps(
            {
                "version": 1,
                "date": day.isoformat(),
                "kind": "文学",
                "kindLabel": "文学",
                "subject": "文学",
                "book": {"uid": "B0001", "title": "甲书", "author": "鲁迅"},
                "chapter": {"index": 99, "title": "第1节", "path": "books/B0001/md/001_第1节.md"},
                "headline": "一封信",
                "body": ["正文。"],
                "quote": {"text": "灯下压着一封没有署名的信。", "line": 3},
                "why": "测试。",
                "research": {"chapters": "无", "web": 0, "queries": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    proc, base = _start_app(
        "apps/daily/server.py",
        ["--root", str(reading_root), "--reading-url", "http://127.0.0.1:18771"],
        ready_path="/api/feed",
    )
    try:
        with urllib.request.urlopen(f"{base}/api/feed", timeout=5) as resp:
            feed = json.loads(resp.read().decode("utf-8"))
        assert feed["reading_url"] == "http://127.0.0.1:18771"
    finally:
        _stop(proc)
