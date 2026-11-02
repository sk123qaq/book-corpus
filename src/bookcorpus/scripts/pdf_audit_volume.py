#!/usr/bin/env python3
"""文本层路线的 PDF 侧审计：md 正文占源书文本层字数的比例，加逐页锚点覆盖率。

与 audit_old_md.py、redo_compare.py 同一套锚点思路。字数按 split_md_batch.py 的 COUNT_RE
计（每个中日韩字或字母数字算一个），md 侧去掉 front matter；锚点是每页跳过前 10 个计数字符后
每 20 个计数字符一个窗口，去空白后在同样去空白的 md 正文里找。比例低于门槛说明 md 少了正文，
锚点落空说明那些页没进 md。只读，不写盘。

退出码 0 = 字数比 ≥ --min-ratio（默认 0.95）且锚点覆盖 ≥ --min-anchors（默认 0.90）。
页级锚点只算计数字符 ≥40 的页；一页窗口全落空会列出来给人看。
PDF 侧字数按整页算，不扣卷首/卷尾页，md 侧又去掉了 front matter，所以字数比可能偏低。

用法:
  python3 pdf_audit_volume.py books/<uid>/source/x.pdf -o out/B0517 [--min-ratio 0.95] [--min-anchors 0.90]
"""
import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from split_md_batch import COUNT_RE  # noqa: E402
import pdf_text_to_md as T  # noqa: E402

import pymupdf as fitz  # noqa: E402

WS_RE = re.compile(r"\s")
SKIP_CHARS, WINDOW, WINDOWS, MIN_PAGE_CHARS, MAX_MISS_PAGES = 10, 20, 3, 40, 10


def md_body(md_dir):
    """内容文件的正文（去 front matter），README.md 不算。返回 (正文, 文件数)。"""
    parts = []
    for path in sorted(glob.glob(os.path.join(md_dir, "*.md"))):
        if os.path.basename(path) == "README.md":
            continue
        text = open(path, encoding="utf-8").read()
        end = text.find("\n---", 3) if text.startswith("---\n") else -1
        parts.append(text[end + 4:] if end >= 0 else text)
    return "\n".join(parts), len(parts)


def page_text(page):
    """一页的正文：块按读序，只要文字块。"""
    items, _ = T.page_items(page, keep_images=False)
    return "\n".join(x for x in items if isinstance(x, str))


def windows(text):
    """页内锚点窗口：跳过前 SKIP_CHARS 个计数字符，之后每 WINDOW 个计数字符一个窗口。"""
    idx = [i for i, ch in enumerate(text) if COUNT_RE.match(ch)]
    if len(idx) < MIN_PAGE_CHARS:
        return []
    out = []
    for k in range(WINDOWS):
        a = SKIP_CHARS + k * WINDOW
        if a + WINDOW > len(idx):
            break
        out.append(WS_RE.sub("", text[idx[a]:idx[a + WINDOW - 1] + 1]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True, help="书的 md 目录（含 README.md 的那层）")
    ap.add_argument("--min-ratio", type=float, default=0.95)
    ap.add_argument("--min-anchors", type=float, default=0.90)
    a = ap.parse_args()

    body, n_md = md_body(a.out)
    if not n_md:
        sys.exit("md 目录里没有内容文件: " + a.out)
    md_chars = len(COUNT_RE.findall(body))
    body_nows = WS_RE.sub("", body)

    pdf_chars, hits, total, miss, pages_with_anchor = 0, 0, 0, [], 0
    try:
        doc = fitz.open(a.pdf)
    except Exception as exc:
        sys.exit("打不开 PDF：%s（%s: %s）" % (a.pdf, type(exc).__name__, exc))
    try:
        for page in doc:
            text = page_text(page)
            pdf_chars += len(COUNT_RE.findall(text))
            wins = windows(text)
            if not wins:
                continue
            pages_with_anchor += 1
            got = [w for w in wins if w in body_nows]
            hits += len(got)
            total += len(wins)
            if not got:
                miss.append(page.number + 1)
    finally:
        doc.close()

    ratio = (md_chars / pdf_chars) if pdf_chars else 0.0
    anchors = (hits / total) if total else 0.0
    print("source : %s" % a.pdf)
    print("outdir : %s" % a.out)
    print()
    print("%9s / %9s  %5.2f%%  %4d md" % (format(md_chars, ","), format(pdf_chars, ","),
                                          ratio * 100, n_md))
    print("锚点 : %d/%d 窗命中 %.2f%%（%d 页有锚点，%d 页全落空）"
          % (hits, total, anchors * 100, pages_with_anchor, len(miss)))
    print("全落空 : %s" % ("、".join("第 %d 页" % p for p in miss[:MAX_MISS_PAGES]) if miss else "无"))
    ok = ratio >= a.min_ratio and anchors >= a.min_anchors
    print("判据 : 字数比 ≥%.0f%% 且锚点 ≥%.0f%% -> %s"
          % (a.min_ratio * 100, a.min_anchors * 100, "过" if ok else "不过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
