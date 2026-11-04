#!/usr/bin/env python3
"""retire_book.py 的验收：临时根里两本书，一本独占源、一本共享源，外加拒绝路径。

不碰真库，不重建派生数据（一律 --no-rebuild 或 --no-snapshot）。跑完删临时根。
"""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPT = os.path.join(ROOT, "src", "bookcorpus", "scripts", "retire_book.py")
TODAY = datetime.date.today().isoformat()
UNIQUE_BYTES = b"unique source bytes" * 100
SHARED_BYTES = b"shared source bytes" * 200


def run(args):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("PASS  " + msg)


def write_book(book_dir, uid, title, source_rel, series=None):
    os.makedirs(os.path.join(book_dir, "md"), exist_ok=True)
    with open(os.path.join(book_dir, "md", "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# %s\n\n- [[01 章]]\n" % title)
    with open(os.path.join(book_dir, "md", "01 章.md"), "w", encoding="utf-8") as fh:
        fh.write("# 01 章\n\n正文正文正文。\n")
    os.makedirs(os.path.join(book_dir, "html"), exist_ok=True)
    with open(os.path.join(book_dir, "html", "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<html></html>\n")
    with open(os.path.join(book_dir, "遗留.json"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    doc = {
        "uid": uid,
        "title": title,
        "title_from_file": "",
        "author": "测试",
        "subjects": ["文学", "类型小说"],
        "series": series,
        "volume": None,
        "container": None,
        "retention": "keep",
        "sources": [{"file": source_rel, "format": "epub", "bytes": None, "sha256": "", "primary_src": 1}],
        "awards": [],
        "derived": [{"kind": "md", "status": "done", "method": "ebook-to-md", "chapters": 1, "bytes": 10}],
        "notes": "",
    }
    with open(os.path.join(book_dir, "book.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    return doc


ROOT_PLACEHOLDER = [""]


def make_fixture(base, with_snapshot=True):
    root = os.path.join(base, "books")
    os.makedirs(root)
    ROOT_PLACEHOLDER[0] = root
    lib = os.path.join(root, "books")
    cat = os.path.join(root, "tables")
    os.makedirs(os.path.join(lib, "_冷宫"))
    os.makedirs(os.path.join(root, "build", "检索", "expand"))
    os.makedirs(os.path.join(cat, "系列"))
    os.makedirs(os.path.join(root, "工作区"))

    unique_dir = os.path.join(lib, "B0001")
    shared_a = os.path.join(lib, "B0002")
    shared_b = os.path.join(lib, "B0003")
    os.makedirs(os.path.join(unique_dir, "source"))
    unique_src = os.path.join(unique_dir, "source", "独本.epub")
    with open(unique_src, "wb") as fh:
        fh.write(UNIQUE_BYTES)
    shared_src = os.path.join(root, "collections", "C9001", "source", "合集.epub")
    os.makedirs(os.path.dirname(shared_src))
    with open(shared_src, "wb") as fh:
        fh.write(SHARED_BYTES)

    books = {}
    books["B0001"] = (unique_dir, write_book(unique_dir, "B0001", "独本", "books/B0001/source/独本.epub"))
    books["B0002"] = (
        shared_a,
        write_book(shared_a, "B0002", "共享甲", "collections/C9001/source/合集.epub", series="测试合集"),
    )
    books["B0003"] = (
        shared_b,
        write_book(shared_b, "B0003", "共享乙", "collections/C9001/source/合集.epub", series="测试合集"),
    )
    for _uid, (book_dir, doc) in books.items():
        with open(os.path.join(book_dir, "book.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        for src in doc["sources"]:
            if src["file"].startswith(("books/", "collections/")):
                path = os.path.join(root, src["file"])
            else:
                path = os.path.normpath(os.path.join(book_dir, src["file"]))
            src["bytes"] = os.path.getsize(path)
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                h.update(fh.read())
            src["sha256"] = h.hexdigest()
        with open(os.path.join(book_dir, "book.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)

    with open(os.path.join(cat, "分类表.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("uid,一级,二级,作者层,保留,正式书名,备注\n")
        fh.write("B0001,文学,类型小说,0,keep,独本,测试\n")
        fh.write("B0002,文学,类型小说,0,keep,共享甲,测试\n")
        fh.write("B0003,文学,类型小说,0,keep,共享乙,测试\n")

    con = sqlite3.connect(os.path.join(root, "build", "library.db"))
    con.execute("CREATE TABLE book(uid TEXT PRIMARY KEY, title TEXT, path TEXT)")
    con.execute("CREATE TABLE book_award(uid TEXT, award TEXT, year TEXT)")
    con.execute("INSERT INTO book VALUES('B0001','独本','B0001')")
    con.execute("INSERT INTO book VALUES('B0002','共享甲','B0002')")
    con.execute("INSERT INTO book_award VALUES('B0001','某奖','2020')")
    con.commit()
    con.close()

    idx = sqlite3.connect(os.path.join(root, "build", "检索", "index.db"))
    idx.execute("CREATE TABLE expand(path TEXT, kind TEXT, tok TEXT, text TEXT)")
    idx.execute("CREATE TABLE para(book TEXT, path TEXT, line INT, chars INT, tok TEXT, text TEXT)")
    idx.execute("INSERT INTO expand VALUES('books/B0001/md/01 章.md','prefix','t','x')")
    for i in range(3):
        idx.execute("INSERT INTO para VALUES('B0001','books/B0001/md/01 章.md',? ,10,'t','x')", (i + 1,))
    idx.commit()
    idx.close()
    with open(os.path.join(root, "build", "检索", "expand", "B0001.md"), "w", encoding="utf-8") as fh:
        fh.write("# B0001 扩展账\n")
    with open(os.path.join(cat, "系列", "测试合集-合集说明.md"), "w", encoding="utf-8") as fh:
        fh.write("# 测试合集\n\n- 独本\n")

    for top, key in (("奖项", "某奖"), ("主题", "某主题")):
        d = os.path.join(root, "build", "views", top, key)
        os.makedirs(d)
        target = os.path.relpath(unique_dir, d)
        os.symlink(target, os.path.join(d, "独本"))

    with open(os.path.join(lib, "_冷宫", "README.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# books/_冷宫\n\n## 账\n\n"
            "| 移入日期 | uid | 书名 | 原路径 | 源文件 | 原 md | 理由 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 2026-10-21 | B1359 | 旧书 | books/B1359 | `a.epub` 1 字节 | 无 md | 试 |\n"
        )

    snapshot = os.path.join(base, "books.snapshot-" + TODAY)
    if with_snapshot:
        os.makedirs(snapshot)
    return root, books, unique_src, shared_src, snapshot


def ledger_rows(root):
    """人账行（ledger.db 的分类表）；库还没建时按旧 CSV 读（拒绝路径不写盘）。"""
    import csv as _csv
    import sqlite3

    dbp = os.path.join(root, "tables", "ledger.db")
    if not os.path.isfile(dbp):
        with open(os.path.join(root, "tables", "分类表.csv"), encoding="utf-8-sig", newline="") as fh:
            return list(_csv.DictReader(fh))
    con = sqlite3.connect(dbp)
    rows = [{"uid": r[0]} for r in con.execute("SELECT uid FROM 分类 ORDER BY uid")]
    con.close()
    return rows


def legacy_csv_uids(root):
    import csv

    with open(os.path.join(root, "tables", "分类表.csv"), encoding="utf-8-sig", newline="") as fh:
        return [r["uid"] for r in csv.DictReader(fh)]


def cold_json(root):
    hits = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(root, "books", "_冷宫")):
        for name in filenames:
            if name.endswith((".json", ".md")) and name != "README.md":
                hits.append(os.path.join(dirpath, name))
    return hits


def test_preview_and_apply():
    base = tempfile.mkdtemp(prefix="retire-a-")
    try:
        root, books, unique_src, shared_src, snapshot = make_fixture(base, with_snapshot=True)
        rc, out, err = run(["--uid", "B0001", "--reason", "测试移出", "--dry-run", "--json", "--root", root])
        check(rc == 0, "dry-run 退出码 0（stderr: %s）" % err.strip())
        preview = json.loads(out)
        check(preview["uid"] == "B0001" and preview["title"] == "独本", "预览回 uid 与书名")
        check(preview["book_dir"] == "books/B0001", "书目录是 books/<uid>")
        src = preview["sources"][0]
        check(src["action"] == "move" and src["shared_with"] == 0, "独占源动作 move，共享 0 本")
        check(src["bytes"] == len(UNIQUE_BYTES), "源字节与磁盘一致")
        check(
            preview["delete"] == ["md/", "html/", "book.json", "遗留.json"],
            "删除清单含 md/、html/、book.json 与其它 json: %s" % preview["delete"],
        )
        check(preview["leftovers"] == [], "没有附带删除项")
        check(preview["ledger"] is True and preview["db"] is True, "人账行与 DB 行都在")
        check(preview["awards"] == ["某奖"], "奖项行读到了")
        check(
            len(preview["views"]) == 2 and preview["view_awards"] and preview["view_topics"], "奖项与主题视图软链各一条"
        )
        check(len(preview["series_docs"]) == 1, "受影响系列文档一条")
        check(preview["md_chapters"] == 1 and preview["md_bytes"] > 0, "md 章数与字节")
        check(preview["expand_rows"] == 1 and preview["index_rows"] == 3, "expand 行与 para 行数对得上")
        check(preview["expand_md"].endswith("expand/B0001.md"), "expand 账文件标出")
        check(preview["cold_path"] == "books/_冷宫/独本/source", "冷宫 source 目录")
        check(src["cold"] == "books/_冷宫/独本/source/独本.epub", "冷宫里每个源的目标文件")
        check(preview["snapshot"].endswith("books.snapshot-" + TODAY) and preview["snapshot_ok"], "快照路径与就绪态")
        check(len(preview["plan_hash"]) == 64, "plan_hash 是 sha256")
        check(preview["blockers"] == [], "预览没有挡住项")
        hash_a = preview["plan_hash"]

        rc, out2, _ = run(["--uid", "B0001", "--reason", "测试移出", "--dry-run", "--json", "--root", root])
        check(json.loads(out2)["plan_hash"] == hash_a, "两次 dry-run 的 plan_hash 一致")

        rc, out3, _ = run(["--uid", "B0002", "--reason", "测试移出", "--dry-run", "--json", "--root", root])
        p2 = json.loads(out3)
        check(
            p2["sources"][0]["action"] == "hardlink" and p2["sources"][0]["shared_with"] == 1,
            "共享源动作 hardlink，共享 1 本",
        )
        check(p2["sources"][0]["path"] == "collections/C9001/source/合集.epub", "共享源在书目录外面")
        check(p2["sources"][0]["fallback"] == "", "同文件系统不用复制")

        rc, out4, err4 = run(
            ["--uid", "B0001", "--reason", "测试移出", "--apply", "--plan-hash", "0" * 64, "--json", "--root", root]
        )
        check(rc == 2 and "不一致" in (out4 + err4), "哈希不一致拒绝")
        check(os.path.isfile(os.path.join(books["B0001"][0], "book.json")), "拒绝后书目录没动")
        check(len(ledger_rows(root)) == 3, "拒绝后人账没动")
        os.rename(snapshot, snapshot + ".off")
        rc, out5, err5 = run(
            ["--uid", "B0001", "--reason", "测试移出", "--apply", "--plan-hash", hash_a, "--json", "--root", root]
        )
        check(rc == 2 and "快照" in (out5 + err5), "快照缺失拒绝")
        check(os.path.isfile(os.path.join(books["B0001"][0], "book.json")), "快照拒绝后书目录没动")
        os.rename(snapshot + ".off", snapshot)

        rc, out6, err6 = run(
            [
                "--uid",
                "B0001",
                "--reason",
                "测试移出",
                "--apply",
                "--plan-hash",
                hash_a,
                "--json",
                "--root",
                root,
                "--no-rebuild",
            ]
        )
        report = json.loads(out6) if out6.strip().startswith("{") else {}
        check(rc == 0 and report.get("ok") is True, "apply 成功（stderr: %s）" % err6.strip())
        check(not os.path.exists(books["B0001"][0]), "书目录删掉了")
        cold_src = os.path.join(root, "books", "_冷宫", "独本", "source", "独本.epub")
        check(os.path.isfile(cold_src), "独占源移进冷宫 source/")
        with open(cold_src, "rb") as fh:
            check(fh.read() == UNIQUE_BYTES, "冷宫里的源字节一致")
        check(not os.path.exists(unique_src), "原位置的独占源已移走")
        check(cold_json(root) == [], "冷宫没有 json 与章节 md")
        check(sorted(os.listdir(os.path.join(root, "books", "_冷宫", "独本"))) == ["source"], "冷宫书目录只有 source/")
        rows = ledger_rows(root)
        check([r["uid"] for r in rows] == ["B0002", "B0003"], "人账删掉 B0001 且其余行保持顺序")
        check(legacy_csv_uids(root) == ["B0001", "B0002", "B0003"], "切换档 CSV 不随删行变（活账在 ledger.db）")
        tsv = os.path.join(root, "工作区", "批次", "移出-" + TODAY, "retired.tsv")
        check(os.path.isfile(tsv), "retired.tsv 落在 移出-<日期>/ 下")
        with open(tsv, encoding="utf-8", newline="") as fh:
            import csv

            body = list(csv.DictReader(fh, delimiter="\t"))
        check(len(body) == 1 and body[0]["uid"] == "B0001", "retired.tsv 一行一条")
        check(len(body[0]) == 16 and body[0]["note"] == "", "retired.tsv 16 列，note 空")
        check(body[0]["shared_with"] == "0" and body[0]["format"] == "epub", "retired.tsv 记了共享本数与格式")
        with open(os.path.join(root, "books", "_冷宫", "README.md"), encoding="utf-8") as fh:
            readme = fh.read().splitlines()
        new_rows = [line for line in readme if "B0001" in line]
        check(len(new_rows) == 1 and len(new_rows[0].strip().strip("|").split("|")) == 7, "冷宫 README 追加 7 列一行")
        check(sum(1 for line in readme if line.startswith("| ")) == 3, "README 原有行还在")

        rc, out7, _ = run(
            [
                "--uid",
                "B0001",
                "--reason",
                "测试移出",
                "--apply",
                "--plan-hash",
                hash_a,
                "--json",
                "--root",
                root,
                "--no-rebuild",
            ]
        )
        check(rc == 0 and json.loads(out7).get("already_retired") is True, "重复 apply 认出已移出，不报错")

        rc, out8, _err8 = run(["--uid", "B0002", "--reason", "测试移出共享本", "--dry-run", "--json", "--root", root])
        hash_b = json.loads(out8)["plan_hash"]
        rc, out9, err9 = run(
            [
                "--uid",
                "B0002",
                "--reason",
                "测试移出共享本",
                "--apply",
                "--plan-hash",
                hash_b,
                "--json",
                "--root",
                root,
                "--no-rebuild",
            ]
        )
        check(rc == 0 and json.loads(out9)["ok"] is True, "共享本 apply 成功：%s" % err9.strip())
        cold_link = os.path.join(root, "books", "_冷宫", "共享甲", "source", "合集.epub")
        check(os.path.isfile(cold_link), "共享源在冷宫里有一份")
        a = os.stat(cold_link)
        b = os.stat(shared_src)
        check((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), "冷宫共享源与原文件 inode 相等")
        check(a.st_nlink == 2, "硬链接数 2，原文件还在")
        check(os.path.isfile(shared_src), "共享源原文件没被移走")
        check(os.path.isfile(os.path.join(books["B0003"][0], "book.json")), "共用同一源的 B0003 还在")
        check([r["uid"] for r in ledger_rows(root)] == ["B0003"], "人账只剩 B0003")
        with open(os.path.join(root, "books", "_冷宫", "README.md"), encoding="utf-8") as fh:
            readme2 = fh.read()
        check("B0002" in readme2 and "与 1 本共用" in readme2, "共享本 README 行标了共用本数")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_snapshot_override():
    base = tempfile.mkdtemp(prefix="retire-b-")
    try:
        root, books, _unique, _shared, _snapshot = make_fixture(base, with_snapshot=False)
        rc, out, _err = run(["--uid", "B0001", "--reason", "无快照测试", "--dry-run", "--json", "--root", root])
        preview = json.loads(out)
        check(preview["snapshot_ok"] is False, "没有快照时 preview 标缺失")
        rc, out2, err2 = run(
            [
                "--uid",
                "B0001",
                "--reason",
                "无快照测试",
                "--apply",
                "--plan-hash",
                preview["plan_hash"],
                "--json",
                "--root",
                root,
                "--no-rebuild",
            ]
        )
        check(rc == 2 and "快照" in (out2 + err2), "默认门槛拒绝无快照落地")
        rc, out3, err3 = run(
            [
                "--uid",
                "B0001",
                "--reason",
                "无快照测试",
                "--apply",
                "--plan-hash",
                preview["plan_hash"],
                "--json",
                "--root",
                root,
                "--no-rebuild",
                "--no-snapshot",
            ]
        )
        check(rc == 0 and json.loads(out3)["ok"] is True, "--no-snapshot 放行（stderr: %s）" % err3.strip())
        check(
            os.path.isfile(os.path.join(root, "books", "_冷宫", "独本", "source", "独本.epub")),
            "--no-snapshot 落地后冷宫有源",
        )
        rc, out4, _err4 = run(["--uid", "B0002", "--reason", "守卫测试", "--dry-run", "--json", "--root", root])
        preview2 = json.loads(out4)
        rc, out5, err5 = run(
            [
                "--uid",
                "B0002",
                "--reason",
                "守卫测试",
                "--apply",
                "--plan-hash",
                preview2["plan_hash"],
                "--json",
                "--root",
                root,
                "--no-snapshot",
            ]
        )
        check(rc == 2 and "重建派生数据" in (out5 + err5), "临时根要重建派生数据时拒绝")
        check(os.path.isfile(os.path.join(books["B0002"][0], "book.json")), "守卫拒绝后书目录没动")
        rc, out6, err6 = run(["--uid", "B9999", "--reason", "不存在", "--dry-run", "--json", "--root", root])
        check(rc == 2 and "不在 books/" in (out6 + err6), "uid 不存在时退出码 2 且说明原因")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_copy_fallback():
    """跨文件系统那条路：硬链接建不出来时改复制，字节一致、inode 不同。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("retire_book", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = tempfile.mkdtemp(prefix="retire-copy-")
    try:
        src = os.path.join(base, "src.epub")
        dst = os.path.join(base, "dst.epub")
        with open(src, "wb") as fh:
            fh.write(SHARED_BYTES)
        how = mod._move_or_link(src, dst, "hardlink", "copy")
        check(how == "copy", "fallback=copy 时走复制")
        with open(dst, "rb") as fh:
            check(fh.read() == SHARED_BYTES, "复制出的文件字节一致")
        check(os.stat(src).st_ino != os.stat(dst).st_ino, "复制后 inode 不同，源文件还在")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    test_preview_and_apply()
    test_snapshot_override()
    test_copy_fallback()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
