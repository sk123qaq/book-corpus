#!/usr/bin/env python3
"""重建 build/views/：主题按概念、奖项按奖项名，各建一棵指向 books/<uid> 的软链树。

  build_views.py [--limit 10] [--only topics|awards]

主题排序直接用 qsearch 的书级聚合，跟用户查到的顺序一致。奖项从 library.db 的
book_award 取。软链树是派生物，整目录删掉能重建。
"""

import argparse
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L
import lib_fts_cjk as cjk
import qsearch

PATHS = L.PATHS


def _connect_ro(path):
    """只读打开。库不在时把路径写进异常，否则只剩一句 unable to open database file。"""
    try:
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError("打不开 %s: %s" % (path, e)) from e


def title_map():
    con = _connect_ro(PATHS.library_db)
    try:
        return dict(con.execute("SELECT uid, title FROM book"))
    finally:
        con.close()


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _reset_dir(path):
    _remove(path)
    os.makedirs(path, exist_ok=True)


def _promote(building, out):
    """新树建完后才动旧树：旧 out 先挪成 .old 留退路，rename 完再删。

    building 与 out 同层同深度，里面的相对软链在 rename 后仍然 resolve。
    """
    backup = out + ".old"
    if os.path.lexists(out):
        _remove(backup)
        os.rename(out, backup)
    elif os.path.lexists(backup):
        # 上次换树在 rename 后、replace 前崩了：out 不在，backup 是唯一的旧树。
        # 先放回 out，再照常挪成 backup，失败时还能换回来，不能先删。
        os.rename(backup, out)
        os.rename(out, backup)
    try:
        os.replace(building, out)
    except BaseException:
        # rename 失败（跨设备、权限等）就把旧树换回去，别只剩个 .old
        if os.path.lexists(backup):
            os.rename(backup, out)
        raise
    _remove(backup)


def link_name(uid, title, used):
    name = L.path_safe(title) or uid
    if name in used:
        name = "%s（%s）" % (name, uid)
    used.add(name)
    return name


def build_topics(limit):
    final = os.path.join(PATHS.views, "主题")
    # 先在新目录里完整生成，输入缺失或中途异常都不碰旧视图
    out = final + ".building"
    _reset_dir(out)
    con = None
    total = 0
    try:
        con = _connect_ro(PATHS.index_db)
        titles = title_map()
        keys = [r[0] for r in con.execute("SELECT key FROM book_concept GROUP BY key ORDER BY key")]
        index = [
            "# 主题视图",
            "",
            "指向 `books/<uid>/` 的软链，随时可删可重建。排序与 `book-corpus search --concept <键> --book` 一致。",
            "",
        ]
        for key in keys:
            groups, exclude = qsearch.load_concept(key)
            expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
            hits, _ = qsearch.search(con, expr, groups, exclude, 400, 40, pool=2000)
            books = qsearch.rollup_by_book(hits, con, limit)
            if not books:
                continue
            d = os.path.join(out, key)
            os.makedirs(d)
            used = set()
            lines = [
                "# %s" % key,
                "",
                "按书级聚合分数排序，最多 %d 本。软链指向书目录；引用的段落回原文核。" % limit,
                "",
            ]
            for b in books:
                uid = b["book"]
                name = link_name(uid, titles.get(uid, ""), used)
                target = os.path.relpath(PATHS.book(uid), d)
                os.symlink(target, os.path.join(d, name))
                total += 1
                best = b["best"]
                lines.append(
                    "- [[%s]] `%.2f` %d 段  %s:%d" % (name, b["score"], b["paras"], best["path"], best["line"])
                )
                lines.append("  " + " ".join(best["text"].split())[:140])
            lines += ["", "共 %d 本。" % len(books), ""]
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            index.append("- [%s](%s/) %d 本" % (key, key, len(books)))
        with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(index) + "\n")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        if con is not None:
            con.close()
    _promote(out, final)
    print("主题 %d 个 / 软链 %d 条" % (len(os.listdir(final)) - 1, total))


def build_awards():
    final = os.path.join(PATHS.views, "奖项")
    out = final + ".building"
    _reset_dir(out)
    con = None
    total = 0
    try:
        con = _connect_ro(PATHS.library_db)
        rows = list(
            con.execute(
                "SELECT ba.award, ba.uid, b.title, coalesce(ba.year, ''), coalesce(ba.result, '') "
                "FROM book_award ba JOIN book b ON b.uid = ba.uid "
                "ORDER BY ba.award, b.title"
            )
        )
        con.close()
        con = None
        groups = {}
        for award, uid, title, year, result in rows:
            groups.setdefault(award, []).append((uid, title, year, result))
        index = ["# 奖项视图", "", "指向 `books/<uid>/` 的软链，随时可删可重建。", ""]
        for award in sorted(groups):
            books = groups[award]
            d = os.path.join(out, L.path_safe(award) or award)
            os.makedirs(d, exist_ok=True)
            used = set()
            lines = ["# %s" % award, "", "共 %d 本。" % len(books), ""]
            for uid, title, year, result in books:
                name = link_name(uid, title, used)
                os.symlink(os.path.relpath(PATHS.book(uid), d), os.path.join(d, name))
                total += 1
                note = " ".join(x for x in (year, result) if x)
                lines.append("- [[%s]]%s" % (name, (" " + note) if note else ""))
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            index.append("- [%s](%s/) %d 本" % (award, os.path.basename(d), len(books)))
        with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(index) + "\n")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        if con is not None:
            con.close()
    _promote(out, final)
    print("奖项 %d 个 / 软链 %d 条" % (len(groups), total))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--only", choices=("topics", "awards"))
    a = ap.parse_args()
    os.makedirs(PATHS.views, exist_ok=True)
    if a.only in (None, "topics"):
        build_topics(a.limit)
    if a.only in (None, "awards"):
        build_awards()
    return 0


if __name__ == "__main__":
    sys.exit(main())
