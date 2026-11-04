#!/usr/bin/env python3
"""现测书库规模与结构：书目数、md 数、章节数、体积、未收录项、长书与系列分布。只读。

每拆一批 md 之后跑一次，用输出核对 `book-corpus-qa` 的结构判断。

用法: python3 corpus_stats.py
"""

import collections
import os
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L

WATCH = ("序言", "自序", "前言", "后记", "导读", "结语")


def md_dirs():
    for dp, _dn, fn in os.walk(L.BOOKS):
        if os.path.basename(dp) == "md":
            yield dp, fn


def main():
    dirs = chapters = readmes = 0
    text_bytes = image_bytes = 0
    excluded_books = 0
    excluded_items = collections.Counter()
    excluded_names = collections.Counter()
    watched_books = 0
    long_books = []
    for dp, fn in md_dirs():
        dirs += 1
        mds = [f for f in fn if f.endswith(".md") and f != "README.md"]
        chapters += len(mds)
        text_bytes += sum(os.path.getsize(os.path.join(dp, f)) for f in mds)
        img = os.path.join(dp, "images")
        if os.path.isdir(img):
            for f in os.listdir(img):
                p = os.path.join(img, f)
                if os.path.isfile(p):
                    image_bytes += os.path.getsize(p)
        long_books.append(
            (
                len(mds),
                os.path.relpath(os.path.dirname(dp), L.BOOKS),
                sum(os.path.getsize(os.path.join(dp, f)) for f in mds),
            )
        )
        rp = os.path.join(dp, "README.md")
        if not os.path.isfile(rp):
            continue
        readmes += 1
        t = pathlib.Path(rp).read_text(encoding="utf-8", errors="ignore")
        sec = t.split("## 未收录")[-1] if "## 未收录" in t else ""
        items = [ln.strip()[2:] for ln in sec.splitlines() if ln.strip().startswith("- ")]
        if items:
            excluded_books += 1
        if any(w in " ".join(items) for w in WATCH):
            watched_books += 1
        for it in items:
            parts = re.split(r"\s+[—–-]\s+", it, maxsplit=1)
            name = parts[0].strip()
            excluded_names[name] += 1
            excluded_items[(parts[1] if len(parts) > 1 else name).strip()] += 1

    mb = 1000000
    print("md 目录            %d" % dirs)
    print("章节 .md 文件      %d" % chapters)
    print("README.md          %d（无 README 的 md 目录 %d 个）" % (readmes, dirs - readmes))
    print("正文               %.1f MB" % (text_bytes / mb))
    print("images/            %.1f MB" % (image_bytes / mb))
    print("合计               %.2f GB" % ((text_bytes + image_bytes) / 1e9))
    print()
    print("有未收录清单的书   %d 本，共 %d 项" % (excluded_books, sum(excluded_names.values())))
    print("按原因前 12        %s" % "、".join("%s %d" % (k, v) for k, v in excluded_items.most_common(12)))
    print("牵涉 %s 的书 %d 本" % ("/".join(WATCH), watched_books))
    print("按页名前 12        %s" % "、".join("%s %d" % (k, v) for k, v in excluded_names.most_common(12)))
    print()
    long_books.sort(reverse=True)
    print("按章节数最长的 14 本（章数 / 正文 MB）")
    for n, rel, b in long_books[:14]:
        print("   %-40.40s %5d 章  %6.1f MB" % (rel, n, b / mb))

    con = sqlite3.connect("file:" + L.PATHS.library_db + "?mode=ro", uri=True)
    books = con.execute("SELECT count(*) FROM book").fetchone()[0]
    rows = con.execute(
        "SELECT series, count(*) FROM book "
        "WHERE series IS NOT NULL AND series != '' "
        "AND uid IN (SELECT uid FROM derived WHERE kind='md') "
        "GROUP BY series ORDER BY count(*) DESC LIMIT 12"
    ).fetchall()
    con.close()
    print()
    print("人账               %d 本" % books)
    if rows:
        print("系列分布（有 md 的书，前 12）")
        for name, n in rows:
            print("   %-20.20s %d" % (name, n))
    else:
        print("系列分布           无 series 字段")


if __name__ == "__main__":
    main()
