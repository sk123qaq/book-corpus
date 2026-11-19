#!/usr/bin/env python3
"""建 build/检索/index.db：段落倒排（CJK bigram FTS5）。零第三方依赖。

  build_para_index.py --sync                    # 按书签名增量同步（秒级）
  build_para_index.py --sync --books B0001,B0002
  build_para_index.py --delete B0001            # 单书增量清理（退库用）
  build_para_index.py --dry-run --json          # 只读计算增量差集
  build_para_index.py --rebuild                 # 全库重建，40-70 min（碎片整理/灾备）
  build_para_index.py --rebuild --limit-books 20

索引只读 books/<uid>/md/（跳过 _收件箱 / _冷宫）。
内容表 para 存原文，FTS 用 external content 存倒排，indexed_book 存每本书签名用于增量同步。
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import PATHS, ROOT, Layout

sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
from defpass import paragraphs

DB = PATHS.index_db
CORPUS = PATHS.books
SNAP = os.path.join(ROOT, "工作区", "中间态", "index-expand-snapshot.db")
os.makedirs(os.path.dirname(SNAP), exist_ok=True)
EXPAND_COLS = ("rowid", "path", "kind", "tok", "text", "model", "prompt_hash", "tokens_in", "tokens_out", "built_at")
EXPAND_SQL = "(%s) VALUES(%s)" % (",".join(EXPAND_COLS), ",".join("?" * len(EXPAND_COLS)))

INDEXED_BOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_book(
  book TEXT PRIMARY KEY,
  sig TEXT NOT NULL,
  chapters INTEGER NOT NULL,
  paras INTEGER NOT NULL,
  chars INTEGER NOT NULL,
  indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS para_book ON para(book);
"""

SCHEMA = (
    """
CREATE TABLE para(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, book TEXT NOT NULL,
  line INTEGER NOT NULL, chars INTEGER NOT NULL, tok TEXT NOT NULL, text TEXT NOT NULL);
CREATE VIRTUAL TABLE para_fts USING fts5(
  tok, content='para', content_rowid='id', tokenize="unicode61 remove_diacritics 0");
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""
    + INDEXED_BOOK_SCHEMA
)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sig_of(paths):
    """与 embed_corpus.sig_of 同口径：章文件名 + 大小 + mtime_ns 的 SHA-256 前 16 位。"""
    h = hashlib.sha256()
    for p in paths:
        st = os.stat(p)
        h.update(f"{os.path.basename(p)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def chapters(limit_books=0, corpus=None):
    base = corpus or CORPUS
    books = []
    if not os.path.isdir(base):
        return books
    for dirpath, dirnames, filenames in os.walk(base):
        if dirpath == base:
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("_"))
        else:
            dirnames.sort()
        if os.path.basename(dirpath) != "md":
            continue
        book = os.path.relpath(os.path.dirname(dirpath), base)
        files = sorted(f for f in filenames if f.endswith(".md") and f != "README.md")
        if files:
            books.append((book, [os.path.join(dirpath, f) for f in files]))
    books.sort()
    return books[:limit_books] if limit_books else books


def _extract_book_rows(book, paths, root=None):
    base_root = root or ROOT
    out = []
    indexed_paths = set()
    total_chars = 0
    for path in paths:
        rel = os.path.relpath(path, base_root)
        rows = [(start, text, cjk.index_text(text)) for start, text in paragraphs(path)]
        if not any(tok for _, _, tok in rows):
            # 正文全是假名或符号时没有可索引 token，保留 front matter，章节不从索引里消失
            rows = [(start, text, cjk.index_text(text)) for start, text in paragraphs(path, keep_front_matter=True)]
        for start, text, tok in rows:
            if not tok:
                continue
            out.append((rel, book, start, len(text), tok, text))
            indexed_paths.add(rel)
            total_chars += len(text)
    return out, len(indexed_paths), total_chars


def snapshot_expand(db, snap=SNAP):
    """重建会替换整个 index.db，先把 expand 表另存一份。返回是否有快照。"""
    if not os.path.exists(db):
        return False
    src = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        n = src.execute("SELECT count(*) FROM expand").fetchone()[0]
    except sqlite3.OperationalError:
        src.close()
        return False
    if not n:
        src.close()
        return False
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    if os.path.exists(snap):
        os.remove(snap)
    import build_expand  # 同一份 DDL，避免两处漂移

    dst = sqlite3.connect(snap)
    dst.executescript(build_expand.SCHEMA)
    cur = src.execute("SELECT %s FROM expand" % ",".join(EXPAND_COLS))  # noqa: S608 列名来自模块常量 EXPAND_COLS
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        dst.executemany("INSERT INTO expand" + EXPAND_SQL, rows)
    dst.commit()
    src.close()
    dst.close()
    print("expand 快照 %d 行 -> %s" % (n, snap), file=sys.stderr)
    return True


def restore_expand(con, snap=SNAP, root=None):
    if not os.path.exists(snap):
        return 0
    base_root = root or ROOT
    import build_expand

    con.executescript(build_expand.SCHEMA)
    src = sqlite3.connect("file:%s?mode=ro" % snap, uri=True)
    cur = src.execute("SELECT %s FROM expand" % ",".join(EXPAND_COLS))  # noqa: S608 列名来自模块常量 EXPAND_COLS
    n = 0
    dropped = 0
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        keep = [r for r in rows if os.path.exists(os.path.join(base_root, r[1]))]
        dropped += len(rows) - len(keep)
        if keep:
            con.executemany("INSERT INTO expand" + EXPAND_SQL, keep)
            n += len(keep)
    src.close()
    con.commit()
    con.execute("INSERT INTO expand_fts(expand_fts) VALUES('rebuild')")
    con.commit()
    os.remove(snap)
    print("expand 回灌 %d 行（丢弃已不存在的 %d 行）" % (n, dropped), file=sys.stderr)
    return n


def build(db, limit_books=0, keep=False, root=None):
    base_root = os.path.abspath(root) if root else ROOT
    corpus = os.path.join(base_root, "books")
    snap = os.path.join(base_root, "工作区", "中间态", "index-expand-snapshot.db")
    had_expand = False
    if not keep:
        if os.path.exists(db):
            had_expand = snapshot_expand(db, snap=snap)
        elif os.path.exists(snap):
            # 上次重建删库后崩了，快照还在：直接回灌，不丢 LLM 产物
            print("发现孤立 expand 快照，重建后回灌", file=sys.stderr)
            had_expand = True
    os.makedirs(os.path.dirname(db), exist_ok=True)
    building = db if keep else db + ".building"
    if os.path.exists(building):
        os.remove(building)
    con = sqlite3.connect(building)
    con.executescript(
        "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
        "PRAGMA temp_store=MEMORY; PRAGMA cache_size=-2000000;" + SCHEMA
    )
    # expand 表即使没有扩展行也要建，否则新库上 book_score/book_query 会查不到表
    import build_expand

    con.executescript(build_expand.SCHEMA)
    t0 = time.time()
    books = chapters(limit_books, corpus=corpus)
    nfiles = sum(len(v) for _, v in books)
    print("书 %d 本 / 章 %d 个" % (len(books), nfiles), file=sys.stderr)
    batch, tracked, done = [], [], 0
    built_at = _now_iso()
    for book, paths in books:
        sig = sig_of(paths)
        rows, ch_cnt, chars_cnt = _extract_book_rows(book, paths, root=base_root)
        batch.extend(rows)
        tracked.append((book, sig, ch_cnt, len(rows), chars_cnt, built_at))
        done += len(paths)
        if len(batch) >= 4000:
            con.executemany("INSERT INTO para(path,book,line,chars,tok,text) VALUES(?,?,?,?,?,?)", batch)
            batch.clear()
        if done % 10000 < len(paths):
            print(
                "  %d/%d 章 %d 段 %.0fs"
                % (done, nfiles, con.execute("SELECT count(*) FROM para").fetchone()[0], time.time() - t0),
                file=sys.stderr,
            )
    if batch:
        con.executemany("INSERT INTO para(path,book,line,chars,tok,text) VALUES(?,?,?,?,?,?)", batch)
    if tracked:
        con.executemany(
            "INSERT OR REPLACE INTO indexed_book(book,sig,chapters,paras,chars,indexed_at) VALUES(?,?,?,?,?,?)",
            tracked,
        )
    npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
    nchap = con.execute("SELECT count(DISTINCT path) FROM para").fetchone()[0]
    nbooks = con.execute("SELECT count(DISTINCT book) FROM para").fetchone()[0]
    nchars = con.execute("SELECT coalesce(sum(chars), 0) FROM para").fetchone()[0]
    print("建 FTS 索引 %d 段 / %d 字" % (npara, nchars), file=sys.stderr)
    con.execute("INSERT INTO para_fts(para_fts) VALUES('rebuild')")
    con.commit()
    if had_expand:
        # expand 与倒排无关，重建后原样回灌，不丢 LLM 产物
        restore_expand(con, snap=snap, root=base_root)
    con.executemany(
        "INSERT OR REPLACE INTO meta VALUES(?,?)",
        [
            ("built_at", _now_iso()),
            ("chapters", str(nchap)),
            ("books", str(nbooks)),
            ("paras", str(npara)),
            ("chars", str(nchars)),
            ("seconds", "%.0f" % (time.time() - t0)),
        ],
    )
    con.commit()
    if building != db:
        con.close()
        os.replace(building, db)  # 建完才换，崩溃留不下半截 index.db
        con = sqlite3.connect(db)
    print("index.db %.2f GB  %d 段  %.0fs" % (os.path.getsize(db) / 1e9, npara, time.time() - t0))
    return con


def _table_names(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def diff_index(db, only_books=None, root=None):
    """只读计算磁盘 md 与 index.db 的书级差集：added / modified / removed。"""
    base_root = os.path.abspath(root) if root else ROOT
    corpus = os.path.join(base_root, "books")
    disk_map = dict(chapters(0, corpus=corpus))
    want = set(only_books) if only_books else None

    if not os.path.isfile(db):
        added = sorted(b for b in disk_map if want is None or b in want)
        return {
            "has_db": False,
            "has_tracking": False,
            "added": added,
            "modified": [],
            "removed": [],
            "unchanged": 0,
            "disk_books": len(disk_map),
        }

    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        tables = _table_names(con)
        has_tracking = "indexed_book" in tables
        tracked = {}
        if has_tracking:
            tracked = {
                r[0]: (r[1], r[2]) for r in con.execute("SELECT book, sig, chapters FROM indexed_book").fetchall()
            }
        if not tracked and "para" in tables:
            # 老库还没有 indexed_book 表：按 para 聚合基线推算差集，避免强迫 60 分钟全量重建
            has_tracking = False
            para_stats = {
                r[0]: r[1] for r in con.execute("SELECT book, count(DISTINCT path) FROM para GROUP BY book").fetchall()
            }
            added, modified, removed = [], [], []
            unchanged = 0
            for book, paths in sorted(disk_map.items()):
                if want is not None and book not in want:
                    continue
                if book not in para_stats:
                    added.append(book)
                elif para_stats[book] != len(paths):
                    modified.append(book)
                else:
                    unchanged += 1
            for book in sorted(para_stats):
                if want is not None and book not in want:
                    continue
                if book not in disk_map:
                    removed.append(book)
            return {
                "has_db": True,
                "has_tracking": False,
                "added": added,
                "modified": modified,
                "removed": removed,
                "unchanged": unchanged,
                "disk_books": len(disk_map),
            }
    finally:
        con.close()

    added, modified, removed = [], [], []
    unchanged = 0
    for book, paths in sorted(disk_map.items()):
        if want is not None and book not in want:
            continue
        cur_sig = sig_of(paths)
        if book not in tracked:
            added.append(book)
        elif tracked[book][0] != cur_sig:
            modified.append(book)
        else:
            unchanged += 1
    for book in sorted(tracked):
        if want is not None and book not in want:
            continue
        if book not in disk_map:
            removed.append(book)
    return {
        "has_db": True,
        "has_tracking": has_tracking,
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
        "disk_books": len(disk_map),
    }


def _bootstrap_indexed_book(con, disk_map):
    """老库首次 --sync：从现有 para 回填 indexed_book 基线，一致的书免重扫。"""
    if con.execute("SELECT count(*) FROM indexed_book").fetchone()[0] > 0:
        return 0
    rows = con.execute(
        "SELECT book, count(DISTINCT path), count(*), coalesce(sum(chars), 0) FROM para GROUP BY book"
    ).fetchall()
    if not rows:
        return 0
    now = _now_iso()
    to_insert = []
    for book, ch_cnt, p_cnt, c_cnt in rows:
        paths = disk_map.get(book)
        if paths is None:
            sig = "__orphan__"
        elif len(paths) == ch_cnt:
            sig = sig_of(paths)
        else:
            sig = "__stale__"
        to_insert.append((book, sig, ch_cnt, p_cnt, c_cnt, now))
    con.executemany(
        "INSERT OR REPLACE INTO indexed_book(book,sig,chapters,paras,chars,indexed_at) VALUES(?,?,?,?,?,?)",
        to_insert,
    )
    return len(to_insert)


def _delete_book_in_tx(con, book, include_expand=False):
    tables = _table_names(con)
    deleted_para = 0
    if "para" in tables:
        if "para_fts" in tables:
            con.execute(
                "INSERT INTO para_fts(para_fts, rowid, tok) SELECT 'delete', id, tok FROM para WHERE book=?",
                (book,),
            )
        deleted_para = con.execute("DELETE FROM para WHERE book=?", (book,)).rowcount
    if "indexed_book" in tables:
        con.execute("DELETE FROM indexed_book WHERE book=?", (book,))
    deleted_expand = 0
    if include_expand and "expand" in tables:
        prefix = "books/" + book + "/%"
        if "expand_fts" in tables:
            con.execute(
                "INSERT INTO expand_fts(expand_fts, rowid, tok) "
                "SELECT 'delete', rowid, tok FROM expand WHERE path LIKE ?",
                (prefix,),
            )
        deleted_expand = con.execute("DELETE FROM expand WHERE path LIKE ?", (prefix,)).rowcount
    return deleted_para, deleted_expand


def _upsert_book_in_tx(con, book, paths, root):
    _delete_book_in_tx(con, book, include_expand=False)
    sig = sig_of(paths)
    rows, ch_cnt, chars_cnt = _extract_book_rows(book, paths, root=root)
    if rows:
        con.executemany("INSERT INTO para(path,book,line,chars,tok,text) VALUES(?,?,?,?,?,?)", rows)
        con.execute("INSERT INTO para_fts(rowid, tok) SELECT id, tok FROM para WHERE book=?", (book,))
    con.execute(
        "INSERT OR REPLACE INTO indexed_book(book,sig,chapters,paras,chars,indexed_at) VALUES(?,?,?,?,?,?)",
        (book, sig, ch_cnt, len(rows), chars_cnt, _now_iso()),
    )
    return len(rows), ch_cnt


def _refresh_meta_from_tracking(con, elapsed):
    tables = _table_names(con)
    if "indexed_book" in tables and con.execute("SELECT count(*) FROM indexed_book").fetchone()[0] > 0:
        row = con.execute(
            "SELECT count(*), coalesce(sum(chapters), 0), coalesce(sum(paras), 0), coalesce(sum(chars), 0) "
            "FROM indexed_book WHERE paras > 0"
        ).fetchone()
        nbooks, nchap, npara, nchars = row
    elif "para" in tables:
        nbooks = con.execute("SELECT count(DISTINCT book) FROM para").fetchone()[0]
        nchap = con.execute("SELECT count(DISTINCT path) FROM para").fetchone()[0]
        npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
        nchars = con.execute("SELECT coalesce(sum(chars), 0) FROM para").fetchone()[0]
    else:
        nbooks = nchap = npara = nchars = 0
    con.executemany(
        "INSERT OR REPLACE INTO meta VALUES(?,?)",
        [
            ("built_at", _now_iso()),
            ("chapters", str(nchap)),
            ("books", str(nbooks)),
            ("paras", str(npara)),
            ("chars", str(nchars)),
            ("seconds", "%.1f" % elapsed),
        ],
    )
    return {"books": nbooks, "chapters": nchap, "paras": npara, "chars": nchars}


def sync(db, only_books=None, root=None, emit_json=False):
    """增量同步 index.db：只删除退库/改源书的旧段，只写入新增/改源书的新段。"""
    base_root = os.path.abspath(root) if root else ROOT
    corpus = os.path.join(base_root, "books")
    if not os.path.isfile(db):
        con = build(db, root=base_root)
        con.close()
        disk_books = len(chapters(0, corpus=corpus))
        return {"mode": "initial_build", "added": disk_books, "modified": 0, "removed": 0, "unchanged": 0}

    t0 = time.time()
    disk_map = dict(chapters(0, corpus=corpus))
    want = set(only_books) if only_books else None
    con = sqlite3.connect(db, timeout=30)
    try:
        con.executescript(INDEXED_BOOK_SCHEMA)
        import build_expand

        con.executescript(build_expand.SCHEMA)
        bootstrapped = _bootstrap_indexed_book(con, disk_map)
        if bootstrapped:
            con.commit()
            print("老库基线回填 indexed_book %d 本" % bootstrapped, file=sys.stderr)

        tracked = {r[0]: r[1] for r in con.execute("SELECT book, sig FROM indexed_book").fetchall()}
        added, modified, removed = [], [], []
        unchanged = 0
        for book, paths in sorted(disk_map.items()):
            if want is not None and book not in want:
                continue
            cur_sig = sig_of(paths)
            if book not in tracked:
                added.append(book)
            elif tracked[book] != cur_sig:
                modified.append(book)
            else:
                unchanged += 1
        for book in sorted(tracked):
            if want is not None and book not in want:
                continue
            if book not in disk_map:
                removed.append(book)

        if not (added or modified or removed):
            if bootstrapped:
                _refresh_meta_from_tracking(con, time.time() - t0)
                con.commit()
            if not emit_json:
                print("index.db 已最新（不变 %d 本，%.2fs）" % (unchanged, time.time() - t0))
            return {"mode": "sync", "added": 0, "modified": 0, "removed": 0, "unchanged": unchanged}

        with con:
            for book in removed:
                _delete_book_in_tx(con, book, include_expand=True)
            for book in added + modified:
                _upsert_book_in_tx(con, book, disk_map[book], root=base_root)
            stats = _refresh_meta_from_tracking(con, time.time() - t0)
        elapsed = time.time() - t0
        if not emit_json:
            print(
                "index.db 增量同步完成：+%d ~%d -%d（不变 %d），共 %d 本 / %d 章 / %d 段，%.2fs"
                % (
                    len(added),
                    len(modified),
                    len(removed),
                    unchanged,
                    stats["books"],
                    stats["chapters"],
                    stats["paras"],
                    elapsed,
                )
            )
        return {
            "mode": "sync",
            "added": len(added),
            "modified": len(modified),
            "removed": len(removed),
            "unchanged": unchanged,
            **stats,
            "seconds": round(elapsed, 2),
        }
    finally:
        con.close()


def delete_books(db, books, include_expand=True, root=None):
    """从 index.db 增量移除指定书（供退库脚本与 --delete 调用），毫秒级不重建全库。"""
    if not os.path.isfile(db) or not books:
        return {"deleted_books": 0, "deleted_paras": 0, "deleted_expand": 0}
    t0 = time.time()
    base_root = os.path.abspath(root) if root else ROOT
    corpus = os.path.join(base_root, "books")
    con = sqlite3.connect(db, timeout=30)
    try:
        con.executescript(INDEXED_BOOK_SCHEMA)
        _bootstrap_indexed_book(con, dict(chapters(0, corpus=corpus)))
        total_p = total_e = 0
        with con:
            for book in books:
                dp, de = _delete_book_in_tx(con, book, include_expand=include_expand)
                total_p += dp
                total_e += de
            _refresh_meta_from_tracking(con, time.time() - t0)
        return {"deleted_books": len(books), "deleted_paras": total_p, "deleted_expand": total_e}
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="构建或增量同步段落倒排索引 index.db")
    ap.add_argument("--root", help="工作区根，默认取 BOOKS_ROOT")
    ap.add_argument("--db", help="index.db 路径，默认随 --root 推导")
    ap.add_argument("--rebuild", action="store_true", help="全库重建（40-70 min）")
    ap.add_argument("--sync", action="store_true", help="按书签名增量同步（秒级）")
    ap.add_argument("--books", help="逗号分隔的书号（如 B0001,B0002），配合 --sync 使用")
    ap.add_argument("--delete", help="逗号分隔的书号，增量清理出索引")
    ap.add_argument("--dry-run", action="store_true", help="只读查看待增量同步差集")
    ap.add_argument("--json", action="store_true", help="JSON 格式输出（配合 --dry-run / --sync / --delete）")
    ap.add_argument("--limit-books", type=int, default=0)
    a = ap.parse_args()

    base_root = os.path.abspath(a.root) if a.root else ROOT
    db = a.db or Layout(base_root).index_db
    only = [x.strip() for x in (a.books or "").split(",") if x.strip()] or None

    if a.dry_run:
        delta = diff_index(db, only_books=only, root=base_root)
        if a.json:
            print(json.dumps(delta, ensure_ascii=False))
        else:
            print(
                "index.db 差集：新增 %d / 修改 %d / 删除 %d / 不变 %d"
                % (len(delta["added"]), len(delta["modified"]), len(delta["removed"]), delta["unchanged"])
            )
        return 0
    if a.delete:
        targets = [x.strip() for x in a.delete.split(",") if x.strip()]
        res = delete_books(db, targets, include_expand=True, root=base_root)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(
                "已清理 %d 本（para %d 段，expand %d 行）"
                % (res["deleted_books"], res["deleted_paras"], res["deleted_expand"])
            )
        return 0
    if a.sync:
        res = sync(db, only_books=only, root=base_root, emit_json=a.json)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        return 0
    if not (a.rebuild or not os.path.exists(db)):
        ap.error("请指定 --sync（增量同步）、--rebuild（全库重建）、--delete 或 --dry-run")
    con = build(db, a.limit_books, root=base_root)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
