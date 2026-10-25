#!/usr/bin/env python3
"""书级 gold 的分层评测：八种书级方案在 30 道 pilot 上的名次、长书占比与证据章。

  book_eval.py --validate   只校验 book_gold.tsv
  book_eval.py              跑评测，写 eval-book.tsv / summary.tsv / strata.tsv / lane-*.tsv
  book_eval.py --perf       30 题上交错跑旧 qsearch --book 路径与新书级路径，比 p95

口径（写死在这里，报告与表格共用）：

  book_rank      可接受书里第一本的名次（1 起；不在候选记 0）
  hit@10/@20     名次落在 10 / 20 内
  mrr            1/名次，没进候选记 0（不按截断名次算，见踩坑记录 C 类）
  ev_rank        可接受章在全库章级名次里的名次（同章去重，取该章最好段）
  ev_hit@10      ev_rank 落在 10 内
  in_book_rank   可接受章在目标书内部的名次（按章最好段排）
  in_book_top3   in_book_rank 落在 3 内，B3 的「每本返回最多三章」就是拿它比
  long_share@10  top10 里 500 章以上书的占比，除以 10 后按题平均
  best_ok        目标书自己最好的章是不是可接受章

long_share@10 报两个口径。`all` 是全部题合并算，`nonlong` 只算 short 与 ordinary 两层。
长书层每题的目标书本身就 ≥500 章，合并口径有地板，所以门槛按 `nonlong` 读。

门槛基准（2026-10-22 操作人定）：段数占比，即长书占全库段数的比例，去掉 1.5 倍系数。
书数占比与章数占比继续打印，只当诊断。

只读 index.db 与 library.db。
"""
import argparse
import collections
import csv
import math
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(ROOT, "工作区", "脚本")
sys.path.insert(0, SCRIPTS)
import book_score
import lib_fts_cjk as cjk
import qsearch

GOLD = os.path.join(HERE, "book_gold.tsv")
DB = book_score.DB
LIBDB = book_score.LIBDB
STRATA = ("long", "short", "ordinary")
SCHEME_ORDER = ["sum", "mean", "best", "mix", "best_support", "best_top3_support",
                "rrf5", "best_support_length"]


def read_gold(path=GOLD):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def validate(rows, con):
    import build_book_gold
    return build_book_gold.check(rows, con)


def acceptable(row):
    books = row["target_book"].split("|")
    chaps = set(row["target_chapters"].split("|"))
    per_book = {b: {c for c in chaps if c.startswith("库/%s/" % b)} for b in books}
    return books, chaps, per_book


def chapter_order(hits):
    best = {}
    for r in hits:
        cur = best.get(r["path"])
        if cur is None or (-r["score"], r["path"], r["line"]) < \
                (-cur["score"], cur["path"], cur["line"]):
            best[r["path"]] = r
    return sorted(best.values(), key=lambda r: (-r["score"], r["path"], r["line"]))


def search_hits(con, terms, pool=2000):
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    return qsearch.search(con, expr, terms, [], 2000, 40, False, pool)


def type_counts(rows):
    c = collections.Counter(b.type for b in rows)
    return " ".join("%s%d" % (k, v) for k, v in sorted(c.items()))


def run(rows, con, meta, expand):
    out, hitstats = [], []
    for row in rows:
        terms = [t.strip() for t in row["terms"].split(",") if t.strip()]
        books, chaps, per_book = acceptable(row)
        hits, ms = search_hits(con, terms)
        hitstats.append({"qid": row["qid"], "stratum": row["stratum"],
                         "terms": ",".join(terms), "hits": len(hits)})
        order = chapter_order(hits)
        chap_rank = {r["path"]: i for i, r in enumerate(order, 1)}
        ev_paths = [c for c in chaps if c in chap_rank]
        ev_rank = min((chap_rank[c] for c in ev_paths), default=0)
        for scheme in SCHEME_ORDER:
            ranked = book_score.rank(hits, terms, scheme, [], meta, expand)
            rank = 0
            for i, b in enumerate(ranked, 1):
                if b.book in books:
                    rank = i
                    break
            top10 = ranked[:10]
            target = ranked[rank - 1] if rank else None
            in_book = 0
            best_ok = 0
            best_chapter = ""
            if target:
                own = per_book.get(target.book, set())
                bchap = chapter_order([r for r in hits if r["book"] == target.book])
                for i, r in enumerate(bchap, 1):
                    if r["path"] in own:
                        in_book = i
                        break
                if bchap:
                    best_chapter = bchap[0]["path"]
                    best_ok = int(bchap[0]["path"] in own)
            out.append({
                "qid": row["qid"], "stratum": row["stratum"], "scheme": scheme,
                "rank": rank, "hit10": int(0 < rank <= 10), "hit20": int(0 < rank <= 20),
                "mrr": round(1.0 / rank, 6) if rank else 0.0,
                "ev_rank": ev_rank, "ev10": int(0 < ev_rank <= 10),
                "in_book_rank": in_book,
                "in_book_top3": int(0 < in_book <= 3),
                "best_chapter": best_chapter, "best_ok": best_ok,
                "single_book": int(bool(target and target.chapters == 1)),
                "top10_long": sum(1 for b in top10 if b.chapters >= 500),
                "top10_types": type_counts(top10),
                "hits": len(hits),
            })
    return out, hitstats


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def metrics(rows):
    n = len(rows)
    return {
        "n": n,
        "hit10": mean([r["hit10"] for r in rows]),
        "hit20": mean([r["hit20"] for r in rows]),
        "mrr": mean([r["mrr"] for r in rows]),
        "ev10": mean([r["ev10"] for r in rows]),
        "in_book_top3": mean([r["in_book_top3"] for r in rows]),
        "long_share10": mean([r["top10_long"] for r in rows]) / 10.0,
        "single_n": len([r for r in rows if r.get("single_book")]),
        "single_hit10": mean([r["hit10"] for r in rows if r.get("single_book")]),
    }


def share_of(con, order):
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    ch = dict(lib.execute(
        "SELECT b.path, d.chapters FROM book b JOIN derived d ON d.uid=b.uid "
        "AND d.kind='md' WHERE d.chapters > 0"))
    if order == "chapters":
        totals = dict(con.execute("SELECT book, count(DISTINCT path) FROM para GROUP BY book"))
    else:
        totals = dict(con.execute("SELECT book, count(*) FROM para GROUP BY book"))
    books = set(totals)
    long_books = [b for b in books if ch.get(b, 0) >= 500]
    denom = sum(totals.values())
    return sum(totals.get(b, 0) for b in long_books) / max(1, denom)


def chapter_share(con):
    return share_of(con, "chapters")


def para_share(con):
    return share_of(con, "paras")


def baseline_share(con):
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    ch = dict(lib.execute(
        "SELECT b.path, d.chapters FROM book b JOIN derived d ON d.uid=b.uid "
        "AND d.kind='md' WHERE d.chapters > 0"))
    books = {r[0] for r in con.execute("SELECT DISTINCT book FROM para")}
    long_books = sum(1 for b in books if ch.get(b, 0) >= 500)
    return long_books / max(1, len(books)), long_books, len(books)


def summarize(per, base_share, para_base):
    summary = []
    for scheme in SCHEME_ORDER:
        rs = [r for r in per if r["scheme"] == scheme]
        s = {"scheme": scheme, **metrics(rs)}
        for st in STRATA:
            m = metrics([r for r in rs if r["stratum"] == st])
            s["%s_hit10" % st] = m["hit10"]
            s["%s_ev10" % st] = m["ev10"]
        s["long_share10_all"] = s.pop("long_share10")
        s["long_share10_nonlong"] = mean(
            [r["top10_long"] for r in rs if r["stratum"] != "long"]) / 10.0
        avg = collections.Counter()
        for r in rs:
            for part in r["top10_types"].split():
                name = part.rstrip("0123456789")
                avg[name] += int(part[len(name):] or 0)
        s["top10_types"] = " ".join("%s%.1f" % (k, v / len(rs))
                                    for k, v in sorted(avg.items()))
        summary.append(s)
    mix = next(s for s in summary if s["scheme"] == "mix")
    for s in summary:
        g1 = s["hit10"] >= mix["hit10"] - 1e-9
        g2 = s["single_hit10"] >= 0.80 or s["single_hit10"] >= mix["single_hit10"] + 0.10
        g3 = s["long_share10_nonlong"] <= para_base
        g4 = s["ordinary_hit10"] >= mix["ordinary_hit10"] - 0.05
        s["gate_hit10"] = int(g1)
        s["gate_single"] = int(g2)
        s["gate_long_share"] = int(g3)
        s["gate_ordinary"] = int(g4)
        s["gate_pass"] = int(g1 and g2 and g3 and g4)
    return summary


def write_tsv(path, cols, rows):
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".tmp", path)


def perf(rows, con, meta, expand):
    out = []
    for i, row in enumerate(rows):
        terms = [t.strip() for t in row["terms"].split(",") if t.strip()]
        expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))

        def base():
            hits, _ = qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)
            return qsearch.rollup_by_book(hits, con, 20, "mix")

        def cur():
            hits, _ = qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)
            return book_score.rank(hits, terms, "best_support", [], meta, expand)[:20]

        steps = [("base", base), ("cur", cur)]
        if i % 2:
            steps.reverse()
        spent = {}
        for tag, fn in steps:
            t0 = time.perf_counter()
            fn()
            spent[tag] = round((time.perf_counter() - t0) * 1000, 1)
        out.append({"qid": row["qid"], "order": steps[0][0],
                    "base_ms": spent["base"], "cur_ms": spent["cur"]})
    return out


def pct(xs, p):
    s = sorted(xs)
    k = int(round(p / 100.0 * (len(s) - 1)))
    return s[max(0, min(len(s) - 1, k))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--gold", default=GOLD)
    ap.add_argument("--outdir", default=HERE)
    a = ap.parse_args()
    rows = read_gold(a.gold)
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    if a.validate:
        return validate(rows, con)
    meta = book_score.load_meta(LIBDB)
    expand = book_score.load_expand(con)
    if a.perf:
        per = perf(rows, con, meta, expand)
        write_tsv(os.path.join(a.outdir, "perf.tsv"),
                  ["qid", "order", "base_ms", "cur_ms"], per)
        base = [r["base_ms"] for r in per]
        cur = [r["cur_ms"] for r in per]
        print("题数 %d，交错顺序按题号奇偶" % len(per))
        print("旧 search+rollup_by_book(mix)  p50 %.1f ms  p95 %.1f ms"
              % (pct(base, 50), pct(base, 95)))
        print("新 search+rank(best_support)   p50 %.1f ms  p95 %.1f ms"
              % (pct(cur, 50), pct(cur, 95)))
        ok = pct(cur, 95) <= 2 * pct(base, 95) and pct(cur, 95) <= 2000
        print("规则 新 p95 <= 2 倍旧 p95 且 <= 2000 ms: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    per, hitstats = run(rows, con, meta, expand)
    base_share, long_books, all_books = baseline_share(con)
    summary = summarize(per, base_share, para_share(con))
    write_tsv(os.path.join(a.outdir, "hits.tsv"),
              ["qid", "stratum", "terms", "hits"], hitstats)
    cols = ["qid", "stratum", "scheme", "rank", "hit10", "hit20", "mrr", "ev_rank",
            "ev10", "in_book_rank", "in_book_top3", "best_chapter", "best_ok",
            "top10_long", "top10_types", "hits"]
    write_tsv(os.path.join(a.outdir, "eval-book.tsv"), cols, per)
    write_tsv(os.path.join(a.outdir, "lane-1.tsv"), cols, per)
    for lane, st in (("lane-2", "long"), ("lane-3", "short"), ("lane-4", "ordinary")):
        write_tsv(os.path.join(a.outdir, lane + ".tsv"), cols,
                  [r for r in per if r["stratum"] == st])
    l5 = [{"qid": r["qid"], "stratum": r["stratum"], "scheme": r["scheme"],
           "book_rank": r["rank"], "best_chapter": r["best_chapter"],
           "best_ok": r["best_ok"], "in_book_rank": r["in_book_rank"],
           "ev_rank": r["ev_rank"]} for r in per]
    write_tsv(os.path.join(a.outdir, "lane-5.tsv"),
              ["qid", "stratum", "scheme", "book_rank", "best_chapter", "best_ok",
               "in_book_rank", "ev_rank"], l5)
    scols = ["scheme", "n", "hit10", "hit20", "mrr", "ev10", "in_book_top3",
             "long_hit10", "short_hit10", "ordinary_hit10", "long_ev10", "short_ev10",
             "ordinary_ev10", "long_share10_all", "long_share10_nonlong", "top10_types",
             "single_n", "single_hit10", "gate_hit10", "gate_single", "gate_long_share",
             "gate_ordinary", "gate_pass"]
    write_tsv(os.path.join(a.outdir, "summary.tsv"), scols, summary)
    srows = []
    for scheme in SCHEME_ORDER:
        for st in STRATA:
            m = metrics([r for r in per if r["scheme"] == scheme and r["stratum"] == st])
            srows.append({"scheme": scheme, "stratum": st, **m})
    lshare = []
    for scheme in SCHEME_ORDER:
        for st in STRATA:
            rs = [r for r in per if r["scheme"] == scheme and r["stratum"] == st]
            lshare.append({"scheme": scheme, "stratum": st,
                           "long_share10": round(mean([r["top10_long"] for r in rs]) / 10.0, 4)})
    lshare.append({"scheme": "baseline", "stratum": "books",
                   "long_share10": round(base_share, 4)})
    lshare.append({"scheme": "baseline", "stratum": "chapters",
                   "long_share10": round(chapter_share(con), 4)})
    lshare.append({"scheme": "baseline", "stratum": "paras",
                   "long_share10": round(para_share(con), 4)})
    write_tsv(os.path.join(a.outdir, "long-share.tsv"),
              ["scheme", "stratum", "long_share10"], lshare)
    write_tsv(os.path.join(a.outdir, "strata.tsv"),
              ["scheme", "stratum", "n", "hit10", "hit20", "mrr", "ev10",
               "in_book_top3", "long_share10"], srows)
    print("全库基准 long_share  书数 %d/%d = %.4f  章数 %.4f  段数 %.4f（门槛用段数）"
          % (long_books, all_books, base_share, chapter_share(con), para_share(con)))
    print("%-20s %6s %6s %6s %6s %6s %6s %6s %6s  %s" % (
        "scheme", "hit10", "hit20", "mrr", "ev10", "short", "ordinary", "long%",
        "single(n)", "gate"))
    for s in summary:
        print("%-20s %6.3f %6.3f %6.3f %6.3f %6.3f %6.3f %6.3f  %d/%.3f  %s" % (
            s["scheme"], s["hit10"], s["hit20"], s["mrr"], s["ev10"],
            s["short_hit10"], s["ordinary_hit10"], s["long_share10_nonlong"],
            s["single_n"], s["single_hit10"],
            "PASS" if s["gate_pass"] else "fail %s" % "".join(
                k for k, v in (("H", s["gate_hit10"]), ("S", s["gate_single"]),
                               ("L", s["gate_long_share"]), ("O", s["gate_ordinary"]))
                if not v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
