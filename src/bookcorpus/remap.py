"""按迁移对照表改写索引里的路径。expand 是模型生成过的扩展文本，重建索引会把它当失效清掉。

v1 → v2 时 expand.path 从 `库/<旧相对路径>/md/<文件>` 变成 `books/<uid>/md/<文件>`。
这里只换前缀，不解释内容。--old-db 把旧索引的 expand 行灌进新索引；不给就原地改。

默认只打印。--apply 才写，写完重建 expand_fts。
"""

import csv
import os
import sqlite3
import sys

COLS = ("rowid", "path", "kind", "tok", "text", "model", "prompt_hash",
        "tokens_in", "tokens_out", "built_at")
BATCH = 4000


def load_map(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("类别") == "书":
                out[row["旧路径"].strip("/")] = row["新路径"].strip("/")
    return out


def remap_path(path, mapping):
    if path.startswith(("books/", "collections/")):
        return path
    parts = path.split("/")
    for cut in range(len(parts) - 1, 0, -1):
        head = "/".join(parts[:cut])
        if head in mapping:
            return mapping[head] + "/" + "/".join(parts[cut:])
    return None


def _expand_count(db):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return con.execute("select count(*) from expand").fetchone()[0]
    finally:
        con.close()


def _existing_paths(db):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return {r[0] for r in con.execute("select path from expand")}
    finally:
        con.close()


def _flush(db, rows):
    con = sqlite3.connect(db)
    try:
        con.executemany("INSERT INTO expand(%s) VALUES(%s)"
                        % (", ".join(COLS), ", ".join("?" * len(COLS))), rows)
        con.commit()
    finally:
        con.close()


def _rebuild_fts(db):
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO expand_fts(expand_fts) VALUES('rebuild')")
        con.commit()
    finally:
        con.close()


def run(db, map_path, old_db=None, apply_it=False):
    """返回 (退出码, 报告行)。退出码 2 表示有路径换不了，一字节没写。"""
    db = os.path.abspath(os.path.expanduser(db))
    mapping = load_map(map_path)
    lines = ["对照表 %d 条书路径映射" % len(mapping)]
    if old_db:
        old_db = os.path.abspath(os.path.expanduser(old_db))
        con = sqlite3.connect("file:%s?mode=ro" % old_db, uri=True)
        try:
            total = con.execute("select count(*) from expand").fetchone()[0]
            rows = con.execute("select %s from expand" % ", ".join(COLS)).fetchall()
        finally:
            con.close()
        have = _existing_paths(db) if os.path.exists(db) else set()
        out, missed, dup = [], 0, 0
        for row in rows:
            new_path = remap_path(row[1], mapping)
            if new_path is None:
                missed += 1
                continue
            if new_path in have:
                dup += 1
                continue
            have.add(new_path)
            out.append((row[0], new_path) + tuple(row[2:]))
        lines.append("旧 expand %d 行，可换前缀 %d 行，已在目标 %d 行，换不了 %d 行"
                     % (total, len(out), dup, missed))
    else:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            rows = con.execute("select rowid, path from expand").fetchall()
        finally:
            con.close()
        out, missed = [], 0
        for rowid, path in rows:
            new_path = remap_path(path, mapping)
            if new_path is None:
                missed += 1
            elif new_path != path:
                out.append((new_path, rowid))
        lines.append("expand %d 行，需改写 %d 行，换不了 %d 行" % (len(rows), len(out), missed))

    lines.append("方式：%s" % ("落盘" if apply_it else "只打印不落盘"))
    if not apply_it:
        return (0 if missed == 0 else 2), lines
    if old_db:
        for i in range(0, len(out), BATCH):
            _flush(db, out[i:i + BATCH])
        _rebuild_fts(db)
        lines.append("新 expand 现在 %d 行" % _expand_count(db))
    elif out:
        con = sqlite3.connect(db)
        try:
            con.executemany("update expand set path=? where rowid=?", out)
            con.commit()
        finally:
            con.close()
        _rebuild_fts(db)
        lines.append("改写 %d 行，expand 现在 %d 行" % (len(out), _expand_count(db)))
    else:
        lines.append("没有要改的行")
    return (0 if missed == 0 else 2), lines
