"""统一鉴权移动网关（`--bind` + Bearer Token）：代理书目、打包、进度与只读跨书检索。"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bookcorpus import envfile
from bookcorpus.reading.book_types import resolve_read_profile
from bookcorpus.reading.export_profiles import build_profiles_payload
from bookcorpus.reading.library import CLOTHES, Library, _nkey
from bookcorpus.reading.store import UID_RE

from .bundler import ensure_cached_bundle, has_non_stale_companion_cache
from .progress import ProgressStore

MAX_BODY = 1024 * 1024
DEFAULT_PORT = 8780
DEFAULT_SEARCH_URL = "http://127.0.0.1:8765"
DEFAULT_READING_URL = "http://127.0.0.1:8771"
DEFAULT_CROSS_THRESHOLD = 0.45
CROSS_SEARCH_LIMIT = 15

CATALOG_RE = re.compile(r"^/api/mobile/v1/catalog$")
HEALTH_RE = re.compile(r"^/api/mobile/v1/health$")
PROFILES_RE = re.compile(r"^/api/mobile/v1/profiles$")
OUTBOX_RE = re.compile(r"^/api/mobile/v1/outbox$")
OUTBOX_ACK_RE = re.compile(r"^/api/mobile/v1/outbox/ack$")
BUNDLE_RE = re.compile(r"^/api/mobile/v1/bundle/([A-Za-z0-9_-]+)$")
PROGRESS_RE = re.compile(r"^/api/mobile/v1/progress/([A-Za-z0-9_-]+)$")
CROSS_SEARCH_RE = re.compile(r"^/api/mobile/v1/cross-search$")


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _parse_bool_param(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_byte_range(header_val: str, total_size: int) -> tuple[int, int] | None:
    """解析单个 ``Range: bytes=...`` 头，返回闭区间 ``(start, end)``；无效或越界返回 None。"""
    if not header_val or total_size <= 0:
        return None
    raw = header_val.strip()
    if not raw.lower().startswith("bytes="):
        return None
    spec = raw[6:].strip()
    if "," in spec or "-" not in spec:
        return None
    start_str, end_str = spec.split("-", 1)
    start_str = start_str.strip()
    end_str = end_str.strip()
    if not start_str and not end_str:
        return None
    if not start_str:
        if not end_str.isdigit():
            return None
        suffix = int(end_str)
        if suffix <= 0:
            return None
        start = max(0, total_size - suffix)
        return start, total_size - 1
    if not start_str.isdigit():
        return None
    start = int(start_str)
    if start >= total_size:
        return None
    if not end_str:
        return start, total_size - 1
    if not end_str.isdigit():
        return None
    end = min(int(end_str), total_size - 1)
    if end < start:
        return None
    return start, end


class OutboxStore:
    """持久化待拉取 `.bcbook` 队列：``<root>/工作区/阅读/mobile_outbox.json``。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.path = self.root / "工作区" / "阅读" / "mobile_outbox.json"
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(doc, dict) and isinstance(doc.get("tickets"), list):
            return [dict(item) for item in doc["tickets"] if isinstance(item, dict) and item.get("ticket_id")]
        return []

    def _write_unlocked(self, tickets: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": _now_iso(), "tickets": tickets}
        tmp = self.path.with_name(f"{self.path.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def list_tickets(self) -> list[dict[str, Any]]:
        """返回当前待拉取队列。"""
        with self._file_lock():
            return self._read_unlocked()

    def enqueue(self, uids: list[str], *, include_cache: bool = True) -> list[dict[str, Any]]:
        """将一批书目加入待拉取队列并返回新增工单。"""
        clean_uids: list[str] = []
        for raw_uid in uids:
            uid = str(raw_uid or "").strip()
            if not UID_RE.match(uid):
                raise ValueError(f"非法 uid: {raw_uid}")
            if uid not in clean_uids:
                clean_uids.append(uid)
        if not clean_uids:
            raise ValueError("uids 不能为空")

        with self._file_lock():
            tickets = self._read_unlocked()
            added: list[dict[str, Any]] = []
            now = _now_iso()
            for uid in clean_uids:
                ticket = {
                    "ticket_id": f"tkt-{uuid.uuid4().hex[:12]}",
                    "uid": uid,
                    "include_cache": bool(include_cache),
                    "created_at": now,
                }
                tickets.append(ticket)
                added.append(ticket)
            self._write_unlocked(tickets)
            return added

    def ack(self, ticket_ids: list[str]) -> dict[str, Any]:
        """按 ``ticket_id`` 出队已接收工单。"""
        clean_ids = [str(tid or "").strip() for tid in ticket_ids if str(tid or "").strip()]
        if not clean_ids:
            raise ValueError("ticket_ids 不能为空")
        ack_set = set(clean_ids)
        with self._file_lock():
            tickets = self._read_unlocked()
            acked = [t["ticket_id"] for t in tickets if t.get("ticket_id") in ack_set]
            remaining = [t for t in tickets if t.get("ticket_id") not in ack_set]
            self._write_unlocked(remaining)
            return {"ok": True, "acked": acked, "remaining": len(remaining), "tickets": remaining}


class MobileGatewayService:
    """封装移动网关全部本地状态读写与上游跨书检索代理逻辑。"""

    def __init__(
        self,
        root: Path | str,
        *,
        token: str,
        search_url: str = DEFAULT_SEARCH_URL,
        reading_url: str = DEFAULT_READING_URL,
    ) -> None:
        self.library = Library(str(root), overlay_root="")
        self.root = Path(self.library.root)
        self.token = token
        self.search_url = search_url.rstrip("/")
        self.reading_url = reading_url.rstrip("/")
        self.progress = ProgressStore(self.root)
        self.outbox = OutboxStore(self.root)
        self._bundle_locks: dict[str, threading.Lock] = {}
        self._bundle_locks_guard = threading.Lock()

    def _bundle_lock(self, key: str) -> threading.Lock:
        with self._bundle_locks_guard:
            lock = self._bundle_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._bundle_locks[key] = lock
            return lock

    def check_token(self, auth_header: str | None) -> bool:
        """常量时间校验 ``Authorization: Bearer <token>``。"""
        if not auth_header:
            return False
        parts = auth_header.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return hmac.compare_digest(parts[1].strip(), self.token)

    def _md_canonical_version(self, uid: str) -> int:
        man_path = Path(self.library.layout.md(uid)) / ".manifest.json"
        if not man_path.is_file():
            return 0
        try:
            doc = json.loads(man_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(doc, dict):
            return 0
        try:
            return int(doc.get("canonical_version") or 0)
        except (TypeError, ValueError):
            return 0

    def catalog(self, *, query: str = "", has_cache: bool | None = None) -> dict[str, Any]:
        """返回移动端书目列表与元数据。"""
        books = list(self.library.books())
        q_norm = _nkey(query) if query else ""
        items: list[dict[str, Any]] = []
        for book in books:
            uid = str(book["uid"])
            title = str(book.get("title") or uid)
            author = str(book.get("author") or "")
            subjects = list(book.get("subjects") or [])
            if q_norm:
                blob = _nkey(" ".join([uid, title, author, " ".join(subjects)]))
                if q_norm not in blob:
                    continue
            cached = has_non_stale_companion_cache(self.library, uid)
            if has_cache is not None and cached != has_cache:
                continue
            profile = resolve_read_profile(subjects, title=title)
            logical_count = len(self.library.chapters(uid))
            items.append(
                {
                    "uid": uid,
                    "title": title,
                    "author": author,
                    "subjects": subjects,
                    "read_type": str(profile.get("read_type") or "social"),
                    "sub_kind": str(profile.get("sub_kind") or ""),
                    "cloth_color": str(book.get("cloth") or CLOTHES[sum(ord(c) for c in uid) % len(CLOTHES)]),
                    "logical_chapter_count": logical_count,
                    "has_companion_cache": cached,
                    "canonical_version": self._md_canonical_version(uid),
                }
            )
        return {"books": items, "total": len(items)}

    def get_bundle(self, uid: str, *, include_cache: bool = True):
        """线程安全地获取（或构建）确定性 `.bcbook` 缓存文件。"""
        flag = "1" if include_cache else "0"
        with self._bundle_lock(f"{uid}:{flag}"):
            return ensure_cached_bundle(self.library, uid, include_cache=include_cache)

    def cross_search(
        self,
        *,
        question: str,
        uid: str = "",
        exclude_books: list[str] | None = None,
        source_text: str = "",
        threshold: float = DEFAULT_CROSS_THRESHOLD,
    ) -> dict[str, Any]:
        """代理 8765 ``POST /api/run`` 并归一化为精简 ``CrossBookHit`` 列表。"""
        clean_q = str(question or "").strip()[:300]
        if not clean_q:
            raise ValueError("question 不能为空")

        excludes: list[str] = []
        for item in exclude_books or ([uid] if uid else []):
            clean = str(item or "").strip()
            if clean and UID_RE.match(clean) and clean not in excludes:
                excludes.append(clean)

        payload: dict[str, Any] = {
            "question": clean_q,
            "mode": "related",
            "stateless": True,
            "exclude_books": excludes,
            "threshold": float(threshold),
        }
        clean_src = str(source_text or "").strip()[:500]
        if clean_src:
            payload["source_text"] = clean_src

        target_url = f"{self.search_url}/api/run"
        if urllib.parse.urlsplit(target_url).scheme not in {"http", "https"}:
            raise ValueError("search_url 仅支持 http/https")
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            target_url,
            data=body_bytes,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                resp_body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", "replace")
            if exc.code >= 500:
                raise SearchUnavailableError(f"检索服务异常 ({exc.code}): {err_body}") from exc
            raise ValueError(f"检索参数被拒绝 ({exc.code}): {err_body}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SearchUnavailableError(f"无法连接检索服务 {self.search_url}: {exc}") from exc

        try:
            data = json.loads(resp_body)
        except json.JSONDecodeError as exc:
            raise SearchUnavailableError("检索服务返回了无效 JSON") from exc

        plan = data.get("plan") if isinstance(data, dict) else None
        ranking = plan.get("ranking") if isinstance(plan, dict) else None
        if not isinstance(ranking, list):
            ranking = []

        hits: list[dict[str, Any]] = []
        for row in ranking[:CROSS_SEARCH_LIMIT]:
            if not isinstance(row, dict):
                continue
            hits.append(
                {
                    "uid": str(row.get("uid") or row.get("book") or ""),
                    "title": str(row.get("title") or ""),
                    "type": str(row.get("type") or ""),
                    "relevance": float(row.get("relevance") or 0.0),
                    "best_path": str(row.get("best_path") or ""),
                    "best_line": int(row.get("best_line") or 0),
                    "snippet": str(row.get("snippet") or row.get("best_text") or ""),
                }
            )
        return {"ok": True, "hits": hits}


class SearchUnavailableError(RuntimeError):
    """8765 跨书检索服务不可达或返回上游错误。"""


class MobileGatewayHandler(BaseHTTPRequestHandler):
    """移动网关 HTTP 处理器，全端点强制 Bearer Token 鉴权。"""

    service: MobileGatewayService
    quiet = False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if not self.quiet:
            sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
        head: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        if status >= 400:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head and body:
            self.wfile.write(body)

    def _json(
        self,
        status: int,
        payload: Any,
        *,
        extra_headers: dict[str, str] | None = None,
        head: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, extra_headers=extra_headers, head=head)

    def _require_auth(self, *, head: bool = False) -> bool:
        if self.service.check_token(self.headers.get("Authorization")):
            return True
        self._json(
            401,
            {"ok": False, "error": "unauthorized"},
            extra_headers={"WWW-Authenticate": "Bearer"},
            head=head,
        )
        return False

    def _read_json_body(self, *, required: bool = True) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length 非法") from exc
        if length <= 0:
            if required:
                raise ValueError("请求体为空")
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体超过 1MB")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体必须是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_GET(self) -> None:
        self._handle("GET", head=False)

    def do_HEAD(self) -> None:
        self._handle("GET", head=True)

    def do_POST(self) -> None:
        self._handle("POST", head=False)

    def _handle(self, method: str, *, head: bool) -> None:
        if not self._require_auth(head=head):
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if method == "GET":
            if HEALTH_RE.match(path):
                self._json(
                    200,
                    {"ok": True, "service": "mobile-gateway", "schema_version": 1},
                    head=head,
                )
                return
            if PROFILES_RE.match(path):
                self._json(200, build_profiles_payload(), head=head)
                return
            if CATALOG_RE.match(path):
                q = (query.get("q") or [""])[0]
                has_cache_raw = (query.get("has_cache") or [""])[0]
                has_cache = _parse_bool_param(has_cache_raw, default=True) if has_cache_raw != "" else None
                self._json(200, self.service.catalog(query=q, has_cache=has_cache), head=head)
                return
            if OUTBOX_RE.match(path):
                tickets = self.service.outbox.list_tickets()
                self._json(200, {"tickets": tickets}, head=head)
                return
            bundle_match = BUNDLE_RE.match(path)
            if bundle_match:
                include_cache = _parse_bool_param((query.get("include_cache") or ["1"])[0], default=True)
                self._serve_bundle(bundle_match.group(1), include_cache=include_cache, head=head)
                return
            progress_match = PROGRESS_RE.match(path)
            if progress_match:
                try:
                    merged = self.service.progress.get_merged(progress_match.group(1))
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)}, head=head)
                    return
                self._json(200, merged, head=head)
                return
            self._json(404, {"ok": False, "error": "not_found"}, head=head)
            return

        if method == "POST":
            if OUTBOX_RE.match(path):
                self._post_outbox()
                return
            if OUTBOX_ACK_RE.match(path):
                self._post_outbox_ack()
                return
            progress_match = PROGRESS_RE.match(path)
            if progress_match:
                self._post_progress(progress_match.group(1))
                return
            if CROSS_SEARCH_RE.match(path):
                self._post_cross_search()
                return
            self._json(404, {"ok": False, "error": "not_found"})
            return

        self._json(405, {"ok": False, "error": "method_not_allowed"})

    def _serve_bundle(self, uid: str, *, include_cache: bool, head: bool) -> None:
        try:
            artifact = self.service.get_bundle(uid, include_cache=include_cache)
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)}, head=head)
            return
        except FileNotFoundError as exc:
            self._json(404, {"ok": False, "error": str(exc)}, head=head)
            return

        if_none_match = (self.headers.get("If-None-Match") or "").strip()
        if if_none_match and if_none_match == artifact.etag:
            self._send_bytes(
                304,
                b"",
                content_type="application/zip",
                extra_headers={"ETag": artifact.etag, "Accept-Ranges": "bytes"},
                head=True,
            )
            return

        base_headers = {
            "Accept-Ranges": "bytes",
            "ETag": artifact.etag,
            "Content-Disposition": f'attachment; filename="{artifact.uid}.bcbook"',
        }
        range_header = self.headers.get("Range")
        if range_header:
            parsed_range = _parse_byte_range(range_header, artifact.size)
            if parsed_range is None:
                self._send_bytes(
                    416,
                    b"",
                    content_type="application/zip",
                    extra_headers={"Content-Range": f"bytes */{artifact.size}", **base_headers},
                    head=True,
                )
                return
            start, end = parsed_range
            length = end - start + 1
            if head:
                data = b""
            else:
                with artifact.path.open("rb") as fh:
                    fh.seek(start)
                    data = fh.read(length)
            headers = {**base_headers, "Content-Range": f"bytes {start}-{end}/{artifact.size}"}
            self.send_response(206)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if not head and data:
                self.wfile.write(data)
            return

        if head:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(artifact.size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in base_headers.items():
                self.send_header(k, v)
            self.end_headers()
            return

        data = artifact.path.read_bytes()
        self._send_bytes(200, data, content_type="application/zip", extra_headers=base_headers, head=False)

    def _post_outbox(self) -> None:
        try:
            body = self._read_json_body()
            raw_uids = body.get("uids")
            if raw_uids is None and body.get("uid"):
                raw_uids = [body.get("uid")]
            if not isinstance(raw_uids, list):
                raise ValueError("uids 必须是字符串数组")
            uids = [str(u) for u in raw_uids]
            for u in uids:
                if self.service.library.book(u) is None:
                    raise FileNotFoundError(f"书目不存在: {u}")
            include_cache = bool(body.get("include_cache", True))
            added = self.service.outbox.enqueue(uids, include_cache=include_cache)
        except FileNotFoundError as exc:
            self._json(404, {"ok": False, "error": str(exc)})
            return
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, {"ok": True, "added": added, "tickets": self.service.outbox.list_tickets()})

    def _post_outbox_ack(self) -> None:
        try:
            body = self._read_json_body()
            raw_ids = body.get("ticket_ids")
            if not isinstance(raw_ids, list):
                raise ValueError("ticket_ids 必须是字符串数组")
            result = self.service.outbox.ack([str(item) for item in raw_ids])
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, result)

    def _post_progress(self, uid: str) -> None:
        try:
            body = self._read_json_body()
            saved = self.service.progress.put_shard(body, uid=uid)
            merged = self.service.progress.get_merged(uid)
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(
            200,
            {
                "ok": True,
                **saved.to_dict(),
                "merged_high_water_mark_ordinal": merged["merged_high_water_mark_ordinal"],
            },
        )

    def _post_cross_search(self) -> None:
        try:
            body = self._read_json_body()
            question = str(body.get("question") or body.get("query") or "")
            uid = str(body.get("uid") or "")
            raw_excl = body.get("exclude_books")
            exclude_books = [str(x) for x in raw_excl] if isinstance(raw_excl, list) else None
            source_text = str(body.get("source_text") or "")
            threshold_raw = body.get("threshold", DEFAULT_CROSS_THRESHOLD)
            threshold = float(threshold_raw) if threshold_raw is not None else DEFAULT_CROSS_THRESHOLD
            result = self.service.cross_search(
                question=question,
                uid=uid,
                exclude_books=exclude_books,
                source_text=source_text,
                threshold=threshold,
            )
        except SearchUnavailableError as exc:
            self._json(503, {"ok": False, "error": str(exc)})
            return
        except (TypeError, ValueError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, result)


def _detect_pair_host(bind_host: str) -> str:
    if bind_host not in {"0.0.0.0", "::", ""}:  # noqa: S104
        return bind_host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return str(ip)
    except OSError:
        pass
    return "127.0.0.1"


def build_server(
    root: Path | str,
    *,
    bind: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    token: str = "",
    search_url: str = DEFAULT_SEARCH_URL,
    reading_url: str = DEFAULT_READING_URL,
    quiet: bool = False,
) -> tuple[ThreadingHTTPServer, MobileGatewayService]:
    """创建已绑定的移动网关 HTTP 服务实例（供 CLI 与测试复用）。"""
    resolved_token = token.strip() or os.environ.get("BOOKCORPUS_MOBILE_TOKEN", "").strip() or secrets.token_hex(32)
    service = MobileGatewayService(
        root,
        token=resolved_token,
        search_url=search_url,
        reading_url=reading_url,
    )

    class _BoundHandler(MobileGatewayHandler):
        pass

    _BoundHandler.service = service
    _BoundHandler.quiet = quiet
    server = ThreadingHTTPServer((bind, port), _BoundHandler)
    return server, service


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：启动 `--mobile-bind` 统一鉴权移动网关。"""
    envfile.load()
    parser = argparse.ArgumentParser(description="book-corpus 统一鉴权移动网关 (--mobile-bind)")
    parser.add_argument("--bind", default="0.0.0.0", help="绑定地址，默认 0.0.0.0")  # noqa: S104
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--root", default=None, help="实例根，含 books.toml；默认读 BOOKS_ROOT")
    parser.add_argument("--token", default="", help="Bearer Token（省略则读 BOOKCORPUS_MOBILE_TOKEN 或随机生成）")
    parser.add_argument("--search-url", default=DEFAULT_SEARCH_URL, help="8765 检索服务地址")
    parser.add_argument("--reading-url", default=DEFAULT_READING_URL, help="8771 阅读服务地址")
    parser.add_argument("--quiet", action="store_true", help="关闭逐条请求访问日志")
    args = parser.parse_args(argv)

    try:
        root = str(envfile.resolve_books_root(args.root))
    except SystemExit as exc:
        parser.error(str(exc))

    try:
        server, service = build_server(
            root,
            bind=args.bind,
            port=args.port,
            token=args.token,
            search_url=args.search_url,
            reading_url=args.reading_url,
            quiet=args.quiet,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))

    actual_port = int(server.server_address[1])
    pair_host = _detect_pair_host(args.bind)
    pair_uri = f"bc-pair://v1?base=http://{pair_host}:{actual_port}&token={service.token}"
    print(f"移动网关 {actual_port} · root={service.root} · bind={args.bind}", flush=True)
    print(f"配对 URI: {pair_uri}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
