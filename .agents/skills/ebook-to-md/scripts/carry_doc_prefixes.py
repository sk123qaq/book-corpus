#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-emit a book whose calibre split docs (index_split_NNN) are misaligned with
its TOC anchors: each document's prefix -- the previous chapter's tail plus its
endnotes -- was nobody's span and vanished from the output.

    python3 carry_doc_prefixes.py "book.epub" -o "书md/书名"

Symptoms that call for this script: audit_volume.py LOW (e.g. 73.8%) with a
short 未收录 list, and per-document window coverage showing the text *before*
EVERY document's first anchor missing (邓小平时代: 26% of the book, ~100k chars).
Same options as ebook_to_md.py (body chapters only by default).  Each prefix is
appended to the previous chapter as a merge_children source, so it lands at the
end of that chapter's file.  See references/broken-toc-anchors.md §6.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ebook_to_md import (  # noqa: E402
    Epub,
    Unit,
    add_output_options,
    build_units,
    emit_book,
    merge_numeric_sections,
    sanitize_filename,
)


def carry_prefixes(epub: "Epub", units: list) -> int:
    """Attach every document's prefix (text before its first unit's anchor) to the
    previous document's last unit as a merge_children source.  Returns how many."""
    added = 0
    for prev, nxt in zip(units, units[1:]):
        if prev.doc == nxt.doc or not nxt.fragment:
            continue
        if epub.soup(nxt.doc).find(id=nxt.fragment) is None:
            continue  # stale anchor: slicing it would take the whole document
        # NB: never probe with slice_root() here -- it extracts nodes out of the
        # cached soup, and the real render would then slice an empty prefix.
        prev.merge_children.append(
            Unit(prev.index, "", nxt.doc, None, 1, untoced=True,
                 next_fragment=nxt.fragment)
        )
        added += 1
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the .epub")
    ap.add_argument("-o", "--outdir", help="output book folder")
    ap.add_argument("--title", help="output folder / book title override")
    ap.add_argument(
        "--no-untoced",
        action="store_true",
        help="only pages the TOC lists (default: TOC-less pages become chapters too)",
    )
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit(f"input not found: {src}")
    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.splitext(os.path.basename(src))[0]
    outdir = os.path.abspath(
        args.outdir or os.path.join(os.path.dirname(src), sanitize_filename(title))
    )
    units = merge_numeric_sections(build_units(epub, not args.no_untoced))
    if not units:
        sys.exit("no chapters found: the book has no TOC (nav doc or NCX).")
    added = carry_prefixes(epub, units)
    if not added:
        print(
            "note: no unowned document prefixes found -- a plain ebook_to_md.py run "
            "gives the same output"
        )

    stats = emit_book(epub, units, outdir, title, os.path.basename(src), args)
    print(f"source      : {src}")
    print(f"output      : {outdir}")
    print(f"prefixes    : {added} carried into the previous chapter")
    print(f"chapters    : {stats['chapters']}")
    print(f"characters  : {stats['chars']:,}")
    print(f"images      : {stats['images']}")
    for line in stats["lines"]:
        print(line)
    if stats["warnings"]:
        print("warnings:")
        for w in stats["warnings"]:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
