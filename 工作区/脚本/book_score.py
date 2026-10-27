#!/usr/bin/env python3
"""书级打分：把段落命中聚成书，按多种书级方案排序。

  book_score.py 礼治 -n 10
  book_score.py 礼治 -n 5 --explain
  book_score.py --concept 自由 --book-score rrf5 --json
  book_score.py 内卷 规训 --pool all

每本书的原料字段（BookScore）固定：book, title, type, chapters, best, best_path,
best_line, best_text, support, topk, coverage, score, groups, has_prefix,
has_queries, scores, hits, mean, total_groups。

默认方案 best_support：
  score    = 0.60*best + 0.25*topk + 0.15*coverage + 0.05*log1p(support)
  best     = 本书最高段落分
  topk     = 最好的 min(3, support) 段分数的均值
  support  = min(5, 段落分 >= 0.75*best 的段数)
  coverage = 命中的组词数 / 组词总数

其余方案见 SCHEMES：sum, mean, best, mix, best_support, best_top3_support,
rrf5, best_support_length。只有 best_support_length 含长度项
（-0.02*log1p(chapters)），默认方案不含。

只读 目录/检索/index.db 与 目录/library.db。
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
LIBDB = os.path.join(ROOT, "目录", "library.db")

CHAPTER_SQL = ("SELECT b.path, b.title, b.subject1, b.subject2, d.chapters "
               "FROM book b LEFT JOIN derived d ON d.uid = b.uid AND d.kind = 'md'")


@dataclass
class Meta:
    title: str = ""
    subject1: str = ""
    subject2: str = ""
    chapters: int = 0


@dataclass
class BookScore:
    book: str
    title: str
    type: str
    chapters: int
    best: float
    best_path: str
    best_line: int
    best_text: str
    support: int
    topk: float
    coverage: float
    score: float
    groups: list
    has_prefix: bool
    has_queries: bool
    scores: list
    hits: int
    mean: float
    total_groups: int


def genre(meta, book):
    m = (meta or {}).get(book)
    if not m:
        return "?"
    if m.subject2 in ("网络文学", "类型小说"):
        return "网文/类型"
    if m.subject1 == "人文社科":
        return "社科"
    if m.subject1 == "文学":
        return "文学"
    return m.subject1


def load_meta(libdb=LIBDB):
    lib = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    out = {}
    try:
        for path, title, s1, s2, chapters in lib.execute(CHAPTER_SQL):
            out[path] = Meta(title=title or "", subject1=s1 or "", subject2=s2 or "",
                             chapters=chapters or 0)
    finally:
        lib.close()
    return out


def load_expand(con):
    out = {}
    for path, kind in con.execute("SELECT DISTINCT path, kind FROM expand"):
        book = path.split("/md/")[0]
        if book.startswith("库/"):
            book = book[2:]
        out.setdefault(book, set()).add(kind)
    return out


def count_md(book, root=None):
    base = os.path.join(ROOT if root is None else root, "库", book, "md")
    try:
        with os.scandir(base) as it:
            return sum(1 for e in it if e.is_file() and e.name.endswith(".md"))
    except OSError:
        return 0


def matched_groups(text, groups, exclude):
    body = text
    if exclude:
        body = re.sub("|".join(map(re.escape, exclude)), "", body)
    if not qsearch.ASCII_ALPHA.search("".join(groups)):
        return [g for g in groups if g in body]
    low = body.lower()
    return [g for g in groups if qsearch.group_in(g.lower(), low)]


def top3_mean(scores):
    k = min(3, len(scores))
    return sum(scores[:k]) / k if k else 0.0


SCHEMES = {
    "sum": lambda b: sum(b.scores),
    "mean": lambda b: b.mean,
    "best": lambda b: b.best,
    "mix": lambda b: 0.7 * b.best + 0.3 * b.mean,
    "best_support": lambda b: b.score,
    "best_top3_support": lambda b: (0.60 * b.best + 0.25 * top3_mean(b.scores)
                                    + 0.15 * b.coverage + 0.05 * math.log1p(b.support)),
    "rrf5": lambda b: sum(1.0 / (60 + i) for i in range(1, min(5, len(b.scores)) + 1)),
    "best_support_length": lambda b: b.score - 0.02 * math.log1p(b.chapters),
}


def aggregate(book, rows, groups, exclude, meta, expand, total_groups):
    scores = sorted((r["score"] for r in rows), reverse=True)
    if scores[0] <= 0:
        return None
    best = sorted(rows, key=lambda r: (-r["score"], r["path"], r["line"]))[0]
    support = min(5, sum(1 for s in scores if s >= 0.75 * scores[0]))
    topk = sum(scores[:min(3, support)]) / min(3, support)
    seen = []
    for r in rows:
        for g in matched_groups(r["text"], groups, exclude):
            if g not in seen:
                seen.append(g)
    total = len(groups) if total_groups is None else total_groups
    coverage = len(seen) / max(1, total)
    m = (meta or {}).get(book)
    chapters = m.chapters if m and m.chapters else count_md(book)
    kinds = (expand or {}).get(book) or ()
    return BookScore(
        book=book,
        title=m.title if m and m.title else book,
        type=genre(meta, book),
        chapters=chapters,
        best=scores[0],
        best_path=best["path"],
        best_line=best["line"],
        best_text=best["text"],
        support=support,
        topk=topk,
        coverage=coverage,
        score=(0.60 * scores[0] + 0.25 * topk + 0.15 * coverage
               + 0.05 * math.log1p(support)),
        groups=seen,
        has_prefix="prefix" in kinds,
        has_queries="queries" in kinds,
        scores=scores,
        hits=len(rows),
        mean=sum(scores) / len(scores),
        total_groups=total,
    )


def rank(hits, groups, scheme, exclude=(), meta=None, expand=None, total_groups=None):
    if scheme not in SCHEMES:
        raise KeyError("没有这个书级方案：%s（可选 %s）" % (scheme, " ".join(SCHEMES)))
    by_book = {}
    for r in hits:
        by_book.setdefault(r["book"], []).append(r)
    out = []
    for book, rows in by_book.items():
        b = aggregate(book, rows, groups, exclude, meta, expand, total_groups)
        if b is not None:
            out.append(b)
    fn = SCHEMES[scheme]
    out.sort(key=lambda b: (-fn(b), b.book, b.best_path, b.best_line))
    return out


def parse_pool(text):
    named = {"low": 400, "medium": 2000, "all": 0}
    if text in named:
        return named[text]
    return int(text)


def snippet(text):
    return re.sub(r"\s+", " ", text)[:150]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--concept")
    ap.add_argument("-n", "--limit", type=int, default=10)
    ap.add_argument("--book-score", default="best_support", choices=list(SCHEMES))
    ap.add_argument("--pool", default="2000", help="取多少段进打分：low=400 / medium=2000 / all=不限 / 整数")
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--libdb", default=LIBDB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args(argv)
    if not (a.query or a.concept):
        ap.error("要给查询词或 --concept")
    if a.concept:
        groups, exclude = qsearch.load_concept(a.concept)
        label = a.concept
    else:
        groups = cjk.split_groups(a.query)
        exclude = []
        label = a.query
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    try:
        pool = parse_pool(a.pool)
    except ValueError:
        ap.error("--pool 只认 low / medium / all 或整数")
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    hits, ms = qsearch.search(con, expr, groups, exclude, max(a.limit, 2000),
                              a.min_chars, False, pool)
    meta = load_meta(a.libdb)
    expand = load_expand(con)
    rows = rank(hits, groups, a.book_score, exclude, meta, expand)[:a.limit]
    if a.json:
        print(json.dumps([asdict(b) for b in rows], ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("[]")
        return 0
    print("%s  %d 本  %s  %.0f ms" % (label, len(rows), a.book_score, ms), file=sys.stderr)
    fn = SCHEMES[a.book_score]
    for i, b in enumerate(rows, 1):
        if a.explain:
            print("%2d %-46s ch=%-5d best=%.3f topk=%.3f support=%d cov=%.2f "
                  "groups=%s prefix=%d queries=%d score=%.3f"
                  % (i, b.book, b.chapters, b.best, b.topk, b.support, b.coverage,
                     ",".join(b.groups) or "-", int(b.has_prefix), int(b.has_queries),
                     fn(b)))
            print("   %s:%d" % (b.best_path, b.best_line))
        else:
            print("%2d %-46s %2d段  %.2f  %-8s %s:%d"
                  % (i, b.book, b.hits, fn(b), b.type, b.best_path, b.best_line))
            print("   " + snippet(b.best_text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
