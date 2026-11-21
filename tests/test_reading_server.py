"""8771 阅读工作台 HTTP 服务：本地绑定、全章正文接口、伴读 SSE 与操作日志。"""

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


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start_server(reading_root, extra_env=None):
    port = _free_port()
    env = dict(os.environ)
    env.update(extra_env or {})
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "apps" / "reading" / "server.py"),
            "--root",
            str(reading_root),
            "--port",
            str(port),
            "--quiet",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited: {proc.stderr.read().decode('utf-8', 'replace')}")
        try:
            with urllib.request.urlopen(base + "/api/shelf", timeout=1) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    else:
        _stop_server(proc)
        raise AssertionError("server did not start")
    return proc, base


def _stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _request(base, path, method="GET", body=None, headers=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), exc.headers


@pytest.fixture
def server(reading_root):
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0"})
    try:
        yield base
    finally:
        _stop_server(proc)


@pytest.fixture
def no_key_server(reading_root):
    home = reading_root.parent / "bare-home"
    home.mkdir(exist_ok=True)
    proc, base = _start_server(
        reading_root,
        {"READING_ANNOTATE": "1", "OPENCODE_GO_API_KEY": "", "HOME": str(home)},
    )
    try:
        yield base
    finally:
        _stop_server(proc)


def test_pages_and_retired_routes(server):
    for page in ("/", "/catalog", "/book/B0001", "/chapter/B0001/01", "/records"):
        status, body, headers = _request(server, page)
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert "工单" not in body or page == "/"  # 页面已清除工单系统

    for gone in (
        "/reader/B0001/01",
        "/reader/B0001/01/depth",
        "/reader/B0001/01/edition",
        "/api/book/B0001/reading",
        "/api/chapter/B0001/01/reading",
        "/api/jobs",
        "/api/depth/B0001/01",
        "/api/edition/B0001/01",
        "/api/editorial/B0001/01",
        "/api/export/B0001/01/standard",
    ):
        status, _, _ = _request(server, gone)
        assert status == 404, gone
    for retired_put in ("/api/book/B0001/reading", "/api/chapter/B0001/01/reading"):
        status, _, _ = _request(server, retired_put, method="PUT", body={"mode": "narrative"})
        assert status == 404, retired_put


def test_chapter_api_returns_full_blocks_and_validates_host(server):
    status, body, _ = _request(server, "/api/chapter/B0001/1")
    assert status == 200
    ch = json.loads(body)
    assert ch["chapter"]["n"] == "01"
    assert len(ch["blocks"]) >= 1
    assert "toolbar" in ch
    assert "reading" not in ch
    assert "tiers" not in ch
    assert "edition" not in ch

    status, _, _ = _request(server, "/api/shelf", headers={"Host": "evil.example"})
    assert status == 403


def test_assist_missing_model_key_returns_400_with_fix(no_key_server):
    status, body, _ = _request(
        no_key_server,
        "/api/assist",
        method="POST",
        body={
            "uid": "B0001",
            "n": 1,
            "action": "explain",
            "block_ids": ["B01"],
            "selected_text": "正文节选",
            "stream": False,
        },
    )
    assert status == 400
    payload = json.loads(body)
    assert payload["code"] == "missing_config"
    assert payload["fix"]


def test_assist_sse_pin_unpin_and_records_roundtrip(reading_root):
    mock_http_port = _free_port()
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    captured_requests = []

    class _MockUpstream(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            captured_requests.append(json.loads(self.rfile.read(length).decode("utf-8")))
            body = (
                'data: {"choices":[{"delta":{"content":"白话"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"解析"},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", mock_http_port), _MockUpstream)
    t = threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        proc, base = _start_server(
            reading_root,
            {
                "READING_MODEL_URL": f"http://127.0.0.1:{mock_http_port}/v1/chat/completions",
                "OPENCODE_GO_API_KEY": "sk-mock",
            },
        )
        try:
            # 1. 短章触发直读建议门控
            status, map_body, _ = _request(base, "/api/chapter-map/B0001/1", method="POST", body={"force": False})
            assert status == 200
            map_res = json.loads(map_body)
            assert map_res["supported"] is False
            assert map_res["can_force"] is True

            # 2. SSE 划词伴读（含 translate 不带读法提示注入）
            status, sse_body, headers = _request(
                base,
                "/api/assist",
                method="POST",
                body={
                    "uid": "B0001",
                    "n": 1,
                    "action": "translate",
                    "block_ids": ["B01"],
                    "selected_text": "选中原文",
                    "stream": True,
                },
            )
            assert status == 200
            assert "text/event-stream" in headers.get("Content-Type", "")
            assert "event: delta" in sse_body
            assert "event: done" in sse_body
            assert "白话解析" in sse_body
            assert captured_requests
            sent_text = "\n".join(m["content"] for m in captured_requests[-1]["messages"])
            assert "阅读偏好参考" not in sent_text
            assert "论证" not in sent_text

            # 3. 钉住与删除旁批
            status, pin_body, _ = _request(
                base,
                "/api/assist/pin",
                method="POST",
                body={
                    "uid": "B0001",
                    "n": 1,
                    "action": "translate",
                    "title": "分段注",
                    "block_ids": ["B01"],
                    "selected_text": "选中原文",
                    "content": "白话解析",
                },
            )
            assert status == 200
            note_id = json.loads(pin_body)["id"]

            # 4. /api/records 返回操作日志流水
            status, rec_body, _ = _request(base, "/api/records")
            assert status == 200
            recs = json.loads(rec_body)
            assert recs["summary"]["total"] == 2
            assert [r["action"] for r in recs["logs"]] == ["pin", "translate"]

            status, _, _ = _request(base, f"/api/assist/pin/B0001/1/{note_id}", method="DELETE")
            assert status == 200
            status, after_ch, _ = _request(base, "/api/chapter/B0001/1")
            assert json.loads(after_ch)["assist_notes"] == []
        finally:
            _stop_server(proc)
    finally:
        upstream.shutdown()
        upstream.server_close()
