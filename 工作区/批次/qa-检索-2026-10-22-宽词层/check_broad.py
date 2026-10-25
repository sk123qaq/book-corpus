#!/usr/bin/env python3
"""Deterministic checks for the broad layer, heldout, plans, and eval outputs."""
import csv
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import qsearch_plan as qp  # noqa: E402

BROAD = os.path.join(ROOT, "目录", "概念表-broad.tsv")
HELDOUT = os.path.join(HERE, "heldout.tsv")
PLANS = os.path.join(HERE, "plans")
DB = os.path.join(ROOT, "目录", "检索", "index.db")
EVAL = os.path.join(HERE, "eval-broad.tsv")


def fail(msg):
    print("FAIL", msg)
    sys.exit(1)


def main():
    broad = list(csv.DictReader(open(BROAD, encoding="utf-8-sig"), delimiter="\t"))
    if len(broad) != 10:
        fail("broad rows %d" % len(broad))
    keys = [r["key"] for r in broad]
    if len(set(keys)) != 10:
        fail("duplicate broad keys")
    for r in broad:
        aliases = [a for a in r["aliases"].split(",") if a]
        if not 2 <= len(aliases) <= 4:
            fail("%s aliases %d" % (r["key"], len(aliases)))
        if not r["exclude"]:
            fail("%s no exclude" % r["key"])
        terms, ex = qp.load_broad(r["key"])
        if terms[0] != r["key"] or not ex:
            fail("%s load_broad" % r["key"])

    rows = list(csv.DictReader(open(HELDOUT, encoding="utf-8-sig"), delimiter="\t"))
    if len(rows) != 40:
        fail("heldout rows %d" % len(rows))
    idx = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    for r in rows:
        if not os.path.isfile(os.path.join(ROOT, r["path"])):
            fail("%s path" % r["qid"])
        line = int(r["line"])
        text = open(os.path.join(ROOT, r["path"]), encoding="utf-8", errors="replace").read().splitlines()
        if line < 1 or line > len(text) or text[line - 1].strip() != r["evidence"].strip():
            fail("%s line" % r["qid"])
        if r["alias"] not in r["evidence"] or r["broad"] in r["evidence"]:
            fail("%s alias/key" % r["qid"])
        if any(x in r["evidence"] for x in r["literal"].split(",")):
            fail("%s literal leak" % r["qid"])
        if not idx.execute("select 1 from para where path=? limit 1", (r["path"],)).fetchone():
            fail("%s not indexed" % r["qid"])

    for r in rows:
        no = json.load(open(os.path.join(PLANS, "no_broad", r["qid"] + ".json"), encoding="utf-8"))
        yes = json.load(open(os.path.join(PLANS, "with_broad", r["qid"] + ".json"), encoding="utf-8"))
        if no.get("broad"):
            fail("%s no_broad has broad" % r["qid"])
        if yes.get("broad") != [r["broad"]]:
            fail("%s with_broad key" % r["qid"])

    hits = qp.run({"question": "权力", "groups": [], "broad": ["权力"]}, limit=5)
    bad = [h for h in hits["hits"] if any(x in h["text"] for x in ["权利", "控制台", "控制变量"])]
    if bad:
        fail("broad exclude leaked %d" % len(bad))

    ev = list(csv.DictReader(open(EVAL, encoding="utf-8-sig"), delimiter="\t"))
    if len(ev) != 160:
        fail("eval rows %d" % len(ev))
    print("PASS broad=10 heldout=40 plans=80 index=40 eval=160 exclude=ok")


if __name__ == "__main__":
    main()
