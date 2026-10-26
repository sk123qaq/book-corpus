#!/usr/bin/env python3
"""给缺 README 的 md 目录补目录页。只写 README.md，不动正文与 .manifest.json。

  backfill_readme.py            # 列出缺 README 的 md 目录，并打印将写入的内容
  backfill_readme.py --apply    # 写盘；已有 README 的目录不碰

格式与 pdf_ocr_to_md.write_readme 一致：标题、合集、作者、章节、图片、来源、目录、
未收录。标题、作者、合集与来源取 book.json；来源只写主源文件名。图片数按正文引用的
本地图片去重计数。只有一章时目录条目用书名，多章时用章内首个 H1，没有 H1 用文件名。
作者字段是文件名（以 .pdf/.epub 等结尾）时不写，免得把来源当成作者印进目录页。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORPUS = os.path.join(ROOT, "库")
IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)")
FILE_AUTHOR = re.compile(r"\.(pdf|epub|mobi|azw3?|txt|djvu)$", re.I)
UNLISTED_SCAN = "- 无（扫描书按页全收）"
UNLISTED_TEXT = "- 无（文本层按页全收）"


def link_dest(name):
    """标出文件名里会截断 markdown 目标的字符（括号、空格）。"""
    return "<%s>" % name if any(c in name for c in "() ") else name


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def md_dirs():
    """产出 (uid, book 相对路径, md 目录, book.json)。"""
    for dirpath, dirnames, _ in os.walk(CORPUS):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if os.path.basename(dirpath) != "md":
            continue
        book_dir = os.path.dirname(dirpath)
        meta = read_json(os.path.join(book_dir, "book.json"))
        if not meta.get("uid"):
            continue
        yield meta["uid"], os.path.relpath(book_dir, CORPUS), dirpath, meta


def h1(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return ""


def images(paths):
    seen = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in IMG_RE.finditer(text):
            target = (m.group(1) or m.group(2) or "").strip()
            if not target or target.startswith(("http://", "https://", "data:")):
                continue
            seen.add(target)
    return len(seen)


def method_of(md_dir, meta):
    man = read_json(os.path.join(md_dir, ".manifest.json"))
    if man.get("method"):
        return str(man["method"])
    for d in meta.get("derived") or []:
        if d.get("kind") == "md" and d.get("method"):
            return str(d["method"])
    return ""


def source_name(meta):
    for src in meta.get("sources") or []:
        if src.get("primary_src"):
            return os.path.basename(src.get("file") or "")
    return ""


def author_line(meta, notes):
    author = (meta.get("author") or "").strip()
    if not author:
        return None
    if FILE_AUTHOR.search(author):
        notes.append("作者字段像文件名，README 不写：%s" % author)
        return None
    return "- 作者：%s" % author


def readme_text(uid, book, md_dir, meta):
    notes = []
    chapters = sorted(f for f in os.listdir(md_dir)
                      if f.endswith(".md") and f != "README.md"
                      and os.path.isfile(os.path.join(md_dir, f)))
    title = (meta.get("title") or "").strip() or book
    if len(chapters) == 1:
        toc = [(title, chapters[0])]
    else:
        toc = []
        for f in chapters:
            path = os.path.join(md_dir, f)
            toc.append((h1(path) or os.path.splitext(f)[0], f))
    lines = ["# %s" % title, ""]
    collection = (meta.get("series") or meta.get("container") or "").strip()
    if collection:
        lines.append("- 合集：%s" % collection)
    author = author_line(meta, notes)
    if author:
        lines.append(author)
    lines.append("- 章节：%d" % len(chapters))
    lines.append("- 图片：%d" % images(os.path.join(md_dir, f) for f in chapters))
    lines.append("- 来源：`%s`" % (source_name(meta) or "未知"))
    lines += ["", "## 目录", ""]
    for label, fname in toc:
        lines.append("- [%s](%s)" % (label, link_dest(fname)))
    method = method_of(md_dir, meta).lower()
    scan = any(k in method for k in ("ocr", "mineru", "paddle"))
    lines += ["", "## 未收录（卷首/卷尾页）", "",
              UNLISTED_SCAN if scan else UNLISTED_TEXT, ""]
    return "\n".join(lines), notes


def write_readme(md_dir, text):
    path = os.path.join(md_dir, "README.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写盘；默认只打印")
    a = ap.parse_args()
    missing = 0
    for uid, book, md_dir, meta in md_dirs():
        if os.path.isfile(os.path.join(md_dir, "README.md")):
            continue
        missing += 1
        text, notes = readme_text(uid, book, md_dir, meta)
        print("== %s  库/%s/md" % (uid, book))
        for note in notes:
            print("   注意  " + note)
        print(text if a.apply else text + "\n")
        if a.apply:
            write_readme(md_dir, text)
            print("   写入 README.md")
    print("缺 README 的 md %d 本%s" % (missing, "，已补写" if a.apply else "（--apply 才写盘）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
