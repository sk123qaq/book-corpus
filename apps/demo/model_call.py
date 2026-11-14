#!/usr/bin/env python3
"""统一模型调用器：优先读 pi 的 JSON 事件流，失败退回纯文本。

JSON 模式把系统提示、思考、工具参数/结果/耗时、用量、重试都放在 stdout 的
JSONL 里；stderr 继续当诊断。事件流拿不到时退回旧行为，调用方只看到
(text, error)，不会因为记录层而失败。
"""

import contextlib
import json
import os
import signal
import subprocess
import threading
import time

import runlog

DEFAULT_TIMEOUT = 240


class Result:
    def __init__(self):
        self.text = ""
        self.error = ""
        self.raw_stream = ""
        self.stderr = ""
        self.events = []
        self.tool_calls = []
        self.reasoning = []
        self.usage = []
        self.retries = []
        self.system = None
        self.exit_code = None
        self.timed_out = False
        self.mode = "json"
        self.ms = 0.0
        self.text_blob = None
        self.event_types = {}
        self.nonjson = []


def _num(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value
    return 0


def _sum_usage(items):
    total = {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "reasoning": 0,
        "totalTokens": 0,
        "cost": {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0},
    }
    for item in items:
        usage = item.get("usage") if isinstance(item, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens"):
            total[key] += _num(usage.get(key))
        cost = usage.get("cost")
        if isinstance(cost, dict):
            for key in ("input", "output", "cacheRead", "cacheWrite", "total"):
                total["cost"][key] += float(_num(cost.get(key)))
    return total


def _message_text(message):
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


def _message_thinking(message):
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("thinking", "reasoning"):
            text = block.get("thinking") or block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


def _has_tool_call(message):
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "toolCall" for block in content)


def _tool_text(result):
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            if parts:
                return "".join(parts)
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _tool_call_map(events):
    calls = {}
    order = []

    def add(call_id, name="", args=None):
        if not call_id:
            return None
        if call_id not in calls:
            calls[call_id] = {
                "id": call_id,
                "name": name,
                "args": args,
                "result": None,
                "is_error": False,
                "duration_ms": None,
            }
            order.append(call_id)
        elif name and not calls[call_id]["name"]:
            calls[call_id]["name"] = name
        if args is not None:
            calls[call_id]["args"] = args
        return calls[call_id]

    for event in events:
        kind = event.get("type")
        if kind == "message_update":
            sub = event.get("assistantMessageEvent") or {}
            if sub.get("type") == "toolcall_end":
                call = sub.get("toolCall") or {}
                add(call.get("id"), call.get("name", ""), call.get("arguments"))
            elif sub.get("type") == "toolcall_start":
                add(sub.get("id"), sub.get("toolName", ""), None)
        elif kind == "tool_execution_start":
            call = add(event.get("toolCallId"), event.get("toolName", ""), event.get("args"))
            if call is not None:
                call["started"] = True
        elif kind == "tool_execution_end":
            call = add(event.get("toolCallId"), event.get("toolName", ""), None)
            if call is not None:
                call["result"] = event.get("result")
                call["is_error"] = bool(event.get("isError"))
                call["duration_ms"] = event.get("durationMs")
        elif kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    add(block.get("id"), block.get("name", ""), block.get("arguments"))
    return [calls[call_id] for call_id in order]


def _first_system(messages):
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            return message
    return None


def parse_stream(text):
    """把 pi --mode json 的 stdout 拆成调用结果所需的结构。"""
    events = []
    nonjson = []
    types = {}
    retries = []
    assistant_messages = []
    agent_messages = None
    system = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            nonjson.append(line)
            continue
        if not isinstance(event, dict):
            nonjson.append(line)
            continue
        events.append(event)
        kind = event.get("type", "")
        types[kind] = types.get(kind, 0) + 1
        if kind in ("auto_retry_start", "auto_retry_end"):
            retries.append(event)
        elif kind == "message_end":
            message = event.get("message") or {}
            role = message.get("role")
            if role == "system" and system is None:
                system = message
            elif role == "assistant":
                assistant_messages.append(message)
        elif kind == "agent_end":
            messages = event.get("messages")
            if isinstance(messages, list):
                agent_messages = messages
    messages = agent_messages if agent_messages is not None else assistant_messages
    if system is None:
        system = _first_system(messages)
    assistants = [message for message in messages if isinstance(message, dict) and message.get("role") == "assistant"]
    final = ""
    if assistants:
        last = assistants[-1]
        final = _message_text(last)
        if not final and not _has_tool_call(last):
            for message in reversed(assistants[:-1]):
                text = _message_text(message)
                if text:
                    final = text
                    break
    reasoning = []
    for index, message in enumerate(assistants):
        thinking = _message_thinking(message)
        if thinking:
            reasoning.append({"message_index": index, "thinking": thinking, "chars": len(thinking)})
    if not reasoning:
        for event in events:
            sub = event.get("assistantMessageEvent") or {}
            if sub.get("type") == "thinking_delta" and isinstance(sub.get("delta"), str):
                reasoning.append({"message_index": 0, "thinking": sub["delta"], "chars": len(sub["delta"])})
    usage = []
    for index, message in enumerate(assistants):
        item = message.get("usage")
        if isinstance(item, dict):
            usage.append(
                {
                    "message_index": index,
                    "provider": message.get("provider", ""),
                    "model": message.get("model", ""),
                    "usage": item,
                    "duration_ms": message.get("durationMs"),
                    "stop_reason": message.get("stopReason", ""),
                }
            )
    return {
        "events": events,
        "event_types": types,
        "event_count": len(events),
        "nonjson": nonjson,
        "system": system,
        "messages": messages,
        "text": final,
        "reasoning": reasoning,
        "usage": usage,
        "usage_total": _sum_usage(usage),
        "tool_calls": _tool_call_map(events),
        "retries": retries,
        "agent_end": any(event.get("type") == "agent_end" for event in events),
    }


def _run(argv, prompt, timeout, cwd, env):
    started = time.monotonic()
    full = [str(part) for part in argv] + [prompt]
    try:
        proc = subprocess.Popen(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return None, "", f"{exc.__class__.__name__}: {exc}", False, runlog.ms_since(started)
    state = {"timed_out": False}
    done = threading.Event()
    err_chunks = []

    def read_err():
        if proc.stderr is None:
            return
        for line in proc.stderr:
            err_chunks.append(line)

    def watchdog():
        if done.wait(max(0, timeout)):
            done.set()
            return
        state["timed_out"] = True
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            proc.kill()
        done.set()

    err_thread = threading.Thread(target=read_err, daemon=True)
    watch_thread = threading.Thread(target=watchdog, daemon=True)
    err_thread.start()
    watch_thread.start()
    out_lines = []
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                out_lines.append(line)
        rc = proc.wait()
    except (OSError, ValueError):
        rc = proc.poll()
    finally:
        done.set()
        watch_thread.join(timeout=2)
        err_thread.join(timeout=2)
    return rc, "".join(out_lines), "".join(err_chunks), state["timed_out"], runlog.ms_since(started)


def _error_text(rc, text, stderr, timed_out, timeout, mode):
    if timed_out:
        return f"超时（{timeout} 秒）"
    if rc not in (0, None):
        tail = " ".join((stderr or "").split())[:240]
        return f"模型调用失败（rc={rc}）：{tail}"
    if not (text or "").strip():
        return "模型没有输出"
    return ""


def _parsed_sha(parser, raw):
    """按调用方给的 parser 重解析最终文本，返回可比较的摘要。解析不了返回空串。"""
    if not raw:
        return ""
    try:
        if parser == "terms":
            import trace

            terms, groups = trace._parse_extract(raw)
            if not terms:
                return ""
            payload = {"terms": terms, "groups": groups}
            return runlog.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if parser == "related":
            import related

            parsed = related._parse(raw)
            if not parsed:
                return ""
            return runlog.sha256(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
        if parser == "answer":
            import answer

            parsed = answer.parse_reply(raw)
            if not parsed:
                return ""
            return runlog.sha256(parsed.get("answer", ""))
    except (ImportError, TypeError, ValueError, KeyError):
        return ""
    return ""


def _model_from_argv(argv):
    try:
        index = list(argv).index("--model")
        return str(list(argv)[index + 1])
    except (ValueError, IndexError):
        return ""


def invoke(
    prompt,
    json_argv,
    text_argv,
    *,
    kind="model",
    rec=None,
    timeout=DEFAULT_TIMEOUT,
    cwd=None,
    env=None,
    parser="",
):
    """调用一次模型。json_argv/text_argv 都不含提示词，最后一项由这里补。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    result = Result()
    t0 = time.monotonic()
    model = _model_from_argv(json_argv or []) or _model_from_argv(text_argv or [])
    rec.event(
        "model.start",
        call_kind=kind,
        mode="json" if json_argv else "text",
        timeout=timeout,
        prompt_chars=len(prompt or ""),
        prompt_sha256=runlog.sha256(prompt or ""),
        parser=parser,
        executable=(json_argv or text_argv or [""])[0],
        model=model,
    )
    if json_argv:
        rc, stdout, stderr, timed_out, ms = _run(json_argv, prompt, timeout, cwd, env)
        result.raw_stream = stdout
        result.stderr = stderr
        result.exit_code = rc
        result.timed_out = timed_out
        result.ms = ms
        parsed = parse_stream(stdout)
        result.events = parsed["events"]
        result.tool_calls = parsed["tool_calls"]
        result.reasoning = parsed["reasoning"]
        result.usage = parsed["usage"]
        result.retries = parsed["retries"]
        result.system = parsed["system"]
        result.event_types = parsed["event_types"]
        result.nonjson = parsed["nonjson"]
        if parsed["events"]:
            result.mode = "json"
            result.text = parsed["text"]
            result.error = _error_text(rc, result.text, stderr, timed_out, timeout, "json")
        elif not timed_out:
            rec.event("model.fallback", reason="json-stream-empty", rc=rc, chars=len(stdout))
            json_argv = None
        else:
            result.mode = "json"
            result.text = (stdout or "").strip()
            result.error = _error_text(rc, result.text, stderr, timed_out, timeout, "json")
    if not json_argv:
        if text_argv:
            rc, stdout, stderr, timed_out, ms = _run(text_argv, prompt, timeout, cwd, env)
            result.raw_stream = stdout
            result.stderr = stderr
            result.exit_code = rc
            result.timed_out = timed_out
            result.ms = ms
            result.mode = "text"
            result.text = (stdout or "").strip()
            result.error = _error_text(rc, result.text, stderr, timed_out, timeout, "text")
        elif not result.text:
            result.error = "没有可用的调用命令"
    rec.event(
        "model.events",
        mode=result.mode,
        count=len(result.events),
        types=result.event_types,
        nonjson=len(result.nonjson),
        tool_calls=len(result.tool_calls),
        retries=len(result.retries),
    )
    if result.system:
        sections = result.system.get("sections") if isinstance(result.system.get("sections"), dict) else {}
        tools = []
        for item in result.system.get("toolsAdded") or []:
            if isinstance(item, dict) and item.get("name"):
                tools.append(item["name"])
        system_text = json.dumps(result.system, ensure_ascii=False, default=str)
        rec.event(
            "model.system",
            sections=list(sections.keys()),
            tools=tools,
            chars=len(system_text),
            **rec.large(system_text, "system", inline=1000),
        )
    if result.raw_stream:
        rec.event(
            "model.raw",
            chars=len(result.raw_stream),
            sha256=runlog.sha256(result.raw_stream),
            **rec.large(result.raw_stream, "raw"),
        )
    if result.stderr.strip():
        rec.event("model.stderr", chars=len(result.stderr), **rec.large(result.stderr, "stderr"))
    for call in result.tool_calls:
        args = call.get("args")
        args_text = json.dumps(args, ensure_ascii=False, default=str) if args is not None else ""
        result_text = _tool_text(call.get("result"))
        fields = {
            "call_id": call.get("id", ""),
            "name": call.get("name", ""),
            "is_error": bool(call.get("is_error")),
            "duration_ms": call.get("duration_ms"),
            "args_chars": len(args_text),
            "result_chars": len(result_text),
        }
        fields.update(rec.large(args_text, "args", inline=800))
        fields.update(rec.large(result_text, "result", inline=800))
        rec.event("model.tool", **fields)
    for item in result.reasoning:
        rec.event(
            "model.reasoning",
            message_index=item.get("message_index"),
            chars=item.get("chars"),
            **rec.large(item.get("thinking", ""), "thinking", inline=800),
        )
    for item in result.usage:
        rec.event("model.usage", **item)
    if result.usage:
        rec.event("model.usage_total", **_sum_usage(result.usage))
    for item in result.retries:
        rec.event("model.retry", **item)
    result.text_blob = rec.blob(result.text) if result.text else None
    if result.text_blob:
        rec.event(
            "model.text",
            **result.text_blob,
            text_preview=runlog.short(result.text, 400),
            parser=parser,
        )
    rec.event(
        "model.end",
        mode=result.mode,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        ms=result.ms,
        text_chars=len(result.text),
        stderr_chars=len(result.stderr),
        error=result.error,
    )
    if result.text:
        raw_info = rec.blob(result.text)
        rec.event(
            "model.final",
            parser=parser,
            mode=result.mode,
            raw_blob=raw_info,
            raw_sha256=raw_info["sha256"],
            parsed_sha256=_parsed_sha(parser, result.text),
            chars=len(result.text),
        )
    total = _sum_usage(result.usage)
    rec.set_index(
        model=model or os.environ.get("ANSWER_MODEL", "") or os.environ.get("BOOKS_MODEL", ""),
        tool_calls=len(result.tool_calls),
        events=len(result.events),
        total_tokens=total.get("totalTokens"),
        model_ms=result.ms,
        model_error=result.error,
    )
    result.ms = runlog.ms_since(t0)
    return result
