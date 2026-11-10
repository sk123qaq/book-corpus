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

from bookcorpus.reading.service import ReadingService

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
def no_key_server(reading_root):
    """没有任何模型凭据：预检必须在排队前给出 400，而不是让任务跑一半才炸。"""
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


@pytest.fixture
def unsearchable_server(reading_root):
    proc, base = _start_server(
        reading_root,
        {
            "READING_DEPTH": "1",
            "READING_ANNOTATE": "0",
            "OPENCODE_GO_API_KEY": "test-key",
            "READING_SEARCH_PROVIDER": "none",
            "PI_WEB_SEARCH_CONFIG": str(reading_root.parent / "no-web-search.json"),
        },
    )
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
                "READING_MODEL_URL": f"http://127.0.0.1:{hang.getsockname()[1]}/v1/chat/completions",
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
    # 现有用例按旧路径断言；默认切换只由专门的用例显式打开。
    env["READING_DEPTH"] = "0"
    env["READING_AGENT_STANDARD"] = "0"
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


def _wait_job_state(base, job_id, target, timeout=15):
    deadline = time.time() + timeout
    current = None
    while time.time() < deadline:
        _, body, _ = _get(base, "/api/jobs/" + job_id)
        current = json.loads(body)
        if current["state"] == target:
            return current
        if current["state"] not in ACTIVE_STATES:
            raise AssertionError(f"job left active before {target}: {current}")
        time.sleep(0.01)
    raise AssertionError(f"job never reached {target}: {current}")


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
    assert "深度生产" in body
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


def test_missing_model_key_returns_400_before_queueing(no_key_server):
    status, body = _post_error(
        no_key_server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["skim"], "overwrite": True}
    )
    assert status == 400
    assert body["code"] == "missing_config"
    assert body["degradable"] is False
    assert [item["key"] for item in body["missing"]] == ["reading.model"]
    assert body["missing"][0]["fix"]


def test_edition_without_model_key_returns_400_with_fix(no_key_server):
    status, body = _post_error(no_key_server, "/api/edition/B0001/1", {"model": "fake", "effort": "high"})
    assert status == 400
    assert body["code"] == "missing_config"
    assert body["missing"][0]["key"] == "reading.model"
    assert "OPENCODE_GO_API_KEY" in body["missing"][0]["fix"]


def test_missing_search_provider_returns_degradable_payload(unsearchable_server):
    status, body = _post_error(
        unsearchable_server,
        "/api/depth/B0001/1",
        {"layers": ["research"], "model": "fake", "effort": "high"},
    )
    assert status == 400
    assert body["code"] == "missing_config"
    assert body["degradable"] is True
    assert [item["key"] for item in body["missing"]] == ["reading.search"]


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


def test_editorial_api_roundtrip(server, reading_root):
    status, job = _post(server, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"]})
    assert status == 202
    deadline = time.time() + 15
    while time.time() < deadline:
        _, body, _ = _get(server, "/api/jobs/" + job["id"])
        current = json.loads(body)
        if current["state"] not in ACTIVE_STATES:
            break
        time.sleep(0.05)
    assert current["state"] == "done", current

    _, body, _ = _get(server, "/api/chapter/B0001/01")
    chapter = json.loads(body)
    blocks = chapter["blocks"]
    payload = {
        "schema": 1,
        "uid": "B0001",
        "n": 1,
        "beats": [
            {
                "id": "beat-1",
                "title": "一个人走进屋子",
                "question": "这封信是谁留下的？",
                "before_block": blocks[0]["id"],
                "tiers": ["standard"],
            }
        ],
        "visuals": [
            {
                "id": "steps-1",
                "kind": "steps",
                "title": "两段之间的一个动作",
                "after_block": blocks[0]["id"],
                "placement": "after",
                "tiers": ["standard"],
                "data": {
                    "steps": [{"label": "进门", "detail": "看见桌上的灯"}, {"label": "坐下", "detail": "听见雨声"}],
                    "result": {"label": "想起", "detail": "许多年前的一个下午"},
                },
                "claims": [{"block": blocks[0]["id"], "quote": "第一段写一个人走进屋子"}],
            }
        ],
        "takeaways": [
            {"text": "一封信把现在和过去接上。", "sources": [blocks[0]["id"]]},
        ],
    }
    status, saved = _post(server, "/api/editorial/B0001/01", payload)
    assert status == 200
    assert saved["uid"] == "B0001"
    assert saved["beats"][0]["id"] == "beat-1"

    status, body, _ = _get(server, "/api/editorial/B0001/01")
    assert status == 200
    assert json.loads(body)["visuals"][0]["kind"] == "steps"
    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    assert (pack / "editorial.json").is_file()

    payload["visuals"][0]["after_block"] = "nope"
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server, "/api/editorial/B0001/01", payload)
    assert exc.value.code == 400


def test_reading_server_new_chapter_uses_depth_job(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    raw = service.library.read_chapter("B0002", 1)
    pack = Path(reading_root) / "工作区" / "阅读" / "B0002" / "ch" / "01"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "uid": "B0002",
                "n": 1,
                "tiers": {},
                "draft": True,
                "source_sha256": raw["source_sha256"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_DEPTH": "1"})
    try:
        status, job = _post(
            base, "/api/jobs", {"uid": "B0001", "n": 1, "tiers": ["standard"], "allow_unverified": True}
        )
        assert status == 202
        assert job["kind"] == "depth"
        # 没有检索 provider 时确认降级：研究层照出，引用层整层不出。
        assert job["layers"] == ["model", "skim", "standard", "deep", "research"]
        assert job["tiers"] == []

        status, old = _post(base, "/api/jobs", {"uid": "B0002", "n": 1, "tiers": ["standard"]})
        assert status == 202
        assert old["kind"] == "reading"
    finally:
        _stop_server(proc)


def test_reading_server_agent_reader_retired(reading_root):
    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "agent-standard.html").write_text("<!doctype html><html><body>旧标准档</body></html>", encoding="utf-8")

    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_AGENT_STANDARD": "0"})
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/reader/B0001/1/agent")
        assert exc.value.code == 404
        assert "退役" in exc.value.read().decode("utf-8")
    finally:
        _stop_server(proc)

    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_AGENT_STANDARD": "1"})
    try:
        status, body, _headers = _get(base, "/reader/B0001/1/agent")
        assert status == 200
        assert "旧标准档" in body
    finally:
        _stop_server(proc)


def test_depth_route_is_404_when_flag_off(reading_root):
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_DEPTH": "0"})
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/reader/B0001/1/depth")
        assert exc.value.code == 404
    finally:
        _stop_server(proc)


def test_depth_route_serves_ready_chapter_and_404s_missing(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    raw = service.library.read_chapter("B0001", 1)
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter.html": "<!doctype html><html><body>编译版</body></html>",
            "state.json": {"status": "ready", "source_sha256": raw["source_sha256"]},
        },
    )
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_DEPTH": "1"})
    try:
        status, body, headers = _get(base, "/reader/B0001/1/depth")
        assert status == 200
        assert "编译版" in body
        assert headers.get_content_type() == "text/html"
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/reader/B0001/2/depth")
        assert exc.value.code == 404
    finally:
        _stop_server(proc)


def test_depth_route_404s_model_state_even_with_old_html(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    raw = service.library.read_chapter("B0001", 1)
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "chapter.html": "<!doctype html><html><body>旧编译版</body></html>",
            "state.json": {"status": "model", "source_sha256": raw["source_sha256"]},
        },
    )
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_DEPTH": "1"})
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base, "/reader/B0001/1/depth")
        assert exc.value.code == 404
        _status, body, _headers = _get(base, "/api/chapter/B0001/1")
        assert json.loads(body)["path"] == "none"
    finally:
        _stop_server(proc)


def test_post_depth_returns_202_job_and_validates_payload(reading_root):
    proc, base = _start_server(reading_root, {"READING_ANNOTATE": "0", "READING_DEPTH": "1"})
    try:
        status, job = _post(
            base,
            "/api/depth/B0001/1",
            {"layers": ["skim"], "model": "fake-model", "effort": "max"},
        )
        assert status == 202
        assert job["kind"] == "depth"
        assert job["tiers"] == []
        assert job["layers"] == ["model", "skim"]
        assert job["model"] == "fake-model"
        assert job["effort"] == "max"

        for payload in (
            {"layers": []},
            {"layers": ["unknown"]},
            {"layers": ["citations"]},
            {"layers": ["skim"], "rebuild_model": "yes"},
            {"layers": ["skim"], "effort": "nope"},
            {"layers": ["skim"], "model": "x" * 81},
        ):
            code, body = _post_error(base, "/api/depth/B0001/1", payload)
            assert code == 400, body
    finally:
        _stop_server(proc)
