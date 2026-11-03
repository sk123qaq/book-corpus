"""项目用哪个 pi 模型：读写 books.toml 的 [model]，环境变量可覆盖，真跑一次算数。

字段（都在 books.toml 的 [model] 表里，空 = 不传，交给 pi 自己的默认）：

    provider      pi 里的 provider 名，如 opencode-go
    model         模型 id，可带 provider 前缀；和 provider 分开写时自动拼
    thinking      思考等级：off/minimal/low/medium/high/xhigh/max
    base_url      自建端点；非空要写进 ~/.pi/agent/models.json 才生效
    api_key_env   放着 API key 的环境变量名；pi 用 --api-key 收下
    api           自建端点的协议，默认 openai-completions

环境变量 BOOKS_MODEL / BOOKS_MODEL_PROVIDER / BOOKS_THINKING / BOOKS_BASE_URL /
BOOKS_API_KEY_ENV / BOOKS_MODEL_API 优先于文件。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib

LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
FALLBACK = "opencode-go/deepseek-v4.1-flash"
DEFAULTS = {"thinking": "high"}
FIELDS = ("provider", "model", "thinking", "base_url", "api_key_env", "api")
ENV_KEYS = {
    "provider": ("BOOKS_MODEL_PROVIDER",),
    "model": ("BOOKS_MODEL",),
    "thinking": ("BOOKS_THINKING",),
    "base_url": ("BOOKS_BASE_URL",),
    "api_key_env": ("BOOKS_API_KEY_ENV",),
    "api": ("BOOKS_MODEL_API",),
}
PI_FLAGS = ["-p", "--no-session", "--no-context-files", "--no-skills", "--no-extensions"]
MANIFEST = "books.toml"
PI_CONFIG = os.path.join("~", ".pi", "agent", "models.json")


def _manifest(root):
    return os.path.join(root, MANIFEST)


def _table(root):
    path = _manifest(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    table = doc.get("model")
    return table if isinstance(table, dict) else {}


def load(root):
    """返回 {"values": {...}, "sources": {...}, "ref": "provider/model"}。"""
    table = _table(root)
    values, sources = {}, {}
    for field in FIELDS:
        value, source = str(table.get(field) or "").strip(), "books.toml"
        if not value:
            source = ""
        for name in ENV_KEYS[field]:
            env = os.environ.get(name, "").strip()
            if env:
                value, source = env, "环境变量 " + name
                break
        if not value and field in DEFAULTS:
            value, source = DEFAULTS[field], "默认"
        values[field] = value
        sources[field] = source or "未设置"
    if not values["model"] and not values["base_url"]:
        provider, model = FALLBACK.split("/", 1)
        values["provider"], values["model"] = provider, model
        sources["provider"] = sources["model"] = "内置默认"
    return {"root": root, "values": values, "sources": sources, "ref": ref(values)}


def ref(values):
    model, provider = values.get("model", ""), values.get("provider", "")
    if not model:
        return ""
    if "/" in model or not provider:
        return model
    return provider + "/" + model


def thinking(values, override=None):
    """给一个白的等级就用它；给了非法值就退回项目配置；配置也没有就返回空。"""
    for candidate in (override, values.get("thinking")):
        level = str(candidate or "").strip().lower()
        if level in LEVELS:
            return level
    return ""


def pi_head():
    raw = os.environ.get("PI_BIN") or shutil.which("pi") or "pi"
    return [sys.executable, raw] if raw.endswith(".py") else [raw]


def model_flags(values, level=None):
    """--thinking / --model / --api-key 三样，按 pi 收参数的顺序排好。"""
    out = []
    level = thinking(values, level)
    if level:
        out += ["--thinking", level]
    if ref(values):
        out += ["--model", ref(values)]
    name = values.get("api_key_env", "")
    key = os.environ.get(name, "") if name else ""
    if key:
        out += ["--api-key", key]
    return out


def argv(values, prompt, extra=(), level=None):
    return pi_head() + PI_FLAGS + list(extra) + model_flags(values, level) + [prompt]


def run(values, prompt, timeout=90, extra=(), level=None):
    cmd = argv(values, prompt, extra, level)
    began = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "ms": _ms(began), "error": "pi 超时（%d 秒）" % timeout, "argv": cmd}
    except OSError as exc:
        return {"ok": False, "ms": _ms(began), "error": str(exc), "argv": cmd}
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"ok": False, "ms": _ms(began), "argv": cmd,
                "error": error[-1][:300] if error else "pi 退出 %d" % proc.returncode}
    return {"ok": True, "ms": _ms(began), "text": text, "argv": cmd}


def _ms(since):
    return int((time.time() - since) * 1000)


def save(root, changes):
    """只改 [model]，其余顶层键原样留着。"""
    path = _manifest(root)
    doc = {}
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    table = dict(doc.get("model") or {})
    for field, value in changes.items():
        if field not in FIELDS:
            raise SystemExit("[model] 不认这个字段：%s" % field)
        value = "" if value is None else str(value).strip()
        if value:
            table[field] = value
        else:
            table.pop(field, None)
    doc["model"] = table
    lines = []
    for key, value in doc.items():
        if key == "model" or isinstance(value, dict):
            continue
        lines.append("%s = %s" % (key, _toml(value)))
    if not lines:
        lines = ['schema = 2']
    lines.append("")
    lines.append("[model]")
    for field in FIELDS:
        if table.get(field):
            lines.append("%s = %s" % (field, _toml(table[field])))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return table


def _toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def models_json_path():
    return os.path.expanduser(PI_CONFIG)


def setup_pi(values):
    """把自建端点写进 pi 的 models.json；返回 (改没改, 说明)。"""
    base_url = values.get("base_url", "")
    if not base_url:
        return False, "没配 base_url，不用改 models.json：provider 由 pi 自己管。"
    provider = values.get("provider") or "book-corpus"
    entry = {"baseUrl": base_url, "api": values.get("api") or "openai-completions"}
    if values.get("api_key_env"):
        entry["apiKey"] = "$" + values["api_key_env"]
    model = values.get("model", "")
    model = model.split("/", 1)[1] if "/" in model else model
    if model:
        entry["models"] = [{"id": model}]
    path = models_json_path()
    doc = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return False, "%s 读不动，没敢改。先自己备份。" % path
        backup = "%s.bak.%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copyfile(path, backup)
    doc.setdefault("$schema", "https://pi.dev/schemas/models.schema.json")
    doc.setdefault("providers", {})[provider] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return True, "写进 %s：providers.%s = %s" % (path, provider, json.dumps(entry, ensure_ascii=False))


def known_models(limit=4000):
    """pi 认得的模型，一行一个；pi 不在或超时就返回空表。"""
    try:
        proc = subprocess.run(pi_head() + ["--list-models"], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()][:limit]
