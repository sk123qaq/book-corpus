#!/usr/bin/env python3
"""在 gold 60 题上评 agent 检索计划，输出分档读数。

  eval_plan.py --mode full
  eval_plan.py --mode noconcept
"""
import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import qsearch_plan as qp  # noqa: E402

GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
PLANS = os.path.join(HERE, "plans")
CAP = 99
WEIGHTS = {"lit1_c1": (1.0, 1.0), "lit06_c12": (0.6, 1.2),
           "lit1_c12": (1.0, 1.2), "lit04_c1": (0.4, 1.0),
           "lit1_c15": (1.0, 1.5)}


def variant(plan, name):
    p = dict(plan)
    if name == "full":
        return p
    if name == "literal":
        p["groups"] = [g for g in plan.get("groups") or [] if g.get("name") == "literal"]
        p["concepts"] = []
    elif name == "bridge":
        p["groups"] = [g for g in plan.get("groups") or [] if g.get("name") == "bridge"]
        p["concepts"] = plan.get("concepts") or []
    elif name == "concept":
        p["groups"] = []
        p["concepts"] = plan.get("concepts") or []
    elif name == "lit_concept":
        p["groups"] = [g for g in plan.get("groups") or [] if g.get("name") == "literal"]
        p["concepts"] = plan.get("concepts") or []
    elif name in WEIGHTS:
        lw, cw = WEIGHTS[name]
        p["groups"] = []
        for g in plan.get("groups") or []:
            if g.get("name") == "literal":
                p["groups"].append({**g, "weight": lw})
        p["concepts"] = plan.get("concepts") or []
        p["concept_weight"] = cw
    else:
        raise SystemExit("未知变体 " + name)
    return p


def rank_of(path, hits):
    for i, r in enumerate(hits, 1):
        if r["path"] == path:
            return min(i, CAP), i
    return CAP, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=["full", "noconcept", "refined"])
    ap.add_argument("--variants", default="full,literal,bridge,concept,lit_concept")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    gold = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    if a.limit:
        gold = gold[:a.limit]
    variants = a.variants.split(",")
    rows = []
    t0 = time.time()
    for r in gold:
        path = os.path.join(PLANS, a.mode, r["qid"] + ".json")
        if not os.path.isfile(path):
            print("缺计划", r["qid"], file=sys.stderr)
            continue
        plan = json.load(open(path, encoding="utf-8"))
        for v in variants:
            p = variant(plan, v)
            try:
                hits = qp.run(p, limit=100)["hits"]
            except SystemExit:
                rank, pos = CAP, ""
            else:
                rank, pos = rank_of(r["path"], hits)
            rows.append({"qid": r["qid"], "category": r["category"], "variant": v,
                         "rank": rank, "pos": pos, "hit10": int(rank <= 10),
                         "hit20": int(rank <= 20)})
    dest = a.out or os.path.join(HERE, "eval-plan-%s.tsv" % a.mode)
    with open(dest, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["qid", "category", "variant", "rank", "pos", "hit10", "hit20"])
        for row in rows:
            w.writerow([row[k] for k in ["qid", "category", "variant", "rank", "pos",
                                         "hit10", "hit20"]])
    print("variant\trecall@10\trecall@20\tmrr\t无原词top20\t原词r@10\tn\tsecs")
    for v in variants:
        rs = [r for r in rows if r["variant"] == v]
        n = len(rs)
        if not n:
            continue
        r10 = sum(r["hit10"] for r in rs) / n
        r20 = sum(r["hit20"] for r in rs) / n
        mrr = sum(1 / r["rank"] if r["rank"] < CAP else 0 for r in rs) / n
        no = [r for r in rs if r["category"] == "无原词"]
        orig = [r for r in rs if r["category"] == "原词"]
        no20 = sum(r["hit20"] for r in no) / len(no) if no else 0
        org10 = sum(r["hit10"] for r in orig) / len(orig) if orig else 0
        print("%s\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%d\t%.1f" % (
            v, r10, r20, mrr, no20, org10, n, time.time() - t0))
    print("->", dest)
    json.dump({"mode": a.mode, "rows": rows}, open(os.path.join(HERE, "eval-plan-%s.json" % a.mode),
                                                   "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
