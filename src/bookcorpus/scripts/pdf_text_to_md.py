#!/usr/bin/env python3
"""把纯文本层 PDF 抽成库内的 md 契约：README.md + images/ + .manifest.json。

结构由源文件决定（操作人 2026-10-19 的规则）：PDF 自带可用目录的按目录切章，一章一个文件；
目录不可用或没有目录的，只生成一个内容文件。--structure auto 走这条判断，single/chapters
是逃生门，强制一种结构（chapters 不看可用性判据）。

目录可用的判据：条目 ≥3、全书 ≥20 页、每条页号都在 1..页数 内且不回退、最浅层有条目。
切章取最浅层的条目做章首，每章覆盖 [章首, 下一章首-1]，最后一章到卷末；目录首条之前的页
并进第一章，所以一页都不会丢。章内正文空的不写，数目记 empty_chapters。

抽字按页读序取 get_text("dict") 的块：文字块成段（块内换行只在两侧非中日韩字时才留空格），
相邻块同栏、行距小、上一段没结句、下一块不是标题列表时接成一段（--merge-blocks，默认开；
有框线的表页不并），图块按块序换成 ![](images/p%04d_i%02d.<ext>)，只留被引用的图；宽或高小于
64 px 的图丢掉，数目记 images_dropped。图文件名带页号，两页不会撞。

README、.manifest.json、front matter、文件名清洗、目录链接都复用 pdf_ocr_to_md.py 那套，
拼装器只有一份。

用法:
  python3 pdf_text_to_md.py --pdf books/<uid>/source/x.pdf --out out/B0517 --uid B0517 --title Hokusai \
      --author "Edmond de Goncourt" --source-sha256 xxx --method pdf-text --structure auto
"""

import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf_ocr_to_md as A
import pymupdf as fitz

MIN_IMAGE_PX = 64
CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
UNLISTED_CHAPTERS = "- 无（按页全收，目录首条之前的页并进第一章）"
UNLISTED_SINGLE = "- 无（文本层按页全收）"

SENT_END = "。！？…；：!?.;:"
SENT_CLOSERS = "\u201d\u2019\"'\u300d\u300f\uff09\u3011\u300b\u3009"
LIST_START = re.compile(
    r"^(?:[\u2022\u00b7*#>\-\u2013\u2014]|\d+[.\u3001)]"
    r"|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.]"
    r"|[\uff08(][\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+[)\uff09])"
)
HEAD_START = re.compile(
    r"^(?:\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e0-9]+[\u7ae0\u8282\u7bc7\u90e8]"
    r"|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.]"
    r"|[\uff08(][\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+[)\uff09]"
    r"|\d+(?:\.\d+)*[\u3001.\s])"
)
EXPLICIT_PAGE_RE = re.compile(
    r"^(?:[-–—]{1,3}\s*\d{1,4}\s*[-–—]{0,3}|\d{1,4}\s*[-–—]{1,3}|第\s*\d{1,4}\s*页|(?:page|p\.?)\s*\d{1,4})$",
    re.I,
)
BARE_PAGE_RE = re.compile(r"^\d{1,4}$")
CAPTION_START_RE = re.compile(
    r"^(?:图|表|插图|附图|附表|Figure|Fig\.?|Table|Chart|数据来源|资料来源|来源|注|Note)\s*[\d.:：-]",
    re.I,
)
CHAPTER_TIER1_RE = re.compile(
    r"^(?:第\s*[〇零一二三四五六七八九十百千两0-9]+\s*[章篇部卷回]|Chapter\s+\d+|Part\s+\d+|"
    r"前言|序言?|自序|代序|导言|引言|绪论|后记|结语|附录|参考文献|注释)",
    re.I,
)


def _hf_key(text):
    s = " ".join((text or "").split())
    s = re.sub(r"^\d{1,4}\s+", "# ", s)
    return re.sub(r"\s+\d{1,4}$", " #", s)


def detect_doc_layout(doc):
    """全书只读扫描一次：计算正文字号、标题字号分档，并定位页眉页脚与页码块索引。"""
    size_counts = collections.Counter()
    band_counts = collections.Counter()
    page_entries = []

    for p_idx, page in enumerate(doc):
        blocks = page.get_text("dict")["blocks"]
        page_h = float(page.rect.height or 800.0)
        entries = []
        for b_idx, b in enumerate(blocks):
            if b.get("type") == 1:
                continue
            text = block_text(b)
            if not text:
                continue
            for ln in b.get("lines") or []:
                for sp in ln.get("spans") or []:
                    st = (sp.get("text") or "").strip()
                    if st:
                        size_counts[round(sp["size"], 1)] += len(st)
            _x0, y0, _x1, y1 = b["bbox"]
            band = ""
            if y0 <= 0.13 * page_h or y1 <= 0.16 * page_h:
                band = "top"
            elif y1 >= 0.87 * page_h or y0 >= 0.84 * page_h:
                band = "bottom"
            entries.append((b_idx, text, band))
            if (
                band
                and len(text) <= 60
                and not EXPLICIT_PAGE_RE.match(text.strip())
                and not BARE_PAGE_RE.fullmatch(text.strip())
                and not text.strip().endswith(("。", "！", "？", "!", "?", "：", ":", "，", ","))
            ):
                band_counts[(band, _hf_key(text))] += 1
        page_entries.append((p_idx, entries))

    doc_body = size_counts.most_common(1)[0][0] if size_counts else 10.0
    threshold = max(3, int(doc.page_count * 0.25)) if doc.page_count >= 3 else 999999
    repeated = {bk for bk, cnt in band_counts.items() if cnt >= threshold}

    skip_by_page = {}
    for p_idx, entries in page_entries:
        skips = set()
        for b_idx, text, band in entries:
            s = text.strip()
            if EXPLICIT_PAGE_RE.match(s) and len(s) <= 25:
                skips.add(b_idx)
                continue
            if BARE_PAGE_RE.fullmatch(s):
                num = int(s)
                if 1 <= num <= 2500 and (band or not (1900 <= num <= 2099)):
                    skips.add(b_idx)
                    continue
            if band and (band, _hf_key(s)) in repeated:
                skips.add(b_idx)
        if skips:
            skip_by_page[p_idx] = skips

    return doc_body, skip_by_page


def block_dominant_size(b, body):
    """按字符加权统计块的主导字号，防止单个大号首字/公式把正文段误判成标题。"""
    counts = collections.Counter()
    max_sz = 0.0
    line_count = 0
    for ln in b.get("lines") or []:
        line_txt = "".join(sp.get("text") or "" for sp in ln.get("spans") or []).strip()
        if not line_txt:
            continue
        line_count += 1
        for sp in ln.get("spans") or []:
            st = (sp.get("text") or "").strip()
            if st:
                sz = round(sp["size"], 1)
                counts[sz] += len(st)
                max_sz = max(max_sz, sz)
    if not counts:
        return body, max_sz or body, line_count
    dom = counts.most_common(1)[0][0]
    return dom, max_sz, line_count


def _heading_level(text, dom_size, body, primary_heading_size=None):
    """判定真标题并返回 Markdown 层级（``##`` 或 ``###``），非标题返回 ``0``。"""
    s = (text or "").strip()
    if not s or s.startswith("#") or len(s) > 60:
        return 0
    if dom_size < body * 1.15 or (dom_size - body) < 0.8:
        return 0
    if CAPTION_START_RE.match(s):
        return 0
    if s[-1] in "。，；,;":
        return 0
    if ("。" in s or "；" in s) or ("，" in s and len(s) > 22):
        return 0
    if s[0].islower():
        return 0
    if re.match(r"^[•·*\->\u2013\u2014]\s+", s):
        return 0
    if CHAPTER_TIER1_RE.match(s):
        return 2
    if primary_heading_size and dom_size >= primary_heading_size - 0.3:
        return 2
    if re.match(r"^\d+\.\d+", s):
        return 3
    return 2 if not primary_heading_size else 3


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


def body_size(blocks):
    """页内覆盖字符最多的字号（一位小数，按字数加权）。页上没有字时给 10 pt。"""
    sizes = collections.Counter()
    for b in blocks:
        for ln in b.get("lines") or []:
            for sp in ln.get("spans") or []:
                t = (sp.get("text") or "").strip()
                if t:
                    sizes[round(sp["size"], 1)] += len(t)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def block_metrics(b, text, body):
    """并段要用的块几何：bbox、块内最大字号（正文缺省）、每行高度。"""
    lines = [
        ln for ln in (b.get("lines") or []) if "".join(sp.get("text") or "" for sp in ln.get("spans") or []).strip()
    ]
    size = 0.0
    for ln in lines:
        for sp in ln.get("spans") or []:
            if (sp.get("text") or "").strip():
                size = max(size, round(sp["size"], 1))
    x0, y0, x1, y1 = b["bbox"]
    return {
        "text": text,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "size": size or body,
        "lh": (y1 - y0) / max(1, len(lines)),
    }


def _can_merge(a, b, body, page_w):
    """后一块 b 能不能接上前一段 a。同栏 + 行距小 + a 没结句 + 两块都不是标题列表。
    逗号顿号不算结句，否则 ePub 转出的 PDF 会在每个逗号处断段，规则等于没做。
    """
    if not a or not b or not a["text"] or not b["text"] or len(a["text"]) < 4:
        return False
    for blk in (a, b):
        if blk["size"] > body * 1.15 and len(blk["text"]) <= 20:
            return False
    if len(a["text"]) <= 40 and HEAD_START.match(a["text"]):
        return False
    tail = a["text"].rstrip(SENT_CLOSERS)
    if tail and tail[-1] in SENT_END:
        return False
    if LIST_START.match(b["text"]):
        return False
    if b["y0"] < a["y0"] - 1:
        return False
    wa, wb = a["x1"] - a["x0"], b["x1"] - b["x0"]
    overlap = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    if not (overlap >= 0.5 * min(wa, wb) or abs(a["x0"] - b["x0"]) <= 0.15 * page_w):
        return False
    return -3.0 <= b["y0"] - a["y1"] <= max(2.0, 0.9 * a["lh"])


def _join_text(a, b):
    """两段接一行：两侧都是中日韩字（含全角标点）时不加空格，否则加一个。"""
    return a + ("" if CJK_RE.match(a[-1]) and CJK_RE.match(b[0]) else " ") + b


def is_table_page(page):
    """有框线的表格页：细矩形横线去重后的 y 数 ≥6、竖线去重后的 x 数 ≥3。
    这类页不并段，保持逐块成段，跟并段之前逐字节一致。无边框表判不出来，已知。
    """
    ys, xs = set(), set()
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] != "re":
                continue
            r = it[1]
            if r.height < 2 and r.width > 5:
                ys.add(round(r.y0))
            elif r.width < 2 and r.height > 5:
                xs.add(round(r.x0))
    return len(xs) >= 3 and len(ys) >= 6


def page_items(
    page,
    min_px=MIN_IMAGE_PX,
    keep_images=True,
    merge_blocks=True,
    *,
    skip_block_indices=None,
    doc_body=None,
    primary_heading_size=None,
    detect_headings=False,
):
    """一页按读序的块：文字块成段落字符串，图块成 ("image", 扩展名, 字节, 宽, 高)。
    过小的图按丢弃计数。返回 (块, 丢弃数)。keep_images=False 时不取图片字节（审计只数文字，
    省下每页几 MB），丢弃口径不变。

    merge_blocks=True 时跨块并段：相邻文字块同栏且行距小、上一段没结句、下一块不是标题列表，
    就接成一段（头图或表页不并）。一行一块的 ePub 转出 PDF 靠这个才成整段。
    """
    blocks = page.get_text("dict")["blocks"]
    tbl_page = is_table_page(page)
    merge = merge_blocks and not tbl_page
    body = doc_body if doc_body else (body_size(blocks) if (merge or detect_headings) else 0.0)
    page_w = page.rect.width
    skips = skip_block_indices or set()
    items, dropped, cur = [], 0, None

    def _emit_cur():
        nonlocal cur
        if not cur:
            return
        txt = cur["text"]
        if cur.get("heading_lvl"):
            txt = ("#" * cur["heading_lvl"]) + " " + txt
        items.append(txt)
        cur = None

    for b_idx, b in enumerate(blocks):
        if b_idx in skips:
            continue
        if b.get("type") != 1:
            t = block_text(b)
            if not t:
                continue
            dom_sz, _max_sz, line_cnt = block_dominant_size(b, body)
            h_lvl = (
                _heading_level(t, dom_sz, body, primary_heading_size=primary_heading_size)
                if (detect_headings and not tbl_page and line_cnt <= 3)
                else 0
            )
            if not merge:
                if h_lvl:
                    items.append(("#" * h_lvl) + " " + t)
                else:
                    items.append(t)
                continue
            nxt = block_metrics(b, t, body)
            nxt["size"] = max(dom_sz, nxt["size"])
            nxt["heading_lvl"] = h_lvl
            if h_lvl:
                _emit_cur()
                cur = nxt
                _emit_cur()
                continue
            if cur and not cur.get("heading_lvl") and _can_merge(cur, nxt, body, page_w):
                cur["text"] = _join_text(cur["text"], nxt["text"])
                cur["y1"] = nxt["y1"]
                cur["x1"] = max(cur["x1"], nxt["x1"])
                continue
            _emit_cur()
            cur = nxt
            continue
        _emit_cur()
        w, h = int(b.get("width") or 0), int(b.get("height") or 0)
        data = (b.get("image") or b"") if keep_images else b""
        if w >= min_px and h >= min_px and (bool(data) or not keep_images):
            items.append(("image", (b.get("ext") or "png").lstrip("."), data, w, h))
        else:
            dropped += 1
    _emit_cur()
    return items, dropped


def usable_toc(doc, toc):
    """目录能不能拿来切章。可用时返回空串，否则返回拒绝原因。"""
    if len(toc) < 3:
        return "目录只有 %d 条（要 ≥3 条）" % len(toc)
    if doc.page_count < 20:
        return "全书 %d 页（要 ≥20 页）" % doc.page_count
    for i, (_lv, title, pg) in enumerate(toc):
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


def extract(doc, out_dir, merge_blocks=True):
    """摊开全书：去页眉页脚/页码、输出字号标题、落盘被引用的图。返回 (逐页块, 丢弃图数, 留图数)。"""
    doc_body, skip_by_page = detect_doc_layout(doc)
    # 预扫候选题号最大字号，用于区分 ## 与 ###
    cand_sizes = set()
    for p_idx, page in enumerate(doc):
        if is_table_page(page):
            continue
        skips = skip_by_page.get(p_idx, set())
        for b_idx, b in enumerate(page.get_text("dict")["blocks"]):
            if b_idx in skips or b.get("type") == 1:
                continue
            t = block_text(b)
            if not t:
                continue
            dom_sz, _, lc = block_dominant_size(b, doc_body)
            if lc <= 3 and _heading_level(t, dom_sz, doc_body):
                cand_sizes.add(round(dom_sz, 1))
    primary_heading_size = max(cand_sizes) if len(cand_sizes) >= 2 else None

    pages, dropped, used = [], 0, 0
    for i, page in enumerate(doc, 1):
        items, ndrop = page_items(
            page,
            merge_blocks=merge_blocks,
            skip_block_indices=skip_by_page.get(i - 1),
            doc_body=doc_body,
            primary_heading_size=primary_heading_size,
            detect_headings=True,
        )
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
    text = '---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\npages: %d\n---\n\n# %s\n\n%s\n' % (
        A.fm_title(a.title),
        A.fm_title(a.author),
        os.path.basename(a.pdf),
        doc.page_count,
        a.title,
        body,
    )
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
        body = "\n\n".join(x for x in ("\n\n".join(p["parts"]) for p in pages[s - 1 : end]) if x.strip())
        if not body.strip():
            empty += 1
            continue
        title = A.clean_title(" ".join(title.split()), seen) or ("%s 第 %d 章" % (a.title, s))
        body = demote_h1(body)
        idx = len(chapters) + 1
        fm = '---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\nchapter: %d\npage: %d\n---\n' % (
            A.fm_title(title),
            A.fm_title(a.author),
            os.path.basename(a.pdf),
            idx,
            s,
        )
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
    ap.add_argument(
        "--merge-blocks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="跨块并段（默认开）。--no-merge-blocks 回到逐块成段，供对照与回滚",
    )
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
    pages, dropped, used = extract(doc, a.out, merge_blocks=a.merge_blocks)
    if structure == "chapters":
        chapters, chars, bytes_, empty = write_chapters(doc, pages, a, a.out)
        unlisted = UNLISTED_CHAPTERS
    else:
        chapters, chars, bytes_, empty = write_single(doc, pages, a, a.out)
        unlisted = UNLISTED_SINGLE

    A.write_readme(
        a.out,
        a.title,
        a.collection,
        a.author,
        a.date,
        len(chapters),
        used,
        os.path.basename(a.pdf),
        [(c["title"], c["file"]) for c in chapters],
        unlisted,
    )
    manifest = {
        "uid": a.uid,
        "method": a.method,
        "tool_version": a.tool_version,
        "source": a.pdf,
        "source_sha256": a.source_sha256,
        "pages": doc.page_count,
        "chapters": len(chapters),
        "bytes": bytes_,
        "chars": chars,
        "images": used,
        "images_dropped": dropped,
        "empty_chapters": empty,
        "structure": structure,
        "single_file": structure == "single",
        "toc_entries": len(toc),
        "toc_levels": len({t[0] for t in toc}),
        "toc_rejected": reject if (toc and structure == "single") else "",
        "generated_at": A.now(),
        "assembler": "pdf_text_to_md.py",
        "verified": False,
    }
    A.write_manifest(a.out, manifest)

    if manifest["toc_rejected"]:
        print("目录不可用，走单文件：%s" % manifest["toc_rejected"])
    print(
        "结构 %s，写出 %d 个内容文件，%d 字，图 %d 张（丢 %d 张过小的），%d 页输入 -> %s"
        % (structure, len(chapters), chars, used, dropped, doc.page_count, a.out)
    )
    if empty:
        print("  空章 %d 个未写出" % empty)
    for c in chapters[:6]:
        print("  %s  %d 字" % (c["file"], c["chars"]))
    doc.close()


if __name__ == "__main__":
    main()
