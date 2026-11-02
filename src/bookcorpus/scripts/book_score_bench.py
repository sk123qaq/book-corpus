#!/usr/bin/env python3
"""书级打分 sum vs mix 的对比（EXPECT 里 11 个概念，正解书未入库的自动略过）。

  book_score_bench.py

每个概念给一份「该概念的正解书」清单（人工指定，来自书库实际有的书），
看两种打分把正解排到第几。正解取不到就是排序错，取到但排在 10 名之后算弱。

  sum = 段落得分求和（旧口径，留作对照）
  mix = 0.7*最好段 + 0.3*段落均值（qsearch --book-score 的现行默认）
pool 固定 medium（2000 段），只变书级打分。
"""
import os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
LIBDB = os.path.join(ROOT, "目录", "library.db")

# 概念 → 该概念在书库里公认讲得对的书（人工指定，不看排序结果）
EXPECT = {
    "群体": ["乌合之众", "刘擎西方现代思想讲义", "社会性动物"],
    "异化": ["刘擎西方现代思想讲义", "1844年经济学哲学手稿", "马克思"],
    "规训": ["刘擎西方现代思想讲义", "规训与惩罚", "疯癫与文明"],
    "荒诞": ["加缪全集", "西西弗神话", "局外人"],
    "铁笼": ["刘擎西方现代思想讲义"],
    "内卷": ["八次危机", "以日为鉴 衰退时代生存指南"],
    "独立判断": ["刘擎西方现代思想讲义"],
    "元认知": ["认知觉醒"],
    "损失厌恶": ["思考，快与慢"],
    "锚定效应": ["思考，快与慢"],
    "礼治": ["乡土中国"],
}


def books_meta(lib):
    out = {}
    for uid, path in lib.execute("SELECT uid, path FROM book"):
        p = path.split("/")
        out[path] = (p[0] if p else "", p[1] if len(p) > 1 else "")
    return out


def rank(con, key, mode, pool=2000, n=40):
    groups, exclude = qsearch.load_concept(key)
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    hits, ms = qsearch.search(con, expr, groups, exclude, 10 ** 6, 40, False, pool)
    agg = {}
    for r in hits:
        b = agg.setdefault(r["book"], {"n": 0, "sum": 0.0, "best": 0.0})
        b["n"] += 1
        b["sum"] += r["score"]
        b["best"] = max(b["best"], r["score"])
    f = (lambda b: b["sum"]) if mode == "sum" else \
        (lambda b: 0.7 * b["best"] + 0.3 * b["sum"] / max(1, b["n"]))
    order = sorted(agg.items(), key=lambda kv: (-f(kv[1]), kv[0]))
    return [b for b, _ in order[:n]], len(agg), ms


def genre(meta, book):
    s1, s2 = meta.get(book, ("?", ""))
    if s2 in ("网络文学", "类型小说"):
        return "noise"
    return "social" if s1 == "人文社科" else "other"


def pos_of(book, order, wants):
    name = book.split("/")[-1]
    for w in wants:
        if w in name:
            try:
                return order.index(book) + 1
            except ValueError:
                return 99
    return None


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    meta = books_meta(lib)
    have = [r[0] for r in lib.execute("SELECT title FROM book")]
    missing = sorted({w for ws in EXPECT.values() for w in ws
                      if not any(w in t for t in have)})
    if missing:
        print("未入库的正解（不计入）：%s" % "、".join(missing))
    want = {k: [w for w in ws if any(w in t for t in have)] for k, ws in EXPECT.items()}
    want = {k: ws for k, ws in want.items() if ws}
    tally = {m: {"hit": 0, "mrr": 0.0, "noise10": 0, "social10": 0} for m in ("sum", "mix")}
    print("%-9s %-5s %-6s %-6s %-6s %s" % ("概念", "方案", "正解最好名次", "噪音@10",
                                           "社科@10", "名次明细"))
    for key, wants in want.items():
        line = {}
        for mode in ("sum", "mix"):
            order, nbook, ms = rank(con, key, mode)
            found = {}
            for bk in order:
                p = pos_of(bk, order, wants)
                if p is not None:
                    nm = [w for w in wants if w in bk.split("/")[-1]][0]
                    found.setdefault(nm, p)
            best = min(found.values()) if found else 99
            noise = sum(1 for b in order[:10] if genre(meta, b) == "noise")
            social = sum(1 for b in order[:10] if genre(meta, b) == "social")
            tally[mode]["hit"] += 1 if best <= 20 else 0
            tally[mode]["mrr"] += 1.0 / best if best < 99 else 0
            tally[mode]["noise10"] += noise
            tally[mode]["social10"] += social
            line[mode] = (best, noise, social, found)
        for mode in ("sum", "mix"):
            best, noise, social, found = line[mode]
            print("%-9s %-5s %-10s %-8d %-8d %s"
                  % (key, mode, ("%d" % best) if best < 99 else "未进前40", noise, social,
                     ", ".join("%s@%d" % kv for kv in sorted(found.items(),
                                                              key=lambda x: x[1])) or "-"))
        print()
    print("=" * 74)
    print("%-6s %8s %8s %10s %10s" % ("方案", "正解进20", "MRR", "噪音@10合计",
                                      "社科@10合计"))
    n = len(want)
    for mode in ("sum", "mix"):
        t = tally[mode]
        print("%-6s %6d/%-2d %8.3f %10d %10d"
              % (mode, t["hit"], n, t["mrr"] / n, t["noise10"], t["social10"]))


if __name__ == "__main__":
    main()
