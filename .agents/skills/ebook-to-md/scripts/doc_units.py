#!/usr/bin/env python3
"""Rebuild a book's units from an explicit spine-document map.

For epubs whose NCX anchors sit on the page *before* the body — calibre's
`index_split_NNN` offsets, or `partNNNN_split_000.html` pages that hold a chapter
number or `PART II` and nothing else — slicing by the TOC leaves the chapters
unowned and emits one stub file per anchor (The Lever of Riches 29.6%, Who Gets
What 87.7%). This bypasses the TOC and builds one unit per page, in spine order,
with chapter names read off the book's own Contents page.

  doc_units.py BOOK.epub -o OUT --map pages.json [--title T] [--all-pages] [--dry-run]

map.json:

  {"pages": [
    {"title": "PART I — MARKETS ARE EVERYWHERE",
     "docs": ["part0005_split_000.html", "part0005_split_001.html"],
     "exclude": "只有 PART I 标记与部名，无正文", "reason": "part divider"},
    {"title": "Signaling", "docs": ["part0017_split_001.html"]},
    {"title": "Introduction", "docs": ["index_split_009.html"],
     "part": "Part I Economic Growth and Technological Progress"}]}

- `docs` must name every spine document exactly once, in reading order. The script
  exits naming the problem when one is missing, repeated, unknown, or out of order.
- `exclude` (with `reason`) puts the page in the README 未收录 list, no file.
- `part` sets `part:` in that chapter's YAML and a bold line in the README index.
- Rendering, images and README come from `emit_book`, same as the default run. The
  title filter still runs inside it, but Notes/References/Epilogue/Glossary are body now;
  `--all-pages` is only for a front-matter page you deliberately want to keep. `exclude`
  pages stay out either way.
- An existing output directory is emptied of files first, so a re-run cannot leave
  files from the previous numbering behind.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ebook_to_md as E  # noqa: E402


def load_spec(path):
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    if not spec.get("pages"):
        sys.exit("%s: no pages" % path)
    return spec


def build(epub, spec):
    """Return (units, part_by_id, skipped). Every spine doc appears once, in order.

    An `exclude` page becomes no unit at all. Slicing it would consume nodes that the
    next chapter's link rewriting looks at, which moved a `[` across paragraphs when
    the same map was compared against a run without those units.
    """
    docs = epub.content_docs()
    order = {d: i for i, d in enumerate(docs)}
    units, seen, parts, skipped = [], [], {}, []
    for page in spec["pages"]:
        names = page.get("docs") or ([page["doc"]] if page.get("doc") else [])
        if not names:
            sys.exit("page %r has no docs" % page.get("title"))
        for name in names:
            if name not in order:
                sys.exit("doc not in spine: " + name)
            if name in seen:
                sys.exit("doc assigned twice: " + name)
        seen.extend(names)
        if page.get("exclude"):
            skipped.append((page["title"], page.get("reason") or page["exclude"]))
            continue
        unit = E.Unit(len(units) + 1, page["title"], names[0], None, 1)
        for name in names[1:]:
            unit.merge_children.append(
                E.Unit(len(units) + 1, page["title"], name, None, 1))
        if page.get("part"):
            parts[id(unit)] = page["part"]
        units.append(unit)
    missing = [d for d in docs if d not in seen]
    if missing:
        sys.exit("spine docs with no page in the map: %s" % ", ".join(missing))
    if seen != docs:
        first = next(i for i, (a, b) in enumerate(zip(seen, docs)) if a != b)
        sys.exit("map order differs from spine reading order at %s (spine has %s)"
                 % (seen[first], docs[first]))
    return units, parts, skipped


def add_part(path, part):
    """Insert `part: "…"` after the title line of a chapter's YAML header."""
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
    head = re.sub(r"^(title: .*)$", lambda m: m.group(1) + "\n" + line, head,
                  count=1, flags=re.M)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(head + tail)
    os.replace(path + ".tmp", path)


def rewrite_readme(text, written, parts, skipped):
    """Rebuild the README index with bold part headings, and the 未收录 list from the
    map. Entries emit_book excluded on its own are appended when their title is new."""
    head, rest = text.split("\n## 目录", 1)
    own = []
    if "\n## 未收录" in rest:
        for line in rest.split("\n## 未收录", 1)[1].splitlines():
            if line.startswith("- "):
                own.append(line)
    lines = [head.rstrip(), "", "## 目录", ""]
    last = None
    for unit in written:
        part = parts.get(id(unit), "")
        if part and part != last:
            lines.append("- **%s**" % part)
            last = part
        lines.append("- [%s](%s)" % (unit.title, E.link_dest(unit.filename)))
    known = {title for title, _reason in skipped}
    extra = [line for line in own if line[2:].split(" — ")[0] not in known]
    if skipped or extra:
        lines += ["", "## 未收录（卷首/卷尾页）", ""]
        lines += ["- %s — %s" % (title, reason) for title, reason in skipped]
        lines += extra
    return "\n".join(lines) + "\n"


def clear(outdir):
    for name in os.listdir(outdir):
        path = os.path.join(outdir, name)
        if os.path.isfile(path):
            os.remove(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="path to the .epub file")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--title")
    ap.add_argument("--map", required=True, help="page map JSON")
    ap.add_argument("--dry-run", action="store_true")
    E.add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit("input not found: " + src)
    epub = E.Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.splitext(
        os.path.basename(src))[0]
    units, parts, skipped = build(epub, load_spec(args.map))

    print("map         : %s" % args.map)
    for unit in units:
        print("     %-56s %s" % (unit.title[:56], unit.doc))
    for title_text, reason in skipped:
        print("   -- %-56s %s" % (title_text[:56], reason))
    print("chapters    : %d files, %d skipped pages" % (len(units), len(skipped)))
    if args.dry_run:
        return 0
    if not args.outdir:
        sys.exit("-o/--outdir required unless --dry-run")
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    clear(outdir)
    stats = E.emit_book(epub, units, outdir, title, os.path.basename(src), args)
    if parts or skipped:
        for unit in stats["written"]:
            if parts.get(id(unit)):
                add_part(os.path.join(outdir, unit.filename), parts[id(unit)])
        readme = os.path.join(outdir, "README.md")
        if os.path.isfile(readme):
            text = open(readme, encoding="utf-8").read()
            with open(readme + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(rewrite_readme(text, stats["written"], parts, skipped))
            os.replace(readme + ".tmp", readme)
    print("output      : %s" % outdir)
    print("written     : %d files, %s chars, %d images"
          % (stats["chapters"], format(stats["chars"], ","), stats["images"]))
    for line in stats["lines"]:
        print(line)
    for warn in stats["warnings"]:
        print("warning     :", warn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
