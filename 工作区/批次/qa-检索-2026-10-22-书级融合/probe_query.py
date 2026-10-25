#!/usr/bin/env python3
"""B2.5 读数：full（现状） / two_stage（两段，1 轮） / loop（词法循环）在 60 题 gold 与 40 题宽词层 heldout 上。

  probe_query.py --gold
  probe_query.py --heldout --broad
  probe_query.py --gold --passes 2

只读 index.db 与 library.db。读数写 live/B2.5/。
"""
import argparse
import csv
import os
import statistics
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_query  # noqa: E402
import book_score  # noqa: E402
import lib_fts_cjk as cjk  # noqa: E402
import qsearch  # noqa: E402

GOLD = os.path.join(HERE, "book_gold.tsv")
HELDOUT = os.path.join(os.path.dirname(HERE), "qa-检索-2026-10-22-宽词层", "heldout.tsv")
OUTDIR = os.path.join(HERE, "live", "B2.5")


def read_tsv(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def gold_rows(path=GOLD):
    rows = []
    for r in read_tsv(path):
        books = r["target_book"].split("|")
        chaps = set(r["target_chapters"].split("|"))
        per_book = {b: {c for c in chaps if c.startswith("库/%s/" % b)} for b in books}
        rows.append({"qid": r["qid"], "stratum": r["stratum"],
                     "terms": [t.strip() for t in r["terms"].split(",") if t.strip()],
                     "books": books, "chaps": chaps, "per_book": per_book,
                     "question": r["question"]})
    return rows


def heldout_rows(path=HELDOUT):
    rows = []
    for r in read_tsv(path):
        target = r["path"]
        book = target.split("/md/")[0]
        if book.startswith("库/"):
            book = book[2:]
        rows.append({"qid": r["qid"], "stratum": "heldout", "broad": r["broad"],
                     "terms": [t.strip() for t in r["literal"].split(",") if t.strip()],
                     "books": [book], "chaps": {target}, "per_book": {book: {target}},
                     "question": r["question"]})
    return rows


def alt_rows(path=None):
    """旧 60 题 gold（qa-检索-2026-10-19）：词集里动作短语多，用来补查名词划分的迁移性。"""
    path = path or os.path.join(os.path.dirname(HERE), "qa-检索-2026-10-19", "gold.tsv")
    rows = []
    for r in read_tsv(path):
        target = r["path"]
        book = target.split("/md/")[0]
        if book.startswith("库/"):
            book = book[2:]
        rows.append({"qid": r["qid"], "stratum": r["category"], "broad": "",
                     "terms": [t.strip() for t in r["terms"].split(",") if t.strip()],
                     "books": [book], "chaps": {target}, "per_book": {book: {target}},
                     "question": r["question"]})
    return rows


def chapter_order(hits, book):
    """同章去重取最好段，按 (-score, path)。与 B2 的证据章口径一致。"""
    best = {}
    for r in hits:
        if r["book"] != book:
            continue
        cur = best.get(r["path"])
        if cur is None or (-r["score"], r["path"], r["line"]) < \
                (-cur["score"], cur["path"], cur["line"]):
            best[r["path"]] = r
    return sorted(best.values(), key=lambda r: (-r["score"], r["path"], r["line"]))


def eval_question(row, mode, con, meta, expand, planner, limit=10):
    terms = row["terms"]
    t0 = time.time()
    hits = None
    rounds = 0
    stop = ""
    fetches = 0
    if mode == "full":
        hits, _ = book_query.Planner(con, meta, expand).search(terms)
        ranking = book_score.rank(hits, terms, "best_support", [], meta, expand)
        fetches = 1
    elif mode == "two_stage":
        p = book_query.Planner(con, meta, expand)
        nouns = book_query.split_terms(terms, p.actions)
        nhits, _ = p.search(nouns)
        ranking = book_score.rank(nhits, nouns, "best_support", [], meta, expand)
        hits, _ = p.search(terms)
        fetches = p.fetches
        rounds = 1
        stop = "two_stage"
    else:
        p = planner
        plan = p.plan(terms, broad=row.get("broad_mode", False), chapters=False,
                      max_rounds=5)
        ranking = plan.ranking
        hits, _ = p.search(terms)
        rounds = len(plan.rounds)
        stop = plan.stop
        fetches = p.fetches
    ms = (time.time() - t0) * 1000
    rank = 0
    for i, b in enumerate(ranking, 1):
        if b.book in row["books"]:
            rank = i
            break
    top1 = ranking[0].book if ranking else ""
    # 章：在可接受书里按全词命中排（B2 口径），与循环轮数无关
    in_book = 0
    ev_book = ""
    for b in row["books"]:
        own = row["per_book"].get(b) or set()
        chap = chapter_order(hits, b)
        for i, r in enumerate(chap, 1):
            if r["path"] in own:
                in_book = i if not in_book else min(in_book, i)
                ev_book = b
                break
    # 端到端：返回的 top1 书与它自己的前三章
    e2e = 0
    e2e_chaps = []
    if top1 in row["books"]:
        own = row["per_book"].get(top1) or set()
        sel = [r["path"] for r in chapter_order(hits, top1)[:3]]
        e2e_chaps = sel
        e2e = int(any(p in own for p in sel))
    return {"qid": row["qid"], "stratum": row["stratum"], "mode": mode,
            "terms": ",".join(terms), "book_rank": rank,
            "hit10": int(0 < rank <= 10), "hit20": int(0 < rank <= 20),
            "mrr": round(1.0 / rank, 6) if rank else 0.0,
            "in_book_rank": in_book, "in_book_top3": int(0 < in_book <= 3),
            "top1": top1, "e2e": e2e, "e2e_chaps": "|".join(e2e_chaps),
            "rounds": rounds, "stop": stop, "fetches": fetches, "ms": round(ms, 1)}


def pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[k]


def summarize(rows, label):
    out = []
    for mode in ("full", "two_stage", "loop"):
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        stops = {}
        for r in rs:
            stops[r["stop"]] = stops.get(r["stop"], 0) + 1
        ms = [r["ms"] for r in rs]
        out.append({
            "set": label, "mode": mode, "n": len(rs),
            "hit10": round(sum(r["hit10"] for r in rs) / len(rs), 3),
            "hit20": round(sum(r["hit20"] for r in rs) / len(rs), 3),
            "mrr": round(sum(r["mrr"] for r in rs) / len(rs), 3),
            "in_book_top3": sum(r["in_book_top3"] for r in rs),
            "e2e": sum(r["e2e"] for r in rs),
            "avg_rounds": round(sum(r["rounds"] for r in rs) / len(rs), 2),
            "avg_fetches": round(sum(r["fetches"] for r in rs) / len(rs), 2),
            "p50_ms": round(statistics.median(ms), 1),
            "p95_ms": round(pct(ms, 0.95), 1),
            "cap": stops.get("cap", 0), "stop": ",".join(
                "%s=%d" % (k, v) for k, v in sorted(stops.items())),
        })
    return out


def perf(rows, con, meta, expand, passes=2, limits=(1, 5), outdir=OUTDIR):
    """端到端延迟：含选章与 README 解析。每遍 60 题，逐题新 Planner。"""
    out = []
    for limit in limits:
        for p in range(passes):
            for i, row in enumerate(rows):
                p2 = book_query.Planner(con, meta, expand, limit=limit)
                t0 = time.time()
                plan = p2.plan(row["terms"], chapters=True, max_chapters=3)
                ms = (time.time() - t0) * 1000
                out.append({"pass": p + 1, "limit": limit, "qid": row["qid"],
                            "ms": round(ms, 1), "rounds": len(plan.rounds),
                            "fetches": p2.fetches, "stop": plan.stop,
                            "cold": int(i == 0 and p == 0)})
    write_tsv(os.path.join(outdir, "perf.tsv"), out,
              ["pass", "limit", "qid", "ms", "rounds", "fetches", "stop", "cold"])
    for limit in limits:
        for p in range(passes):
            xs = [r["ms"] for r in out if r["limit"] == limit and r["pass"] == p + 1]
            cold = [r["ms"] for r in out if r["limit"] == limit and r["pass"] == p + 1
                    and r["cold"]]
            print("perf limit=%d pass=%d n=%d p50 %.0f ms p95 %.0f ms max %.0f ms%s"
                  % (limit, p + 1, len(xs), statistics.median(xs), pct(xs, 0.95), max(xs),
                     (" 首题 %.0f ms" % cold[0]) if cold else ""))
    return out


def write_tsv(path, rows, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".tmp", path)


def run_set(rows, con, meta, expand, broad=False, passes=1, limit=10):
    out = []
    for row in rows:
        row["broad_mode"] = broad
        for mode in ("full", "two_stage", "loop"):
            planner = book_query.Planner(con, meta, expand, limit=limit)
            out.append(eval_question(row, mode, con, meta, expand, planner, limit))
    return out


def run_one(tag, rows, con, meta, expand, broad, passes, limit, outdir):
    out = []
    for p in range(passes):
        got = run_set(rows, con, meta, expand, broad, passes, limit)
        pt = tag if passes == 1 else "%s-p%d" % (tag, p + 1)
        write_tsv(os.path.join(outdir, "per-question-%s.tsv" % pt), got,
                  ["qid", "stratum", "mode", "terms", "book_rank", "hit10", "hit20",
                   "mrr", "in_book_rank", "in_book_top3", "top1", "e2e",
                   "e2e_chaps", "rounds", "stop", "fetches", "ms"])
        out += summarize(got, pt)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", action="store_true")
    ap.add_argument("--heldout", action="store_true")
    ap.add_argument("--alt", action="store_true", help="旧 60 题 gold 的迁移性补查")
    ap.add_argument("--broad", action="store_true", help="循环第 4 轮加宽词组")
    ap.add_argument("--perf", action="store_true", help="端到端延迟（含选章）")
    ap.add_argument("--all", action="store_true", help="gold 两遍 + alt + heldout + heldout-broad")
    ap.add_argument("--passes", type=int, default=1)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--out", default=OUTDIR)
    a = ap.parse_args(argv)
    if not (a.gold or a.heldout or a.alt or a.perf or a.all):
        ap.error("要 --gold / --heldout / --alt / --perf / --all")
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta()
    expand = book_score.load_expand(con)
    os.makedirs(a.out, exist_ok=True)
    if a.perf:
        perf(gold_rows(), con, meta, expand, max(2, a.passes), outdir=a.out)
        return 0
    rows_out = []
    if a.all:
        rows_out += run_one("gold", gold_rows(), con, meta, expand, False, 2, a.limit, a.out)
        rows_out += run_one("alt", alt_rows(), con, meta, expand, False, 1, a.limit, a.out)
        rows_out += run_one("heldout", heldout_rows(), con, meta, expand, False, 1, a.limit, a.out)
        rows_out += run_one("heldout-broad", heldout_rows(), con, meta, expand, True, 1,
                            a.limit, a.out)
    else:
        if a.gold:
            rows_out += run_one("gold", gold_rows(), con, meta, expand, False,
                                max(1, a.passes), a.limit, a.out)
        if a.alt:
            rows_out += run_one("alt", alt_rows(), con, meta, expand, a.broad,
                                max(1, a.passes), a.limit, a.out)
        if a.heldout:
            tag = "heldout-broad" if a.broad else "heldout"
            rows_out += run_one(tag, heldout_rows(), con, meta, expand, a.broad,
                                max(1, a.passes), a.limit, a.out)
    write_tsv(os.path.join(a.out, "summary.tsv"), rows_out,
              ["set", "mode", "n", "hit10", "hit20", "mrr", "in_book_top3", "e2e",
               "avg_rounds", "avg_fetches", "p50_ms", "p95_ms", "cap", "stop"])
    for r in rows_out:
        print("%-14s %-9s n=%-3d 书 hit@10 %.3f hit@20 %.3f MRR %.3f  "
              "目标书前3章 %d/%d 端到端 %d/%d 轮 %.2f 查询 %.1f p50 %.0f ms p95 %.0f ms cap=%d  %s"
              % (r["set"], r["mode"], r["n"], r["hit10"], r["hit20"], r["mrr"],
                 r["in_book_top3"], r["n"], r["e2e"], r["n"], r["avg_rounds"],
                 r["avg_fetches"], r["p50_ms"], r["p95_ms"], r["cap"], r["stop"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
