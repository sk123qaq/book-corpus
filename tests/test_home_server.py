"""8773 首页的真实 HTTP 边界：本机绑定、Host、CSS、只读空状态。"""

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOME = REPO / "apps" / "home"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_issue(root: Path, date: dt.date | None = None) -> dt.date:
    day = date or dt.date.today()
    payload = {
        "version": 1,
        "date": day.isoformat(),
        "kind": "文学",
        "kindLabel": "文学",
        "subject": "测试",
        "book": {"uid": "B0001", "title": "甲书", "author": "鲁迅"},
        "chapter": {
            "index": 1,
            "title": "第1节",
            "path": "books/B0001/md/001_第1节.md",
        },
        "headline": "灯下的一封信",
        "body": ["第一段写一个人走进屋子，看见桌上有一盏灯，灯下压着一封没有署名的信。"],
        "quote": {"text": "灯下压着一封没有署名的信。", "line": 1},
        "why": "测试用。",
        "research": {"chapters": "无", "web": 0, "queries": []},
    }
    directory = root / "日课"
    directory.mkdir(exist_ok=True)
    (directory / f"{day.isoformat()}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return day


def _get(base: str, path: str = "/"):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return response.status, response.read().decode("utf-8"), response.headers


def _start_server(root: Path, extra_args=()):
    port = _free_port()
    env = dict(os.environ)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(HOME / "server.py"),
            "--root",
            str(root),
            "--port",
            str(port),
            "--quiet",
            *extra_args,
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"home server exited with {proc.returncode}:\n{stderr}")
        try:
            status, _, _ = _get(base)
            if status == 200:
                return proc, base
        except Exception as exc:
            last_error = exc
        time.sleep(0.1)
    _stop_server(proc)
    raise AssertionError(f"home server did not become ready: {last_error}")


def _stop_server(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture
def home_server(reading_root):
    _write_issue(reading_root)
    proc, base = _start_server(
        reading_root,
        [
            "--search-url",
            "http://127.0.0.1:9999/search",
            "--reading-url",
            "http://127.0.0.1:9998/read",
            "--daily-url",
            "http://127.0.0.1:9997/daily",
            "--ops-url",
            "http://127.0.0.1:9996/ops",
        ],
    )
    try:
        yield base
    finally:
        _stop_server(proc)


def test_home_renders_real_data(home_server):
    status, body, headers = _get(home_server)

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "<title>书库 · 今天读哪一页</title>" in body
    assert "2 本可读" in body
    assert "本在架" not in body
    assert "1 期" in body
    assert "灯下的一封信" in body
    assert "第 001 期" in body
    assert "《甲书》 · 第1节" in body
    assert "今日日课" in body
    assert "读这一期" in body
    assert dt.date.today().strftime("%Y.%m.%d") in body
    assert 'href="http://127.0.0.1:9999/search"' in body
    assert 'href="http://127.0.0.1:9998/read"' in body
    assert 'href="http://127.0.0.1:9997/daily"' in body
    assert 'href="http://127.0.0.1:9996/ops"' in body
    assert "管理" in body
    assert "{{" not in body


def test_index_html_alias(home_server):
    status, body, _ = _get(home_server, "/index.html")
    assert status == 200
    assert "灯下的一封信" in body


def test_css_and_404(home_server):
    status, body, headers = _get(home_server, "/style.css")
    assert status == 200
    assert headers["Content-Type"].startswith("text/css")
    assert "--cinnabar" in body

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(home_server, "/missing")
    assert exc.value.code == 404


def test_foreign_host_is_rejected(home_server):
    request = urllib.request.Request(
        home_server + "/",
        headers={"Host": "evil.example"},
        method="GET",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 403


def test_go_unknown_is_404(home_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(home_server, "/go/nope")
    assert exc.value.code == 404


def test_no_daily_directory_shows_empty_card(reading_root):
    proc, base = _start_server(reading_root)
    try:
        status, body, _ = _get(base)
        assert status == 200
        assert 'href="/go/search"' in body
        assert 'href="/go/reading"' in body
        assert 'href="/go/daily"' in body
        assert 'href="/go/ops"' in body
        assert "今天还没有日课" in body
        assert "0 期" in body
        assert "去日课页看看" in body
    finally:
        _stop_server(proc)
