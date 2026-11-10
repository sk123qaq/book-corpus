"""执行前的配置预检。

缺 key 时不要等到任务跑到一半才炸：按场景先查一遍，缺什么、在哪配、配完
怎么让它生效，都写进一段中文提示。``require`` 抛 :class:`MissingConfigError`；
HTTP 层把 ``code`` 变成 400，CLI 直接打印。

只做本地判断（环境变量、``.env``、pi 的 auth.json / web-search.json、
工作区清单），不发网络请求。能力清单见 :data:`SPECS`。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import envfile

OK = "ok"
MISSING = "missing"
WARN = "warn"

#: 这些能力缺失时可以确认后降级（研究层能退成未检索议程）。
DEGRADABLE = frozenset({"reading.search"})

PI_AUTH = Path("~/.pi/agent/auth.json")
MIMO_KEY = Path("~/.config/mimo/api_key")
PADDLE_KEY = Path("~/.config/PaddleOCR/api_key")
MINERU_KEY = Path("~/.config/mineru/api_key")

SEARCH_KEYS = ("TAVILY_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY", "FIRECRAWL_API_KEY")


@dataclass(frozen=True)
class Spec:
    label: str
    scenario: str
    needs: str
    fix: str


@dataclass(frozen=True)
class Status:
    key: str
    label: str
    scenario: str
    state: str
    detail: str
    needs: str
    fix: str

    @property
    def missing(self) -> bool:
        return self.state == MISSING

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "scenario": self.scenario,
            "state": self.state,
            "detail": self.detail,
            "needs": self.needs,
            "fix": self.fix,
        }


def _env_file() -> str:
    return str(envfile.project_env_path())


SPECS: dict[str, Spec] = {
    "books.model": Spec(
        label="书库流水线的模型",
        scenario="跑 ingest、分类、拆章修复、问答归纳这些要调模型的脚本时",
        needs="工作区 books.toml 的 [model]（provider + model），以及可执行的 pi",
        fix=(
            "先确认 pi 在 PATH 里（或设 PI_BIN），并在 pi 里 /login 配好对应 provider；"
            "再用 book-corpus model set --provider … --model … 写进 books.toml"
        ),
    ),
    "reading.model": Spec(
        label="四读的标注/结构模型",
        scenario="给章节做标注、生成或重建深读结构层时",
        needs="OPENCODE_GO_API_KEY 环境变量，或 pi 已登录的 opencode-go（~/.pi/agent/auth.json）",
        fix=(
            "在 %s 里写 OPENCODE_GO_API_KEY=...，存盘后重启 8771；或者直接在 pi 里 /login 选 opencode-go" % _env_file()
        ),
    ),
    "reading.search": Spec(
        label="四读·检索 provider",
        scenario="生成研究层（research）或引用层（citations）时",
        needs="READING_SEARCH_PROVIDER 加上 TAVILY_/BRAVE_/SERPER_/FIRECRAWL_API_KEY 之一；"
        "或 READING_SEARCH_PROVIDER=http 加 READING_SEARCH_ENDPOINT",
        fix=(
            "在 %s 里写：\n"
            "        READING_SEARCH_PROVIDER=auto\n"
            "        FIRECRAWL_API_KEY=fc-...（或 TAVILY_API_KEY=...）\n"
            "        存盘后重启 8771 生效；不想配就带 allow_unverified 跑未检索议程" % _env_file()
        ),
    ),
    "reading.fetch": Spec(
        label="四读·抓页",
        scenario="生成引用层、需要抓取网页原文时",
        needs="READING_FETCH_PROVIDER（默认 http，不需要 key）；firecrawl 抓页需要 FIRECRAWL_API_KEY",
        fix="在 %s 里设 READING_FETCH_PROVIDER=http（默认）或 firecrawl 并配 FIRECRAWL_API_KEY" % _env_file(),
    ),
    "ocr.mineru": Spec(
        label="MinerU OCR",
        scenario="PDF 读不出文字、需要 OCR 兜底时",
        needs="MINERU_API_KEY 环境变量，或 ~/.config/mineru/api_key",
        fix="把 token 写进 %s，或写成 ~/.config/mineru/api_key（一行）" % _env_file(),
    ),
    "ocr.paddleocr": Spec(
        label="PaddleOCR 云端 OCR",
        scenario="扫描件逐页 OCR、且要用 PaddleOCR 而不是 MinerU 时",
        needs="~/.config/PaddleOCR/api_key（一行 token）",
        fix="把 token 写到 ~/.config/PaddleOCR/api_key；这个 CLI 只读该文件",
    ),
    "expand.mimo": Spec(
        label="MiMo 扩展生成",
        scenario="build_expand.py --provider mimo 时",
        needs="~/.config/mimo/api_key（一行 key）",
        fix="把 key 写到 ~/.config/mimo/api_key；这个脚本只读该文件",
    ),
    "expand.pi": Spec(
        label="扩展生成的 pi 账号",
        scenario="build_expand.py 用 commandcode 或 opencode 时",
        needs="~/.pi/agent/auth.json 里有 commandcode 或 opencode-go 的凭据",
        fix="在 pi 里 /login 对应的 provider",
    ),
}


def _auth_entry(name: str) -> bool:
    try:
        data = json.loads(PI_AUTH.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    entry = data.get(name)
    if isinstance(entry, dict):
        return bool(str(entry.get("key") or "").strip())
    if isinstance(entry, str):
        return bool(entry.strip())
    return bool(str(data.get(name + ".key") or "").strip())


def _key_file(*paths: Path) -> Path | None:
    for item in paths:
        target = item.expanduser()
        try:
            if target.is_file() and target.stat().st_size > 0:
                return target
        except OSError:
            continue
    return None


def _status(key: str, state: str, detail: str) -> Status:
    spec = SPECS[key]
    return Status(
        key=key,
        label=spec.label,
        scenario=spec.scenario,
        state=state,
        detail=detail,
        needs=spec.needs,
        fix=spec.fix,
    )


def _check_books_model(root: str | None) -> Status:
    from . import pimodel

    if not root:
        return _status("books.model", MISSING, "没有工作区（找不到 books.toml）")
    head = pimodel.pi_head()
    if len(head) == 1 and shutil.which(head[0]) is None:
        return _status("books.model", MISSING, "PATH 里找不到 pi，也没有设 PI_BIN")
    info = pimodel.load(root)
    values = info["values"]
    key_env = values.get("api_key_env") or ""
    if key_env and not (os.environ.get(key_env) or "").strip():
        return _status("books.model", MISSING, "books.toml 指定了 api_key_env=%s，但环境里没有这个变量" % key_env)
    return _status("books.model", OK, "%s（pi 管 key）" % (info.get("ref") or "内置默认"))


def _check_reading_model() -> Status:
    if (os.environ.get("OPENCODE_GO_API_KEY") or "").strip():
        return _status("reading.model", OK, "OPENCODE_GO_API_KEY 已设置")
    if _auth_entry("opencode-go"):
        return _status("reading.model", OK, "用 pi 里已登录的 opencode-go")
    return _status("reading.model", MISSING, "没有 OPENCODE_GO_API_KEY，pi 的 auth.json 里也没有 opencode-go")


def _check_reading_search() -> Status:
    from .reading.depth import citations

    info = citations.search_config_status()
    if not info["ok"]:
        return _status("reading.search", MISSING, info["detail"])
    return _status("reading.search", OK, "%s（%s）" % (info["detail"], info["origin"]))


def _check_reading_fetch() -> Status:
    from .reading.depth import citations

    info = citations.fetch_config_status()
    state = OK if info["ok"] else MISSING
    return _status("reading.fetch", state, info["detail"])


def _check_ocr_mineru() -> Status:
    if (os.environ.get("MINERU_API_KEY") or os.environ.get("MINERU_TOKEN") or "").strip():
        return _status("ocr.mineru", OK, "MINERU_API_KEY 已设置")
    found = _key_file(MINERU_KEY)
    if found:
        return _status("ocr.mineru", OK, str(found))
    return _status("ocr.mineru", MISSING, "没有 MINERU_API_KEY，也没有 ~/.config/mineru/api_key")


def _check_ocr_paddle() -> Status:
    found = _key_file(PADDLE_KEY)
    if found:
        return _status("ocr.paddleocr", OK, str(found))
    return _status("ocr.paddleocr", MISSING, "没有 ~/.config/PaddleOCR/api_key")


def _check_expand_mimo() -> Status:
    found = _key_file(MIMO_KEY)
    if found:
        return _status("expand.mimo", OK, str(found))
    return _status("expand.mimo", MISSING, "没有 ~/.config/mimo/api_key")


def _check_expand_pi() -> Status:
    names = [name for name in ("commandcode", "opencode-go") if _auth_entry(name)]
    if names:
        return _status("expand.pi", OK, "pi 已登录 " + "、".join(names))
    return _status("expand.pi", MISSING, "pi 的 auth.json 里没有 commandcode 或 opencode-go")


_CHECKS = {
    "books.model": _check_books_model,
    "reading.model": _check_reading_model,
    "reading.search": _check_reading_search,
    "reading.fetch": _check_reading_fetch,
    "ocr.mineru": _check_ocr_mineru,
    "ocr.paddleocr": _check_ocr_paddle,
    "expand.mimo": _check_expand_mimo,
    "expand.pi": _check_expand_pi,
}


def check(key: str, root: str | None = None) -> Status:
    """查一个能力；``root`` 只在查 ``books.model`` 时需要。"""

    fn = _CHECKS.get(key)
    if fn is None:
        raise KeyError("未知的预检项：%s" % key)
    return fn(root) if key == "books.model" else fn()


def check_all(root: str | None = None) -> list[Status]:
    return [check(key, root) for key in SPECS]


def missing(keys: Iterable[str], root: str | None = None) -> list[Status]:
    return [status for status in (check(key, root) for key in keys) if status.missing]


def summary(root: str | None = None) -> str:
    """一行摘要，供服务启动日志用；全齐时返回空串。"""

    bad = [status for status in check_all(root) if status.missing]
    if not bad:
        return ""
    return "缺少配置：" + "；".join("%s（%s）" % (status.label, status.detail) for status in bad)


class MissingConfigError(RuntimeError):
    """缺 key/环境；``code`` 供 HTTP 层转 400。"""

    code = "missing_config"

    def __init__(self, statuses: Iterable[Status], note: str = ""):
        self.statuses = list(statuses)
        self.note = note
        super().__init__(self.render())

    @property
    def degradable(self) -> bool:
        return bool(self.statuses) and all(status.key in DEGRADABLE for status in self.statuses)

    def render(self) -> str:
        blocks = []
        for status in self.statuses:
            lines = [
                "缺少配置：%s" % status.label,
                "  场景：%s" % status.scenario,
                "  需要：%s" % status.needs,
                "  当前：%s" % status.detail,
                "  配置：%s" % status.fix,
            ]
            if self.note:
                lines.append("  说明：%s" % self.note)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def short(self) -> str:
        return "；".join("%s（%s）" % (status.label, status.detail) for status in self.statuses)

    def as_payload(self) -> dict[str, Any]:
        return {
            "error": "缺少配置：" + self.short(),
            "code": self.code,
            "missing": [status.as_dict() for status in self.statuses],
            "degradable": self.degradable,
        }


def require(
    keys: Iterable[str],
    root: str | None = None,
    *,
    allow_unverified: bool = False,
    note: str = "",
) -> None:
    """按场景要求若干能力；可降级的（研究层）允许显式放行。"""

    bad = missing(keys, root)
    if not bad:
        return
    if allow_unverified and all(status.key in DEGRADABLE for status in bad):
        return
    raise MissingConfigError(bad, note=note)
