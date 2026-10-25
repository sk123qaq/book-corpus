#!/usr/bin/env python3
"""审计 B6 性能基线：qsearch --book 旧路径 vs book_query mix vs 默认。只读。"""
import csv
import os
import sqlite3
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_cards as BC
import book_query as BQ
import book_score as BS
import lib_fts_cjk as cjk
import qsearch

OUT = os.path.join(HERE, "live", "B6", "audit-perf-paths.tsv")


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(0.95 * (len(xs) - 1)))))]


def main():
    con = sqlite3.connect("file:%s?mode=ro" % BS.DB, uri=True)
    meta = BS.load_meta()
    exp = BS.load_expand(con)
    rows = BC.gold_rows()
    out = []

    def run_q(terms):
        t0 = time.time()
        expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
        hits, _ = qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)
        qsearch.rollup_by_book(hits, con, 5, "mix")
        return (time.time() - t0) * 1000

    def run_mix(terms):
        t0 = time.time()
        p = BQ.Planner(con, meta, exp, scheme="mix", limit=5)
        p.plan(terms, chapters=True)
        return (time.time() - t0) * 1000

    def run_def(terms, question):
        t0 = time.time()
        p = BQ.Planner(con, meta, exp, scheme="best_support", limit=5)
        res = p.plan(terms, chapters=True)
        cs, _, _, _ = BC.cards_for(p, terms, question, 6)
        if cs.trigger:
            keep = set(cs.default_selection)
            res.chapters = [b for b in res.chapters if b["book"] in keep]
        return (time.time() - t0) * 1000

    for pnum in (1, 2):
        for i, row in enumerate(rows):
            terms = row["terms"]
            fns = [("qsearch_mix", lambda: run_q(terms)),
                   ("book_query_mix", lambda: run_mix(terms)),
                   ("default", lambda: run_def(terms, row["question"]))]
            if (pnum + i) % 2:
                fns = fns[::-1]
            vals = {}
            for name, fn in fns:
                vals[name] = fn()
            out.append({"pass": pnum, "qid": row["qid"],
                        **{k: round(vals[k], 1) for k in
                           ("qsearch_mix", "book_query_mix", "default")}})
    con.close()
    with open(OUT + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]), delimiter="\t",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    os.replace(OUT + ".tmp", OUT)
    print("n", len(out))
    for pnum in (1, 2):
        print("pass", pnum)
        for k in ("qsearch_mix", "book_query_mix", "default"):
            xs = [r[k] for r in out if r["pass"] == pnum]
            print("  %-14s p50 %.0f p95 %.0f max %.0f"
                  % (k, statistics.median(xs), p95(xs), max(xs)))
        d = [r["default"] for r in out if r["pass"] == pnum]
        q = [r["qsearch_mix"] for r in out if r["pass"] == pnum]
        m = [r["book_query_mix"] for r in out if r["pass"] == pnum]
        print("  default/qsearch_mix p95 ratio %.2f" % (p95(d) / p95(q)))
        print("  default/book_query_mix p95 ratio %.2f" % (p95(d) / p95(m)))
    print("written", OUT)


if __name__ == "__main__":
    main()
