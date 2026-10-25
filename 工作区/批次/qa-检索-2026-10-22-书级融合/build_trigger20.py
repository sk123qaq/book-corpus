#!/usr/bin/env python3
"""重做 B5 触发清单：20 题，每题带判断所需的全部信息。

  python3 build_trigger20.py

写 live/B5/trigger-20.tsv（机读）与 live/B5/trigger-20.txt（人读）。
expected 是人按规则读问句得出的；actual 是 book_cards.trigger 跑出来的。
"""
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_cards as BC  # noqa: E402
import book_query as BQ  # noqa: E402
import book_score  # noqa: E402

OUT_TSV = os.path.join(HERE, "live", "B5", "trigger-20.tsv")
OUT_TXT = os.path.join(HERE, "live", "B5", "trigger-20.txt")
QIDS = ([f"O{i:02d}" for i in range(1, 11)] + [f"L{i:02d}" for i in range(1, 6)]
        + [f"S{i:02d}" for i in range(1, 6)])

# 人工判断：读问句，先看有没有书名/作者/原句提示，再看四条触发规则。
EXPECTED = {
    "O01": (1, "开放概念问句；没有书名/作者/原句提示；R1/R2/R3/R4 都成立"),
    "O02": (1, "开放概念问句；没有书名/作者/原句提示；R2/R3/R4 成立"),
    "O03": (1, "开放概念问句；没有书名/作者/原句提示；R1/R3/R4 成立"),
    "O04": (0, "问句点了书名《动物农场》；按抑制规则不弹卡"),
    "O05": (1, "开放概念问句；没有书名/作者/原句提示；R2/R3/R4 成立"),
    "O06": (1, "开放概念问句；没有书名/作者/原句提示；R1/R2/R3/R4 成立"),
    "O07": (1, "概念问句，斯宾诺莎只是被提到，不是作者查询；没有书名/原句提示；R1/R2/R3/R4 成立"),
    "O08": (1, "概念问句，韦伯只是被提到，不是作者查询；没有书名/原句提示；R1/R3/R4 成立"),
    "O09": (1, "开放情节问句；没有书名/作者/原句提示；R1/R2/R3/R4 成立"),
    "O10": (1, "开放概念问句；没有书名/作者/原句提示；R3/R4 成立"),
    "L01": (0, "问句点了书名《凡人修仙传》；按抑制规则不弹卡"),
    "L02": (0, "问句点了书名《完美世界》；按抑制规则不弹卡"),
    "L03": (0, "问句以 2 字书名「遮天」开头并带「里」；按抑制规则不弹卡"),
    "L04": (0, "问句点了书名《诡秘之主》；按抑制规则不弹卡"),
    "L05": (0, "问句以 2 字书名「剑来」开头并带「里」；按抑制规则不弹卡"),
    "S01": (0, "问句点了书名《中县干部》；按抑制规则不弹卡"),
    "S02": (0, "问句里有书名号《搜神记》，即使库里没有这本书也按书名查询处理"),
    "S03": (1, "开放设计问句；没有书名/作者/原句提示；R1/R3/R4 成立"),
    "S04": (1, "具体事实问句，但没有书名/作者/原句提示；R2/R3/R4 成立"),
    "S05": (0, "问句点了书名《解忧杂货店》；按抑制规则不弹卡"),
}


def main():
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta()
    expand = book_score.load_expand(con)
    books = BC.load_books()
    concepts = BC.load_concepts()
    rows = {r["qid"]: r for r in BC.gold_rows()}
    out = []
    for qid in QIDS:
        row = rows[qid]
        planner = BQ.Planner(con, meta, expand, limit=10)
        nouns = BQ.split_terms(row["terms"], planner.actions)
        hits, _ = planner.search(nouns)
        ranking = book_score.rank(hits, nouns, "best_support", [], meta, expand)
        h = BC.query_hints(row["question"], books, concepts, row["terms"])
        fire, reasons = BC.trigger(row["question"], ranking, h)
        top1 = ranking[0].book if ranking else ""
        margin = 0.0
        if len(ranking) > 1 and ranking[0].score:
            margin = (ranking[0].score - ranking[1].score) / ranking[0].score
        types = len({b.type for b in ranking[:10]})
        long10 = sum(1 for b in ranking[:10] if b.chapters >= BC.LONG_TRIGGER)
        cards = BC.build_cards(ranking, hits, nouns, 6) if fire else []
        shown = {c.book for c in cards}
        expected, why = EXPECTED[qid]
        out.append({
            "qid": qid, "stratum": row["stratum"], "question": row["question"],
            "terms": ",".join(row["terms"]), "target_book": "|".join(row["books"]),
            "top1": top1, "margin": round(margin, 4), "types": types, "long10": long10,
            "hint_title": h["title"] or "-", "hint_author": h["author"] or "-",
            "hint_verbatim": int(h["verbatim"]), "hint_concept": int(h["concept"]),
            "hint_question": int(h["question"]), "hint_qw": "|".join(h["qw"]) or "-",
            "expected": expected, "expected_reason": why, "actual": int(fire),
            "reasons": ",".join(reasons) or "-", "match": int(expected == int(fire)),
            "cards": len(cards), "target_shown": int(bool(shown & set(row["books"]))),
        })
    cols = list(out[0])
    os.makedirs(os.path.dirname(OUT_TSV), exist_ok=True)
    with open(OUT_TSV + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    os.replace(OUT_TSV + ".tmp", OUT_TSV)

    lines = [
        "B5 触发清单（20 题）",
        "=" * 60,
        "",
        "要判断什么：",
        "  每题问句会不会弹书卡。expected 是人按规则读问句得出的；actual 是代码跑出来的；",
        "  match=1 表示一致。若不一致，要么规则写错，要么人读错，逐项看下面的提示。",
        "",
        "判断顺序：",
        "  1) 先看抑制。问句点了书名、作者或原句 → expected=0（不弹）。",
        "     书名：3 字以上书名出现在问句；2 字书名 + 里/中/内/的/讲/说/是/这/那；",
        "           或书名号《…》里的书名（库里没有也算）。",
        "     作者：作者名出现，且没有问句词，或作者名占问句一半以上。",
        "     原句：引号里 ≥8 字，或 ≥12 字且没有问句词。",
        "  2) 没有抑制，再看四条规则，任一成立 → expected=1（弹卡）。",
        "     R1 top1/top2 分差 <10%；R2 top10 ≥3 个类型；R3 top10 有 ≥50 章的书；",
        "     R4 概念型开放问题（概念表命中或问句词）。",
        "",
        "列说明（TSV）：",
        "  question 问句 · terms 检索词 · target_book 可接受书 · top1 首轮第一 ·",
        "  margin 分差 · types top10 类型数 · long10 top10 ≥50 章书数 ·",
        "  hint_* 书名/作者/原句/概念/问句词提示 · expected/expected_reason 人工判断 ·",
        "  actual/reasons 代码结果 · match 是否一致 · cards 卡数 · target_shown 目标书在不在卡里",
        "",
    ]
    for r in out:
        lines += [
            "[%s] expected=%d actual=%d match=%d" % (r["qid"], r["expected"], r["actual"],
                                                     r["match"]),
            "  问句：%s" % r["question"],
            "  检索词：%s" % r["terms"],
            "  可接受书：%s" % r["target_book"],
            "  首轮 top1：%s" % (r["top1"] or "-"),
            "  分差 %.2f%% ｜ top10 类型 %d ｜ top10 ≥50 章 %d ｜ 卡数 %d ｜ 目标书在卡里 %d"
            % (r["margin"] * 100, r["types"], r["long10"], r["cards"], r["target_shown"]),
            "  提示：书名=%s ｜ 作者=%s ｜ 原句=%s ｜ 概念=%s ｜ 问句词=%s"
            % (r["hint_title"], r["hint_author"], "是" if r["hint_verbatim"] else "否",
               "是" if r["hint_concept"] else "否", r["hint_qw"]),
            "  期望理由：%s" % r["expected_reason"],
            "  代码原因：%s" % r["reasons"],
            "",
        ]
    lines.append("对不上 %d 题 / %d" % (sum(1 for r in out if not r["match"]), len(out)))
    open(OUT_TXT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("写 %s 与 %s；对不上 %d/%d" % (
        OUT_TSV, OUT_TXT, sum(1 for r in out if not r["match"]), len(out)))
    for r in out:
        if not r["match"]:
            print("  DIFF", r["qid"], r["expected"], r["actual"], r["reasons"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
