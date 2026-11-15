#!/usr/bin/env python3
"""回答阶段的直连 LLM HTTP 客户端（OpenAI Chat Completions SSE + 进程内工具循环）。

替代每次回答 fork `pi` Node.js 子进程与 `readonly_tools.ts`：
- System Prompt < 500 字节（原 `pi` Agent 脚手架 36.6KB / `--no-skills` 7.5KB）
- `book_grep` / `read` 在 Python 进程内直接执行，不经 Node IPC
- 实时解析 SSE chunk：分别发出 thinking / tool_start / tool_end / answer_delta 事件
- 自动对齐 `books.toml`、`~/.pi/agent/models.json` 与 `~/.pi/agent/auth.json`；
  若当前模型未配置 OpenAI 兼容 HTTP 端点或设了 `ANSWER_BACKEND=pi`，调用方退回 `pi`。
"""

from __future__ import annotations

import http.client
import json
import os
import select
import socket
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import answer_tools
import model_call
import runlog

SYSTEM_PROMPT = (
    "你是书库原文问答助手，只根据给出的书库候选原文与只读工具（book_grep、read）作答。\n"
    "严禁编造原文中不存在的事实、人物、数字或引文；所有结论都要有原文支撑。\n"
    "严格按用户要求输出单个合法 JSON 对象，不要代码块、不要额外说明文字。"
)

REPAIR_SYSTEM_PROMPT = (
    "你是 JSON 格式修复器。把用户提供的损坏文本修成单个合法 JSON 对象，保持原有字段与正文引文含义，不要代码块或解释。"
)

CONNECT_TIMEOUT = 15
CHUNK_READ_TIMEOUT = 90
DEFAULT_TIMEOUT = 240
MAX_TOOL_TURNS = 12


def _loopback_reachable(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return True
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_api_key(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("$"):
        return os.environ.get(text[1:].strip(), "").strip()
    if text.startswith("!"):
        # 不在直连客户端里执行任意 shell 命令拿密钥，交由 pi 兜底。
        return ""
    return text


def _map_thinking(thinking: str, model_entry: dict[str, Any]) -> str | None:
    level = str(thinking or "").strip().lower()
    if not level:
        return None
    tmap = model_entry.get("thinkingLevelMap")
    if isinstance(tmap, dict) and level in tmap:
        mapped = tmap[level]
        return str(mapped).strip() if isinstance(mapped, str) and mapped.strip() else None
    if level == "off":
        return None
    if model_entry.get("reasoning") is False:
        return None
    return level


def _chat_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def resolve_config(
    root: str | Path,
    *,
    model_ref: str = "",
    thinking: str = "low",
    backend: str | None = None,
) -> dict[str, Any] | None:
    """解析直连 HTTP 端点配置；不可直连或指定 `pi` 后端时返回 `None`。"""
    mode = (backend if backend is not None else os.environ.get("ANSWER_BACKEND") or "auto").strip().lower()
    if mode == "pi":
        return None

    values: dict[str, str] = {}
    ref = str(model_ref or "").strip()
    try:
        from bookcorpus import pimodel

        cfg = pimodel.load(str(root))
        values = dict(cfg.get("values") or {})
        if not ref:
            ref = str(cfg.get("ref") or "").strip()
    except ImportError:
        pass
    ref = os.environ.get("ANSWER_MODEL", "").strip() or ref or "opencode-go/deepseek-v4.1-flash"

    # 1. 项目 books.toml / 环境变量显式配置的 OpenAI 兼容端点优先
    base_url = values.get("base_url", "").strip()
    api_kind = values.get("api", "").strip()
    if base_url and api_kind in ("", "openai-completions"):
        key_env = values.get("api_key_env", "").strip()
        api_key = os.environ.get(key_env, "").strip() if key_env else ""
        model_id = values.get("model", "").strip() or ref
        if "/" in model_id and values.get("provider") and model_id.startswith(values["provider"] + "/"):
            model_id = model_id.split("/", 1)[1]
        return {
            "provider": values.get("provider", "") or "custom",
            "model_ref": ref,
            "model": model_id,
            "url": _chat_url(base_url),
            "api_key": api_key,
            "headers": {},
            "send_session_affinity": False,
            "reasoning_effort": None if str(thinking).lower() in ("", "off") else str(thinking).lower(),
            "cost": {},
            "thinking": str(thinking or ""),
        }

    # 2. `~/.pi/agent/models.json` + `~/.pi/agent/settings.json`
    agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
    models_doc = _read_json(agent_dir / "models.json")
    settings_doc = _read_json(agent_dir / "settings.json")
    providers = models_doc.get("providers") if isinstance(models_doc.get("providers"), dict) else {}

    ordered_providers: list[str] = []
    if "/" in ref:
        head = ref.split("/", 1)[0]
        if head in providers:
            ordered_providers.append(head)
    default_provider = str(settings_doc.get("defaultProvider") or "").strip()
    if default_provider and default_provider in providers and default_provider not in ordered_providers:
        ordered_providers.append(default_provider)
    for name in providers:
        if name not in ordered_providers:
            ordered_providers.append(name)

    for pname in ordered_providers:
        pcfg = providers.get(pname)
        if not isinstance(pcfg, dict):
            continue
        if pcfg.get("api") not in (None, "", "openai-completions"):
            continue
        url = _chat_url(str(pcfg.get("baseUrl") or ""))
        if not url:
            continue
        raw_models = pcfg.get("models") if isinstance(pcfg.get("models"), list) else []
        matched: dict[str, Any] | None = None
        candidates = [ref]
        if "/" in ref and ref.startswith(pname + "/"):
            candidates.append(ref.split("/", 1)[1])
        for item in raw_models:
            if isinstance(item, dict) and str(item.get("id") or "") in candidates:
                matched = item
                break
        if matched is None:
            continue
        api_key = _resolve_api_key(str(pcfg.get("apiKey") or ""))
        if pcfg.get("apiKey") and not api_key:
            continue
        if not url or not _loopback_reachable(url):
            continue
        compat = pcfg.get("compat") if isinstance(pcfg.get("compat"), dict) else {}
        raw_hdrs = pcfg.get("headers")
        hdrs = {str(k): str(v) for k, v in raw_hdrs.items()} if isinstance(raw_hdrs, dict) else {}
        m_compat = matched.get("compat") if isinstance(matched.get("compat"), dict) else {}
        thinking_fmt = str(m_compat.get("thinkingFormat") or compat.get("thinkingFormat") or "")
        req_reasoning = bool(
            m_compat.get("requiresReasoningContentOnAssistantMessages")
            or compat.get("requiresReasoningContentOnAssistantMessages")
        )
        return {
            "provider": pname,
            "model_ref": ref,
            "model": str(matched.get("id") or ref),
            "url": url,
            "api_key": api_key,
            "headers": hdrs,
            "send_session_affinity": bool(compat.get("sendSessionAffinityHeaders")),
            "reasoning_effort": _map_thinking(thinking, matched),
            "thinking_format": thinking_fmt,
            "requires_reasoning_content": req_reasoning,
            "cost": dict(matched.get("cost") or {}) if isinstance(matched.get("cost"), dict) else {},
            "thinking": str(thinking or ""),
        }

    # 3. `~/.pi/agent/auth.json` 兜底 opencode-go 直连（对齐 pi-ai opencode-go/openai 端点）
    auth_doc = _read_json(agent_dir / "auth.json")
    if ref.startswith("opencode-go/") and isinstance(auth_doc.get("opencode-go"), dict):
        key = str(auth_doc["opencode-go"].get("key") or "").strip()
        if key:
            model_id = ref.split("/", 1)[1]
            is_ds = "deepseek" in model_id.lower()
            effort = None if str(thinking).lower() in ("", "off") else str(thinking).lower()
            return {
                "provider": "opencode-go",
                "model_ref": ref,
                "model": model_id,
                "url": "https://opencode.ai/inference/go/openai/v1/chat/completions",
                "api_key": key,
                "headers": {"User-Agent": "opencode/1.18.16"},
                "send_session_affinity": True,
                "reasoning_effort": effort,
                "thinking_format": "deepseek" if is_ds else "",
                "requires_reasoning_content": is_ds,
                "cost": {},
                "thinking": str(thinking or ""),
            }

    return None


class _AnswerStreamExtractor:
    """增量状态机：从流式 JSON token 里实时剥出顶层 `"answer"` 字段的正文。

    只要 `"answer"` 尚未开始（如先发工具或空白），不吐出多余的 JSON 语法符号；
    一旦 `"answer": "` 开始，立即按 JSON 字符串转义规则解码为纯净中文文本。
    """

    def __init__(self) -> None:
        self._state = "scan"  # scan | in_str | after_str | in_answer | done
        self._depth = 0
        self._esc = False
        self._str_buf: list[str] = []
        self._in_key = False
        self._pending_key: str | None = None
        self._unicode_buf: str | None = None
        self.text = ""

    def _emit_answer_char(self, ch: str, out: list[str]) -> None:
        if self._unicode_buf is not None:
            if ch in "0123456789abcdefABCDEF":
                self._unicode_buf += ch
                if len(self._unicode_buf) == 4:
                    out.append(chr(int(self._unicode_buf, 16)))
                    self._unicode_buf = None
                return
            self._unicode_buf = None
        if self._esc:
            self._esc = False
            if ch == "n":
                out.append("\n")
            elif ch == "r":
                pass
            elif ch == "t":
                out.append("\t")
            elif ch in ('"', "\\", "/"):
                out.append(ch)
            elif ch == "u":
                self._unicode_buf = ""
            else:
                out.append(ch)
            return
        if ch == "\\":
            self._esc = True
            return
        if ch == '"':
            self._state = "done"
            return
        out.append(ch)

    def feed(self, chunk: str) -> str:
        if not chunk or self._state == "done":
            return ""
        out: list[str] = []
        for ch in chunk:
            if self._state == "done":
                break
            if self._state == "in_answer":
                self._emit_answer_char(ch, out)
                continue
            if self._state == "in_str":
                if self._esc:
                    self._esc = False
                    if self._in_key:
                        self._str_buf.append(ch)
                    continue
                if ch == "\\":
                    self._esc = True
                    continue
                if ch == '"':
                    if self._in_key:
                        self._pending_key = "".join(self._str_buf)
                    self._str_buf.clear()
                    self._in_key = False
                    self._state = "after_str"
                elif self._in_key:
                    self._str_buf.append(ch)
                continue
            # scan / after_str
            if ch.isspace():
                continue
            if self._state == "after_str":
                if ch == ":":
                    self._state = "expect_val"
                    continue
                self._pending_key = None
                self._state = "scan"
            if self._state == "expect_val":
                if ch == '"' and self._depth == 1 and self._pending_key == "answer":
                    self._state = "in_answer"
                    self._esc = False
                    self._unicode_buf = None
                    continue
                self._pending_key = None
                self._state = "scan"
            if ch == "{":
                self._depth += 1
            elif ch == "}":
                self._depth = max(0, self._depth - 1)
            elif ch == '"':
                self._state = "in_str"
                self._esc = False
                self._in_key = self._depth == 1
                self._str_buf.clear()
        emitted = "".join(out)
        if emitted:
            self.text += emitted
        return emitted

    @property
    def done(self) -> bool:
        """Return True once the JSON ``answer`` string has reached its closing quote."""
        return self._state == "done"


def _compute_cost(usage: dict[str, Any], cost_cfg: dict[str, Any]) -> dict[str, float]:
    def per_m(key: str) -> float:
        val = cost_cfg.get(key)
        return float(val) if isinstance(val, (int, float)) else 0.0

    inp = int(usage.get("input") or 0)
    out = int(usage.get("output") or 0)
    cache_read = int(usage.get("cacheRead") or 0)
    cache_write = int(usage.get("cacheWrite") or 0)
    c_in = inp * per_m("input") / 1_000_000.0
    c_out = out * per_m("output") / 1_000_000.0
    c_cr = cache_read * per_m("cacheRead") / 1_000_000.0
    c_cw = cache_write * per_m("cacheWrite") / 1_000_000.0
    return {
        "input": c_in,
        "output": c_out,
        "cacheRead": c_cr,
        "cacheWrite": c_cw,
        "total": c_in + c_out + c_cr + c_cw,
    }


def _normalize_usage(raw: dict[str, Any] | None, cost_cfg: dict[str, Any]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("prompt_tokens_details") if isinstance(raw.get("prompt_tokens_details"), dict) else {}
    comp_details = (
        raw.get("completion_tokens_details") if isinstance(raw.get("completion_tokens_details"), dict) else {}
    )
    cache_read = int(details.get("cached_tokens") or 0)
    prompt_tokens = int(raw.get("prompt_tokens") or 0)
    uncached_input = max(0, prompt_tokens - cache_read)
    completion_tokens = int(raw.get("completion_tokens") or 0)
    reasoning_tokens = int(comp_details.get("reasoning_tokens") or 0)
    total_tokens = int(raw.get("total_tokens") or (prompt_tokens + completion_tokens))
    out = {
        "input": uncached_input,
        "output": completion_tokens,
        "cacheRead": cache_read,
        "cacheWrite": 0,
        "reasoning": reasoning_tokens,
        "totalTokens": total_tokens,
    }
    out["cost"] = _compute_cost(out, cost_cfg)
    return out


def _has_buffered_bytes(resp: http.client.HTTPResponse, sock: Any) -> bool:
    try:
        if getattr(sock, "pending", None) and sock.pending() > 0:
            return True
    except OSError:
        pass
    try:
        peek = getattr(resp.fp, "peek", None)
        if peek and len(peek(1)) > 0:
            return True
    except (OSError, ValueError):
        pass
    return False


def _wait_sock_or_cancel(
    sock: Any,
    cancel_event: threading.Event | None,
    deadline: float,
    chunk_timeout: float,
) -> str:
    """在有取消信号时按 0.25s 短步等 socket 可读，确保暂停后很快停止读取。"""
    if cancel_event is None or sock is None:
        return "ok"
    idle_deadline = min(deadline, time.monotonic() + float(chunk_timeout))
    while True:
        if cancel_event.is_set():
            return "PAUSED"
        try:
            if getattr(sock, "pending", None) and sock.pending() > 0:
                return "ok"
        except OSError:
            return "ok"
        now = time.monotonic()
        if now >= deadline or now >= idle_deadline:
            return "TIMED_OUT"
        step = min(0.25, max(0.02, min(deadline, idle_deadline) - now))
        try:
            ready, _, _ = select.select([sock], [], [], step)
        except (OSError, ValueError, TypeError):
            return "ok"
        if ready:
            return "ok"


def _http_stream_turn(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    *,
    session_id: str,
    deadline: float,
    on_delta: Callable[[str, str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, Any], str]:
    """发一轮 OpenAI-compatible SSE 请求，返回 `(turn_result, error)`。"""
    if cancel_event is not None and cancel_event.is_set():
        return {}, "PAUSED"
    parsed = urlsplit(cfg["url"])
    is_https = parsed.scheme.lower() == "https"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if is_https else 80)
    target = parsed.path or "/v1/chat/completions"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    remaining = max(1.0, deadline - time.monotonic())
    conn_timeout = min(float(CONNECT_TIMEOUT), remaining)
    conn: http.client.HTTPConnection
    if is_https:
        conn = http.client.HTTPSConnection(host, port, timeout=conn_timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(host, port, timeout=conn_timeout)

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    if cfg.get("send_session_affinity"):
        headers["x-opencode-session"] = session_id
        headers["x-session-id"] = session_id
    for k, v in (cfg.get("headers") or {}).items():
        headers[k] = v

    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    t0 = time.monotonic()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_by_idx: dict[int, dict[str, Any]] = {}
    usage_raw: dict[str, Any] = {}
    stop_reason = ""
    raw_lines: list[str] = []

    def _partial_result() -> dict[str, Any]:
        return {
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "tool_calls": [],
            "usage": _normalize_usage(usage_raw, cfg.get("cost") or {}),
            "stop_reason": stop_reason,
            "duration_ms": round(runlog.ms_since(t0), 1),
            "raw_stream": "\n".join(raw_lines),
        }

    try:
        conn.request("POST", target, body=raw_body, headers=headers)
        if cancel_event is not None:
            wait_st = _wait_sock_or_cancel(conn.sock, cancel_event, deadline, CHUNK_READ_TIMEOUT)
            if wait_st != "ok":
                return {}, wait_st
        if conn.sock is not None:
            conn.sock.settimeout(min(float(CHUNK_READ_TIMEOUT), max(1.0, deadline - time.monotonic())))
        resp = conn.getresponse()
        ctype = (resp.getheader("Content-Type") or "").lower()
        if resp.status >= 400:
            err_body = resp.read(2048).decode("utf-8", errors="replace").strip()
            return {}, f"HTTP {resp.status}: {err_body[:240]}"

        # 非 SSE 网关兜底：一次读完 JSON 响应
        if "text/event-stream" not in ctype:
            raw_json = resp.read().decode("utf-8", errors="replace")
            raw_lines.append(raw_json)
            doc = json.loads(raw_json)
            if isinstance(doc.get("error"), dict):
                return {}, str(doc["error"].get("message") or doc["error"])[:240]
            choices = doc.get("choices") or []
            if choices and isinstance(choices[0], dict):
                choice = choices[0]
                stop_reason = str(choice.get("finish_reason") or "")
                msg = choice.get("message") or {}
                txt = str(msg.get("content") or "")
                reas = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
                if reas:
                    reasoning_parts.append(reas)
                    if on_delta:
                        on_delta("reasoning", reas, "")
                if txt:
                    content_parts.append(txt)
                    if on_delta:
                        on_delta("content", txt, "")
                for idx, tc in enumerate(msg.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    tool_calls_by_idx[idx] = {
                        "id": str(tc.get("id") or f"call_{idx}"),
                        "type": "function",
                        "function": {"name": str(fn.get("name") or ""), "arguments": str(fn.get("arguments") or "{}")},
                    }
            if isinstance(doc.get("usage"), dict):
                usage_raw = doc["usage"]
        else:
            # 标准 SSE 流式读取
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return _partial_result(), "PAUSED"
                if time.monotonic() >= deadline:
                    return {}, "TIMED_OUT"
                if cancel_event is not None and conn.sock is not None and not _has_buffered_bytes(resp, conn.sock):
                    wait_st = _wait_sock_or_cancel(conn.sock, cancel_event, deadline, CHUNK_READ_TIMEOUT)
                    if wait_st == "PAUSED":
                        return _partial_result(), "PAUSED"
                    if wait_st == "TIMED_OUT":
                        return {}, "TIMED_OUT"
                if conn.sock is not None:
                    conn.sock.settimeout(min(float(CHUNK_READ_TIMEOUT), max(1.0, deadline - time.monotonic())))
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].lstrip()
                if data_str == "[DONE]":
                    break
                raw_lines.append(data_str)
                try:
                    chunk = json.loads(data_str)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if isinstance(chunk.get("error"), dict):
                    return {}, str(chunk["error"].get("message") or chunk["error"])[:240]
                if isinstance(chunk.get("usage"), dict):
                    usage_raw = chunk["usage"]
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                if choice.get("finish_reason"):
                    stop_reason = str(choice["finish_reason"])
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                reas = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reas, str) and reas:
                    reasoning_parts.append(reas)
                    if on_delta:
                        on_delta("reasoning", reas, "")
                txt = delta.get("content")
                if isinstance(txt, str) and txt:
                    content_parts.append(txt)
                    if on_delta:
                        on_delta("content", txt, "")
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list):
                    for tc in tcs:
                        if not isinstance(tc, dict):
                            continue
                        idx = int(tc.get("index") if tc.get("index") is not None else 0)
                        slot = tool_calls_by_idx.setdefault(
                            idx,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if tc.get("id"):
                            slot["id"] = str(tc["id"])
                        fn = tc.get("function")
                        if isinstance(fn, dict):
                            if fn.get("name"):
                                if not slot["function"]["name"] and on_delta:
                                    on_delta("tool_name", str(fn["name"]), slot["id"])
                                slot["function"]["name"] += str(fn["name"])
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += str(fn["arguments"])
    except TimeoutError:
        return {}, "TIMED_OUT"
    except (OSError, http.client.HTTPException, ValueError) as exc:
        return {}, f"{exc.__class__.__name__}: {exc}"
    finally:
        conn.close()

    tool_calls = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]
    for idx, tc in enumerate(tool_calls):
        if not tc["id"]:
            tc["id"] = f"call_{idx}"
    return {
        "content": "".join(content_parts),
        "reasoning": "".join(reasoning_parts),
        "tool_calls": tool_calls,
        "usage": _normalize_usage(usage_raw, cfg.get("cost") or {}),
        "stop_reason": stop_reason,
        "duration_ms": round(runlog.ms_since(t0), 1),
        "raw_stream": "\n".join(raw_lines),
    }, ""


def _build_turn_payload(cfg: dict[str, Any], messages: list[dict[str, Any]], *, use_tools: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    effort = cfg.get("reasoning_effort")
    if cfg.get("thinking_format") == "deepseek":
        payload["thinking"] = {"type": "enabled" if effort else "disabled"}
    if effort:
        payload["reasoning_effort"] = effort
    if use_tools:
        payload["tools"] = answer_tools.TOOL_SCHEMAS
        payload["tool_choice"] = "auto"
    return payload


def _make_turn_delta_handler(
    emit: Callable[[dict[str, Any]], None],
    titles: dict[str, str] | None,
    extractor: _AnswerStreamExtractor | None = None,
) -> Callable[[str, str, str], None]:
    extractor = extractor if extractor is not None else _AnswerStreamExtractor()
    think_state: dict[str, Any] = {"announced": False, "last_ts": 0.0, "chars": 0}
    tool_seen: set[str] = set()
    cit_sent = {"v": False}

    def handle_delta(kind_delta: str, delta_val: str, _extra: str) -> None:
        if kind_delta == "reasoning":
            think_state["chars"] += len(delta_val)
            now_ts = time.monotonic()
            if not think_state["announced"] or now_ts - float(think_state["last_ts"]) >= 0.8:
                think_state["announced"] = True
                think_state["last_ts"] = now_ts
                emit({"type": "thinking", "chars": think_state["chars"], "message": "正在梳理原文依据…"})
        elif kind_delta == "tool_name":
            if delta_val not in tool_seen:
                tool_seen.add(delta_val)
                emit(
                    {
                        "type": "stage",
                        "stage": "tool_prepare",
                        "tool": delta_val,
                        "message": answer_tools.friendly_tool_label(delta_val, None, titles),
                    }
                )
        elif kind_delta == "content":
            decoded = extractor.feed(delta_val)
            if decoded:
                emit({"type": "answer_delta", "delta": decoded, "text": extractor.text})
            if not cit_sent["v"] and extractor.done and extractor.text.strip():
                cit_sent["v"] = True
                emit(
                    {
                        "type": "stage",
                        "stage": "citations",
                        "message": "正文已生成，正在整理并核对原文引文…",
                    }
                )

    return handle_delta


def invoke_answer(
    prompt: str,
    md_roots: list[str],
    *,
    root: str | Path,
    cfg: dict[str, Any],
    kind: str = "answer",
    rec: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    use_tools: bool = True,
    system_prompt: str = SYSTEM_PROMPT,
    titles: dict[str, str] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
    checkpoint_out: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
) -> model_call.Result:
    """直连 HTTP 跑完多轮工具循环并返回与 `model_call.Result` 同构的结果对象。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    result = model_call.Result()
    result.mode = "direct"
    t0 = time.monotonic()
    deadline = t0 + max(5, int(timeout))
    session_id = f"ses_{uuid.uuid4().hex[:24]}"
    model_ref = str(cfg.get("model_ref") or cfg.get("model") or "")

    rec.event(
        "model.start",
        call_kind=kind,
        mode="direct",
        timeout=timeout,
        prompt_chars=len(prompt or ""),
        prompt_sha256=runlog.sha256(prompt or ""),
        parser="answer",
        executable="direct_llm",
        model=model_ref,
        provider=cfg.get("provider", ""),
        thinking=cfg.get("thinking", ""),
    )
    tools_names = [t["function"]["name"] for t in answer_tools.TOOL_SCHEMAS] if use_tools else []
    rec.event(
        "model.system",
        sections=["direct_system"],
        tools=tools_names,
        chars=len(system_prompt),
        **rec.large(system_prompt, "system", inline=1000),
    )

    resumed_from = resume_state if isinstance(resume_state, dict) else None
    if resumed_from and isinstance(resumed_from.get("messages"), list) and resumed_from["messages"]:
        messages = [dict(m) for m in resumed_from["messages"]]
        partial_raw_prev = str(resumed_from.get("partial_raw") or "")
        if partial_raw_prev.strip():
            messages.append({"role": "assistant", "content": partial_raw_prev})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "刚才回答已暂停，现在继续完成。"
                        "请直接接着已有原文依据与前文内容，"
                        "输出完整且合法的 JSON 对象（包含完整的 answer 与 citations 字段）。"
                    ),
                }
            )
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    executor = answer_tools.ToolExecutor(root, md_roots) if use_tools else None
    raw_chunks: list[str] = []
    last_text = ""

    def emit(ev: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(ev)

    def save_checkpoint(partial_raw: str = "", partial_text: str = "") -> None:
        cp = {
            "messages": [dict(m) for m in messages],
            "partial_raw": partial_raw,
            "partial_text": partial_text,
        }
        result.checkpoint = cp
        if checkpoint_out is not None:
            checkpoint_out.clear()
            checkpoint_out.update(cp)

    def mark_paused(partial_raw: str = "", partial_text: str = "") -> None:
        fallback = str((resumed_from or {}).get("partial_text") or "")
        shown = partial_text or fallback
        save_checkpoint(partial_raw=partial_raw, partial_text=shown)
        result.paused = True
        result.error = "PAUSED"
        result.text = shown
        emit({"type": "paused", "partial_text": shown, "message": "已暂停，点击「继续问答」可接续生成"})

    emit(
        {
            "type": "stage",
            "stage": "model",
            "message": "正在从暂停处继续生成回答…" if resumed_from else "正在联系模型分析原文…",
        }
    )

    paused_mid_turn = False
    for turn_idx in range(MAX_TOOL_TURNS):
        if cancel_event is not None and cancel_event.is_set():
            mark_paused()
            paused_mid_turn = True
            break
        if time.monotonic() >= deadline:
            result.timed_out = True
            result.error = f"回答超时（{timeout} 秒）"
            break

        payload = _build_turn_payload(cfg, messages, use_tools=use_tools)
        extractor = _AnswerStreamExtractor()
        handle_delta = _make_turn_delta_handler(emit, titles, extractor=extractor)
        stream_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "deadline": deadline,
            "on_delta": handle_delta,
        }
        if cancel_event is not None:
            stream_kwargs["cancel_event"] = cancel_event
        turn_res, err = _http_stream_turn(cfg, payload, **stream_kwargs)
        if err:
            if err == "PAUSED":
                mark_paused(
                    partial_raw=str(turn_res.get("content") or last_text or ""),
                    partial_text=extractor.text,
                )
                paused_mid_turn = True
            elif err == "TIMED_OUT":
                result.timed_out = True
                result.error = f"回答超时（{timeout} 秒）"
            else:
                result.exit_code = 1
                result.stderr = err
                result.error = f"模型调用失败：{err[:240]}"
            break

        if turn_res.get("raw_stream"):
            raw_chunks.append(turn_res["raw_stream"])
        if turn_res.get("reasoning"):
            result.reasoning.append(
                {
                    "message_index": turn_idx,
                    "thinking": turn_res["reasoning"],
                    "chars": len(turn_res["reasoning"]),
                }
            )
        if turn_res.get("usage"):
            result.usage.append(
                {
                    "message_index": turn_idx,
                    "provider": cfg.get("provider", ""),
                    "model": cfg.get("model", ""),
                    "usage": turn_res["usage"],
                    "duration_ms": turn_res.get("duration_ms"),
                    "stop_reason": turn_res.get("stop_reason", ""),
                }
            )

        if turn_res.get("content"):
            last_text = turn_res["content"]

        tool_calls = turn_res.get("tool_calls") or []
        if not tool_calls or not use_tools or executor is None:
            result.text = turn_res.get("content") or last_text
            result.exit_code = 0
            break

        turn_base_len = len(messages)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": turn_res.get("content") or None,
            "tool_calls": tool_calls,
        }
        if cfg.get("requires_reasoning_content") and turn_res.get("reasoning"):
            assistant_msg["reasoning_content"] = turn_res["reasoning"]
        messages.append(assistant_msg)
        for tc in tool_calls:
            if cancel_event is not None and cancel_event.is_set():
                del messages[turn_base_len:]
                mark_paused()
                paused_mid_turn = True
                break
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            raw_args = str(fn.get("arguments") or "{}")
            call_id = str(tc.get("id") or "")
            try:
                preview_args = json.loads(raw_args) if raw_args.strip() else {}
            except ValueError:
                preview_args = {}
            label = answer_tools.friendly_tool_label(name, preview_args, titles)
            emit({"type": "tool_start", "id": call_id, "name": name, "args": preview_args, "message": label})

            t_tool = time.monotonic()
            out_text, is_error, parsed_args = executor.execute(name, raw_args)
            dur_ms = round(runlog.ms_since(t_tool), 1)
            result.tool_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "args": parsed_args,
                    "result": out_text,
                    "is_error": is_error,
                    "duration_ms": dur_ms,
                }
            )
            emit({"type": "tool_end", "id": call_id, "name": name, "is_error": is_error, "duration_ms": dur_ms})
            messages.append({"role": "tool", "tool_call_id": call_id, "content": out_text})
        if paused_mid_turn:
            break
    else:
        # 达到最大工具轮次，做最后一轮不带 tools 的收尾输出
        if not result.text and not result.error and not result.paused:
            messages.append({"role": "user", "content": "工具查询已达上限，请直接基于已有原文按要求的 JSON 格式作答。"})
            payload = _build_turn_payload(cfg, messages, use_tools=False)
            extractor_final = _AnswerStreamExtractor()
            final_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "deadline": deadline,
                "on_delta": _make_turn_delta_handler(emit, titles, extractor=extractor_final),
            }
            if cancel_event is not None:
                final_kwargs["cancel_event"] = cancel_event
            turn_res, err = _http_stream_turn(cfg, payload, **final_kwargs)
            if err == "PAUSED":
                mark_paused(
                    partial_raw=str(turn_res.get("content") or last_text or ""),
                    partial_text=extractor_final.text,
                )
            elif err == "TIMED_OUT":
                result.timed_out = True
                result.error = f"回答超时（{timeout} 秒）"
            elif err:
                result.error = f"模型调用失败：{err[:240]}"
                result.exit_code = 1
            else:
                result.text = turn_res.get("content") or last_text
                result.exit_code = 0
                if turn_res.get("usage"):
                    result.usage.append(
                        {
                            "message_index": MAX_TOOL_TURNS,
                            "provider": cfg.get("provider", ""),
                            "model": cfg.get("model", ""),
                            "usage": turn_res["usage"],
                            "duration_ms": turn_res.get("duration_ms"),
                            "stop_reason": turn_res.get("stop_reason", ""),
                        }
                    )

    if not result.error and not result.paused and not (result.text or "").strip():
        result.error = "模型没有输出"

    result.raw_stream = "\n".join(raw_chunks)
    result.ms = runlog.ms_since(t0)

    # 与 model_call.invoke 同构写入 runlog，既有复盘与探针脚本零修改可读
    for call in result.tool_calls:
        args_text = json.dumps(call.get("args"), ensure_ascii=False, default=str)
        res_text = str(call.get("result") or "")
        fields = {
            "call_id": call.get("id", ""),
            "name": call.get("name", ""),
            "is_error": bool(call.get("is_error")),
            "duration_ms": call.get("duration_ms"),
            "args_chars": len(args_text),
            "result_chars": len(res_text),
        }
        fields.update(rec.large(args_text, "args", inline=800))
        fields.update(rec.large(res_text, "result", inline=800))
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
        rec.event("model.usage_total", **model_call._sum_usage(result.usage))
    result.text_blob = rec.blob(result.text) if result.text else None
    if result.text_blob:
        rec.event(
            "model.text",
            **result.text_blob,
            text_preview=runlog.short(result.text, 400),
            parser="answer",
        )
    rec.event(
        "model.end",
        mode="direct",
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
            parser="answer",
            mode="direct",
            raw_blob=raw_info,
            raw_sha256=raw_info["sha256"],
            parsed_sha256=model_call._parsed_sha("answer", result.text),
            chars=len(result.text),
        )
    total = model_call._sum_usage(result.usage)
    rec.set_index(
        model=model_ref,
        tool_calls=len(result.tool_calls),
        events=len(result.tool_calls) + len(result.usage),
        total_tokens=total.get("totalTokens"),
        model_ms=result.ms,
        model_error=result.error,
    )
    return result
