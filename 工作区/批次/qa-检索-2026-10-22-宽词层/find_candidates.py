#!/usr/bin/env python3
"""Find candidate target lines for the broad-layer heldout.

  find_candidates.py --key 权力 --limit 40
  find_candidates.py --all --out candidates.tsv
"""
import argparse
import csv
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BROAD = os.path.join(ROOT, "目录", "概念表-broad.tsv")
ROOTS = ["库/人文社科", "库/文学", "库/科学技术", "库/商业与管理"]
CUE = re.compile(r"所谓|指的是|也就是说|本质上|意味着|换句话说|可以定义为|就是|因为|所以")


def load_broad(key=""):
    with open(BROAD, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if key:
        rows = [r for r in rows if r["key"] == key]
    if not rows:
        sys.exit("no broad key: " + key)
    out = []
    for r in rows:
        out.append({"key": r["key"],
                    "aliases": [a for a in r["aliases"].split(",") if a],
                    "exclude": [x for x in r["exclude"].split(",") if x and x != "无"],
                    "note": r["note"]})
    return out


def rg_lines(alias):
    cmd = ["rg", "-n", "-F", "--no-heading", "--color", "never",
           "-g", "*.md", alias] + ROOTS
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if p.returncode not in (0, 1):
        sys.exit("rg failed: " + p.stderr.strip())
    for line in p.stdout.splitlines():
        path, lineno, text = line.split(":", 2)
        yield path, int(lineno), text


def is_bad_line(text):
    s = text.strip()
    if len(s) < 30 or len(s) > 500:
        return True
    return s.startswith(("#", ">", "|", "!", "```", "图"))


def candidates(row, limit):
    seen = {}
    for alias in row["aliases"]:
        for path, line, text in rg_lines(alias):
            if not path.startswith(tuple(ROOTS)) or "/md/" not in path:
                continue
            if "_冷宫" in path or "图册" in path:
                continue
            if is_bad_line(text):
                continue
            if row["key"] in text:
                continue
            if any(x in text for x in row["exclude"]):
                continue
            try:
                body = open(os.path.join(ROOT, path), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if len(body) < 800 or row["key"] in body:
                continue
            if any(x in body for x in row["exclude"]):
                continue
            score = 2 * len(CUE.findall(text)) + min(text.count(alias), 3)
            if path.startswith("库/人文社科/"):
                score += 1
            old = seen.get(path)
            if old is None or score > old["score"]:
                seen[path] = {"broad": row["key"], "alias": alias, "path": path,
                              "line": line, "chars": len(body),
                              "score": score, "text": text.strip()}
    rows = sorted(seen.values(), key=lambda r: (-r["score"], r["path"], r["line"]))
    return rows[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not a.key and not a.all:
        sys.exit("give --key or --all")
    keys = [""] if a.all else [a.key]
    rows = []
    for key in keys:
        for row in load_broad(key):
            got = candidates(row, a.limit)
            rows += got
            print("== %s (%d) ==" % (row["key"], len(got)), file=sys.stderr)
            for r in got:
                print("%s\t%s\t%s:%d\t%s\t%d" % (
                    r["broad"], r["alias"], r["path"], r["line"],
                    r["text"][:90], r["score"]))
    dest = a.out or os.path.join(HERE, "candidates.tsv")
    with open(dest + ".tmp", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["broad", "alias", "path", "line", "chars", "score", "text"])
        for r in rows:
            w.writerow([r["broad"], r["alias"], r["path"], r["line"],
                        r["chars"], r["score"], r["text"]])
    os.replace(dest + ".tmp", dest)
    print("->", dest, len(rows), file=sys.stderr)


if __name__ == "__main__":
    main()
