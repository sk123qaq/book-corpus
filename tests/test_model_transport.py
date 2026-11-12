"""模型传输层：分类、重试、超时、对端地址都必须可验证。

用真的回环 HTTP 服务制造每一种对端行为，不用 mock 顶替 socket——
这次要修的就是「异常从哪来」，替身会把被替掉的那一层正好藏起来。
"""

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bookcorpus.reading import events, jobs, model
from bookcorpus.reading import model as model_mod
from bookcorpus.reading.depth import model as depth_model


class _AbortError(Exception):
    """处理器主动断连后用它跳出，避免基类再写响应。"""


class _Recorder:
    def __init__(self, respond):
        self.respond = respond
        self.hits = 0


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.server.bodies.append(self.rfile.read(length))
        self.server.received_headers.append(dict(self.headers))
        self.server.recorder.hits += 1
        try:
            self.server.recorder.respond(self, self.server.recorder.hits)
        except (_AbortError, OSError, ValueError):
            return

    def log_message(self, *args):
        return

    def finish(self):
        with contextlib.suppress(OSError, ValueError):
            super().finish()


def _send_json(handler, payload, status=200, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def _ok_body(text="{}"):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"total_tokens": 3}}


def _close_without_response(handler, _hits):
    """对端在状态行之前干净关闭：正是 RemoteDisconnected 的唯一来源。"""
    handler.close_connection = True
    with contextlib.suppress(OSError):
        handler.connection.shutdown(socket.SHUT_WR)
    raise _AbortError


def _truncate_body(handler, _hits):
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", "200")
    handler.end_headers()
    handler.wfile.write(b'{"choices":')
    handler.wfile.flush()
    handler.close_connection = True
    with contextlib.suppress(OSError):
        handler.connection.shutdown(socket.SHUT_WR)
    raise _AbortError


@contextlib.contextmanager
def _serve(respond):
    server = _Server(("127.0.0.1", 0), _Handler)
    server.recorder = _Recorder(respond)
    server.bodies = []
    server.received_headers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield server.recorder, f"http://{host}:{port}/inference/go/v1/chat/completions", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _client(url, *, attempts=model.MAX_ATTEMPTS, read_timeout=2.0, deadline=30.0, fallback=False):
    client = model.ModelClient(endpoint=url, key="sk-test", timeout=read_timeout, backoff=(0, 0, 0), max_tokens=64)
    client.attempts = attempts
    client.deadline = deadline
    client.fallback_model = model.FALLBACK_MODEL if fallback else None
    return client


MESSAGES = [{"role": "user", "content": "只回一个空对象"}]


def test_remote_disconnect_is_retried_then_classified():
    with _serve(_close_without_response) as (recorder, url, _server):
        client = _client(url)
        with pytest.raises(model.ModelTransportError, match="对端在返回状态行前关闭了连接") as info:
            client.complete_json(MESSAGES)
        exc = info.value
        assert exc.kind == "transport_closed"
        assert exc.attempt == 4
        assert exc.retries_exhausted is True
        assert recorder.hits == 4
        # 不能再是那句让人无从下手的裸英文：先给分类，再给原始细节。
        assert str(exc).startswith("模型调用失败（对端在返回状态行前关闭了连接，阶段 read）")
        assert "transport_closed" in str(exc)
        assert exc.peer == "127.0.0.1"
        assert exc.elapsed_ms >= 0


def test_retry_succeeds_after_one_disconnect():
    def respond(handler, hits):
        if hits == 1:
            _close_without_response(handler)
        _send_json(handler, _ok_body('{"ok": true}'))

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2)
        content, usage = client.complete_json(MESSAGES)
        assert content == {"ok": True}
        assert recorder.hits == 2
        assert usage["total_tokens"] == 3


def test_rate_limited_honours_retry_after():
    def respond(handler, hits):
        if hits == 1:
            _send_json(handler, {"error": "slow down"}, status=429, headers={"Retry-After": "0"})
        _send_json(handler, _ok_body())

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2)
        content, _usage = client.complete_json(MESSAGES)
        assert content == {}
        assert recorder.hits == 2


def test_server_error_exhausts_attempts():
    def respond(handler, _hits):
        _send_json(handler, {"error": "upstream"}, status=500)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2)
        with pytest.raises(model.ModelProviderError, match="模型 HTTP 500") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "http_5xx"
        assert info.value.attempt == 2
        assert recorder.hits == 2


def test_server_error_then_success():
    def respond(handler, hits):
        if hits <= 3:
            _send_json(handler, {"error": "upstream"}, status=500)
        _send_json(handler, _ok_body('{"ok": 1}'))

    with _serve(respond) as (recorder, url, _server):
        client = _client(url)
        content, _usage = client.complete_json(MESSAGES)
        assert content == {"ok": 1}
        assert recorder.hits == 4


def test_truncated_output_is_not_retried():
    def respond(handler, _hits):
        body = {
            "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
            "usage": {"total_tokens": 9},
        }
        _send_json(handler, body)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=3)
        with pytest.raises(model.ModelTruncatedError, match="max_tokens 截断") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "truncated"
        assert recorder.hits == 1


def test_client_error_is_not_retried():
    def respond(handler, _hits):
        _send_json(handler, {"error": "bad"}, status=400)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=3)
        with pytest.raises(model.ModelProviderError, match="模型 HTTP 400") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "http_4xx"
        assert recorder.hits == 1


def test_auth_error_is_not_retried():
    def respond(handler, _hits):
        _send_json(handler, {"error": "bad key"}, status=401)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=3)
        with pytest.raises(model.ModelAuthError, match="鉴权失败") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "auth"
        assert recorder.hits == 1


def test_truncated_body_is_retried():
    with _serve(_truncate_body) as (recorder, url, _server):
        client = _client(url, attempts=2)
        with pytest.raises(model.ModelTransportError, match="响应正文不完整") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "transport_truncated"
        assert recorder.hits == 2


def test_read_timeout_is_retried_and_classified():
    def respond(handler, _hits):
        time.sleep(0.35)
        _send_json(handler, _ok_body())

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2, read_timeout=0.12)
        with pytest.raises(model.ModelTransportError, match="等待响应超时") as info:
            client.complete_json(MESSAGES)
        assert info.value.kind == "transport_timeout"
        assert info.value.attempt == 2
        assert recorder.hits == 2


def test_bad_endpoint_is_a_config_error():
    client = model.ModelClient(endpoint="ftp://example.com/x", key="sk-test")
    with pytest.raises(model.ModelConfigError, match="只支持 http/https"):
        client.complete_json(MESSAGES)


def test_job_queue_writes_transport_log_to_work_tree(tmp_path):
    (tmp_path / "阅读").mkdir()
    with _serve(_close_without_response) as (_recorder, url, _server):

        def runner(job_id, job):
            del job_id, job
            _client(url, attempts=1).complete_json(MESSAGES)

        queue = jobs.JobQueue(str(tmp_path), runner, max_workers=1)
        try:
            job = queue.submit("B0005", 10, [], kind="depth", layers=["deep"], model="m", effort="high")
            state = None
            deadline = time.time() + 10
            while time.time() < deadline:
                state = queue.get(job["id"])
                if state and state["state"] == "failed":
                    break
                time.sleep(0.05)
            assert state is not None
            assert state["state"] == "failed"
            assert "对端在返回状态行前关闭了连接" in (state["error"] or "")
            log_path = tmp_path / "阅读" / "logs" / (job["id"] + ".jsonl")
            assert log_path.exists()
            kinds = [json.loads(line)["kind"] for line in log_path.read_text(encoding="utf-8").splitlines()]
            assert "http_error" in kinds
        finally:
            queue.shutdown()


def test_events_record_peer_phase_and_timing(tmp_path):
    with _serve(_close_without_response) as (_recorder, url, _server):
        client = _client(url, attempts=1)
        client.stream_mode = "off"
        token = events.bind(events.EventLog(tmp_path, "job-transport"))
        try:
            with pytest.raises(model.ModelTransportError):
                client.complete_json(MESSAGES)
        finally:
            events.unbind(token)
        lines = (tmp_path / "job-transport.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    kinds = [record["kind"] for record in records]
    assert kinds[0] == "http_start"
    assert "http_error" in kinds
    error = next(record for record in records if record["kind"] == "http_error")
    assert error["error_kind"] == "transport_closed"
    assert error["phase"] == "read"
    assert error["peer"] == "127.0.0.1"
    assert error["family"] == "ipv4"
    assert isinstance(error["connect_ms"], int)
    assert "first_byte_ms" not in error
    start = next(record for record in records if record["kind"] == "http_start")
    assert start["bytes_out"] > 0
    assert start["stream"] is False


class _ExhaustedClient:
    """已经用光共享重试策略的客户端：上层不许再叠一次。"""

    def __init__(self):
        self.calls = 0

    def complete_json(self, messages, model=None, effort=None):
        del messages, model, effort
        self.calls += 1
        exc = model_mod.ModelTransportError(
            "模型调用失败（对端在返回状态行前关闭了连接，阶段 read）：transport_closed",
            kind="transport_closed",
            attempt=4,
            peer="104.18.10.66",
        )
        exc.retries_exhausted = True
        raise exc


def test_depth_layer_does_not_stack_a_second_retry():
    client = _ExhaustedClient()
    with pytest.raises(model.ModelProviderError, match="对端在返回状态行前关闭了连接"):
        depth_model._complete_json(client, MESSAGES, "m", "high")
    assert client.calls == 1


def _sse(handler, chunks, delay=0.0):
    """按 SSE 逐块写，flush 让客户端立刻看见状态行。"""
    body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    data = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    for chunk in chunks:
        line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
        handler.wfile.write(line)
        handler.wfile.flush()
        if delay:
            time.sleep(delay)
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


def _delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def test_streaming_assembles_deltas_and_records_first_delta(tmp_path):
    def respond(handler, hits):
        _sse(handler, [_delta('{"units":'), _delta("[]}")])

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        token = events.bind(events.EventLog(recorder_log := tmp_path, "job-stream"))
        try:
            payload, usage = client.complete_json([{"role": "user", "content": "hi"}])
        finally:
            events.unbind(token)
    assert payload == {"units": []}
    assert usage == {}
    records = [json.loads(line) for line in (recorder_log / "job-stream.jsonl").read_text().splitlines()]
    start = records[0]
    assert start["kind"] == "http_start"
    assert start["stream"] is True
    response = next(record for record in records if record["kind"] == "http_response")
    assert response["status"] == 200
    # 流式可用的直接证据：首块到达远早于整段读完（这正是旧实现缺的那件事）。
    assert response["first_delta_ms"] is not None
    assert response["first_delta_ms"] < 1000
    assert response["chunks"] == 2
    assert recorder.hits == 1


def test_streaming_inter_chunk_timeout_is_short():
    # 块间超时走 stream_read_timeout，与「等整段响应」的 600 秒无关。
    def respond(handler, hits):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(f"data: {json.dumps(_delta('{'))}\n\n".encode())
        handler.wfile.flush()
        time.sleep(0.6)

    with _serve(respond) as (_recorder, url, _server):
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        client.stream_read_timeout = 0.15
        with pytest.raises(model.ModelTransportError) as caught:
            client.complete_json([{"role": "user", "content": "hi"}])
    assert caught.value.kind == "transport_timeout"


def test_non_sse_response_falls_back_and_is_remembered():
    def respond(handler, hits):
        _send_json(handler, {"choices": [{"message": {"content": '{"ok": 1}'}}], "usage": {}})

    with _serve(respond) as (recorder, url, _server):
        key = f"http://{url.split('//')[1].split('/')[0]}"
        model._STREAM_UNSUPPORTED.discard(key)
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        payload, _usage = client.complete_json([{"role": "user", "content": "hi"}])
        assert payload == {"ok": 1}
        assert recorder.hits == 1
        second, _usage = client.complete_json([{"role": "user", "content": "hi"}])
        assert second == {"ok": 1}
    bodies = [json.loads(raw) for raw in _server.bodies]
    assert bodies[0].get("stream") is True
    assert "stream" not in bodies[1]  # 记过之后不再试流式
    assert recorder.hits == 2
    model._STREAM_UNSUPPORTED.clear()


def test_auth_failure_does_not_trigger_fallback_model():
    def respond(handler, hits):
        handler.send_response(401)
        handler.send_header("Content-Length", "2")
        handler.end_headers()
        handler.wfile.write(b"no")

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2, fallback=True)
        with pytest.raises(model.ModelAuthError):
            client.complete_json([{"role": "user", "content": "hi"}], model="primary-model")
    assert recorder.hits == 1
    assert json.loads(_server.bodies[0])["model"] == "primary-model"


def test_truncated_output_does_not_trigger_fallback_model():
    def respond(handler, hits):
        _send_json(
            handler,
            {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}], "usage": {}},
        )

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2, fallback=True)
        with pytest.raises(model.ModelTruncatedError):
            client.complete_json([{"role": "user", "content": "hi"}], model="primary-model")
    assert recorder.hits == 1
    assert json.loads(_server.bodies[0])["model"] == "primary-model"


def test_transport_failure_switches_to_fallback_model_once():
    seen: list[int] = []

    def respond(handler, hits):
        if json.loads(handler.server.bodies[-1])["model"] == "primary-model":
            _close_without_response(handler, hits)
        # 处理备用模型请求的这一刻，读的是活的 client.attempts。
        seen.append(handler.server.client.attempts)
        _send_json(handler, {"choices": [{"message": {"content": '{"ok": 1}'}}], "usage": {}})

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=4, fallback=True)
        _server.client = client
        payload, _usage = client.complete_json([{"role": "user", "content": "hi"}], model="primary-model")
        # 备用模型那次不许改共享的 attempts：服务里四个 worker 共用一个 client。
        assert client.attempts == 4
        assert client.last_model == model.FALLBACK_MODEL
        assert client.last_fallback is True
    assert payload == {"ok": 1}
    # 主模型 4 次 + 备用 1 次；备用那次不许把共享预算改成 1。
    assert recorder.hits == 5
    assert json.loads(_server.bodies[4])["model"] == model.FALLBACK_MODEL
    assert seen == [4]


def test_stream_rejected_response_format_is_dropped_and_retried():
    # 这台网关 400 掉 response_format：去掉它重发，而不是把流式整条关掉。
    def respond(handler, hits):
        raw = handler.server.bodies[-1]
        if b"response_format" in raw:
            handler.send_response(400)
            payload = b'{"error":{"param":"response_format","message":"the request is invalid"}}'
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)
            return
        _sse(handler, [_delta('{"ok": 2}')])

    key = None
    with _serve(respond) as (recorder, url, _server):
        key = f"http://{url.split('//')[1].split('/')[0]}"
        model._NO_JSON_MODE.discard(key)
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        payload, _usage = client.complete_json([{"role": "user", "content": "hi"}])
    assert payload == {"ok": 2}
    assert recorder.hits == 2
    assert b"response_format" not in _server.bodies[1]
    assert key in model._NO_JSON_MODE
    model._NO_JSON_MODE.discard(key)


def test_stream_closed_before_done_is_truncated():
    def respond(handler, _hits):
        body = (f"data: {json.dumps(_delta('半截'), ensure_ascii=False)}\n\n").encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        # 声明比实际更长的长度：客户端读到 EOF 时既没 [DONE] 也没 finish_reason。
        handler.send_header("Content-Length", str(len(body) + 200))
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        with pytest.raises(model.ModelTransportError) as excinfo:
            client.complete_json(MESSAGES)
    assert excinfo.value.kind == "transport_truncated"
    assert recorder.hits == 1


def test_stream_error_chunk_is_not_an_empty_success():
    def respond(handler, _hits):
        lines = [
            f"data: {json.dumps(_delta('开头'), ensure_ascii=False)}\n\n",
            'data: {"error": {"type": "server_error", "message": "boom"}}\n\n',
        ]
        body = "".join(lines).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=1)
        client.stream_mode = "auto"
        with pytest.raises(model.ModelTransportError) as excinfo:
            client.complete_json(MESSAGES)
    assert excinfo.value.kind == "transport_truncated"
    assert "流式返回错误" in str(excinfo.value)
    assert recorder.hits == 1


def test_stream_deadline_bounds_a_slow_stream():
    def respond(handler, _hits):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", "100000")
        handler.end_headers()
        for _ in range(40):
            handler.wfile.write(f"data: {json.dumps(_delta('x'))}\n\n".encode())
            handler.wfile.flush()
            time.sleep(0.05)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=1, read_timeout=30.0, deadline=30.0)
        client.stream_mode = "auto"
        client.stream_read_timeout = 5.0
        client.stream_deadline = 0.4
        started = time.monotonic()
        with pytest.raises(model.ModelTransportError) as excinfo:
            client.complete_json(MESSAGES)
        elapsed = time.monotonic() - started
    assert excinfo.value.kind == "transport_timeout"
    # 滴流能把 90 秒的块间超时无限续命，只有墙钟预算拦得住。
    assert elapsed < 3.0
    assert recorder.hits == 1


def test_no_json_mode_downgrade_also_applies_without_streaming():
    def respond(handler, _hits):
        if b"response_format" in handler.server.bodies[-1]:
            payload = b'{"error":{"param":"response_format","message":"the request is invalid"}}'
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)
            return
        _send_json(handler, {"choices": [{"message": {"content": '{"ok": 3}'}}], "usage": {}})

    with _serve(respond) as (recorder, url, _server):
        key = f"http://{url.split('//')[1].split('/')[0]}"
        model._NO_JSON_MODE.discard(key)
        client = _client(url, attempts=1)
        client.stream_mode = "off"
        payload, _usage = client.complete_json(MESSAGES)
    assert payload == {"ok": 3}
    assert recorder.hits == 2
    assert b"response_format" not in _server.bodies[1]
    model._NO_JSON_MODE.discard(key)


def test_fallback_failure_restores_shared_attempts():
    def respond(handler, hits):
        _close_without_response(handler, hits)

    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=4, fallback=True)
        with pytest.raises(model.ModelTransportError):
            client.complete_json([{"role": "user", "content": "hi"}], model="primary-model")
        # 备用也失败时最容易把 attempts 永久改小，这里必须还是 4。
        assert client.attempts == 4
    assert recorder.hits == 5


def test_annotate_auth_failure_does_not_switch_to_fallback_model():
    def respond(handler, hits):
        handler.send_response(401)
        handler.send_header("Content-Length", "2")
        handler.end_headers()
        handler.wfile.write(b"no")

    book = {"title": "示例书"}
    chapter = {"title": "第一章", "n": 1}
    blocks = [{"id": "B01", "kind": "para", "text": "正文"}]
    with _serve(respond) as (recorder, url, _server):
        client = _client(url, attempts=2, fallback=True)
        with pytest.raises(model.ModelAuthError):
            client.annotate_with_meta(book, chapter, blocks, model="primary-model")
    # 标注路径也必须有门槛：鉴权坏了不该悄悄换模型把成功写在账上。
    assert recorder.hits == 1
    assert json.loads(_server.bodies[0])["model"] == "primary-model"
