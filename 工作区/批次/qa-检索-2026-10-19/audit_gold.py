#!/usr/bin/env python3
"""gold.tsv 的分类自检：原词类必须含概念原词，无原词类的问法二元组不该落在目标章里。

只读，不改 gold.tsv。重叠率 = 问句二元组中出现在目标章文本里的比例。
"""
import csv, re, sys

ROOT = "/home/shike/文档/books"
GOLD = ROOT + "/工作区/批次/qa-检索-2026-10-19/gold.tsv"
CJK = re.compile(r"[\u4e00-\u9fff]+")
FUNC = set("的了是一个在有和也就都不么什怎为要会能可以这那他她它们你我人上下来去说过得地着把被让给对从与及或而但其此之乎者矣吗呢吧啊很更最再又还只已经非常所")
GEN = {"自己", "大家", "人家", "时候", "现在", "知道", "觉得", "应该", "可能", "这样", "那样", "一个", "什么", "怎么", "我们", "他们", "你们"}


def bigrams(s):
    out = []
    for run in CJK.findall(s):
        for i in range(len(run) - 1):
            g = run[i:i + 2]
            if g[0] not in FUNC and g[1] not in FUNC and g not in GEN:
                out.append(g)
    return out


def main():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    worst, bad = [], 0
    for r in rows:
        text = open(ROOT + "/" + r["path"], encoding="utf-8", errors="ignore").read()
        qs = bigrams(r["question"])
        overlap = sum(1 for g in qs if g in text) / max(1, len(qs))
        terms = [t for t in r["terms"].split(",") if t]
        literal = sum(1 for t in terms if t in text)
        flag = ""
        if r["category"] == "原词" and literal == 0:
            flag, bad = "原词类不含词表中的词", bad + 1
        if r["category"] == "近义表达" and literal == len(terms):
            flag = "注：近义词也在本章，近义类偏弱"
        if r["category"] == "无原词" and overlap > 0.4:
            flag, bad = "无原词类问法重叠过高", bad + 1
        worst.append((r["qid"], r["category"], round(overlap, 2), literal, flag, r["question"]))
    for w in worst:
        if w[4]:
            print("!", *w)
    for cat in ("原词", "近义表达", "无原词"):
        sub = [w[2] for w in worst if w[1] == cat]
        print(cat, "均值重叠", round(sum(sub) / len(sub), 2), "最大", max(sub))
    print("问题条数", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
