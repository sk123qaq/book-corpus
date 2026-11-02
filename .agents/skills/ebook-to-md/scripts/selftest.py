#!/usr/bin/env python3
"""Small, self-contained regression tests for the chapter-detection rules.

    python3 scripts/selftest.py

Builds throwaway EPUBs in a temporary directory, so it needs no book corpus.
The cases are the ones that used to need a hand-written page map:

1. a TOC anchor on an empty title page, with the prose in the next spine doc
   (calibre split files; 重新定义公司 29 files / 71.6% before the fix);
2. a footnote icon whose alt repeats the note's prose (芯片战争 133.2%);
3. a publisher colophon after the last chapter (论人类不平等的起源和基础);
4. notes / references / afterword kept by default, index still dropped.
"""
import base64
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ebook_to_md.py")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def make_epub(path, docs, toc, title="Test Book"):
    """docs: {name: body_html}; toc: [(label, name)] in reading order."""
    names = list(docs)
    manifest = "\n".join(
        f'<item id="d{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(names)
    )
    spine = "\n".join(f'<itemref idref="d{i}"/>' for i in range(len(names)))
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title><dc:language>zh</dc:language>
<dc:identifier id="id">selftest</dc:identifier></metadata>
<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{manifest}</manifest>
<spine toc="ncx">{spine}</spine></package>"""
    points = []
    for i, (label, name) in enumerate(toc, 1):
        points.append(
            f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{label}</text>'
            f'</navLabel><content src="{name}"/></navPoint>'
        )
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="selftest"/></head>
<docTitle><text>{title}</text></docTitle><navMap>{''.join(points)}</navMap></ncx>"""
    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf"
 media-type="application/oebps-package+xml"/></rootfiles></container>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        for name, body in docs.items():
            z.writestr(f"OEBPS/{name}", f"<html><body>{body}</body></html>")
        if ".png" in " ".join(docs.values()):
            z.writestr("OEBPS/note.png", PNG)


def run_convert(epub, outdir, *extra):
    cmd = [sys.executable, SCRIPT, epub, "-o", outdir, *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return proc.stdout


def read(outdir, name):
    with open(os.path.join(outdir, name), encoding="utf-8") as fh:
        return fh.read()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_continuation(tmp):
    epub = os.path.join(tmp, "continuation.epub")
    make_epub(
        epub,
        {
            "c1.xhtml": "<p>第一章</p>",
            "c1b.xhtml": "<p>" + "这是本该属于第一章的正文，却被拆在下一个文档里。" * 12 + "</p>",
            "c2.xhtml": "<h1>第二章</h1><p>" + "第二章正文。" * 40 + "</p>",
        },
        [("第一章", "c1.xhtml"), ("第二章", "c2.xhtml")],
    )
    out = os.path.join(tmp, "continuation")
    run_convert(epub, out)
    files = sorted(n for n in os.listdir(out) if n.endswith(".md") and n != "README.md")
    check(len(files) == 2, f"continuation: expected 2 chapters, got {files}")
    check("这是本该属于第一章的正文" in read(out, files[0]), "continuation: body missing")
    check("前置页" not in " ".join(files), "continuation: TOC-less body became a 前置页")
    print("ok  continuation (empty title page + body in the next spine doc)")


def test_alt_duplication(tmp):
    epub = os.path.join(tmp, "alt.epub")
    note = "这是一条很长的脚注正文，图片的 alt 逐字复制了它，转换器不能再写第二遍。"
    body = (
        f'<p>正文<sup><a epub:type="noteref" href="#fn1">'
        f'<img src="note.png" alt="{note}"/></a></sup></p>'
        f'<aside epub:type="footnote">{note}</aside>'
    )
    make_epub(epub, {"c1.xhtml": body}, [("第一章", "c1.xhtml")])
    out = os.path.join(tmp, "alt")
    run_convert(epub, out)
    text = read(out, "01_第一章.md")
    check(text.count(note) == 1, "alt: note duplicated")
    check("![这是一条很长" not in text, "alt: footnote prose kept in image alt")
    check("![](images/note.png)" in text, "alt: image lost")
    print("ok  image alt (note icon does not repeat the footnote)")


def test_colophon(tmp):
    epub = os.path.join(tmp, "colophon.epub")
    colophon = (
        "<p>产品经理│张三</p><p>责任编辑│李四</p>"
        "<p>封面设计│王五</p><p>官方网站 http://example.com</p>"
    )
    make_epub(
        epub,
        {"c1.xhtml": "<h1>第一章</h1><p>" + "正文。" * 300 + "</p>", "c2.xhtml": colophon},
        [("第一章", "c1.xhtml")],
    )
    out = os.path.join(tmp, "colophon")
    run_convert(epub, out)
    files = sorted(n for n in os.listdir(out) if n.endswith(".md") and n != "README.md")
    check(len(files) == 1, f"colophon: expected 1 chapter, got {files}")
    check("产品经理" not in read(out, files[0]), "colophon: publisher page leaked into the chapter")
    print("ok  colophon (publisher page excluded, not appended to the last chapter)")


def test_link_to_excluded(tmp):
    epub = os.path.join(tmp, "links.epub")
    make_epub(
        epub,
        {
            "front.xhtml": "<p>导读</p>",
            "c1.xhtml": '<p>正文 <a href="front.xhtml#s1">[1]</a></p>',
        },
        [("第一章", "c1.xhtml")],
    )
    out = os.path.join(tmp, "links")
    run_convert(epub, out)
    text = read(out, "01_第一章.md")
    check("[[1]]()" not in text, "link: bracketed label left an empty target")
    check("[1]" in text, "link: dropped the footnote label")
    print("ok  links (bracketed label to an excluded page becomes plain text)")


def test_apparatus(tmp):
    epub = os.path.join(tmp, "apparatus.epub")
    make_epub(
        epub,
        {
            "c1.xhtml": "<h1>第一章</h1><p>" + "正文。" * 80 + "</p>",
            "notes.xhtml": "<h1>注释</h1><p>" + "注文。" * 30 + "</p>",
            "refs.xhtml": "<h1>参考文献</h1><p>" + "[1] 文献。" * 30 + "</p>",
            "after.xhtml": "<h1>后记</h1><p>" + "后记正文。" * 20 + "</p>",
            "index.xhtml": "<h1>索引</h1><p>正文 1 2 3</p>",
        },
        [
            ("第一章", "c1.xhtml"),
            ("注释", "notes.xhtml"),
            ("参考文献", "refs.xhtml"),
            ("后记", "after.xhtml"),
            ("索引", "index.xhtml"),
        ],
    )
    out = os.path.join(tmp, "apparatus")
    run_convert(epub, out)
    files = " ".join(sorted(os.listdir(out)))
    check("注释" in files, "apparatus: 注释 dropped")
    check("参考文献" in files, "apparatus: 参考文献 dropped")
    check("后记" in files, "apparatus: 后记 dropped")
    check("索引" not in files, "apparatus: 索引 kept")
    print("ok  apparatus (注释/参考文献/后记 kept, 索引 dropped)")


def test_title_filter():
    sys.path.insert(0, HERE)
    from ebook_to_md import is_body_title

    for title in ("注释", "注释 卢梭注于讲稿完成后", "参考文献", "后记", "词汇表", "Notes", "References", "Epilogue"):
        check(is_body_title(title), f"title filter: {title!r} should be body")
    for title in ("索引", "致谢", "推荐序 赋能", "前言", "Index", "Acknowledgments"):
        check(not is_body_title(title), f"title filter: {title!r} should be matter")
    print("ok  title filter")


def main():
    with tempfile.TemporaryDirectory(prefix="ebook-to-md-selftest-") as tmp:
        test_title_filter()
        test_continuation(tmp)
        test_alt_duplication(tmp)
        test_colophon(tmp)
        test_link_to_excluded(tmp)
        test_apparatus(tmp)
    print("all selftests passed")


if __name__ == "__main__":
    main()
