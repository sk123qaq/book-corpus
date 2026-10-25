#!/usr/bin/env python3
"""查询词变体探针：全词 vs 只留名词 vs 只留名词并做同段共现过滤。

  probe_terms.py           写 term-variants.tsv 并打汇总
"""
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import book_readme
import book_score
import lib_fts_cjk as cjk
import qsearch

GOLD = os.path.join(HERE, "book_gold.tsv")
OUT = os.path.join(HERE, "term-variants.tsv")
VERBS = {"收拾", "吞", "护道", "考不出", "厌恶", "逃走", "批判", "检查", "恐吓",
         "发烧", "贬低", "参劾", "发芽", "限制", "传染"}
COLS = ["qid", "stratum", "variant", "book_rank", "chap_hit", "in_book_rank",
        "hits", "filt_hits"]


def variants(row):
    terms = [t.strip() for t in row["terms"].split(",") if t.strip()]
    noun = [t for t in terms if t not in VERBS] or terms
    return terms, noun


def search(con, terms):
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    return qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)[0]


def chap_rank(hits, book, chaps):
    best = {}
    for r in hits:
        if r["book"] != book:
            continue
        if r["path"] not in best or r["score"] > best[r["path"]]["score"]:
            best[r["path"]] = r
    order = [r["path"] for r in sorted(best.values(), key=lambda r: (-r["score"], r["path"]))]
    for i, p in enumerate(order, 1):
        if p in chaps:
            return i
    return 0


def run():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta(book_score.LIBDB)
    expand = book_score.load_expand(con)
    out = []
    for row in rows:
        full, noun = variants(row)
        books = set(row["target_book"].split("|"))
        chaps = set(row["target_chapters"].split("|"))
        for variant, terms, and_filter in (("full", full, False), ("noun", noun, False),
                                           ("noun_and", noun, True), ("two_stage", noun, False)):
            hits = search(con, terms)
            filtered = hits
            if and_filter:
                keep = [h for h in hits if all(t in h["text"] for t in terms)]
                if len(keep) >= 5:
                    filtered = keep
            ranked = book_score.rank(filtered, terms, "best_support", [], meta, expand)
            rank = next((i for i, b in enumerate(ranked, 1) if b.book in books), 0)
            chap_hits = search(con, full) if variant == "two_stage" else filtered
            ib = chap_rank(chap_hits, next(iter(books)), chaps) if rank else 0
            out.append({"qid": row["qid"], "stratum": row["stratum"], "variant": variant,
                        "book_rank": rank, "chap_hit": int(0 < ib <= 3),
                        "in_book_rank": ib, "hits": len(hits), "filt_hits": len(filtered)})
    with open(OUT + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    os.replace(OUT + ".tmp", OUT)
    for variant in ("full", "noun", "noun_and", "two_stage"):
        rs = [r for r in out if r["variant"] == variant]
        hit10 = sum(1 for r in rs if 0 < r["book_rank"] <= 10) / len(rs)
        hit20 = sum(1 for r in rs if 0 < r["book_rank"] <= 20) / len(rs)
        avg = sum(min(r["book_rank"], 60) if r["book_rank"] else 60 for r in rs) / len(rs)
        print("%-9s 书 hit@10 %.3f  hit@20 %.3f  平均名次(未中按 60) %.1f  "
              "目标书前 3 章含可接受章 %d/60  平均候选段 %d" % (
                  variant, hit10, hit20, avg, sum(r["chap_hit"] for r in rs),
                  sum(r["filt_hits"] for r in rs) / len(rs)))
    print("\n逐题变化（只在 full 与 noun 或 noun_and 不同时列出）：")
    by = {}
    for r in out:
        by.setdefault(r["qid"], {})[r["variant"]] = r
    for qid in sorted(by):
        f, n, a, sp = (by[qid]["full"], by[qid]["noun"], by[qid]["noun_and"],
                       by[qid]["two_stage"])
        if (f["book_rank"], f["in_book_rank"]) != (n["book_rank"], n["in_book_rank"]) or \
                (f["book_rank"], f["in_book_rank"]) != (a["book_rank"], a["in_book_rank"]):
            print("  %-5s full %3d/%3d  noun %3d/%3d  noun_and %3d/%3d  两段 %3d/%3d" % (
                qid, f["book_rank"], f["in_book_rank"], n["book_rank"],
                n["in_book_rank"], a["book_rank"], a["in_book_rank"],
                sp["book_rank"], sp["in_book_rank"]))
    return 0


if __name__ == "__main__":
    sys.exit(run())
