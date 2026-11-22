"""操作台搜索入口的可注入点：注入生效，默认仍是本机 8765 与仓库里的演示页脚本。"""

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "ops"))

_spec = importlib.util.spec_from_file_location("ops_server_search_test", REPO / "apps" / "ops" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

DEMO_SERVER = str(REPO / "apps" / "demo" / "server.py")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/health":
            self.send_error(404)
            return
        body = json.dumps({"ok": True, "degraded": False, "db": "ok", "meta": {"fake": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _fake_search():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _app(env):
    app = server.App.__new__(server.App)
    app.env = env
    env.prepare()
    return app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("BOOK_CORPUS_SEARCH_URL", raising=False)
    monkeypatch.delenv("BOOK_CORPUS_DEMO_SERVER", raising=False)


def test_default_env_keeps_real_search_and_demo_script(tmp_path):
    env = server.Env(str(tmp_path))
    assert env.search_url == "http://127.0.0.1:8765"
    assert env.demo_server == DEMO_SERVER
    assert server.DEMO_SERVER == DEMO_SERVER


def test_env_overrides_search_and_demo_script(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOK_CORPUS_SEARCH_URL", "http://127.0.0.1:18065/")
    monkeypatch.setenv("BOOK_CORPUS_DEMO_SERVER", str(tmp_path / "fake-demo.py"))
    env = server.Env(str(tmp_path))
    assert env.search_url == "http://127.0.0.1:18065"
    assert env.demo_server == str(tmp_path / "fake-demo.py")


def test_constructor_args_win_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOK_CORPUS_SEARCH_URL", "http://127.0.0.1:18065")
    monkeypatch.setenv("BOOK_CORPUS_DEMO_SERVER", str(tmp_path / "from-env.py"))
    env = server.Env(
        str(tmp_path),
        search_url="http://127.0.0.1:18066",
        demo_server=str(tmp_path / "from-arg.py"),
    )
    assert env.search_url == "http://127.0.0.1:18066"
    assert env.demo_server == str(tmp_path / "from-arg.py")


def test_search_health_follows_injected_url(tmp_path):
    httpd, url = _fake_search()
    try:
        app = _app(server.Env(str(tmp_path), search_url=url))
        health = app.search_health()
        assert health["running"] is True
        assert health["url"] == url
        assert health["meta"] == {"fake": True}
    finally:
        httpd.shutdown()
        httpd.server_close()
    health = app.search_health()
    assert health["running"] is False
    assert health["url"] == url
    assert health["error"]


def test_search_start_already_running_is_idempotent(tmp_path):
    httpd, url = _fake_search()
    try:
        app = _app(server.Env(str(tmp_path), search_url=url, demo_server=str(tmp_path / "missing.py")))
        body = app.search_start()
        assert body["ok"] is True
        assert body["already"] is True
        assert body["health"]["url"] == url
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_search_start_missing_injected_script_reports_exact_error(tmp_path):
    missing = tmp_path / "missing.py"
    app = _app(server.Env(str(tmp_path), search_url="http://127.0.0.1:9", demo_server=str(missing)))
    body = app.search_start()
    assert body["ok"] is False
    assert body["error"] == f"演示页脚本不存在：{missing.relative_to(tmp_path)}"
    assert not Path(app.env.search_pid).exists()
