#!/usr/bin/env python3
"""只读状态审计：人账、库、library.db、index.db、expand、视图是否互相对得上。

  audit_state.py            # 快检，不读章节正文
  audit_state.py --deep     # 另算 expand 覆盖率，区分空正文章与真缺章
  audit_state.py --json     # 只输出 {"fail": [...], "warn": [...], "ok": [...]}
  audit_state.py --root DIR # 换库根，默认按脚本位置推

FAIL 表示有孤儿行、断链或对不上的账；WARN 表示批次在跑时允许落后的读数。
退出码：有 FAIL 为 1，否则 0。--json 不改判定，只换输出。
「== 文档 ==」一节只 WARN：AGENTS.md 的数字块与现库不一致就提示 doc_numbers.py --write。
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
CORPUS = os.path.join(ROOT, "库")
CAT = os.path.join(ROOT, "目录")
CLASS_CSV = os.path.join(CAT, "分类表.csv")
LIBDB = os.path.join(CAT, "library.db")
INDEX = os.path.join(CAT, "检索", "index.db")
VIEWS = os.path.join(ROOT, "视图")
MAP_CSV = os.path.join(CAT, "对照表.csv")

FAIL = []
WARN = []
OK = []
QUIET = False


def say(msg):
    if not QUIET:
        print(msg)


def ok(msg):
    OK.append(msg)
    say("PASS  " + msg)


def bad(msg):
    FAIL.append(msg)
    say("FAIL  " + msg)


def warn(msg):
    WARN.append(msg)
    say("WARN  " + msg)


def sample(items, n=5):
    items = sorted(items)
    return ", ".join(items[:n]) + (" ..." if len(items) > n else "")


def walk_books():
    books = {}
    for dirpath, dirnames, filenames in os.walk(CORPUS):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as fh:
                uid = json.load(fh)["uid"]
        except Exception as exc:
            bad("book.json 读不了 %s: %s" % (dirpath, exc))
            continue
        if uid in books:
            bad("uid 重复 %s: %s / %s" % (uid, books[uid], dirpath))
        books[uid] = dirpath
    return books


def walk_md(books):
    md = {}
    for dirpath, dirnames, filenames in os.walk(CORPUS):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if os.path.basename(dirpath) != "md":
            continue
        parent = os.path.dirname(dirpath)
        try:
            with open(os.path.join(parent, "book.json"), encoding="utf-8") as fh:
                uid = json.load(fh)["uid"]
        except Exception:
            continue
        chapters = [f for f in filenames if f.endswith(".md") and f != "README.md"]
        md[uid] = (dirpath, chapters)
    return md


def use_root(root):
    global ROOT, CORPUS, CAT, CLASS_CSV, LIBDB, INDEX, VIEWS, MAP_CSV
    ROOT = os.path.abspath(root)
    CORPUS = os.path.join(ROOT, "库")
    CAT = os.path.join(ROOT, "目录")
    CLASS_CSV = os.path.join(CAT, "分类表.csv")
    LIBDB = os.path.join(CAT, "library.db")
    INDEX = os.path.join(CAT, "检索", "index.db")
    VIEWS = os.path.join(ROOT, "视图")
    MAP_CSV = os.path.join(CAT, "对照表.csv")


def docs():
    try:
        sys.path.insert(0, HERE)
        import doc_numbers
        path = os.path.join(ROOT, doc_numbers.DOC)
        if not os.path.isfile(path):
            return
        counts = doc_numbers.collect(ROOT)
        block = doc_numbers.render(counts)
        with open(path, encoding="utf-8") as fh:
            got = doc_numbers.read_block(fh.read())
    except Exception as exc:
        warn("数字块取数失败: %s" % exc)
        return
    if got is None:
        warn("%s 没有数字块；doc_numbers.py --write 可补（现库 as_of %s）" % (
            doc_numbers.DOC, counts["as_of"]))
    elif not doc_numbers.same_numbers(got, block):
        warn("%s 的数字块过期（现库 as_of %s）；doc_numbers.py --write 重写" % (
            doc_numbers.DOC, counts["as_of"]))
    else:
        ok("%s 的数字块与现库一致（as_of %s）" % (doc_numbers.DOC, counts["as_of"]))


def report(args):
    """按模式给结论，返回退出码。JSON 模式下 FAIL/WARN/OK 只走 stdout。"""
    if args.json:
        print(json.dumps({"fail": FAIL, "warn": WARN, "ok": OK}, ensure_ascii=False))
    elif not FAIL:
        print("FAIL 0 项 / WARN %d 项%s" % (
            len(WARN), "（--strict 把 WARN 判失败）" if args.strict and WARN else ""))
    else:
        print("FAIL %d 项 / WARN %d 项" % (len(FAIL), len(WARN)))
    if FAIL:
        return 1
    return 1 if args.strict and WARN else 0


def main():
    global QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="读章节正文，区分空正文章与真缺章")
    ap.add_argument("--strict", action="store_true", help="收尾门槛：WARN 也算失败")
    ap.add_argument("--json", action="store_true", help="只输出 fail/warn/ok 三段 JSON")
    ap.add_argument("--root", default=ROOT, help="库根，默认按脚本位置推")
    args = ap.parse_args()
    QUIET = args.json
    use_root(args.root)
    if not os.path.isdir(CORPUS):
        bad("没有 库/")
        if args.json:
            return report(args)
        print("\nFAIL %d 项" % len(FAIL))
        return 1

    say("== 人账 / 库 ==")
    with open(CLASS_CSV, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    csv_uids = [r["uid"] for r in rows]
    dup = {u for u in csv_uids if csv_uids.count(u) > 1}
    if dup:
        bad("分类表 uid 重复 %d: %s" % (len(dup), sample(dup)))
    books = walk_books()
    for label, got in (("分类表", set(csv_uids)), ("book.json", set(books))):
        say("      %s %d" % (label, len(got)))
    if set(csv_uids) != set(books):
        bad("分类表与 book.json 差集: 人账多 %d [%s], 盘上多 %d [%s]" % (
            len(set(csv_uids) - set(books)), sample(set(csv_uids) - set(books)),
            len(set(books) - set(csv_uids)), sample(set(books) - set(csv_uids))))
    else:
        ok("分类表与 book.json 一一对应（%d 本）" % len(books))

    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    db_books = {r[0] for r in lib.execute("SELECT uid FROM book")}
    if set(books) != db_books:
        bad("book.json 与 library.db 差集: 盘上多 %d [%s], DB 多 %d [%s]" % (
            len(set(books) - db_books), sample(set(books) - db_books),
            len(db_books - set(books)), sample(db_books - set(books))))
    else:
        ok("book.json 与 library.db 一一对应（%d 本）" % len(db_books))
    missing_src = [(u, f) for u, f, p in
                   lib.execute("SELECT uid, file, primary_src FROM source")
                   if p and not os.path.exists(os.path.join(ROOT, f))]
    if missing_src:
        bad("主源文件缺失 %d: %s" % (len(missing_src), sample(u for u, _ in missing_src)))
    else:
        ok("主源文件全部存在（%d 个）" % lib.execute(
            "SELECT count(*) FROM source WHERE primary_src=1").fetchone()[0])

    say("\n== md ==")
    md = walk_md(books)
    derived = {u: (s, c) for u, s, c in
               lib.execute("SELECT uid, status, chapters FROM derived WHERE kind='md'")}
    empty = [u for u, (_, ch) in md.items() if not ch]
    no_readme = [u for u, (d, _) in md.items() if not os.path.isfile(os.path.join(d, "README.md"))]
    done = {u for u, (s, _) in derived.items() if s == "done"}
    if set(md) == done:
        ok("md 目录与 derived(md, done) 一一对应（%d 本）" % len(md))
    else:
        bad("md 目录与 derived 差集: 盘上多 %d [%s], DB 多 %d [%s]" % (
            len(set(md) - done), sample(set(md) - done),
            len(done) - len(set(md)), sample(done - set(md))))
    chapters = sum(len(ch) for _, ch in md.values())
    derived_ch = sum(c or 0 for s, c in derived.values())
    if chapters != derived_ch:
        bad("章节数不一致: 盘上 %d / derived %d" % (chapters, derived_ch))
    else:
        ok("章节数一致（%d 章）" % chapters)
    if empty:
        ignored = {r["uid"] for r in rows if "图片书，不索引" in r.get("备注", "")}
        unexpected = [u for u in empty if u not in ignored]
        if unexpected:
            warn("空 md（0 章）%d 本: %s" % (len(unexpected), sample(unexpected)))
        else:
            ok("空 md %d 本均已在分类表备注「图片书，不索引」" % len(empty))
    if no_readme:
        warn("缺 README 的 md %d 本: %s" % (len(no_readme), sample(no_readme)))

    say("\n== 检索层 ==")
    if not os.path.isfile(INDEX):
        bad("没有 目录/检索/index.db")
        idx = None
    else:
        idx = sqlite3.connect("file:%s?mode=ro" % INDEX, uri=True)
    if idx is not None:
        meta = dict(idx.execute("SELECT key, value FROM meta"))
        para_books = {r[0] for r in idx.execute("SELECT DISTINCT book FROM para")}
        para_paths = {r[0] for r in idx.execute("SELECT DISTINCT path FROM para")}
        para_missing = [p for p in para_paths if not os.path.exists(os.path.join(ROOT, p))]
        book_missing = [b for b in para_books
                        if not os.path.isdir(os.path.join(CORPUS, b))]
        if para_missing or book_missing:
            bad("倒排里有不存在的路径: 章 %d [%s], 书 %d [%s]" % (
                len(para_missing), sample(para_missing),
                len(book_missing), sample(book_missing)))
        else:
            ok("倒排 %d 段 / %d 章 / %d 本，路径全部存在" % (
                idx.execute("SELECT count(*) FROM para").fetchone()[0],
                len(para_paths), len(para_books)))
        behind = []
        indexable = sum(1 for _, ch in md.values() if ch)
        if len(para_books) != indexable:
            behind.append("书 %d/%d" % (len(para_books), indexable))
        if len(para_paths) != chapters:
            behind.append("章 %d/%d" % (len(para_paths), chapters))
        if behind:
            warn("index.db 落后库（built_at %s）: %s" % (
                meta.get("built_at", "?"), ", ".join(behind)))
        else:
            ok("index.db 与库的书写数、章节数一致")
        n_expand = idx.execute("SELECT count(*) FROM expand").fetchone()[0]
        exp_paths = {r[0] for r in idx.execute("SELECT DISTINCT path FROM expand")}
        exp_missing = [p for p in exp_paths if not os.path.exists(os.path.join(ROOT, p))]
        if exp_missing:
            bad("expand 指向不存在的章 %d: %s" % (len(exp_missing), sample(exp_missing)))
        else:
            ok("expand %d 行 / %d 章，路径全部存在" % (n_expand, len(exp_paths)))
        n_fts = idx.execute("SELECT count(*) FROM expand_fts").fetchone()[0]
        if n_fts != n_expand:
            bad("expand_fts 与 expand 行数不等: %d / %d" % (n_fts, n_expand))
        else:
            ok("expand_fts 行数与 expand 相等（%d）" % n_fts)
        missing_ch = set()
        for d, ch in md.values():
            for f in ch:
                p = os.path.relpath(os.path.join(d, f), ROOT)
                if p not in exp_paths:
                    missing_ch.add(p)
        if missing_ch:
            stub = 0
            if args.deep:
                sys.path.insert(0, HERE)
                import build_expand
                stub = sum(1 for p in missing_ch if build_expand.is_stub(os.path.join(ROOT, p)))
            warn("expand 未覆盖 %d 章（空正文章 %d，待扩 %d）%s" % (
                len(missing_ch), stub, len(missing_ch) - stub,
                "" if args.deep else "；--deep 才区分"))

    say("\n== 视图 / 对照表 ==")
    links = broken = 0
    for dirpath, dirnames, filenames in os.walk(VIEWS):
        for fn in dirnames + filenames:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                links += 1
                if not os.path.exists(p):
                    broken += 1
    if broken:
        bad("视图软链 %d 条，断链 %d" % (links, broken))
    else:
        ok("视图软链 %d 条全部 resolve" % links)
    with open(MAP_CSV, encoding="utf-8-sig") as fh:
        map_rows = list(csv.DictReader(fh))
    map_missing = [r["uid"] or r["旧路径"] for r in map_rows
                   if r["新路径"].startswith("库/")
                   and not os.path.exists(os.path.join(ROOT, r["新路径"]))]
    if map_missing:
        bad("对照表指向不存在的库路径 %d: %s" % (len(map_missing), sample(map_missing)))
    else:
        ok("对照表的 库/ 目标全部存在")

    say("\n== 文档 ==")
    docs()

    say("")
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
