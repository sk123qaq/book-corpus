"""工作区：根解析、清单、初始化、体检。"""

import os
import shutil
import sqlite3
import sys
import tomllib

from .scripts import layout as L

SCHEMA = L.SCHEMA
MANIFEST = L.MANIFEST
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def manifest_path(root):
    return os.path.join(root, MANIFEST)


def resolve(explicit=None):
    """工作区根。显式参数优先，其次 BOOKS_ROOT，再从 cwd 向上找 books.toml。

    找不到就报错，不回退到 cwd。回退会让命令把产物写进当前目录。
    """
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    root = L.find_root()
    if root is None:
        raise SystemExit(
            "找不到工作区。用 book-corpus init <目录> 建一个，"
            "或传 --root，或设 BOOKS_ROOT。当前目录 %s 往上都没有 %s。"
            % (os.getcwd(), MANIFEST))
    return root


def read_manifest(root):
    path = manifest_path(root)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return tomllib.load(f)


def init(target, tool_version="0.1.0", force=False):
    target = os.path.abspath(os.path.expanduser(target))
    if os.path.exists(manifest_path(target)) and not force:
        raise SystemExit("%s 已经是工作区（有 %s）。要重写就加 --force。" % (target, MANIFEST))
    for rel in L.INIT_DIRS:
        os.makedirs(os.path.join(target, rel), exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(TEMPLATES):
        rel = os.path.relpath(dirpath, TEMPLATES)
        for name in filenames:
            dst_dir = os.path.join(target, rel) if rel != "." else target
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, name), os.path.join(dst_dir, name))
    with open(manifest_path(target), "w", encoding="utf-8") as f:
        f.write("# book-corpus 工作区清单。工具靠它认根，别再改 schema。\n")
        f.write("schema = %d\n" % SCHEMA)
        f.write('tool = "book-corpus %s"\n' % tool_version)
    return target


def check(root, version="0.1.0"):
    """体检，返回 (level, label, detail) 列表。level 取 ok / warn / fail。"""
    rows = []

    def add(level, label, detail=""):
        rows.append((level, label, detail))

    if not os.path.isdir(root):
        add("fail", "工作区", "目录不存在 %s" % root)
        return rows

    paths = L.Layout(root)

    man = read_manifest(root)
    if man is None:
        add("fail", "清单", "缺 %s，不是工作区" % MANIFEST)
    elif man.get("schema") != SCHEMA:
        add("fail", "清单", "schema=%r，本工具只认 %d" % (man.get("schema"), SCHEMA))
    else:
        add("ok", "清单", "%s schema=%d tool=%s" % (MANIFEST, SCHEMA, man.get("tool", "?")))

    if not os.path.exists(paths.ledger):
        add("fail", "人账", "缺 tables/分类表.csv")
    else:
        with open(paths.ledger, encoding="utf-8-sig") as f:
            head = f.readline().lstrip("\ufeff").strip()
        cols = head.split(",")
        if cols[:len(L.LEDGER_COLS)] != L.LEDGER_COLS:
            add("fail", "人账", "表头是 %s" % head)
        else:
            with open(paths.ledger, encoding="utf-8-sig") as f:
                n = sum(1 for _ in f) - 1
            add("ok", "人账", "分类表.csv %d 行" % n)

    if not os.path.exists(paths.concepts):
        add("fail", "概念表", "缺 tables/概念表.tsv")
    else:
        with open(paths.concepts, encoding="utf-8-sig") as f:
            n = sum(1 for _ in f) - 1
        add("ok" if n else "warn", "概念表", "概念表.tsv %d 条" % n)

    books = 0
    for dirpath, dirnames, filenames in os.walk(paths.books):
        dirnames[:] = [d for d in dirnames
                       if not (dirpath == paths.books and d in L.RESERVED)]
        if "book.json" in filenames:
            books += 1
    add("ok" if books else "warn", "书", "%d 本有 book.json" % books)

    collections = 0
    for name in os.listdir(paths.collections) if os.path.isdir(paths.collections) else []:
        if os.path.isdir(os.path.join(paths.collections, name)):
            collections += 1
    add("ok", "合集", "%d 个合集目录" % collections)

    if not os.path.exists(paths.library_db):
        add("warn", "书目库", "无 build/library.db，跑 book-corpus library 生成")
    else:
        try:
            con = sqlite3.connect("file:%s?mode=ro" % paths.library_db, uri=True)
            n = con.execute("select count(*) from book").fetchone()[0]
            con.close()
            add("ok" if n == books else "warn", "书目库",
                "book 表 %d 行，磁盘 %d 本" % (n, books))
        except sqlite3.Error as exc:
            add("fail", "书目库", "%s" % exc)

    if not os.path.exists(paths.index_db):
        add("warn", "检索索引", "无 build/检索/index.db，跑 book-corpus index --rebuild 生成")
    else:
        try:
            con = sqlite3.connect("file:%s?mode=ro" % paths.index_db, uri=True)
            meta = dict(con.execute("select key, value from meta"))
            con.close()
            add("ok", "检索索引", "段 %s 章 %s 书 %s，建于 %s" % (
                meta.get("paras", "?"), meta.get("chapters", "?"),
                meta.get("books", "?"), meta.get("built_at", "?")))
        except sqlite3.Error as exc:
            add("fail", "检索索引", "%s" % exc)

    add("ok", "工具", "book-corpus %s，Python %s" % (version, sys.version.split()[0]))
    return rows
