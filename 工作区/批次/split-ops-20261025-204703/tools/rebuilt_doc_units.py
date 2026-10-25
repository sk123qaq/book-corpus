#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按显式「文档 → 章」映射重建一本书的 md，不读 epub 自带的 TOC。

给 calibre 的 `index_split_NNN` 与 NCX `filepos` 锚点不同步的书用（B1750
《The Lever of Riches》）：这种 epub 的 NCX 标签指的文档不是章正文所在的
那个（锚点落在分隔页/图片页里，正文在下一个文档），按 TOC 切片会把正文
切碎、把图片页当章。这里绕开 TOC，按 `--units` 给出的阅读顺序，一个 spine
文档一章，章名取自书自己的 Contents 页。

    python3 rebuilt_doc_units.py book.epub -o OUT --title T \
        --units map.json [--keep/--drop/--all-pages… 与 ebook_to_md 相同]

map.json：
  {"chapters": [{"doc": "index_split_009.html", "title": "Introduction", "part": "Part I …"}],
   "skipped":  [{"doc": "index_split_004.html", "title": "Preface", "reason": "front matter"}]}

`part` 可省；`skipped` 只进 README 的未收录清单，不产生文件。
输出、图片、README 全由 ebook_to_md.emit_book() 生成，和后者的默认产物一致。
"""
import argparse
import json
import os
import re
import sys

SKILL = os.path.expanduser("~/.agents/skills/ebook-to-md/scripts")
sys.path.insert(0, SKILL)

import ebook_to_md as E  # noqa: E402


def load_spec(path):
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    if not spec.get("chapters"):
        sys.exit(f"{path}: no chapters")
    return spec


def add_part(path, part):
    """把 part 写进已有 YAML front matter 的 title 行后面。"""
    text = open(path, encoding="utf-8").read()
    if not text.startswith("---\n"):
        return
    end = text.find("\n---", 4)
    if end == -1:
        return
    head, tail = text[:end], text[end:]
    if "\npart: " in head:
        return
    line = 'part: "%s"' % part.replace('"', "'")
    head = re.sub(r'^(title: .*)$', lambda m: m.group(1) + "\n" + line, head,
                  count=1, flags=re.M)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(head + tail)
    os.replace(path + ".tmp", path)


def rewrite_index(outdir, title, written, parts, skipped):
    """重写 README 的目录与未收录段（章节来自映射，故不用 emit_book 的那份）。"""
    path = os.path.join(outdir, "README.md")
    if not os.path.isfile(path):
        return
    text = open(path, encoding="utf-8").read()
    head = text.split("\n## 目录", 1)[0].rstrip() + "\n"
    lines = [head, "## 目录", ""]
    last_part = None
    for u in written:
        part = parts.get(u.title, "")
        if part and part != last_part:
            lines.append(f"- **{part}**")
            last_part = part
        lines.append(f"- [{u.title}]({E.link_dest(u.filename)})")
    if skipped:
        lines += ["", "## 未收录（卷首/卷尾页）", ""]
        for item in skipped:
            lines.append(f"- {item['title']} — {item['reason']}")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).strip() + "\n")
    os.replace(path + ".tmp", path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the .epub file")
    ap.add_argument("-o", "--outdir", help="output directory")
    ap.add_argument("--title", help="book title used in README and naming")
    ap.add_argument("--units", required=True, help="JSON mapping doc -> chapter title")
    E.add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit(f"input not found: {src}")
    spec = load_spec(args.units)
    epub = E.Epub(src)
    docs = set(epub.content_docs())
    title = args.title or epub.metadata.get("title") or os.path.splitext(os.path.basename(src))[0]
    outdir = os.path.abspath(args.outdir or os.path.join(os.path.dirname(src), title))

    units, parts = [], {}
    for i, ch in enumerate(spec["chapters"], 1):
        doc = ch["doc"]
        if doc not in docs:
            sys.exit(f"{args.units}: {doc} is not in the spine")
        units.append(E.Unit(i, ch["title"], doc, None, 1))
        if ch.get("part"):
            parts[ch["title"]] = ch["part"]

    stats = E.emit_book(epub, units, outdir, title, os.path.basename(src), args)

    if parts:
        for u in stats["written"]:
            if parts.get(u.title):
                add_part(os.path.join(outdir, u.filename), parts[u.title])
    skipped = list(spec.get("skipped") or [])
    skipped += [{"title": u.title, "reason": u.reason}
                for u in stats["skipped"] if not any(u.title == c["title"] for c in skipped)]
    rewrite_index(outdir, title, stats["written"], parts, skipped)

    print(f"source      : {src}")
    print(f"title       : {title}")
    print(f"output      : {outdir}")
    print(f"mapping     : {args.units}")
    print(f"chapters    : {stats['chapters']}")
    print(f"characters  : {stats['chars']:,} (CJK+latin, whitespace excluded)")
    print(f"images      : {stats['images']}")
    print(f"baseline fs : {stats['baseline']}px")
    print("")
    for line in stats["lines"]:
        print(line)
    if stats["warnings"]:
        print("\nwarnings:")
        for w in stats["warnings"]:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
