#!/usr/bin/env python3
"""按检索计划跑查：多组词分开检索，章级 RRF 融合，再用组覆盖与原词加权。

计划 JSON：
{
  "question": "...",
  "must": ["打工", "换掉"],
  "groups": [
    {"name": "口语", "terms": ["打工", "被换掉"], "weight": 1.0},
    {"name": "学术", "terms": ["劳动力商品化", "产业后备军"], "weight": 1.2}
  ],
  "concepts": ["铁笼"],
  "broad": ["权力"],
  "broad_weight": 1.0,
  "exclude": ["铁笼山"],
  "book_hint": ""
}

  qsearch_plan.py --plan plan.json -n 20
  qsearch_plan.py --plan - --json < plan.json
"""
import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk  # noqa: E402
import qsearch  # noqa: E402

DB = os.path.join(ROOT, "目录", "检索", "index.db")
BROAD = os.path.join(ROOT, "目录", "概念表-broad.tsv")


def load_plan(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_broad(key):
    with open(BROAD, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["key"] == key:
                aliases = [a for a in r["aliases"].split(",") if a]
                exclude = [x for x in r["exclude"].split(",") if x and x != "无"]
                return [key] + aliases, exclude
    raise KeyError(key)


def normalize(plan):
    def weight(v):
        # 显式给 0 是想把这一组关掉，不能被 or 吞成 1.0
        return 1.0 if v is None or v == "" else float(v)

    groups = []
    for g in plan.get("groups") or []:
        terms = [str(t).strip() for t in (g.get("terms") or []) if str(t).strip()]
        if terms:
            groups.append({"name": g.get("name") or "组", "terms": terms,
                           "weight": weight(g.get("weight"))})
    if not groups and plan.get("must"):
        groups.append({"name": "原词", "terms": [str(t) for t in plan["must"]],
                       "weight": 1.0})
    exclude = [str(x) for x in (plan.get("exclude") or []) if str(x).strip()]
    for key in plan.get("concepts") or []:
        try:
            terms, ex = qsearch.load_concept(key)
        except Exception:
            continue
        if terms:
            groups.append({"name": "概念:" + key, "terms": terms,
                           "weight": weight(plan.get("concept_weight"))})
            exclude += ex
    for key in plan.get("broad") or []:
        try:
            terms, ex = load_broad(key)
        except KeyError:
            continue
        if terms:
            groups.append({"name": "宽:" + key, "terms": terms,
                           "weight": weight(plan.get("broad_weight")),
                           "exclude": ex})
    return groups, exclude


def run_group(con, terms, exclude, limit=100, min_chars=40, pool=2000, group_exclude=None):
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    if not expr:
        return []
    hits, _ = qsearch.search(con, expr, terms, exclude + (group_exclude or []),
                             limit, min_chars, False, pool)
    return hits


def run(plan, limit=20, pool=2000, min_chars=40, db=DB, group_limit=100):
    t0 = time.time()
    groups, exclude = normalize(plan)
    if not groups:
        raise SystemExit("计划里没有可检索的词组")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    scores, best = defaultdict(float), {}
    group_hits = []
    for g in groups:
        hits = run_group(con, g["terms"], exclude, group_limit, min_chars, pool,
                         g.get("exclude"))
        group_hits.append(hits)
        for i, r in enumerate(hits, 1):
            scores[r["path"]] += g["weight"] / (60 + i)
    must = [str(t) for t in (plan.get("must") or []) if str(t).strip()]
    hint = (plan.get("book_hint") or "").strip()
    if hint and not any(hint in p for p in scores):
        hint = ""
    out = []
    for p, base in scores.items():
        paras = {}
        for hits in group_hits:
            for r in hits:
                if r["path"] == p:
                    paras[(r["line"], r["text"])] = r
        cand = []
        for (line, text), r in paras.items():
            hit = [gi for gi, g in enumerate(groups)
                   if any(t in text for t in g["terms"])
                   and not any(x in text for x in g.get("exclude", []))]
            mh = sum(1 for t in must if t in text)
            cand.append((len(hit), mh, r.get("score", 0), r, hit))
        if not cand:
            continue
        cov, mh, _, r, hit = max(cand, key=lambda x: (x[0], x[1], x[2]))
        text = r.get("text") or ""
        if hint and hint not in p:
            continue
        if any(x in text for x in exclude):
            continue
        score = base + 0.10 * cov + 0.20 * mh
        out.append({"path": p, "book": r.get("book") or p.split("/md/")[0][2:],
                    "line": r.get("line", 0), "text": text,
                    "score": round(score, 6), "groups_hit": cov,
                    "must_hit": mh, "group_names": [groups[i]["name"] for i in hit]})
    out.sort(key=lambda r: (-r["score"], r["path"], r["line"]))
    return {"question": plan.get("question", ""), "hits": out[:limit],
            "groups": groups, "exclude": exclude,
            "ms": round((time.time() - t0) * 1000, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--pool", type=int, default=2000,
                    help="每组先取多少段进打分，2000 是默认；0 表示不限")
    ap.add_argument("--group-limit", type=int, default=100,
                    help="每组重排后留多少段进融合，默认 100")
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run(load_plan(a.plan), a.limit, a.pool, a.min_chars, a.db, a.group_limit)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print("%s  %d 条  %.0f ms" % (res["question"], len(res["hits"]), res["ms"]),
          file=sys.stderr)
    for i, r in enumerate(res["hits"], 1):
        print("%2d %-66s:%d  %.4f  组%d 原词%d" % (
            i, r["path"], r["line"], r["score"], r["groups_hit"], r["must_hit"]))
        print("   " + re.sub(r"\s+", " ", r["text"])[:150])


if __name__ == "__main__":
    main()
