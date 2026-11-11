#!/usr/bin/env python3
"""缺索引时的页头计数：API 报未知，页面显示「—」与「先跑索引」，不把占位 0 当规模。

  PYTHONPATH=src python3 -m pytest tests/acceptance/demo/test_health_stats.py -q

起一个 BOOKS_ROOT 下没有 build/ 的 demo 服务，接口断言 meta 计数为 null，
再用 headless Chrome 读真实 DOM，断言页头没有「0 段」。
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEMO = REPO / "apps" / "demo"
sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
import shot


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_health(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            return resp.status, body
        except OSError:
            time.sleep(0.1)
    raise AssertionError("demo server did not start")


@pytest.fixture
def bare_server(tmp_path):
    root = tmp_path / "repro_demo"
    root.mkdir()
    port = free_port()
    env = dict(os.environ, BOOKS_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen(
        [sys.executable, str(DEMO / "server.py"), "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        status, body = wait_health(port)
        yield port, status, body
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_health_reports_unknown_counts(bare_server):
    _port, status, body = bare_server
    assert status == 200, body
    assert body["ok"] is True
    assert body["degraded"] is True
    assert body["db"] in ("missing", "empty"), body
    assert body["message"] in ("未建索引", "索引为空"), body["message"]
    meta = body["meta"]
    assert meta["library"] == "missing"
    assert meta["index"] == "missing"
    assert meta["books"] is None, meta
    assert meta["paras"] is None, meta
    assert meta["chapters"] is None, meta


def header_snapshot(port, path="/"):
    url = f"http://127.0.0.1:{port}{path}"
    chrome, profile, ws_url = shot.launch(1200, 900, "about:blank", free_port())
    cdp = shot.CDP(shot.WS(ws_url))
    try:
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": url})
        deadline = time.time() + 15
        while time.time() < deadline:
            if shot.eval_js(cdp, "!!document.querySelector('[data-stat-note]')"):
                break
            time.sleep(0.2)
        return shot.eval_js(
            cdp,
            """(() => ({
              stats: Array.from(document.querySelectorAll('[data-stat]'))
                .map((el) => el.textContent.trim()),
              note: (document.querySelector('[data-stat-note]') || {}).textContent || '',
              noteHidden: document.querySelector('[data-stat-note]')
                ? document.querySelector('[data-stat-note]').hidden : null,
              body: document.body.innerText,
            }))()""",
        )
    finally:
        cdp.ws.close()
        chrome.kill()
        chrome.wait(timeout=10)
        subprocess.run(["rm", "-rf", profile], check=False)


def assert_header_degrades(snap):
    assert snap is not None, "page did not evaluate"
    assert snap["stats"] == ["—", "—", "—"], snap
    assert "0 段" not in snap["body"], snap["body"][:400]
    assert snap["noteHidden"] is False, snap
    assert "先跑索引" in snap["note"], snap
    assert "索引未建" in snap["note"] or "索引为空" in snap["note"], snap


def test_header_shows_dashes_and_hint_not_zero(bare_server):
    assert_header_degrades(header_snapshot(bare_server[0]))


def test_classic_header_shows_dashes_and_hint_not_zero(bare_server):
    assert_header_degrades(header_snapshot(bare_server[0], "/classic"))
