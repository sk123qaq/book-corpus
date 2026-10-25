#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录只标了卷，正文却按定长切在 20 多个 spine 文档里时的拆章器（B0571）。

B0571 蒂博一家：全4册：NCX 只有 15 条（版权页/颁奖辞/致答辞/八卷/四篇附录），
但 spine 是 21 个 ~50k 字的定长文档，卷与卷的分界落在一个文档中间 —— 并且
下一卷之前还夹着若干没有目录项的文档。ebook_to_md.py 按「本篇最后一个条目
只认到文档末尾」切开后，后一个文档在前一卷锚点之前的那段正文无人拥有：
audit 75.1%，且多出来的 7 个文档各自成篇（前置页 N / 19 / 31 …）。

这里按阅读顺序取「本条目锚点到下一条目锚点」的完整跨度：同文档的下一条目用
next_fragment 收边；跨文档时跨途中的整篇文档与前一篇的前缀都作为 merge_children
挂到本条目后面。切完每个文档的正文恰好各归其卷，既不重叠也不遗漏。

用法与 ebook_to_md.py 相同。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))

from ebook_to_md import Epub, Unit, add_output_options, emit_book, link_dest  # noqa: E402


def span_units(epub: "Epub") -> list:
    """一条目录项一份跨度：到同文档的下一条目，或一路吃到下一个锚点。"""
    docs = epub.content_docs()
    order = {d: i for i, d in enumerate(docs)}
    entries = [e for e in epub.toc() if e.path in order]
    units = []
    for i, entry in enumerate(entries):
        soup = epub.soup(entry.path)
        if entry.fragment and soup.find(id=entry.fragment) is None:
            sys.exit(f"stale TOC anchor: {entry.label!r} -> {entry.path}#{entry.fragment}")
        u = Unit(i + 1, entry.label, entry.path, entry.fragment, entry.level)
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        if nxt is not None and nxt.path == entry.path:
            u.next_fragment = (
                "" if (nxt.fragment or None) == (entry.fragment or None) else nxt.fragment
            )
        else:
            u.next_fragment = None  # 本篇吃到文档末尾
            if nxt is not None:
                for doc in docs[order[entry.path] + 1:order[nxt.path]]:
                    # 卷中间没有目录项的整篇文档，归本篇
                    u.merge_children.append(Unit(i + 1, "", doc, None, 1, untoced=True))
                # 下一卷锚点之前的正文继续属于本篇
                u.merge_children.append(
                    Unit(i + 1, "", nxt.path, None, 1, untoced=True,
                         next_fragment=nxt.fragment)
                )
        units.append(u)
    return units


MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\((?:<([^>]+)>|([^)]+))\)")


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000·、,，.。:：\-–—_]+", "", s or "")


def retarget_toc_links(outdir: str, units: list) -> int:
    """把附录目录页里的内部链接接到本条目自己的文件上。

    emit_book 的 doc_unit 按「文档」映射，四个附录同在一个文档（text00020），
    三个链接会全指向同一个文件；本脚本的跨度又把每个文档的前缀挂到上一卷，
    按文档查还会整体错一格。链接的标签就是章节名，按标签对到落盘的文件名。
    """
    by_label = {norm(u.title): u.filename for u in units if u.written}

    def repl(m: "re.Match") -> str:
        label = m.group(1)
        target = by_label.get(norm(label))
        if not target:
            return m.group(0)
        return f"[{label}]({link_dest(target)})"

    changed = 0
    for name in sorted(os.listdir(outdir)):
        if not name.endswith(".md") or name == "README.md":
            continue
        path = os.path.join(outdir, name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new = MD_LINK_RE.sub(repl, text)
        if new != text:
            changed += 1
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(new)
            os.replace(path + ".tmp", path)
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--title")
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.splitext(os.path.basename(src))[0]
    units = span_units(epub)
    stats = emit_book(epub, units, os.path.abspath(args.outdir), title,
                      os.path.basename(src), args)
    fixed = retarget_toc_links(os.path.abspath(args.outdir), units)
    print(f"source      : {src}")
    print(f"output      : {os.path.abspath(args.outdir)}")
    print(f"toc links   : {fixed} file(s) retargeted")
    print(f"chapters    : {stats['chapters']}")
    print(f"characters  : {stats['chars']:,}")
    print(f"images      : {stats['images']}")
    for line in stats["lines"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
