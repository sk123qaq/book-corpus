#!/usr/bin/env python3
"""把 eval-*.tsv 汇总成 §2.2 那张表。只读，缺档就留空。

  eval_report.py [--batch 工作区/批次/qa-检索-2026-10-19]
"""
import argparse, csv, glob, os

ROOT = "/home/shike/文档/books"


def load(path):
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"):
        out.setdefault(r["system"], []).append(r)
    return out


def metrics(rows, gold, cat=None):
    sub = [r for r in rows if cat is None or gold[r["qid"]]["category"] == cat]
    if not sub:
        return None
    n = len(sub)
    ranks = [int(r["rank"]) for r in sub]
    pos = [int(r["pos"]) if r["pos"] else 0 for r in sub]
    return {"n": n,
            "r10": sum(1 for x in ranks if x <= 10) / n,
            "r20": sum(1 for x in ranks if x <= 20) / n,
            "mrr": sum(1.0 / p for p in pos if 0 < p < 99) / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19"))
    a = ap.parse_args()
    gold = {r["qid"]: r for r in csv.DictReader(
        open(os.path.join(a.batch, "gold.tsv"), encoding="utf-8"), delimiter="\t")}
    per = {}
    skip = ("作废", "缺陷前", "rerun", "summary")
    paths = [p for p in glob.glob(os.path.join(a.batch, "eval-*.tsv"))
             if not any(k in os.path.basename(p) for k in skip)]
    paths.sort(key=os.path.getmtime, reverse=True)   # 同档取最新一次读数
    for path in paths:
        for k, v in load(path).items():
            per.setdefault(k, v)
    labels = {"A": "A `grep -rlF`", "B": "B `rg -l -F`", "C": "C `defpass --concept`",
              "D": "D 近义词组 grep", "E": "E FTS bigram + 覆盖度（P1）",
              "F": "F 概念表档（P1，概念键按 gold 给）",
              "G": "G E + 文档扩展档（P1.5）", "H": "H E + 伪查询档（P1.5）",
              "K": "K 查询侧扩展（query_expand）", "L": "L 问句路由（concept_route）",
              "M": "M 路由与段落档降合（路 E）"}
    ALL = ("A", "B", "C", "D", "E", "F", "G", "H", "K", "L", "M")
    print("| 档 | recall@10 | recall@20 | MRR | 无原词类进前 20 |")
    print("|---|---|---|---|---|")
    for k in ALL:
        if k not in per:
            continue
        m = metrics(per[k], gold)
        no = metrics(per[k], gold, "无原词")
        print("| %s | %.3f | %.3f | %.3f | %s |" % (
            labels[k], m["r10"], m["r20"], m["mrr"],
            "%.3f" % no["r20"] if no else "-"))
    print()
    print("| 档 | 原词 r@10 | 近义 r@10 | 无原词 r@10 | 原词 MRR |")
    print("|---|---|---|---|---|")
    for k in ALL:
        if k not in per:
            continue
        cells = []
        for cat in ("原词", "近义表达", "无原词"):
            m = metrics(per[k], gold, cat)
            cells.append("%.3f" % m["r10"] if m else "-")
        m0 = metrics(per[k], gold, "原词")
        print("| %s | %s | %s | %s | %.3f |" % (labels[k], cells[0], cells[1], cells[2], m0["mrr"]))


if __name__ == "__main__":
    main()
