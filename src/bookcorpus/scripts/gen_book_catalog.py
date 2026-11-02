#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 目录/library.db 出书目总表，并核对每条记录能落到磁盘。

L4 之前这个脚本扫 原书/ 与 原书pdf/ 做 200 行模糊匹配；L3 之后书目录、源文件、
md、html 都收在 库/**，书与产物的对应关系由 book.json 与 DB 记死，匹配不再需要。

用法: python3 工作区/脚本/gen_book_catalog.py
输出: 目录/书目总表.xlsx、目录/书目总表.csv
门槛: 末行必须是「未匹配 0 条」，否则以非零码退出。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

DB = L.PATHS.library_db
XLSX = L.PATHS.catalog_xlsx
CSV = L.PATHS.catalog

COLS = ["uid", "书名", "作者", "一级", "二级", "系列", "卷序", "容器", "保留",
        "源格式", "源文件", "md状态", "md章节", "md字节", "md方法", "html页数",
        "奖项", "路径", "备注"]

BOOK_SQL = """
SELECT b.uid, b.title, b.author, b.subject1, b.subject2, b.series, b.volume,
       b.container, b.retention, b.path, b.html_pages, b.notes,
       (SELECT group_concat(DISTINCT s.format) FROM source s WHERE s.uid = b.uid),
       -- 主源排在首列：ORDER BY 必须放进子查询，聚合层的 ORDER BY 不保证顺序
       (SELECT group_concat(f, '|') FROM (SELECT s.file AS f FROM source s
                                           WHERE s.uid = b.uid
                                           ORDER BY s.primary_src DESC, s.file)),
       (SELECT d.status FROM derived d WHERE d.uid = b.uid AND d.kind = 'md'),
       (SELECT d.chapters FROM derived d WHERE d.uid = b.uid AND d.kind = 'md'),
       (SELECT d.bytes FROM derived d WHERE d.uid = b.uid AND d.kind = 'md'),
       (SELECT d.method FROM derived d WHERE d.uid = b.uid AND d.kind = 'md'),
       (SELECT group_concat(ba.award, '；') FROM book_award ba WHERE ba.uid = b.uid)
FROM book b
ORDER BY b.subject1, b.subject2, b.title
"""


def query_rows(con):
    out = []
    for (uid, title, author, s1, s2, series, volume, container, retention, path,
         html_pages, notes, formats, files, md_status, md_chapters, md_bytes, md_method,
         awards) in con.execute(BOOK_SQL):
        srcs = [f for f in (files or "").split("|") if f]
        primary = srcs[0] if srcs else ""
        for prefix in ("books/", "collections/"):
            if primary.startswith(prefix):
                primary = primary[len(prefix):]
                break
        out.append({
            "uid": uid, "书名": title or "", "作者": author or "",
            "一级": s1 or "", "二级": s2 or "", "系列": series or "",
            "卷序": volume or "", "容器": container or "", "保留": retention or "",
            "源格式": formats or "", "源文件": primary,
            "md状态": md_status or "无", "md章节": md_chapters if md_chapters is not None else "",
            "md字节": md_bytes if md_bytes is not None else "",
            "md方法": md_method or "", "html页数": html_pages or 0,
            "奖项": awards or "", "路径": path or "", "备注": notes or "",
        })
    return out


def audit(con):
    """返回 (problems, counts)。problems 为空即「未匹配 0 条」。"""
    problems = []
    db_paths = {}
    for uid, path in con.execute("SELECT uid,path FROM book"):
        db_paths[uid] = path
        if not os.path.isdir(os.path.join(L.BOOKS, path)):
            problems.append("书目目录不在: %s %s" % (uid, path))
    db_src = set()
    for (f,) in con.execute("SELECT DISTINCT file FROM source"):
        p = os.path.normpath(os.path.join(L.ROOT, f))
        db_src.add(p)
        if not os.path.isfile(p):
            problems.append("源文件不在: " + f)
    kinds = {}
    for uid, kind in con.execute("SELECT uid,kind FROM derived"):
        kinds.setdefault(uid, set()).add(kind)

    n_disk_src = 0
    for dirpath, dirnames, filenames in os.walk(L.BOOKS):
        # 下划线目录不是账上的书（_收件箱 是新书入口，_冷宫 是移出流程的书）
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if os.path.basename(dirpath) == "source":
            for fn in filenames:
                n_disk_src += 1
                p = os.path.normpath(os.path.join(dirpath, fn))
                if p not in db_src:
                    problems.append("盘上源文件不在账: " + os.path.relpath(p, L.ROOT))

    books = L.iter_book_json()
    seen_paths = set()
    all_db_paths = set(db_paths.values())
    n_md = n_html = 0
    for rel, doc in books:
        seen_paths.add(rel)
        if rel not in all_db_paths:
            problems.append("book.json 不在 DB: " + rel)
        if doc.get("path") and doc["path"] != rel:
            problems.append("book.json 的 path 与目录不符: %s vs %s" % (doc["path"], rel))
        uid = doc.get("uid") or ""
        has = kinds.get(uid, set())
        md_dir = os.path.join(L.BOOKS, rel, "md")
        html_dir = os.path.join(L.BOOKS, rel, "html")
        if os.path.isdir(md_dir):
            n_md += 1
            if "md" not in has:
                problems.append("md/ 在盘上但 DB 没记: " + rel)
        elif "md" in has:
            problems.append("DB 记了 md 但目录不在: " + rel)
        if os.path.isdir(html_dir):
            n_html += 1
            if "html" not in has:
                problems.append("html/ 在盘上但 DB 没记: " + rel)
        elif "html" in has:
            problems.append("DB 记了 html 但目录不在: " + rel)
    for uid, path in db_paths.items():
        if path not in seen_paths:
            problems.append("DB 里的书没有 book.json: %s %s" % (uid, path))

    counts = {
        "books": con.execute("SELECT count(*) FROM book").fetchone()[0],
        "src_rows": con.execute("SELECT count(*) FROM source").fetchone()[0],
        "src_files": len(db_src),
        "disk_src_files": n_disk_src,
        "md": n_md,
        "html": n_html,
        "awards": con.execute("SELECT count(*) FROM book_award").fetchone()[0],
        "award_names": con.execute("SELECT count(*) FROM award").fetchone()[0],
    }
    return problems, counts


def write_xlsx(rows, counts):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="305496")
    md_fill = PatternFill("solid", fgColor="E2EFDA")
    no_fill = PatternFill("solid", fgColor="FCE4E4")

    ws = wb.active
    ws.title = "书目总表"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append([r[c] for c in COLS])
        row = ws.max_row
        ws.cell(row=row, column=COLS.index("md状态") + 1).fill = (
            md_fill if r["md状态"] == "done" else no_fill)
    for i, w in enumerate([8, 34, 18, 10, 12, 18, 6, 12, 7, 12, 46, 9, 8, 10, 16, 9, 24, 40, 30],
                          start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(COLS)), ws.max_row)

    ws2 = wb.create_sheet("统计")
    ws2.append(["项目", "数量"])
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
    for k, v in (("书名", counts["books"]), ("源文件行", counts["src_rows"]),
                 ("源文件（去重）", counts["src_files"]),
                 ("盘上 source/ 文件", counts["disk_src_files"]),
                 ("md 目录", counts["md"]), ("html 目录", counts["html"]),
                 ("奖项记录", counts["awards"]), ("奖项名", counts["award_names"])):
        ws2.append([k, v])
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 12
    with open(XLSX + ".tmp", "wb") as fh:
        wb.save(fh)
    os.replace(XLSX + ".tmp", XLSX)


def main():
    if not os.path.isfile(DB):
        raise SystemExit("没有 %s，先跑 build_library.py" % DB)
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    rows = query_rows(con)
    problems, counts = audit(con)
    con.close()

    if problems:
        for p in problems[:20]:
            print("  [未匹配] " + p)
        if len(problems) > 20:
            print("  …另有 %d 条" % (len(problems) - 20))
        print("未匹配 %d 条（未覆盖书目总表）" % len(problems))
        sys.exit(1)
    L.write_csv(CSV, rows, COLS)
    write_xlsx(rows, counts)
    for p in (XLSX, CSV):
        print("已生成: %s" % p)
    print("书名 %d | 源文件 %d 行 / %d 个文件（盘上 %d）| md %d 目录 | html %d 目录 | "
          "奖项记录 %d（%d 个奖项）"
          % (counts["books"], counts["src_rows"], counts["src_files"], counts["disk_src_files"],
             counts["md"], counts["html"], counts["awards"], counts["award_names"]))
    for p in problems[:20]:
        print("  [未匹配] " + p)
    if len(problems) > 20:
        print("  …另有 %d 条" % (len(problems) - 20))
    print("未匹配 %d 条" % len(problems))
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
