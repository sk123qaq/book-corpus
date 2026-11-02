"""v1 → v2 布局迁移：库/<一级>/…/<书名>/ → books/<uid>/，合集原书 → collections/<Cuid>/。

v1 的形状是 库/ 下按学科分层的书目录、目录/ 下的表与库、视图/ 下的软链。
v2 的形状见 layout.py。这里只搬与改名，不解释内容，不改写正文。

默认只打印计划。--apply 才落盘，落盘方式和 books/ 的 0001 迁移一样是 move / link / copy。
派生产物（library.db、检索索引、视图）只随行，不重建；迁移完成后跑
book-corpus library、book-corpus index --rebuild 重出。

合集 uid 由这里一次性派发，写进引用它的每本书的 book.json 的 sources[].collection。
"""

import json
import os
import shutil
import sqlite3

from .scripts import lib_common as L
from .scripts import layout as LY

TABLE_MEMBERS = ("分类表.csv", "概念表.tsv", "概念表-broad.tsv", "概念表-动作词.tsv",
                 "书目录清单.csv", "对照表.csv", "搬迁计划.csv", "标注样本.csv",
                 "源文件清单.csv", "系列")
BUILD_MEMBERS = ("library.db", "检索", "阅读版索引.html", "书目总表.csv", "书目总表.xlsx")
MAP_NAME = "对照表-0002.csv"
MAP_COLS = ["序号", "阶段", "uid", "类别", "旧路径", "新路径", "备注"]


class Item:
    def __init__(self, kind, src, dst, uid="", note=""):
        self.kind = kind
        self.src = src
        self.dst = dst
        self.uid = uid
        self.note = note
        self.done = False

    @property
    def label(self):
        return self.uid or os.path.basename(self.src)


def v1_books(root):
    """(uid, 旧相对路径)。先看 library.db，没有就扫 book.json。"""
    db = os.path.join(root, "目录", "library.db")
    if os.path.exists(db):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        rows = con.execute("select uid, path from book order by uid").fetchall()
        con.close()
        if rows:
            return rows
    out = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(root, "库")):
        if "book.json" not in filenames:
            continue
        rel = os.path.relpath(dirpath, os.path.join(root, "库"))
        if rel.split(os.sep)[0].startswith("_"):
            continue
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as f:
                uid = json.load(f).get("uid") or os.path.basename(dirpath)
        except (OSError, json.JSONDecodeError):
            uid = os.path.basename(dirpath)
        out.append((uid, rel))
    return sorted(out)


def share_dirs(root, book_rels):
    """库/ 下不在任何书目录内的文件，按最外层目录归堆。合集原书就住在这里。"""
    lib = os.path.join(root, "库")
    covered = [os.path.join(lib, rel) for rel in book_rels]
    covered += [os.path.join(lib, n) for n in LY.RESERVED]
    found = {}
    for dirpath, dirnames, filenames in os.walk(lib):
        if any(dirpath == c or dirpath.startswith(c + os.sep) for c in covered):
            dirnames[:] = []
            continue
        if filenames:
            found[dirpath] = filenames
    keep = {}
    for dirpath in found:
        if any(other != dirpath and dirpath.startswith(other + os.sep) for other in found):
            continue
        keep[dirpath] = True
    return sorted(keep)


def plan(root, dest):
    books = v1_books(root)
    lib = os.path.join(root, "库")
    rels = [rel for _uid, rel in books]
    items = []
    for uid, rel in books:
        item = Item("书", os.path.join(lib, rel), os.path.join(dest, "books", uid), uid=uid)
        items.append(item)

    shares = share_dirs(root, rels)
    owner = {}
    for n, dirpath in enumerate(shares, 1):
        cuid = "C%04d" % n
        item = Item("合集", dirpath, os.path.join(dest, "collections", cuid, "source"), uid=cuid,
                    note=os.path.relpath(dirpath, lib))
        items.append(item)
        owner[dirpath] = cuid

    for name in LY.RESERVED:
        src = os.path.join(lib, name)
        if os.path.isdir(src):
            items.append(Item("保留", src, os.path.join(dest, "books", name), uid=name))

    for name in TABLE_MEMBERS:
        src = os.path.join(root, "目录", name)
        if os.path.exists(src):
            items.append(Item("表", src, os.path.join(dest, "tables", name)))
    for name in BUILD_MEMBERS:
        src = os.path.join(root, "目录", name)
        if os.path.exists(src):
            items.append(Item("产物", src, os.path.join(dest, "build", name)))
    views = os.path.join(root, "视图")
    if os.path.isdir(views):
        items.append(Item("视图", views, os.path.join(dest, "build", "views")))

    problems = []
    for item in items:
        if os.path.isdir(item.dst) or os.path.exists(item.dst):
            item.done = True
    seen = {}
    for item in items:
        if item.dst in seen:
            problems.append("目标撞车：%s 与 %s 都指向 %s" % (seen[item.dst], item.label, item.dst))
        seen[item.dst] = item.label
    return items, owner, problems


def resolve_share(root, book_path, file_field, owner):
    """book.json 里 ../ 指向的源文件落在哪个合集。落在不认识的目录就返回 None。"""
    lib = os.path.join(root, "库")
    target = os.path.normpath(os.path.join(lib, book_path, file_field))
    for dirpath, cuid in owner.items():
        if target == dirpath or target.startswith(dirpath + os.sep):
            return cuid, os.path.basename(target)
    return None, None


def rewrite_book_json(path, root, book_path, owner):
    """把 ../ 的 sources 改成 collection + 文件名，删掉 path。返回改了几条。"""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    changed = 0
    for src in doc.get("sources") or []:
        field = str(src.get("file", ""))
        if not field.startswith(".."):
            continue
        cuid, name = resolve_share(root, book_path, field, owner)
        if not cuid:
            continue
        src["collection"] = cuid
        src["file"] = name
        changed += 1
    doc.pop("path", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return changed


def apply(items, mode):
    failures = []
    for item in items:
        if item.done:
            continue
        os.makedirs(os.path.dirname(item.dst), exist_ok=True)
        if mode == "move":
            shutil.move(item.src, item.dst)
        elif mode == "copy":
            if os.path.isdir(item.src):
                shutil.copytree(item.src, item.dst)
            else:
                shutil.copy2(item.src, item.dst)
        elif os.path.isdir(item.src):
            _link_tree(item.src, item.dst)
        else:
            os.link(item.src, item.dst)
        item.done = True
    return failures


def _link_tree(src, dst):
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for name in filenames:
            link = os.path.join(target, name)
            if not os.path.exists(link):
                os.link(os.path.join(dirpath, name), link)


def write_manifest(dest, version):
    path = os.path.join(dest, LY.MANIFEST)
    if os.path.exists(path):
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write("# book-corpus 工作区清单。工具靠它认根，别再改 schema。\n")
        f.write("schema = %d\n" % LY.SCHEMA)
        f.write('tool = "book-corpus %s"\n' % version)
    return path


def write_map(items, out, root, dest):
    path = os.path.join(out, MAP_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = L.csv.writer(f)
        w.writerow(MAP_COLS)
        for n, item in enumerate(items, 1):
            w.writerow([n, 3, item.uid, item.kind,
                        os.path.relpath(item.src, root), os.path.relpath(item.dst, dest),
                        item.note or "阶段 3 0002"])
    return path


def run(root, dest, mode, apply_it, out, version):
    """返回 (退出码, 报告行)。退出码 2 表示对账不过，一字节没写。"""
    root = os.path.abspath(os.path.expanduser(root))
    dest = os.path.abspath(os.path.expanduser(dest or root))
    if not os.path.isdir(os.path.join(root, "库")):
        raise SystemExit("%s 下没有 库/，不像是 v1 工作区" % root)
    if mode != "move" and dest == root:
        raise SystemExit("原地搬只能用 --mode move，%s 会把新树写进旧树" % mode)

    items, owner, problems = plan(root, dest)
    lines = []
    books = [i for i in items if i.kind == "书"]
    lines.append("书 %d 本，合集 %d 个，保留目录 %d 个，表 %d 项，产物 %d 项"
                 % (len(books), len(owner), len([i for i in items if i.kind == "保留"]),
                    len([i for i in items if i.kind == "表"]),
                    len([i for i in items if i.kind in ("产物", "视图")])))
    lines.append("方式 %s，%s" % (mode, "落盘" if apply_it else "只打印不落盘"))
    for item in items:
        lines.append("%-8s %-10s %s -> %s"
                     % (item.kind, item.label, os.path.relpath(item.src, root),
                        os.path.relpath(item.dst, dest)))
    if problems:
        for problem in problems:
            lines.append("对账不过：%s" % problem)
        return 2, lines

    if not apply_it:
        return 0, lines

    book_rows = v1_books(root)
    apply(items, mode)
    touched = 0
    for uid, rel in book_rows:
        for base in (os.path.join(dest, "books", uid), os.path.join(root, "books", uid)):
            doc = os.path.join(base, "book.json")
            if os.path.exists(doc):
                touched += rewrite_book_json(doc, root, rel, owner)
                break
    manifest = write_manifest(dest, version)
    mapping = write_map(items, os.path.abspath(out), root, dest)
    lines.append("落盘完成，改了 %d 条源路径引用" % touched)
    lines.append("清单 %s" % manifest)
    lines.append("对照表 %s" % mapping)
    return 0, lines
