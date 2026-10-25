#!/usr/bin/env python3
"""把纯文本层 PDF 抽成库内的 md 契约：README.md + images/ + .manifest.json。

结构由源文件决定（操作人 2026-10-19 的规则）：PDF 自带可用目录的按目录切章，一章一个文件；
目录不可用或没有目录的，只生成一个内容文件。--structure auto 走这条判断，single/chapters
是逃生门，强制一种结构（chapters 不看可用性判据）。

目录可用的判据：条目 ≥3、全书 ≥20 页、每条页号都在 1..页数 内且不回退、最浅层有条目。
切章取最浅层的条目做章首，每章覆盖 [章首, 下一章首-1]，最后一章到卷末；目录首条之前的页
并进第一章，所以一页都不会丢。章内正文空的不写，数目记 empty_chapters。

抽字按页读序取 get_text("dict") 的块：文字块成段（块内换行只在两侧非中日韩字时才留空格），
图块按块序换成 ![](images/p%04d_i%02d.<ext>)，只留被引用的图；宽或高小于 64 px 的图丢掉，
数目记 images_dropped。图文件名带页号，两页不会撞。

README、.manifest.json、front matter、文件名清洗、目录链接都复用 pdf_ocr_to_md.py 那套，
拼装器只有一份。

用法:
  python3 pdf_text_to_md.py --pdf 库/…/x.pdf --out out/B0517 --uid B0517 --title Hokusai \
      --author "Edmond de Goncourt" --source-sha256 xxx --method pdf-text --structure auto
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf_ocr_to_md as A  # noqa: E402

import pymupdf as fitz  # noqa: E402

MIN_IMAGE_PX = 64
CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
UNLISTED_CHAPTERS = "- 无（按页全收，目录首条之前的页并进第一章）"
UNLISTED_SINGLE = "- 无（文本层按页全收）"


def block_text(b):
    """块内各行接成一段。行尾与下行首都是中日韩字（含全角标点）时不加空格，否则加一个。"""
    lines = []
    for ln in b.get("lines") or []:
        s = "".join(sp.get("text") or "" for sp in ln.get("spans") or []).strip()
        if s:
            lines.append(s)
    if not lines:
        return ""
    out = lines[0]
    for s in lines[1:]:
        out += ("" if CJK_RE.match(out[-1]) and CJK_RE.match(s[0]) else " ") + s
    return out


def page_items(page, min_px=MIN_IMAGE_PX, keep_images=True):
    """一页按读序的块：文字块成段落字符串，图块成 ("image", 扩展名, 字节, 宽, 高)。
    过小的图按丢弃计数。返回 (块, 丢弃数)。keep_images=False 时不取图片字节（审计只数文字，
    省下每页几 MB），丢弃口径不变。
    """
    items, dropped = [], 0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 1:
            t = block_text(b)
            if t:
                items.append(t)
            continue
        w, h = int(b.get("width") or 0), int(b.get("height") or 0)
        data = (b.get("image") or b"") if keep_images else b""
        if w >= min_px and h >= min_px and (bool(data) or not keep_images):
            items.append(("image", (b.get("ext") or "png").lstrip("."), data, w, h))
        else:
            dropped += 1
    return items, dropped


def usable_toc(doc, toc):
    """目录能不能拿来切章。可用时返回空串，否则返回拒绝原因。"""
    if len(toc) < 3:
        return "目录只有 %d 条（要 ≥3 条）" % len(toc)
    if doc.page_count < 20:
        return "全书 %d 页（要 ≥20 页）" % doc.page_count
    for i, (lv, title, pg) in enumerate(toc):
        if not (1 <= int(pg) <= doc.page_count):
            return "第 %d 条「%s」页号 %d 不在 1..%d 内" % (i + 1, title[:20], pg, doc.page_count)
    pages = [int(t[2]) for t in toc]
    for i in range(1, len(pages)):
        if pages[i] < pages[i - 1]:
            return "第 %d 条页号从 %d 回退到 %d，按目录切会乱序" % (i + 1, pages[i - 1], pages[i])
    return ""


def chapter_starts(toc):
    """每章的 (标题, 章首页)。取最浅层的条目；页号回退或重复时夹到上一章之后，
    这样每页恰好属于一章，也不会切出空范围。
    """
    shallow = min(int(lv) for lv, _t, _p in toc)
    out = []
    for lv, title, page in toc:
        if int(lv) != shallow:
            continue
        s = 1 if not out else max(int(page), out[-1][1] + 1)
        out.append((" ".join(str(title).split()), s))
    return out


def extract(doc, out_dir):
    """摊开全书：被引用的图落 images/，正文里的图换成一个占位。返回 (逐页块, 丢弃图数, 留图数)。"""
    pages, dropped, used = [], 0, 0
    for i, page in enumerate(doc, 1):
        items, ndrop = page_items(page)
        dropped += ndrop
        parts, n = [], 0
        for it in items:
            if isinstance(it, str):
                parts.append(it)
                continue
            n += 1
            name = "p%04d_i%02d.%s" % (i, n, it[1])
            os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
            with open(os.path.join(out_dir, "images", name), "wb") as fh:
                fh.write(it[2])
            used += 1
            parts.append("![](images/%s)" % name)
        pages.append({"no": i, "parts": parts})
    return pages, dropped, used


def demote_h1(body):
    return re.sub(r"(?m)^#\s+", "### ", body)


def write_single(doc, pages, a, out_dir):
    """一本书一个内容文件：页序拼接，空页跳过。front matter 与拼装器的 single 同形。"""
    parts = ["\n\n".join(p["parts"]) for p in pages if p["parts"]]
    body = demote_h1("\n\n".join(parts))
    fname = A.sanitize_filename(a.title, a.uid) + ".md"
    text = ('---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\npages: %d\n---\n\n# %s\n\n%s\n'
            % (A.fm_title(a.title), A.fm_title(a.author), os.path.basename(a.pdf),
               doc.page_count, a.title, body))
    with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
        fh.write(text)
    chapters = [{"index": 1, "title": a.title, "file": fname, "chars": len(body)}]
    return chapters, len(body), len(text.encode("utf-8")), 0


def write_chapters(doc, pages, a, out_dir):
    """一章一个文件，章号只数写出来的章。"""
    starts = chapter_starts(doc.get_toc())
    seen, chapters, empty = set(), [], 0
    total_chars, total_bytes = 0, 0
    for k, (title, s) in enumerate(starts):
        end = starts[k + 1][1] - 1 if k + 1 < len(starts) else doc.page_count
        body = "\n\n".join(x for x in ("\n\n".join(p["parts"]) for p in pages[s - 1:end])
                           if x.strip())
        if not body.strip():
            empty += 1
            continue
        title = A.clean_title(" ".join(title.split()), seen) or ("%s 第 %d 章" % (a.title, s))
        body = demote_h1(body)
        idx = len(chapters) + 1
        fm = ('---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\nchapter: %d\npage: %d\n---\n'
              % (A.fm_title(title), A.fm_title(a.author), os.path.basename(a.pdf), idx, s))
        text = fm + "\n# %s\n\n%s\n" % (title, body)
        fname = ("%02d_%s.md" % (idx, title))[:120].replace(" ", "_")
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
        total_chars += len(body)
        total_bytes += len(text.encode("utf-8"))
        chapters.append({"index": idx, "title": title, "file": fname, "chars": len(body)})
    return chapters, total_chars, total_bytes, empty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--collection", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--source-sha256", default="")
    ap.add_argument("--method", default="pdf-text")
    ap.add_argument("--tool-version", default="")
    ap.add_argument("--structure", choices=("auto", "single", "chapters"), default="auto")
    a = ap.parse_args()

    doc = fitz.open(a.pdf)
    toc = [(int(lv), str(title), int(pg)) for lv, title, pg in doc.get_toc()]
    reject = usable_toc(doc, toc) if toc else ""
    structure = a.structure
    if structure == "auto":
        structure = "chapters" if (toc and not reject) else "single"
    elif structure == "chapters" and not toc:
        sys.exit("目录是空的，切不了章: " + a.pdf)

    os.makedirs(a.out, exist_ok=True)
    pages, dropped, used = extract(doc, a.out)
    if structure == "chapters":
        chapters, chars, bytes_, empty = write_chapters(doc, pages, a, a.out)
        unlisted = UNLISTED_CHAPTERS
    else:
        chapters, chars, bytes_, empty = write_single(doc, pages, a, a.out)
        unlisted = UNLISTED_SINGLE

    A.write_readme(a.out, a.title, a.collection, a.author, a.date, len(chapters), used,
                   os.path.basename(a.pdf), [(c["title"], c["file"]) for c in chapters],
                   unlisted)
    manifest = {
        "uid": a.uid, "method": a.method, "tool_version": a.tool_version,
        "source": a.pdf, "source_sha256": a.source_sha256,
        "pages": doc.page_count, "chapters": len(chapters), "bytes": bytes_, "chars": chars,
        "images": used, "images_dropped": dropped, "empty_chapters": empty,
        "structure": structure, "single_file": structure == "single",
        "toc_entries": len(toc), "toc_levels": len({t[0] for t in toc}),
        "toc_rejected": reject if (toc and structure == "single") else "",
        "generated_at": A.now(), "assembler": "pdf_text_to_md.py", "verified": False,
    }
    A.write_manifest(a.out, manifest)

    if manifest["toc_rejected"]:
        print("目录不可用，走单文件：%s" % manifest["toc_rejected"])
    print("结构 %s，写出 %d 个内容文件，%d 字，图 %d 张（丢 %d 张过小的），%d 页输入 -> %s"
          % (structure, len(chapters), chars, used, dropped, doc.page_count, a.out))
    if empty:
        print("  空章 %d 个未写出" % empty)
    for c in chapters[:6]:
        print("  %s  %d 字" % (c["file"], c["chars"]))
    doc.close()


if __name__ == "__main__":
    main()
