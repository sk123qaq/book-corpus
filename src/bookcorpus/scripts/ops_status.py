#!/usr/bin/env python3
"""只读状态：操作台顶栏与总览要的数字，一次采集。

  ops_status.py                 # 人读
  ops_status.py --json          # 字段见 apps/ops/CONTRACT.md §5
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
    if not os.path.isfile(p.libdb):
        state["problems"].append("没有 build/library.db")
        return
    con = ro(p.libdb)
    try:
        state["books"] = con.execute("SELECT count(*) FROM book").fetchone()[0]
        state["md_done"] = con.execute("SELECT count(*) FROM derived WHERE kind='md' AND status='done'").fetchone()[0]
        state["md_chapters"] = con.execute("SELECT coalesce(sum(chapters), 0) FROM derived WHERE kind='md'").fetchone()[
            0
        ]
        state["md_indexable"] = con.execute(
            "SELECT count(*) FROM derived WHERE kind='md' AND status='done' AND coalesce(chapters, 0) > 0"
        ).fetchone()[0]
        counts = dict(con.execute("SELECT lower(coalesce(format, '')), count(*) FROM v_pending_md GROUP BY 1"))
    finally:
        con.close()
    pending = {k: counts.pop(k, 0) for k in PENDING_KEYS}
    pending.update(sorted(counts.items()))
    pending["total"] = sum(n for k, n in pending.items())
    state["pending"] = pending


def inbox(p, state):
    items = []
    if os.path.isdir(p.inbox):
        for name in sorted(os.listdir(p.inbox)):
            fp = os.path.join(p.inbox, name)
            if name.startswith(".") or name.endswith(".part") or not os.path.isfile(fp):
                continue
            st = os.stat(fp)
            items.append({"name": name, "bytes": st.st_size, "mtime": iso(st.st_mtime)})
    state["inbox"] = {"files": len(items), "items": items[:INBOX_ITEMS_CAP]}


def ledger(p, state):
    rows = 0
    db = L.ledger_path(p.root)
    try:
        rows = L.categories_count(root=p.root)
    except SystemExit as exc:
        state["problems"].append(str(exc))
    state["ledger"] = {
        "rows": rows,
        "mtime": mtime_of(db) if os.path.isfile(db) else mtime_of(p.class_csv),
        "db": db if os.path.isfile(db) else None,
        "legacy_csv": p.class_csv if os.path.isfile(p.class_csv) else None,
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
    unmatched = None
    try:
        unmatched = catalog_unmatched(p)
    except (SystemExit, sqlite3.Error, OSError) as exc:
        state["problems"].append("书目总表未匹配算不了: %s" % exc)
    state["catalog"] = {"mtime": mtime_of(p.catalog_csv), "unmatched": unmatched}


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
    if not os.path.isfile(p.indexdb):
        state["problems"].append("没有 build/检索/index.db")
    else:
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
    state["index"] = {
        "built_at": meta.get("built_at"),
        "paras": paras,
        "chapters": chapters,
        "books": index_books,
        "lag_books": lag_books,
        "lag_chapters": lag_chapters,
        "lag_exact": bool(deep),
    }


def views(p, state):
    counts = {"awards": 0, "topics": 0}
    broken = 0
    for key, sub in (("awards", "奖项"), ("topics", "主题")):
        base = os.path.join(p.views, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            for name in dirnames + filenames:
                fp = os.path.join(dirpath, name)
                if not os.path.islink(fp):
                    continue
                counts[key] += 1
                if not os.path.exists(fp):
                    broken += 1
    state["views"] = {"awards": counts["awards"], "topics": counts["topics"], "broken": broken}


def read_cache(p, state):
    try:
        with open(p.last_audit, encoding="utf-8") as fh:
            cache = json.load(fh)
        return cache if isinstance(cache, dict) else {}
    except OSError:
        return {}
    except ValueError as exc:
        state["problems"].append("last-audit.json 读不了: %s" % exc)
        return {}


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


def audit(state, cache):
    fail = list(cache.get("fail") or [])
    warn = list(cache.get("warn") or [])
    items = ["FAIL " + m for m in fail] + ["WARN " + m for m in warn]
    state["audit"] = {"fail": len(fail), "warn": len(warn), "items": items[:AUDIT_ITEMS_CAP], "at": cache.get("at")}
    if len(items) > AUDIT_ITEMS_CAP:
        state["audit"]["items"].append("…另 %d 项" % (len(items) - AUDIT_ITEMS_CAP))


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
    skip_path = os.path.join(p.batches, "pdf-普查-2026-10-19", "skip_list.tsv")
    if os.path.isfile(skip_path):
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
    defer_path = os.path.join(p.batches, "pdf-ocr-2026-10-19", "defer", "lang.tsv")
    if os.path.isfile(defer_path):
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


def collect(p, deep=False):
    state = {
        "at": now_iso(),
        "root": p.root,
        "books": 0,
        "md_done": 0,
        "md_chapters": 0,
        "md_indexable": 0,
        "pending": dict.fromkeys((*PENDING_KEYS, "total"), 0),
        "inbox": {"files": 0, "items": []},
        "ledger": {"rows": 0, "mtime": None, "db": None, "legacy_csv": None},
        "catalog": {"mtime": None, "unmatched": None},
        "index": {
            "built_at": None,
            "paras": 0,
            "chapters": 0,
            "books": 0,
            "lag_books": 0,
            "lag_chapters": 0,
            "lag_exact": False,
        },
        "views": {"awards": 0, "topics": 0, "broken": 0},
        "audit": {"fail": 0, "warn": 0, "items": [], "at": None},
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
    views(p, state)
    cache = read_cache(p, state)
    if deep:
        fresh = refresh_audit(p, state)
        cache = fresh if fresh is not None else cache
    audit(state, cache)
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
        "视图 奖项 %d / 主题 %d / 断链 %d"
        % (state["views"]["awards"], state["views"]["topics"], state["views"]["broken"]),
        "审计 FAIL %d / WARN %d（%s）" % (audit_state_["fail"], audit_state_["warn"], audit_state_["at"] or "未审"),
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
