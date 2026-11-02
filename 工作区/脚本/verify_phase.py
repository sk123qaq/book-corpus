#!/usr/bin/env python3
"""L3/L5 门槛检查。拿基线（工作区/中间态/基线/）与 目录/对照表.csv 对现状。

用法:
  python3 verify_phase.py            # 六个箱子全查
  python3 verify_phase.py --box 1    # 只查一个箱子
箱子 1 源文件 inode 与抽查 sha  2 md 章节数  3 奖项 resolve  4 顶层与 book.json
箱子 5 从零重建一致
箱子 6 检索层（index.db / 概念表 / 视图视图软链 / qsearch）
"""
import argparse
import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

BASE = os.path.join(L.WORK, "中间态", "基线")
MAP = os.path.join(L.CAT, "对照表.csv")
BATCH_CSV = os.path.join(L.WORK, "批次", "奖项下载-2026-10-18", "下载结果总表.csv")
FAIL = []


def ok(msg):
    print("  [过] " + msg)


def fail(msg):
    FAIL.append(msg)
    print("  [败] " + msg)


def load_map():
    m = {}
    for r in L.read_csv(MAP):
        m[r["旧路径"]] = r["新路径"]
    return m


def box1():
    print("箱子 1 · 源文件 inode 与 sha256")
    mp = load_map()
    with open(os.path.join(BASE, "源文件inode清单.txt"), encoding="utf-8") as fh:
        base = [l.rstrip("\n") for l in fh if l.strip()]
    if not base:
        fail("基线 inode 清单是空的")
    else:
        ok("基线 inode 清单 %d 行（以清单自身为准）" % len(base))
    bad = 0
    for line in base:
        parts = line.split(" ", 2)
        if len(parts) != 3:
            fail("基线行格式怪: " + line[:60])
            continue
        ino, size, old = parts
        new = mp.get(old)
        if not new:
            fail("对照表没有: " + old)
            bad += 1
            continue
        p = os.path.join(L.ROOT, new)
        if not os.path.exists(p):
            fail("搬完后文件不在: " + new)
            bad += 1
            continue
        st = os.stat(p)
        if str(st.st_ino) != ino or str(st.st_size) != size:
            fail("inode/size 不符: " + new)
            bad += 1
    if not bad:
        ok("1392 个源文件按对照表逐行对上，inode 与字节数不变")
    # 多余源文件
    extra = [r["新路径"] for r in L.read_csv(MAP)
             if r["类别"] in ("源文件", "重复源") and not os.path.exists(
                 os.path.join(L.ROOT, r["新路径"]))]
    if extra:
        fail("对照表里的源文件缺 %d 个: %s" % (len(extra), extra[:3]))
    # 抽查 20 本 sha
    rows = L.read_csv(os.path.join(BASE, "抽查20本.csv"))
    bad = 0
    for r in rows:
        new = mp.get(r["路径"])
        if not new:
            fail("抽查书目不在对照表: " + r["路径"])
            bad += 1
            continue
        got = L.sha256_file(os.path.join(L.ROOT, new))
        if got != r["sha256"]:
            fail("sha256 变了: " + new)
            bad += 1
    if not bad:
        ok("抽查 %d 本 sha256 与迁移前一致" % len(rows))


def box2():
    print("箱子 2 · md 目录章节数")
    mp = load_map()
    base = L.read_csv(os.path.join(BASE, "md章节数.csv"))
    bad = 0
    for r in base:
        old = os.path.join("书md", r["md目录"])
        new = mp.get(old)
        if not new:
            fail("对照表没有 md 目录: " + old)
            bad += 1
            continue
        p = os.path.join(L.ROOT, new)
        if not os.path.isdir(p):
            fail("搬完后 md 目录不在: " + new)
            bad += 1
            continue
        ch, by = L.count_md_dir(p)
        if ch != int(r["章节数"]) or by != int(r["字节数"]):
            fail("md 不符 %s: %s/%s -> %d/%d" % (r["md目录"], r["章节数"], r["字节数"], ch, by))
            bad += 1
    if not bad:
        ok("%d 个 md 目录、%d 章逐一对上" % (len(base), sum(int(r["章节数"]) for r in base)))


def box3():
    print("箱子 3 · 奖项 resolve")
    con = sqlite3.connect("file:" + os.path.join(L.CAT, "library.db") + "?mode=ro", uri=True)
    orphan = con.execute("SELECT count(*) FROM book_award ba LEFT JOIN award a "
                         "ON a.name=ba.award WHERE a.name IS NULL").fetchone()[0]
    if orphan:
        fail("book_award 有 %d 条对不上 award" % orphan)
    else:
        ok("book_award %d 条全部落在 award 表里" % con.execute(
            "SELECT count(*) FROM book_award").fetchone()[0])
    uid_bad = con.execute("SELECT count(*) FROM book_award ba LEFT JOIN book b "
                          "ON b.uid=ba.uid WHERE b.uid IS NULL").fetchone()[0]
    if uid_bad:
        fail("book_award 有 %d 条对不上书" % uid_bad)
    else:
        ok("每条奖项记录都能 resolve 到具体书")
    # 批次总表里成功的行，按「文件路径」逐条落到磁盘（库或废弃）
    if not os.path.isfile(BATCH_CSV):
        fail("批次总表不存在: %s" % BATCH_CSV)
    else:
        rows = L.read_csv(BATCH_CSV)
        good = [r for r in rows if (r.get("状态") or "").startswith("✅")]
        mp = load_map()
        miss, in_lib, in_discard = [], 0, 0
        for r in good:
            fp = (r.get("文件路径") or "").strip()
            if not fp:
                rel = ""
            elif os.path.isabs(fp):
                rel = os.path.relpath(fp, L.ROOT)
            else:
                rel = fp
            new = mp.get(rel)
            if not new and os.path.exists(os.path.join(L.ROOT, rel)):
                new = rel          # L4 之后批次表里记的已是新路径
            if not new or not os.path.exists(os.path.join(L.ROOT, new)):
                miss.append((r.get("奖项"), (r.get("下载书名") or "")[:40], rel[:70]))
                continue
            if new.startswith(os.path.join("工作区", "废弃")):
                in_discard += 1
            else:
                in_lib += 1
        if miss:
            fail("批次总表 %d 条成功记录对不上磁盘: %s" % (len(miss), miss[:3]))
        else:
            ok("批次总表 %d 条成功记录的 文件路径 全部落到磁盘（库 %d，废弃 %d）"
               % (len(good), in_lib, in_discard))
    con.close()


def box4():
    print("箱子 4 · 顶层结构与 book.json")
    tops = sorted(n for n in os.listdir(L.ROOT) if os.path.isdir(os.path.join(L.ROOT, n)))
    want = sorted(["库", "目录", "视图", "工作区"])
    # .pi 是 pi 的项目配置目录、.git 是版本库、.agents 是随仓库提交的技能，
    # 都算工具账，不是库内容
    harness = [n for n in tops if n in (".pi", ".git", ".agents")]
    tops = [n for n in tops if n not in harness]
    if tops != want:
        fail("顶层目录 %s != %s" % (tops, want))
    elif harness:
        ok("顶层内容目录只剩 库/ 目录/ 视图/ 工作区/，另有工具目录 %s" % ", ".join(harness))
    else:
        ok("顶层只剩 库/ 目录/ 视图/ 工作区/")
    books = L.iter_book_json()
    ledger_n = len(L.read_csv(os.path.join(L.CAT, "分类表.csv")))
    if len(books) != ledger_n:
        fail("book.json 数 %d != 人账 %d 行" % (len(books), ledger_n))
    else:
        ok("库/**/book.json 共 %d 份，与人账行数一致" % len(books))
    miss = 0
    for rel, doc in books:
        for s in doc.get("sources") or []:
            p = os.path.normpath(os.path.join(L.LIB, rel, s.get("file", "")))
            if not os.path.exists(p):
                miss += 1
                if miss <= 3:
                    fail("book.json 指的源不在: %s -> %s" % (rel, s.get("file")))
    if not miss:
        ok("每本书的 source 路径都能落到文件")
    if os.path.isdir(os.path.join(L.LIB, "_收件箱")):
        ok("库/_收件箱 已建")


def db_dump():
    con = sqlite3.connect("file:" + os.path.join(L.CAT, "library.db") + "?mode=ro", uri=True)
    lines = []
    for row in con.execute("SELECT uid,title,subject1,subject2,path,path_state,retention,"
                           "series,volume,container,html_pages FROM book ORDER BY uid"):
        lines.append("B\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,file,format,bytes,sha256,primary_src FROM source "
                           "ORDER BY uid,file"):
        lines.append("S\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,kind,status,method,chapters,bytes FROM derived "
                           "ORDER BY uid,kind"):
        lines.append("D\t" + "\t".join(str(x) for x in row))
    for row in con.execute("SELECT uid,award,year,edition,result FROM book_award "
                           "ORDER BY uid,award"):
        lines.append("A\t" + "\t".join(str(x) for x in row))
    con.close()
    return "\n".join(lines)


def box5():
    print("箱子 5 · 从零重建一致")
    before = db_dump()
    h1 = hashlib.sha256(before.encode()).hexdigest()
    out = subprocess.run([sys.executable, os.path.join(L.SCRIPTS, "build_library.py")],
                         capture_output=True, text=True)
    if out.returncode != 0:
        fail("重建失败: " + out.stderr[-300:])
        return
    after = db_dump()
    h2 = hashlib.sha256(after.encode()).hexdigest()
    if h1 != h2:
        for i, (a, b) in enumerate(zip(before.splitlines(), after.splitlines())):
            if a != b:
                fail("重建后第 %d 行不同:\n    前 %s\n    后 %s" % (i, a[:120], b[:120]))
                break
        else:
            fail("重建后行数不同: %d -> %d" % (len(before.splitlines()), len(after.splitlines())))
    else:
        ok("DB 内容哈希一致（%s，%d 行）" % (h1[:16], len(before.splitlines())))
    print("    重建输出: " + out.stdout.strip().replace("\n", " "))


def box6():
    print("箱子 6 · 检索层（可重建产物）")
    db = os.path.join(L.CAT, "检索", "index.db")
    if not os.path.isfile(db):
        fail("没有 目录/检索/index.db")
        return
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    meta = dict(con.execute("SELECT key,value FROM meta"))
    npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
    nchap = con.execute("SELECT count(DISTINCT path) FROM para").fetchone()[0]
    if npara and nchap and int(meta.get("paras", 0)) == npara:
        ok("index.db %d 段 / %d 章，meta 一致（%.2f GB）" % (
            npara, nchap, os.path.getsize(db) / 1e9))
    else:
        fail("index.db 段落数或 meta 不一致: %s vs %d" % (meta.get("paras"), npara))
    bad = con.execute("SELECT count(*) FROM para WHERE path NOT LIKE '库/%'").fetchone()[0]
    if bad == 0:
        ok("倒排只指向 库/ 下的路径")
    else:
        fail("倒排里有 %d 段不在 库/ 下" % bad)
    ctab = os.path.join(L.CAT, "概念表.tsv")
    rows = []
    if os.path.isfile(ctab):
        with open(ctab, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    keys = [r["key"] for r in rows]
    if len(rows) >= 100 and len(set(keys)) == len(keys):
        ok("概念表 %d 条，键不重复" % len(rows))
    else:
        fail("概念表条数 %d，或键有重复" % len(rows))
    views = os.path.join(L.ROOT, "视图", "主题")
    if not os.path.isdir(views):
        fail("没有 视图/主题/")
        return
    links = broken = 0
    for dirpath, dirnames, files in os.walk(views):
        for fn in dirnames + [f for f in files if f != "README.md"]:
            p = os.path.join(dirpath, fn)
            if not os.path.islink(p):
                continue
            links += 1
            if not os.path.exists(p):
                broken += 1
    if links and not broken:
        ok("视图/主题 %d 条软链全部 resolve" % links)
    else:
        fail("视图/主题 软链 %d 条，断链 %d 条" % (links, broken))
    for word in ("自由", "内卷"):
        out = subprocess.run([sys.executable, os.path.join(L.SCRIPTS, "qsearch.py"), word,
                              "-n", "3", "--json"], capture_output=True, text=True)
        if out.returncode == 0 and '"hits"' in out.stdout and '"path"' in out.stdout:
            ok("qsearch %s 有结果" % word)
        else:
            fail("qsearch %s 没结果: %s" % (word, out.stderr[-200:]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", type=int, default=0)
    args = ap.parse_args()
    boxes = {1: box1, 2: box2, 3: box3, 4: box4, 5: box5, 6: box6}
    for i in sorted(boxes):
        if args.box and args.box != i:
            continue
        boxes[i]()
    print()
    if FAIL:
        print("门槛不过：%d 项" % len(FAIL))
        sys.exit(1)
    print("全过")


if __name__ == "__main__":
    main()
