#!/usr/bin/env python3
"""把逐页 OCR markdown 拼成书库的 md 契约：README.md + images/ + .manifest.json。

输入是 PaddleOCR 那种一页一文件（doc_001.md…）的目录，加一份页码映射（paddle_run.json 的
per_page，页序即文件序）。输出与 ebook-to-md 同形，好让 verify_output.py 直接复用。

两种结构（--structure）：
  single    一个内容文件 md/<书名>.md，页序拼接、空页跳过。扫描书按页全收，不做卷首卷尾过滤，
            默认值，OCR 路线只用它。
  chapters  按标题切章，一章一个文件。三级兜底：
              1. 第N章/第N篇/第N部/卷 这类章级标题开头，一章一文件；
              2. 全书没有章级标题时，任意 ## 标题都切；
              3. 一个标题都没有时，按 --fallback-pages 页数切并记进 README 与 manifest。
两种结构的 README 用同一个生成器，条目格式与库内其余书一致（- 合集 / - 作者 / - 日期 /
- 章节 / - 图片 / - 来源），`## 目录` 每行一条链接，好过 md_link_health.py 的判据。
合集、作者、日期都来自调用方（库里的人账），不知道就不写那一行——日期尤其不能拿生成日期
顶替，读者会当成出版年。

用法：
  python3 pdf_ocr_to_md.py --pages-dir <dir> --run-json <paddle_run.json> \
      --uid B0540 --title 中国神话史 --source 库/…/x.pdf --source-sha256 xxx --out <outdir>
  python3 pdf_ocr_to_md.py … --structure chapters   # 按标题切章
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

SRC_IMG_RE = re.compile(r"<div[^>]*>\s*<img[^>]*src=\"([^\"]+)\"[^>]*/?>\s*</div>", re.I)
IMG_TAG_RE = re.compile(r"<img[^>]*src=\"([^\"]+)\"[^>]*/?>", re.I)
CHAPTER_RE = re.compile(r"^(#+)\s*(第[〇零一二三四五六七八九十百千0-9]+[章篇部卷]|附录|后记|前言|序言|自序|导言|尾声|结语)\b")
SKIP_IMG = re.compile(r"(footer|header)", re.I)
BAD_FILENAME = re.compile(r"[\\/:*?\"<>|]")
UNLISTED_NONE = "- 无（扫描书按页全收）"
BARE_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]{0,200})\]\(")


def escape_bare_links(text):
    """OCR 正文里的 `[a](如“安”[an])` 会被当成 markdown 链接，目标并不是路径，
    渲染时还会把后面的括号吐掉。转义掉方括号，让文字原样显示。
    """
    return BARE_LINK_RE.sub(lambda m: "\\[%s\\](" % m.group(1), text)


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def link_dest(name):
    """标出文件名里会截断 markdown 目标的字符（括号、空格）。"""
    return "<%s>" % name if any(c in name for c in "() ") else name


def sanitize_filename(title, fallback, maxlen=120, maxbytes=240):
    """书名到内容文件名。与 clean_title 同一套字符口径，超长截断，空名退回 uid。
    再按 UTF-8 字节数截一刀：120 个汉字是 360 字节，超过文件名上限（255 字节）。
    """
    t = BAD_FILENAME.sub("", (title or "").strip()).strip(" .")[:maxlen].strip()
    b = t.encode("utf-8")
    if len(b) > maxbytes:
        t = b[:maxbytes].decode("utf-8", "ignore").strip()
    return t or fallback


def load_pages(pages_dir, run_json):
    def order(name):
        m = re.search(r"(\d+)", name)
        return (int(m.group(1)) if m else 0, name)

    pages = []
    for name in sorted((n for n in os.listdir(pages_dir) if n.endswith(".md")), key=order):
        text = open(os.path.join(pages_dir, name), encoding="utf-8").read()
        pages.append({"file": name, "text": escape_bare_links(text)})
    mapping = []
    if run_json and os.path.exists(run_json):
        run = json.load(open(run_json, encoding="utf-8"))
        mapping = [p.get("page") for p in run.get("per_page", [])]
    return pages, mapping


def rewrite_images(text, pages_dir, out_dir, used):
    """把 <div><img></div> 与裸 <img> 换成 markdown 图链，只搬被引用的图。"""
    def repl(m):
        src = m.group(1)
        if SKIP_IMG.search(src):
            return ""
        dst_rel = src if src.startswith("images/") else "images/" + os.path.basename(src)
        src_path = os.path.join(pages_dir, src)
        if not os.path.exists(src_path):
            return ""
        dst_path = os.path.join(out_dir, dst_rel)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        if dst_rel not in used:
            shutil.copyfile(src_path, dst_path)
        used.add(dst_rel)
        return "\n![](%s)\n" % dst_rel
    text = SRC_IMG_RE.sub(repl, text)
    text = IMG_TAG_RE.sub(repl, text)
    return text


def split_units(pages, fallback_pages, mapping=None):
    """(标题, 起始页序号, 正文) 列表。"""
    def page_no(i):
        return mapping[i] if mapping and i < len(mapping) else i + 1

    stream = []
    for i, p in enumerate(pages):
        stream.append((i, p["text"].rstrip()))
    units, cur = [], None
    for i, text in stream:
        lines = text.split("\n")
        first_head = None
        for j, line in enumerate(lines):
            m = CHAPTER_RE.match(line.strip())
            if m:
                first_head = (j, line.strip())
                break
        if first_head is None:
            if cur is None:
                cur = {"title": None, "start": i, "parts": []}
            cur["parts"].append(text)
            continue
        j, head = first_head
        lead = "\n".join(lines[:j]).strip()
        rest = "\n".join(lines[j:])
        if lead:
            if cur is None:
                cur = {"title": None, "start": i, "parts": [lead]}
            else:
                cur["parts"].append(lead)
        if cur:
            units.append(cur)
        cur = {"title": head, "start": i, "parts": [rest]}
    if cur:
        units.append(cur)

    if len([u for u in units if u["title"]]) == 0:
        units = []
        for i in range(0, len(pages), fallback_pages):
            chunk = [p["text"].rstrip() for p in pages[i:i + fallback_pages]]
            last = min(i + fallback_pages, len(pages)) - 1
            units.append({"title": "第 %d 至 %d 页" % (page_no(i), page_no(last)),
                          "start": i, "parts": chunk})
    return units


def clean_title(head, seen):
    t = re.sub(r"^#+\s*", "", head).strip()
    t = BAD_FILENAME.sub("", t)
    if len(t) > 60:
        t = t[:60]
    base, n = t, 2
    while t in seen:
        t = "%s（%d）" % (base, n)
        n += 1
    seen.add(t)
    return t


def write_readme(out_dir, title, collection, author, date, chapters, images, source_name, toc,
                 unlisted):
    """一个 README 生成器，single 与 chapters 共用。toc 是 [(标签, 文件名)]。"""
    lines = ["# %s" % title, ""]
    if collection:
        lines.append("- 合集：%s" % collection)
    if author:
        lines.append("- 作者：%s" % author)
    if date:
        lines.append("- 日期：%s" % date)
    lines.append("- 章节：%d" % chapters)
    lines.append("- 图片：%d" % images)
    lines.append("- 来源：`%s`" % (source_name or "未知"))
    lines += ["", "## 目录", ""]
    for label, fname in toc:
        lines.append("- [%s](%s)" % (label, link_dest(fname)))
    lines += ["", "## 未收录（卷首/卷尾页）", "", unlisted, ""]
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def fm_title(title):
    return title.replace('"', "'")


def write_manifest(out_dir, doc):
    with open(os.path.join(out_dir, ".manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def build_single(pages, a, used, out_dir):
    """一本书一个内容文件：页序拼接，空页跳过，正文里残留的 H1 降级。"""
    parts = [p["text"].strip() for p in pages if p["text"].strip()]
    body = re.sub(r"(?m)^#\s+", "### ", "\n\n".join(parts))
    fname = sanitize_filename(a.title, a.uid) + ".md"
    text = ('---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\npages: %d\n---\n\n# %s\n\n%s\n'
            % (fm_title(a.title), fm_title(a.author),
               os.path.basename(a.source) if a.source else "", len(pages), a.title, body))
    with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
        fh.write(text)
    return [{"index": 1, "title": a.title, "file": fname, "chars": len(body)}], len(body), \
        len(text.encode("utf-8"))


def build_chapters(pages, a, mapping, used, out_dir):
    units = split_units(pages, a.fallback_pages, mapping)
    seen, chapters, total_bytes, total_chars = set(), [], 0, 0
    for u in units:
        title = clean_title(u["title"] or "%s 开头" % a.title, seen)
        body = "\n\n".join(x.strip() for x in u["parts"] if x.strip())
        if not body:
            continue
        idx = len(chapters) + 1  # 空章不写，章号只数写出来的章
        body = re.sub(r"(?m)^#\s+", "### ", body)
        page_no = mapping[u["start"]] if u["start"] < len(mapping) else u["start"] + 1
        fm = ('---\ntitle: "%s"\nauthor: "%s"\nsource: "%s"\nchapter: %d\n'
              % (fm_title(title), fm_title(a.author),
                 os.path.basename(a.source) if a.source else "", idx))
        if page_no is not None:
            fm += "page: %d\n" % page_no
        fm += "---\n"
        text = fm + "\n# %s\n\n%s\n" % (title, body)
        fname = ("%02d_%s.md" % (idx, title))[:120].replace(" ", "_")
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
        total_bytes += len(text.encode("utf-8"))
        total_chars += len(body)
        chapters.append({"index": idx, "title": title, "file": fname, "chars": len(body)})
    return chapters, total_chars, total_bytes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--run-json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", default="")
    ap.add_argument("--source-sha256", default="")
    ap.add_argument("--method", default="paddle-ocr")
    ap.add_argument("--tool-version", default="")
    ap.add_argument("--collection", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--structure", choices=("single", "chapters"), default="single")
    ap.add_argument("--fallback-pages", type=int, default=30)
    a = ap.parse_args()

    pages, mapping = load_pages(a.pages_dir, a.run_json)
    if not pages:
        sys.exit("没有页文件: " + a.pages_dir)
    os.makedirs(a.out, exist_ok=True)
    used = set()
    for p in pages:
        p["text"] = rewrite_images(p["text"], a.pages_dir, a.out, used)

    if a.structure == "single":
        chapters, total_chars, total_bytes = build_single(pages, a, used, a.out)
    else:
        chapters, total_chars, total_bytes = build_chapters(pages, a, mapping, used, a.out)

    write_readme(a.out, a.title, a.collection, a.author, a.date,
                 len(chapters), len(used), os.path.basename(a.source) if a.source else "",
                 [(c["title"], c["file"]) for c in chapters], UNLISTED_NONE)
    manifest = {
        "uid": a.uid, "method": a.method, "tool_version": a.tool_version,
        "source": a.source, "source_sha256": a.source_sha256,
        "pages": len(pages), "chapters": len(chapters), "bytes": total_bytes,
        "chars": total_chars, "images": len(used), "structure": a.structure,
        "single_file": a.structure == "single",
        "generated_at": now(), "assembler": "pdf_ocr_to_md.py", "verified": False,
    }
    write_manifest(a.out, manifest)
    print("结构 %s，写出 %d 个内容文件，%d 字，%d 图，%d 页输入 -> %s"
          % (a.structure, len(chapters), total_chars, len(used), len(pages), a.out))
    for c in chapters[:6]:
        print("  %s  %d 字" % (c["file"], c["chars"]))


if __name__ == "__main__":
    main()
