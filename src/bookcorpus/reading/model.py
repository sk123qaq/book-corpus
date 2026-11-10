"""可选模型标注：OpenAI-compatible chat/completions。

默认开启。测试可用 ``READING_ANNOTATE=0`` / ``--no-annotate`` 走离线草稿；
真实生成失败时直接报错，不再用四档同文草稿冒充成功。
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .tiers import TIER_ORDER, validate_annotation

ENDPOINT = "https://opencode.ai/inference/go/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4.1-flash"
FALLBACK_MODEL = "glm-5.3-flash"
DEFAULT_EFFORT = "high"
EFFORTS = ("max", "high", "medium", "low")
TIMEOUT = 240
MAX_TOKENS = 32768
PROMPT_VERSION = "phase1-v5"
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
    """网络/上游/空响应错误；可切换备用模型。"""


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
    ):
        self.endpoint = endpoint or os.environ.get("READING_MODEL_URL") or ENDPOINT
        self.model = model or os.environ.get("READING_MODEL_NAME") or DEFAULT_MODEL
        self.effort = (effort or os.environ.get("READING_MODEL_EFFORT") or DEFAULT_EFFORT).lower()
        self.timeout = int(timeout or os.environ.get("READING_MODEL_TIMEOUT") or TIMEOUT)
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
            if requested_model == FALLBACK_MODEL:
                raise
            fallback = True
            actual_model = FALLBACK_MODEL
            try:
                annotation, usage = self._annotate_once(messages, blocks, book, chapter, FALLBACK_MODEL, effort)
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
        content, usage = self._request(requested_model, effort, messages)
        return _loads_content(content), usage

    def _annotate_once(self, messages, blocks, book, chapter, model, effort):
        last_error = None
        usage_total = {}
        for _attempt in range(2):
            content, usage = self._request(model, effort, messages)
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

    def _request(self, model, effort, messages, *, json_mode=True):
        payload = {
            "model": model,
            "messages": messages,
            "reasoning_effort": effort,
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - endpoint is the fixed HTTPS API or operator override
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.key,
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
                ),
                "Origin": "https://opencode.ai",
                "Referer": "https://opencode.ai/",
                "x-opencode-session": self.session,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise ModelProviderError(f"模型 HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelProviderError(f"模型调用失败：{exc}") from exc
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError(f"模型返回缺 choices[0].message.content：{exc}") from exc
        if choice.get("finish_reason") == "length":
            raise ModelProviderError("模型输出被 max_tokens 截断，请重试或降低档位数量")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        self.total_usage = merge_usage(self.total_usage, usage)
        return content, usage


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
