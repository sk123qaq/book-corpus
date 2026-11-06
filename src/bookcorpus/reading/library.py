"""实例书库的只读投影：书目、章表、章节正文与全书检索。"""

import glob
import hashlib
import json
import os
import re
import unicodedata
from functools import lru_cache

from ..scripts.layout import Layout, find_root
from .mdparse import _take_h1, chapter_title, non_space_count, parse_markdown, strip_front_matter

CHAPTER_RE = re.compile(r"^(\d+)_(.*)\.md$")
TITLE_PAGE_MAX_CHARS = 120
BODY_MIN_CHARS = 500
BODY_STEM_PREFIX = "前置页"
AUTHOR_NOISE_RE = re.compile(r"(?:z-library(?:\.sk)?|1lib\.sk|z-lib\.sk)", re.I)
CLOTHES = ("#1E3A36", "#2C4056", "#6B2B2B", "#A87A3E", "#4A5839", "#3F4E5A", "#5A3E2B", "#2F4F4F")
# 叠加层默认关闭：Work/book-corpus-data 暂不作为本项目的数据源。
# 需要时由调用方显式传 overlay_root，不读环境变量。
DEFAULT_OVERLAY = ""


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


@lru_cache(maxsize=2048)
def _read_meta(path, mtime, size):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    meta, body = strip_front_matter(raw)
    title = chapter_title(raw)
    _, rest = _take_h1(body)
    body_no_space = non_space_count(rest)
    full_chars = non_space_count(body)
    return {
        "title": title,
        "chars": body_no_space,
        "full_chars": full_chars,
        "front_title": meta.get("title", ""),
    }


class Library:
    """``root`` 是实例根（含 books.toml）。所有读路径都由 Layout 推出。"""

    def __init__(self, root=None, overlay_root=None):
        found = find_root(root) if root else find_root()
        if not found:
            raise FileNotFoundError("找不到 books.toml；用 --root 指定实例根")
        self.layout = Layout(found)
        self.root = self.layout.root
        if overlay_root is None:
            overlay_root = DEFAULT_OVERLAY
        self.overlay_root = overlay_root
        self._books = None
        self._chapters = {}
        self._overlay = None

    # ---- 书目 ---------------------------------------------------------
    def _load_books(self):
        if self._books is not None:
            return self._books
        books = []
        for path in sorted(glob.glob(os.path.join(self.layout.books, "*", "book.json"))):
            uid = os.path.basename(os.path.dirname(path))
            if uid.startswith("_"):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            chapters = self.chapters(uid)
            if not chapters:
                continue
            total_chars = sum(item.get("full_chars", item["chars"]) for item in chapters)
            books.append(
                {
                    "uid": uid,
                    "title": data.get("title") or uid,
                    "title_short": _short_title(data.get("title") or uid),
                    "author": _clean_author(data.get("author")),
                    "author_display": _clean_author(data.get("author")) or "作者待校",
                    "subjects": list(data.get("subjects") or []),
                    "subject": (data.get("subjects") or ["未分类"])[0],
                    "retention": data.get("retention", ""),
                    "chapters": len(chapters),
                    "chapter_count": len(chapters),
                    "chars": total_chars,
                    "packs": 0,
                    "readable": True,
                    "color": _cloth(uid),
                    "cloth": _cloth(uid),
                    "dir": self.layout.book(uid),
                }
            )
        self._books = books
        return books

    def books(self):
        return self._load_books()

    def book(self, uid):
        for book in self._load_books():
            if book["uid"] == uid:
                return book
        return None

    def chapters(self, uid):
        if uid in self._chapters:
            return self._chapters[uid]
        md_dir = self.layout.md(uid)
        chapters = []
        if os.path.isdir(md_dir):
            for name in os.listdir(md_dir):
                match = CHAPTER_RE.match(name)
                if not match:
                    continue
                path = os.path.join(md_dir, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                meta = _read_meta(path, stat.st_mtime_ns, stat.st_size)
                chapters.append(
                    {
                        "n": int(match.group(1)),
                        "file": name,
                        "stem": match.group(2),
                        "title": meta["title"] or match.group(2),
                        "chars": meta["chars"],
                        "full_chars": meta.get("full_chars", meta["chars"]),
                    }
                )
        chapters.sort(key=lambda item: (item["n"], item["file"]))
        chapters = self._merge_title_pages(chapters)
        for ordinal, chapter in enumerate(chapters, 1):
            chapter["ordinal"] = ordinal
        self._chapters[uid] = chapters
        return chapters

    @staticmethod
    def _merge_title_pages(chapters):
        """Merge a short title/divider page with its following 前置页 body.

        Some EPUB conversions write the chapter title and its epigraph as one md
        file, then the real body as ``NN_前置页 X.md``.  Treating both as chapters
        makes the title page a 30-Han "chapter" and hides the body behind a
        separate entry.  Keep the title page's ``n`` (stable URL) and absorb the
        body file into it.
        """
        merged = []
        index = 0
        while index < len(chapters):
            current = dict(chapters[index])
            following = chapters[index + 1] if index + 1 < len(chapters) else None
            if (
                current["chars"] <= TITLE_PAGE_MAX_CHARS
                and following is not None
                and following["stem"].startswith(BODY_STEM_PREFIX)
                and following["chars"] >= BODY_MIN_CHARS
            ):
                current["body_file"] = following["file"]
                current["body_stem"] = following["stem"]
                current["body_n"] = following["n"]
                current["chars"] += following["chars"]
                current["full_chars"] = current.get("full_chars", 0) + following.get("full_chars", 0)
                current["title"] = current["title"] or following["title"]
                current["merged"] = True
                current["source_files"] = [current["file"], following["file"]]
                merged.append(current)
                index += 2
                continue
            current["merged"] = False
            current["source_files"] = [current["file"]]
            merged.append(current)
            index += 1
        return merged

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
        parsed = parse_markdown(raw, chapter["stem"], base_dir=os.path.dirname(paths[0]))
        if len(chunks) > 1:
            body_raw = chunks[1].decode("utf-8", "replace")
            body = parse_markdown(body_raw, chapter.get("body_stem", "body"), base_dir=os.path.dirname(paths[1]))
            blocks = list(parsed.get("blocks") or []) + list(body.get("blocks") or [])
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
        parsed["source_files"] = [os.path.basename(path) for path in paths]
        parsed["source_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
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
        for book in self._load_books():
            for chapter in self.chapters(book["uid"]):
                if q in _nkey(chapter["title"]):
                    chapters.append(
                        {
                            "uid": book["uid"],
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

    def catalog(self, query="", subject=""):
        metadata = self.metadata()
        total = len(self._load_overlay()) or (len(metadata) + len(self._load_books()))
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
