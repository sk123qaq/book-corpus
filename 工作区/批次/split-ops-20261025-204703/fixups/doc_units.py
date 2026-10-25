#!/usr/bin/env python3
"""按 spine 文档建单元：目录条目落在章号页上时的退路。

B1757《Who Gets What》的 NCX 把每章锚在 `partNNNN_split_000.html` 上，而 calibre 的
那个切片只有章号（`10`/`11`/`12`）或部标题（`PART II`），正文在同一切片的
`_split_001.html` 里。按锚点切（默认路径）每章会多出一个十几字的残页
（`16_Signaling` / `20_Repugnant…` / `22_Free…`），四个部还各多出两页 `PART N`
与部名的空页，README 的未收录也解释不了这些页。

这里不查 NCX，直接按 `--map` 显式给「一页一条、页序即读序」的映射：正文页按原书章名
建单元（章号页、部页按 excluded 收进未收录），split_001 的正文页接过章名，于是每章
一个文件、没有残页。渲染仍然交给 `ebook_to_md.emit_book`，图片、front matter、
README 口径不变。

用法：
  doc_units.py BOOK.epub -o OUTDIR --title 书名 --map chapters.json [--all-pages]
  doc_units.py BOOK.epub --map chapters.json --dry-run

map 格式（pages 必须按 spine 读序、覆盖全部正文文档）：
  {"pages": [{"title": "PART I — MARKETS ARE EVERYWHERE",
              "docs": ["text/part0005_split_000.html", "text/part0005_split_001.html"],
              "exclude": "部页：只有 PART I 标记与部名，无正文",
              "reason": "part divider"},
             {"title": "Signaling", "docs": ["text/part0017_split_001.html"]}]}
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))

from ebook_to_md import Epub, Unit, add_output_options, emit_book  # noqa: E402


def build(epub, spec):
    docs = epub.content_docs()
    order = {d: i for i, d in enumerate(docs)}
    units, seen = [], []
    for page in spec.get("pages") or []:
        ds = page.get("docs") or [page["doc"]]
        for d in ds:
            if d not in order:
                sys.exit(f"doc not in spine: {d}")
            if d in seen:
                sys.exit(f"doc assigned twice: {d}")
            seen.append(d)
        u = Unit(len(units) + 1, page["title"], ds[0], None, 1)
        for d in ds[1:]:
            u.merge_children.append(
                Unit(len(units) + 1, page["title"], d, None, 1))
        if page.get("exclude"):
            u.excluded = True
            u.reason = page.get("reason") or page["exclude"]
        units.append(u)
    missing = [d for d in docs if d not in seen]
    if missing:
        sys.exit("spine docs with no page in the map: %s" % ", ".join(missing))
    if seen != docs:
        sys.exit("map order differs from spine reading order")
    return units


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--title")
    ap.add_argument("--map", required=True, help="page map JSON")
    ap.add_argument("--dry-run", action="store_true")
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.basename(src)
    spec = json.load(open(args.map, encoding="utf-8"))
    units = build(epub, spec)

    print("units       :")
    for u in units:
        print("   %s %-52s %s%s" % (
            "--" if u.excluded else "  ", u.title[:52], u.doc,
            "" if u.excluded else "  +%d part(s)" % len(u.merge_children)))
    if args.dry_run:
        return 0
    if not args.outdir:
        sys.exit("-o/--outdir required unless --dry-run")
    outdir = os.path.abspath(args.outdir)
    if os.path.isdir(outdir):
        for name in os.listdir(outdir):
            path = os.path.join(outdir, name)
            if os.path.isdir(path):
                continue
            os.remove(path)
    stats = emit_book(epub, units, outdir, title, os.path.basename(src), args)
    print("written     : %d files, %s chars, %d images"
          % (stats["chapters"], format(stats["chars"], ","), stats["images"]))
    for line in stats["lines"]:
        print(line)
    for w in stats["warnings"]:
        print("warning     :", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
