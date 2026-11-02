#!/usr/bin/env python3
"""重建 视图/主题/<概念>/：指向书目录的相对软链 + 每概念一份可读清单。

  build_topic_views.py [--limit 10]

排序直接用 qsearch 的书级聚合，跟用户查到的顺序一致。软链树随时可删可重建。
"""
import argparse, os, shutil, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
OUT = os.path.join(ROOT, "视图", "主题")
LIB = os.path.join(ROOT, "books")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    keys = [r[0] for r in con.execute("SELECT key FROM book_concept GROUP BY key ORDER BY key")]
    index = ["# 主题视图", "",
             "指向 `库/` 的相对软链，随时可删可重建。排序与 `qsearch.py --concept <键> --book` 一致。", ""]
    total = 0
    for key in keys:
        groups, exclude = qsearch.load_concept(key)
        expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
        hits, _ = qsearch.search(con, expr, groups, exclude, 400, 40, pool=2000)
        books = qsearch.rollup_by_book(hits, con, a.limit)
        if not books:
            continue
        d = os.path.join(OUT, key)
        os.makedirs(d)
        lines = ["# %s" % key, "",
                 "按书级聚合分数排序，最多 %d 本。软链指向书目录；引用的段落回原文核。" % a.limit, ""]
        for b in books:
            base = os.path.basename(b["book"])
            target = os.path.relpath(os.path.join(LIB, b["book"]), d)
            link = os.path.join(d, base)
            if os.path.exists(link):
                link = os.path.join(d, "%s（%s）" % (base, b["book"].split("/")[0]))
            os.symlink(target, link)
            total += 1
            best = b["best"]
            lines.append("- [[%s]] `%.2f` %d 段  %s:%d" % (
                os.path.basename(link), b["score"], b["paras"], best["path"], best["line"]))
            lines.append("  " + " ".join(best["text"].split())[:140])
        lines += ["", "共 %d 本。" % len(books), ""]
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        index.append("- [%s](%s/) %d 本" % (key, key, len(books)))
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index) + "\n")
    print("概念 %d 个 / 软链 %d 条" % (len(os.listdir(OUT)) - 1, total))


if __name__ == "__main__":
    main()
