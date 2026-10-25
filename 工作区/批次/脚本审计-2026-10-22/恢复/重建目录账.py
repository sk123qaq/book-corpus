#!/usr/bin/env python3
"""重建 2026-10-22 被 scan_library.py 误清空的 目录/ 过程账。

事故：scan_library.py 在旧池目录已不存在时扫到 0 个源文件，仍无条件写盘，
把 源文件清单/书目录清单/分类预填/重复/路径冲突/损坏 六份 CSV 覆盖成只剩表头。

重建口径（以仍在的权威与产物为准）：
  人账 目录/分类表.csv      一级/二级/正式书名/作者层/保留/备注
  索引 目录/library.db     book/source/derived/book_award
  冷宫 库/_冷宫/唐诗学书系/book.json.归档  B1359 一行

能恢复到什么程度：行数、uid、分类、书名字段、源文件五元组（路径/格式/字节/sha256/主源）
可恢复。L1 当时的 预填依据、额外源 JSON、卷数、路径冲突的 15 条人工结论不可恢复，
相应列写「重建-2026-10-22」或留空。损坏.csv 重建为空表（当前库无损坏文件）。
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/shike/文档/books"
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import lib_common as L

STAMP = "重建-2026-10-22"
COLD = os.path.join(ROOT, "库", "_冷宫", "唐诗学书系", "book.json.归档")
BOOK_HEADER = ["uid", "一级", "二级", "作者", "作者层", "容器", "正式书名", "系列", "卷序",
               "src_path", "额外源", "md_dir", "md_chapters", "md_bytes", "html_book",
               "format", "bytes", "sha256", "备注", "预填依据"]
SRC_HEADER = ["src_path", "file", "format", "bytes", "sha256", "award", "pool", "卷数",
              "title_clean", "author_guess", "损坏原因", "一级预填", "二级预填"]


def main():
    ledger = L.read_csv(os.path.join(L.CAT, "分类表.csv"))
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    con.row_factory = sqlite3.Row
    books = {r["uid"]: dict(r) for r in con.execute("SELECT * FROM book")}
    srcs = {}
    for r in con.execute("SELECT * FROM source ORDER BY primary_src DESC, file"):
        srcs.setdefault(r["uid"], []).append(dict(r))
    derived = {r["uid"]: dict(r) for r in con.execute(
        "SELECT * FROM derived WHERE kind='md'")}
    awards = {}
    for r in con.execute("SELECT uid, award FROM book_award ORDER BY award"):
        awards.setdefault(r["uid"], []).append(r["award"])

    inv, src_rows = [], []
    for led in ledger:
        uid = led["uid"]
        b = books.get(uid) or {}
        ss = srcs.get(uid) or []
        main_src = ss[0] if ss else {}
        md_dir = os.path.join(ROOT, "库", b.get("path") or "", "md")
        has_md = os.path.isdir(md_dir)
        d = derived.get(uid) or {}
        inv.append({
            "uid": uid, "一级": led["一级"], "二级": led["二级"],
            "作者": b.get("author", ""), "作者层": led["作者层"],
            "容器": b.get("container", ""), "正式书名": led["正式书名"],
            "系列": b.get("series", ""), "卷序": b.get("volume", ""),
            "src_path": main_src.get("file", ""),
            "额外源": json.dumps([s["file"] for s in ss[1:]], ensure_ascii=False) if len(ss) > 1 else "",
            "md_dir": ("库/%s/md" % b.get("path")) if has_md else "",
            "md_chapters": d.get("chapters", 0) if has_md else 0,
            "md_bytes": d.get("bytes", 0) if has_md else 0,
            "html_book": str(b.get("html_pages")) if b.get("html_pages") else "",
            "format": main_src.get("format", ""), "bytes": main_src.get("bytes", ""),
            "sha256": main_src.get("sha256", ""), "备注": b.get("notes", ""),
            "预填依据": STAMP,
        })
        for s in ss:
            fn = os.path.basename(s["file"])
            tc = L.clean_title(fn)
            src_rows.append({
                "src_path": s["file"], "file": fn, "format": s.get("format", ""),
                "bytes": s.get("bytes", ""), "sha256": s.get("sha256", ""),
                "award": "；".join(awards.get(uid) or []), "pool": "库", "卷数": "",
                "title_clean": tc, "author_guess": L.author_guess(fn, tc),
                "损坏原因": "", "一级预填": led["一级"], "二级预填": led["二级"],
            })

    if os.path.isfile(COLD):
        c = json.load(open(COLD, encoding="utf-8"))
        subj = (c.get("subjects") or []) + ["", ""]
        src = (c.get("sources") or [{}])[0]
        inv.append({
            "uid": c["uid"], "一级": subj[0], "二级": subj[1], "作者": c.get("author", ""),
            "作者层": "1", "容器": c.get("container", ""), "正式书名": c["title"],
            "系列": c.get("series", ""), "卷序": c.get("volume", ""),
            "src_path": "库/%s/%s" % (c["path"], src.get("file", "")),
            "额外源": "", "md_dir": "", "md_chapters": 0, "md_bytes": 0, "html_book": "",
            "format": src.get("format", ""), "bytes": src.get("bytes", ""),
            "sha256": src.get("sha256", ""), "备注": c.get("notes", ""), "预填依据": STAMP,
        })

    dup = {}
    for r in src_rows:
        if r["sha256"]:
            dup.setdefault(r["sha256"], []).append(r["src_path"])
    dup_rows = [{"sha256": k, "文件数": len(v), "src_path": " | ".join(v)}
                for k, v in dup.items() if len(v) > 1]

    L.write_csv(os.path.join(L.CAT, "书目录清单.csv"), inv, BOOK_HEADER)
    L.write_csv(os.path.join(L.CAT, "源文件清单.csv"), src_rows, SRC_HEADER)
    L.write_csv(os.path.join(L.CAT, "分类预填.csv"),
                [{"uid": r["uid"], "一级": r["一级"], "二级": r["二级"], "作者层": r["作者层"],
                  "保留": next(x["保留"] for x in ledger if x["uid"] == r["uid"]),
                  "正式书名": r["正式书名"], "备注": r["备注"], "预填依据": STAMP}
                 for r in inv if r["uid"] != "B1359"],
                L.LEDGER_HEADER + ["预填依据"])
    L.write_csv(os.path.join(L.CAT, "重复.csv"), dup_rows, ["sha256", "文件数", "src_path"])
    L.write_csv(os.path.join(L.CAT, "损坏.csv"), [], ["src_path", "format", "bytes", "原因"])
    L.write_csv(os.path.join(L.CAT, "路径冲突.csv"), [],
                ["提议路径", "uid", "src_path", "md_dir", "sha256前16", "处置"])
    print("书目录清单 %d 行，源文件清单 %d 行，重复 %d 组，路径冲突 0 行（结论已丢失）"
          % (len(inv), len(src_rows), len(dup_rows)))


if __name__ == "__main__":
    main()
