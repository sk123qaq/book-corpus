#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dry-run a split map against a collection EPUB -- writes nothing.

    python3 verify_map.py "合集.epub" --map split.json

Checks, before you spend a conversion run on it:

  * every book label resolves (exact match first, then prefix, at the
    shallowest TOC level -- same rule split_collection.py applies);
  * book positions are strictly ascending (books are listed in reading order);
  * no two books claim the same TOC entry;
  * folder names are unique and contain no path separators;
  * every skip label resolves;
  * each book actually owns some pages, and reports how many.

Exit code 0 when the map is sound, 1 otherwise.  Use this to iterate a map
(fix labels, re-run) until it is clean, then run split_collection.py.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ebook_to_md import Epub, build_units, merge_numeric_sections  # noqa: E402
from split_collection import locate  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the collection .epub")
    ap.add_argument("--map", required=True, help="JSON split map")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit(f"input not found: {src}")
    if not zipfile.is_zipfile(src):
        sys.exit(f"{src} is not an EPUB (not a zip archive).")
    spec = json.load(open(args.map, encoding="utf-8"))

    epub = Epub(src)
    units = merge_numeric_sections(build_units(epub, True))

    problems = []
    books = spec.get("books") or []
    if not books:
        sys.exit("the map lists no books")

    # ---- labels ---------------------------------------------------------
    resolved = []          # (idx, bi, folder)
    for bi, bk in enumerate(books):
        labels = bk.get("labels") or []
        if not labels:
            problems.append(f"book {bi}: no labels")
            continue
        idx = locate(units, labels[0])
        if idx is None:
            problems.append(f"{labels[0]!r}: label not found in TOC")
            continue
        resolved.append((idx, bi, bk))
        for extra in labels[1:]:
            if locate(units, extra) is None:
                problems.append(f"{labels[0]!r}: alias label {extra!r} not found")

    # ---- order / duplicates --------------------------------------------
    seen = {}
    for idx, bi, bk in resolved:
        if idx in seen:
            problems.append(
                f"two books claim the same TOC entry: "
                f"{books[seen[idx]]['labels'][0]!r} and {bk['labels'][0]!r}"
            )
        seen[idx] = bi
    resolved.sort()
    for (i1, _b1, b1), then in zip(resolved, resolved[1:]):
        i2, _b2, b2 = then
        if i2 <= i1:
            problems.append(
                f"positions not ascending: {b1['labels'][0]!r} then {b2['labels'][0]!r}"
            )

    # ---- folder names ---------------------------------------------------
    folders = [bk.get("folder") or "" for _i, _b, bk in resolved]
    if any(not f for f in folders):
        problems.append("a book has no folder name")
    bad = [f for f in folders if "/" in f or "\\" in f or f in (".", "..")]
    if bad:
        problems.append("folder name contains a path separator: " + ", ".join(bad))
    dupes = sorted({f for f in folders if f and folders.count(f) > 1})
    if dupes:
        problems.append("duplicate folder names: " + ", ".join(dupes))

    # ---- skip labels ----------------------------------------------------
    skip_idx = set()
    for sk in spec.get("skip", []):
        lab = sk.get("label", "") if isinstance(sk, dict) else sk
        idx = locate(units, lab)
        if idx is None:
            problems.append(f"skip label not found: {lab!r}")
        else:
            skip_idx.add(idx)

    # ---- ownership ------------------------------------------------------
    owner = [None] * len(units)
    starts = {idx: bi for idx, bi, _bk in resolved}
    cur = None
    for i in range(len(units)):
        if i in starts:
            cur = starts[i]
        elif i in skip_idx:
            cur = None
        owner[i] = cur
    counts = {}
    for o in owner:
        if o is not None:
            counts[o] = counts.get(o, 0) + 1

    # ---- report ---------------------------------------------------------
    print(f"source : {src}")
    print(f"map    : {args.map}")
    print(f"units  : {len(units)}   books: {len(resolved)}   skipped markers: {len(skip_idx)}")
    print("")
    for idx, bi, bk in resolved:
        lab = bk["labels"][0]
        n = counts.get(bi, 0)
        flag = "!!" if n == 0 else "  "
        print(f"{flag} {bk.get('folder', ''):<52s} L{units[idx].toc_level}  {n:>4d} 页  <- {lab[:40]}")
        if n == 0:
            problems.append(f"{lab!r}: owns no pages (check reading order / duplicate marker)")

    if problems:
        print("\nPROBLEMS")
        for p in problems:
            print("  !", p)
        return 1
    print("\nmap OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
