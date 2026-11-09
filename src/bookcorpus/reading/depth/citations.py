"""检索、抓页与引用核验。

没有检索提供方时只产出 ``not_searched`` 账，不编造外部事实。有提供方时，
命中先去重，再抓取有限的页面，抓取失败与核验失败都降级成引用状态，不阻断
章节生成。所有证据只落在 ``citations.json``，研究议程仍由 ``research.json``
负责。
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup

from ..model import ModelProviderError
from ..textnorm import collapse_ws, normalize_ws

CITATIONS_SCHEMA_VERSION = 1
CITATIONS_PROMPT_VERSION = "research-citations-v1"
MAX_QUERIES = 5
MAX_HITS_PER_QUERY = 2
MAX_FETCHES = 10
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_CHARS = 200_000
MAX_VERIFY_CHARS = 1200
MAX_RESPONSE_BYTES = 1_000_000
FETCH_TIMEOUT = 10
MAX_REDIRECTS = 5
STALE_DAYS = 180
USER_AGENT = "book-corpus-research/1.0"

SEARCH_PROVIDER_ENV = "READING_SEARCH_PROVIDER"
SEARCH_ENDPOINT_ENV = "READING_SEARCH_ENDPOINT"
SEARCH_API_KEY_ENV = "READING_SEARCH_API_KEY"
FETCH_PROVIDER_ENV = "READING_FETCH_PROVIDER"
PI_WEB_SEARCH_CONFIG_ENV = "PI_WEB_SEARCH_CONFIG"
DOTENV_ENV = "BOOK_CORPUS_ENV"
SEARCH_TIMEOUT = 20

CLAIM_STATUSES = (
    "not_searched",
    "not_found",
    "fetch_failed",
    "unverified",
    "partial",
    "verified",
)


def _parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        if key:
            values[key] = value
    return values


def dotenv_path(path: str | Path | None = None) -> Path | None:
    """Find the first readable environment file.

    Precedence: explicit path, ``BOOK_CORPUS_ENV``, ``./.env``, then the
    user-level ``~/.config/book-corpus/.env``. An explicit path (argument or
    ``BOOK_CORPUS_ENV``) is authoritative: if it does not exist, no fallback
    is used. This keeps tests and isolated runs from reading a real user file.
    """

    if path:
        target = Path(path).expanduser()
        return target if target.is_file() else None
    explicit = (os.environ.get(DOTENV_ENV) or "").strip()
    if explicit:
        target = Path(explicit).expanduser()
        return target if target.is_file() else None
    for candidate in (Path.cwd() / ".env", Path.home() / ".config" / "book-corpus" / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: str | Path | None = None) -> Path | None:
    """Load key=value pairs without overriding the real environment."""

    target = dotenv_path(path)
    if target is None:
        return None
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for key, value in _parse_dotenv(text).items():
        os.environ.setdefault(key, value)
    return target


STATUS_LABELS = {
    "not_searched": "未检索",
    "not_found": "未找到来源",
    "fetch_failed": "抓取失败",
    "unverified": "未核验",
    "partial": "部分核验",
    "verified": "已核验",
}
STALE_WARNING = "引用已超过 180 天，建议复核"
QUOTE_MISMATCH = "引文未在页面中找到"

VERIFY_SYSTEM_PROMPT = (
    "你是引用核验员。只有页面中出现能直接支持判断的逐字引文时才判 supported。"
    "不要补写页面没有的内容。只输出 JSON 对象，格式为 "
    '{"results":[{"citation_id":"C1","supported":true,"quote":"逐字引文","reason":"理由"}]}；'
    "supported 必须是布尔值 true 或 false。"
)

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_QUERY_NEEDED_RE = re.compile(r"核验|查|统计|数据|研究")
_REQUIRED_NEEDED_RE = re.compile(r"统计|数据|研究|调查")
_NUMBER_RE = re.compile(r"\d")


class SearchError(ValueError):
    """检索提供方错误；可降级为未检索/未找到。"""


class FetchError(ValueError):
    """页面抓取或抓取安全校验错误。"""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str
    fetched_at: str


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]: ...


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...


class NullSearchProvider:
    name = "none"

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        del query, limit
        return []


class StaticSearchProvider:
    def __init__(self, name: str = "static", results: dict[str, list[SearchHit]] | None = None):
        self.name = name
        self.results = results or {}

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        hits = []
        for hit in self.results.get(query, [])[:limit]:
            if isinstance(hit, SearchHit):
                hits.append(hit)
            elif isinstance(hit, dict):
                hits.append(SearchHit(**hit))
        return hits


class HttpSearchProvider:
    name = "http"

    def __init__(self, endpoint: str, *, api_key: str | None = None, timeout: int = FETCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        body = json.dumps({"query": query, "limit": limit}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                status = int(getattr(response, "status", 200))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SearchError(f"检索请求失败：HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SearchError(f"检索请求失败：{exc}") from exc
        if not 200 <= status < 300:
            raise SearchError(f"检索请求失败：HTTP {status}")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SearchError("检索响应过大")
        if isinstance(raw, str):
            text = raw
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SearchError(f"检索响应不是合法 JSON：{exc}") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SearchError(f"检索响应不是合法 JSON：{exc}") from exc
        if isinstance(payload, dict):
            results = payload.get("results")
        elif isinstance(payload, list):
            results = payload
        else:
            results = None
        if not isinstance(results, list):
            raise SearchError("检索响应缺少 results 数组")
        hits = []
        for item in results:
            if not isinstance(item, dict):
                raise SearchError("检索结果项不是对象")
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                raise SearchError("检索结果缺少 url")
            hits.append(
                SearchHit(
                    url=url.strip(),
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("snippet") or ""),
                )
            )
        return hits[:limit]


def _pi_web_search_config() -> dict[str, Any]:
    """读取 pi-web-access 的 ``web-search.json``；不读则返回空对象。"""
    raw_path = (os.environ.get(PI_WEB_SEARCH_CONFIG_ENV) or "").strip()
    path = Path(raw_path).expanduser() if raw_path else Path.home() / ".pi/agent/web-search.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SearchError(f"无法读取 {path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise SearchError(f"{path} 必须是 JSON 对象")
    return payload


def _first_env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _first_config(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _api_key(kind: str, config: dict[str, Any], *, generic: bool = True) -> str:
    names = {
        "tavily": ("TAVILY_API_KEY",),
        "brave": ("BRAVE_API_KEY",),
        "serper": ("SERPER_API_KEY",),
        "firecrawl": ("FIRECRAWL_API_KEY",),
    }[kind]
    value = _first_env(*names)
    if not value:
        value = _first_env(SEARCH_API_KEY_ENV) if generic else ""
    if not value:
        key = {
            "tavily": "tavilyApiKey",
            "brave": "braveApiKey",
            "serper": "serperApiKey",
            "firecrawl": "firecrawlApiKey",
        }[kind]
        value = _first_config(config, key)
    return value


def _base_url(kind: str, config: dict[str, Any]) -> str:
    env_name = {
        "tavily": "TAVILY_BASE_URL",
        "brave": "BRAVE_BASE_URL",
        "serper": "SERPER_BASE_URL",
        "firecrawl": "FIRECRAWL_BASE_URL",
    }[kind]
    config_key = {
        "tavily": "tavilyBaseUrl",
        "brave": "braveBaseUrl",
        "serper": "serperBaseUrl",
        "firecrawl": "firecrawlBaseUrl",
    }[kind]
    default = {
        "tavily": "https://api.tavily.com",
        "brave": "https://api.search.brave.com/res/v1",
        "serper": "https://google.serper.dev",
        "firecrawl": "https://api.firecrawl.dev",
    }[kind]
    return (_first_env(env_name) or _first_config(config, config_key) or default).rstrip("/")


def _search_endpoint(kind: str, config: dict[str, Any]) -> str:
    explicit = (os.environ.get(SEARCH_ENDPOINT_ENV) or "").strip()
    if explicit:
        return explicit
    base = _base_url(kind, config)
    if kind == "firecrawl":
        return f"{base}/search" if base.endswith("/v2") else f"{base}/v2/search"
    return {
        "tavily": f"{base}/search",
        "brave": f"{base}/web/search",
        "serper": f"{base}/search",
    }[kind]


def _firecrawl_scrape_endpoint(config: dict[str, Any]) -> str:
    base = _base_url("firecrawl", config)
    return f"{base}/scrape" if base.endswith("/v2") else f"{base}/v2/scrape"


def _clean_text(value: Any) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def _request_json(
    url: str,
    *,
    label: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = SEARCH_TIMEOUT,
) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read(300).decode("utf-8", "replace")
        suffix = f"：{detail}" if detail else ""
        raise SearchError(f"{label} 请求失败：HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SearchError(f"{label} 请求失败：{exc}") from exc
    if not 200 <= status < 300:
        raise SearchError(f"{label} 请求失败：HTTP {status}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SearchError(f"{label} 响应过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchError(f"{label} 响应不是合法 JSON：{exc}") from exc


def _hits_from_items(items: Any, *, limit: int, url_key: str, title_key: str, snippet_key: str) -> list[SearchHit]:
    if not isinstance(items, list):
        return []
    hits = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get(url_key)
        if not isinstance(url, str) or not url.strip():
            continue
        hits.append(
            SearchHit(
                url=url.strip(),
                title=_clean_text(item.get(title_key)),
                snippet=_clean_text(item.get(snippet_key)),
            )
        )
        if len(hits) >= limit:
            break
    return hits


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, endpoint: str, api_key: str, *, timeout: int = SEARCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        payload = _request_json(
            self.endpoint,
            label="Tavily",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            body={"query": query, "max_results": limit, "search_depth": "basic"},
            timeout=self.timeout,
        )
        return _hits_from_items(
            payload.get("results") if isinstance(payload, dict) else None,
            limit=limit,
            url_key="url",
            title_key="title",
            snippet_key="content",
        )


class BraveSearchProvider:
    name = "brave"

    def __init__(self, endpoint: str, api_key: str, *, timeout: int = SEARCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        params = urllib.parse.urlencode({"q": query, "count": limit})
        payload = _request_json(
            f"{self.endpoint}?{params}",
            label="Brave",
            headers={"X-Subscription-Token": self.api_key},
            timeout=self.timeout,
        )
        web = payload.get("web") if isinstance(payload, dict) else None
        items = web.get("results") if isinstance(web, dict) else None
        return _hits_from_items(items, limit=limit, url_key="url", title_key="title", snippet_key="description")


class SerperSearchProvider:
    name = "serper"

    def __init__(self, endpoint: str, api_key: str, *, timeout: int = SEARCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        payload = _request_json(
            self.endpoint,
            label="Serper",
            method="POST",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            body={"q": query, "num": limit},
            timeout=self.timeout,
        )
        return _hits_from_items(
            payload.get("organic") if isinstance(payload, dict) else None,
            limit=limit,
            url_key="link",
            title_key="title",
            snippet_key="snippet",
        )


class FirecrawlSearchProvider:
    name = "firecrawl"

    def __init__(self, endpoint: str, api_key: str, *, timeout: int = SEARCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, limit: int = MAX_HITS_PER_QUERY) -> list[SearchHit]:
        payload = _request_json(
            self.endpoint,
            label="Firecrawl",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            body={"query": query, "limit": limit, "sources": ["web"]},
            timeout=self.timeout,
        )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise SearchError("Firecrawl 返回失败响应")
        data = payload.get("data")
        web = data.get("web") if isinstance(data, dict) else data
        return _hits_from_items(web, limit=limit, url_key="url", title_key="title", snippet_key="description")


class FirecrawlPageFetcher:
    def __init__(self, endpoint: str, api_key: str, *, timeout: int = SEARCH_TIMEOUT):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, url: str) -> FetchedPage:
        try:
            payload = _request_json(
                self.endpoint,
                label="Firecrawl 抓页",
                method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                body={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                timeout=self.timeout,
            )
        except SearchError as exc:
            raise FetchError(str(exc)) from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise FetchError("Firecrawl 抓页返回失败响应")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FetchError("Firecrawl 抓页缺少 data")
        text = data.get("markdown")
        if not isinstance(text, str) or not text.strip():
            raise FetchError("Firecrawl 抓页没有正文")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        title = str(metadata.get("title") or data.get("title") or "")
        final_url = str(metadata.get("sourceURL") or metadata.get("url") or url)
        return FetchedPage(
            url=url,
            final_url=final_url,
            title=title,
            text=text.strip(),
            content_type="text/markdown; charset=utf-8",
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


def provider_from_env() -> SearchProvider:
    kind = (os.environ.get(SEARCH_PROVIDER_ENV) or "").strip().lower()
    config = None
    if not kind:
        config = _pi_web_search_config()
        kind = str(config.get("searchProvider") or "none").strip().lower()
    if kind == "none":
        return NullSearchProvider()
    if kind == "http":
        endpoint = (os.environ.get(SEARCH_ENDPOINT_ENV) or "").strip()
        if not endpoint:
            return NullSearchProvider()
        api_key = (os.environ.get(SEARCH_API_KEY_ENV) or "").strip() or None
        return HttpSearchProvider(endpoint, api_key=api_key)
    config = config if config is not None else _pi_web_search_config()
    if kind == "auto":
        kind = next(
            (
                candidate
                for candidate in ("tavily", "brave", "serper", "firecrawl")
                if _api_key(candidate, config, generic=False)
            ),
            "none",
        )
    if kind == "none":
        return NullSearchProvider()
    if kind not in ("tavily", "brave", "serper", "firecrawl"):
        raise SearchError(f"未知检索提供方：{kind}")
    api_key = _api_key(kind, config)
    if not api_key:
        raise SearchError(f"{kind} 缺少 API key；设置 {kind.upper()}_API_KEY 或在 pi 的 web-search.json 中配置")
    endpoint = _search_endpoint(kind, config)
    if kind == "tavily":
        return TavilySearchProvider(endpoint, api_key)
    if kind == "brave":
        return BraveSearchProvider(endpoint, api_key)
    if kind == "serper":
        return SerperSearchProvider(endpoint, api_key)
    return FirecrawlSearchProvider(endpoint, api_key)


def fetcher_from_env() -> PageFetcher:
    kind = (os.environ.get(FETCH_PROVIDER_ENV) or "").strip().lower()
    config = None
    if not kind:
        config = _pi_web_search_config()
        kind = str(config.get("fetchProvider") or "http").strip().lower()
    if kind in ("http", "", "none"):
        return HttpPageFetcher()
    if kind != "firecrawl":
        raise SearchError(f"未知抓页提供方：{kind}")
    config = config if config is not None else _pi_web_search_config()
    api_key = _api_key("firecrawl", config)
    if not api_key:
        raise SearchError("firecrawl 抓页缺少 API key；设置 FIRECRAWL_API_KEY 或在 pi 的 web-search.json 中配置")
    return FirecrawlPageFetcher(_firecrawl_scrape_endpoint(config), api_key)


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _iso_now(now: datetime | None = None) -> str:
    return _utc_now(now).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _quote_in_page(quote: Any, text: Any) -> bool:
    needle = normalize_ws(str(quote or ""))
    return bool(needle) and needle in normalize_ws(str(text or ""))


def _required_sources(row: dict[str, Any]) -> int:
    claim = str(row.get("claim") or "")
    needed = str(row.get("needed") or "")
    if row.get("verdict") != "still":
        return 2
    if _NUMBER_RE.search(claim):
        return 2
    if _REQUIRED_NEEDED_RE.search(needed):
        return 2
    return 1


def _question_for(row: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any] | None:
    units = set(row.get("units") or [])
    if not units:
        return None
    for question in questions:
        if units & set(question.get("units") or []):
            return question
    return None


def plan_queries(research: dict[str, Any], *, limit: int = MAX_QUERIES) -> list[dict[str, str]]:
    """把判断矩阵中需要外部核验的行转成有限检索查询。"""
    matrix = [row for row in research.get("matrix") or [] if isinstance(row, dict)]
    questions = [item for item in research.get("questions") or [] if isinstance(item, dict)]
    candidates = []
    for row in matrix:
        if not row.get("id"):
            continue
        needed = str(row.get("needed") or "")
        if row.get("verdict") in {"changed", "caution", "verify"} or _QUERY_NEEDED_RE.search(needed):
            candidates.append(row)
    if not candidates:
        candidates = [row for row in matrix if row.get("id") and row.get("verdict") == "still" and row.get("needed")]
    targets = []
    seen: set[str] = set()
    for row in candidates:
        question = _question_for(row, questions)
        query = str((question or {}).get("query") or "").strip()
        if not query:
            query = f"{row.get('claim') or ''} {row.get('needed') or ''}".strip()
        if not query:
            continue
        query = query[:160]
        key = normalize_ws(query)
        if key in seen:
            continue
        seen.add(key)
        reason = str((question or {}).get("why") or "").strip()
        if not reason:
            reason = f"核对：{row.get('claim') or ''}"
        targets.append(
            {
                "query_id": f"Q{len(targets) + 1}",
                "claim_id": str(row.get("id")),
                "query": query,
                "reason": reason,
            }
        )
        if len(targets) >= limit:
            break
    return targets


def _validated_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise FetchError("地址格式无效") from exc
    if parsed.scheme not in ("http", "https"):
        raise FetchError("只允许 http/https 地址")
    if parsed.username or parsed.password:
        raise FetchError("地址不能包含登录信息")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise FetchError("地址缺少主机名")
    default_port = 80 if parsed.scheme == "http" else 443
    try:
        port = parsed.port
    except ValueError as exc:
        raise FetchError("地址端口无效") from exc
    if port not in (None, default_port):
        raise FetchError("只允许默认端口")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise FetchError("不允许抓取本机地址")
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"域名无法解析：{host}") from exc
    if not addresses:
        raise FetchError(f"域名无法解析：{host}")
    for info in addresses:
        raw_ip = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise FetchError(f"地址解析结果无效：{raw_ip}") from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise FetchError("不允许抓取内网或保留地址")
    return parsed


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None  # noqa: RET501


class HttpPageFetcher:
    def __init__(self, timeout: int = FETCH_TIMEOUT, max_bytes: int = MAX_PAGE_BYTES):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def _open(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})  # noqa: S310
        return self._opener.open(request, timeout=self.timeout)

    def fetch(self, url: str) -> FetchedPage:
        current = url
        _validated_url(current)
        for _redirects in range(MAX_REDIRECTS + 1):
            try:
                response = self._open(current)
            except urllib.error.HTTPError as exc:
                if exc.code in _REDIRECT_CODES:
                    current = self._redirect_target(current, exc.headers.get("Location"))
                    continue
                raise FetchError(f"页面抓取失败：HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise FetchError(f"页面抓取失败：{exc}") from exc
            try:
                status = getattr(response, "status", 200)
                if status in _REDIRECT_CODES:
                    location = response.headers.get("Location")
                    current = self._redirect_target(current, location)
                    continue
                try:
                    return self._page(url, current, response)
                except FetchError:
                    raise
                except Exception as exc:
                    raise FetchError(f"页面解析失败：{exc}") from exc
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise FetchError(f"重定向超过 {MAX_REDIRECTS} 次")

    def _redirect_target(self, current: str, location: Any) -> str:
        if not location:
            raise FetchError("重定向缺少目标地址")
        target = urllib.parse.urljoin(current, str(location))
        _validated_url(target)
        return target

    def _page(self, url: str, final_url: str, response: Any) -> FetchedPage:
        headers = response.headers
        content_type = str(headers.get("Content-Type") or "")
        mime = content_type.split(";", 1)[0].strip().lower()
        if not mime.startswith(("text/html", "text/plain")):
            raise FetchError("不是可读网页")
        raw = response.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise FetchError("页面超过大小上限")
        charset = None
        get_charset = getattr(headers, "get_content_charset", None)
        if callable(get_charset):
            charset = get_charset()
        try:
            decoded = raw.decode(charset or "utf-8", errors="replace")
        except LookupError:
            decoded = raw.decode("utf-8", errors="replace")
        if mime.startswith("text/html"):
            soup = BeautifulSoup(decoded, "html.parser")
            for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg"]):
                tag.decompose()
            title = collapse_ws(soup.title.get_text(" ")) if soup.title else ""
            if soup.title:
                soup.title.decompose()
            text = collapse_ws(soup.get_text(" "))
        else:
            title = ""
            text = collapse_ws(decoded)
        if not text:
            raise FetchError("页面没有可读正文")
        return FetchedPage(
            url=url,
            final_url=final_url,
            title=title,
            text=text[:MAX_PAGE_CHARS],
            content_type=content_type,
            fetched_at=_iso_now(),
        )


def _excerpt_for(text: str, needles: list[str], limit: int) -> str:
    """Return a window around the first query/claim token, not just the page head."""
    if len(text) <= limit:
        return text
    source = text.casefold()
    positions = []
    for needle in needles:
        value = normalize_ws(needle).casefold()
        if len(value) < 2:
            continue
        position = source.find(value)
        if position >= 0:
            positions.append(position)
    if not positions:
        return text[:limit]
    start = max(0, min(positions) - limit // 3)
    end = min(len(text), start + limit)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _verify_payload(
    target: dict[str, Any],
    row: dict[str, Any],
    pages: list[tuple[str, FetchedPage]],
) -> dict[str, Any]:
    needles = [str(target.get("query") or ""), str(row.get("claim") or ""), str(row.get("needed") or "")]
    return {
        "claim": row.get("claim") or "",
        "needed": row.get("needed") or "",
        "pages": [
            {
                "citation_id": citation_id,
                "url": page.final_url,
                "title": page.title,
                "text": _excerpt_for(page.text, needles, MAX_VERIFY_CHARS),
            }
            for citation_id, page in pages
        ],
    }


def _parse_verdicts(payload: Any, pages: list[tuple[str, FetchedPage]]) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("核验结果缺少 results 数组")
    page_map = dict(pages)
    parsed = {}
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        citation_id = str(item.get("citation_id") or "")
        if citation_id not in page_map or citation_id in parsed:
            continue
        supported = item.get("supported")
        if not isinstance(supported, bool):
            raise ValueError("supported 必须是布尔值")
        quote = str(item.get("quote") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if supported and not _quote_in_page(quote, page_map[citation_id].text):
            supported = False
            reason = QUOTE_MISMATCH
        parsed[citation_id] = {
            "supported": supported,
            "quote": quote,
            "reason": reason,
            "error": None,
        }
    return parsed


def verify_candidates(
    client: Any,
    research: dict[str, Any],
    targets: list[dict[str, Any]],
    pages_by_query: dict[str, list[FetchedPage]],
    *,
    model: str | None = None,
    effort: str | None = None,
) -> list[dict[str, Any]]:
    """逐查询核验候选引文；单次调用失败只降级该查询。"""
    rows = {
        str(row.get("id")): row
        for row in research.get("matrix") or []
        if isinstance(row, dict) and row.get("id") is not None
    }
    records = []
    for target in targets or []:
        query_id = str(target.get("query_id") or "")
        pages = pages_by_query.get(query_id) or []
        if not pages:
            continue
        local = [(f"C{index + 1}", page) for index, page in enumerate(pages)]
        row = rows.get(str(target.get("claim_id"))) or {}
        messages = [
            {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(_verify_payload(target, row, local), ensure_ascii=False),
            },
        ]
        parsed = None
        error = None
        for attempt in range(2):
            try:
                payload, _usage = client.complete_json(messages, model=model, effort=effort)
                parsed = _parse_verdicts(payload, local)
                error = None
                break
            except (json.JSONDecodeError, ValueError) as exc:
                error = str(exc)
                if attempt:
                    break
                messages = [
                    *messages,
                    {"role": "user", "content": f"上一次输出无法解析：{exc}。请只输出完整 JSON。"},
                ]
            except (ModelProviderError, RuntimeError) as exc:
                error = str(exc)
                break
        for citation_id, page in local:
            verdict = (parsed or {}).get(citation_id)
            if verdict is None:
                verdict = {
                    "supported": False,
                    "quote": "",
                    "reason": "核验失败" if error else "模型未返回该引文",
                    "error": error,
                }
            records.append(
                {
                    "query_id": query_id,
                    "claim_id": str(target.get("claim_id") or ""),
                    "citation_id": citation_id,
                    "url": page.url,
                    "final_url": page.final_url,
                    "supported": bool(verdict.get("supported")),
                    "quote": str(verdict.get("quote") or ""),
                    "reason": str(verdict.get("reason") or ""),
                    "error": verdict.get("error"),
                }
            )
    return records


def _no_search_payload(research: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    claims = []
    for row in research.get("matrix") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        claims.append(
            {
                "claim_id": str(row["id"]),
                "status": "not_searched",
                "required_sources": _required_sources(row),
                "found_sources": 0,
                "citation_ids": [],
                "reason": "未连接检索提供方",
            }
        )
    return {
        "schema": CITATIONS_SCHEMA_VERSION,
        "prompt_version": CITATIONS_PROMPT_VERSION,
        "searched": False,
        "provider": "none",
        "generated_at": _iso_now(now),
        "queries": [],
        "citations": [],
        "claims": claims,
    }


def _claim_status(
    claim_queries: list[dict[str, Any]],
    supporting: list[dict[str, Any]],
    required_sources: int,
    fetched_urls: set[str],
) -> tuple[str, list[str], int, str]:
    if not claim_queries:
        return "not_searched", [], 0, "未生成检索查询"
    citation_ids = list(dict.fromkeys(item["id"] for item in supporting))
    domains = list(dict.fromkeys(item["source_domain"] for item in supporting if item["source_domain"]))
    if supporting:
        if len(domains) >= required_sources:
            return "verified", citation_ids, len(domains), f"已找到 {len(domains)} 个独立来源"
        return "partial", citation_ids, len(domains), f"仅找到 {len(domains)} 个独立来源，需要 {required_sources} 个"
    urls = {hit_url for query in claim_queries for hit_url in (query.get("hit_urls") or [])}
    if urls & fetched_urls:
        return "unverified", [], 0, "已取回页面，但没有找到可支持的逐字引文"
    if any(query.get("hit_count", 0) > 0 for query in claim_queries):
        return "fetch_failed", [], 0, "检索有结果，但页面抓取失败"
    return "not_found", [], 0, "检索没有返回结果"


def build_citations(
    client: Any,
    research: dict[str, Any],
    *,
    provider: SearchProvider | None = None,
    fetcher: PageFetcher | None = None,
    model: str | None = None,
    effort: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """检索、抓页、核验并汇总成可审计的引用账。"""
    provider = provider or NullSearchProvider()
    if isinstance(provider, NullSearchProvider):
        return _no_search_payload(research, now)
    fetcher = fetcher or HttpPageFetcher()
    targets = plan_queries(research)
    queries = []
    pages_by_query: dict[str, list[FetchedPage]] = {}
    page_citation_ids: dict[tuple[str, str], str] = {}
    fetched_urls: set[str] = set()
    fetched_pages: dict[str, tuple[FetchedPage, str]] = {}
    fetch_errors: dict[str, str] = {}
    seen_urls: set[str] = set()
    fetch_attempts = 0
    next_citation = 1

    for target in targets:
        query_id = target["query_id"]
        hits: list[SearchHit] = []
        status = "ok"
        error = None
        try:
            raw_hits = list(provider.search(target["query"], limit=MAX_HITS_PER_QUERY) or [])
            hits = []
            for hit in raw_hits:
                if isinstance(hit, dict):
                    hit = SearchHit(**hit)
                if isinstance(hit, SearchHit) and hit.url.strip():
                    hits.append(hit)
            hits = hits[:MAX_HITS_PER_QUERY]
        except Exception as exc:  # 提供方失败必须降级
            status = "error"
            error = str(exc)
        if status == "ok" and not hits:
            status = "empty"
        hit_urls = []
        fetched_count = 0
        reused_count = 0
        reused_urls: list[str] = []
        for hit in hits:
            url = hit.url.strip()
            hit_urls.append(url)
            if url in seen_urls:
                if url in fetched_pages:
                    reused_urls.append(url)
                continue
            seen_urls.add(url)
            if fetch_attempts >= MAX_FETCHES:
                continue
            fetch_attempts += 1
            try:
                page = fetcher.fetch(url)
            except Exception as exc:  # 单页失败必须降级
                fetch_errors[url] = str(exc)
                continue
            citation_id = f"C{next_citation}"
            next_citation += 1
            pages_by_query.setdefault(query_id, []).append(page)
            page_citation_ids[(query_id, f"C{len(pages_by_query[query_id])}")] = citation_id
            fetched_pages[url] = (page, citation_id)
            fetched_urls.add(url)
            fetched_count += 1
        for url in reused_urls:
            page, global_id = fetched_pages[url]
            pages_by_query.setdefault(query_id, []).append(page)
            local_id = f"C{len(pages_by_query[query_id])}"
            page_citation_ids[(query_id, local_id)] = global_id
            reused_count += 1
        queries.append(
            {
                "query_id": query_id,
                "claim_id": target["claim_id"],
                "query": target["query"],
                "reason": target["reason"],
                "status": status,
                "hit_count": len(hits),
                "fetched_count": fetched_count,
                "reused_count": reused_count,
                "hit_urls": hit_urls,
                **({"error": error} if error else {}),
            }
        )

    verdicts = verify_candidates(
        client,
        research,
        targets,
        pages_by_query,
        model=model,
        effort=effort,
    )
    verdict_map = {(item["query_id"], item["citation_id"]): item for item in verdicts}
    citation_by_id: dict[str, dict[str, Any]] = {}
    for query_id, pages in pages_by_query.items():
        for index, page in enumerate(pages):
            local_id = f"C{index + 1}"
            global_id = page_citation_ids.get((query_id, local_id))
            verdict = verdict_map.get((query_id, local_id))
            if not global_id or not verdict or not verdict["supported"]:
                continue
            if not _quote_in_page(verdict["quote"], page.text):
                continue
            citation_by_id[global_id] = {
                "id": global_id,
                "url": page.final_url,
                "title": page.title,
                "accessed_at": page.fetched_at,
                "quote": verdict["quote"],
                "source_domain": (urllib.parse.urlsplit(page.final_url).hostname or "").lower(),
                "page_sha256": hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
                "query_id": query_id,
            }

    supporting_by_claim: dict[str, list[dict[str, Any]]] = {}
    for verdict in verdicts:
        if not verdict["supported"]:
            continue
        global_id = page_citation_ids.get((verdict["query_id"], verdict["citation_id"]))
        citation = citation_by_id.get(global_id or "")
        if citation is None:
            continue
        bucket = supporting_by_claim.setdefault(verdict["claim_id"], [])
        if citation not in bucket:
            bucket.append(citation)

    claims = []
    for row in research.get("matrix") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        claim_id = str(row["id"])
        required = _required_sources(row)
        claim_queries = [query for query in queries if query["claim_id"] == claim_id]
        status, citation_ids, found_sources, reason = _claim_status(
            claim_queries,
            supporting_by_claim.get(claim_id) or [],
            required,
            fetched_urls,
        )
        claims.append(
            {
                "claim_id": claim_id,
                "status": status,
                "required_sources": required,
                "found_sources": found_sources,
                "citation_ids": citation_ids,
                "reason": reason,
            }
        )

    for query in queries:
        query.pop("hit_urls", None)
    payload = {
        "schema": CITATIONS_SCHEMA_VERSION,
        "prompt_version": CITATIONS_PROMPT_VERSION,
        "searched": True,
        "provider": getattr(provider, "name", "unknown"),
        "generated_at": _iso_now(now),
        "queries": queries,
        "citations": list(citation_by_id.values()),
        "claims": claims,
    }
    if fetch_errors:
        payload["fetch_errors"] = dict(fetch_errors)
    return payload


def _citation_domain(citation: dict[str, Any]) -> str:
    domain = str(citation.get("source_domain") or "").strip().lower()
    if domain:
        return domain
    url = str(citation.get("url") or "")
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _is_stale(accessed_at: Any, now: datetime | None) -> bool:
    parsed = _parse_iso(accessed_at)
    if parsed is None:
        return False
    return _utc_now(now) - parsed > timedelta(days=STALE_DAYS)


def audit_citations(
    payload: dict[str, Any],
    research: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """G13 引用审计：只查账，不联网。"""
    errors = []
    warnings = []
    if not isinstance(payload, dict):
        return {"status": "fail", "errors": ["引用账不是对象"], "warnings": [], "stats": {}}
    if payload.get("schema") != CITATIONS_SCHEMA_VERSION:
        errors.append("引用账 schema 不匹配")
    if payload.get("prompt_version") != CITATIONS_PROMPT_VERSION:
        errors.append("引用账 prompt_version 不匹配")
    searched = payload.get("searched")
    if not isinstance(searched, bool):
        errors.append("searched 必须是布尔值")
        searched = False
    citations_raw = payload.get("citations")
    if not isinstance(citations_raw, list):
        errors.append("citations 必须是数组")
        citations_raw = []
    claims_raw = payload.get("claims")
    if not isinstance(claims_raw, list):
        errors.append("claims 必须是数组")
        claims_raw = []
    citations = {}
    for citation in citations_raw:
        if not isinstance(citation, dict):
            errors.append("引用项不是对象")
            continue
        citation_id = str(citation.get("id") or "")
        if not citation_id:
            errors.append("引用项缺少 id")
            continue
        if citation_id in citations:
            errors.append(f"引用 id 重复：{citation_id}")
        citations[citation_id] = citation
    matrix_ids = {str(row.get("id")) for row in research.get("matrix") or [] if isinstance(row, dict) and row.get("id")}
    if not searched:
        if citations_raw:
            errors.append("未检索时不能有引用")
    elif str(payload.get("provider") or "") in ("", "none"):
        errors.append("已检索时 provider 不能是 none")
    stats = dict.fromkeys(CLAIM_STATUSES, 0)
    seen_claims = set()
    for claim in claims_raw:
        if not isinstance(claim, dict):
            errors.append("claim 项不是对象")
            continue
        claim_id = str(claim.get("claim_id") or claim.get("id") or "")
        status = claim.get("status")
        if claim_id not in matrix_ids:
            errors.append(f"未知 claim id：{claim_id or '(空)'}")
        if claim_id in seen_claims:
            errors.append(f"claim id 重复：{claim_id}")
        seen_claims.add(claim_id)
        if status not in CLAIM_STATUSES:
            errors.append(f"未知引用状态：{status}")
            continue
        stats[status] += 1
        ids = claim.get("citation_ids") or []
        if not isinstance(ids, list):
            errors.append(f"{claim_id} citation_ids 必须是数组")
            ids = []
        missing = [str(item) for item in ids if str(item) not in citations]
        if missing:
            errors.append(f"{claim_id} 引用了不存在的 citation：{'、'.join(missing)}")
        linked = [citations[str(item)] for item in ids if str(item) in citations]
        required = claim.get("required_sources")
        if not isinstance(required, int) or required < 1:
            required = 1
        if not searched and status != "not_searched":
            errors.append(f"未检索时 claim {claim_id} 不能是 {status}")
        if status == "verified":
            valid = [
                citation
                for citation in linked
                if str(citation.get("url") or "").strip()
                and str(citation.get("quote") or "").strip()
                and str(citation.get("accessed_at") or "").strip()
            ]
            if not valid:
                errors.append(f"{claim_id} verified 缺少带 url/quote/accessed_at 的引用")
            domains = {_citation_domain(citation) for citation in valid if _citation_domain(citation)}
            if len(domains) < required:
                errors.append(f"{claim_id} verified 只有 {len(domains)} 个独立来源，需要 {required} 个")
        if required >= 2:
            linked_domains = [_citation_domain(citation) for citation in linked if _citation_domain(citation)]
            if len(linked_domains) > len(set(linked_domains)):
                warnings.append(f"{claim_id} 来源域名重复，独立来源不足")
        if status in ("partial", "unverified", "fetch_failed", "not_found"):
            warnings.append(f"{claim_id} 状态为 {status}")
    missing_claims = sorted(matrix_ids - seen_claims)
    if missing_claims:
        errors.append(f"引用账缺少判断：{'、'.join(missing_claims)}")
    for citation in citations.values():
        if _is_stale(citation.get("accessed_at"), now):
            warnings.append(STALE_WARNING)
    return {
        "status": "fail" if errors else ("warn" if warnings else "pass"),
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
        "stats": stats,
    }
