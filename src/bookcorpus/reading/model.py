"""可选模型标注：OpenAI-compatible chat/completions。

默认开启。测试可用 ``READING_ANNOTATE=0`` / ``--no-annotate`` 走离线草稿；
真实生成失败时直接报错，不再用三档同文草稿冒充成功。
"""

import contextlib
import errno
import hashlib
import http.client
import json
import os
import random
import socket
import ssl
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import events
from .tiers import TIER_ORDER, validate_annotation

ENDPOINT = "https://opencode.ai/inference/go/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4.1-flash"
FALLBACK_MODEL = "glm-5.3-flash"
DEFAULT_EFFORT = "high"
EFFORTS = ("max", "high", "medium", "low")
# 等响应的上限必须长于最长合法响应。按管线真实输出量 24000 token 实测：deepseek 140 秒、
# glm 265 秒、qwen 295 秒，即 81-172 token/秒。600 秒覆盖到 40 token/秒，还留了高峰降速的余量。
# 总截止要装得下「一次读超时 + 一次完整重试」：600 读 + 15 连接 + 2 退避 + 600 读 = 1217，故取 1800。
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 600
DEADLINE = 1800
MAX_ATTEMPTS = 4
BACKOFF = (2.0, 8.0, 30.0)
TIMEOUT = READ_TIMEOUT  # 旧名，外部还有引用
# 流式：状态行随首块立刻到，读超时只管「两块之间」，所以可以短到 90 秒；
# 总截止放长到 1200 秒，给慢模型的整段生成留够时间，又不至于挂一小时。
STREAM_MODE = (os.environ.get("READING_MODEL_STREAM") or "auto").strip().lower()
STREAM_MODES = ("auto", "on", "off")
STREAM_READ_TIMEOUT = 90
STREAM_DEADLINE = 1200
MAX_TOKENS = 32768
PROMPT_VERSION = "phase1-v5"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
# 重试的判据是「再试一次可能不同」，不是「看起来像网络问题」。
RETRYABLE_KINDS = frozenset(
    {
        "transport_closed",
        "transport_timeout",
        "transport_reset",
        "transport_truncated",
        "rate_limited",
        "http_5xx",
    }
)
# 不认流式的端点记在这里（键是 scheme://host:port），下一次直接走非流式。
_STREAM_UNSUPPORTED: set[str] = set()
# 有的网关流式时拒收 response_format，记下来别再发，而不是整条流式关掉。
_NO_JSON_MODE: set[str] = set()

SYSTEM_PROMPT = """你是中文书稿的结构编辑。只输出一个 JSON 对象，不要 markdown 代码围栏，不要解释。
输入是一章中**有文字的块**（id/kind/text），空脚注块已省略。为每块给出 role、priority、spine、deps；
可选给出 supports。再给 4–6 条 cards。规则：
- role 只能是 spine/claim/conclusion/evidence/context/transition/card。
- priority 是 1..3，1 最重要。
- spine 只给真正的骨架块；deps 只能引用已有块 id，且不要把所有块串成一条链。
- cards 是讲解层，每项必须含 id、trigger、text、citations、quotes；
  trigger 必须是某块 id，citations 非空。quotes 必须是对象数组，每项显式写
  {"block":"B03","text":"原文逐字片段"}，block 不能省略、不能为 null；
  quotes 可以是完整句，也可以是 8 字以上的原文短语，不要改写或拼接。
- 不要改写原文，不要合并块。
- supports 必须放在顶层数组，每项是 {"from":"支撑块 id","to":"被支撑的 claim/conclusion id"}；
  不要把 supports 放进 blocks 条目。
- 不要输出 note、rationale、tier_blocks；档位由确定性投影器选块。
- 输入里也会给出 image 块，带 rel 和 hint（紧跟其后的图题）。
  每张 image 块都必须给 caption：一句话写这张图说明什么、为什么值得看，≤40 字；
  不写「如图/下图」，不复述 hint。
JSON 顶层：{"schema":1,"blocks":[...],"supports":[...],"cards":[...]}。
image 块的条目形如 {"id":"B07","role":"evidence","priority":2,"spine":false,"deps":[],"caption":"一句话图注"}。
"""


def _payload_blocks(blocks):
    """Send text blocks plus figures. The hint is the next heading."""
    out = []
    for index, block in enumerate(blocks):
        if block["kind"] == "image":
            hint = ""
            for neighbor in blocks[index + 1 : index + 3]:
                if neighbor["kind"] == "heading" and (neighbor.get("text") or "").strip():
                    hint = neighbor["text"].strip()
                    break
            out.append(
                {
                    "id": block["id"],
                    "kind": "image",
                    "text": "",
                    "rel": block.get("rel", ""),
                    "hint": hint,
                }
            )
            continue
        if (block.get("text") or "").strip():
            out.append({"id": block["id"], "kind": block["kind"], "text": block["text"]})
    return out


class ModelProviderError(RuntimeError):
    """网络/上游/空响应错误；可切换备用模型。

    ``kind`` 是机器可读的分类，账本、重试与降级都按它判断，不再拿错误文本做字符串匹配。
    """

    kind = "provider"
    retryable = False
    # 共享重试策略已经用光；上游那层别再加一次重复尝试。
    retries_exhausted = False

    def __init__(
        self,
        message,
        *,
        kind=None,
        retryable=None,
        attempt=0,
        elapsed_ms=0,
        peer="",
        status=None,
    ):
        super().__init__(message)
        if kind:
            self.kind = kind
        if retryable is not None:
            self.retryable = retryable
        self.attempt = attempt
        self.elapsed_ms = elapsed_ms
        self.peer = peer
        self.status = status


class ModelTransportError(ModelProviderError):
    """链路层失败：对端关连接、读超时、重置、TLS、DNS、拒绝。"""

    kind = "transport_other"


class ModelProtocolError(ModelProviderError):
    """响应不合协议：状态行错、正文截断、不是 JSON、缺 choices。"""

    kind = "protocol"


class ModelAuthError(ModelProviderError):
    """鉴权失败：401/403。重试没有意义。"""

    kind = "auth"


class ModelTruncatedError(ModelProviderError):
    """输出被 max_tokens 截断。同一预算重试必然再截断。"""

    kind = "truncated"


class ModelConfigError(ModelProviderError):
    """配置错：端点不是 http/https、缺主机名、缺 key。"""

    kind = "config"


def _elapsed_ms(started):
    return int((time.perf_counter() - started) * 1000)


def _parse_backoff(value):
    if not value:
        return BACKOFF
    if isinstance(value, (tuple, list)):
        return tuple(float(item) for item in value) or BACKOFF
    try:
        parts = tuple(float(piece) for piece in str(value).split(",") if piece.strip())
    except ValueError:
        return BACKOFF
    return parts or BACKOFF


def _split_endpoint(endpoint):
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https"):
        raise ModelConfigError(f"模型端点只支持 http/https：{endpoint}", kind="config")
    host = parts.hostname or ""
    if not host:
        raise ModelConfigError(f"模型端点缺少主机名：{endpoint}", kind="config")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.scheme, host, port, path


def _peer_of(sock):
    """对端地址。这才是「谁关的」唯一可留证的字段。"""
    if sock is None:
        return "", ""
    try:
        peer = sock.getpeername()
    except OSError:
        return "", ""
    if not isinstance(peer, tuple) or not peer:
        return "", ""
    host = str(peer[0])
    return host, ("ipv6" if ":" in host else "ipv4")


def _classify(exc, phase=None):
    """把异常翻成分类。顺序即优先级：子类先于父类。"""
    if isinstance(exc, ModelProviderError):
        return exc.kind
    if isinstance(exc, http.client.RemoteDisconnected):
        return "transport_closed"
    if isinstance(exc, http.client.IncompleteRead):
        return "transport_truncated"
    if isinstance(exc, TimeoutError):  # socket.timeout 自 3.10 起就是 TimeoutError
        return "transport_timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "transport_refused"
    if isinstance(exc, ConnectionResetError | ConnectionAbortedError | BrokenPipeError):
        return "transport_reset"
    if isinstance(exc, socket.gaierror):
        return "transport_dns"
    if isinstance(exc, ssl.SSLError):
        return "transport_tls"
    if isinstance(exc, http.client.HTTPException):
        return "protocol"
    if isinstance(exc, OSError):
        if exc.errno in (errno.ECONNRESET, errno.EPIPE, errno.ECONNABORTED):
            return "transport_reset"
        if exc.errno == errno.ETIMEDOUT:
            return "transport_timeout"
        if exc.errno == errno.ECONNREFUSED:
            return "transport_refused"
        return "transport_other"
    return "protocol"


def _status_kind(status):
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "http_5xx"
    if status in (401, 403):
        return "auth"
    return "http_4xx"


def _parse_retry_after(value):
    if not value:
        return 0.0
    try:
        seconds = float(str(value).strip())
    except ValueError:
        return 0.0
    return max(0.0, min(seconds, 60.0))


def _transport_error(exc, kind, attempt, elapsed_ms, meta):
    peer = meta.get("peer") or ""
    phase = meta.get("phase") or "?"
    if kind == "protocol":
        return ModelProtocolError(
            f"模型响应不合协议（{phase}）：{exc}",
            kind=kind,
            attempt=attempt,
            elapsed_ms=elapsed_ms,
            peer=peer,
        )
    label = {
        "transport_closed": "对端在返回状态行前关闭了连接",
        "transport_timeout": "等待响应超时",
        "transport_reset": "连接被重置",
        "transport_refused": "连接被拒绝",
        "transport_tls": "TLS 握手失败",
        "transport_dns": "域名解析失败",
        "transport_truncated": "响应正文不完整",
    }.get(kind, "链路失败")
    return ModelTransportError(
        f"模型调用失败（{label}，阶段 {phase}）：{kind}：{exc}",
        kind=kind,
        attempt=attempt,
        elapsed_ms=elapsed_ms,
        peer=peer,
        retryable=kind in RETRYABLE_KINDS,
    )


def prompt_sha256():
    return hashlib.sha256((PROMPT_VERSION + "\n" + SYSTEM_PROMPT).encode("utf-8")).hexdigest()


def _api_key():
    key = os.environ.get("OPENCODE_GO_API_KEY")
    if key:
        return key.strip()
    path = Path("~/.pi/agent/auth.json").expanduser()
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    entry = data.get("opencode-go")
    if isinstance(entry, dict):
        return str(entry.get("key") or "").strip()
    if isinstance(entry, str):
        return entry.strip()
    return str(data.get("opencode-go.key") or "").strip()


class ModelClient:
    def __init__(
        self,
        endpoint=None,
        model=None,
        effort=None,
        timeout=None,
        key=None,
        max_tokens=None,
        backoff=None,
    ):
        self.endpoint = endpoint or os.environ.get("READING_MODEL_URL") or ENDPOINT
        self.model = model or os.environ.get("READING_MODEL_NAME") or DEFAULT_MODEL
        self.effort = (effort or os.environ.get("READING_MODEL_EFFORT") or DEFAULT_EFFORT).lower()
        # timeout 参数沿用旧语义：它就是等响应的上限。
        self.timeout = float(
            timeout
            or os.environ.get("READING_MODEL_READ_TIMEOUT")
            or os.environ.get("READING_MODEL_TIMEOUT")
            or READ_TIMEOUT
        )
        self.read_timeout = self.timeout
        self.connect_timeout = float(os.environ.get("READING_MODEL_CONNECT_TIMEOUT") or CONNECT_TIMEOUT)
        self.deadline = float(os.environ.get("READING_MODEL_DEADLINE") or DEADLINE)
        self.attempts = max(1, int(os.environ.get("READING_MODEL_ATTEMPTS") or MAX_ATTEMPTS))
        self.backoff = _parse_backoff(backoff if backoff is not None else os.environ.get("READING_MODEL_BACKOFF"))
        mode = (os.environ.get("READING_MODEL_STREAM") or STREAM_MODE).strip().lower()
        self.stream_mode = mode if mode in STREAM_MODES else "auto"
        self.stream_read_timeout = float(os.environ.get("READING_MODEL_STREAM_TIMEOUT") or STREAM_READ_TIMEOUT)
        self.stream_deadline = float(os.environ.get("READING_MODEL_STREAM_DEADLINE") or STREAM_DEADLINE)
        # 传输类失败时换谁再来一次；置 None 关掉（测试里用来固定调用次数）。
        self.fallback_model = FALLBACK_MODEL if os.environ.get("READING_MODEL_FALLBACK", "1") != "0" else None
        self.max_tokens = int(max_tokens or os.environ.get("READING_MODEL_MAX_TOKENS") or MAX_TOKENS)
        self.key = key if key is not None else _api_key()
        self.session = uuid.uuid4().hex
        self.last_model = self.model
        self.last_effort = self.effort
        self.last_usage = {}
        self.last_fallback = False
        self.total_usage = {}

    def available(self):
        return bool(self.key)

    def annotate(self, book, chapter, blocks, model=None, effort=None):
        """兼容入口：返回 annotation，元信息仍写回 ``last_*``。"""
        annotation, _meta = self.annotate_with_meta(book, chapter, blocks, model=model, effort=effort)
        return annotation

    def annotate_with_meta(self, book, chapter, blocks, model=None, effort=None):
        """调用一次并返回 ``(annotation, meta)``；并发任务各用各的元信息。

        ``meta`` 含 requested_model / model（实际使用）/ effort / usage / fallback。
        ``last_*`` 仅为旧调用方保留，编排层不得依赖它。
        """
        if not self.available():
            raise RuntimeError("模型未配置：缺少 opencode-go key")
        requested_model = (model or self.model).strip()
        effort = (effort or self.effort).strip().lower()
        if effort not in EFFORTS:
            raise ValueError(f"reasoning_effort 只能是 {'/'.join(EFFORTS)}")
        if not requested_model:
            raise ValueError("模型名不能为空")
        payload_blocks = _payload_blocks(blocks)
        user = json.dumps(
            {
                "book": book.get("title", ""),
                "chapter": chapter.get("title", ""),
                "tiers": list(TIER_ORDER),
                "blocks": payload_blocks,
            },
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        actual_model = requested_model
        fallback = False
        try:
            annotation, usage = self._annotate_once(messages, blocks, book, chapter, requested_model, effort)
        except ModelProviderError as exc:
            # 门槛与 complete_json 一致：鉴权/截断/格式换谁都一样，只有读路和上游这类值得换。
            if requested_model == FALLBACK_MODEL or not _fallback_worthy(exc):
                raise
            fallback = True
            actual_model = FALLBACK_MODEL
            try:
                annotation, usage = self._annotate_once(
                    messages,
                    blocks,
                    book,
                    chapter,
                    FALLBACK_MODEL,
                    effort,
                    attempts=1,
                )
            except Exception as fallback_exc:
                raise RuntimeError(f"主模型失败：{exc}；备用模型也失败：{fallback_exc}") from fallback_exc
        meta = {
            "requested_model": requested_model,
            "model": actual_model,
            "effort": effort,
            "usage": usage,
            "fallback": fallback,
        }
        self.last_model = actual_model
        self.last_effort = effort
        self.last_usage = usage
        self.last_fallback = fallback
        return annotation, meta

    def complete_json(self, messages, *, model=None, effort=None):
        """单次 JSON 补全，供图表编辑等第二遍调用；不做业务校验。"""
        if not self.available():
            raise RuntimeError("模型未配置：缺少 opencode-go key")
        requested_model = (model or self.model).strip()
        effort = (effort or self.effort).strip().lower()
        if effort not in EFFORTS:
            raise ValueError(f"reasoning_effort 只能是 {'/'.join(EFFORTS)}")
        try:
            content, usage = self._request(requested_model, effort, messages)
        except ModelProviderError as exc:
            # 只有网络/上游这两类值得换模型重来；鉴权、截断、格式问题换谁都一样。
            target = self.fallback_model
            if not target or requested_model == target or not _fallback_worthy(exc):
                raise
            # 备用模型只给一次机会：主模型的四次重试已经把等待预算花掉了。
            events.current().emit(
                "model_fallback",
                requested=requested_model,
                fallback=target,
                error_kind=exc.kind,
                attempts=1,
            )
            content, usage = self._request(target, effort, messages, attempts=1)
            self.last_fallback = True
            actual_model = target
        else:
            self.last_fallback = False
            actual_model = requested_model
        # 各层用 last_model 记「这层内容是谁生成的」；备用模型跑出来的不能记成主模型。
        self.last_model = actual_model
        self.last_effort = effort
        self.last_usage = usage
        return _loads_content(content), usage

    def _annotate_once(self, messages, blocks, book, chapter, model, effort, attempts=None):
        last_error = None
        usage_total = {}
        for _attempt in range(2):
            content, usage = self._request(model, effort, messages, attempts=attempts)
            usage_total = merge_usage(usage_total, usage)
            try:
                annotation = _loads_content(content)
                annotation.setdefault("uid", book.get("uid"))
                annotation.setdefault("n", chapter.get("n"))
                # 档位必须由确定性投影器决定；模型偶尔仍会输出 tier_blocks。
                annotation.pop("tier_blocks", None)
                return validate_annotation(annotation, blocks), usage_total
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": f"上面的 JSON 未通过校验：{exc}。只重发修正后的完整 JSON，不要解释。",
                    }
                )
        raise ValueError(f"模型标注解析失败：{last_error}")

    def complete(self, messages, *, model=None, effort=None, json_mode=False):
        """单次文本补全；``json_mode`` 仅用于需要 JSON 对象的调用。"""
        if not self.available():
            raise RuntimeError("模型未配置：缺少 opencode-go key")
        requested_model = (model or self.model).strip()
        effort = (effort or self.effort).strip().lower()
        if effort not in EFFORTS:
            raise ValueError(f"reasoning_effort 只能是 {'/'.join(EFFORTS)}")
        return self._request(requested_model, effort, messages, json_mode=json_mode)

    def _stream_enabled(self, parsed=None):
        """auto/on 且该端点没被记过「不认流式」；off 永不流式。"""
        if self.stream_mode == "off":
            return False
        if parsed is not None:
            key = f"{parsed[0]}://{parsed[1]}:{parsed[2]}"
            if key in _STREAM_UNSUPPORTED:
                return False
        return True

    def _read_sse(self, response, meta, deadline_at=None):
        """逐块读 SSE。读超时管“两次读之间”，总时长由 deadline_at 管。

        没见到 ``[DONE]`` 也没见到 ``finish_reason`` 就断流，判定为截断：
        半截 JSON 如果当成功返回，上游会把它记成“模型返回非法 JSON”，
        真正的故障就被藏起来了。
        """
        pieces = []
        usage = {}
        finish = None
        saw_done = False
        attempt = int(meta.get("attempt") or 0)
        peer = str(meta.get("peer") or "")
        while True:
            if deadline_at is not None and time.perf_counter() > deadline_at:
                meta["stream"] = True
                raise ModelTransportError(
                    "模型流式输出超过总截止时间，已中止",
                    kind="transport_timeout",
                    attempt=attempt,
                    peer=peer,
                )
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text or text.startswith(":"):
                continue
            if not text.startswith("data:"):
                continue
            data = text[5:].strip()
            if data == "[DONE]":
                saw_done = True
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk.get("error"), dict):
                # 上游在流中途报错：这条流没有交付完整回答，按截断处理，可重试、可换模型。
                meta["stream"] = True
                detail = json.dumps(chunk["error"], ensure_ascii=False)[:200]
                raise ModelTransportError(
                    f"模型流式返回错误：{detail}",
                    kind="transport_truncated",
                    attempt=attempt,
                    peer=peer,
                )
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    if not pieces:
                        meta["first_delta_ms"] = _elapsed_ms(meta.get("_started", time.perf_counter()))
                    pieces.append(delta["content"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        meta["stream"] = True
        if not saw_done and finish is None:
            raise ModelTransportError(
                "模型流在 [DONE] 之前断开，回答不完整",
                kind="transport_truncated",
                attempt=attempt,
                peer=peer,
            )
        meta["saw_done"] = saw_done
        meta["chunks"] = len(pieces)
        text = "".join(pieces)
        # 上层只认非流式的响应体形状，这里合成一份，解析路径不分叉。
        payload = {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": usage}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _retry_wait(self, attempt):
        schedule = self.backoff or BACKOFF
        base = schedule[min(attempt - 1, len(schedule) - 1)]
        if base <= 0:
            return 0.0
        return base * random.uniform(0.8, 1.2)

    def _post_once(self, body, headers, parsed, meta, budget, stream=False, attempts=None):
        """一次 HTTP 尝试。meta 在失败前先把已测得的事实填好。"""
        scheme, host, port, path = parsed
        started = time.perf_counter()
        meta["stream"] = stream
        meta["phase"] = "connect"
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                host,
                port,
                timeout=self.connect_timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.connect_timeout)
        try:
            conn.connect()
            meta["connect_ms"] = _elapsed_ms(started)
            # getresponse() 会把 conn.sock 置空（Connection: close），先把套接字留住，
            # 否则后面想改读超时只能改到一个 None 上，超时会一直沿用连上时那个值。
            sock = conn.sock
            meta["peer"], meta["family"] = _peer_of(sock)
            if sock is not None:
                sock.settimeout(max(0.001, min(self.read_timeout, budget)))
            meta["phase"] = "send"
            conn.request("POST", path, body=body, headers=headers)
            meta["phase"] = "read"
            response = conn.getresponse()
            meta["first_byte_ms"] = _elapsed_ms(started)
            status = response.status
            meta["status"] = status
            retry_after = response.getheader("Retry-After")
            if retry_after:
                meta["retry_after"] = retry_after
            content_type = (response.getheader("Content-Type") or "").lower()
            try:
                if stream and status == 200 and "text/event-stream" in content_type:
                    if sock is not None:
                        sock.settimeout(max(0.001, min(self.stream_read_timeout, budget)))
                    meta["_started"] = started
                    raw = self._read_sse(response, meta, time.perf_counter() + budget)
                    meta.pop("_started", None)
                else:
                    if stream and status == 200:
                        # 端点不认流式，把这份普通响应直接用掉，并记住别再试。
                        key = f"{parsed[0]}://{parsed[1]}:{parsed[2]}"
                        _STREAM_UNSUPPORTED.add(key)
                        meta["stream_unsupported"] = True
                        events.current().emit("stream_unsupported", host=parsed[1], port=parsed[2])
                    raw = response.read()
            finally:
                response.close()
            meta["phase"] = "done"
            return status, raw
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _request(self, model, effort, messages, *, json_mode=True, attempts=None):
        payload = {
            "model": model,
            "messages": messages,
            "reasoning_effort": effort,
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }
        parsed = _split_endpoint(self.endpoint)
        stream = self._stream_enabled(parsed)
        endpoint_key = f"{parsed[0]}://{parsed[1]}:{parsed[2]}"
        if json_mode and endpoint_key not in _NO_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.key,
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
            "Origin": "https://opencode.ai",
            "Referer": "https://opencode.ai/",
            "x-opencode-session": self.session,
            "Connection": "close",
        }
        log = events.current()
        log.emit(
            "http_start",
            model=model,
            effort=effort,
            host=parsed[1],
            port=parsed[2],
            max_tokens=self.max_tokens,
            bytes_out=len(body),
            stream=stream,
        )
        started = time.perf_counter()

        def rebuild_without_json_mode():
            payload.pop("response_format", None)
            return json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers

        rebuild = rebuild_without_json_mode if json_mode and "response_format" in payload else None
        return self._call_with_retry(
            body,
            headers,
            parsed,
            started,
            log,
            stream=stream,
            rebuild=rebuild,
            attempts=attempts,
        )

    def _call_with_retry(self, body, headers, parsed, started, log, stream=False, rebuild=None, attempts=None):
        """重试策略只在这里，别处不许再写第二套。"""
        try:
            return self._attempt_loop(
                body,
                headers,
                parsed,
                started,
                log,
                stream=stream,
                rebuild=rebuild,
                attempts=attempts,
            )
        except ModelProviderError as exc:
            # protocol 类错误（响应体缺字段、不是 JSON）再问一次可能好，留给上层那一次；
            # 其余分类已经在这里定案，上层不许再叠。
            if exc.kind != "protocol":
                exc.retries_exhausted = True
            raise

    def _attempt_loop(self, body, headers, parsed, started, log, stream=False, rebuild=None, attempts=None):
        attempt = 0
        limit = self.attempts if attempts is None else max(1, int(attempts))
        # 流式有内容不断到达，不需要 1800 秒那么宽的兜底；1200 秒够一章，也不会让挂住的流占半小时。
        budget_total = self.stream_deadline if stream else self.deadline
        while attempt < limit:
            remaining = budget_total - (time.perf_counter() - started)
            if remaining <= 0:
                break
            attempt += 1
            meta = {"attempt": attempt, "phase": "connect"}
            try:
                status, raw = self._post_once(body, headers, parsed, meta, remaining, stream=stream)
            except Exception as exc:
                kind = _classify(exc, meta.get("phase"))
                elapsed = _elapsed_ms(started)
                log.emit(
                    "http_error",
                    attempt=attempt,
                    phase=meta.get("phase"),
                    error_class=exc.__class__.__name__,
                    error_kind=kind,
                    detail=str(exc)[:300],
                    elapsed_ms=elapsed,
                    peer=meta.get("peer"),
                    family=meta.get("family"),
                    connect_ms=meta.get("connect_ms"),
                    first_byte_ms=meta.get("first_byte_ms"),
                    first_delta_ms=meta.get("first_delta_ms"),
                    chunks=meta.get("chunks"),
                    stream=bool(meta.get("stream")),
                )
                elapsed = _elapsed_ms(started)
                if kind not in RETRYABLE_KINDS or attempt >= limit:
                    raise _transport_error(exc, kind, attempt, elapsed, meta) from exc
                left = budget_total - (time.perf_counter() - started)
                if left <= 0:
                    raise _transport_error(exc, "transport_timeout", attempt, elapsed, meta) from exc
                wait = min(self._retry_wait(attempt), left)
                log.emit("http_retry", attempt=attempt, error_kind=kind, wait_ms=int(wait * 1000))
                time.sleep(wait)
                continue

            elapsed = _elapsed_ms(started)
            log.emit(
                "http_response",
                attempt=attempt,
                status=status,
                peer=meta.get("peer"),
                family=meta.get("family"),
                connect_ms=meta.get("connect_ms"),
                first_byte_ms=meta.get("first_byte_ms"),
                first_delta_ms=meta.get("first_delta_ms"),
                chunks=meta.get("chunks"),
                stream=bool(meta.get("stream") or meta.get("stream_unsupported")),
                total_ms=elapsed,
                bytes_in=len(raw),
            )
            if status != 200:
                kind = _status_kind(status)
                detail = raw.decode("utf-8", "replace")[:400]
                if rebuild is not None and status == 400 and "response_format" in raw.decode("utf-8", "replace"):
                    # 该网关不认 response_format（流式下必现）：记下来，改发不带它的同一条请求，不占重试名额。
                    _NO_JSON_MODE.add(f"{parsed[0]}://{parsed[1]}:{parsed[2]}")
                    body, headers = rebuild()
                    rebuild = None
                    attempt -= 1
                    log.emit("no_json_mode", host=parsed[1], status=status, detail=detail[:200])
                    continue
                if kind in RETRYABLE_KINDS and attempt < limit:
                    left = budget_total - (time.perf_counter() - started)
                    if left > 0:
                        wait = min(max(self._retry_wait(attempt), _parse_retry_after(meta.get("retry_after"))), left)
                        log.emit(
                            "http_retry",
                            attempt=attempt,
                            error_kind=kind,
                            status=status,
                            wait_ms=int(wait * 1000),
                        )
                        time.sleep(wait)
                        continue
                log.emit("call_failed", attempts=attempt, elapsed_ms=elapsed, error_kind=kind, status=status)
                if kind == "auth":
                    raise ModelAuthError(
                        f"模型鉴权失败 HTTP {status}：{detail}",
                        kind=kind,
                        attempt=attempt,
                        elapsed_ms=elapsed,
                        status=status,
                    )
                raise ModelProviderError(
                    f"模型 HTTP {status}：{detail}",
                    kind=kind,
                    retryable=kind in RETRYABLE_KINDS,
                    attempt=attempt,
                    elapsed_ms=elapsed,
                    status=status,
                )

            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError as exc:
                log.emit("call_failed", attempts=attempt, elapsed_ms=elapsed, error_kind="protocol")
                raise ModelProtocolError(
                    f"模型返回不是 JSON：{exc}",
                    kind="protocol",
                    attempt=attempt,
                    elapsed_ms=elapsed,
                ) from exc
            try:
                choice = data["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                log.emit("call_failed", attempts=attempt, elapsed_ms=elapsed, error_kind="protocol")
                raise ModelProtocolError(
                    f"模型返回缺 choices[0].message.content：{exc}",
                    kind="protocol",
                    attempt=attempt,
                    elapsed_ms=elapsed,
                ) from exc
            if choice.get("finish_reason") == "length":
                log.emit("call_failed", attempts=attempt, elapsed_ms=elapsed, error_kind="truncated")
                raise ModelTruncatedError(
                    "模型输出被 max_tokens 截断，请重试或降低档位数量",
                    attempt=attempt,
                    elapsed_ms=elapsed,
                )
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            self.total_usage = merge_usage(self.total_usage, usage)
            log.emit(
                "call_ok",
                attempts=attempt,
                elapsed_ms=elapsed,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            return content, usage

        elapsed = _elapsed_ms(started)
        log.emit("call_failed", attempts=attempt, elapsed_ms=elapsed, error_kind="transport_timeout")
        raise ModelTransportError(
            f"模型调用总的截止时间已用完：{elapsed} 毫秒（上限 {int(budget_total)} 秒）",
            kind="transport_timeout",
            attempt=attempt,
            elapsed_ms=elapsed,
        )


def _fallback_worthy(exc):
    """换模型能不能救：传输类与 5xx 能，鉴权/截断/协议类不能。"""
    kind = getattr(exc, "kind", "") or ""
    return kind.startswith("transport_") or kind == "http_5xx"


def merge_usage(total, usage):
    merged = dict(total or {})
    for key, value in (usage or {}).items():
        if isinstance(value, dict):
            current = merged.get(key)
            merged[key] = merge_usage(current if isinstance(current, dict) else {}, value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[key] = merged.get(key, 0) + value
        elif key not in merged:
            merged[key] = value
    return merged


_merge_usage = merge_usage  # 兼容旧调用名


def _loads_content(content):
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)
