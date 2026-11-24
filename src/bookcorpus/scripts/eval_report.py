#!/usr/bin/env python3
"""把 eval-*.tsv 汇总成 §2.2 那张表。只读，缺档就留空。

eval_report.py [--batch 工作区/批次/qa-检索-2026-10-19]
"""

import argparse
import csv
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L

ROOT = L.ROOT


def load(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            out.setdefault(r["system"], []).append(r)
    return out


def metrics(rows, gold, cat=None):
    sub = [r for r in rows if cat is None or gold[r["qid"]]["category"] == cat]
    if not sub:
        return None
    n = len(sub)
    ranks = [int(r["rank"]) for r in sub]
    pos = [int(r["pos"]) if r["pos"] else 0 for r in sub]
    return {
        "n": n,
        "r10": sum(1 for x in ranks if x <= 10) / n,
        "r20": sum(1 for x in ranks if x <= 20) / n,
        "mrr": sum(1.0 / p for p in pos if 0 < p < 99) / n,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19"))
    a = ap.parse_args(argv)
    with open(os.path.join(a.batch, "gold.tsv"), encoding="utf-8") as fh:
        gold = {r["qid"]: r for r in csv.DictReader(fh, delimiter="\t")}
    per = {}
    skip = ("作废", "缺陷前", "rerun", "summary")
    paths = [
        p for p in glob.glob(os.path.join(a.batch, "eval-*.tsv")) if not any(k in os.path.basename(p) for k in skip)
    ]
    paths.sort(key=os.path.getmtime, reverse=True)  # 同档取最新一次读数
    for path in paths:
        for k, v in load(path).items():
            per.setdefault(k, v)
    labels = {
        "A": "A `grep -rlF`",
        "B": "B `rg -l -F`",
        "C": "C `defpass`",
        "D": "D 扩展组 grep",
        "E": "E FTS bigram + 覆盖度（P1）",
        "F": "F 历史概念表档",
        "G": "G E + 文档扩展档（P1.5）",
        "H": "H E + 启发重排（--rerank）",
        "I": "I G + 启发重排（--expand --rerank）",
        "J": "J E + 伪查询扩展档（queries）",
        "K": "K 查询侧扩展（query_expand）",
        "K1": "K1 查询侧扩展（章级 base=1）",
        "K2": "K2 查询侧扩展（章级 base=2）",
        "L": "L 历史问句路由档",
        "M": "M 路由与段落档降合（路 E）",
    }
    all_keys = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "K1", "K2", "L", "M")
    keys = [k for k in all_keys if k in per] + sorted(k for k in per if k not in labels)
    print("| 档 | recall@10 | recall@20 | MRR | 无原词类进前 20 |")
    print("|---|---|---|---|---|")
    for k in keys:
        m = metrics(per[k], gold)
        no = metrics(per[k], gold, "无原词")
        label = labels.get(k, k)
        if m:
            print(
                "| %s | %.3f | %.3f | %.3f | %s |"
                % (label, m["r10"], m["r20"], m["mrr"], "%.3f" % no["r20"] if no else "-")
            )
        else:
            print("| %s | - | - | - | - |" % label)
    print()
    print("| 档 | 原词 r@10 | 近义 r@10 | 无原词 r@10 | 原词 MRR |")
    print("|---|---|---|---|---|")
    for k in keys:
        cells = []
        for cat in ("原词", "近义表达", "无原词"):
            m = metrics(per[k], gold, cat)
            cells.append("%.3f" % m["r10"] if m else "-")
        m0 = metrics(per[k], gold, "原词")
        label = labels.get(k, k)
        print("| %s | %s | %s | %s | %s |" % (label, cells[0], cells[1], cells[2], "%.3f" % m0["mrr"] if m0 else "-"))


if __name__ == "__main__":
    main()
