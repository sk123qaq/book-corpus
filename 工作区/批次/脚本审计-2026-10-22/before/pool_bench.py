#!/usr/bin/env python3
"""取数上限（pool）的档位标定台。

  pool_bench.py                  # 默认 10 个概念 × 4 档
  pool_bench.py 自由 内卷        # 指定概念

对每个概念、每个 pool，算三件事：耗时、候选书数、前 20 名与「全取」的重合度。
书级打分用 0.7*best + 0.3*mean（五个场景里噪音最低且社科不掉的那档）。
"""
import collections, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
LIBDB = os.path.join(ROOT, "目录", "library.db")
POOLS = [400, 2000, 10000, 0]
DEFAULT_KEYS = ["自由", "群体", "异化", "规训", "荒诞", "铁笼", "内卷", "独立判断",
                "工具理性", "平庸之恶"]


def books_meta(lib):
    out = {}
    for uid, path in lib.execute("SELECT uid, path FROM book"):
        p = path.split("/")
        out[path] = (p[0] if p else "", p[1] if len(p) > 1 else "")
    return out


def rank(con, key, pool, n=20):
    groups, exclude = qsearch.load_concept(key)
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    t = time.time()
    hits, _ = qsearch.search(con, expr, groups, exclude, 10 ** 6, 40, False, pool)
    ms = (time.time() - t) * 1000
    agg = {}
    for r in hits:
        b = agg.setdefault(r["book"], {"n": 0, "sum": 0.0, "best": 0.0})
        b["n"] += 1
        b["sum"] += r["score"]
        b["best"] = max(b["best"], r["score"])
    score = lambda b: 0.7 * b["best"] + 0.3 * b["sum"] / max(1, b["n"])
    order = sorted(agg.items(), key=lambda kv: (-score(kv[1]), kv[0]))
    return [b for b, _ in order[:n]], len(agg), len(hits), ms


def genre(meta, book):
    s1, s2 = meta.get(book, ("?", ""))
    if s2 in ("网络文学", "类型小说"):
        return "网文/类型"
    return "社科" if s1 == "人文社科" else "其他"


def main():
    keys = sys.argv[1:] or DEFAULT_KEYS
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    meta = books_meta(lib)
    print("%-10s %6s %7s %7s %7s %8s %8s" % ("概念", "pool", "候选书", "打分段", "毫秒",
                                             "噪音", "社科"))
    total = collections.defaultdict(lambda: {"ms": 0, "noise": 0, "sh": 0, "ov10": 0})
    for key in keys:
        full, _, _, _ = rank(con, key, 0)
        for pool in POOLS:
            top, nbook, nseg, ms = rank(con, key, pool)
            noise = sum(1 for b in top if genre(meta, b) == "网文/类型")
            sh = sum(1 for b in top if genre(meta, b) == "社科")
            ov10 = len(set(top[:10]) & set(full[:10]))
            print("%-10s %6s %7d %7d %7.0f %8d %8d   前10与全取重合 %d/10"
                  % (key, pool or "全部", nbook, nseg, ms, noise, sh, ov10))
            total[pool]["ms"] += ms
            total[pool]["noise"] += noise
            total[pool]["sh"] += sh
            total[pool]["ov10"] += ov10
        print()
    print("=" * 78)
    print("%-8s %8s %8s %8s %10s" % ("pool", "平均毫秒", "噪音合计", "社科合计",
                                     "前10重合"))
    for pool in POOLS:
        t = total[pool]
        print("%-8s %8.0f %8d %8d %8.1f/10"
              % (pool or "全部", t["ms"] / len(keys), t["noise"], t["sh"],
                 t["ov10"] / len(keys)))


if __name__ == "__main__":
    main()
