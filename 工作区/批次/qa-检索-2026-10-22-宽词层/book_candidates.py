#!/usr/bin/env python3
"""List broad-key candidate lines inside one book.

  book_candidates.py --uid B0121 --broad 权力 --limit 30
"""
import argparse
import csv
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BROAD = os.path.join(ROOT, "目录", "概念表-broad.tsv")
DB = os.path.join(ROOT, "目录", "library.db")
CUE = re.compile(r"所谓|指的是|也就是说|本质上|意味着|换句话说|可以定义为|就是|因为|所以")


def load_broad(key):
    with open(BROAD, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["key"] == key:
                return {"key": key,
                        "aliases": [a for a in r["aliases"].split(",") if a],
                        "exclude": [x for x in r["exclude"].split(",") if x and x != "无"]}
    sys.exit("no broad key: " + key)


def book_path(uid):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    row = con.execute("SELECT path FROM book WHERE uid=?", (uid,)).fetchone()
    if not row:
        sys.exit("no book: " + uid)
    return os.path.join(ROOT, "库", row[0], "md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--broad", required=True)
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()
    row = load_broad(a.broad)
    root = book_path(a.uid)
    out = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".md") or name == "README.md":
            continue
        path = os.path.join(root, name)
        text = open(path, encoding="utf-8", errors="replace").read()
        if len(text) < 500:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if len(s) < 30 or len(s) > 400:
                continue
            if s.startswith(("#", ">", "|", "!", "```")):
                continue
            if row["key"] in s or any(x in s for x in row["exclude"]):
                continue
            alias = next((x for x in row["aliases"] if x in s), "")
            if not alias:
                continue
            score = 2 * len(CUE.findall(s)) + min(s.count(alias), 3)
            out.append((score, os.path.relpath(path, ROOT), i, alias, s))
    out.sort(key=lambda x: (-x[0], x[1], x[2]))
    for score, path, line, alias, s in out[:a.limit]:
        print("%s:%d\t%s\t%d\t%s" % (path, line, alias, score, s[:220]))


if __name__ == "__main__":
    main()
