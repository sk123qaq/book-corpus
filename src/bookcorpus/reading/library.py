"""实例书库的只读投影：书目、章表、章节正文与全书检索。"""

import glob
import hashlib
import json
import os
import re
import threading
import time
import unicodedata

from ..md.roles import load_manifest_chapter_roles, resolve_chapter_role
from ..scripts.layout import Layout, find_root
from ..scripts.lib_common import DOWNLOAD_SITE_TOKEN_RE
from .chapters import merge_title_pages
from .footnotes import NOTE_CHAPTER_TITLE_RE, resolve_chapter_footnotes
from .mdparse import non_space_count, parse_markdown
from .shelf_index import ShelfIndex

AUTHOR_NOISE_RE = DOWNLOAD_SITE_TOKEN_RE
CLOTHES = ("#1E3A36", "#2C4056", "#6B2B2B", "#A87A3E", "#4A5839", "#3F4E5A", "#5A3E2B", "#2F4F4F")
# 叠加层默认关闭：不读旧数据仓的叠加层，需要时由调用方显式传 overlay_root。
# 需要时由调用方显式传 overlay_root，不读环境变量。
DEFAULT_OVERLAY = ""
DEFAULT_SHELF_TTL = 2.0


def _shelf_ttl():
    raw = os.environ.get("READING_SHELF_TTL", "").strip()
    if not raw:
        return DEFAULT_SHELF_TTL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_SHELF_TTL


def _nkey(text):
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", "", normalized)


def _clean_author(author):
    text = AUTHOR_NOISE_RE.sub(" ", author or "")
    text = re.sub(r"^[\s,，、;；/]+|[\s,，、;；/]+$", "", text)
    return re.sub(r"\s{2,}", " ", text)


def _short_title(title):
    text = (title or "").split("=")[0].strip()
    return re.sub(r"\s{2,}", " ", text) or title


def _cloth(uid):
    return CLOTHES[sum(ord(ch) for ch in uid) % len(CLOTHES)]


class Library:
    """``root`` 是实例根（含 books.toml）。所有读路径都由 Layout 推出。"""

    def __init__(self, root=None, overlay_root=None):
        found = find_root(root, ignore_env=True) if root else find_root()
        if not found:
            raise FileNotFoundError("找不到 books.toml；用 --root 指定实例根")
        self.layout = Layout(found)
        self.root = self.layout.root
        if overlay_root is None:
            overlay_root = DEFAULT_OVERLAY
        self.overlay_root = overlay_root
        self._books = None
        self._books_lock = threading.Lock()
        self._books_sig = None
        self._books_checked = 0.0
        self._books_ttl = _shelf_ttl()
        self._index = None
        self._index_lock = threading.Lock()
        self._chapters = {}
        self._overlay = None

    # ---- 书目 ---------------------------------------------------------
    def prepare_index(self):
        """按盘上签名核对章表索引（冷建或增量）。服务启动时调一次，首个请求就不必等。"""
        return self._shelf_index().ensure()

    def _shelf_index(self):
        """章表索引：第一次用时建库或核对签名（单飞）。"""
        if self._index is None:
            with self._index_lock:
                if self._index is None:
                    index = ShelfIndex(self.layout)
                    index.ensure()
                    self._index = index
        return self._index

    def _book_entry(self, uid, info):
        if not info or not info["chapters"]:
            return None
        path = os.path.join(self.layout.book(uid), "book.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        chapter_count = info["chapters"]
        return {
            "uid": uid,
            "title": data.get("title") or uid,
            "title_short": _short_title(data.get("title") or uid),
            "author": _clean_author(data.get("author")),
            "author_display": _clean_author(data.get("author")) or "作者待校",
            "subjects": list(data.get("subjects") or []),
            "subject": (data.get("subjects") or ["未分类"])[0],
            "retention": data.get("retention", ""),
            "chapters": chapter_count,
            "chapter_count": chapter_count,
            "chars": info["chars"],
            "packs": 0,
            "readable": True,
            "color": _cloth(uid),
            "cloth": _cloth(uid),
            "dir": self.layout.book(uid),
        }

    def _books_signature(self):
        """书架签名：每本 book.json 与 md 目录的 stat。"""
        try:
            entries = list(os.scandir(self.layout.books))
        except OSError:
            return None
        parts = []
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            try:
                meta = os.stat(os.path.join(entry.path, "book.json"))
                try:
                    md = os.stat(os.path.join(entry.path, "md"))
                    md_sig = (md.st_mtime_ns, md.st_ino)
                except OSError:
                    md_sig = (0, 0)
            except OSError:
                continue
            parts.append((entry.name, md_sig, meta.st_mtime_ns, meta.st_size))
        return tuple(sorted(parts))

    def _shelf_outdated(self):
        return time.monotonic() - self._books_checked >= self._books_ttl

    def _load_books(self):
        cached = self._books
        if cached is not None and not self._shelf_outdated():
            return cached
        with self._books_lock:
            if self._books is not None and not self._shelf_outdated():
                return self._books
            signature = self._books_signature()
            if self._books is not None and (signature is None or signature == self._books_sig):
                self._books_checked = time.monotonic()
                return self._books
            self._books_checked = time.monotonic()
        books = self._rebuild_books()
        with self._books_lock:
            if books is not None:
                self._books = books
                self._books_sig = signature
            self._books_checked = time.monotonic()
            return self._books if self._books is not None else []

    def _rebuild_books(self):
        """重建书单；索引被别的写者占着时返回 None，旧列表与旧签名都留着。"""
        index = self._shelf_index()
        result = index.ensure()
        if result.get("mode") == "busy":
            return None
        stats = index.book_stats()
        books = []
        for path in sorted(glob.glob(os.path.join(self.layout.books, "*", "book.json"))):
            uid = os.path.basename(os.path.dirname(path))
            if uid.startswith("_"):
                continue
            entry = self._book_entry(uid, stats.get(uid))
            if entry is not None:
                books.append(entry)
        return books

    def books(self):
        return self._load_books()

    def book(self, uid):
        for book in self._load_books():
            if book["uid"] == uid:
                # 服务运行中改章：只核对该书的章目录，不重启也能看到修改。
                info = self._shelf_index().refresh_book(uid)
                book["chapters"] = info["chapters"]
                book["chapter_count"] = info["chapters"]
                book["chars"] = info["chars"]
                self._chapters.pop(uid, None)
                return book
        # 运行中刚新拆章/入库的书：增量补扫单书并挂入缓存，不必重启服务。
        if not uid or uid.startswith("_"):
            return None
        info = self._shelf_index().refresh_book(uid)
        entry = self._book_entry(uid, info)
        if entry is None:
            return None
        with self._books_lock:
            if self._books is not None and not any(b["uid"] == uid for b in self._books):
                self._books.append(entry)
                self._books.sort(key=lambda b: b["uid"])
        self._chapters.pop(uid, None)
        return entry

    def chapters(self, uid):
        cached = self._chapters.get(uid)
        if cached is not None:
            return cached
        manifest_roles = load_manifest_chapter_roles(self.layout.md(uid))
        raw_rows = self._shelf_index().chapters(uid)
        for row in raw_rows:
            fm_role = row.pop("fm_role", "")
            row["chapter_role"] = resolve_chapter_role(
                meta={"chapter_role": fm_role} if fm_role else None,
                manifest_roles=manifest_roles,
                file_name=row["file"],
            )
        chapters = merge_title_pages(raw_rows)
        for ordinal, chapter in enumerate(chapters, 1):
            chapter["ordinal"] = ordinal
        if len(self._chapters) >= 32:
            self._chapters.clear()
        self._chapters[uid] = chapters
        return chapters

    def chapter(self, uid, n):
        for chapter in self.chapters(uid):
            if chapter["n"] == int(n):
                return chapter
        return None

    def path_for(self, uid, n):
        chapter = self.chapter(uid, n)
        if not chapter:
            return None
        return os.path.join(self.layout.md(uid), chapter["file"])

    def redirect_target(self, uid, n):
        """被并入前一章的 ``前置页`` 旧章号 → 合并后的章号。"""
        for chapter in self.chapters(uid):
            if chapter.get("body_n") == int(n):
                return chapter["n"]
        return None

    def paths_for(self, uid, n):
        chapter = self.chapter(uid, n)
        if not chapter:
            return []
        md_dir = self.layout.md(uid)
        return [os.path.join(md_dir, name) for name in chapter.get("source_files") or [chapter["file"]]]

    def read_chapter(self, uid, n):
        chapter = self.chapter(uid, n)
        if not chapter:
            return None
        paths = self.paths_for(uid, n)
        chunks = []
        for path in paths:
            with open(path, "rb") as fh:
                chunks.append(fh.read())
        raw_bytes = b"\0".join(chunks)
        raw = chunks[0].decode("utf-8", "replace")
        first_name = os.path.basename(paths[0])
        source_names = [os.path.basename(path) for path in paths]
        note_titles = [
            str(item.get("title") or "")
            for item in self.chapters(uid)
            if NOTE_CHAPTER_TITLE_RE.match(str(item.get("title") or "").strip())
        ]
        parsed = parse_markdown(
            raw,
            chapter["stem"],
            base_dir=os.path.dirname(paths[0]),
            source_files=source_names,
            note_chapters=note_titles,
        )
        for block in parsed.get("blocks") or []:
            block["source_file"] = first_name
        if len(chunks) > 1:
            body_raw = chunks[1].decode("utf-8", "replace")
            second_name = os.path.basename(paths[1])
            body = parse_markdown(
                body_raw,
                chapter.get("body_stem", "body"),
                base_dir=os.path.dirname(paths[1]),
                source_files=source_names,
                note_chapters=note_titles,
            )
            body_blocks = list(body.get("blocks") or [])
            for block in body_blocks:
                block["source_file"] = second_name
            blocks = list(parsed.get("blocks") or []) + body_blocks
            for block_index, block in enumerate(blocks, 1):
                block["id"] = f"B{block_index:02d}"
            parsed["blocks"] = blocks
            parsed["body_han"] = int(parsed.get("body_han") or 0) + int(body.get("body_han") or 0)
            parsed["body_chars"] = int(parsed.get("body_chars") or 0) + int(body.get("body_chars") or 0)
            parsed["h1"] = parsed.get("h1") or body.get("h1") or ""
            meta = dict(body.get("meta") or {})
            meta.update(parsed.get("meta") or {})
            parsed["meta"] = meta
            parsed["title"] = parsed.get("title") or body.get("title") or chapter["title"]
            parsed["body_file"] = os.path.basename(paths[1])
        parsed["uid"] = uid
        parsed["n"] = int(n)
        parsed["ordinal"] = chapter.get("ordinal")
        parsed["file"] = os.path.basename(paths[0])
        parsed["source_files"] = source_names
        parsed["source_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        resolve_chapter_footnotes(parsed["blocks"], source_files=source_names, note_chapters=note_titles)
        parsed["body_han"] = sum(block["chars"] for block in parsed["blocks"])
        parsed["body_chars"] = sum(non_space_count(block["text"]) for block in parsed["blocks"])
        return parsed

    # ---- 全库检索 -----------------------------------------------------
    def search(self, query, limit=60):
        q = _nkey(query)
        if not q:
            return {"books": [], "chapters": [], "authors": [], "subjects": []}
        books = []
        chapters = []
        authors = {}
        subjects = {}
        for book in self._load_books():
            blob = _nkey(" ".join([book["title"], book["author"], " ".join(book["subjects"])]))
            if q not in blob:
                continue
            books.append(book)
            if book["author"]:
                authors[book["author"]] = authors.get(book["author"], 0) + 1
            for subject in book["subjects"]:
                subjects[subject] = subjects.get(subject, 0) + 1
            if len(books) >= limit:
                break
        # 索引流式逐本给：并发搜索时内存只占一本书，不物化全库。
        by_uid = {book["uid"]: book for book in self._load_books()}
        stream = self._shelf_index().iter_chapters()
        try:
            for uid, rows in stream:
                book = by_uid.get(uid)
                if book is None:
                    continue
                for chapter in merge_title_pages(rows):
                    if q in _nkey(chapter["title"]):
                        chapters.append(
                            {
                                "uid": uid,
                                "n": chapter["n"],
                                "title": chapter["title"],
                                "book_title": book["title_short"],
                                "chars": chapter["chars"],
                            }
                        )
                        if len(chapters) >= limit:
                            break
                if len(chapters) >= limit:
                    break
        finally:
            stream.close()
        return {
            "books": books[:limit],
            "chapters": chapters,
            "authors": [{"name": k, "count": v} for k, v in sorted(authors.items(), key=lambda kv: (-kv[1], kv[0]))],
            "subjects": [{"name": k, "count": v} for k, v in sorted(subjects.items(), key=lambda kv: (-kv[1], kv[0]))],
        }

    # ---- 元数据覆盖 ---------------------------------------------------
    def _load_overlay(self):
        if self._overlay is not None:
            return self._overlay
        if not self.overlay_root:
            self._overlay = []
            return self._overlay
        books_dir = os.path.join(self.overlay_root, "books")
        records = []
        if os.path.isdir(books_dir):
            for path in sorted(glob.glob(os.path.join(books_dir, "*", "book.json"))):
                try:
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                records.append(
                    {
                        "uid": data.get("uid") or os.path.basename(os.path.dirname(path)),
                        "title": data.get("title") or "",
                        "title_short": _short_title(data.get("title") or ""),
                        "author": _clean_author(data.get("author")),
                        "subjects": list(data.get("subjects") or []),
                        "overlay": True,
                        "readable": False,
                        "key": "meta:%s" % (data.get("uid") or os.path.basename(os.path.dirname(path))),
                    }
                )
        self._overlay = records
        return records

    def metadata(self):
        """叠加层书目：只用于书目页检索，不参与书架/章节/读本。

        按书名 + 作者与实例书去重；实例作者未知时不去重，避免误伤同名书。
        """
        readable = self._load_books()
        by_title = {}
        for book in readable:
            by_title.setdefault(_nkey(book["title"]), []).append(book)
        out = []
        for record in self._load_overlay():
            matched = False
            for book in by_title.get(_nkey(record["title"]), []):
                if book["author"] and record["author"]:
                    if _nkey(book["author"]) in _nkey(record["author"]) or _nkey(record["author"]) in _nkey(
                        book["author"]
                    ):
                        matched = True
                        break
                elif not book["author"] and not record["author"]:
                    matched = True
                    break
            if not matched:
                out.append(record)
        return out

    def total(self):
        """书目总数：有叠加层时以叠加层为准，否则实例书 + 叠加元数据。"""
        overlay = self._load_overlay()
        return len(overlay) or (len(self.metadata()) + len(self._load_books()))

    def catalog(self, query="", subject=""):
        metadata = self.metadata()
        total = self.total()
        readable = [book for book in self.books() if subject in book["subjects"]] if subject else list(self.books())
        if query:
            found = self.search(query)
            readable = found["books"]
            chapters = found["chapters"]
            authors = found["authors"]
            subjects = found["subjects"]
            q = _nkey(query)
            metadata = [
                record
                for record in metadata
                if q in _nkey(" ".join([record["title"], record["author"], " ".join(record["subjects"])]))
            ][:80]
        else:
            chapters, authors, subjects = [], [], []
            metadata = []
        if subject:
            metadata = [record for record in metadata if subject in record["subjects"]]
        return {
            "query": query,
            "subject": subject,
            "total": total,
            "readable_count": len(self.books()),
            "readable": readable,
            "books": readable,
            "metadata": metadata,
            "chapters": chapters,
            "authors": authors,
            "subjects": subjects,
        }

    def subjects(self):
        counts = {}
        for book in self.books():
            for subject in book["subjects"]:
                counts[subject] = counts.get(subject, 0) + 1
        return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
