#!/usr/bin/env python3
"""书级排序方案对比台。同一概念，五种书级打分，看前 10 名怎么变。

  book_rank_bench.py 自由 内卷 权力 孤独 正义

五种打分：
  sum      段落得分求和（现状）
  mean     段落得分均值
  best     只看最好的那一段
  0.7b+0.3m  最好段为主，均值当小权重
  b+0.3ln   最好段为主，命中段数只当对数小权重

只读 index.db 与 library.db，不改任何东西。
"""
import csv, math, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
LIBDB = os.path.join(ROOT, "目录", "library.db")

SCHEMES = {
    "sum": lambda b: b["sum"],
    "mean": lambda b: b["sum"] / max(1, b["n"]),
    "best": lambda b: b["best"],
    "0.7b+0.3m": lambda b: 0.7 * b["best"] + 0.3 * b["sum"] / max(1, b["n"]),
    "b+0.3ln": lambda b: b["best"] + 0.3 * math.log1p(b["n"]) * (b["sum"] / max(1, b["n"])),
}


def book_meta(lib):
    out = {}
    for uid, path in lib.execute("SELECT uid, path FROM book"):
        parts = path.split("/")
        out[path] = (uid, parts[0] if parts else "", parts[1] if len(parts) > 1 else "")
    return out


def collect(con, key, depth=400):
    groups, exclude = qsearch.load_concept(key)
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    hits, ms = qsearch.search(con, expr, groups, exclude, depth, 40)
    agg = {}
    for r in hits:
        b = agg.setdefault(r["book"], {"book": r["book"], "n": 0, "sum": 0.0, "best": 0.0})
        b["n"] += 1
        b["sum"] += r["score"]
        b["best"] = max(b["best"], r["score"])
    return agg, ms


def topn(agg, scheme, n=10):
    fn = SCHEMES[scheme]
    rows = sorted(agg.values(), key=lambda b: (-fn(b), b["book"]))[:n]
    return [(r["book"], fn(r), r["n"]) for r in rows]


def genre(meta, book):
    m = meta.get(book)
    if not m:
        return "?"
    _, s1, s2 = m
    if s2 in ("网络文学", "类型小说"):
        return "网文/类型"
    if s1 == "人文社科":
        return "社科"
    if s1 == "文学":
        return "文学"
    return s1


def main():
    concepts = sys.argv[1:] or ["自由", "内卷", "权力", "孤独", "正义"]
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    meta = book_meta(lib)
    for key in concepts:
        agg, ms = collect(con, key)
        print("\n" + "=" * 78)
        print("概念 %s  %d 本候选  %.0f ms" % (key, len(agg), ms))
        print("=" * 78)
        for scheme in SCHEMES:
            rows = topn(agg, scheme, 10)
            noise = sum(1 for b, _, _ in rows if genre(meta, b) == "网文/类型")
            social = sum(1 for b, _, _ in rows if genre(meta, b) == "社科")
            print("\n-- %-10s 前10里 网文/类型 %d 本，社科 %d 本" % (scheme, noise, social))
            for i, (b, sc, n) in enumerate(rows, 1):
                name = b.split("/")[-1][:26]
                print("   %2d %-28s %7.2f  %2d段  %s" % (i, name, sc, n, genre(meta, b)))


if __name__ == "__main__":
    main()
