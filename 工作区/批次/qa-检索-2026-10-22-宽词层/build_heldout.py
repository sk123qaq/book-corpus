#!/usr/bin/env python3
"""Pair extracted targets with questions and validate the heldout rows.

  build_heldout.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
TARGETS = os.path.join(HERE, "targets.tsv")
OUT = os.path.join(HERE, "heldout.tsv")

Q = [
    ("权力", "权力为什么能左右普通人的日常生活？", ["权力", "左右", "日常生活"]),
    ("权力", "权力为什么能让人接受最坏的事情？", ["权力", "最坏"]),
    ("权力", "权力为什么会变成一种压迫？", ["权力", "压迫"]),
    ("权力", "权力为什么不能只靠一个人说了算？", ["权力", "一个人", "说了算"]),
    ("正义", "正义为什么不能只看法律条文？", ["正义", "法律条文"]),
    ("正义", "正义为什么需要道德约束？", ["正义", "道德约束"]),
    ("正义", "正义为什么不能靠一时冲动？", ["正义", "一时冲动"]),
    ("正义", "正义为什么让人争论不休？", ["正义", "争论不休"]),
    ("秩序", "秩序为什么在熟人社会靠习惯维持？", ["秩序", "熟人社会", "习惯"]),
    ("秩序", "秩序为什么会让制度变得僵化？", ["秩序", "僵化"]),
    ("秩序", "秩序为什么要求人保持冷淡？", ["秩序", "冷淡"]),
    ("秩序", "秩序为什么在人口流动后变弱？", ["秩序", "人口流动"]),
    ("服从", "服从为什么总站在强者一边？", ["服从", "强者"]),
    ("服从", "服从为什么是一种宗教式情感？", ["服从", "宗教式"]),
    ("服从", "服从为什么和年龄有关？", ["服从", "年龄"]),
    ("服从", "服从为什么需要民众的信任？", ["服从", "信任"]),
    ("孤独", "孤独为什么和被群体疏远有关？", ["孤独", "疏远"]),
    ("孤独", "孤独为什么会在亲密之后出现？", ["孤独", "亲密"]),
    ("孤独", "孤独为什么让人渴望一个人待着？", ["孤独", "一个人待着"]),
    ("孤独", "孤独为什么需要别人来安慰？", ["孤独", "安慰"]),
    ("爱", "爱为什么让人更容易繁衍？", ["爱", "繁衍"]),
    ("爱", "爱为什么不能只靠冲动？", ["爱", "冲动"]),
    ("爱", "爱为什么不能没有承诺？", ["爱", "承诺"]),
    ("爱", "爱为什么会被距离冲淡？", ["爱", "距离"]),
    ("死亡", "死亡为什么让人感到眩晕？", ["死亡", "眩晕"]),
    ("死亡", "死亡为什么让人放下权力？", ["死亡", "权力"]),
    ("死亡", "死亡为什么让人回忆过去？", ["死亡", "回忆"]),
    ("死亡", "死亡为什么让人想到造物主？", ["死亡", "造物主"]),
    ("身份", "身份为什么会影响一个人在乡村里的威信？", ["身份", "威信"]),
    ("身份", "身份为什么会影响投票？", ["身份", "投票"]),
    ("身份", "身份为什么不能传给下一代？", ["身份", "下一代"]),
    ("身份", "身份为什么是一种情感需求？", ["身份", "情感需求"]),
    ("家庭", "家庭为什么不能只靠感情维系？", ["家庭", "感情"]),
    ("家庭", "家庭为什么会影响人对陌生人的信任？", ["家庭", "信任"]),
    ("家庭", "家庭为什么和继承制度有关？", ["家庭", "继承"]),
    ("家庭", "家庭为什么会在分手后减少？", ["家庭", "分手"]),
    ("幸福", "幸福为什么不能只靠生理释放？", ["幸福", "生理释放"]),
    ("幸福", "幸福为什么和接近目标有关？", ["幸福", "接近目标"]),
    ("幸福", "幸福为什么不能只靠本能？", ["幸福", "本能"]),
    ("幸福", "幸福为什么在富裕之后反而变难？", ["幸福", "富裕"]),
]


def main():
    targets = list(csv.DictReader(open(TARGETS, encoding="utf-8-sig"), delimiter="\t"))
    if len(targets) != len(Q):
        sys.exit("targets %d != questions %d" % (len(targets), len(Q)))
    rows, bad = [], []
    for i, (t, (broad, question, literal)) in enumerate(zip(targets, Q), 1):
        if t["broad"] != broad:
            bad.append("%d broad %s != %s" % (i, t["broad"], broad))
            continue
        text = t["text"]
        path = os.path.join(ROOT, t["path"])
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        line = int(t["line"])
        if line < 1 or line > len(lines) or lines[line - 1].strip() != text.strip():
            bad.append("%d line mismatch %s:%d" % (i, t["path"], line))
        if broad not in question:
            bad.append("%d question lacks broad key" % i)
        if t["alias"] not in text:
            bad.append("%d target lacks alias %s" % (i, t["alias"]))
        if broad in text:
            bad.append("%d target contains broad key" % i)
        leak = [x for x in literal if x in text]
        if leak:
            bad.append("%d literal leaks %s" % (i, ",".join(leak)))
        rows.append({"qid": "b%03d" % i, "broad": broad, "question": question,
                     "literal": ",".join(literal), "uid": t["uid"],
                     "path": t["path"], "line": line, "alias": t["alias"],
                     "evidence": text, "chapter_has_key": t["chapter_has_key"]})
    if bad:
        print("FAIL", len(bad), file=sys.stderr)
        for b in bad:
            print(" ", b, file=sys.stderr)
        sys.exit(1)
    with open(OUT + ".tmp", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, delimiter="\t", lineterminator="\n",
                           fieldnames=["qid", "broad", "question", "literal", "uid",
                                       "path", "line", "alias", "evidence",
                                       "chapter_has_key"])
        w.writeheader()
        w.writerows(rows)
    os.replace(OUT + ".tmp", OUT)
    print("->", OUT, len(rows), "rows")


if __name__ == "__main__":
    main()
