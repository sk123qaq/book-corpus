#!/usr/bin/env python3
"""旧 md 重拆批次的范围账本。

范围是库里已存在、但不是拆章批次（md拆分-2026-10-18）产物的 md 目录。按主源文件分组：
单 owner 的书写入 worklist.tsv（split_md_batch.py 的输入格式），多本书共用一个
源文件的合集写入 collections.tsv（走 split_collection.py，不用驱动）。

只读 库/ 与 DB，只写批次目录。默认只打印，--apply 才落盘。

用法: python3 redo_md_scope.py [--batch 旧md重拆-2026-10-18] [--apply]
"""
import argparse
import collections
import json
import os
import sqlite3
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

FORMATS = ("epub", "mobi", "azw3", "azw")
COLS = ["uid", "format", "sha256", "path", "title", "src"]


def batch_dests(batch_name):
    base = os.path.join(L.WORK, "批次", batch_name, "logs")
    dests = set()
    if not os.path.isdir(base):
        return dests
    for name in os.listdir(base):
        if name.endswith(".json"):
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                rec = json.load(fh)
            dests.add(os.path.join(L.ROOT, rec["dest"]))
    return dests


def old_md_dirs():
    produced = batch_dests("md拆分-2026-10-18")
    out = []
    for root, subdirs, _files in os.walk(os.path.join(L.ROOT, "库")):
        if os.path.relpath(root, os.path.join(L.ROOT, "库")).split(os.sep)[0].startswith("_"):
            continue
        if "md" in subdirs:
            d = os.path.join(root, "md")
            if d not in produced:
                out.append(d)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", default="旧md重拆-2026-10-18")
    ap.add_argument("--apply", action="store_true", help="写 worklist.tsv 与 collections.tsv")
    a = ap.parse_args()
    batch_path = os.path.join(L.WORK, "批次", a.batch)

    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    con.row_factory = sqlite3.Row
    groups = collections.defaultdict(list)
    for d in old_md_dirs():
        rel = os.path.dirname(os.path.relpath(d, os.path.join(L.ROOT, "库")))
        r = con.execute(
            """SELECT b.uid AS uid, b.path AS path, b.title AS title,
                      s.format AS format, s.sha256 AS sha256, s.file AS src
               FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
               WHERE b.path=?""", (rel,)).fetchone()
        if r is None:
            sys.exit("旧 md 目录在 DB 里解析不到书: %s" % rel)
        groups[r["src"]].append(dict(r))

    singles, colls = [], []
    for src, members in groups.items():
        (singles if len(members) == 1 else colls).append((src, members))
    singles.sort(key=lambda x: x[1][0]["uid"])
    colls.sort(key=lambda x: -len(x[1]))
    fmt = collections.Counter(m["format"] for _src, mem in singles for m in mem)
    can = [m for _src, mem in singles for m in mem if m["format"] in FORMATS]
    off = [m for _src, mem in singles for m in mem if m["format"] not in FORMATS]
    n_dirs = sum(len(members) for members in groups.values())
    print("旧 md 目录 %d 个：单 owner %d 本（驱动 %d，非驱动 %d），合集 %d 个源（%d 本）"
          % (n_dirs, len(singles), len(can), len(off), len(colls),
             sum(len(m) for _s, m in colls)))
    print("单 owner 格式：%s；非驱动源：%s"
          % (dict(fmt), [(m["uid"], m["format"]) for m in off]))
    print("合集源：")
    for src, members in colls:
        print("  %2d 本 %-6s %s" % (len(members), members[0]["format"],
                                     os.path.basename(src)[:64]))

    if a.apply:
        os.makedirs(batch_path, exist_ok=True)
        wl = os.path.join(batch_path, "worklist.tsv")
        if os.path.isfile(wl):
            os.replace(wl, wl + ".prev-" + time.strftime("%Y%m%dT%H%M%S"))
        with open(wl + ".tmp", "w", encoding="utf-8") as fh:
            fh.write("\t".join(COLS) + "\n")
            for m in can:
                fh.write("\t".join(str(m[c]) for c in COLS) + "\n")
        os.replace(wl + ".tmp", wl)
        cl = os.path.join(batch_path, "collections.tsv")
        if os.path.isfile(cl):
            os.replace(cl, cl + ".prev-" + time.strftime("%Y%m%dT%H%M%S"))
        with open(cl + ".tmp", "w", encoding="utf-8") as fh:
            fh.write("uid\tformat\tsrc\ttitle\tpath\tdir\n")
            for src, members in colls:
                for m in members:
                    d = os.path.join(L.ROOT, "库", m["path"], "md")
                    fh.write("\t".join([m["uid"], m["format"], src, m["title"],
                                        m["path"], os.path.relpath(d, L.ROOT)]) + "\n")
        os.replace(cl + ".tmp", cl)
        print("已写 %s（%d 行 + 表头）与 %s（%d 行 + 表头）"
              % (os.path.relpath(wl, L.ROOT), len(can),
                 os.path.relpath(cl, L.ROOT), sum(len(m) for _s, m in colls)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
