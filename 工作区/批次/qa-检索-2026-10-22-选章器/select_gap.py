#!/usr/bin/env python3
"""按词表挑出「前缀没点到概念词」的章，输出 build_expand.py --only-file 清单。

  select_gap.py --vocab union --missing-only --out gap-union.tsv

词表：
  t126     目录/概念表.tsv 的 126 个键 + 别名
  booktop  每本书 book_concept 的前 10 个概念 + 别名
  broad    前缀提示词里的 10 个宽主题
  union    以上三者任一命中就算命中

只读 index.db 与概念表，产物默认写本目录。
"""
import argparse
import collections
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
IDX = os.path.join(ROOT, "目录", "检索", "index.db")
CONCEPT_TSV = os.path.join(ROOT, "目录", "概念表.tsv")
BROAD = ["自由", "权力", "服从", "孤独", "正义", "秩序", "爱", "死亡", "身份", "家庭"]


def load_concepts():
    out = {}
    for r in csv.DictReader(open(CONCEPT_TSV, encoding="utf-8-sig"), delimiter="\t"):
        out[r["key"]] = [r["key"]] + [a for a in r["aliases"].split(",") if a]
    return out


def book_top(con, limit=10):
    out = collections.defaultdict(list)
    for book, key in con.execute(
            "SELECT book, key FROM book_concept ORDER BY book, score DESC"):
        out[book].append(key)
    return {b: ks[:limit] for b, ks in out.items()}


def hit(text, terms):
    low = text.lower()
    return [t for t in terms if t.lower() in low]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", default="union", choices=["t126", "booktop", "broad", "union"])
    ap.add_argument("--missing-only", action="store_true", help="排除已经有 queries 的章")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    concepts = load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % IDX, uri=True)
    top = book_top(con)
    queries = {r[0] for r in con.execute("SELECT path FROM expand WHERE kind='queries'")}
    rows = con.execute("SELECT path, text FROM expand WHERE kind='prefix'").fetchall()
    out = []
    for path, text in rows:
        if not path.startswith("库/"):
            continue
        book = path[len("库/"):].split("/md/")[0]
        t126 = hit(text, [t for ts in concepts.values() for t in ts])
        bt = [t for k in top.get(book, []) for t in concepts.get(k, [k])]
        tbook = hit(text, bt)
        tb = hit(text, BROAD)
        if args.vocab == "t126":
            hits = t126
        elif args.vocab == "booktop":
            hits = tbook
        elif args.vocab == "broad":
            hits = tb
        else:
            hits = list(dict.fromkeys(t126 + tbook + tb))
        if hits:
            continue
        if args.missing_only and path in queries:
            continue
        out.append({"path": path, "book": book, "chapter": os.path.basename(path),
                    "prefix": text, "has_queries": int(path in queries)})
    out.sort(key=lambda r: r["path"])
    dest = args.out or os.path.join(HERE, "gap-%s.tsv" % args.vocab)
    with open(dest, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["book", "chapter", "prefix", "has_queries", "path"])
        for r in out:
            w.writerow([r["book"], r["chapter"], r["prefix"], r["has_queries"], r["path"]])
    per = 60 / 84086
    print("vocab=%s missing_only=%s gap=%d cost≈$%.1f -> %s" % (
        args.vocab, args.missing_only, len(out), len(out) * per, dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
