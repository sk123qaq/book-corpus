#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split a collection EPUB (套装/全集/大全集) into one folder per book.

    python3 split_collection.py BOOK.epub -o OUTDIR --map MAP.json [options]

The MAP.json decides the split:

    {
      "collection": "鬼吹灯 8部全集图文版",
      "series": "鬼吹灯",
      "books": [
        {"folder": "鬼吹灯-精绝古城", "labels": ["鬼吹灯I精绝古城"]},
        {"folder": "三体-克拉克奖获奖感言",
         "labels": ["刘慈欣2018克拉克奖获奖感言"], "keep_marker": true},
        ...
      ],
      "skip": [{"label": "版权信息", "reason": "合集卷首页"}, "制作说明"]
    }

Each book's content runs from its first label's page up to the next book's
first label in reading order, so label the books in reading order.  `skip`
lists collection-level pages (covers, copyright pages, the collection TOC)
that belong to no book: they are left out and reported.  A book's own title/
cover page is never written as a file (it is listed under 未收录) unless the
book sets "keep_marker": true.  Output per book is identical to
ebook_to_md.py -- the conversion pipeline lives there (emit_book); this
script only segments the collection, so every option that shapes the output
(--no-images, --all-pages, --heading-recovery, ...) matches that script.

Chapter boundaries and titles come from the book's own table of contents.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ebook_to_md import (  # noqa: E402
    Epub,
    add_output_options,
    build_units,
    emit_book,
    merge_numeric_sections,
    sanitize_filename,
)
from carry_doc_prefixes import carry_prefixes  # noqa: E402


def locate(units, label: str):
    """Index of the TOC unit titled `label` (exact first, then prefix).

    Books sometimes repeat a title inside themselves -- a chapter that lists
    its own name again one level down (余华's 《战栗》 is also a section of
    在细雨中呼喊).  Among matches pick the shallowest TOC level, which is
    the entry that actually starts the book.
    """
    for exact in (True, False):
        hits = []
        for i, u in enumerate(units):
            if u.untoced:
                continue
            if (u.title == label) if exact else u.title.startswith(label):
                hits.append((u.toc_level, i))
        if hits:
            return min(hits)[1]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Split a collection EPUB (套装/全集) into one folder per book."
    )
    ap.add_argument("input", help="path to the collection .epub")
    ap.add_argument("-o", "--outdir", help="output root (default: next to the epub)")
    ap.add_argument("--map", required=True, help="JSON split map (see the docstring)")
    ap.add_argument(
        "--carry-doc-prefixes",
        action="store_true",
        help="hand each document's unowned prefix to the previous chapter "
             "(misaligned split docs; see carry_doc_prefixes.py)",
    )
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit(f"input not found: {src}")
    if not zipfile.is_zipfile(src):
        sys.exit(f"{src} is not an EPUB (not a zip archive).")
    spec = json.load(open(args.map, encoding="utf-8"))

    epub = Epub(src)
    units = build_units(epub, True)
    if not args.no_merge_sections:
        units = merge_numeric_sections(units)
    if args.carry_doc_prefixes and carry_prefixes(epub, units):
        print("prefixes carried into the previous chapter (misaligned split docs)")

    # ---- resolve labels --------------------------------------------------
    books = spec.get("books") or []
    if not books:
        sys.exit("the map lists no books")
    missing: list[str] = []
    book_pos: list[tuple[int, int, dict]] = []
    for bi, bk in enumerate(books):
        idx = locate(units, bk["labels"][0])
        if idx is None:
            missing.append(bk["labels"][0])
            continue
        book_pos.append((idx, bi, bk))
        for extra in bk["labels"][1:]:
            if locate(units, extra) is None:
                missing.append(extra)
    skip_reason: dict[int, str] = {}
    for sk in spec.get("skip", []):
        if isinstance(sk, dict):
            lab, reason = sk.get("label", ""), sk.get("reason", "")
        else:
            lab, reason = sk, ""
        idx = locate(units, lab)
        if idx is None:
            missing.append(lab)
        else:
            skip_reason[idx] = reason or "合集卷首/卷尾页"
    if missing:
        sys.exit("labels not found in this EPUB's TOC: " + ", ".join(missing))
    seen: dict[int, int] = {}
    for idx, bi, _bk in book_pos:
        if idx in seen:
            sys.exit(
                f"two books claim the same TOC entry: "
                f"{books[seen[idx]]['labels'][0]!r} and {books[bi]['labels'][0]!r}"
            )
        seen[idx] = bi
    book_pos.sort()
    folders = [(bk.get("folder") or sanitize_filename(bk["labels"][0], maxlen=80)) for _i, _b, bk in book_pos]
    if len(set(folders)) != len(folders):
        dupes = sorted({f for f in folders if folders.count(f) > 1})
        sys.exit("two books map to the same folder name: " + ", ".join(dupes))

    # ---- ownership: reading order decides who owns each page --------------
    owner: list = [None] * len(units)
    starts = {idx: bi for idx, bi, _bk in book_pos}
    cur = None
    for i in range(len(units)):
        if i in starts:
            cur = starts[i]
        elif i in skip_reason:
            cur = None
        owner[i] = cur

    # ---- one folder per book ---------------------------------------------
    outroot = os.path.abspath(args.outdir or os.path.dirname(src))
    collection = spec.get("collection") or epub.metadata.get("title") or ""
    rows = []
    total_chapters = total_chars = total_images = 0
    print(f"source      : {src}")
    print(f"collection  : {collection}")
    print(f"output root : {outroot}")
    print("")
    for idx, bi, bk in book_pos:
        group = [u for i, u in enumerate(units) if owner[i] == bi]
        folder = bk.get("folder") or sanitize_filename(bk["labels"][0], maxlen=80)
        marker = None if bk.get("keep_marker") else units[idx]
        stats = emit_book(
            epub,
            group,
            os.path.join(outroot, folder),
            folder,
            os.path.basename(src),
            args,
            collection=collection,
            marker=marker,
        )
        rows.append((folder, stats))
        total_chapters += stats["chapters"]
        total_chars += stats["chars"]
        total_images += stats["images"]
        flag = "!!" if stats["chapters"] == 0 else "  "
        print(f"{flag} {folder:<54s} {stats['chapters']:>4d} 章  {stats['chars']:>9,} 字  {stats['images']:>4d} 图")

    # ---- collection-level pages that belong to no book --------------------
    skipped = []
    for i, u in enumerate(units):
        if owner[i] is None:
            skipped.append((u.title, skip_reason.get(i, "合集卷首/卷尾页")))

    # ---- collection README ------------------------------------------------
    if not args.no_index:
        lines = [f"# {collection} — 合集拆分说明", ""]
        lines.append(f"- 来源：`{os.path.basename(src)}`")
        lines.append(f"- 书目：{len(rows)} 本")
        lines.append(f"- 合计：{total_chapters} 章、{total_chars:,} 字、{total_images} 张图片")
        lines += ["", "## 书目", ""]
        for folder, stats in rows:
            lines.append(f"- [{folder}]({folder}/) — {stats['chapters']} 章")
        if skipped:
            lines += ["", "## 未收录（合集卷首/卷尾页）", ""]
            for title, reason in skipped:
                lines.append(f"- {title} — {reason}")
        fname = f"{spec.get('series') or collection}-合集说明.md"
        with open(os.path.join(outroot, fname), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).strip() + "\n")
        print(f"\ncollection README: {fname}")

    print(
        f"\ntotal: {len(rows)} books, {total_chapters} chapters, "
        f"{total_chars:,} chars, {total_images} images"
    )
    if skipped:
        print(f"skipped collection pages: {len(skipped)}")
        for title, reason in skipped:
            print(f"  -- {title[:52]:<54s} ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
