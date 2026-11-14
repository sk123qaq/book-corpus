"""模型调用器的离线验收：JSONL 事件流、工具调用、纯文本降级。"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "demo"))

import model_call
import runlog

STREAM = (
    "\n".join(
        [
            json.dumps({"type": "session", "version": 3, "id": "s1"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "先想"},
                            {
                                "type": "toolCall",
                                "id": "t1",
                                "name": "book_grep",
                                "arguments": {"pattern": "上海"},
                            },
                            {"type": "text", "text": "答"},
                        ],
                        "usage": {
                            "input": 10,
                            "output": 5,
                            "cacheRead": 100,
                            "cacheWrite": 2,
                            "totalTokens": 15,
                            "cost": {"total": 0.001},
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "t1",
                    "toolName": "book_grep",
                    "args": {"pattern": "上海"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "t1",
                    "toolName": "book_grep",
                    "result": {"content": [{"type": "text", "text": "命中"}]},
                    "isError": False,
                    "durationMs": 12,
                }
            ),
            json.dumps({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 2, "delayMs": 10}),
            json.dumps({"type": "auto_retry_end", "success": True, "attempt": 1}),
        ]
    )
    + "\n"
)


def test_parse_stream_reads_text_tools_and_usage():
    parsed = model_call.parse_stream(STREAM)
    assert parsed["text"] == "答"
    assert parsed["reasoning"][0]["thinking"] == "先想"
    assert parsed["tool_calls"][0]["name"] == "book_grep"
    assert parsed["tool_calls"][0]["args"] == {"pattern": "上海"}
    assert parsed["tool_calls"][0]["duration_ms"] == 12
    assert parsed["usage_total"]["totalTokens"] == 15
    assert parsed["retries"][0]["attempt"] == 1


def test_invoke_json_records_final_and_tools(tmp_path, monkeypatch):
    runlog.configure(str(tmp_path))
    rec = runlog.start("terms", question="问")
    monkeypatch.setattr(model_call, "_run", lambda *a, **k: (0, STREAM, "", False, 3.0))
    result = model_call.invoke(
        "p",
        ["pi", "--mode", "json", "-p", "--model", "test/model"],
        ["pi", "-p", "--model", "test/model"],
        kind="terms",
        rec=rec,
        parser="terms",
    )
    assert result.mode == "json"
    assert result.text == "答"
    assert result.tool_calls[0]["name"] == "book_grep"
    rec.finish("ok")
    events = runlog.read_run(rec.id)["events"]
    assert any(row["type"] == "model.final" for row in events)
    assert any(row["type"] == "model.tool" for row in events)
    start = next(row for row in events if row["type"] == "model.start")
    assert start["model"] == "test/model"


def test_invoke_falls_back_to_text(tmp_path, monkeypatch):
    runlog.configure(str(tmp_path))
    rec = runlog.start("terms", question="问")
    monkeypatch.setattr(model_call, "_run", lambda *a, **k: (0, "纯文本", "", False, 2.0))
    result = model_call.invoke(
        "p",
        ["pi", "--mode", "json", "-p"],
        ["pi", "-p"],
        kind="terms",
        rec=rec,
        parser="terms",
    )
    assert result.mode == "text"
    assert result.text == "纯文本"
    rec.finish("ok")
    events = runlog.read_run(rec.id)["events"]
    assert any(row["type"] == "model.fallback" for row in events)
