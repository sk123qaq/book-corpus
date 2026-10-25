#!/usr/bin/env python3
"""Extract one target line per spec from a book chapter.

  extract_targets.py specs.tsv
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


def load_broad():
    out = {}
    with open(BROAD, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            out[r["key"]] = {
                "aliases": [a for a in r["aliases"].split(",") if a],
                "exclude": [x for x in r["exclude"].split(",") if x and x != "无"]}
    return out


def book_path(uid):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    row = con.execute("SELECT path FROM book WHERE uid=?", (uid,)).fetchone()
    if not row:
        return ""
    return os.path.join(ROOT, "库", row[0], "md")


def pick(spec, broad):
    root = book_path(spec["uid"])
    if not root or not os.path.isdir(root):
        return None, "no md"
    names = [n for n in sorted(os.listdir(root))
             if n.endswith(".md") and spec["chapter"] in n and n != "README.md"]
    if not names:
        return None, "no chapter"
    name = names[0]
    path = os.path.join(root, name)
    body = open(path, encoding="utf-8", errors="replace").read()
    aliases = [spec["alias"]] if spec["alias"] else broad["aliases"]
    best = None
    for i, line in enumerate(body.splitlines(), 1):
        s = line.strip()
        if len(s) < 40 or len(s) > 400:
            continue
        if s.startswith(("#", ">", "|", "!", "```")):
            continue
        if spec["broad"] in s or any(x in s for x in broad["exclude"]):
            continue
        hit = [a for a in aliases if a in s]
        if not hit:
            continue
        score = 2 * len(CUE.findall(s)) + min(sum(s.count(a) for a in hit), 3)
        cand = (score, os.path.relpath(path, ROOT), i, hit[0], s)
        if best is None or cand[0] > best[0]:
            best = cand
    if not best:
        return None, "no line"
    score, rel, line, alias, text = best
    return {"broad": spec["broad"], "uid": spec["uid"], "path": rel,
            "line": line, "alias": alias, "text": text,
            "chapter_has_key": body.count(spec["broad"]),
            "chapter_chars": len(body)}, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", default=os.path.join(HERE, "specs.tsv"))
    ap.add_argument("--out", default=os.path.join(HERE, "targets.tsv"))
    a = ap.parse_args()
    broad = load_broad()
    specs = list(csv.DictReader(open(a.spec, encoding="utf-8-sig"), delimiter="\t"))
    rows = []
    for spec in specs:
        row, err = pick(spec, broad[spec["broad"]])
        if err:
            print("MISS", spec["broad"], spec["uid"], spec["chapter"], err, file=sys.stderr)
            continue
        rows.append(row)
        print("%s\t%s:%d\t%s\tkey=%d\t%s" % (
            row["broad"], row["path"], row["line"], row["alias"],
            row["chapter_has_key"], row["text"][:140]))
    with open(a.out + ".tmp", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, delimiter="\t", lineterminator="\n",
                           fieldnames=["broad", "uid", "path", "line", "alias",
                                       "chapter_has_key", "chapter_chars", "text"])
        w.writeheader()
        w.writerows(rows)
    os.replace(a.out + ".tmp", a.out)
    print("->", a.out, len(rows), file=sys.stderr)


if __name__ == "__main__":
    main()
