#!/usr/bin/env python3
"""按页脚页码标记建单元：NCX 只有页级垃圾锚点时的退路。

B1705《Development, Geography, and Economic Theory》（netLibrary → calibre 的
MOBI）：NCX 里 1711 个 navPoint 全是 `Local Disk / Document` 的页级重复条目，
一半挂在同一个 filepos 上，按锚点切出来是 89 个「Document」，其中 61 个是同一页
（page_74）的复制；而 page_74 的正文前面没有锚点，spine 也不是一章一文档。

这里不查 NCX，改用每页页脚 `file://…filename=page_N.html` 当页边界，按章映射
（--map 给的 from/to 页标）把连续页并成一个单元，于是每页正文恰好归一个单元。
单元仍然交给 ebook_to_md.emit_book 渲染，图片、front matter、README 都由它处理。

用法：
  page_units.py BOOK.epub -o OUTDIR --title 书名 --map chapters.json
  page_units.py BOOK.epub --map chapters.json --dry-run     # 只报页归属与字数

map 格式：
  {"chapters": [{"title": "...", "from": "page_1", "to": "page_29"},
                {"doc": "index_split_002.html", "title": "..."}],
   "excluded_docs": [{"doc": "index_split_003.html", "title": "...",
                      "reason": "..."}]}
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))

from ebook_to_md import Epub, Unit, add_output_options, emit_book  # noqa: E402

FOOTER_RE = re.compile(r"^file:///\S*filename=(page_[0-9a-z]+)\.html")
COUNT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]")


def count(text):
    return len(COUNT_RE.findall(text))


def page_spans(epub, doc):
    """一页一条：{label, anchor, chars}（页边界取页脚段落）。"""
    soup = epub.soup(doc)
    body = soup.find("body") if soup else None
    if body is None:
        return []
    pages, cur = [], []
    for child in body.children:
        if getattr(child, "name", None) is None:
            continue
        cur.append(child)
        text = child.get_text(" ", strip=True)
        m = FOOTER_RE.match(text)
        if not m:
            continue
        label = m.group(1)
        anchor = next((c.get("id") for c in cur if c.get("id")), None)
        chars = sum(count(c.get_text(" ")) for c in cur)
        if pages and pages[-1]["label"] == label:
            # 同一印刷页被拆成几段（netLibrary 的 "(1 of 3)" 尾页）：并回一页
            pages[-1]["chars"] += chars
        else:
            pages.append({"label": label, "anchor": anchor, "chars": chars,
                          "doc": doc, "doc_pos": len(pages)})
        cur = []
    if cur and pages:  # 页脚之后还有内容（尾页空行之类）：算进最后一页
        pages[-1]["chars"] += sum(count(c.get_text(" ")) for c in cur)
    # 没有页脚的文档（calibre 生成的目录页、netLibrary 的展平目录）不参与页序，
    # 它们只按整文档建单元
    return pages


def build(epub, spec):
    """[[Unit or merge child]] —— 一章一条，页级连续，跨文档用 merge_children 续。"""
    docs = epub.content_docs()
    by_doc = {d: page_spans(epub, d) for d in docs}
    order = [p for d in docs for p in by_doc[d]]
    where = {p["label"]: i for i, p in enumerate(order)}
    units = []
    owned = [False] * len(order)
    taken = []

    def unit_for(title, first, last):
        for i in range(first, last + 1):
            if owned[i]:
                sys.exit(f"page {order[i]['label']} assigned twice ({title})")
            owned[i] = True
        taken.append((title, first, last))
        runs = []  # [(doc, start_page, end_page)]
        for p in order[first:last + 1]:
            if runs and runs[-1][0] == p["doc"]:
                runs[-1][2] = p
            else:
                runs.append([p["doc"], p, p])
        head = runs[0]
        doc = head[0]
        nxt = None
        pos = head[2]["doc_pos"] + 1
        doc_pages = by_doc[doc]
        if pos < len(doc_pages):
            nxt = doc_pages[pos]["anchor"]
            if nxt is None:
                sys.exit(f"{doc}: page {doc_pages[pos]['label']} has no anchor, cannot bound")
        u = Unit(len(units) + 1, title, doc, head[1]["anchor"], 1, next_fragment=nxt)
        if head[1]["anchor"] is None and head[1]["doc_pos"] != 0:
            sys.exit(f"{doc}: page {head[1]['label']} is mid-document without an anchor")
        for rdoc, rfirst, rlast in runs[1:]:
            rpos = rlast["doc_pos"] + 1
            rnxt = by_doc[rdoc][rpos]["anchor"] if rpos < len(by_doc[rdoc]) else None
            if rnxt is None and rpos < len(by_doc[rdoc]):
                sys.exit(f"{rdoc}: page {by_doc[rdoc][rpos]['label']} has no anchor")
            if rfirst["anchor"] is None and rfirst["doc_pos"] != 0:
                sys.exit(f"{rdoc}: page {rfirst['label']} is mid-document without an anchor")
            u.merge_children.append(
                Unit(len(units) + 1, title, rdoc, rfirst["anchor"], 1, next_fragment=rnxt)
            )
        units.append(u)
        return u

    for ch in spec.get("chapters") or []:
        if "doc" in ch:
            # 整文档单元是机读目录页：它的链接是按 filepos 指向页的，落到 md 里只能
            # 指向某个章的章名，误导读者，所以整页按纯文本收（标签留着，href 去掉）
            soup = epub.soup(ch["doc"])
            for a in soup.find_all("a"):
                del a["href"]
            units.append(Unit(len(units) + 1, ch["title"], ch["doc"], None, 1,
                              untoced=True))
            continue
        if ch["from"] not in where or ch["to"] not in where:
            sys.exit(f"page label not found: {ch['from']} / {ch['to']}")
        unit_for(ch["title"], where[ch["from"]], where[ch["to"]])
    gaps = [p["label"] for i, p in enumerate(order) if not owned[i]]
    if gaps:
        sys.exit(f"pages with no chapter: {gaps[:8]}{' …' if len(gaps) > 8 else ''}")
    for ex in spec.get("excluded_docs") or []:
        u = Unit(len(units) + 1, ex["title"], ex["doc"], None, 1, untoced=True)
        u.excluded, u.reason = True, ex["reason"]
        units.append(u)
    return units, order, taken


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--title")
    ap.add_argument("--map", required=True, help="chapter map JSON")
    ap.add_argument("--dry-run", action="store_true")
    add_output_options(ap)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.basename(src)
    spec = json.load(open(args.map, encoding="utf-8"))
    units, order, taken = build(epub, spec)

    raw = 0
    for doc in epub.content_docs():
        soup = epub.soup(doc)
        body = soup.find("body") if soup else None
        raw += count(body.get_text(" ")) if body else 0
    covered = sum(p["chars"] for p in order)
    print(f"pages       : {len(order)} ({covered:,} chars on pages, epub raw {raw:,})")
    print("chapters    :")
    for u in units:
        hit = [t for t in taken if t[0] == u.title]
        if hit:
            _, first, last = hit[0]
            print(f"   {u.index:3d} {u.title:<48s} {order[first]['label']}..{order[last]['label']}"
                  f"  {sum(p['chars'] for p in order[first:last + 1]):>7,} chars"
                  f"  {len(u.merge_children) + 1} doc part(s)")
        else:
            print(f"   {u.index:3d} {u.title:<48s} {u.doc} whole doc"
                  f"{'  (excluded)' if u.excluded else ''}")
    if args.dry_run:
        return 0
    if not args.outdir:
        sys.exit("-o/--outdir required unless --dry-run")
    outdir = os.path.abspath(args.outdir)
    # 老产物一律清掉：文件名这次全变了，留着会被 verify 当成重复章
    if os.path.isdir(outdir):
        for name in os.listdir(outdir):
            path = os.path.join(outdir, name)
            if os.path.isdir(path):
                for f in os.listdir(path):
                    os.remove(os.path.join(path, f))
                os.rmdir(path)
            else:
                os.remove(path)
    stats = emit_book(epub, units, outdir, title, os.path.basename(src), args)
    print(f"written     : {stats['chapters']} files, {stats['chars']:,} chars, "
          f"{stats['images']} images")
    for line in stats["lines"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
