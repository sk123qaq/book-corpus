#!/usr/bin/env python3
"""目录锚点坏掉、spine 又不分章时的退路（references/broken-toc-anchors.md §3）。

B1096 ａｂさんご：15 个〈章名〉的锚点全挤在同一个 div 顶部，彼此只隔几个字，
按锚点切每节只有 4-8 字，正文 35,038 字没人拥有。
B0881 Tales of the South Pacific：MOBI6 缺 guide，修补后的 calibre 转出无目录 EPUB。

两本的 spine 都不是一章一文档，按 §3 不臆造边界，改成按 spine 文档建单元。
`--unit-title` 给整本只有一个文档在讲正文的书用（B0881），文件名用书名。

用法与 ebook_to_md.py 相同，另加 --unit-title。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))

from ebook_to_md import Epub, Unit, add_output_options, emit_book  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--title")
    ap.add_argument("--unit-title", help="每个单元都用这个标题（整本一个文档时用书名）")
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.basename(src)
    docs = epub.content_docs()
    if args.unit_title:
        # 整本正文在一个文档里：只给正文最多的那个文档建单元，其余（书名页、
        # 版权页）不建，否则同名单元会互相挤掉，目录也列不出来
        def weight(doc):
            soup = epub.soup(doc)
            body = soup.find("body") if soup else None
            return len(body.get_text(strip=True)) if body else 0
        docs = [max(docs, key=weight)]
        units = [Unit(1, args.unit_title, docs[0], None, 1)]
    else:
        # untoced 单元会拿首行重猜标题，这里要的是「一节一文档」的兜底
        units = [Unit(i, f"章节 {i}", doc, None, 1, untoced=True)
                 for i, doc in enumerate(docs, 1)]
    stats = emit_book(epub, units, os.path.abspath(args.outdir), title,
                      os.path.basename(src), args)
    print(f"chapters    : {stats['chapters']}")
    print(f"characters  : {stats['chars']:,}")
    for line in stats["lines"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
