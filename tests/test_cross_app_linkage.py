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
from conftest import run_cli, write_book, write_ledger

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
            chapter_js = resp.read().decode("utf-8")
        assert "本书查这段" not in chapter_js
        assert 'data-action="search-book"' not in chapter_js
        assert "全库查这段" not in chapter_js
        assert "跨书找相关 ↗" in chapter_js
        assert "mode', 'related'" in chapter_js
        assert "exclude', UID" in chapter_js

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


def test_demo_related_mode_excludes_source_and_applies_threshold(tmp_path: Path):
    env = {"BOOKS_ROOT": str(tmp_path)}
    assert run_cli("init", str(tmp_path), "--no-pdf-venv", env=env).returncode == 0

    src_para = "乡土社会是礼治社会，依靠长老教化与传统习惯维持日常秩序，不需要外在的法律强力与铁笼约束。"
    # Create 13 books: B0001 (source), B0002 (verbatim duplicate),
    # B0003 (all 4 terms, highest relevance), B0004..B0012 (3 terms), B0013 (1 term, weak)
    books_spec = [("B0001", "源书", src_para), ("B0002", "复本", src_para)]
    for i in range(3, 14):
        if i == 3:
            body = "这本书深入分析礼治、秩序、法律与铁笼，比较传统社会与现代制度的差异。" * 3
        elif i <= 12:
            body = f"第{i}本书探讨礼治、秩序与铁笼，说明社会约束如何形成与演化。" * 2
        else:
            body = f"第{i}本书只顺便提到过铁笼一次，主要讨论完全不相干的农业技术与水利工程方法。" * 2
        books_spec.append((f"B{i:04d}", f"相关书{i}", body))

    ledger_rows = []
    for uid, title, body in books_spec:
        write_book(tmp_path, uid, "人文社科", title, [body])
        ledger_rows.append((uid, "人文社科", title, ""))
    write_ledger(tmp_path, ledger_rows)

    assert run_cli("index", "--rebuild", env=env).returncode == 0
    assert run_cli("library", env=env).returncode == 0

    proc, base = _start_app(
        "apps/demo/server.py",
        ["--root", str(tmp_path), "--reading-url", "http://127.0.0.1:18771", "--quiet"],
        ready_path="/api/health",
        env={"PI_BIN": "/bin/false"},
    )
    try:

        def post_run(payload):
            req = urllib.request.Request(
                f"{base}/api/run",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 1. Moderate threshold (0.40): excludes B0001 and duplicate B0002, caps selected_books at 10
        res = post_run(
            {
                "question": "礼治 秩序 法律 铁笼",
                "mode": "related",
                "exclude_books": ["B0001"],
                "exclude_spots": [{"path": "books/B0001/md/001_第1节.md", "line": 3}],
                "source_text": src_para,
                "threshold": 0.40,
                "stateless": True,
            }
        )
        uids = [r["uid"] for r in res["plan"]["ranking"]]
        assert "B0001" not in uids
        assert "B0002" not in uids
        assert res["related_search"]["active"] is True
        assert res["related_search"]["max_books"] == 10
        assert len(res["related_search"]["selected_books"]) == 10
        assert res["result"]["uid"] == "B0003"

        # 2. Mid-high threshold between B0003 and B0004..B0012 -> only 1 book qualifies
        top_rel = res["plan"]["ranking"][0]["relevance"]
        second_rel = res["plan"]["ranking"][1]["relevance"]
        assert top_rel > second_rel
        mid_thr = round((top_rel + second_rel) / 2, 4)
        res_one = post_run(
            {
                "question": "礼治 秩序 法律 铁笼",
                "mode": "related",
                "exclude_books": ["B0001"],
                "source_text": src_para,
                "threshold": mid_thr,
                "stateless": True,
            }
        )
        assert res_one["related_search"]["qualified_count"] == 1
        assert res_one["related_search"]["selected_books"] == ["B0003"]
        assert res_one["result"]["uid"] == "B0003"

        # 3. Very high threshold (0.99): 0 books qualify -> result is None
        res_empty = post_run(
            {
                "question": "礼治 秩序 法律 铁笼",
                "mode": "related",
                "exclude_books": ["B0001"],
                "source_text": src_para,
                "threshold": 0.99,
                "stateless": True,
            }
        )
        assert res_empty["related_search"]["qualified_count"] == 0
        assert res_empty["related_search"]["selected_books"] == []
        assert res_empty["result"] is None
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


def test_demo_reads_books_root_from_dotenv_and_syncs_book_expand_and_readme(tmp_path: Path):
    """不传 --root、不设 BOOKS_ROOT 时，8765 从 .env 读 BOOKS_ROOT 并同步 book_expand / book_readme。"""
    env = {"BOOKS_ROOT": str(tmp_path)}
    assert run_cli("init", str(tmp_path), "--no-pdf-venv", env=env).returncode == 0
    write_book(
        tmp_path,
        "B0001",
        "人文社科",
        "源书",
        ["这是源书的第一段内容，专门讨论制度与习惯。"],
    )
    write_book(
        tmp_path,
        "B0002",
        "人文社科",
        "相关书",
        ["这本书深入分析礼治秩序与传统习惯如何约束社会成员行为。" * 2],
    )
    write_ledger(tmp_path, [("B0001", "人文社科", "源书", ""), ("B0002", "人文社科", "相关书", "")])
    assert run_cli("index", "--rebuild", env=env).returncode == 0
    assert run_cli("library", env=env).returncode == 0

    dotenv = tmp_path / "demo.env"
    dotenv.write_text(f"BOOKS_ROOT={tmp_path}\n", encoding="utf-8")

    # 1. 不带 --root、不带 BOOKS_ROOT，仅靠 .env 启动 demo server，能健康就绪并跑通 related 检索
    proc, base = _start_app(
        "apps/demo/server.py",
        ["--quiet"],
        ready_path="/api/health",
        env={"BOOK_CORPUS_ENV": str(dotenv), "PI_BIN": "/bin/false"},
    )
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        assert health["ok"] is True
        assert health["meta"]["books"] == 2

        req = urllib.request.Request(
            f"{base}/api/run",
            data=json.dumps(
                {
                    "question": "礼治 秩序 传统 习惯",
                    "mode": "related",
                    "exclude_books": ["B0001"],
                    "source_text": "制度与习惯。",
                    "threshold": 0.20,
                    "stateless": True,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            run_res = json.loads(resp.read().decode("utf-8"))
        assert run_res["result"]["uid"] == "B0002"
        assert (tmp_path / "工作区" / "检索" / "运行记录" / "index.jsonl").is_file()
    finally:
        _stop(proc)

    # 2. 先在无 BOOKS_ROOT 下导入 answer / book_expand / book_readme，再 configure_runtime 也能取到原文窗口
    probe_code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO / 'apps' / 'demo')!r})\n"
        "import context as demo_context\n"
        "import answer\n"
        "import book_expand\n"
        "import book_readme\n"
        f"demo_context.configure_runtime({str(tmp_path)!r})\n"
        "entries, status = book_readme.readme_toc('B0002')\n"
        "assert entries and entries[0]['path'] == 'books/B0002/md/001_第1节.md', (entries, status)\n"
        "lines = book_expand.read_lines('books/B0002/md/001_第1节.md')\n"
        "assert lines and '礼治秩序' in '\\n'.join(lines), lines\n"
        "win = answer.build_windows(\n"
        "    [{'book': 'B0002', 'path': 'books/B0002/md/001_第1节.md', 'line': 3}],\n"
        "    {'B0002': {'books/B0002/md/001_第1节.md': '第1节'}},\n"
        "    {'B0002': status},\n"
        ")\n"
        "assert len(win) == 1 and '礼治秩序' in win[0]['text'], win\n"
    )
    clean_env = dict(os.environ)
    clean_env.pop("BOOKS_ROOT", None)
    clean_env["BOOK_CORPUS_ENV"] = str(tmp_path / "missing.env")
    proc2 = subprocess.run(
        [sys.executable, "-c", probe_code],
        cwd=str(REPO),
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr


def test_other_apps_read_books_root_from_dotenv_without_cli_flag(reading_root: Path, tmp_path: Path):
    """不传 --root、不设 BOOKS_ROOT 时，8773/8771/8772/8770 统一从 .env 读 BOOKS_ROOT。"""
    dotenv = tmp_path / "apps.env"
    dotenv.write_text(f"BOOKS_ROOT={reading_root}\n", encoding="utf-8")
    env = {"BOOK_CORPUS_ENV": str(dotenv)}

    # 8773 首页
    proc, base = _start_app("apps/home/server.py", ["--quiet"], ready_path="/", env=env)
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            assert resp.status == 200
    finally:
        _stop(proc)

    # 8771 分层读
    proc, base = _start_app("apps/reading/server.py", ["--no-annotate", "--quiet"], ready_path="/api/shelf", env=env)
    try:
        with urllib.request.urlopen(f"{base}/api/shelf", timeout=5) as resp:
            shelf = json.loads(resp.read().decode("utf-8"))
        assert {b["uid"] for b in shelf["books"]} == {"B0001", "B0002"}
    finally:
        _stop(proc)

    # 8772 日课
    proc, base = _start_app("apps/daily/server.py", [], ready_path="/api/feed", env=env)
    try:
        with urllib.request.urlopen(f"{base}/api/feed", timeout=5) as resp:
            feed = json.loads(resp.read().decode("utf-8"))
        assert Path(feed["dir"]).resolve() == (reading_root / "日课").resolve()
    finally:
        _stop(proc)

    # 8770 操作台
    proc, base = _start_app("apps/ops/server.py", [], ready_path="/api/status", env=env)
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        assert status["status"] is not None
    finally:
        _stop(proc)
