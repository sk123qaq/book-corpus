#!/usr/bin/env python3
"""只读状态：操作台顶栏与总览要的数字，一次采集。

  ops_status.py                 # 人读
  ops_status.py --json          # 字段见 apps/ops/README.md（历史契约见 docs/briefs/OPS-CONTRACT-2026-10-24.md §5）
  ops_status.py --deep          # 另跑 audit_state --deep，写 工作区/运维/last-audit.json
  ops_status.py --root /tmp/x   # 全部路径跟着换（临时库测试用）

SQLite 一律只读打开。--deep 是唯一的写盘动作，只写 last-audit.json。
索引落后按契约算：library.db 的 md done 与章数，比 index.db 的书与章。
零章 md（图册、图片页）进不了倒排，不算落后，否则徽标永远挂着这 4 本。
"""

import argparse
import csv
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT, L

AUDIT = os.path.join(HERE, "audit_state.py")
AUDIT_TIMEOUT = 1800
OCR_QUOTA = "20000/天"
AUDIT_ITEMS_CAP = 50
INBOX_ITEMS_CAP = 50
PENDING_KEYS = ("pdf", "txt", "djvu")
JOB_FIELDS = ("id", "kind", "title", "state", "created", "ended", "exit_code", "summary", "log_path")


class Paths:
    """--root 下所有账与产物的位置。换根只改这一个对象。"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lib = os.path.join(self.root, "books")
        self.cat = os.path.join(self.root, "tables")
        self.views = os.path.join(self.root, "build", "views")
        self.work = os.path.join(self.root, "工作区")
        self.batches = os.path.join(self.work, "批次")
        self.ops = os.path.join(self.work, "运维")
        self.inbox = os.path.join(self.lib, "_收件箱")
        self.class_csv = os.path.join(self.cat, "分类表.csv")
        self.libdb = os.path.join(self.root, "build", "library.db")
        self.indexdb = os.path.join(self.root, "build", "检索", "index.db")
        self.catalog_csv = os.path.join(self.root, "build", "书目总表.csv")
        self.opsdb = os.path.join(self.ops, "ops.db")
        self.last_audit = os.path.join(self.ops, "last-audit.json")


def iso(stamp):
    return datetime.datetime.fromtimestamp(stamp).astimezone().replace(microsecond=0).isoformat()


def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def mtime_of(path):
    try:
        return iso(os.path.getmtime(path))
    except OSError:
        return None


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def library(p, state):
    # ok=False 表示读数未知：页面据此显示「未知/—」，不能把 0 当真实计数。
    if not os.path.isfile(p.libdb):
        state["problems"].append("没有 build/library.db")
        state["library"] = {"db": None, "ok": False}
        return
    try:
        con = ro(p.libdb)
        try:
            state["books"] = con.execute("SELECT count(*) FROM book").fetchone()[0]
            state["md_done"] = con.execute("SELECT count(*) FROM derived WHERE kind='md' AND status='done'").fetchone()[
                0
            ]
            state["md_chapters"] = con.execute(
                "SELECT coalesce(sum(chapters), 0) FROM derived WHERE kind='md'"
            ).fetchone()[0]
            state["md_indexable"] = con.execute(
                "SELECT count(*) FROM derived WHERE kind='md' AND status='done' AND coalesce(chapters, 0) > 0"
            ).fetchone()[0]
            counts = dict(con.execute("SELECT lower(coalesce(format, '')), count(*) FROM v_pending_md GROUP BY 1"))
        finally:
            con.close()
    except sqlite3.Error as exc:
        state["problems"].append("library.db 读不了: %s" % exc)
        state["library"] = {"db": p.libdb, "ok": False}
        return
    pending = {k: counts.pop(k, 0) for k in PENDING_KEYS}
    pending.update(sorted(counts.items()))
    pending["total"] = sum(n for k, n in pending.items())
    ebooks = ("epub", "mobi", "azw3", "azw")
    docs = L.DOC_FORMATS
    state["pending"] = pending
    state["pending_groups"] = {
        "ebook": sum(n for fmt, n in pending.items() if fmt in ebooks),
        "doc": sum(n for fmt, n in pending.items() if fmt in docs),
        "pdf": pending.get("pdf", 0),
        "other": sum(n for fmt, n in pending.items() if fmt not in (*ebooks, *docs, "pdf", "total")),
    }
    state["library"] = {"db": p.libdb, "ok": True}


def inbox(p, state):
    items, ignored = [], []
    accept = L.ACCEPT_EXTS
    if os.path.isdir(p.inbox):
        for name in sorted(os.listdir(p.inbox)):
            fp = os.path.join(p.inbox, name)
            if name.startswith(".") or name.endswith(".part") or not os.path.isfile(fp):
                continue
            if name.lower() in ("readme.md", "readme.txt"):
                # 与 ingest.py / 操作台收件箱同口径：说明文件不算书
                continue
            st = os.stat(fp)
            entry = {"name": name, "bytes": st.st_size, "mtime": iso(st.st_mtime)}
            if os.path.splitext(name)[1].lower() not in accept:
                ignored.append(entry)
                continue
            items.append(entry)
    state["inbox"] = {
        "files": len(items),
        "items": items[:INBOX_ITEMS_CAP],
        "ignored_count": len(ignored),
        "ignored": ignored[:INBOX_ITEMS_CAP],
    }


def ledger(p, state):
    rows = 0
    db = L.ledger_path(p.root)
    try:
        rows = L.categories_count(root=p.root)
    except SystemExit as exc:
        state["problems"].append(str(exc))
    stale = False
    if os.path.isfile(db) and os.path.isfile(p.libdb):
        try:
            stale = os.path.getmtime(db) > os.path.getmtime(p.libdb) + 1
        except OSError:
            stale = False
    state["ledger"] = {
        "rows": rows,
        "mtime": mtime_of(db) if os.path.isfile(db) else mtime_of(p.class_csv),
        "db": db if os.path.isfile(db) else None,
        "legacy_csv": p.class_csv if os.path.isfile(p.class_csv) else None,
        "stale": stale,
    }


def catalog_unmatched(p):
    """未匹配条数，口径与 gen_book_catalog.py 的 audit() 同一份，不改它的判定。

    audit() 读 lib_common 的 ROOT/LIB 全局，这里临时指到 --root，读完原样放回。
    """
    if not os.path.isfile(p.libdb):
        return None
    sys.path.insert(0, HERE)
    import gen_book_catalog as G
    import lib_common as L

    saved = (L.ROOT, L.BOOKS, L.TABLES)
    L.ROOT, L.BOOKS, L.TABLES = p.root, p.lib, p.cat
    try:
        con = ro(p.libdb)
        try:
            problems, _counts = G.audit(con)
        finally:
            con.close()
    finally:
        L.ROOT, L.BOOKS, L.TABLES = saved
    return len(problems)


def catalog(p, state):
    # CSV 不在时不上报「未匹配 0 条」：没有总表就是状态不完整，页面要显示「未知」。
    has_csv = os.path.isfile(p.catalog_csv)
    unmatched = None
    if not has_csv:
        state["problems"].append("没有 build/书目总表.csv")
    else:
        try:
            unmatched = catalog_unmatched(p)
        except (SystemExit, sqlite3.Error, OSError) as exc:
            state["problems"].append("书目总表未匹配算不了: %s" % exc)
    state["catalog"] = {
        "mtime": mtime_of(p.catalog_csv),
        "unmatched": unmatched,
        "ok": has_csv and unmatched is not None,
    }


def index_meta(con):
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    except sqlite3.DatabaseError:
        return {}


def index_counts(con, meta):
    def pick(key, sql):
        value = meta.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return con.execute(sql).fetchone()[0]

    return (
        pick("books", "SELECT count(DISTINCT book) FROM para"),
        pick("chapters", "SELECT count(DISTINCT path) FROM para"),
        pick("paras", "SELECT count(*) FROM para"),
    )


def md_book_paths(p):
    con = ro(p.libdb)
    try:
        return {
            path
            for (path,) in con.execute(
                "SELECT b.path FROM book b JOIN derived d ON d.uid = b.uid "
                "WHERE d.kind = 'md' AND d.status = 'done' AND coalesce(d.chapters, 0) > 0"
            )
        }
    finally:
        con.close()


def chapter_paths(p, books):
    out = set()
    for rel in books:
        d = os.path.join(p.lib, rel, "md")
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".md") and name != "README.md" and os.path.isfile(os.path.join(d, name)):
                out.add(os.path.join("books", rel, "md", name))
    return out


def index(p, state, deep):
    meta = {}
    index_books = lag_books = lag_chapters = 0
    chapters = paras = 0
    # ok=False 表示索引读数未知（缺库或读不了），页面显示「未知」，不报「已同步」。
    ok = os.path.isfile(p.indexdb)
    if not ok:
        state["problems"].append("没有 build/检索/index.db")
    else:
        try:
            con = ro(p.indexdb)
            try:
                meta = index_meta(con)
                if deep:
                    idx_books = set()
                    idx_chapters = set()
                    for book, path in con.execute("SELECT DISTINCT book, path FROM para"):
                        idx_books.add(book)
                        idx_chapters.add(path)
                    index_books = len(idx_books)
                    chapters = len(idx_chapters)
                    try:
                        paras = int(meta["paras"])
                    except (KeyError, TypeError, ValueError):
                        paras = con.execute("SELECT count(*) FROM para").fetchone()[0]
                    lib_books = md_book_paths(p)
                    lag_books = len(lib_books - idx_books) - len(idx_books - lib_books)
                    lib_chapters = chapter_paths(p, lib_books)
                    lag_chapters = len(lib_chapters - idx_chapters) - len(idx_chapters - lib_chapters)
                else:
                    index_books, chapters, paras = index_counts(con, meta)
                    lag_books = state["md_indexable"] - index_books
                    lag_chapters = state["md_chapters"] - chapters
            finally:
                con.close()
        except sqlite3.Error as exc:
            ok = False
            state["problems"].append("index.db 读不了: %s" % exc)
    extra_books = max(0, -lag_books)
    extra_chapters = max(0, -lag_chapters)
    if ok and extra_books:
        state["problems"].append("索引比书库多 %d 本（可能含已移出的书），需重建" % extra_books)
    if ok and extra_chapters:
        state["problems"].append("索引比书库多 %d 章（可能含已移出的章节），需重建" % extra_chapters)
    state["index"] = {
        "built_at": meta.get("built_at"),
        "paras": paras,
        "chapters": chapters,
        "books": index_books,
        "lag_books": lag_books,
        "lag_chapters": lag_chapters,
        "extra_books": extra_books,
        "extra_chapters": extra_chapters,
        "lag_exact": bool(deep),
        "db": p.indexdb if os.path.isfile(p.indexdb) else None,
        "ok": ok,
    }


def views(p, state):
    awards = 0
    broken = 0
    base = os.path.join(p.views, "奖项")
    if os.path.isdir(base):
        for dirpath, dirnames, filenames in os.walk(base):
            for name in dirnames + filenames:
                fp = os.path.join(dirpath, name)
                if not os.path.islink(fp):
                    continue
                awards += 1
                if not os.path.exists(fp):
                    broken += 1
    state["views"] = {
        "awards": awards,
        "broken": broken,
        "ok": os.path.isdir(p.views),
    }


def read_cache(p, state):
    try:
        with open(p.last_audit, encoding="utf-8") as fh:
            cache = json.load(fh)
    except OSError:
        return {}
    except ValueError as exc:
        state["problems"].append("last-audit.json 读不了: %s" % exc)
        return {}
    if not isinstance(cache, dict):
        state["problems"].append("last-audit.json 不是对象，已忽略")
        return {}
    cached_root = cache.get("root")
    if not cached_root:
        state["problems"].append("last-audit.json 没写 root，无法确认属于本库，已忽略")
        return {}
    if os.path.realpath(str(cached_root)) != os.path.realpath(p.root):
        state["problems"].append("last-audit.json 属于另一个书库（%s），已忽略" % cached_root)
        return {}
    if cache.get("deep") is not True:
        state["problems"].append("last-audit.json 不是深度体检，已忽略")
        return {}
    return cache


def write_cache(p, cache):
    os.makedirs(p.ops, exist_ok=True)
    tmp = p.last_audit + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p.last_audit)


def refresh_audit(p, state):
    try:
        proc = subprocess.run(
            [sys.executable, AUDIT, "--json", "--deep", "--root", p.root],
            capture_output=True,
            text=True,
            timeout=AUDIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        state["problems"].append("audit_state 跑不起来: %s" % exc)
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        state["problems"].append("audit_state 没回 JSON（rc=%s）: %s" % (proc.returncode, proc.stderr.strip()[-300:]))
        return None
    cache = {
        "at": now_iso(),
        "root": p.root,
        "deep": True,
        "fail": data.get("fail") or [],
        "warn": data.get("warn") or [],
        "ok": data.get("ok") or [],
    }
    write_cache(p, cache)
    return cache


def audit(p, state, cache):
    fail = list(cache.get("fail") or [])
    warn = list(cache.get("warn") or [])
    items = ["FAIL " + m for m in fail] + ["WARN " + m for m in warn]
    at = cache.get("at")
    stale = False
    if at:
        try:
            at_ts = datetime.datetime.fromisoformat(str(at)).timestamp()
        except (TypeError, ValueError):
            state["problems"].append("last-audit.json 的体检时间读不了: %r" % (at,))
            at = None
            stale = True
        else:
            # 体检缓存是结构/链接检查的结果；库、索引或书目表在体检之后又变了，
            # 旧结果就不能再担保「全部通过」。按产物 mtime 判断，不按天数过期。
            for artifact, label in (
                (p.libdb, "build/library.db"),
                (p.indexdb, "build/检索/index.db"),
                (p.catalog_csv, "build/书目总表.csv"),
            ):
                try:
                    changed = os.path.getmtime(artifact)
                except OSError:
                    continue
                if changed > at_ts + 1:
                    stale = True
                    state["problems"].append("%s 比上次体检新，体检结果已过期，需重跑 --deep" % label)
    state["audit"] = {
        "fail": len(fail),
        "warn": len(warn),
        "items": items[:AUDIT_ITEMS_CAP],
        "at": at,
        # ran=False：还没有体检记录（at 为空），页面显示「未运行」并进待办。
        "ran": bool(at),
        # stale=True：体检跑过，但库/索引/书目表在那之后又变了；旧结果不能担保现在。
        "stale": stale,
    }
    if len(items) > AUDIT_ITEMS_CAP:
        state["audit"]["items"].append("…另 %d 项" % (len(items) - AUDIT_ITEMS_CAP))


def _latest_batch_file(batches_dir, prefix, *parts):
    """找 工作区/批次/<prefix>YYYY-MM-DD 下最近一个存在指定相对文件的目录。"""
    if not os.path.isdir(batches_dir):
        return ""
    pat = re.compile(r"^%s\d{4}-\d{2}-\d{2}$" % re.escape(prefix))
    for name in sorted(os.listdir(batches_dir), reverse=True):
        if not pat.match(name):
            continue
        cand = os.path.join(batches_dir, name, *parts)
        if os.path.isfile(cand):
            return cand
    return ""


def ocr(p, state):
    batch = None
    if os.path.isdir(p.batches):
        names = [
            n
            for n in os.listdir(p.batches)
            if n.startswith("pdf-ocr-")
            and os.path.isdir(os.path.join(p.batches, n))
            and os.path.isfile(os.path.join(p.batches, n, "worklist.tsv"))
        ]
        batch = max(names) if names else None
    result = {
        "batch": batch,
        "remaining_books": 0,
        "remaining_pages": 0,
        "deferred_books": 0,
        "deferred_pages": 0,
        "skip_books": 0,
        "skip_pages": 0,
        "quota": OCR_QUOTA,
    }
    skip_rows = []
    skip_path = _latest_batch_file(p.batches, "pdf-普查-", "skip_list.tsv")
    if skip_path and os.path.isfile(skip_path):
        with open(skip_path, encoding="utf-8-sig") as fh:
            skip_rows = [r for r in csv.DictReader(fh, delimiter="\t") if r.get("uid")]
    skip_uids = {r["uid"] for r in skip_rows}
    pending_pdf = set()
    if os.path.isfile(p.libdb):
        try:
            con = ro(p.libdb)
            try:
                pending_pdf = {r[0] for r in con.execute("SELECT uid FROM v_pending_md WHERE format='pdf'")}
            finally:
                con.close()
        except sqlite3.DatabaseError:
            pass
    skip_uids &= pending_pdf
    result["skip_books"] = len(skip_uids)
    result["skip_pages"] = sum(int(float(r.get("pages") or 0)) for r in skip_rows if r["uid"] in skip_uids)
    defer_uids = set()
    defer_path = _latest_batch_file(p.batches, "pdf-ocr-", "defer", "lang.tsv")
    if defer_path and os.path.isfile(defer_path):
        with open(defer_path, encoding="utf-8-sig") as fh:
            defer_uids.update(
                r.get("uid")
                for r in csv.DictReader(fh, delimiter="\t")
                if r.get("uid") and (r.get("lang") or "").strip() == "ja"
            )
    if batch:
        worklist = os.path.join(p.batches, batch, "worklist.tsv")
        if not os.path.isfile(worklist):
            state["problems"].append("%s 没有 worklist.tsv" % batch)
        else:
            with open(worklist, encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    book = row.get("path") or ""
                    if book and os.path.isdir(os.path.join(p.lib, book, "md")):
                        continue
                    try:
                        pages = int(row.get("pages") or 0)
                    except ValueError:
                        pages = 0
                    uid = row.get("uid") or ""
                    if uid in defer_uids:
                        result["deferred_books"] += 1
                        result["deferred_pages"] += pages
                        continue
                    if uid in skip_uids:
                        continue
                    result["remaining_books"] += 1
                    result["remaining_pages"] += pages
    state["ocr"] = result


def jobs(p, state):
    result = {"running": 0, "awaiting_review": 0, "last": None}
    if os.path.isfile(p.opsdb):
        try:
            con = ro(p.opsdb)
            try:
                for name, n in con.execute("SELECT state, count(*) FROM job GROUP BY 1"):
                    if name in result:
                        result[name] = n
                sql = (
                    "SELECT %s FROM job ORDER BY created DESC, id DESC LIMIT 1"  # noqa: S608 列名来自常量 JOB_FIELDS
                    % ", ".join(JOB_FIELDS)
                )
                row = con.execute(sql).fetchone()
                if row:
                    result["last"] = dict(zip(JOB_FIELDS, row, strict=False))
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            state["problems"].append("ops.db 读不了: %s" % exc)
    state["jobs"] = result


def embed_status(p, state, disk_sigs=None):
    db = os.path.join(p.root, "build", "检索", "embeddings", "emb.db")
    try:
        import embed_corpus as EC

        diff = EC.diff_embed(p.root, db_path=db, check_network=False, disk_sigs=disk_sigs)
        state["embed"] = {
            "ok": diff["has_db"],
            "engine_ready": diff["engine_ready"],
            "books": diff["unchanged"] + len(diff["modified"]),
            "chunks": diff["chunks"],
            "garbage": diff["garbage"],
            "garbage_ratio": diff["garbage_ratio"],
            "added": diff["added"],
            "modified": diff["modified"],
            "orphan": diff["orphan"],
        }
    except (ImportError, OSError, sqlite3.Error, ValueError):
        diff = None
    if diff is not None:
        return
    if not os.path.isfile(db):
        state["embed"] = {
            "ok": False,
            "engine_ready": False,
            "books": 0,
            "chunks": 0,
            "garbage": 0,
            "garbage_ratio": 0.0,
            "added": [],
            "modified": [],
            "orphan": [],
        }
        return
    try:
        con = ro(db)
        try:
            chunks = con.execute("SELECT count(*) FROM chunk WHERE active=1").fetchone()[0]
            books = con.execute("SELECT count(DISTINCT book) FROM chunk WHERE active=1").fetchone()[0]
            state["embed"] = {
                "ok": True,
                "engine_ready": False,
                "books": books,
                "chunks": chunks,
                "garbage": 0,
                "garbage_ratio": 0.0,
                "added": [],
                "modified": [],
                "orphan": [],
            }
        finally:
            con.close()
    except sqlite3.Error:
        state["embed"] = {
            "ok": False,
            "engine_ready": False,
            "books": 0,
            "chunks": 0,
            "garbage": 0,
            "garbage_ratio": 0.0,
            "added": [],
            "modified": [],
            "orphan": [],
        }


def sync_delta(p, state, disk_sigs=None):
    """只读计算各派生层相对活库的精确增量差集（绝不写盘、绝不把冷宫混入活库）。"""
    fts = {
        "has_db": os.path.isfile(p.indexdb),
        "has_tracking": False,
        "added": [],
        "modified": [],
        "removed": [],
        "unchanged": 0,
    }
    try:
        import build_para_index as BPI

        fts = BPI.diff_index(p.indexdb, root=p.root, disk_sigs=disk_sigs)
    except (ImportError, OSError, sqlite3.Error, ValueError):
        fts["has_db"] = os.path.isfile(p.indexdb)

    expand_covered = 0
    if os.path.isfile(p.indexdb):
        try:
            con = ro(p.indexdb)
            try:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "expand" in tables:
                    # path 形如 books/B0001/md/...
                    expand_covered = con.execute(
                        "SELECT count(DISTINCT substr(path, 7, instr(substr(path, 7), '/') - 1)) "
                        "FROM expand WHERE path LIKE 'books/%/%'"
                    ).fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            expand_covered = 0

    cold_count = 0
    if os.path.isdir(p.batches):
        seen_cold = set()
        for name in sorted(os.listdir(p.batches)):
            if not name.startswith("移出-"):
                continue
            tsv = os.path.join(p.batches, name, "retired.tsv")
            if not os.path.isfile(tsv):
                continue
            try:
                with open(tsv, encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh, delimiter="\t"):
                        if row.get("uid") and not (row.get("note") or "").strip():
                            seen_cold.add((row["uid"], row.get("retired_at") or ""))
            except OSError:
                continue
        cold_count = len(seen_cold)

    emb = state.get("embed") or {}
    fts_drift = len(fts.get("added") or []) + len(fts.get("modified") or []) + len(fts.get("removed") or [])
    emb_pending = len(emb.get("added") or []) + len(emb.get("modified") or [])
    emb_orphan = len(emb.get("orphan") or [])
    emb_initialized = bool(emb.get("ok") and (emb.get("chunks") or 0) > 0)
    ledger_stale = bool((state.get("ledger") or {}).get("stale"))
    # 向量库尚未首次初始化（有效块为 0）不算「增量偏差」，避免误导用户或自动拉起 6-7 小时全库建库
    has_drift = bool(
        ledger_stale
        or fts_drift > 0
        or emb_orphan > 0
        or (emb_initialized and emb.get("engine_ready") and emb_pending > 0)
    )
    uncovered_books = max(0, (state.get("md_indexable") or 0) - expand_covered)
    state["sync_delta"] = {
        "has_drift": has_drift,
        "meta": {
            "ledger_stale": ledger_stale,
            "catalog_missing": not os.path.isfile(p.catalog_csv),
            "views_missing": not os.path.isdir(os.path.join(p.views, "奖项")),
        },
        "fts": {
            "has_db": bool(fts.get("has_db")),
            "has_tracking": bool(fts.get("has_tracking")),
            "added": fts.get("added") or [],
            "modified": fts.get("modified") or [],
            "removed": fts.get("removed") or [],
            "unchanged": fts.get("unchanged") or 0,
            "drift_count": fts_drift,
        },
        "embed": {
            "ok": bool(emb.get("ok")),
            "initialized": emb_initialized,
            "engine_ready": bool(emb.get("engine_ready")),
            "added": emb.get("added") or [],
            "modified": emb.get("modified") or [],
            "orphan": emb.get("orphan") or [],
            "chunks": emb.get("chunks") or 0,
            "garbage": emb.get("garbage") or 0,
            "garbage_ratio": emb.get("garbage_ratio") or 0.0,
            "compact_recommended": (emb.get("garbage") or 0) > 0 and (emb.get("garbage_ratio") or 0.0) >= 0.1,
        },
        "expand": {
            "covered_books": expand_covered,
            "uncovered_books": uncovered_books,
        },
        "cold_count": cold_count,
    }


def collect(p, deep=False):
    state = {
        "at": now_iso(),
        "root": p.root,
        "books": 0,
        "md_done": 0,
        "md_chapters": 0,
        "md_indexable": 0,
        "pending": dict.fromkeys((*PENDING_KEYS, "total"), 0),
        "pending_groups": {"ebook": 0, "doc": 0, "pdf": 0, "other": 0},
        "inbox": {"files": 0, "items": [], "ignored_count": 0, "ignored": []},
        "ledger": {"rows": 0, "mtime": None, "db": None, "legacy_csv": None, "stale": False},
        "catalog": {"mtime": None, "unmatched": None},
        "index": {
            "built_at": None,
            "paras": 0,
            "chapters": 0,
            "books": 0,
            "lag_books": 0,
            "lag_chapters": 0,
            "extra_books": 0,
            "extra_chapters": 0,
            "lag_exact": False,
        },
        "views": {"awards": 0, "broken": 0},
        "audit": {"fail": 0, "warn": 0, "items": [], "at": None, "ran": False, "stale": False},
        "ocr": {
            "batch": None,
            "remaining_books": 0,
            "remaining_pages": 0,
            "deferred_books": 0,
            "deferred_pages": 0,
            "skip_books": 0,
            "skip_pages": 0,
            "quota": OCR_QUOTA,
        },
        "jobs": {"running": 0, "awaiting_review": 0, "last": None},
        "problems": [],
    }
    library(p, state)
    inbox(p, state)
    ledger(p, state)
    catalog(p, state)
    index(p, state, deep)
    disk_sigs = None
    try:
        import build_para_index as BPI

        disk_sigs = BPI.scan_md_sigs(corpus=p.lib)
    except (ImportError, OSError):
        disk_sigs = None
    embed_status(p, state, disk_sigs=disk_sigs)
    views(p, state)
    sync_delta(p, state, disk_sigs=disk_sigs)
    cache = read_cache(p, state)
    if deep:
        fresh = refresh_audit(p, state)
        cache = fresh if fresh is not None else cache
    audit(p, state, cache)
    ocr(p, state)
    jobs(p, state)
    return state


def render(state):
    pending = " ".join("%s %d" % (k, state["pending"][k]) for k in state["pending"] if k != "total")
    idx = state["index"]
    audit_state_ = state["audit"]
    ocr_state = state["ocr"]
    last = state["jobs"]["last"]
    lines = [
        "书 %d | md %d / %d 章 | 待转 %s（共 %d）"
        % (state["books"], state["md_done"], state["md_chapters"], pending, state["pending"]["total"]),
        "收件箱 %d 个文件" % state["inbox"]["files"],
        "人账 %d 行 (%s) | 书目表 (%s) 未匹配 %s"
        % (
            state["ledger"]["rows"],
            (state["ledger"].get("db") or "ledger.db"),
            state["catalog"]["mtime"] or "无",
            "未知" if state["catalog"]["unmatched"] is None else state["catalog"]["unmatched"],
        ),
        "索引 %d 本 / %d 章 / %d 段 (%s) 落后 %d 本 / %d 章%s"
        % (
            idx["books"],
            idx["chapters"],
            idx["paras"],
            idx["built_at"] or "无",
            idx["lag_books"],
            idx["lag_chapters"],
            "" if idx["lag_exact"] else "（按计数，--deep 精确）",
        ),
        "视图 奖项 %d / 断链 %d" % (state["views"]["awards"], state["views"]["broken"]),
        "审计 FAIL %d / WARN %d（%s%s）"
        % (
            audit_state_["fail"],
            audit_state_["warn"],
            audit_state_["at"] or "未审",
            "，已过期" if audit_state_.get("stale") else "",
        ),
        "OCR %s 还剩 %d 本 / %d 页（额度 %s）"
        % (
            ocr_state["batch"] or "无批次",
            ocr_state["remaining_books"],
            ocr_state["remaining_pages"],
            ocr_state["quota"],
        ),
        "任务 running %d / awaiting_review %d / 最近 %s"
        % (
            state["jobs"]["running"],
            state["jobs"]["awaiting_review"],
            "%s %s" % (last["id"], last["state"]) if last else "无",
        ),
    ]
    print("%s  root=%s" % (state["at"], state["root"]))
    for line in lines:
        print("  " + line)
    for item in state["audit"]["items"]:
        print("  " + item)
    for problem in state["problems"]:
        print("问题 " + problem, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="只读输出书库状态（JSON 或人读）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--root", default=ROOT, help="库根，默认按脚本位置推")
    ap.add_argument(
        "--deep", action="store_true", help="跑 audit_state --deep，精确算索引落后，写 工作区/运维/last-audit.json"
    )
    args = ap.parse_args()
    p = Paths(args.root)
    if not os.path.isdir(p.root):
        print("没有根目录: %s" % p.root, file=sys.stderr)
        return 2
    state = collect(p, deep=args.deep)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        render(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
