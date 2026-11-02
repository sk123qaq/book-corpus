#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Text-volume audit: converted Markdown vs the raw EPUB text.

    # a single book
    python3 audit_volume.py "book.epub" -o "书md/书名"

    # a collection split with a map
    python3 audit_volume.py "合集.epub" -o "书md" --map split.json

Counts CJK + latin/digit characters (whitespace and punctuation excluded) on
both sides, so markdown syntax never skews the ratio.  A faithful body-only
run is ~94-97% of the raw text (the rest is front/back matter, listed in the
README 未收录 section); `--all-pages` runs are ~100%.  Anything below 88% for
a normal book means text was dropped -- check the 未收录 list first, then look
for the failure modes in the skill's Judgement calls (inline footnote anchors,
part-titled units holding whole parts).

Exit 1 when any book is LOW, HIGH (duplicated text), or not converted.
"""
import argparse
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ebook_to_md import Epub, build_units, merge_numeric_sections  # noqa: E402
from split_collection import locate  # noqa: E402

COUNT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]")

LOW, HIGH = 0.88, 1.15


def count(text: str) -> int:
    return len(COUNT_RE.findall(text))


def raw_doc_counts(epub: "Epub") -> dict:
    out = {}
    for doc in epub.content_docs():
        soup = epub.soup(doc)
        body = soup.find("body") if soup else None
        out[doc] = count(body.get_text(" ", strip=True)) if body else 0
    return out


def folder_chars(folder: str):
    if not os.path.isdir(folder):
        return None
    total, files = 0, 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md") or name == "README.md":
            continue
        files += 1
        text = open(os.path.join(folder, name), encoding="utf-8").read()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[text.find("\n", end + 1) + 1:]
        total += count(text)
    return total, files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the .epub")
    ap.add_argument("-o", "--outdir", required=True, help="book folder, or collection root")
    ap.add_argument("--map", help="JSON split map (collection mode)")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        sys.exit(f"input not found: {args.input}")
    epub = Epub(os.path.abspath(args.input))
    doc_raw = raw_doc_counts(epub)
    all_raw = sum(doc_raw.values())

    rows = []  # (label, folder, raw, got, files)  files None = folder missing
    if not args.map:
        got = folder_chars(args.outdir)
        rows.append((os.path.basename(args.outdir.rstrip("/")), args.outdir,
                     all_raw, got[0] if got else 0, got[1] if got else None))
    else:
        import json
        spec = json.load(open(args.map, encoding="utf-8"))
        units = merge_numeric_sections(build_units(epub, True))
        owner = [None] * len(units)
        starts = {}
        for bi, bk in enumerate(spec.get("books") or []):
            idx = locate(units, (bk.get("labels") or [""])[0])
            if idx is None:
                sys.exit(f"map label not found: {bk.get('labels')!r} -- run verify_map.py first")
            starts[idx] = bi
        skip_idx = set()
        for sk in spec.get("skip", []):
            lab = sk.get("label") if isinstance(sk, dict) else sk
            i = locate(units, lab)
            if i is None:
                sys.exit(f"skip label not found: {lab!r} -- run verify_map.py first")
            skip_idx.add(i)
        cur = None
        for i, u in enumerate(units):
            if i in starts:
                cur = starts[i]
            elif i in skip_idx:
                cur = None
            owner[i] = cur
        # doc-level ownership: the first unit starting in a doc decides; skip
        # pages own nothing; docs with no unit carry the previous owner
        # forward (continuation pages belong to the book before them).
        doc_owner = {}
        for i, u in enumerate(units):
            if u.doc not in doc_owner:
                doc_owner[u.doc] = None if i in skip_idx else owner[i]
        cur = None
        for doc in epub.content_docs():          # spine order
            if doc in doc_owner:
                cur = doc_owner[doc]
            doc_owner[doc] = cur
        raw_by_book = {}
        for doc in epub.content_docs():
            o = doc_owner.get(doc)
            if o is not None:
                raw_by_book[o] = raw_by_book.get(o, 0) + doc_raw.get(doc, 0)
        for bi, bk in enumerate(spec.get("books") or []):
            folder = os.path.join(args.outdir, bk.get("folder", ""))
            got = folder_chars(folder)
            rows.append((bk.get("folder", ""), folder, raw_by_book.get(bi, 0),
                         got[0] if got else 0, got[1] if got else None))

    print(f"source : {args.input}")
    print(f"outdir : {args.outdir}\n")
    low = high = missing = 0
    for label, folder, raw, got, files in rows:
        if files is None:
            missing += 1
            print(f"  MISSING {label:<44s}  raw {raw:>9,d}   no folder: {folder}")
            continue
        ratio = (got / raw) if raw else 1.0
        if raw < 100:
            mark = "ok (no-text page)"
        elif got == 0:
            missing += 1
            mark = "NOT CONVERTED"
        elif ratio < LOW:
            low += 1
            mark = "LOW  <-- check 未收录 / failure modes"
        elif ratio > HIGH:
            high += 1
            mark = "HIGH <-- duplicated text?"
        else:
            mark = "ok"
        print(f"  {label:<48s} {got:>9,d} / {raw:>9,d}  {ratio*100:5.1f}%  {files:>4d} md  {mark}")
    print(f"\n{len(rows)} books: {low} LOW, {high} HIGH, {missing} missing")
    return 1 if (low or high or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
