#!/usr/bin/env python3
"""重建 build/views/奖项/ 软链树。

  build_views.py

奖项从 library.db 的 book_award 取。软链树是派生物，整目录删掉能重建。
"""

import argparse
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L

PATHS = L.PATHS


def _connect_ro(path):
    """只读打开。库不在时把路径写进异常，否则只剩一句 unable to open database file。"""
    try:
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError("打不开 %s: %s" % (path, e)) from e


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
    argparse.ArgumentParser().parse_args()
    os.makedirs(PATHS.views, exist_ok=True)
    # 清理已退役的旧主题软链视图及其暂存目录
    for legacy in ("主题", "主题.building", "主题.old"):
        _remove(os.path.join(PATHS.views, legacy))
    build_awards()
    return 0


if __name__ == "__main__":
    sys.exit(main())
