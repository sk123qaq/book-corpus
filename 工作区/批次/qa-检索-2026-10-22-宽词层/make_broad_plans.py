#!/usr/bin/env python3
"""Write paired no-broad and with-broad plans from heldout.tsv.

  make_broad_plans.py
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HELDOUT = os.path.join(HERE, "heldout.tsv")
PLANS = os.path.join(HERE, "plans")


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    rows = list(csv.DictReader(open(HELDOUT, encoding="utf-8-sig"), delimiter="\t"))
    for r in rows:
        literal = [x for x in r["literal"].split(",") if x]
        base = {"question": r["question"],
                "groups": [{"name": "literal", "terms": literal, "weight": 1.0}],
                "concepts": [], "book_hint": "", "exclude": []}
        write(os.path.join(PLANS, "no_broad", r["qid"] + ".json"),
              {**base, "broad": [], "broad_weight": 1.0})
        write(os.path.join(PLANS, "with_broad", r["qid"] + ".json"),
              {**base, "broad": [r["broad"]], "broad_weight": 1.0})
    print("wrote", len(rows), "pairs to", PLANS)


if __name__ == "__main__":
    main()
