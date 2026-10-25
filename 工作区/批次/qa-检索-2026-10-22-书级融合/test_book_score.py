#!/usr/bin/env python3
"""book_score 的用例与端到端耗时对照。

  test_book_score.py          六个单元用例，全过打印 PASS 6 cases
  test_book_score.py --perf   60 个概念键上跑基线/本轮两条聚合路径，比 p50 与 p95

只用标准库。单元用例里前五条不连真库，第六条在临时目录里造假 ROOT。
"""
import csv
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(ROOT, "工作区", "脚本")
CONCEPTS = os.path.join(ROOT, "目录", "概念表.tsv")
sys.path.insert(0, SCRIPTS)
import book_score
import lib_fts_cjk as cjk
import qsearch

GROUP = ["礼治"]
TEXT = "礼治秩序在这里成形"


def hit(book, path, line, score, text=TEXT, coverage=1):
    return {"id": 0, "path": path, "book": book, "line": line, "text": text,
            "bm25": 0.0, "kana": 0.0, "coverage": coverage, "score": score}


class Fail(Exception):
    pass


def want(cond, why):
    if not cond:
        raise Fail(why)


def one_book(hits, meta, chapters=None):
    rows = book_score.rank(hits, GROUP, "best_support", [], meta, None)
    want(len(rows) == 1, "应只聚出 1 本，实得 %d" % len(rows))
    b = rows[0]
    if chapters is not None:
        want(b.chapters == chapters, "chapters 应为 %d，实得 %d" % (chapters, b.chapters))
    return b


def case_single_chapter():
    meta = {"文学/单章": book_score.Meta("单章", "文学", "", 1)}
    hits = [hit("文学/单章", "库/文学/单章/md/001.md", 1, 1.0),
            hit("文学/单章", "库/文学/单章/md/001.md", 2, 0.5),
            hit("文学/单章", "库/文学/单章/md/002.md", 1, 0.5)]
    b = one_book(hits, meta, chapters=1)
    want(b.support == 1, "support 应为 1，实得 %d" % b.support)
    want(abs(b.topk - 1.0) < 1e-12, "topk 应为 1.0，实得 %r" % b.topk)


def case_long_book_one_strong():
    meta = {"文学/长书": book_score.Meta("长书", "文学", "", 1000)}
    hits = [hit("文学/长书", "库/文学/长书/md/001.md", 1, 1.0)]
    hits += [hit("文学/长书", "库/文学/长书/md/001.md", i, 0.1) for i in range(2, 8)]
    b = one_book(hits, meta, chapters=1000)
    want(b.support == 1, "support 应为 1，实得 %d" % b.support)
    want(abs(b.best - 1.0) < 1e-12, "best 应为 1.0，实得 %r" % b.best)
    small = {"文学/短书": book_score.Meta("短书", "文学", "", 1)}
    first = [hit("文学/短书", "库/文学/短书/md/001.md", 1, 1.0),
             hit("文学/短书", "库/文学/短书/md/001.md", 2, 0.5),
             hit("文学/短书", "库/文学/短书/md/002.md", 1, 0.5)]
    a = one_book(first, small, chapters=1)
    want(abs(a.score - b.score) < 1e-12,
         "默认分不该随章数变：短书 %r vs 长书 %r" % (a.score, b.score))


def case_long_book_many_strong():
    hits = [hit("文学/多强", "库/文学/多强/md/001.md", i, 1.0) for i in range(1, 11)]
    a = one_book(hits, {"文学/多强": book_score.Meta("多强", "文学", "", 1000)}, 1000)
    b = one_book(hits, {"文学/多强": book_score.Meta("多强", "文学", "", 2000)}, 2000)
    for name, x in (("1000 章", a), ("2000 章", b)):
        want(x.support == 5, "%s 的 support 应为 5，实得 %d" % (name, x.support))
        want(abs(x.topk - 1.0) < 1e-12, "%s 的 topk 应为 1.0，实得 %r" % (name, x.topk))
    want(abs(a.score - b.score) < 1e-12,
         "默认分不该随章数涨：1000 章 %r vs 2000 章 %r" % (a.score, b.score))


def case_no_hits():
    rows = book_score.rank([], GROUP, "best_support", [], {}, None)
    want(rows == [], "无命中应返回空列表，实得 %d 项" % len(rows))


def case_tie_order():
    meta = {"文学/一": book_score.Meta("一", "文学", "", 1),
            "文学/二": book_score.Meta("二", "文学", "", 1)}
    hits = [hit("文学/二", "库/文学/二/md/001.md", 5, 1.0),
            hit("文学/二", "库/文学/二/md/001.md", 2, 1.0),
            hit("文学/二", "库/文学/二/md/001.md", 9, 0.4),
            hit("文学/一", "库/文学/一/md/001.md", 5, 1.0),
            hit("文学/一", "库/文学/一/md/001.md", 2, 1.0),
            hit("文学/一", "库/文学/一/md/001.md", 9, 0.4)]
    order = [b.book for b in book_score.rank(hits, GROUP, "best_support", [], meta, None)]
    want(order == ["文学/一", "文学/二"], "并列时按 book 排，实得 %r" % (order,))
    first = book_score.rank(hits, GROUP, "best_support", [], meta, None)
    second = book_score.rank(hits, GROUP, "best_support", [], meta, None)
    for x, y in zip(first, second):
        want(asdict(x) == asdict(y), "两次聚合结果不一致：%r vs %r" % (x, y))
    for b in first:
        want(b.best_line == 2, "并列时取更小的行号，%s 实得 %d" % (b.book, b.best_line))


def case_missing_derived():
    old = book_score.ROOT
    with tempfile.TemporaryDirectory(prefix="book_score_") as tmp:
        md = os.path.join(tmp, "库", "文学", "x", "md")
        os.makedirs(md)
        for name in ("001.md", "002.md"):
            with open(os.path.join(md, name), "w", encoding="utf-8") as f:
                f.write(TEXT + "\n")
        book_score.ROOT = tmp
        try:
            hits = [hit("文学/x", "库/文学/x/md/001.md", 1, 1.0)]
            one_book(hits, {}, chapters=2)
        finally:
            book_score.ROOT = old


CASES = [
    ("单章书", case_single_chapter),
    ("长书单强章", case_long_book_one_strong),
    ("长书多强章", case_long_book_many_strong),
    ("无命中", case_no_hits),
    ("并列排序", case_tie_order),
    ("缺 derived", case_missing_derived),
]


def run_cases():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("PASS %s" % name)
        except Fail as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    if failed:
        return 1
    print("PASS %d cases" % len(CASES))
    return 0


def pct(xs, p):
    s = sorted(xs)
    k = int(round(p / 100.0 * (len(s) - 1)))
    return s[max(0, min(len(s) - 1, k))]


def median(xs):
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def timed(fn, times=1):
    best = []
    for _ in range(times):
        t0 = time.perf_counter()
        fn()
        best.append((time.perf_counter() - t0) * 1000)
    return best


def perf():
    with open(CONCEPTS, encoding="utf-8-sig") as f:
        keys = [r["key"] for r in csv.DictReader(f, delimiter="\t")][:60]
    t0 = time.perf_counter()
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta(book_score.LIBDB)
    expand = book_score.load_expand(con)
    setup_ms = (time.perf_counter() - t0) * 1000
    base_ms, cur_ms, seen = [], [], []

    def search(groups, exclude, expr):
        hits, _ = qsearch.search(con, expr, groups, exclude, max(20, 2000), 40, False, 2000)
        seen.append((len(hits), hits, groups, exclude))
        return hits

    for i, key in enumerate(keys):
        groups, exclude = qsearch.load_concept(key)
        expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
        steps = [("base", lambda: qsearch.rollup_by_book(
                     search(groups, exclude, expr), con, 20, "mix")),
                 ("cur", lambda: book_score.rank(
                     search(groups, exclude, expr), groups, "best_support", exclude,
                     meta, expand)[:20])]
        if i % 2:
            steps.reverse()
        spent = {}
        for tag, fn in steps:
            t = time.perf_counter()
            fn()
            spent[tag] = (time.perf_counter() - t) * 1000
        base_ms.append(spent["base"])
        cur_ms.append(spent["cur"])
    pool_hits = max(seen, key=lambda x: x[0]) if seen else (0, [], [], [])
    print("题数 %d（概念表前 %d 个键，按题号奇偶交错跑，端到端含搜索）"
          % (len(base_ms), len(keys)))
    print("装载 meta/expand 一次性开销 %.1f ms" % setup_ms)
    print("基线 search+rollup_by_book(mix)  p50 %.1f ms  p95 %.1f ms"
          % (median(base_ms), pct(base_ms, 95)))
    print("本轮 search+rank(best_support)   p50 %.1f ms  p95 %.1f ms"
          % (median(cur_ms), pct(cur_ms, 95)))
    ok = pct(cur_ms, 95) <= pct(base_ms, 95) + 100 and pct(cur_ms, 95) <= 500
    print("规则 本轮 p95 <= 基线 p95 + 100 且 <= 500: %s" % ("PASS" if ok else "FAIL"))
    _, hits, groups, exclude = pool_hits
    if hits:
        base = median(timed(lambda: qsearch.rollup_by_book(hits, con, 20, "mix"), 10))
        cur = median(timed(lambda: book_score.rank(hits, groups, "best_support", exclude,
                                                   meta, expand), 10))
        print("%d 段聚合（10 次中位数）  基线 %.1f ms  本轮 %.1f ms"
              % (len(hits), base, cur))
    else:
        print("没有命中段，跳过 2000 段聚合对照")
    return 0 if ok else 1


def main():
    if "--perf" in sys.argv[1:]:
        return perf()
    return run_cases()


if __name__ == "__main__":
    sys.exit(main())
