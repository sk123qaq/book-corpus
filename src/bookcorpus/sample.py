"""内置样书：把《阿Q正传》装进工作区，空库立刻能检索、能问答。

数据在 sample/阿Q正传/，随包发；不联网，不跑模型，不占拆章时间。
"""

import json
import os
import shutil
import time

from .scripts import ingest as ING
from .scripts import lib_common as L

NAME = "阿Q正传"
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample", NAME)
MARK = "sample:" + NAME
TITLE = "阿Q正传"
AUTHOR = "鲁迅"
SUBJECTS = ["文学", "严肃文学小说"]


def load_books(root):
    books = os.path.join(root, "books")
    out = []
    if not os.path.isdir(books):
        return out
    for name in sorted(os.listdir(books)):
        doc = os.path.join(books, name, "book.json")
        if not os.path.isfile(doc):
            continue
        try:
            with open(doc, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if MARK in (data.get("notes") or ""):
            out.append((name, data))
    return out


def _count(root, uid):
    md = os.path.join(root, "books", uid, "md")
    files = sorted(f for f in os.listdir(md) if f.endswith(".md")) if os.path.isdir(md) else []
    chars = 0
    for name in files:
        with open(os.path.join(md, name), encoding="utf-8") as fh:
            chars += len(fh.read())
    return len(files), chars


def install(root):
    """复制样书、写 book.json 与人账。返回 (uid, 章数, 字符数)。"""
    rows = L.read_csv(os.path.join(root, "tables", "分类表.csv"))
    uid = ING.next_uid(rows)
    dest = os.path.join(root, "books", uid)
    if os.path.exists(dest):
        raise SystemExit("目标目录已存在：%s" % dest)
    source = os.path.join(DIR, "source", NAME + ".txt")
    if not os.path.isfile(source) or not os.path.isdir(os.path.join(DIR, "md")):
        raise SystemExit("包里的样书不全：%s（重装 book-corpus 试试）" % DIR)

    os.makedirs(os.path.join(dest, "source"), exist_ok=True)
    shutil.copyfile(source, os.path.join(dest, "source", NAME + ".txt"))
    shutil.copytree(os.path.join(DIR, "md"), os.path.join(dest, "md"))

    src = os.path.join("source", NAME + ".txt")
    notes = "%s；公有领域底本 Project Gutenberg #27166；重建用 book-corpus sample" % MARK
    L.write_json_atomic(os.path.join(dest, "book.json"), L.book_doc(
        uid, TITLE, "", AUTHOR, SUBJECTS, None, None, None, "keep", notes,
        [{"file": src, "format": "txt",
          "bytes": os.path.getsize(os.path.join(dest, src)),
          "sha256": L.sha256_file(os.path.join(dest, src)), "primary_src": 1}], []))

    rows.append({"uid": uid, "一级": SUBJECTS[0], "二级": SUBJECTS[1], "作者层": "0",
                 "保留": "keep", "正式书名": TITLE,
                 "备注": "sample:待复核 %s" % time.strftime("%Y-%m-%d")})
    L.write_csv(os.path.join(root, "tables", "分类表.csv"), rows, L.LEDGER_HEADER)
    chapters, chars = _count(root, uid)
    return uid, chapters, chars
