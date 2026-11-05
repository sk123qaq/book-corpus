"""8771 HTTP 外壳：本地绑定、真实 API、任务与读本。"""

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

ACTIVE_STATES = ("queued", "chunking", "annotating", "validating", "projecting", "rendering")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def server(reading_root):
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0"})
    try:
        yield base
    finally:
        _stop_server(proc)


@pytest.fixture
def slow_server(reading_root):
    """模型端只接连接不回响应：任务确定地停在 annotating，方便观察冲突与取消。"""
    hang = socket.socket()
    hang.bind(("127.0.0.1", 0))
    hang.listen(16)
    try:
        proc, base = _start_server(
            reading_root,
            {
                "READING_ANNOTATE": "1",
                "READING_MODEL_URL": "http://127.0.0.1:%d/v1/chat/completions" % hang.getsockname()[1],
                "READING_MODEL_TIMEOUT": "30",
                "OPENCODE_GO_API_KEY": "test-key",
            },
        )
        try:
            yield base
        finally:
            _stop_server(proc)
    finally:
        hang.close()


def _start_server(reading_root, extra_env=None):
    port = _free_port()
    env = dict(os.environ)
    env["READING_OVERLAY"] = str(reading_root.parent / "no-overlay")
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
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("server exited: %s" % proc.stderr.read().decode("utf-8", "replace"))
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


def _wait_job_state(base, job_id, target, timeout=15):
    deadline = time.time() + timeout
    current = None
    while time.time() < deadline:
        _, body, _ = _get(base, "/api/jobs/" + job_id)
        current = json.loads(body)
        if current["state"] == target:
            return current
        if current["state"] not in ACTIVE_STATES:
            raise AssertionError("job left active before %s: %s" % (target, current))
        time.sleep(0.01)
    raise AssertionError("job never reached %s: %s" % (target, current))


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return response.status, response.read().decode("utf-8"), response.headers


def _post(base, path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_pages_and_api_are_real_data(server):
    status, body, _headers = _get(server, "/api/shelf")
    assert status == 200
    shelf = json.loads(body)
    assert shelf["stats"]["readable"] == 2
    assert shelf["stats"]["chapters"] == 3

    for path in ("/", "/catalog", "/records", "/book/B0001", "/chapter/B0001/01"):
        status, body, _ = _get(server, path)
        assert status == 200, path
        assert "四读" in body, path
    status, body, _ = _get(server, "/api/book/B0001")
    assert len(json.loads(body)["chapters"]) == 2
    status, body, _ = _get(server, "/api/chapter/B0001/01")
    chapter = json.loads(body)
    assert chapter["chapter"]["chars"] > 0
    assert len(chapter["tiers"]) == 4
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/book/NOPE")
    assert exc.value.code == 404
    assert exc.value.headers["Content-Type"].startswith("application/json")


def test_job_poll_and_generated_reader(server, reading_root):
    status, job = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    assert job["state"] in ("queued", "chunking", "annotating", "validating", "projecting", "rendering", "done")
    deadline = time.time() + 15
    current = job
    while time.time() < deadline:
        _, body, _ = _get(server, "/api/jobs/" + job["id"])
        current = json.loads(body)
        if current["state"] not in ("queued", "chunking", "annotating", "validating", "projecting", "rendering"):
            break
        time.sleep(0.05)
    assert current["state"] == "done", current
    assert current["status"] == "draft"
    assert (Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "chapter.html").is_file()

    status, body, _ = _get(server, "/reader/B0001/01")
    assert status == 200
    assert 'data-origin="source"' in body
    assert body.count('data-tier-tab="') == 4
    assert "第一段写一个人走进屋子" in body
    status, body, headers = _get(server, "/api/export/B0001/01/standard")
    assert status == 200
    assert "text/html" in headers["Content-Type"]

    _, body, _ = _get(server, "/api/chapter/B0001/01")
    chapter = json.loads(body)
    assert [item["exists"] for item in chapter["tiers"]] == [False, True, False, False]
    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    assert not (pack / "export" / "skim.html").exists()
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/export/B0001/01/skim")
    assert exc.value.code == 404

    status, records, _ = _get(server, "/api/records")
    records = json.loads(records)
    assert any(item["id"] == job["id"] for item in records["jobs"])
    assert records["packs"][0]["uid"] == "B0001"


def test_post_validates_payload_and_host(server):
    request = urllib.request.Request(
        server + "/api/jobs",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 400

    request = urllib.request.Request(server + "/api/shelf", headers={"Host": "evil.example"}, method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 403


def _post_error(base, path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError("expected HTTP error")


def test_existing_tier_returns_409_and_overwrite_is_accepted(server):
    status, job = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    deadline = time.time() + 15
    while time.time() < deadline:
        _, body, _ = _get(server, "/api/jobs/" + job["id"])
        state = json.loads(body)["state"]
        if state not in ("queued", "chunking", "annotating", "validating", "projecting", "rendering"):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job did not finish")

    code, body = _post_error(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert code == 409
    assert body["code"] == "pack_exists"
    assert body["tiers"] == ["standard"]

    status, fresh = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"], "overwrite": True})
    assert status == 202
    assert fresh["overwrite"] is True


def test_source_change_returns_409_rebuild_required_and_rebuild_drops_unselected(server, reading_root):
    status, job = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    deadline = time.time() + 15
    while time.time() < deadline:
        _, body, _ = _get(server, "/api/jobs/" + job["id"])
        if json.loads(body)["state"] not in (
            "queued",
            "chunking",
            "annotating",
            "validating",
            "projecting",
            "rendering",
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job did not finish")

    path = Path(reading_root) / "books" / "B0001" / "md" / "001_第1节.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n\n后来补写的一段。\n", encoding="utf-8")

    code, body = _post_error(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["deep"]})
    assert code == 409
    assert body["code"] == "rebuild_required"
    assert body["tiers"] == ["standard"]
    assert body["reason"] == "原文已更改"

    status, fresh = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["deep"], "rebuild": True})
    assert status == 202
    assert fresh["rebuild"] is True
    deadline = time.time() + 15
    while time.time() < deadline:
        _, body, _ = _get(server, "/api/jobs/" + fresh["id"])
        state = json.loads(body)["state"]
        if state not in ("queued", "chunking", "annotating", "validating", "projecting", "rendering"):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("rebuild job did not finish")

    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["tiers"]) == {"deep"}
    assert not (pack / "export" / "standard.html").exists()


def test_double_post_same_chapter_returns_409_job_active(slow_server):
    status, job = _post(slow_server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    _wait_job_state(slow_server, job["id"], "annotating")

    code, body = _post_error(slow_server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert code == 409
    assert body["code"] == "job_active"
    assert [item["id"] for item in body["conflicts"]] == [job["id"]]

    _, payload, _ = _get(slow_server, "/api/chapter/B0001/01")
    chapter = json.loads(payload)
    assert [item["id"] for item in chapter["jobs"]] == [job["id"]]


def test_cancel_running_job_returns_cancelled_immediately(slow_server):
    status, job = _post(slow_server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    _wait_job_state(slow_server, job["id"], "annotating")

    status, cancelled = _post(slow_server, "/api/jobs/" + job["id"] + "/cancel", {})
    assert status == 200
    assert cancelled["state"] == "cancelled"
    assert cancelled["error"] == "已取消"

    # 后台线程还在收尾：内联任务不能消失，新任务仍被这一章挡住。
    _, payload, _ = _get(slow_server, "/api/chapter/B0001/01")
    chapter = json.loads(payload)
    assert [item["id"] for item in chapter["jobs"]] == [job["id"]]

    code, body = _post_error(slow_server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["deep"]})
    assert code == 409
    assert body["code"] == "job_active"
