#!/usr/bin/env python3
"""随机 100 章：只问一次 Jev Choice，估计全库 gap 率与单价。

  python3 random_choice.py --build
  python3 random_choice.py --run --workers 2
  python3 random_choice.py --analyze
"""
import argparse
import csv
import json
import os
import random
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import select_experiment as se  # noqa: E402

SAMPLE = os.path.join(HERE, "random-sample.tsv")
RAW = os.path.join(HERE, "random-union-raw.jsonl")
SUMMARY = os.path.join(HERE, "random-union-summary.json")
NONE = "无"
BROAD = [b[0] for b in se.BROAD]


def build(n=100):
    concepts = se.load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % se.IDX, uri=True)
    top_map = se.book_top(con, 10)
    rows = []
    for path, text in se.prefix_rows():
        if not path.startswith("库/"):
            continue
        book = path[len("库/"):].split("/md/")[0]
        if not top_map.get(book):
            continue
        rows.append({"path": path, "book": book, "chapter": os.path.basename(path),
                     "prefix": text})
    rnd = random.Random(20261022)
    picked = rnd.sample(rows, min(n, len(rows)))
    with open(SAMPLE, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["path", "book", "chapter", "prefix"])
        for r in picked:
            w.writerow([r["path"], r["book"], r["chapter"], r["prefix"]])
    print("random sample %d -> %s" % (len(picked), SAMPLE))


def candidates(book, text, top_map, concepts):
    names = [k for k in top_map.get(book, []) if k in concepts]
    low = text.lower()
    names += [k for k, meta in concepts.items()
              if any(t.lower() in low for t in [k] + meta["aliases"])]
    names += BROAD
    seen, out = set(), []
    for k in names:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return (out or BROAD)[:20]


def run(workers):
    key = se.api_key()
    concepts = se.load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % se.IDX, uri=True)
    top_map = se.book_top(con, 10)
    with open(SAMPLE, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    done = set()
    if os.path.isfile(RAW):
        for line in open(RAW, encoding="utf-8"):
            if line.strip():
                done.add(json.loads(line)["path"])
    todo = [r for r in rows if r["path"] not in done]
    print("待跑 %d / %d 章" % (len(todo), len(rows)))

    def one(chunk):
        state = [{"书名": r["book"], "章标题": r["chapter"], "前缀": r["prefix"]}
                 for r in chunk]
        qs = {}
        for j, r in enumerate(chunk):
            cands = candidates(r["book"], r["prefix"], top_map, concepts)
            crit = {}
            for k in cands:
                meta = concepts.get(k, {"note": dict(se.BROAD).get(k, "")})
                crit[k] = meta.get("note") or None
            crit[NONE] = "前缀没有点到上述任何概念"
            qs["c%d" % j] = {"type": "choice",
                             "instructions": "这条前缀主要点到哪一个概念？如果都没有，选「无」。",
                             "criteria": crit}
        return chunk, se.ask(state, qs, key)

    out = open(RAW, "a", encoding="utf-8")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, todo[i:i + 10]) for i in range(0, len(todo), 10)]
        for n, fut in enumerate(as_completed(futs), 1):
            chunk, resp = fut.result()
            for j, r in enumerate(chunk):
                out.write(json.dumps({"path": r["path"], "choice": resp["answers"]["c%d" % j],
                                      "usage": resp.get("usage", {}),
                                      "model": resp.get("model")}, ensure_ascii=False) + "\n")
            out.flush()
            print("  %d/%d 批  %.0fs" % (n, len(futs), time.time() - t0), file=sys.stderr)
    out.close()


def analyze():
    concepts = se.load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % se.IDX, uri=True)
    top_map = se.book_top(con, 10)
    sample = {r["path"]: r for r in csv.DictReader(open(SAMPLE, encoding="utf-8-sig"), delimiter="\t")}
    recs = {}
    tokens = 0
    for line in open(RAW, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        recs[rec["path"]] = rec
        tokens += int(rec.get("usage", {}).get("input_tokens", 0) or 0)
    n = len(recs)
    gap = 0
    by = {"token126_gap": 0, "tokenbook_gap": 0, "broad_gap": 0, "union_gap": 0}
    for path, rec in recs.items():
        r = sample[path]
        text = r["prefix"]
        book = r["book"]
        cands = candidates(book, text, top_map, concepts)
        ch = rec["choice"]
        sel = ch.get("choice")
        prob = (ch.get("probabilities") or {}).get(sel, 0.0)
        if sel == NONE or prob < 0.5:
            gap += 1
        low = text.lower()
        def hit(terms):
            return any(t.lower() in low for t in terms)
        all126 = [t for k, meta in concepts.items() for t in [k] + meta["aliases"]]
        book_terms = [t for k in cands for t in [k] + concepts.get(k, {"aliases": []})["aliases"]]
        if not hit(all126):
            by["token126_gap"] += 1
        if not hit(book_terms):
            by["tokenbook_gap"] += 1
        if not hit(BROAD):
            by["broad_gap"] += 1
        if not hit(all126 + book_terms + BROAD):
            by["union_gap"] += 1
    summary = {"n": n, "tokens": tokens,
               "tokens_per_chapter": round(tokens / n, 0) if n else 0,
               "cost_usd": round(tokens * 0.042 / 1_000_000, 3),
               "cost_full_84k": round(tokens / n * 84086 * 0.042 / 1_000_000, 2) if n else 0,
               "jev_choice_gap": gap, "jev_choice_gap_pct": round(100 * gap / n, 1),
               **{k: round(100 * v / n, 1) for k, v in by.items()}}
    with open(SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if a.build:
        build()
    if a.run:
        run(a.workers)
    if a.analyze:
        analyze()
    if not (a.build or a.run or a.analyze):
        ap.error("要 --build / --run / --analyze")


if __name__ == "__main__":
    main()
