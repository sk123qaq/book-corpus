#!/usr/bin/env python3
"""重测 book-corpus-qa/references/corpus-map.md 里写死的那批数字。

每拆一批 md 之后跑一次，把输出抄回该文件的表格。只读，不改任何文件。

用法: python3 corpus_stats.py
"""
import collections
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

WATCH = ("序言", "自序", "前言", "后记", "导读", "结语")
SERIES = ("阿加莎", "东野圭吾", "理想国译丛", "毛泽东选集", "余华", "王小波",
          "盗墓笔记", "鬼吹灯", "吉姆·柯林斯", "杰克·韦尔奇", "金字塔原理", "三体")


def md_dirs():
    for dp, dn, fn in os.walk(L.LIB):
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
        long_books.append((len(mds), os.path.relpath(os.path.dirname(dp), L.LIB),
                           sum(os.path.getsize(os.path.join(dp, f)) for f in mds)))
        rp = os.path.join(dp, "README.md")
        if not os.path.isfile(rp):
            continue
        readmes += 1
        t = open(rp, encoding="utf-8", errors="ignore").read()
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

    MB = 1000000
    print("md 目录            %d" % dirs)
    print("章节 .md 文件      %d" % chapters)
    print("README.md          %d（无 README 的 md 目录 %d 个）" % (readmes, dirs - readmes))
    print("正文               %.1f MB" % (text_bytes / MB))
    print("images/            %.1f MB" % (image_bytes / MB))
    print("合计               %.2f GB" % ((text_bytes + image_bytes) / 1e9))
    print()
    print("有未收录清单的书   %d 本，共 %d 项" % (excluded_books, sum(excluded_names.values())))
    print("按原因前 12        %s" % "、".join("%s %d" % (k, v) for k, v in
                                           excluded_items.most_common(12)))
    print("牵涉 %s 的书 %d 本" % ("/".join(WATCH), watched_books))
    print("按页名前 12        %s" % "、".join("%s %d" % (k, v) for k, v in
                                           excluded_names.most_common(12)))
    print()
    long_books.sort(reverse=True)
    print("按章节数最长的 14 本（章数 / 正文 MB）")
    for n, rel, b in long_books[:14]:
        print("   %-40.40s %5d 章  %6.1f MB" % (rel, n, b / MB))

    con = sqlite3.connect("file:" + os.path.join(L.CAT, "library.db") + "?mode=ro", uri=True)
    print()
    # 口径是 path 含关键词，不是 book.series 字段：path 含作者层目录，所以把 series 为空的书
    # 也算进来了（阿加莎 path 87 / series 85，王小波 path 11 / series 10）。corpus-map.md
    # 记的就是这套 path 数，改按 series 会跟它对不上。
    print("人账口径（有 md 的书里按 path 含关键词数）：")
    for name in SERIES:
        n = con.execute("SELECT count(*) FROM book WHERE path LIKE ? AND uid IN "
                        "(SELECT uid FROM derived WHERE kind='md')", ("%" + name + "%",)).fetchone()[0]
        print("   %-12s %d" % (name, n))
    con.close()

    rg = shutil.which("rg")
    if rg:
        try:
            out = subprocess.run([rg, "-l", "波特", L.LIB], capture_output=True, text=True,
                                 timeout=300)
        except subprocess.TimeoutExpired:
            sys.exit("rg 超过 300 秒没返回")
        print()
        if out.returncode not in (0, 1):
            sys.stderr.write(out.stderr)
            sys.exit("rg 出错，退出码 %d" % out.returncode)
        print("rg -l 波特 命中 %d 个文件（%s）"
              % (len(out.stdout.splitlines()), "计时没复现，只报命中数"))


if __name__ == "__main__":
    main()
