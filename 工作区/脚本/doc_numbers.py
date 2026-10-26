#!/usr/bin/env python3
"""AGENTS.md 现状数字块：现取读数、渲染成固定一段、比对或重写。

  doc_numbers.py             # 打印现库该有的块，不碰文件
  doc_numbers.py --check     # 比对 AGENTS.md 里的块，不一致退出 1 并打印两份
  doc_numbers.py --write     # tmp + os.replace 重写块；没有块就插在「## 现状」标题之后
  doc_numbers.py --json      # 只输出 counts
  doc_numbers.py --root DIR  # 换库根，默认按脚本位置推
  doc_numbers.py --doc PATH  # 换文档，默认 <root>/AGENTS.md

块由 <!-- numbers:start --> 与 <!-- numbers:end --> 夹住，只换夹住的那几行，块外一字不动。
取数只读：library.db 以 mode=ro 打开，md 本数看 库/<path>/md 是不是目录，视图数软链。
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOC = "AGENTS.md"
START = "<!-- numbers:start -->"
END = "<!-- numbers:end -->"
BLOCK_RE = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
DATE_RE = re.compile(r"As of \S+?：")
HEADING = "## 现状"
ACTION_TEXT = {"replaced": "换", "inserted": "补上"}
PENDING_KEYS = ("pdf", "txt", "djvu")
OTHER = "other"


class DocError(Exception):
    """文档结构不认识，不改盘。"""


def num(value):
    return "{:,}".format(int(value))


def collect(root=ROOT, as_of=None):
    """现库的十项读数。"""
    libdb = os.path.join(root, "目录", "library.db")
    con = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    try:
        books = con.execute("SELECT count(*) FROM book").fetchone()[0]
        paths = [r[0] for r in con.execute("SELECT path FROM book")]
        awards = con.execute("SELECT count(*) FROM book_award").fetchone()[0]
        award_names = con.execute("SELECT count(*) FROM award").fetchone()[0]
        pending = {k: 0 for k in PENDING_KEYS + (OTHER,)}
        for fmt, n in con.execute(
                "SELECT coalesce(nullif(lower(format), ''), ?), count(*) "
                "FROM v_pending_md GROUP BY 1", (OTHER,)):
            pending[fmt if fmt in PENDING_KEYS else OTHER] += n
    finally:
        con.close()
    view = views(root)
    return {
        "books": books,
        "md": sum(1 for p in paths if os.path.isdir(os.path.join(root, "库", p, "md"))),
        "pending_total": sum(pending.values()),
        "pending": pending,
        "awards": awards,
        "award_names": award_names,
        "view_total": sum(view.values()),
        "view": view,
        "pitfall_classes": pitfall(root),
        "as_of": as_of or datetime.date.today().isoformat(),
    }


def views(root):
    out = {"奖项": 0, "主题": 0}
    base = os.path.join(root, "视图")
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            entry = os.path.join(dirpath, name)
            if not os.path.islink(entry):
                continue
            top = os.path.relpath(entry, base).split(os.sep)[0]
            if top in out:
                out[top] += 1
    return out


def pitfall(root):
    path = os.path.join(root, "目录", "检索", "踩坑记录.md")
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if re.match(r"## [A-Z] 类", line))


def render(counts):
    pending = counts["pending"]
    parts = ["%s %s" % (num(pending.get(k, 0)), k) for k in PENDING_KEYS]
    if pending.get(OTHER):
        parts.append("%s %s" % (num(pending[OTHER]), OTHER))
    view = counts["view"]
    line = ("As of %s：%s 本书，%s 本有 md，%s 本待拆章（%s）；%s 条奖项记录，"
            "视图 %s 条软链（奖项 %s、主题 %s）；坑账 %s 类。") % (
        counts["as_of"], num(counts["books"]), num(counts["md"]),
        num(counts["pending_total"]), "、".join(parts), num(counts["awards"]),
        num(counts["view_total"]), num(view["奖项"]), num(view["主题"]),
        num(counts["pitfall_classes"]))
    return "%s\n%s\n%s" % (START, line, END)


def read_block(text):
    m = BLOCK_RE.search(text)
    return m.group(0) if m else None


def same_numbers(got, block):
    """只比数字，忽略 as_of 日期。日期是同步戳，不是契约。"""
    return DATE_RE.sub("As of ：", got) == DATE_RE.sub("As of ：", block)


def sync(text, block):
    """把 block 放进 text，返回 (新全文, 动作)。有块就换，没有块就插在「## 现状」之后。"""
    m = BLOCK_RE.search(text)
    if m:
        return text[:m.start()] + block + text[m.end():], "replaced"
    if START in text or END in text:
        raise DocError("只有一个标记，块的范围判不出来")
    lines = text.splitlines(True)
    for i, line in enumerate(lines):
        if line.startswith(HEADING):
            head = line if line.endswith("\n") else line + "\n"
            return "".join(lines[:i] + [head, "\n" + block + "\n"] + lines[i + 1:]), "inserted"
    raise DocError("没有 numbers 块，也没有「%s」标题" % HEADING)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def apply_write(doc, block):
    try:
        text = read_text(doc)
    except OSError as exc:
        print("读不了 %s: %s" % (doc, exc))
        return 1
    try:
        new, action = sync(text, block)
    except DocError as exc:
        print("没动 %s：%s" % (doc, exc))
        return 1
    if new == text:
        print("%s 已是现库数字，没改" % doc)
        return 0
    write_text(doc, new)
    print("%s %s了数字块" % (doc, ACTION_TEXT[action]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="比对文档里的块，不一致退出 1")
    mode.add_argument("--write", action="store_true", help="重写或补上块")
    mode.add_argument("--json", action="store_true", help="只输出 counts")
    ap.add_argument("--root", default=ROOT, help="库根，默认按脚本位置推")
    ap.add_argument("--doc", help="文档，默认 <root>/AGENTS.md")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    doc = args.doc or os.path.join(root, DOC)
    try:
        counts = collect(root)
    except (OSError, sqlite3.Error) as exc:
        print("取数失败 %s: %s" % (root, exc))
        return 2
    if args.json:
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        return 0
    block = render(counts)
    if args.write:
        return apply_write(doc, block)
    if not args.check:
        print(block)
        return 0
    try:
        got = read_block(read_text(doc))
    except OSError as exc:
        print("读不了 %s: %s" % (doc, exc))
        return 1
    if got == block:
        print("%s 的数字块与现库一致（as_of %s）" % (doc, counts["as_of"]))
        return 0
    if got is not None and same_numbers(got, block):
        print("%s 的数字与现库一致，块日期旧；--write 只刷新日期" % doc)
        return 0
    print("%s 的数字块%s" % (doc, "缺失" if got is None else "过期"))
    if got is not None:
        print("--- %s 里的块 ---" % doc)
        print(got)
    print("--- 现库的块 ---")
    print(block)
    print("--- 修：python3 工作区/脚本/doc_numbers.py --write ---")
    return 1


if __name__ == "__main__":
    sys.exit(main())
