#!/usr/bin/env python3
"""建 目录/检索/index.db：段落倒排（CJK bigram FTS5）+ 概念剖面。零第三方依赖。

  build_para_index.py --rebuild                 # 全库重建，40-70 min
  build_para_index.py --rebuild --limit-books 20
  build_para_index.py --concepts                # 只补 book_concept 剖面

索引只读 库/。重建用内容表 para 存原文，FTS 用 external content 只存索引。
"""
import argparse, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
from defpass import paragraphs

DB = os.path.join(ROOT, "目录", "检索", "index.db")
CONCEPTS = os.path.join(ROOT, "目录", "概念表.tsv")
CORPUS = os.path.join(ROOT, "库")
SNAP = os.path.join(ROOT, "工作区", "中间态", "index-expand-snapshot.db")
EXPAND_COLS = ("rowid", "path", "kind", "tok", "text", "model", "prompt_hash",
               "tokens_in", "tokens_out", "built_at")
EXPAND_SQL = "(%s) VALUES(%s)" % (",".join(EXPAND_COLS), ",".join("?" * len(EXPAND_COLS)))

SCHEMA = """
CREATE TABLE para(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, book TEXT NOT NULL,
  line INTEGER NOT NULL, chars INTEGER NOT NULL, tok TEXT NOT NULL, text TEXT NOT NULL);
CREATE VIRTUAL TABLE para_fts USING fts5(
  tok, content='para', content_rowid='id', tokenize="unicode61 remove_diacritics 0");
CREATE TABLE book_concept(
  book TEXT NOT NULL, key TEXT NOT NULL, hits INTEGER NOT NULL,
  book_chars INTEGER NOT NULL, score REAL NOT NULL, PRIMARY KEY(book, key)) WITHOUT ROWID;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def chapters(limit_books=0):
    books = []
    for dirpath, dirnames, filenames in os.walk(CORPUS):
        dirnames.sort()
        if os.path.basename(dirpath) != "md":
            continue
        book = os.path.relpath(os.path.dirname(dirpath), CORPUS)
        files = sorted(f for f in filenames if f.endswith(".md") and f != "README.md")
        if files:
            books.append((book, [os.path.join(dirpath, f) for f in files]))
    books.sort()
    return books[:limit_books] if limit_books else books


def read_concepts():
    import csv
    out = []
    with open(CONCEPTS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            aliases = [a for a in r["aliases"].split(",") if a]
            out.append((r["key"], [r["key"]] + aliases))
    return out


def snapshot_expand(db, snap=SNAP):
    """重建会 os.remove 整个 index.db，先把 expand 表另存一份。返回是否有快照。"""
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
    if os.path.exists(snap):
        os.remove(snap)
    import build_expand  # 同一份 DDL，避免两处漂移
    dst = sqlite3.connect(snap)
    dst.executescript(build_expand.SCHEMA)
    cur = src.execute("SELECT %s FROM expand" % ",".join(EXPAND_COLS))
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


def restore_expand(con, snap=SNAP):
    if not os.path.exists(snap):
        return 0
    import build_expand
    con.executescript(build_expand.SCHEMA)
    src = sqlite3.connect("file:%s?mode=ro" % snap, uri=True)
    cur = src.execute("SELECT %s FROM expand" % ",".join(EXPAND_COLS))
    n = 0
    dropped = 0
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        keep = [r for r in rows if os.path.exists(os.path.join(ROOT, r[1]))]
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


def build(db, limit_books=0, keep=False):
    had_expand = False
    if not keep:
        if os.path.exists(db):
            had_expand = snapshot_expand(db)
        elif os.path.exists(SNAP):
            # 上次重建立删库后崩了，快照还在：直接回灌，不丢 LLM 产物
            print("发现孤立 expand 快照，重建后回灌", file=sys.stderr)
            had_expand = True
    os.makedirs(os.path.dirname(db), exist_ok=True)
    building = db if keep else db + ".building"
    if os.path.exists(building):
        os.remove(building)
    con = sqlite3.connect(building)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
                      "PRAGMA temp_store=MEMORY; PRAGMA cache_size=-2000000;" + SCHEMA)
    t0 = time.time()
    books = chapters(limit_books)
    nfiles = sum(len(v) for _, v in books)
    print("书 %d 本 / 章 %d 个" % (len(books), nfiles), file=sys.stderr)
    batch, done, npages = [], 0, 0
    for book, paths in books:
        for path in paths:
            rel = os.path.relpath(path, ROOT)
            rows = [(start, text, cjk.index_text(text))
                    for start, text in paragraphs(path)]
            if not any(tok for _, _, tok in rows):
                # 正文全是假名或符号时没有可索引 token，保留 front matter，章节不从索引里消失
                rows = [(start, text, cjk.index_text(text))
                        for start, text in paragraphs(path, keep_front_matter=True)]
            for start, text, tok in rows:
                if not tok:
                    continue
                batch.append((rel, book, start, len(text), tok, text))
            done += 1
            npages += 1
        if len(batch) >= 4000:
            con.executemany("INSERT INTO para(path,book,line,chars,tok,text) "
                            "VALUES(?,?,?,?,?,?)", batch)
            batch.clear()
        if done % 10000 < len(paths):
            print("  %d/%d 章 %d 段 %.0fs" % (done, nfiles,
                  con.execute("SELECT count(*) FROM para").fetchone()[0], time.time() - t0),
                  file=sys.stderr)
    if batch:
        con.executemany("INSERT INTO para(path,book,line,chars,tok,text) "
                        "VALUES(?,?,?,?,?,?)", batch)
    npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
    nchars = con.execute("SELECT sum(chars) FROM para").fetchone()[0] or 0
    print("建 FTS 索引 %d 段 / %d 字" % (npara, nchars), file=sys.stderr)
    con.execute("INSERT INTO para_fts(para_fts) VALUES('rebuild')")
    con.commit()
    if had_expand:
        # expand 与倒排无关，重建后原样回灌，不丢 LLM 产物
        restore_expand(con)
    con.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
        ("built_at", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        ("chapters", str(nfiles)), ("books", str(len(books))),
        ("paras", str(npara)), ("chars", str(nchars)),
        ("seconds", "%.0f" % (time.time() - t0)),
    ])
    con.commit()
    if building != db:
        os.replace(building, db)   # 建完才换，崩溃留不下半截 index.db
    print("index.db %.2f GB  %d 段  %.0fs" % (
        os.path.getsize(db) / 1e9, npara, time.time() - t0))
    return con


def build_concepts(con):
    con.execute("DELETE FROM book_concept")   # 全量重算，概念表删键后不留孤儿
    t0 = time.time()
    concepts = read_concepts()
    book_chars = dict(con.execute(
        "SELECT book, sum(chars) FROM para GROUP BY book").fetchall())
    per = {}
    for key, aliases in concepts:
        hits = {}
        for alias in aliases:
            expr = cjk.query_expr(alias)
            if not expr:
                continue
            for book, n in con.execute(
                    "SELECT p.book, count(*) FROM para_fts f JOIN para p ON p.id=f.rowid "
                    "WHERE para_fts MATCH ? GROUP BY p.book", (expr,)):
                hits[book] = hits.get(book, 0) + n
        for book, n in hits.items():
            per[(book, key)] = n
    nbooks = len(book_chars)
    rows = []
    for (book, key), n in per.items():
        idf = max(0.0, __import__("math").log(nbooks / max(1, len({b for b, k in per if k == key}))))
        score = n * idf / (1.0 + __import__("math").log1p(book_chars.get(book, 1)))
        rows.append((book, key, n, book_chars.get(book, 0), round(score, 6)))
    con.executemany("INSERT OR REPLACE INTO book_concept VALUES(?,?,?,?,?)", rows)
    con.commit()
    print("book_concept %d 条  %.0fs" % (len(rows), time.time() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--concepts", action="store_true")
    ap.add_argument("--limit-books", type=int, default=0)
    a = ap.parse_args()
    if not (a.rebuild or a.concepts or not os.path.exists(a.db)):
        ap.error("要 --rebuild 或 --concepts")
    if a.rebuild or not os.path.exists(a.db):
        con = build(a.db, a.limit_books)
    else:
        con = sqlite3.connect(a.db)
    if a.concepts:
        build_concepts(con)


if __name__ == "__main__":
    main()
