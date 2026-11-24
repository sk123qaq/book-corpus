"""Follow-up turns must keep the previous book in scope.

The demo session `20cda5124de1` showed the failure: the first answer ended with
"人类被迁往澳大利亚保留地", the follow-up asked about that phrase, and the
answer moved to B0262 because `_previous` cut the answer at 300 chars and the
new trace's top1 was not the previous book.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "demo"))

import server


def make_turn(n, question, answer="", citations=None, ranking=None, result=None):
    return {
        "n": n,
        "question": question,
        "answer": {"answer": answer, "citations": citations or []},
        "trace": {
            "plan": {"ranking": ranking or [], "chapters": []},
            "result": result or {},
        },
    }


def test_previous_sends_full_answer():
    body = "甲" * 400 + "人类被迁往澳大利亚保留地。"
    sess = {"turns": [make_turn(1, "三体", body)]}

    rows = server._previous(sess, 2)

    assert len(rows) == 1
    assert rows[0]["answer"] == body
    assert "澳大利亚" in rows[0]["answer"]


def test_related_prompt_contains_the_tail_of_the_previous_answer(monkeypatch):
    body = "甲" * 400 + "Z_TAIL：人类被迁往澳大利亚保留地。"
    sess = {"turns": [make_turn(1, "三体", body)]}
    seen = {}

    def fake_call(prompt, thinking, rec=None):
        seen["prompt"] = prompt
        return '{"related": true, "reason": "承接上一轮"}', ""

    monkeypatch.setattr(server.related, "_call", fake_call)

    out = server.related.check("详细讲一下人类被迁往澳大利亚保留地", server._previous(sess, 2), use_cache=False)

    assert out["related"] is True
    assert "Z_TAIL" in seen["prompt"]


def test_previous_source_prefers_the_answer_citation():
    sess = {
        "turns": [
            make_turn(
                1,
                "三体",
                citations=[{"book": "B0024", "chapter": "第一章", "path": "books/B0024/md/03.md", "line": 42}],
                ranking=[{"book": "B0024", "title": "死神永生"}],
            )
        ]
    }

    src = server._previous_source(sess, 2)

    assert src["book"] == "B0024"
    assert src["book_title"] == "死神永生"
    assert src["path"] == "books/B0024/md/03.md"
    assert src["line"] == 42


def test_related_without_anchor_pins_the_previous_book(monkeypatch):
    trace = {
        "plan": {
            "ranking": [{"book": "B0262", "title": "枪炮、病菌与钢铁"}, {"book": "B0024", "title": "死神永生"}],
            "chapters": [{"book": "B0262"}, {"book": "B0024"}],
        },
        "result": {"book": "B0262", "book_title": "枪炮、病菌与钢铁", "path": "books/B0262/md/01.md", "line": 1},
    }
    src = {"book": "B0024", "book_title": "死神永生", "title": "第一章", "path": "books/B0024/md/03.md", "line": 42}
    monkeypatch.setattr(server, "_previous_source", lambda sess, n: src)
    monkeypatch.setattr(
        server.tracer,
        "read_chapter",
        lambda path, line=1, chars=900, before=700: {"path": path, "line": line, "title": "第一章", "text": "原文"},
    )

    out = server._pin_followup(trace, {"turns": []}, 2, {"related": True}, None)

    assert out["result"]["book"] == "B0024"
    assert out["result"]["book_title"] == "死神永生"
    assert out["result"]["path"] == "books/B0024/md/03.md"
    assert out["plan"]["ranking"][0]["book"] == "B0024"
    assert out["plan"]["chapters"][0]["book"] == "B0024"
    assert out["pin"]["book"] == "B0024"


def test_related_without_anchor_inserts_a_missing_previous_book(monkeypatch):
    trace = {
        "plan": {
            "ranking": [{"book": "B0262", "title": "枪炮、病菌与钢铁", "score": 26.1}],
            "chapters": [{"book": "B0262", "title": "枪炮、病菌与钢铁"}],
        },
        "result": {"book": "B0262", "book_title": "枪炮、病菌与钢铁", "path": "books/B0262/md/01.md", "line": 1},
    }
    prev = make_turn(
        1,
        "三体",
        citations=[{"book": "B0024", "chapter": "第一章", "path": "books/B0024/md/03.md", "line": 42}],
        ranking=[{"book": "B0024", "title": "死神永生", "score": 40.7}],
    )
    prev["trace"]["plan"]["chapters"] = [
        {
            "book": "B0024",
            "title": "死神永生",
            "selected": [
                {"chapter_path": "books/B0024/md/03.md", "chapter_title": "第一章", "line": 42, "score": 40.7}
            ],
        }
    ]
    src = {"book": "B0024", "book_title": "死神永生", "title": "第一章", "path": "books/B0024/md/03.md", "line": 42}
    monkeypatch.setattr(server, "_previous_source", lambda sess, n: src)
    monkeypatch.setattr(
        server.tracer,
        "read_chapter",
        lambda path, line=1, chars=900, before=700: {"path": path, "line": line, "title": "第一章", "text": "原文"},
    )

    out = server._pin_followup(trace, {"turns": [prev]}, 2, {"related": True}, None)

    assert out["result"]["book"] == "B0024"
    assert out["plan"]["ranking"][0]["book"] == "B0024"
    assert out["plan"]["ranking"][0]["score"] == 40.7
    assert out["plan"]["chapters"][0]["book"] == "B0024"
    assert out["plan"]["chapters"][0]["selected"][0]["chapter_path"] == "books/B0024/md/03.md"


def test_unrelated_or_anchored_turns_keep_the_new_result(monkeypatch):
    trace = {
        "plan": {"ranking": [{"book": "B0262", "title": "枪炮、病菌与钢铁"}], "chapters": []},
        "result": {"book": "B0262", "book_title": "枪炮、病菌与钢铁", "path": "books/B0262/md/01.md", "line": 1},
    }
    monkeypatch.setattr(
        server, "_previous_source", lambda sess, n: {"book": "B0024", "path": "books/B0024/md/03.md", "line": 42}
    )

    assert server._pin_followup(trace, {"turns": []}, 2, {"related": False}, None) is trace
    anchor = {"path": "books/B0024/md/03.md", "line": 42}
    assert server._pin_followup(trace, {"turns": []}, 2, {"related": True}, anchor) is trace


class FakeHandler:
    def __init__(self):
        self.code = None
        self.payload = None

    def fail(self, code, message):
        raise AssertionError((code, message))

    def send_json(self, code, payload):
        self.code = code
        self.payload = payload


def test_api_run_pins_a_related_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(server.session, "SESS_DIR", str(tmp_path))
    sess = server.session.create()
    sess["turns"] = [
        make_turn(
            1,
            "三体",
            answer="第一答" * 200,
            citations=[{"book": "B0024", "chapter": "第一章", "path": "books/B0024/md/03.md", "line": 42}],
            ranking=[{"book": "B0024", "title": "死神永生"}],
        )
    ]
    server.session.save(sess)

    trace = {
        "thinking": "low",
        "plan": {"ranking": [{"book": "B0262", "title": "枪炮、病菌与钢铁"}], "chapters": []},
        "result": {"book": "B0262", "book_title": "枪炮、病菌与钢铁", "path": "books/B0262/md/01.md", "line": 1},
    }
    monkeypatch.setattr(server.tracer, "build", lambda *args, **kwargs: trace)
    monkeypatch.setattr(
        server.related,
        "check",
        lambda question, previous, rec=None: {
            "related": True,
            "reason": "承接上一轮",
            "against": [1],
            "ms": 0,
            "source": "model",
            "thinking": "low",
        },
    )
    monkeypatch.setattr(
        server.tracer,
        "read_chapter",
        lambda path, line=1, chars=900, before=700: {"path": path, "line": line, "title": "第一章", "text": "原文"},
    )

    fake = FakeHandler()
    server.Handler.api_run(fake, "详细讲一下人类被迁往澳大利亚保留地", 0, session_id=sess["id"], turn=2)

    assert fake.code == 200
    assert fake.payload["result"]["book"] == "B0024"
    saved = server.session.load(sess["id"])
    assert saved["turns"][1]["related"]["related"] is True
    assert saved["turns"][1]["trace"]["result"]["book"] == "B0024"


def test_related_call_prefers_direct_llm_when_configured(monkeypatch, tmp_path):
    server.related.runlog.configure(str(tmp_path / "runlog"))
    rec = server.related.runlog.start("related", question="继续讲")
    seen = {}

    monkeypatch.setenv("ANSWER_BACKEND", "auto")
    monkeypatch.setattr(
        server.related.direct_llm,
        "resolve_config",
        lambda root, model_ref="", thinking="low", backend=None: {
            "provider": "opencode-go",
            "model_ref": model_ref or "test/model",
            "model": "test-model",
            "url": "https://example.invalid/v1/chat/completions",
            "api_key": "k",
            "headers": {},
            "send_session_affinity": False,
            "reasoning_effort": None,
            "thinking": thinking,
        },
    )

    def fake_stream_turn(cfg, payload, **kwargs):
        seen["payload"] = payload
        return (
            {
                "content": '{"related": true, "reason": "直连命中"}',
                "reasoning": "",
                "tool_calls": [],
                "usage": {"input": 10, "output": 5, "totalTokens": 15},
                "duration_ms": 12.0,
                "stop_reason": "stop",
                "raw_stream": "",
            },
            "",
        )

    monkeypatch.setattr(server.related.direct_llm, "_http_stream_turn", fake_stream_turn)
    monkeypatch.setattr(
        server.related.model_call,
        "invoke",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("直连可用时不应拉起 pi 子进程")),
    )

    out = server.related.check(
        "再举例说明",
        [{"n": 1, "question": "三体", "answer": "上一轮回答"}],
        use_cache=False,
        rec=rec,
    )
    rec.finish("ok")

    assert out["related"] is True
    assert out["source"] == "model"
    assert out["reason"] == "直连命中"
    assert "tools" not in seen["payload"]
    assert seen["payload"]["messages"][0]["content"] == server.related.SYSTEM_PROMPT
    events = server.related.runlog.read_run(rec.id)["events"]
    start = next(ev for ev in events if ev["type"] == "model.start")
    final = next(ev for ev in events if ev["type"] == "model.final")
    assert start["mode"] == "direct"
    assert start["parser"] == "related"
    assert final["parser"] == "related"
    assert final["parsed_sha256"]


def test_related_call_falls_back_to_pi_when_answer_backend_is_pi(monkeypatch):
    monkeypatch.setenv("ANSWER_BACKEND", "pi")
    monkeypatch.setattr(
        server.related.direct_llm,
        "invoke_answer",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ANSWER_BACKEND=pi 时不应走 direct_llm")),
    )

    res = server.related.model_call.Result()
    res.exit_code = 0
    res.text = '{"related": false, "reason": "pi 兜底"}'
    seen = {}

    def fake_invoke(*args, **kwargs):
        seen["kind"] = kwargs.get("kind")
        seen["parser"] = kwargs.get("parser")
        return res

    monkeypatch.setattr(server.related.model_call, "invoke", fake_invoke)

    out = server.related.check(
        "换一本书",
        [{"n": 1, "question": "三体", "answer": "上一轮回答"}],
        use_cache=False,
    )
    assert out["related"] is False
    assert out["source"] == "model"
    assert out["reason"] == "pi 兜底"
    assert seen == {"kind": "related", "parser": "related"}
