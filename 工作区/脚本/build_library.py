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

DB = os.path.join(L.CAT, "library.db")

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
    if books:
        return build_from_library(books, emit_json)
    return build_from_inventory(emit_json)


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
    ledger = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "分类表.csv"))}
    if not ledger:
        raise SystemExit("缺少 目录/分类表.csv，库模式建不起来")
    t0 = time.time()
    con = fresh_db()
    awards_books = defaultdict(set)
    seen_uids = set()
    for rel, doc in books:
        uid = doc.get("uid") or ""
        seen_uids.add(uid)
        led = ledger.get(uid)
        if not led:
            raise SystemExit("库里有书不在人账: %s (%s)" % (uid, rel))
        if doc.get("path") and doc["path"] != rel:
            raise SystemExit("book.json 的 path 与目录不符: %s vs %s (%s)" % (doc["path"], rel, uid))
        bdir = os.path.join(L.LIB, rel)
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
                     led["一级"], led["二级"], rel, doc.get("series") or "", doc.get("volume") or "",
                     doc.get("container") or "", html_pages, led["保留"] or "keep", led["备注"]))
        for i, s in enumerate(sources):
            con.execute("""INSERT INTO source(uid,file,format,bytes,sha256,origin,batch,primary_src)
                           VALUES(?,?,?,?,?,'zlib',?,?)""",
                        (uid, "库/" + os.path.normpath(os.path.join(rel, s["file"])),
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
            manifest = ("库/" + os.path.join(rel, "md", ".manifest.json")
                        if os.path.isfile(os.path.join(md_dir, ".manifest.json")) else "")
            con.execute("""INSERT OR REPLACE INTO derived(uid,kind,status,method,tool_version,
                           source_sha256,chapters,bytes,generated_at,manifest)
                           VALUES(?,'md','done',?,?,?,?,?,?,?)""",
                        (uid, method, str(man.get("tool_version") or ""), primary, chapters, bytes_,
                         str(man.get("generated_at") or ""), manifest))
            con.execute("INSERT INTO job(uid,kind,status,actor,batch,tool,source_sha256,evidence)"
                        " VALUES(?,'md','done',?,'导入',?,?,?)",
                        (uid, method, method, primary,
                         manifest or ("库/" + os.path.join(rel, "md"))))
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


# ---------------------------------------------------------------- 清单模式（L3 之前）
def build_from_inventory(emit_json=False):
    ledger = L.read_csv(os.path.join(L.CAT, "分类表.csv"))
    inv = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))}
    awards_csv = L.read_csv(os.path.join(L.WORK, "批次", "奖项下载-2026-10-18", "下载结果总表.csv"))
    if not awards_csv:
        awards_csv = L.read_csv(os.path.join(L.ROOT, "获奖名单与进度", "下载结果总表.csv"))
    award_rows = defaultdict(list)
    for r in awards_csv:
        if r.get("状态", "").startswith("✅"):
            award_rows[r.get("奖项", "")].append(r)

    t0 = time.time()
    con = fresh_db()

    n_src = 0
    for r in ledger:
        uid = r["uid"]
        m = inv.get(uid, {})
        path = L.book_relpath(r, m)
        state = "active" if os.path.isdir(os.path.join(L.LIB, path)) else "proposed"
        con.execute("""INSERT INTO book(uid,title,title_from_file,author,subject1,subject2,path,
                       path_state,series,volume,container,html_pages,retention,notes)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid, r["正式书名"],
                     m.get("title_clean") or L.clean_title(
                         os.path.basename(str(m.get("src_path") or ""))),
                     m.get("作者", ""),
                     r["一级"], r["二级"], path, state, m.get("系列", ""), m.get("卷序", ""),
                     m.get("容器", ""), int(re.sub(r"\D", "", str(m.get("html_book") or "")) or 0),
                     r["保留"] or "keep", r["备注"]))
        srcs = [m.get("src_path", "")]
        extra = (m.get("额外源") or "").strip()
        if extra:
            # 机器字段，JSON 数组。文件名里可能含「；」，不能用它当分隔符。
            parsed = json.loads(extra)
            if not isinstance(parsed, list):
                raise SystemExit("额外源 不是 JSON 数组: %r" % extra[:80])
            srcs += [str(x) for x in parsed]
        for i, sp in enumerate([s for s in srcs if s]):
            con.execute("""INSERT INTO source(uid,file,format,bytes,sha256,origin,batch,primary_src)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (uid, sp, m.get("format", ""), int(m.get("bytes") or 0),
                         m.get("sha256", ""), "zlib", m.get("award", ""), 1 if i == 0 else 0))
            n_src += 1
        for aw in [a for a in (m.get("备注", "") or "").split("；") if a.startswith("奖项:")]:
            name = aw.split(":", 1)[1]
            rec = next((x for x in award_rows.get(name, []) if x.get("下载书名", "") ==
                        os.path.basename(m.get("src_path", ""))), None)
            con.execute("INSERT INTO book_award(uid,award,year,edition,result) VALUES(?,?,?,?,?)",
                        (uid, name, (rec or {}).get("年份", ""), (rec or {}).get("届数", ""),
                         (rec or {}).get("状态", "已在盘")))
        if m.get("md_dir"):
            con.execute("""INSERT OR REPLACE INTO derived(uid,kind,status,method,tool_version,
                           source_sha256,chapters,bytes,generated_at,manifest)
                           VALUES(?,'md','done','ebook-to-md','',?,?,?,?,'')""",
                        (uid, m.get("sha256", ""), int(m.get("md_chapters") or 0),
                         int(m.get("md_bytes") or 0), ""))
            con.execute("INSERT INTO job(uid,kind,status,actor,batch,tool,source_sha256,evidence)"
                        " VALUES(?,'md','done','ebook-to-md','导入','ebook-to-md',?,?)",
                        (uid, m.get("sha256", ""), "书md/" + m["md_dir"]))
        if m.get("html_book"):
            con.execute("""INSERT OR REPLACE INTO derived(uid,kind,status,method,chapters,bytes)
                           VALUES(?,'html','done','chapter-html-versions',?,0)""",
                        (uid, int(re.sub(r"\D", "", str(m["html_book"])) or 0)))
    for name in sorted({a for a in award_rows}):
        con.execute("INSERT OR REPLACE INTO award(name,books) VALUES(?,?)",
                    (name, len({r.get("下载书名", "") for r in award_rows[name]})))
    con.commit()
    return finalize(con, t0, emit_json)


def emit_book_json(con):
    """给已落地的书写 book.json。book.json 是投影，唯一写入者就是这个函数。"""
    con.row_factory = sqlite3.Row
    written = 0
    for b in con.execute("SELECT * FROM book WHERE path_state='active'"):
        dest = os.path.join(L.LIB, b["path"])
        if not os.path.isdir(dest):
            continue
        srcs = []
        for s in con.execute(
                "SELECT file,format,bytes,sha256,primary_src FROM source WHERE uid=?", (b["uid"],)):
            f = s["file"] or ""
            if f.startswith("库/"):
                # 共用源（合集）在容器目录里，书内相对路径可能是 ../source/x
                f = os.path.relpath(f[len("库/"):], b["path"])
            srcs.append({"file": f, "format": s["format"], "bytes": s["bytes"],
                         "sha256": s["sha256"], "primary_src": s["primary_src"]})
        awards = [dict(a) for a in con.execute(
            "SELECT award,year,edition,result FROM book_award WHERE uid=?", (b["uid"],))]
        derived = [dict(d) for d in con.execute(
            "SELECT kind,status,method,chapters,bytes FROM derived WHERE uid=?", (b["uid"],))]
        doc = L.book_doc(b["uid"], b["title"], b["title_from_file"], b["author"],
                         [b["subject1"], b["subject2"]], b["path"], b["series"], b["volume"],
                         b["container"], b["retention"], b["notes"], srcs, awards, derived)
        L.write_json_atomic(os.path.join(dest, "book.json"), doc)
        written += 1
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-json", action="store_true")
    args = ap.parse_args()
    st = build(emit_json=args.emit_json)
    print(json.dumps(st, ensure_ascii=False, indent=1))
