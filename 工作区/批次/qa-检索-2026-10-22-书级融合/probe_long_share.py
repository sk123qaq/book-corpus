#!/usr/bin/env python3
"""long_share@10 的基准选择实验。

  probe_long_share.py --random 100   随机查询下的长书占比分布，每个方案一份
  probe_long_share.py --corr         金标题上 long_share 与证据章命中的关系

要回答的问题：`long_share@10` 的基准该用书数、章数还是段数。

- 书数基准 20/1374 = 0.0146，是「每本书同样可能」的假设。
- 章数基准 0.3414 与段数基准 0.2797，是「每个章/段同样可能」的假设。
- 段落级检索的打分单位是段，聚合不改变段的排序，所以长度中性的参照应该接近段数占比。
  随机查询探针就是验证这一点：没有语义信号时，各方案的长书占比落在哪。

输出 `long-share-probe.tsv` 与 `long-share-corr.tsv`。
"""
import argparse
import csv
import os
import random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import book_score
import lib_fts_cjk as cjk
import qsearch

DB = book_score.DB
LIBDB = book_score.LIBDB
SCHEMES = ["sum", "mean", "best", "mix", "best_support", "best_top3_support",
           "rrf5", "best_support_length"]


def chapter_counts():
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    idx = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    ch = dict(lib.execute(
        "SELECT b.path, d.chapters FROM book b JOIN derived d ON d.uid=b.uid "
        "AND d.kind='md' WHERE d.chapters > 0"))
    paras = dict(idx.execute("SELECT book, count(*) FROM para GROUP BY book"))
    chapters = dict(idx.execute("SELECT book, count(DISTINCT path) FROM para GROUP BY book"))
    return ch, paras, chapters


def long_share_10(ranked, ch):
    return sum(1 for b in ranked[:10] if ch.get(b.book, 0) >= 500) / 10.0


def random_queries(n, con, meta, expand, ch, seed=20261022):
    random.seed(seed)
    rows = con.execute(
        "SELECT text FROM para WHERE chars BETWEEN 150 AND 400 AND line > 5 "
        "ORDER BY id LIMIT 200000").fetchall()
    out, made = [], 0
    while made < n:
        text = random.choice(rows)[0]
        clean = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
        segs = [s for s in clean.split() if len(s) >= 8]
        if not segs:
            continue
        seg = random.choice(segs)
        i = random.randrange(0, len(seg) - 5)
        terms = [seg[i:i + 2], seg[i + 2:i + 6]]
        if len(set(terms[0])) == 1:
            continue
        expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
        hits, _ = qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)
        if not hits:
            continue
        for scheme in SCHEMES:
            ranked = book_score.rank(hits, terms, scheme, [], meta, expand)
            out.append({"qid": "r%03d" % made, "query": ",".join(terms),
                        "scheme": scheme, "hits": len(hits),
                        "long_share10": round(long_share_10(ranked, ch), 4)})
        made += 1
    return out


def corr(xs, ys):
    if len(xs) < 3:
        return 0.0
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def corr_report():
    path = os.path.join(HERE, "eval-book.tsv")
    rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
    out = []
    groups = [("all", rows)] + [(st, [r for r in rows if r["stratum"] == st])
                               for st in ("long", "short", "ordinary")]
    for name, rs in groups:
        long10 = [int(r["top10_long"]) for r in rs]
        ev10 = [int(r["ev10"]) for r in rs]
        out.append({"group": name, "n": len(rs),
                    "mean_long10": round(sum(long10) / len(rs), 3),
                    "ev10": round(sum(ev10) / len(rs), 3),
                    "corr_long10_ev10": round(corr(long10, ev10), 4)})
    with open(os.path.join(HERE, "long-share-corr.tsv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    for r in out:
        print("%-10s n=%-3d mean_long10=%.3f ev10=%.3f corr=%+.4f" % (
            r["group"], r["n"], r["mean_long10"], r["ev10"], r["corr_long10_ev10"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=0)
    ap.add_argument("--corr", action="store_true")
    a = ap.parse_args()
    if a.corr:
        return corr_report()
    if not a.random:
        ap.error("给 --random N 或 --corr")
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    meta = book_score.load_meta(LIBDB)
    expand = book_score.load_expand(con)
    ch, paras, chapters = chapter_counts()
    rows = random_queries(a.random, con, meta, expand, ch)
    path = os.path.join(HERE, "long-share-probe.tsv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["qid", "query", "scheme", "hits", "long_share10"],
                           delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("随机查询 %d 条，每方案 %d 行" % (a.random, len(rows) // len(SCHEMES)))
    all_books = set(paras)
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    ch_map = ch
    long_books = [b for b in all_books if ch_map.get(b, 0) >= 500]
    print("基准  书数 %.4f  章数 %.4f  段数 %.4f" % (
        len(long_books) / len(all_books),
        sum(chapters.get(b, 0) for b in long_books) / sum(chapters.values()),
        sum(paras.get(b, 0) for b in long_books) / sum(paras.values())))
    for scheme in SCHEMES:
        xs = [float(r["long_share10"]) for r in rows if r["scheme"] == scheme]
        xs.sort()
        print("%-20s 均值 %.4f  p5 %.2f  中位 %.2f  p95 %.2f" % (
            scheme, sum(xs) / len(xs), xs[int(0.05 * (len(xs) - 1))],
            xs[len(xs) // 2], xs[int(0.95 * (len(xs) - 1))]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
