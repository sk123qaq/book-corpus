"""章表索引：每章一行，按文件签名核对，变化才重读。

书架只需要每本有多少章、多少字。旧实现为这两个数把每本每一章都读一遍。
索引把这件事变成一次 ``scandir`` + ``stat``：签名没变就不读文件；改一章
只重读那个文件、改一行；整本重拆只重读那本书。

库文件在 ``<root>/build/阅读/shelf-index.db``，是可重建产物。索引缺失时
由服务在第一次请求里建好（约十几秒），之后每次启动只做签名核对。
"""

import os
import sqlite3
import threading
import time
from pathlib import Path

from .chapters import CHAPTER_RE, book_totals
from .mdparse import lean_meta

SCHEMA_VERSION = 1
# lean_meta 的口径变了就递增，下次启动整库重建。
PARSER_VERSION = 1
DB_DIR = "阅读"
DB_NAME = "shelf-index.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chapter (
    uid TEXT NOT NULL,
    name TEXT NOT NULL,
    n INTEGER NOT NULL,
    stem TEXT NOT NULL,
    title TEXT NOT NULL,
    chars INTEGER NOT NULL,
    full_chars INTEGER NOT NULL,
    ino INTEGER NOT NULL,
    ctime_ns INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    PRIMARY KEY (uid, name)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS book (
    uid TEXT PRIMARY KEY,
    chapters INTEGER NOT NULL,
    chars INTEGER NOT NULL
) WITHOUT ROWID;
"""

UPSERT_CHAPTER = """
INSERT INTO chapter (uid, name, n, stem, title, chars, full_chars, ino, ctime_ns, mtime_ns, size)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (uid, name) DO UPDATE SET
    n = excluded.n, stem = excluded.stem, title = excluded.title,
    chars = excluded.chars, full_chars = excluded.full_chars,
    ino = excluded.ino, ctime_ns = excluded.ctime_ns,
    mtime_ns = excluded.mtime_ns, size = excluded.size
"""


class ShelfIndex:
    """``Layout`` 上的章表索引。读路径无副作用，写路径由 ``_lock`` 串行化。"""

    def __init__(self, layout):
        self.layout = layout
        self.path = str(Path(layout.build) / DB_DIR / DB_NAME)
        self._lock = threading.Lock()

    # ---- 连接与建表 ---------------------------------------------------
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _prepare(self, conn):
        """建表并核对版本。返回 fresh / rebuild / incremental。"""
        conn.executescript(SCHEMA)
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        schema = stored.get("schema_version")
        parser = stored.get("parser_version")
        if schema is None and parser is None:
            return "fresh" if not conn.execute("SELECT 1 FROM chapter LIMIT 1").fetchone() else "rebuild"
        if schema != str(SCHEMA_VERSION) or parser != str(PARSER_VERSION):
            return "rebuild"
        return "incremental"

    def ensure(self):
        """建库或增量核对。返回本次做了什么。"""
        started = time.time()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                return self._ensure_locked(started)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    # 另一次 ensure 正在写：库是健康的，别当损坏改名。
                    return {
                        "mode": "busy",
                        "parsed": 0,
                        "removed": 0,
                        "books": 0,
                        "chapters": 0,
                        "elapsed": round(time.time() - started, 2),
                    }
                self._quarantine("corrupt")
                return self._ensure_locked(started)
            except sqlite3.DatabaseError:
                # 索引损坏不是数据丢失：移到一边，从磁盘重来一次。
                self._quarantine("corrupt")
                return self._ensure_locked(started)

    def _ensure_locked(self, started):
        conn = self._connect()
        try:
            mode = self._prepare(conn)
            if mode == "rebuild":
                conn.executescript("DROP TABLE IF EXISTS chapter; DROP TABLE IF EXISTS book;")
                conn.executescript(SCHEMA)
            parsed, removed, books, chapters = self._sync(conn, None)
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('parser_version', ?)", (str(PARSER_VERSION),)
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),)
            )
            conn.commit()
            return {
                "mode": mode,
                "parsed": parsed,
                "removed": removed,
                "books": books,
                "chapters": chapters,
                "elapsed": round(time.time() - started, 2),
            }
        finally:
            conn.close()

    def _quarantine(self, kind):
        stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.path}{suffix}")
            if path.exists():
                path.replace(path.with_name(f"{path.name}.{kind}-{stamp}"))

    def rebuild(self):
        """整库重来。服务运行中调用也行：下次查询会等新库建完。"""
        with self._lock:
            # 重建的是健康库，用 .bak 而不是 .corrupt，免得和真损坏混在一起。
            self._quarantine("bak")
        return self.ensure()

    # ---- 扫描与同步 ---------------------------------------------------
    def _book_uids(self):
        try:
            return [
                entry.name
                for entry in Path(self.layout.books).iterdir()
                if entry.is_dir() and not entry.name.startswith("_")
            ]
        except OSError:
            return []

    def _scan_book(self, uid):
        """返回 ``{(uid, name): (n, stem, ino, ctime_ns, mtime_ns, size)}``。"""
        md_dir = self.layout.md(uid)
        try:
            entries = list(os.scandir(md_dir))
        except OSError:
            return {}
        numbered = []
        fallback = []
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            match = CHAPTER_RE.match(entry.name)
            if match:
                numbered.append((int(match.group(1)), entry.name, match.group(2), entry))
            elif (
                entry.name.lower().endswith(".md")
                and entry.name.lower() != "readme.md"
                and not entry.name.startswith(".")
            ):
                fallback.append(entry)
        if numbered:
            chosen = numbered
        elif len(fallback) == 1:
            entry = fallback[0]
            chosen = [(1, entry.name, Path(entry.name).stem, entry)]
        else:
            chosen = []
        found = {}
        for n, name, stem, entry in chosen:
            try:
                stat = entry.stat()
            except OSError:
                continue
            found[(uid, name)] = (n, stem, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size)
        return found

    def _cached(self, conn, uids):
        query = "SELECT uid, name, n, stem, ino, ctime_ns, mtime_ns, size FROM chapter"
        params = ()
        if uids is not None:
            placeholders = ",".join("?" for _ in uids) or "''"
            query += f" WHERE uid IN ({placeholders})"
            params = tuple(uids)
        rows = conn.execute(query, params)
        return {(row[0], row[1]): (row[2], row[3], row[4], row[5], row[6], row[7]) for row in rows}

    def _sync(self, conn, uids, full=False):
        if uids is None:
            uids = self._book_uids()
            full = True
        seen = {}
        for uid in uids:
            seen.update(self._scan_book(uid))
        cached = self._cached(conn, None if full else uids)
        changed = [key for key, sig in seen.items() if cached.get(key) != sig]
        removed = [key for key in cached if key not in seen]
        if changed:
            rows = []
            failed = []
            for uid, name in sorted(changed):
                path = Path(self.layout.md(uid)) / name
                try:
                    raw = path.read_bytes().decode("utf-8", "replace")
                except OSError:
                    failed.append((uid, name))
                    continue
                meta = lean_meta(raw)
                sig = seen[(uid, name)]
                rows.append(
                    (
                        uid,
                        name,
                        sig[0],
                        sig[1],
                        meta["title"] or sig[1],
                        meta["chars"],
                        meta["full_chars"],
                        sig[2],
                        sig[3],
                        sig[4],
                        sig[5],
                    )
                )
            conn.executemany(UPSERT_CHAPTER, rows)
            if failed:
                conn.executemany("DELETE FROM chapter WHERE uid = ? AND name = ?", failed)
                removed.extend(failed)
        if removed:
            conn.executemany("DELETE FROM chapter WHERE uid = ? AND name = ?", removed)
        affected = {uid for uid, _name in changed} | {uid for uid, _name in removed}
        for uid in sorted(affected):
            self._refresh_book_row(conn, uid)
        chapters = conn.execute("SELECT COUNT(*) FROM chapter").fetchone()[0]
        books = conn.execute("SELECT COUNT(*) FROM book").fetchone()[0]
        return len(changed), len(removed), books, chapters

    def _refresh_book_row(self, conn, uid):
        rows = conn.execute(
            "SELECT name, n, stem, title, chars, full_chars FROM chapter WHERE uid = ? ORDER BY n, name", (uid,)
        ).fetchall()
        if not rows:
            conn.execute("DELETE FROM book WHERE uid = ?", (uid,))
            return
        chapters = [
            {"file": row[0], "n": row[1], "stem": row[2], "title": row[3], "chars": row[4], "full_chars": row[5]}
            for row in rows
        ]
        count, chars = book_totals(chapters)
        if count:
            conn.execute(
                "INSERT INTO book (uid, chapters, chars) VALUES (?, ?, ?) "
                "ON CONFLICT (uid) DO UPDATE SET chapters = excluded.chapters, chars = excluded.chars",
                (uid, count, chars),
            )
        else:
            conn.execute("DELETE FROM book WHERE uid = ?", (uid,))

    # ---- 运行期单本核对 -----------------------------------------------
    def refresh_book(self, uid):
        """核对一本书的章目录（服务运行中改章用）。返回该书最新统计。"""
        return self._recover(self._refresh_book, uid)

    def _refresh_book(self, uid):
        with self._lock:
            conn = self._connect()
            try:
                self._sync(conn, [uid])
                conn.commit()
                row = conn.execute("SELECT chapters, chars FROM book WHERE uid = ?", (uid,)).fetchone()
                return {"chapters": row[0], "chars": row[1]} if row else {"chapters": 0, "chars": 0}
            finally:
                conn.close()

    def _recover(self, call, *args):
        """索引在运行中被删/损坏时自愈一次，再失败才上抛。"""
        try:
            return call(*args)
        except sqlite3.DatabaseError:
            self.ensure()
            return call(*args)

    # ---- 查询 ---------------------------------------------------------
    def book_stats(self):
        return self._recover(self._book_stats)

    def _book_stats(self):
        conn = self._connect()
        try:
            return {
                uid: {"chapters": count, "chars": chars}
                for uid, count, chars in conn.execute("SELECT uid, chapters, chars FROM book")
            }
        finally:
            conn.close()

    def chapters(self, uid):
        return self._recover(self._chapters, uid)

    def _chapters(self, uid):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name, n, stem, title, chars, full_chars FROM chapter WHERE uid = ? ORDER BY n, name", (uid,)
            ).fetchall()
        finally:
            conn.close()
        return [
            {"file": row[0], "n": row[1], "stem": row[2], "title": row[3], "chars": row[4], "full_chars": row[5]}
            for row in rows
        ]

    def iter_chapters(self):
        """逐本产出 ``(uid, 章行列表)``，内存只占一本书；首行之前坏库会自愈一次。"""

        def run():
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT uid, name, n, stem, title, chars, full_chars FROM chapter ORDER BY uid, n, name"
                )
                current = None
                bucket = []
                for uid, name, n, stem, title, chars, full_chars in cursor:
                    if uid != current:
                        if bucket:
                            yield current, bucket
                        current = uid
                        bucket = []
                    bucket.append(
                        {"file": name, "n": n, "stem": stem, "title": title, "chars": chars, "full_chars": full_chars}
                    )
                if bucket:
                    yield current, bucket
            finally:
                conn.close()

        yielded = False
        try:
            for item in run():
                yielded = True
                yield item
        except sqlite3.DatabaseError:
            if yielded:
                raise
            self.ensure()
            yield from run()

    def stats(self):
        return self._recover(self._stats)

    def _stats(self):
        path = Path(self.path)
        if not path.exists():
            return {"books": 0, "chapters": 0, "meta": {}, "path": self.path, "bytes": 0, "missing": True}
        conn = self._connect()
        try:
            books = conn.execute("SELECT COUNT(*) FROM book").fetchone()[0]
            chapters = conn.execute("SELECT COUNT(*) FROM chapter").fetchone()[0]
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
        return {
            "books": books,
            "chapters": chapters,
            "meta": meta,
            "path": self.path,
            "bytes": path.stat().st_size if path.exists() else 0,
        }
