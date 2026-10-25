#!/usr/bin/env python3
"""把 eval-*.tsv 汇总成 §2.2 那张表。只读，缺档就留空。

  eval_report.py [--batch 工作区/批次/qa-检索-2026-10-19]
"""
import argparse, csv, glob, os

ROOT = "/home/shike/文档/books"


def load(path, gold):
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
            "mrr": sum(1.0 / p if p else 0.0 for p in pos) / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19"))
    a = ap.parse_args()
    gold = {r["qid"]: r for r in csv.DictReader(
        open(os.path.join(a.batch, "gold.tsv"), encoding="utf-8"), delimiter="\t")}
    per = {}
    for path in sorted(glob.glob(os.path.join(a.batch, "eval-*.tsv"))):
        if "rerun" in path or "summary" in path:
            continue
        for k, v in load(path, gold).items():
            per.setdefault(k, v)
    labels = {"A": "A `grep -rlF`", "B": "B `rg -l -F`", "C": "C `defpass --concept`",
              "D": "D 近义词组 grep", "E": "E FTS bigram + 覆盖度（P1）",
              "F": "F 概念表档（P1，概念键按 gold 给）",
              "G": "G E + 文档扩展档（P1.5）", "H": "H E + 伪查询档（P1.5）"}
    print("| 档 | recall@10 | recall@20 | MRR | 无原词类进前 20 |")
    print("|---|---|---|---|---|")
    for k in ("A", "B", "C", "D", "E", "F", "G", "H"):
        if k not in per:
            continue
        m = metrics(per[k], gold)
        no = metrics(per[k], gold, "无原词")
        print("| %s | %.3f | %.3f | %.3f | %.3f |" % (
            labels[k], m["r10"], m["r20"], m["mrr"], no["r20"]))
    print()
    print("| 档 | 原词 r@10 | 近义 r@10 | 无原词 r@10 | 原词 MRR |")
    print("|---|---|---|---|---|")
    for k in ("A", "B", "C", "D", "E", "F", "G", "H"):
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
