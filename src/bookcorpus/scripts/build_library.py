#!/usr/bin/env python3
"""L1/L5 索引构建。人账 + 机账 + 磁盘 -> 目录/library.db（可随时重建）。

两种模式，自动选:
  库模式     库/**/book.json 存在时，从 库/ 树重建（L3 之后）
  清单模式   否则读 目录/书目录清单.csv（L3 之前）

用法:
  python3 build_library.py                 # 全量重建
  python3 build_library.py --emit-json     # 顺便给每本书写 book.json
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

DB = L.PATHS.library_db

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE book(
  uid TEXT PRIMARY KEY, title TEXT NOT NULL, title_from_file TEXT,
  author TEXT, translator TEXT, publisher TEXT, language TEXT,
  subject1 TEXT NOT NULL, subject2 TEXT,
  path TEXT NOT NULL, path_state TEXT NOT NULL DEFAULT 'proposed'
    CHECK (path_state IN ('proposed','active')),
  series TEXT, volume TEXT, container TEXT, html_pages INT,
  retention TEXT NOT NULL DEFAULT 'keep'
    CHECK (retention IN ('keep','review','drop')),
  notes TEXT);
CREATE UNIQUE INDEX book_active_path ON book(path) WHERE path_state='active';
CREATE INDEX book_subject ON book(subject1, subject2);
CREATE TABLE source(
  uid TEXT NOT NULL REFERENCES book(uid), file TEXT, format TEXT, bytes INT,
  sha256 TEXT, origin TEXT, batch TEXT, primary_src INT DEFAULT 0);
CREATE INDEX source_sha ON source(sha256);
CREATE TABLE award(name TEXT PRIMARY KEY, books INT DEFAULT 0);
CREATE TABLE book_award(uid TEXT, award TEXT, year TEXT, edition TEXT, result TEXT);
CREATE TABLE derived(
  uid TEXT, kind TEXT CHECK (kind IN ('md','html','ocr')), status TEXT
    CHECK (status IN ('none','partial','done','failed')),
  method TEXT, tool_version TEXT, source_sha256 TEXT, chapters INT, bytes INT,
  generated_at TEXT, manifest TEXT, PRIMARY KEY (uid, kind));
CREATE TABLE job(
  job_id INTEGER PRIMARY KEY, uid TEXT, kind TEXT, status TEXT
    CHECK (status IN ('pending','running','done','failed','skipped')),
  attempt INT DEFAULT 1, actor TEXT, batch TEXT, source_sha256 TEXT, tool TEXT,
  tool_version TEXT, started_at TEXT, finished_at TEXT, error TEXT, output_path TEXT,
  evidence TEXT, UNIQUE (uid, kind, batch, actor, attempt));
CREATE VIEW v_path_conflict AS
  SELECT path, count(*) n, group_concat(uid) uids FROM book GROUP BY path HAVING n > 1;
CREATE VIEW v_pending_md AS
  SELECT b.uid, b.subject1, b.subject2, b.title, s.format, s.sha256
  FROM book b JOIN source s ON s.uid = b.uid AND s.primary_src = 1
  LEFT JOIN derived d ON d.uid = b.uid AND d.kind = 'md'
  WHERE d.uid IS NULL OR (d.status != 'done')
     OR (d.source_sha256 IS NOT NULL AND d.source_sha256 != s.sha256);
CREATE VIEW v_coverage AS
  SELECT subject1, count(*) books,
    sum(CASE WHEN uid IN (SELECT uid FROM derived WHERE kind='md') THEN 1 ELSE 0 END) md,
    sum(CASE WHEN uid IN (SELECT uid FROM derived WHERE kind='html') THEN 1 ELSE 0 END) html
  FROM book GROUP BY subject1;
CREATE VIEW v_award_book AS
  SELECT a.name award, count(*) books FROM award a
  LEFT JOIN book_award ba ON ba.award = a.name GROUP BY a.name ORDER BY books DESC;
CREATE VIEW v_dup_sha AS
  SELECT sha256, count(*) copies, group_concat(uid) uids FROM source
  WHERE sha256 != '' GROUP BY sha256 HAVING copies > 1;
CREATE VIEW v_series AS
  SELECT series, count(*) vols, group_concat(volume) order_no FROM book
  WHERE series != '' GROUP BY series ORDER BY vols DESC;
"""


def fresh_db():
    if os.path.exists(DB):
        for suf in ("", "-wal", "-shm"):
            p = DB + suf
            if os.path.exists(p):
                os.remove(p)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def finalize(con, t0, emit_json):
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    stats = {
        "books": q("SELECT count(*) FROM book")[0][0],
        "sources": q("SELECT count(*) FROM source")[0][0],
        "active": q("SELECT count(*) FROM book WHERE path_state='active'")[0][0],
        "conflicts": q("SELECT count(*) FROM v_path_conflict")[0][0],
        "md_done": q("SELECT count(*) FROM derived WHERE kind='md' AND status='done'")[0][0],
        "pending_md": q("SELECT count(*) FROM v_pending_md")[0][0],
        "award_books": q("SELECT count(*) FROM book_award")[0][0],
        "dup_sha": q("SELECT count(*) FROM v_dup_sha")[0][0],
        "build_sec": round(time.time() - t0, 3),
        "db_bytes": 0,
    }
    con.commit()
    if emit_json:
        stats["book_json"] = emit_book_json(con)
    con.close()
    stats["db_bytes"] = os.path.getsize(DB)   # close 之后才 checkpoint 完
    return stats


def build(emit_json=False):
    books = L.iter_book_json()
    if not books:
        raise SystemExit("books/ 下一本 book.json 都没有，不建库。先跑 book-corpus doctor 看根对不对。")
    return build_from_library(books, emit_json)


# ---------------------------------------------------------------- 库模式
def award_roster():
    """奖项名单：迁移后看 工作区/批次/奖项下载-2026-10-18/，迁移前看 获奖名单与进度/。"""
    for p in (os.path.join(L.WORK, "批次", "奖项下载-2026-10-18", "下载结果总表.csv"),
              os.path.join(L.ROOT, "获奖名单与进度", "下载结果总表.csv")):
        rows = L.read_csv(p)
        if rows:
            return sorted({r.get("奖项", "") for r in rows if r.get("奖项")})
    return []


def build_from_library(books, emit_json=False):
    ledger = {r["uid"]: r for r in L.read_csv(L.PATHS.ledger)}
    if not ledger:
        raise SystemExit("缺少 tables/分类表.csv，建不起来")
    t0 = time.time()
    con = fresh_db()
    awards_books = defaultdict(set)
    seen_uids = set()
    for uid, doc in books:
        seen_uids.add(uid)
        led = ledger.get(uid)
        if not led:
            raise SystemExit("books/ 里有书不在人账: %s" % uid)
        bdir = os.path.join(L.BOOKS, uid)
        if not os.path.isdir(bdir):
            raise SystemExit("目录不存在: " + bdir)
        md_dir, html_dir = os.path.join(bdir, "md"), os.path.join(bdir, "html")
        sources = [s for s in (doc.get("sources") or []) if s.get("file")]
        html_pages = (len([f for f in os.listdir(html_dir)
                           if f.endswith(".html") and f != "index.html"])
                      if os.path.isdir(html_dir) else 0)
        awards = [a for a in (doc.get("awards") or []) if a.get("award")]
        con.execute("""INSERT INTO book(uid,title,title_from_file,author,subject1,subject2,path,
                       path_state,series,volume,container,html_pages,retention,notes)
                       VALUES(?,?,?,?,?,?,?,'active',?,?,?,?,?,?)""",
                    (uid, led["正式书名"], doc.get("title_from_file", ""), doc.get("author", ""),
                     led["一级"], led["二级"], uid, doc.get("series") or "",
                     doc.get("volume") or "",
                     doc.get("container") or "", html_pages, led["保留"] or "keep", led["备注"]))
        for i, s in enumerate(sources):
            owner = s.get("collection") or uid
            con.execute("""INSERT INTO source(uid,file,format,bytes,sha256,origin,batch,primary_src)
                           VALUES(?,?,?,?,?,'zlib',?,?)""",
                        (uid, L.source_rel(owner, os.path.basename(s["file"])),
                         s.get("format", ""), int(s.get("bytes") or 0), s.get("sha256", ""),
                         "导入", 1 if i == 0 else 0))
        for a in awards:
            con.execute("INSERT INTO book_award(uid,award,year,edition,result) VALUES(?,?,?,?,?)",
                        (uid, a["award"], a.get("year", ""), a.get("edition", ""), a.get("result", "")))
            awards_books[a["award"]].add(uid)
        if os.path.isdir(md_dir):
            man = L.read_md_manifest(md_dir)
            chapters, bytes_ = L.count_md_dir(md_dir)
            method = man.get("method") or "ebook-to-md"
            primary = man.get("source_sha256") or (sources[0].get("sha256", "") if sources else "")
            manifest = (L.store_path(L.md_rel(uid), ".manifest.json")
                        if os.path.isfile(os.path.join(md_dir, ".manifest.json")) else "")
            con.execute("""INSERT OR REPLACE INTO derived(uid,kind,status,method,tool_version,
                           source_sha256,chapters,bytes,generated_at,manifest)
                           VALUES(?,'md','done',?,?,?,?,?,?,?)""",
                        (uid, method, str(man.get("tool_version") or ""), primary, chapters, bytes_,
                         str(man.get("generated_at") or ""), manifest))
            con.execute("INSERT INTO job(uid,kind,status,actor,batch,tool,source_sha256,evidence)"
                        " VALUES(?,'md','done',?,'导入',?,?,?)",
                        (uid, method, method, primary,
                         manifest or L.md_rel(uid)))
        if os.path.isdir(html_dir):
            con.execute("""INSERT OR REPLACE INTO derived(uid,kind,status,method,chapters,bytes)
                           VALUES(?,'html','done','chapter-html-versions',?,0)""",
                        (uid, html_pages))
    missing = sorted(set(ledger) - seen_uids)
    if missing:
        raise SystemExit("人账有 %d 本在库里找不到 book.json，先查清楚再建库: %s"
                         % (len(missing), " ".join(missing[:10])))
    for name in sorted(set(award_roster()) | set(awards_books)):
        con.execute("INSERT OR REPLACE INTO award(name,books) VALUES(?,?)",
                    (name, len(awards_books.get(name, ()))))
    con.commit()
    return finalize(con, t0, emit_json)


def emit_book_json(con):
    """给已落地的书写 book.json。book.json 是投影，唯一写入者就是这个函数。"""
    con.row_factory = sqlite3.Row
    written = 0
    for b in con.execute("SELECT * FROM book WHERE path_state='active'"):
        dest = os.path.join(L.BOOKS, b["uid"])
        if not os.path.isdir(dest):
            continue
        srcs = []
        for s in con.execute(
                "SELECT file,format,bytes,sha256,primary_src FROM source WHERE uid=?", (b["uid"],)):
            parts = (s["file"] or "").split("/")
            shared = len(parts) == 4 and parts[0] == "collections"
            entry = {"format": s["format"], "bytes": s["bytes"],
                     "sha256": s["sha256"], "primary_src": s["primary_src"]}
            if shared:
                entry["collection"] = parts[1]
                entry["file"] = parts[3]
            else:
                entry["file"] = "source/" + parts[-1]
            srcs.append(entry)
        awards = [dict(a) for a in con.execute(
            "SELECT award,year,edition,result FROM book_award WHERE uid=?", (b["uid"],))]
        doc = L.book_doc(b["uid"], b["title"], b["title_from_file"], b["author"],
                         [b["subject1"], b["subject2"]], b["series"], b["volume"],
                         b["container"], b["retention"], b["notes"], srcs, awards)
        L.write_json_atomic(os.path.join(dest, "book.json"), doc)
        written += 1
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-json", action="store_true")
    args = ap.parse_args()
    st = build(emit_json=args.emit_json)
    print(json.dumps(st, ensure_ascii=False, indent=1))
