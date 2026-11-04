#!/usr/bin/env python3
"""书级排序方案对比台。同一概念，八种书级打分，看前 10 名怎么变。

  book_rank_bench.py 自由 内卷 权力 孤独 正义
  book_rank_bench.py --depth 400 自由    # 复现 2026-10-22 之前的旧读数

每个概念默认取 2000 段进打分，与 `qsearch --book` 同口径；旧版写死 400。

八种打分取自 book_score.SCHEMES：
  sum                  段落得分求和（旧口径，留作对照）
  mean                 段落得分均值
  best                 只看最好的那一段
  mix                  0.7*best + 0.3*mean
  best_support         0.6*best + 0.25*top3 + 0.15*coverage + 0.05*log1p(support)
  best_top3_support    同上，top3 取最好的 3 段，不受 support 封顶
  rrf5                 前 5 段各 1/(60+i)
  best_support_length  best_support - 0.02*log1p(chapters)

只读 index.db 与 library.db，不改任何东西。
"""

import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import book_score
import lib_fts_cjk as cjk
import qsearch

DB = book_score.DB
LIBDB = book_score.LIBDB
SCHEMES = book_score.SCHEMES
genre = book_score.genre


def collect(con, key, depth=2000):
    groups, exclude = qsearch.load_concept(key)
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    hits, ms = qsearch.search(con, expr, groups, exclude, depth, 40, False, depth)
    return hits, groups, exclude, ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("concepts", nargs="*", help="概念键，默认 自由 内卷 规训 异化 荒诞")
    ap.add_argument(
        "--depth", type=int, default=2000, help="每个概念取多少段进打分，默认 2000（与 qsearch --book 同口径）"
    )
    a = ap.parse_args()
    concepts = a.concepts or ["自由", "内卷", "规训", "异化", "荒诞"]
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    meta = book_score.load_meta(LIBDB)
    expand = book_score.load_expand(con)
    keys = {r[0] for r in con.execute("SELECT DISTINCT key FROM book_concept")}
    missing = [k for k in concepts if k not in keys]
    if missing:
        print("概念表或索引里没有这些键，跳过：%s" % " ".join(missing))
    for key in [k for k in concepts if k in keys]:
        hits, groups, exclude, ms = collect(con, key, a.depth)
        print("\n" + "=" * 78)
        print("概念 %s  %d 段候选  %.0f ms" % (key, len(hits), ms))
        print("=" * 78)
        for scheme in SCHEMES:
            rows = book_score.rank(hits, groups, scheme, exclude, meta, expand)[:10]
            noise = sum(1 for b in rows if genre(meta, b.book) == "网文/类型")
            social = sum(1 for b in rows if genre(meta, b.book) == "社科")
            print("\n-- %-20s 前10里 网文/类型 %d 本，社科 %d 本" % (scheme, noise, social))
            fn = SCHEMES[scheme]
            for i, b in enumerate(rows, 1):
                print("   %2d %-28s %7.2f  %2d段  %s" % (i, b.book.split("/")[-1][:26], fn(b), b.hits, b.type))


if __name__ == "__main__":
    main()
