#!/usr/bin/env python3
"""收尾门槛：四个箱子，对任何新库都成立，没有「跳过」。

用法:
  python3 verify_phase.py            # 四个箱子全查
  python3 verify_phase.py --box 2    # 只查一个箱子
箱子 1 奖项 resolve  2 顶层与 book.json  3 从零重建一致  4 检索层

退出码：0 全过；1 有失败项。没有「跳过」：箱子里的检查在空库、单本库、
多本库上都给出过或不过，验不到东西就不该放进这里。

迁移库的源文件 inode 基线、奖项下载批次对盘、旧路径对照表，都是作者迁移
一次性的输入，不在这个门槛里；对照表只剩 remap.py 的显式 --map 参数。
"""

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L

FAIL: list[str] = []
PASSED: list[str] = []


def ok(msg):
    PASSED.append(msg)
    print("  [过] " + msg)


def fail(msg):
    FAIL.append(msg)
    print("  [败] " + msg)


def box1():
    print("箱子 1 · 奖项 resolve")
    try:
        con = sqlite3.connect("file:" + L.PATHS.library_db + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        fail("打不开 build/library.db（先 book-corpus library）：%s" % exc)
        return
    try:
        orphan = con.execute(
            "SELECT count(*) FROM book_award ba LEFT JOIN award a ON a.name=ba.award WHERE a.name IS NULL"
        ).fetchone()[0]
        if orphan:
            fail("book_award 有 %d 条对不上 award" % orphan)
        else:
            ok("book_award %d 条全部落在 award 表里" % con.execute("SELECT count(*) FROM book_award").fetchone()[0])
        uid_bad = con.execute(
            "SELECT count(*) FROM book_award ba LEFT JOIN book b ON b.uid=ba.uid WHERE b.uid IS NULL"
        ).fetchone()[0]
        if uid_bad:
            fail("book_award 有 %d 条对不上书" % uid_bad)
        else:
            ok("每条奖项记录都能 resolve 到具体书")
    except sqlite3.Error as exc:
        fail("奖项表查询失败：%s" % exc)
    finally:
        con.close()


def box2():
    print("箱子 2 · 顶层结构与 book.json")
    tops = sorted(n for n in os.listdir(L.ROOT) if os.path.isdir(os.path.join(L.ROOT, n)))
    want = sorted(["books", "collections", "tables", "build", "工作区", "日课"])
    # .pi 是 pi 的项目配置目录、.git 是版本库、.agents 是随仓库提交的技能，
    # 都算工具账，不是库内容；archive 是数据仓的决策与演示存档
    harness = [n for n in tops if n in (".pi", ".git", ".agents", "archive")]
    tops = [n for n in tops if n not in harness]
    if tops != want:
        fail("顶层目录 %s != %s" % (tops, want))
    elif harness:
        ok("顶层内容目录只剩 books/ collections/ tables/ build/ 工作区/ 日课/，另有工具目录 %s" % ", ".join(harness))
    else:
        ok("顶层只剩 books/ collections/ tables/ build/ 工作区/ 日课/")
    books = L.iter_book_json()
    ledger_n = L.categories_count()
    if len(books) != ledger_n:
        fail("book.json 数 %d != 人账 %d 行" % (len(books), ledger_n))
    else:
        ok("books/**/book.json 共 %d 份，与人账行数一致" % len(books))
    missing = []
    checked = 0
    for uid, doc in books:
        for src in doc.get("sources") or []:
            checked += 1
            name = src.get("file", "")
            owner = src.get("collection") or uid
            p = os.path.join(
                L.COLLECTIONS if owner.startswith("C") else L.BOOKS, owner, "source", os.path.basename(name)
            )
            if not os.path.exists(p):
                missing.append((uid, name))
    if missing:
        for uid, f in missing[:3]:
            fail("book.json 指的源不在: %s -> %s" % (uid, f))
        if len(missing) > 3:
            fail("共 %d/%d 个源文件找不到" % (len(missing), checked))
    elif checked:
        ok("每本书的 source 路径都能落到文件（%d 个）" % checked)
    if os.path.isdir(L.PATHS.inbox):
        ok("books/_收件箱 已建")
    else:
        fail("缺 books/_收件箱（book-corpus init 会建）")


def db_dump():
    con = sqlite3.connect("file:" + L.PATHS.library_db + "?mode=ro", uri=True)
    lines = []
    for row in con.execute(
        "SELECT uid,title,subject1,subject2,path,path_state,retention,"
        "series,volume,container,html_pages FROM book ORDER BY uid"
    ):
        lines.append("B\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,file,format,bytes,sha256,primary_src FROM source ORDER BY uid,file"):
        lines.append("S\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,kind,status,method,chapters,bytes FROM derived ORDER BY uid,kind"):
        lines.append("D\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,award,year,edition,result FROM book_award ORDER BY uid,award"):
        lines.append("A\t" + "\t".join(str(x) for x in row))
    con.close()
    return "\n".join(lines)


def box3():
    print("箱子 3 · 从零重建一致")
    before = db_dump()
    h1 = hashlib.sha256(before.encode()).hexdigest()
    out = subprocess.run([sys.executable, os.path.join(L.SCRIPTS, "build_library.py")], capture_output=True, text=True)
    if out.returncode != 0:
        fail("重建失败: " + out.stderr[-300:])
        return
    after = db_dump()
    h2 = hashlib.sha256(after.encode()).hexdigest()
    if h1 != h2:
        for i, (a, b) in enumerate(zip(before.splitlines(), after.splitlines(), strict=False)):
            if a != b:
                fail("重建后第 %d 行不同:\n    前 %s\n    后 %s" % (i, a[:120], b[:120]))
                break
        else:
            fail("重建后行数不同: %d -> %d" % (len(before.splitlines()), len(after.splitlines())))
    else:
        ok("DB 内容哈希一致（%s，%d 行）" % (h1[:16], len(before.splitlines())))
    print("    重建输出: " + out.stdout.strip().replace("\n", " "))


def _qsearch_hits(word):
    out = subprocess.run(
        [sys.executable, os.path.join(L.SCRIPTS, "qsearch.py"), word, "-n", "3", "--json"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None, out.stderr[-200:]
    try:
        import json

        body = json.loads(out.stdout)
    except ValueError:
        return None, "输出不是 JSON: " + out.stdout[:120]
    return body.get("hits") or [], ""


def box4():
    print("箱子 4 · 检索层（可重建产物）")
    db = L.PATHS.index_db
    if not os.path.isfile(db):
        fail("没有 build/检索/index.db（先 book-corpus index --rebuild）")
        return
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        meta = dict(con.execute("SELECT key,value FROM meta"))
        npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
        nchap = con.execute("SELECT count(DISTINCT path) FROM para").fetchone()[0]
        if int(meta.get("paras", -1)) == npara and int(meta.get("chapters", -1)) == nchap:
            ok("index.db %d 段 / %d 章，meta 一致（%.2f GB）" % (npara, nchap, os.path.getsize(db) / 1e9))
        else:
            fail(
                "meta 与表不一致: paras %s/%d chapters %s/%d" % (meta.get("paras"), npara, meta.get("chapters"), nchap)
            )
        outside = con.execute("SELECT count(*) FROM para WHERE path NOT LIKE 'books/%'").fetchone()[0]
        if outside:
            fail("倒排里有 %d 段不在 books/ 下" % outside)
        gone = [
            p for (p,) in con.execute("SELECT DISTINCT path FROM para") if not os.path.isfile(os.path.join(L.ROOT, p))
        ]
        if gone:
            fail("倒排指向 %d 个不存在的文件，例：%s" % (len(gone), gone[:3]))
        else:
            ok("倒排路径全部落在 books/ 且文件都在")
        rows = L.concept_rows()
        keys = [r["key"] for r in rows]
        if not rows or len(set(keys)) != len(keys):
            fail("概念表 %d 条，或键有重复" % len(rows))
        else:
            ok("概念表 %d 条，键不重复" % len(rows))
        known = set(keys)
        bc = con.execute("SELECT key, count(*) FROM book_concept GROUP BY key").fetchall()
        stray = [c for c, _ in bc if c not in known]
        if stray:
            fail("概念剖面里有 %d 个键不在概念表：%s" % (len(stray), stray[:3]))
        else:
            ok("概念剖面 %d 条，键都在概念表里" % sum(n for _, n in bc))
    except sqlite3.Error as exc:
        fail("index.db 查询失败：%s" % exc)
    finally:
        con.close()
    views = L.PATHS.views
    links = broken = 0
    outside = []
    for dirpath, dirnames, files in os.walk(views):
        for fn in dirnames + [f for f in files if f != "README.md"]:
            p = os.path.join(dirpath, fn)
            if not os.path.islink(p):
                continue
            links += 1
            if not os.path.exists(p):
                broken += 1
            elif not os.path.realpath(p).startswith(os.path.realpath(L.PATHS.books) + os.sep):
                outside.append(p)
    if broken:
        fail("build/views 有 %d/%d 条软链断了" % (broken, links))
    elif outside:
        fail("build/views 有 %d 条软链指到 books/ 之外，例：%s" % (len(outside), outside[:3]))
    else:
        ok("build/views %d 条软链全部 resolve 且指向 books/" % links)
    for word in ("自由", "内卷"):
        hits, err = _qsearch_hits(word)
        if hits is None:
            fail("qsearch %s 跑不通: %s" % (word, err))
            continue
        bad = [h for h in hits if not os.path.isfile(os.path.join(L.ROOT, h.get("path", "")))]
        if bad:
            fail("qsearch %s 的 %d 条命中指向不存在的文件" % (word, len(bad)))
        else:
            ok("qsearch %s 返回 %d 条，路径都在盘上" % (word, len(hits)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", type=int, default=0)
    args = ap.parse_args()
    boxes = {1: box1, 2: box2, 3: box3, 4: box4}
    if args.box and args.box not in boxes:
        ap.error("--box 只认 1-4")
    for i in sorted(boxes):
        if args.box and args.box != i:
            continue
        boxes[i]()
    print()
    if FAIL:
        print("门槛不过：%d 项失败，%d 项通过" % (len(FAIL), len(PASSED)))
        sys.exit(1)
    print("全过：%d 项" % len(PASSED))


if __name__ == "__main__":
    main()
