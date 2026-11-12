"""异常收口的契约。

两件事分开验，缺一不可：

1. 边界闭包。传输层里任何异常，不管来自 socket、解析还是代码 bug，都必须变成带
   ``kind`` 的 ``ModelProviderError``，并且用 ``from`` 保住原异常。测试用与网络无关
   的类型（``ValueError``/``KeyError``/``ZeroDivisionError``）代表「未来没见过的异常」，
   它们必须落到 ``protocol`` 兜底，而不是裸奔到调用方。
2. 栈不丢。任务层任何崩溃都要写一条 ``job_failed``，带 traceback，能定位到文件行。
   只留 ``str(exc)`` 的账等于没账。

契约的意义在闭包性质：以后接新网关、换新库，抛出的类型都落在这条兜底里，
不需要改测试。
"""

import json
from http.client import IncompleteRead, RemoteDisconnected

import pytest

from bookcorpus.reading import events, model
from bookcorpus.reading.jobs import JobQueue

DISCONNECT = "Remote end closed connection without response"

# (抛出的异常, 期望分类)。前三行是 stdlib 真实会抛的，后三行代表未知异常。
CASES = [
    (RemoteDisconnected(DISCONNECT), "transport_closed"),
    (TimeoutError("timed out"), "transport_timeout"),
    (ConnectionResetError(104, "Connection reset by peer"), "transport_reset"),
    (IncompleteRead(b"", 100), "transport_truncated"),
    (ValueError("响应体不是 JSON"), "protocol"),
    (KeyError("choices"), "protocol"),
    (ZeroDivisionError("division by zero"), "protocol"),
]


def _client():
    client = model.ModelClient(
        endpoint="http://127.0.0.1:9/v1/chat/completions",
        key="sk-test",
        timeout=0.2,
        backoff=(0, 0, 0),
    )
    # 一次就够：这里验的是分类，不是重试次数。
    client.attempts = 1
    client.stream_mode = "off"
    return client


@pytest.mark.parametrize(("raised", "kind"), CASES, ids=[c[1] + ":" + type(c[0]).__name__ for c in CASES])
def test_every_exception_at_the_boundary_comes_out_classified(monkeypatch, raised, kind):
    def raiser(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(model.ModelClient, "_post_once", raiser)
    client = _client()
    with pytest.raises(model.ModelProviderError) as caught:
        client.complete_json([{"role": "user", "content": "x"}], model="m", effort="low")
    assert caught.value.kind == kind
    # from 链没断：原始异常在里面，栈才能顺着 __cause__ 打出来。
    assert caught.value.__cause__ is raised or isinstance(caught.value.__cause__, type(raised))


def test_keyboard_interrupt_is_not_swallowed(monkeypatch):
    def raiser(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(model.ModelClient, "_post_once", raiser)
    with pytest.raises(KeyboardInterrupt):
        _client().complete_json([{"role": "user", "content": "x"}], model="m", effort="low")


def test_boundary_failure_keeps_the_failing_frame_in_the_log(monkeypatch, tmp_path):
    def raiser(*_args, **_kwargs):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(model.ModelClient, "_post_once", raiser)
    client = _client()
    with events.job_scope(tmp_path, "r-contract-boundary"), pytest.raises(model.ModelProviderError):
        client.complete_json([{"role": "user", "content": "x"}], model="m", effort="low")
    records = [json.loads(line) for line in (tmp_path / "r-contract-boundary.jsonl").read_text().splitlines()]
    failure = next(record for record in records if record["kind"] == "http_error")
    assert failure["error_kind"] == "protocol"
    assert failure["error_class"] == "ZeroDivisionError"
    assert failure["phase"] == "connect"


def test_job_crash_is_recorded_with_a_traceback(tmp_path):
    def runner(_job_id, _job):
        raise KeyError("choices")

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", "06", ["standard"])
        deadline = 5
        import time as _time

        end = _time.time() + deadline
        while _time.time() < end and queue.get(job["id"])["state"] not in ("failed", "done", "partial"):
            _time.sleep(0.01)
        stored = queue.get(job["id"])
        assert stored["state"] == "failed"
        assert stored["error"]
        path = tmp_path / "阅读" / "logs" / f"{job['id']}.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        crash = next(record for record in records if record["kind"] == "job_failed")
        assert crash["error_class"] == "KeyError"
        assert "KeyError" in crash["traceback"]
        # 栈要指向真实代码位置，而不是只有一句消息。
        assert "test_error_capture_contract.py" in crash["traceback"]
        assert crash["cause_chain"]
    finally:
        queue.shutdown()


def test_record_exception_survives_an_unwritable_log(tmp_path):
    # 记账失败不许把作业带倒：目录不存在时 emit 吞掉 OSError。
    events.record_exception(ValueError("x"), layer="deep")
    bad = events.EventLog(tmp_path / "不存在" / "更深的目录")
    bad.emit("job_failed", traceback="x")
