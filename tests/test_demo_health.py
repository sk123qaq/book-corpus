import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from conftest import run_cli

REPO = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_health_degrades_on_empty_workspace(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    port = free_port()
    env = dict(os.environ, BOOKS_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "apps" / "demo" / "server.py"), "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                body = json.loads(resp.read())
                conn.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("demo server did not start")
        assert resp.status == 200, body
        assert body["ok"] is True
        assert body["degraded"] is True
        # init 现在就把空索引建好，所以这里是「索引为空」而不是「未建索引」
        assert body["message"] in ("索引为空", "未建索引"), body["message"]
        assert body["meta"]["books"] == 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)
