#!/usr/bin/env python3
"""book_readme 的用例、章节数核对、README 选章对照与耗时探针。

  test_book_readme.py            五个单元用例
  test_book_readme.py --counts   20 本书：解析出的章数 vs md/*.md 文件数
  test_book_readme.py --compare  60 题 gold：README 选章 vs 只用段落的选章
  test_book_readme.py --perf     8 本书解析耗时 + 20 题交错对照

只用标准库。
"""
import csv
import os
import random
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(ROOT, "工作区", "脚本")
sys.path.insert(0, SCRIPTS)
import book_readme
import book_score
import lib_fts_cjk as cjk
import qsearch

GOLD = os.path.join(HERE, "book_gold.tsv")


class Fail(Exception):
    pass


def want(cond, why):
    if not cond:
        raise Fail(why)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_book(tmp, book, files, readme=None):
    md = os.path.join(tmp, "库", book, "md")
    for name in files:
        write(os.path.join(md, name), "正文\n")
    if readme is not None:
        write(os.path.join(md, "README.md"), readme)
    return md


def hit(book, name, line, score, text="礼治秩序"):
    return {"id": 0, "path": "库/%s/md/%s" % (book, name), "book": book, "line": line,
            "text": text, "bm25": 0.0, "kana": 0.0, "coverage": 1, "score": score}


README_OK = """# 测试书

- 作者：某人
- 章节：3

## 目录

- [前言](00_前言.md)
- **第一卷上**
  - [礼治秩序](01_礼治秩序.md)
  - [无讼](02_无讼.md)

## 未收录（卷首/卷尾页）

- 封面 — front/back matter
"""


def case_normal():
    with tempfile.TemporaryDirectory() as tmp:
        book = "文学/测试"
        make_book(tmp, book, ["00_前言.md", "01_礼治秩序.md", "02_无讼.md"], README_OK)
        toc, status = book_readme.readme_toc(book, root=tmp)
        want(status == "ok", "状态应为 ok，实得 %s" % status)
        want([e["title"] for e in toc] == ["前言", "礼治秩序", "无讼"],
             "章标题或顺序不对：%r" % [e["title"] for e in toc])
        want(toc[1]["volume"] == "第一卷上", "卷名没带上：%r" % toc[1]["volume"])
        rows = book_readme.select(
            [hit(book, "01_礼治秩序.md", 3, 9.0), hit(book, "02_无讼.md", 4, 5.0)],
            ["礼治"], ["礼治"], toc)
        want(rows[0]["chapter_title"] == "礼治秩序", "标题命中应排第一，实得 %r"
             % rows[0]["chapter_title"])


README_LINKS = """# 链接书

## 目录

- ![封面](cover.png)
- [锚点](#目录)
- [外链](https://example.com/x.md)
- [文本](notes.txt)
- [带空格的章](<03_带 空格.md>)
- [正常章](04_正常.md)
"""


def case_links():
    with tempfile.TemporaryDirectory() as tmp:
        book = "文学/链接"
        make_book(tmp, book, ["03_带 空格.md", "04_正常.md"], README_LINKS)
        toc, status = book_readme.readme_toc(book, root=tmp)
        want(status == "ok", "状态应为 ok，实得 %s" % status)
        want([e["title"] for e in toc] == ["带空格的章", "正常章"],
             "只应收 md 链接，实得 %r" % [e["title"] for e in toc])
        want(toc[0]["path"].endswith("03_带 空格.md"), "带空格的链接解析错：%s"
             % toc[0]["path"])


def case_missing():
    with tempfile.TemporaryDirectory() as tmp:
        book = "文学/无目录"
        make_book(tmp, book, ["02_乙.md", "01_甲.md"])
        toc, status = book_readme.readme_toc(book, root=tmp)
        want(status == "missing", "状态应为 missing，实得 %s" % status)
        want([e["title"] for e in toc] == ["甲", "乙"], "应按文件名排，实得 %r"
             % [e["title"] for e in toc])


def case_long():
    with tempfile.TemporaryDirectory() as tmp:
        book = "文学/长书"
        names = ["%04d_第%d章.md" % (i, i) for i in range(1, 1001)]
        make_book(tmp, book, names, "# 长书\n\n## 目录\n\n" + "".join(
            "- [第%d章](%s)\n" % (i, n) for i, n in enumerate(names, 1)))
        toc, status = book_readme.readme_toc(book, root=tmp)
        want(status == "ok" and len(toc) == 1000, "应解析 1000 章，实得 %d" % len(toc))
        hits = [hit(book, names[i], 1, 10.0 + i) for i in (0, 500, 999)]
        rows = book_readme.select(hits, ["礼治"], ["礼治"], toc)
        want(len(rows) == 3, "只应返回 3 章，实得 %d" % len(rows))
        want(rows[0]["chapter_title"] == "第1000章", "应按检索分降序，实得 %r"
             % rows[0]["chapter_title"])
        want(book_readme.context_line(toc).startswith("首章 第1章"), "首尾章名不对")


def case_single():
    with tempfile.TemporaryDirectory() as tmp:
        book = "人文社科/单章"
        make_book(tmp, book, ["中县干部.md"],
                  "# 单章\n\n## 目录\n\n- [中县干部](中县干部.md)\n")
        toc, status = book_readme.readme_toc(book, root=tmp)
        rows = book_readme.select([hit(book, "中县干部.md", 7, 8.0)], ["干部"], ["干部"],
                                  toc, max_chapters=3)
        want(len(rows) == 1, "单章书只应返回 1 章，实得 %d" % len(rows))
        want(rows[0]["chapter_path"].endswith("中县干部.md"), "章路径不对")


CASES = [
    ("正常 README", case_normal),
    ("相对链接与跳过规则", case_links),
    ("缺 README", case_missing),
    ("长 README", case_long),
    ("单章书", case_single),
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


def gold_books():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    seen = []
    for r in rows:
        b = r["target_book"].split("|")[0]
        if b not in seen:
            seen.append(b)
    return rows, seen


def counts():
    rows, books = gold_books()
    bad = 0
    for b in books[:20]:
        toc, status = book_readme.readme_toc(b)
        base = os.path.join(ROOT, "库", b, "md")
        files = [f for f in os.listdir(base) if f.endswith(".md") and f != "README.md"]
        ok = len(toc) == len(files)
        bad += 0 if ok else 1
        print("%s %-52s toc=%-5d files=%-5d %s" % (
            "PASS" if ok else "FAIL", b, len(toc), len(files), status))
    print("核对 %d 本，对不上 %d 本" % (min(20, len(books)), bad))
    return 1 if bad else 0


def questions(limit=None):
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    return rows[:limit] if limit else rows


def hits_for(con, row, pool=2000):
    terms = [t.strip() for t in row["terms"].split(",") if t.strip()]
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    return terms, qsearch.search(con, expr, terms, [], 2000, 40, False, pool)[0]


def para_chapters(hits, book, max_chapters=3):
    rs = [r for r in hits if r["book"] == book]
    best = {}
    for r in rs:
        cur = best.get(r["path"])
        if cur is None or r["score"] > cur["score"]:
            best[r["path"]] = r
    order = sorted(best.values(), key=lambda r: (-r["score"], r["path"]))
    return [r["path"] for r in order[:max_chapters]]


def compare():
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta(book_score.LIBDB)
    expand = book_score.load_expand(con)
    cache = {}
    book_hit = chapter_para = chapter_readme = 0
    rows = questions()
    for row in rows:
        terms, hits = hits_for(con, row)
        ranked = book_score.rank(hits, terms, "best_support", [], meta, expand)[:10]
        want_books = set(row["target_book"].split("|"))
        chaps = set(row["target_chapters"].split("|"))
        if want_books & {b.book for b in ranked}:
            book_hit += 1
        got_para, got_readme = set(), set()
        for b in ranked:
            got_para.update(para_chapters(hits, b.book))
            if b.book not in cache:
                cache[b.book] = book_readme.readme_toc(b.book)
            toc, _ = cache[b.book]
            got_readme.update(r["chapter_path"] for r in
                              book_readme.select([r for r in hits if r["book"] == b.book],
                                                 terms, terms, toc))
        chapter_para += 1 if chaps & got_para else 0
        chapter_readme += 1 if chaps & got_readme else 0
    n = len(rows)
    print("题数 %d" % n)
    print("书 hit@10              %.3f" % (book_hit / n))
    print("章 hit@10 只用段落     %.3f  (%d/%d)" % (chapter_para / n, chapter_para, n))
    print("章 hit@10 README 选章  %.3f  (%d/%d)" % (chapter_readme / n, chapter_readme, n))
    delta = (chapter_readme - chapter_para) / n
    print("章 hit@10 差 %+.3f 点" % delta)
    ok = delta >= 0.05
    print("规则 章 hit@10 提升 >= 5 点: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def perf():
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta(book_score.LIBDB)
    expand = book_score.load_expand(con)
    rows = questions()
    cache = {}
    t0 = time.perf_counter()
    for b in gold_books()[1][:8]:
        cache[b] = book_readme.readme_toc(b)
    parse_ms = (time.perf_counter() - t0) * 1000
    base_ms, cur_ms = [], []
    for i, row in enumerate(rows[:20]):
        terms, hits = hits_for(con, row)
        top = book_score.rank(hits, terms, "best_support", [], meta, expand)[:10]

        def base():
            for b in top:
                para_chapters(hits, b.book)

        def cur():
            for b in top:
                if b.book not in cache:
                    cache[b.book] = book_readme.readme_toc(b.book)
                toc, _ = cache[b.book]
                book_readme.select([r for r in hits if r["book"] == b.book], terms, terms, toc)

        steps = [("base", base), ("cur", cur)]
        if i % 2:
            steps.reverse()
        spent = {}
        for tag, fn in steps:
            t = time.perf_counter()
            fn()
            spent[tag] = (time.perf_counter() - t) * 1000
        base_ms.append(spent["base"])
        cur_ms.append(spent["cur"])
    def pct(xs, p):
        s = sorted(xs)
        return s[max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))]
    print("8 本书 README 解析 %.1f ms（一次性，进程内缓存）" % parse_ms)
    print("段落选章  p50 %.1f ms  p95 %.1f ms" % (pct(base_ms, 50), pct(base_ms, 95)))
    print("README 选章 p50 %.1f ms  p95 %.1f ms" % (pct(cur_ms, 50), pct(cur_ms, 95)))
    ok = pct(cur_ms, 95) <= 500 and pct(cur_ms, 95) <= pct(base_ms, 95) + 200
    print("规则 README p95 <= 500 ms 且 <= 段落 p95 + 200 ms: %s"
          % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    if "--counts" in sys.argv:
        return counts()
    if "--compare" in sys.argv:
        return compare()
    if "--perf" in sys.argv:
        return perf()
    return run_cases()


if __name__ == "__main__":
    sys.exit(main())
