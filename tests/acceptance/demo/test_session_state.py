#!/usr/bin/env python3
"""会话回答状态的离线验收：字段、状态转移、旧会话派生、重启恢复、删除、同轮去重。

  PYTHONPATH=src python3 -m pytest tests/acceptance/demo/test_session_state.py -q

不调模型：会话目录换成 tmp，同轮去重只测 job 键与 Event 复用。
"""
import os
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEMO = os.path.join(REPO, "apps", "demo")
os.environ.setdefault("BOOKS_ROOT",
                      os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/文档/book-corpus-data"))
if DEMO not in sys.path:
    sys.path.insert(0, DEMO)

import server  # noqa: E402
import session  # noqa: E402

SID = "0123456789ab"
TRACE = {"plan": {"ranking": []}, "result": {}}


@pytest.fixture(autouse=True)
def sess_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path / "sessions"))
    with server.ANSWER_JOBS_LOCK:
        server.ANSWER_JOBS.clear()
    yield
    with server.ANSWER_JOBS_LOCK:
        server.ANSWER_JOBS.clear()


def save_turns(turns):
    sess = session.create()
    sess["id"] = SID
    sess["turns"] = turns
    session.save(sess)
    return sess


def saved_turn():
    """走 _write_turn 落一轮，拿回来的 token/种子字段与真实会话一致。"""
    sess = session.create()
    sess["id"] = SID
    server._write_turn(sess, 1, "问", TRACE, None, None)
    session.save(sess)
    return session.load(SID)


def new_turn(n=1, question="问", trace=TRACE, answer=None):
    turn = {"n": n, "question": question, "at": session.now(),
            "trace": trace, "answer": answer, "context": None}
    return turn


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def live_pid():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])


# ---- 字段与派生 -----------------------------------------------------

def test_write_turn_sets_new_token_and_pending():
    sess = session.create()
    server._write_turn(sess, 1, "问", TRACE, None, None)
    turn = sess["turns"][0]
    assert turn["run_token"] and turn["run_token"] == turn["run_token"].strip()
    assert turn["answer_state"] == "pending"
    assert turn["answer_error"] == ""
    assert turn["answer_attempt"] == 0
    assert turn["answer_owner"] == ""
    assert turn["answer_started_at"] is None and turn["answer_finished_at"] is None

    old = turn["run_token"]
    server._write_turn(sess, 1, "重跑同一问", TRACE, None, None)
    assert len(sess["turns"]) == 1
    assert sess["turns"][0]["run_token"] != old
    assert sess["turns"][0]["answer_state"] == "pending"


def test_load_derives_legacy_states():
    save_turns([
        new_turn(1, answer={"ok": True, "answer": "答"}),
        new_turn(2, answer={"ok": False, "error": "模型超时"}),
        new_turn(3, trace=TRACE, answer=None),
        new_turn(4, trace=None, answer=None),
    ])
    sess = session.load(SID)
    states = [t["answer_state"] for t in sess["turns"]]
    assert states == ["ready", "error", "incomplete", "run_error"]
    assert sess["turns"][1]["answer_error"] == "模型超时"
    assert all("未知错误" not in (t["answer_error"] or "") for t in sess["turns"])
    assert all(t["run_token"] == "" for t in sess["turns"])


def test_load_keeps_running_for_recovery():
    turn = new_turn()
    turn.update({"answer_state": "running", "answer_owner": "999999",
                 "answer_attempt": 2})
    save_turns([turn])
    loaded = session.load(SID)["turns"][0]
    assert loaded["answer_state"] == "running"
    assert loaded["answer_attempt"] == 2


# ---- 状态转移 -------------------------------------------------------

def test_begin_finish_ready_and_stale_attempt():
    sess = saved_turn()
    token = sess["turns"][0]["run_token"]
    assert session.begin_answer(SID, 1, 1, os.getpid(), token=token) is True
    meta = session.turn_meta(SID, 1)
    assert meta["answer_state"] == "running"
    assert meta["answer_owner"] == str(os.getpid())
    assert meta["answer_started_at"]

    assert session.finish_answer(SID, 1, 1, {"ok": True, "answer": "答"},
                                 {"text": "上文"}, token=token) is True
    meta = session.turn_meta(SID, 1)
    assert meta["answer_state"] == "ready"
    assert meta["answer_error"] == ""
    assert meta["answer_finished_at"]
    assert meta["answer_owner"] == ""
    turn = session.find(session.load(SID), 1)
    assert turn["answer"]["answer"] == "答"
    assert turn["context"] == {"text": "上文"}

    assert session.finish_answer(SID, 1, 0, {"ok": True, "answer": "旧任务"},
                                 None, token=token) is False
    turn = session.find(session.load(SID), 1)
    assert turn["answer"]["answer"] == "答"


def test_finish_rejects_replaced_run_token():
    sess = saved_turn()
    old_token = sess["turns"][0]["run_token"]
    session.begin_answer(SID, 1, 1, os.getpid(), token=old_token)
    reloaded = session.load(SID)
    server._write_turn(reloaded, 1, "重跑同一问", TRACE, None, None)
    session.save(reloaded)
    new_token = session.find(session.load(SID), 1)["run_token"]
    assert new_token != old_token
    assert session.finish_answer(SID, 1, 1, {"ok": True, "answer": "旧"},
                                 None, token=old_token) is False
    assert session.begin_answer(SID, 1, 1, os.getpid(), token=old_token) is False
    assert session.finish_answer(SID, 1, 1, {"ok": True, "answer": "新"},
                                 None, token=new_token) is False  # 还没 begin
    assert session.turn_meta(SID, 1)["answer_state"] == "pending"


def test_fail_answer_sets_error_and_ignores_stale():
    sess = saved_turn()
    token = sess["turns"][0]["run_token"]
    session.begin_answer(SID, 1, 3, os.getpid(), token=token)
    assert session.fail_answer(SID, 1, 3, "模型超时", token=token) is True
    meta = session.turn_meta(SID, 1)
    assert meta["answer_state"] == "error"
    assert meta["answer_error"] == "模型超时"
    assert meta["answer_finished_at"]

    assert session.fail_answer(SID, 1, 2, "旧任务", token=token) is False
    assert session.turn_meta(SID, 1)["answer_error"] == "模型超时"


def test_finish_ok_false_marks_error():
    sess = saved_turn()
    token = sess["turns"][0]["run_token"]
    session.begin_answer(SID, 1, 1, os.getpid(), token=token)
    session.finish_answer(SID, 1, 1, {"ok": False, "error": "没有可用原文"},
                          None, token=token)
    meta = session.turn_meta(SID, 1)
    assert meta["answer_state"] == "error"
    assert meta["answer_error"] == "没有可用原文"
    assert meta["answered"] is True


# ---- 重启恢复 -------------------------------------------------------

def test_recover_interrupted_dead_and_live_owner():
    live = live_pid()
    try:
        turns = []
        for n in range(1, 5):
            turn = new_turn(n)
            turn.update({"answer_state": "running", "answer_attempt": 1})
            turns.append(turn)
        turns[0]["answer_owner"] = ""                      # 空 owner：标
        turns[1]["answer_owner"] = str(dead_pid())         # 已死：标
        turns[2]["answer_owner"] = str(live.pid)           # 别的活进程：不标
        turns[3]["answer_owner"] = str(os.getpid())        # 当前进程：标
        turns.append(dict(new_turn(5), answer={"ok": True, "answer": "答"},
                          answer_state="ready"))
        save_turns(turns)

        assert session.recover_interrupted() == 3
        loaded = session.load(SID)["turns"]
        assert loaded[0]["answer_state"] == "interrupted"
        assert loaded[1]["answer_state"] == "interrupted"
        assert loaded[2]["answer_state"] == "running"
        assert loaded[3]["answer_state"] == "interrupted"
        assert loaded[4]["answer_state"] == "ready"
        assert loaded[0]["answer_error"] == session.INTERRUPTED_ERROR
        assert loaded[0]["answer_finished_at"]
        assert loaded[0]["answer_owner"] == ""
    finally:
        live.terminate()
        live.wait(timeout=10)


# ---- 摘要、公开形状与删除 -------------------------------------------

def test_summaries_and_public_expose_state():
    save_turns([new_turn(1, answer={"ok": True, "answer": "答"})])
    rows = session.summaries()
    assert len(rows) == 1
    assert rows[0]["answer_state"] == "ready"
    assert rows[0]["answer_error"] == ""
    pub = session.public(session.load(SID))
    assert pub["turns"][0]["answer_state"] == "ready"
    assert pub["turns"][0]["answer_error"] == ""


def test_delete_deleted_and_missing():
    save_turns([new_turn()])
    assert session.delete(SID) == "deleted"
    assert session.load(SID) is None
    assert session.delete(SID) == "missing"
    with pytest.raises(ValueError):
        session.delete("not-a-session")


# ---- 同轮去重 -------------------------------------------------------

def test_answer_job_key_includes_run_token():
    assert server.answer_job_key(SID, 1, "tok") == (SID, 1, "tok")
    assert server.answer_job_key(SID, 1, "tok") == server.answer_job_key(SID, 1, "tok")
    assert server.answer_job_key(SID, 1, "tok") != server.answer_job_key(SID, 1, "tok2")
    assert server.answer_job_key(SID, 1, "tok") != server.answer_job_key(SID, 2, "tok")


def test_answer_job_dedupes_and_reuses_result():
    key = server.answer_job_key(SID, 1, "tok")
    job, owner = server.claim_answer_job(key, 1)
    assert owner is True

    seen = {}

    def waiter():
        same, own = server.claim_answer_job(key, 1)
        seen["same"] = same is job
        seen["owner"] = own
        seen["done"] = same["event"].wait(5)
        seen["result"] = same["result"]

    thread = threading.Thread(target=waiter)
    thread.start()
    result = {"ok": True, "answer": "一份"}
    server.publish_answer_job(job, result=result)
    thread.join(5)
    assert seen == {"same": True, "owner": False, "done": True, "result": result}

    fresh_key = server.answer_job_key(SID, 1, "tok2")
    fresh, owner = server.claim_answer_job(fresh_key, 1)
    assert owner is True and fresh is not job


def test_answer_job_error_reaches_waiter():
    key = server.answer_job_key(SID, 1, "tok")
    job, owner = server.claim_answer_job(key, 7)
    assert owner and job["attempt"] == 7
    server.publish_answer_job(job, error=(500, "坏了"))
    assert job["event"].wait(1) is True
    assert job["error"] == (500, "坏了")
    assert job["result"] is None
    assert job["expires"] > time.time()


class FakeHandler(server.Handler):
    """不经过 socket 的 Handler：只收下状态码与响应体。"""

    def __init__(self):
        self.code = None
        self.payload = None

    def fail(self, code, message):
        self.code = code
        self.payload = {"ok": False, "error": message}

    def send_json(self, code, payload):
        self.code = code
        self.payload = payload


def test_api_answer_dedupes_concurrent_requests(monkeypatch):
    saved_turn()
    calls = []

    def slow(*args, **kwargs):
        calls.append(args[0])
        time.sleep(0.3)
        return {"ok": True, "answer": "一份", "error": ""}

    monkeypatch.setattr(server.answerer, "answer_question", slow)
    real_finish = session.finish_answer
    finished = []

    def counting_finish(*args, **kwargs):
        finished.append(args[2])
        return real_finish(*args, **kwargs)

    monkeypatch.setattr(session, "finish_answer", counting_finish)
    handlers = [FakeHandler(), FakeHandler()]

    def go(handler):
        handler.api_answer("问", "", "books/B0001/md/01.md", 1, [], [],
                           session_id=SID, turn=1)

    threads = [threading.Thread(target=go, args=(h,)) for h in handlers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert [h.code for h in handlers] == [200, 200]
    assert len(calls) == 1
    assert finished == [1]  # 只有 owner 落库一次，等待者不重复 finish
    assert handlers[0].payload["answer"] == handlers[1].payload["answer"]
    assert handlers[0].payload["answer_state"] == "ready"
    meta = session.turn_meta(SID, 1)
    assert meta["answer_state"] == "ready"
    assert meta["answer_owner"] == ""


def test_api_answer_stale_job_gets_409(monkeypatch):
    saved_turn()

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return {"ok": True, "answer": "旧任务", "error": ""}

    monkeypatch.setattr(server.answerer, "answer_question", slow)
    handler = FakeHandler()
    thread = threading.Thread(
        target=lambda: handler.api_answer("问", "", "books/B0001/md/01.md", 1, [], [],
                                          session_id=SID, turn=1))
    thread.start()
    time.sleep(0.1)
    current = session.load(SID)
    server._write_turn(current, 1, "重跑同一问", TRACE, None, None)
    session.save(current)
    thread.join(10)
    assert handler.code == 409
    assert session.turn_meta(SID, 1)["answer_state"] == "pending"
    assert session.load(SID)["turns"][0]["answer"] is None


def test_claim_completed_job_reuses_or_reopens_on_fresh():
    key = server.answer_job_key(SID, 1, "tok")
    old, owner = server.claim_answer_job(key, attempt=1)
    assert owner
    server.publish_answer_job(old, result={"ok": True, "answer": "旧"})

    # 普通重复请求（fresh=False）仍拿已完成的结果，不再调模型。
    same, owner = server.claim_answer_job(key, attempt=2)
    assert not owner
    assert same is old

    # 点「重试回答」带 fresh=True：开新任务，旧结果不冒充这一次。
    fresh, owner = server.claim_answer_job(key, attempt=2, fresh=True)
    assert owner
    assert fresh is not old
    assert fresh["attempt"] == 2
    assert not fresh["event"].is_set()
    # 已经拿到旧 job 引用的等待者仍能读到最后一次的结果。
    assert old["event"].is_set()
    assert old["result"] == {"ok": True, "answer": "旧"}

    # 新任务还在跑时，fresh=True 也加入，不并发起第二个模型调用。
    joined, owner = server.claim_answer_job(key, attempt=3, fresh=True)
    assert not owner
    assert joined is fresh
    assert not joined["event"].is_set()


def test_api_run_appends_existing_session_without_truncation(monkeypatch):
    saved_turn()
    first_token = session.load(SID)["turns"][0]["run_token"]
    data = {"thinking": "", "plan": {"ranking": []}, "result": {}}
    monkeypatch.setattr(server.tracer, "build", lambda *a, **k: data)
    monkeypatch.setattr(server.related, "check", lambda q, prev: {"related": False})

    handler = FakeHandler()
    server.Handler.api_run(handler, "第二问", 0, session_id=SID, turn=2)
    assert handler.code == 200
    turns = session.load(SID)["turns"]
    assert [t["n"] for t in turns] == [1, 2]
    assert turns[0]["run_token"] == first_token
    assert turns[1]["answer_state"] == "pending"

    again = FakeHandler()
    server.Handler.api_run(again, "重跑第一问", 0, session_id=SID, turn=1)
    assert again.code == 200
    turns = session.load(SID)["turns"]
    assert [t["n"] for t in turns] == [1]
    assert turns[0]["question"] == "重跑第一问"
    assert turns[0]["run_token"] != first_token


def test_api_answer_retry_after_error_calls_model_again(monkeypatch):
    saved_turn()
    calls = []

    def boom(*args, **kwargs):
        calls.append("boom")
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(server.answerer, "answer_question", boom)
    first = FakeHandler()
    first.api_answer("问", "", "books/B0001/md/01.md", 1, [], [],
                     session_id=SID, turn=1)
    assert first.code == 500
    assert session.turn_meta(SID, 1)["answer_state"] == "error"
    token = session.turn_meta(SID, 1)["run_token"]

    def fine(*args, **kwargs):
        calls.append("fine")
        return {"ok": True, "answer": "重试成功", "error": ""}

    # 普通重复 POST 直接复用上一次的失败，不再调模型。
    duplicate = FakeHandler()
    duplicate.api_answer("问", "", "books/B0001/md/01.md", 1, [], [],
                         session_id=SID, turn=1)
    assert duplicate.code == 500
    assert calls == ["boom"]

    # UI 点「重试回答」带 fresh=True：同一 run_token 也重新调模型。
    monkeypatch.setattr(server.answerer, "answer_question", fine)
    second = FakeHandler()
    second.api_answer("问", "", "books/B0001/md/01.md", 1, [], [],
                      session_id=SID, turn=1, fresh=True)
    assert second.code == 200
    assert calls == ["boom", "fine"]
    meta = session.turn_meta(SID, 1)
    assert meta["run_token"] == token
    assert meta["answer_state"] == "ready"
    assert meta["answer_attempt"] == 2
