#!/usr/bin/env python3
"""第二版选章器：候选只取书级 top10 概念，Jev 用 Choice + 章级 Noul 各一问。

  python3 select_booktop.py --run --workers 2
  python3 select_booktop.py --analyze
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import select_experiment as se  # noqa: E402

RAW = os.path.join(HERE, "booktop-raw.jsonl")
RESULTS = os.path.join(HERE, "booktop-results.tsv")
SUMMARY = os.path.join(HERE, "booktop-summary.json")
NONE = "无"


def load_sample():
    with open(se.SAMPLE, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def row_candidates(r, top_map, concepts):
    cands = [k for k in top_map.get(r["book"], []) if k in concepts]
    if not cands:
        cands = [b[0] for b in se.BROAD]
    return cands


def token_hit(text, cands, concepts):
    low = text.lower()
    for k in cands:
        meta = concepts.get(k, {"aliases": []})
        if any(t.lower() in low for t in [k] + meta["aliases"]):
            return True
    return False


def run(workers):
    key = se.api_key()
    concepts = se.load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % se.IDX, uri=True)
    top_map = se.book_top(con, 10)
    rows = load_sample()
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
            cands = row_candidates(r, top_map, concepts)
            crit = {}
            for k in cands:
                meta = concepts.get(k, {"note": dict(se.BROAD).get(k, "")})
                crit[k] = meta.get("note") or None
            crit[NONE] = "前缀没有点到上述任何概念"
            qs["c%d" % j] = {"type": "choice",
                             "instructions": "这条前缀主要点到哪一个概念？如果都没有，选「无」。",
                             "criteria": crit}
            qs["n%d" % j] = {"type": "noul",
                             "instructions": {"问": "这条前缀是否点到候选概念中的任意一个？",
                                              "候选概念": cands},
                             "criteria": {"true": "至少一个概念是前缀的主题或明确论断",
                                          "false": "候选概念一个都没有点到"}}
        return chunk, se.ask(state, qs, key)

    out = open(RAW, "a", encoding="utf-8")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, todo[i:i + 10]) for i in range(0, len(todo), 10)]
        for n, fut in enumerate(as_completed(futs), 1):
            chunk, resp = fut.result()
            for j, r in enumerate(chunk):
                out.write(json.dumps({"path": r["path"], "group": r["group"],
                                      "qid": r["qid"], "terms": r["terms"],
                                      "choice": resp["answers"]["c%d" % j],
                                      "noul": resp["answers"]["n%d" % j],
                                      "usage": resp.get("usage", {}),
                                      "model": resp.get("model")}, ensure_ascii=False) + "\n")
            out.flush()
            print("  %d/%d 批  %.0fs" % (n, len(futs), time.time() - t0), file=sys.stderr)
    out.close()


def analyze():
    concepts = se.load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % se.IDX, uri=True)
    top_map = se.book_top(con, 10)
    sample = {r["path"]: r for r in load_sample()}
    recs = {}
    tokens = 0
    for line in open(RAW, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        recs[rec["path"]] = rec
        tokens += int(rec.get("usage", {}).get("input_tokens", 0) or 0)
    rows = []
    for path, r in sample.items():
        rec = recs.get(path)
        if not rec:
            continue
        cands = row_candidates(r, top_map, concepts)
        choice = rec["choice"]
        sel = choice.get("choice")
        prob = (choice.get("probabilities") or {}).get(sel, 0.0)
        noul = rec["noul"].get("noul", 0.0)
        rows.append({"path": path, "group": r["group"], "qid": r["qid"],
                     "terms": r["terms"], "token_hit": int(token_hit(r["prefix"], cands, concepts)),
                     "choice": sel, "choice_prob": round(prob, 3),
                     "choice_hit": int(sel != NONE and prob >= 0.5),
                     "noul": round(noul, 3), "noul_hit50": int(noul >= 0.5),
                     "noul_hit70": int(noul >= 0.7), "cands": ",".join(cands)})
    with open(RESULTS, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["path", "group", "qid", "terms", "token_hit", "choice", "choice_prob",
                    "choice_hit", "noul", "noul_hit50", "noul_hit70", "cands"])
        for r in rows:
            w.writerow([r[k] for k in ["path", "group", "qid", "terms", "token_hit",
                                       "choice", "choice_prob", "choice_hit", "noul",
                                       "noul_hit50", "noul_hit70", "cands"]])
    # token gap = not token_hit
    def score(name, hit_key):
        tg = [1 - r["token_hit"] for r in rows]
        jg = [1 - r[hit_key] for r in rows]
        same = sum(1 for a, b in zip(tg, jg) if a == b)
        n = len(rows)
        p0 = (tg.count(1) / n) * (jg.count(1) / n) + (tg.count(0) / n) * (jg.count(0) / n)
        po = same / n
        kappa = (po - p0) / (1 - p0) if p0 != 1 else 1.0
        return {"method": name, "n": n, "token_gap": sum(tg), "jev_gap": sum(jg),
                "agree": same, "agree_pct": round(100 * po, 1), "kappa": round(kappa, 3),
                "both_gap": sum(1 for a, b in zip(tg, jg) if a and b),
                "token_only_gap": sum(1 for a, b in zip(tg, jg) if a and not b),
                "jev_only_gap": sum(1 for a, b in zip(tg, jg) if not a and b)}
    summary = {"n": len(rows), "tokens": tokens,
               "cost_usd": round(tokens * 0.042 / 1_000_000, 4),
               "methods": [score("choice", "choice_hit"),
                           score("noul50", "noul_hit50"),
                           score("noul70", "noul_hit70")]}
    targets = [r for r in rows if r["group"] == "target"]
    summary["targets"] = {
        "n": len(targets),
        "token_gap": sum(1 - r["token_hit"] for r in targets),
        "choice_gap": sum(1 - r["choice_hit"] for r in targets),
        "noul50_gap": sum(1 - r["noul_hit50"] for r in targets),
        "noul70_gap": sum(1 - r["noul_hit70"] for r in targets),
        "detail": [{k: r[k] for k in ("qid", "terms", "path", "token_hit", "choice",
                                      "choice_prob", "noul")} for r in targets],
    }
    with open(SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n分歧样例")
    for r in rows:
        if (1 - r["token_hit"]) != (1 - r["choice_hit"]):
            print("-", r["group"], r["path"])
            print("  prefix:", sample[r["path"]]["prefix"][:100])
            print("  token_hit:", r["token_hit"], "| choice:", r["choice"], r["choice_prob"],
                  "| noul:", r["noul"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if a.run:
        run(a.workers)
    if a.analyze:
        analyze()
    if not (a.run or a.analyze):
        ap.error("要 --run / --analyze")


if __name__ == "__main__":
    main()
