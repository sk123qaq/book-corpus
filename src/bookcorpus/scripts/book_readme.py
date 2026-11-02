#!/usr/bin/env python3
"""README 选章：把 md/README.md 当章节目录读，按检索分挑前三章。

  book_readme.py 礼治 -n 5
  book_readme.py --concept 自由 -n 3 --json
  book_readme.py 韩立 --max-chapters 3 --explain

README 只当目录用，正文一律从段落命中里取。选章分数（越大越好）：

  base  = 该章里最高的段落分
  cov   = 该章命中的组词数 / 组词总数
  title = 问句词落在章标题里的比例（只算长度 ≥2 的词）
  score = base * (1 + 0.10*cov + w*title)

`w` 默认 0，即章序只由段落分决定。B3 的对照实验（60 题，title 权重 0 / 0.15 / 0.5 / 1 / 2）
章 hit@10 分别是 54 / 54 / 56 / 56 / 56，最高只涨 3.3 点，没到方案要求的 5 点，
所以默认不启用标题加权；需要时用 --title-weight 打开。README 仍然提供章标题、
卷名、首末章与顺序。

前三章是「按检索分最高的三章」，不是书开头的三章。关联章可能在 800 章之后。
README 缺失时回退到排序后的 md/*.md 文件名，标记 missing；README 不全时
已列的章按 README 顺序，未列的按文件名补在后面，标记 partial。

只读 库/、index.db 与 library.db，不写 md/，不索引 README。
"""
import argparse
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, HERE)
import book_score
import lib_fts_cjk as cjk
import qsearch

DB = book_score.DB
LIBDB = book_score.LIBDB
TOC_RE = re.compile(r"^(\s*)[-*]\s+\[(?P<label>.*)\]\((?P<target>.*)\)\s*$")
VOLUME_RE = re.compile(r"^\s*[-*]\s+\*\*(?P<name>.+?)\*\*\s*$")
NAME_RE = re.compile(r"^(\d+[_\-．.]?)?\s*(?P<title>.+?)(\.md)?$")


def toc_section(text):
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## 目录":
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def title_from_name(name):
    m = NAME_RE.match(name)
    return (m.group("title") if m else name).strip()


def usable(target):
    t = target.strip()
    if not t or t.startswith("#") or t.startswith("//") or re.match(r"^[a-z]+:", t):
        return None
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1]
    t = t.replace("%20", " ")
    if not t.endswith(".md"):
        return None
    return t


def entry(book, target, title, order, volume):
    path = os.path.normpath("库/%s/md/%s" % (book, target)).replace(os.sep, "/")
    if not path.startswith("库/%s/md/" % book) or ".." in path.split("/"):
        return None
    return {"book": book, "path": path, "title": title or title_from_name(target),
            "order": order, "volume": volume}


def readme_toc(book, root=None):
    """返回 (章节列表, readme_status)。列表顺序是 README 的目录顺序。"""
    base = os.path.join(ROOT if root is None else root, "库", book, "md")
    try:
        files = sorted(f for f in os.listdir(base) if f.endswith(".md") and f != "README.md")
    except OSError:
        return [], "missing"
    rp = os.path.join(base, "README.md")
    fallback = [{"book": book, "path": "库/%s/md/%s" % (book, f),
                 "title": title_from_name(f), "order": i, "volume": ""}
                for i, f in enumerate(files)]
    if not os.path.exists(rp):
        return fallback, "missing"
    with open(rp, encoding="utf-8", errors="ignore") as fh:
        lines = toc_section(fh.read())
    if lines is None:
        return fallback, "partial"
    entries, seen, stale, volume, order = [], set(), 0, "", 0
    for line in lines:
        vm = VOLUME_RE.match(line)
        if vm:
            volume = vm.group("name").strip()
            continue
        m = TOC_RE.match(line)
        if not m:
            continue
        target = usable(m.group("target"))
        if target is None:
            continue
        if target not in files:
            stale += 1
            continue
        if target in seen:
            continue
        e = entry(book, target, m.group("label").strip(), order, volume)
        if e is None:
            stale += 1
            continue
        seen.add(target)
        entries.append(e)
        order += 1
    for f in files:
        if f not in seen:
            entries.append({"book": book, "path": "库/%s/md/%s" % (book, f),
                            "title": title_from_name(f), "order": order,
                            "volume": ""})
            order += 1
    status = "partial" if stale or len(seen) != len(files) else "ok"
    return entries, status


def title_overlap(title, terms):
    words = [t for t in terms if len(t) >= 2]
    if not words:
        return 0.0
    return sum(1 for t in words if t in title) / len(words)


def select(hits, groups, terms, toc, max_chapters=3, title_weight=0.0):
    """在给定命中里按检索分挑章，最多 max_chapters 章。"""
    order = {e["path"]: e for e in toc}
    by_path = {}
    for r in hits:
        if r["path"] in order:
            by_path.setdefault(r["path"], []).append(r)
    rows = []
    for path, rs in by_path.items():
        best = max(rs, key=lambda r: (r["score"], -r["line"]))
        cov = len({g for g in groups if any(g in r["text"] for r in rs)}) / max(1, len(groups))
        title = title_overlap(order[path]["title"], terms)
        score = best["score"] * (1 + 0.10 * cov + title_weight * title)
        rows.append({
            "book": order[path]["book"], "chapter_path": path,
            "chapter_title": order[path]["title"], "volume": order[path]["volume"],
            "line": best["line"], "snippet": re.sub(r"\s+", " ", best["text"])[:150],
            "score": round(score, 4), "reason": "best=%.2f cov=%.2f title=%.2f"
            % (best["score"], cov, title), "readme_status": "",
            "chapter_order": order[path]["order"], "paragraphs": len(rs),
        })
    rows.sort(key=lambda r: (-r["score"], r["chapter_order"]))
    return rows[:max_chapters]


def context_line(toc):
    if len(toc) < 2:
        return ""
    return "首章 %s / 末章 %s" % (toc[0]["title"], toc[-1]["title"])


def search(con, terms, exclude=(), pool=2000):
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    return qsearch.search(con, expr, terms, list(exclude), 2000, 40, False, pool)


def run(query, con, meta, expand, scheme="best_support", concept=None, limit=10,
        max_chapters=3, pool=2000, title_weight=0.0):
    if concept:
        groups, exclude = qsearch.load_concept(concept)
        label = concept
    else:
        groups = cjk.split_groups(query)
        exclude = []
        label = query
    hits, ms = search(con, groups, exclude, pool)
    ranked = book_score.rank(hits, groups, scheme, exclude, meta, expand)
    out = []
    for b in ranked[:limit]:
        toc, status = readme_toc(b.book)
        rows = select([r for r in hits if r["book"] == b.book], groups, groups, toc,
                      max_chapters, title_weight)
        for r in rows:
            r["readme_status"] = status
        out.append({"book": b.book, "title": b.title, "chapters": b.chapters,
                    "book_score": b.score, "readme_status": status,
                    "context": context_line(toc) if len(toc) > 200 else "",
                    "selected": rows})
    return {"query": label, "scheme": scheme, "ms": round(ms, 1), "hits": len(hits),
            "books": out}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--concept")
    ap.add_argument("-n", "--limit", type=int, default=10)
    ap.add_argument("--max-chapters", type=int, default=3)
    ap.add_argument("--book-score", default="best_support", choices=list(book_score.SCHEMES))
    ap.add_argument("--pool", type=int, default=2000)
    ap.add_argument("--title-weight", type=float, default=0.0,
                    help="章标题重合度权重，默认 0（对照实验未过 5 点门槛）")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--libdb", default=LIBDB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args(argv)
    if not (a.query or a.concept):
        ap.error("要给查询词或 --concept")
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    meta = book_score.load_meta(a.libdb)
    expand = book_score.load_expand(con)
    res = run(a.query, con, meta, expand, a.book_score, a.concept, a.limit,
              a.max_chapters, a.pool, a.title_weight)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("%s  %d 本  %s  %.0f ms" % (res["query"], len(res["books"]), res["scheme"],
                                      res["ms"]), file=sys.stderr)
    for i, b in enumerate(res["books"], 1):
        print("%2d %-46s ch=%-5d %s  %.2f" % (i, b["book"], b["chapters"],
                                              b["readme_status"], b["book_score"]))
        if b["context"]:
            print("   " + b["context"])
        for r in b["selected"]:
            print("   [%s] %s:%d  %.2f  %s" % (r["chapter_title"], r["chapter_path"],
                                               r["line"], r["score"], r["reason"]))
            if a.explain:
                print("      " + r["snippet"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
