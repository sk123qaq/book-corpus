#!/usr/bin/env python3
"""L3 搬迁：把旧树 rename 成 库/<一级>/<二级>/<作者?>/<容器?>/<书名>/。

只做同盘 rename，不复制、不覆盖。目标存在就中止。

用法:
  python3 migrate_layout.py --plan      # 生成 目录/搬迁计划.csv（只读盘，不动文件）
  python3 migrate_layout.py --apply     # 按计划执行，写 book.json、对照表.csv，重建 DB
  python3 migrate_layout.py --reverse   # 按 对照表.csv 反向搬回
  python3 migrate_layout.py --views     # 只重建 视图/奖项 软链树
"""
import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

PLAN = os.path.join(L.CAT, "搬迁计划.csv")
MAP = os.path.join(L.CAT, "对照表.csv")
PLAN_HEADER = ["序号", "阶段", "uid", "类别", "旧路径", "新路径", "备注"]
DB_URI = "file:" + os.path.join(L.CAT, "library.db") + "?mode=ro"
BATCH = os.path.join("工作区", "批次", "奖项下载-2026-10-18")
DISCARD = os.path.join("工作区", "废弃")
OLD_TOP = ["原书", "原书pdf", "建筑", "漫画与动画", "人文社科", "文学", "写作与编剧",
           "艺术", "游戏设定", "游戏设计", "奖项书", "书md", "书籍HTML",
           "获奖名单与进度", "scripts"]


def collect_plan():
    """(rows, meta)。所有路径相对 ROOT；阶段 1 的目录搬完后，阶段 2 的旧路径才成立。"""
    ledger = L.read_csv(os.path.join(L.CAT, "分类表.csv"))
    inv = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))}
    rows, meta = [], {"kinds": defaultdict(int)}

    def lsdir(relpath):
        p = os.path.join(L.ROOT, relpath)
        if os.path.isdir(p):
            return sorted(os.listdir(p))
        print("跳过不存在的旧目录: %s" % relpath, file=sys.stderr)
        return []

    def add(phase, uid, kind, old, new, note=""):
        rows.append({"阶段": phase, "uid": uid, "类别": kind, "旧路径": old,
                     "新路径": new, "备注": note})
        meta["kinds"][kind] += 1

    # ---- 阶段 1：工作区与顶层散件 ----
    add(1, "", "批次", "获奖名单与进度", BATCH, "含 work/")
    add(1, "", "中间态", os.path.join("原书pdf", "mineru_work"),
        os.path.join("工作区", "中间态", "pdf-ocr-2026-09"), "MinerU 中间产物")
    add(1, "", "脚本", os.path.join("书籍HTML", "build_index.py"),
        os.path.join("工作区", "脚本", "build_index.py"))
    add(1, "", "目录", os.path.join("书籍HTML", "index.html"),
        os.path.join("目录", "阅读版索引.html"), "阅读版总索引")
    add(1, "", "废弃", "原书和原书pdf详细目录.xlsx",
        os.path.join(DISCARD, "原书和原书pdf详细目录.xlsx"), "过期")
    for fn in lsdir("scripts"):
        if os.path.isfile(os.path.join(L.ROOT, "scripts", fn)):
            add(1, "", "脚本", os.path.join("scripts", fn), os.path.join("工作区", "脚本", fn))
    pyc = os.path.join(L.ROOT, "scripts", "__pycache__")
    if os.path.isdir(pyc):
        for fn in sorted(os.listdir(pyc)):
            add(1, "", "脚本", os.path.join("scripts", "__pycache__", fn),
                os.path.join("工作区", "脚本", "__pycache__", fn), "并入现有缓存目录")

    # ---- 阶段 2：批次目录里的备份 ----
    cur_bak = os.path.join(L.ROOT, "获奖名单与进度", "work", "backups")
    if os.path.isdir(cur_bak):
        for fn in sorted(os.listdir(cur_bak)):
            old = os.path.join(BATCH, "work", "backups", fn)
            if os.path.isdir(os.path.join(cur_bak, fn)):
                add(2, "", "废弃", old, os.path.join(DISCARD, fn),
                    "坏文件留档" if fn == "corrupt" else "重复副本留档")
            elif fn.endswith(".bak"):
                add(2, "", "废弃", old, os.path.join(DISCARD, fn), "旧备份")

    # ---- 阶段 3：书内容 ----
    by_src = defaultdict(list)
    rels = {}
    for led in ledger:
        m = inv.get(led["uid"], {})
        rels[led["uid"]] = L.book_relpath(led, m)
        if m.get("src_path"):
            by_src[m["src_path"]].append(led["uid"])
    for old, uids in sorted(by_src.items()):
        if len(uids) > 1:
            target_dir = os.path.commonpath([rels[u] for u in uids])
            note = "合集共享源，%d 卷" % len(uids)
        else:
            target_dir = rels[uids[0]]
            note = ""
        add(3, sorted(uids)[0], "源文件", old,
            os.path.join("库", target_dir, "source", os.path.basename(old)), note)
    dup_seen = set()
    for led in ledger:
        for old in json.loads(inv.get(led["uid"], {}).get("额外源") or "[]"):
            if old in dup_seen:
                continue
            dup_seen.add(old)
            add(3, "", "重复源", old, os.path.join(DISCARD, "duplicates", os.path.basename(old)),
                "sha256 与正本相同，留档不删")
    for led in ledger:
        uid = led["uid"]
        m = inv.get(uid, {})
        if m.get("md_dir"):
            add(3, uid, "md", os.path.join("书md", m["md_dir"]),
                os.path.join("库", rels[uid], "md"))
    # html 两本书
    html_root = os.path.join(L.ROOT, "书籍HTML")
    for name in lsdir("书籍HTML"):
        p = os.path.join(html_root, name)
        if not os.path.isdir(p):
            continue
        cand = [r for r in ledger if r["正式书名"] == name]
        if len(cand) != 1:
            raise SystemExit("书籍HTML/%s 对不上人账正式书名（命中 %d 条）" % (name, len(cand)))
        uid = cand[0]["uid"]
        tgt = os.path.join("库", rels[uid], "html")
        add(3, uid, "html", os.path.join("书籍HTML", name, "html"), tgt)
        add(3, uid, "html_images", os.path.join("书籍HTML", name, "images"),
            os.path.join(tgt, "images"), "图片收进 html/")
        add(3, uid, "html_index", os.path.join("书籍HTML", name, "index.html"),
            os.path.join(tgt, "index.html"))
        leftover = set(os.listdir(p)) - {"html", "images", "index.html"}
        if leftover:
            raise SystemExit("书籍HTML/%s 还有没登记的条目: %s" % (name, sorted(leftover)))
    # OCR 产物
    ocr_root = os.path.join(L.ROOT, "原书pdf", "md")
    ocr_uids = []
    for name in lsdir(os.path.join("原书pdf", "md")):
        old = os.path.join("原书pdf", "md", name)
        if name == "转换说明.md":
            add(3, "", "ocr_note", old,
                os.path.join("工作区", "中间态", "pdf-ocr-2026-09", "转换说明.md"), "OCR 运行说明")
            continue
        stem = name[:-len(".assets")] if name.endswith(".assets") else name[:-len(".md")]
        cand = [r for r in ledger if r["正式书名"] in (stem, stem.rstrip("_"))]
        if len(cand) != 1:
            raise SystemExit("原书pdf/md/%s 对不上人账（命中 %d 条）" % (name, len(cand)))
        uid = cand[0]["uid"]
        ocr_uids.append(uid)
        add(3, uid, "ocr_assets" if name.endswith(".assets") else "ocr_md", old,
            os.path.join("库", rels[uid], "md", name), "method=mineru-ocr")
    # 合集说明
    for fn in lsdir("书md"):
        p = os.path.join(L.ROOT, "书md", fn)
        if os.path.isfile(p) and fn.endswith("合集说明.md"):
            add(3, "", "系列", os.path.join("书md", fn), os.path.join("目录", "系列", fn),
                "卷序说明")
    meta["ocr_uids"] = sorted(set(ocr_uids))
    for i, r in enumerate(rows, 1):
        r["序号"] = i
    return rows, meta


def pre_move(path, rows):
    """阶段 2 的旧路径以阶段 1 的落点为前缀，回推到阶段 1 之前的位置。"""
    for r in rows:
        if r["阶段"] == 1 and path == r["新路径"] or \
                (r["阶段"] == 1 and path.startswith(r["新路径"] + os.sep)):
            return r["旧路径"] + path[len(r["新路径"]):]
    return path


def verify_plan(rows):
    bad = []
    src_rows = {r["src_path"] for r in L.read_csv(os.path.join(L.CAT, "源文件清单.csv"))}
    planned_src = {r["旧路径"] for r in rows if r["类别"] in ("源文件", "重复源")}
    if src_rows != planned_src:
        bad.append("源文件账对不上：清单 %d，计划 %d，缺 %s，多 %s"
                   % (len(src_rows), len(planned_src),
                      sorted(src_rows - planned_src)[:3], sorted(planned_src - src_rows)[:3]))
    inv = L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))
    want_md = sum(1 for r in inv if r.get("md_dir"))
    got_md = sum(1 for r in rows if r["类别"] == "md")
    if want_md != got_md:
        bad.append("md 计划 %d，清单 %d" % (got_md, want_md))
    targets = [r["新路径"] for r in rows]
    if len(targets) != len(set(targets)):
        dup = [t for t in set(targets) if targets.count(t) > 1][:5]
        bad.append("目标路径重复: %s" % dup)
    dev = None
    for r in rows:
        old = pre_move(r["旧路径"], rows)
        p = os.path.join(L.ROOT, old)
        if not os.path.exists(p):
            bad.append("源不存在: " + old)
        elif dev is None:
            dev = os.stat(p).st_dev
        elif os.stat(p).st_dev != dev:
            bad.append("跨设备: " + old)
        if os.path.exists(os.path.join(L.ROOT, r["新路径"])):
            bad.append("目标已存在: " + r["新路径"])
    if os.path.exists(os.path.join(L.LIB, "book.json")):
        bad.append("库/ 里已经有 book.json，先确认是不是已经搬过")
    if bad:
        print("计划校验不过：")
        for b in bad:
            print("  -", b)
        raise SystemExit(1)
    return True


def write_plan(rows, meta):
    L.write_csv(PLAN, rows, PLAN_HEADER)
    print("搬迁计划 %d 行 -> %s" % (len(rows), PLAN))
    for k in sorted(meta["kinds"]):
        print("   %-12s %d" % (k, meta["kinds"][k]))
    print("校验通过：源 %d 个路径对清单 %d 条，目标无重复、无已存在，同一设备，OCR 书 %s"
          % (sum(1 for r in rows if r["类别"] in ("源文件", "重复源")),
             len(L.read_csv(os.path.join(L.CAT, "源文件清单.csv"))),
             ",".join(meta["ocr_uids"])))


def read_plan():
    rows = L.read_csv(PLAN)
    if not rows:
        raise SystemExit("没有 %s，先跑 --plan" % PLAN)
    for r in rows:
        r["阶段"], r["序号"] = int(r["阶段"]), int(r["序号"])
    return rows


def move_rows(rows, direction="forward"):
    order = sorted(rows, key=lambda r: (r["阶段"], r["序号"]),
                   reverse=(direction == "reverse"))
    for r in order:
        src, dst = ((r["旧路径"], r["新路径"]) if direction == "forward"
                    else (r["新路径"], r["旧路径"]))
        sp, dp = os.path.join(L.ROOT, src), os.path.join(L.ROOT, dst)
        if not os.path.exists(sp):
            raise SystemExit("缺源，停：%s" % src)
        if os.path.exists(dp):
            raise SystemExit("目标已存在，停（不覆盖）：%s" % dst)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        os.rename(sp, dp)
    return order


def write_map():
    rows = read_plan()
    L.write_csv(MAP, rows, PLAN_HEADER)
    print("对照表 %d 行 -> %s" % (len(rows), MAP))


def db_awards():
    aw = defaultdict(list)
    if not os.path.isfile(os.path.join(L.CAT, "library.db")):
        return aw
    con = sqlite3.connect(DB_URI, uri=True)
    for uid, award, year, edition, result in con.execute(
            "SELECT uid,award,year,edition,result FROM book_award"):
        aw[uid].append({"award": award or "", "year": year or "", "edition": edition or "",
                        "result": result or ""})
    con.close()
    return aw


def write_book_jsons(rows):
    """给每本书写 book.json。源用书内相对路径（合集共享源会是 ../source/x）。"""
    ledger = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "分类表.csv"))}
    inv = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))}
    ocr_uids = {r["uid"] for r in rows if r["类别"] == "ocr_md"}
    src_dir = {r["旧路径"]: r["新路径"] for r in rows if r["类别"] == "源文件"}
    awards_db = db_awards()
    written = 0
    for uid, led in ledger.items():
        m = inv.get(uid)
        if not m:
            raise SystemExit("人账里的 %s 不在机器清单里" % uid)
        rel = L.book_relpath(led, m)
        bdir = os.path.join(L.LIB, rel)
        if not os.path.isdir(bdir):
            raise SystemExit("书目录不存在: %s (%s)" % (bdir, uid))
        src_new = src_dir.get(m.get("src_path", ""))
        if not src_new:
            raise SystemExit("找不到源去向: %s (%s)" % (m.get("src_path"), uid))
        sources = [{"file": os.path.relpath(os.path.join(L.ROOT, src_new), bdir),
                    "format": m.get("format", ""), "bytes": int(m.get("bytes") or 0),
                    "sha256": m.get("sha256", ""), "primary_src": 1}]
        awards = awards_db.get(uid) or [
            {"award": a.split(":", 1)[1], "year": "", "edition": "", "result": ""}
            for a in (m.get("备注") or "").split("；") if a.startswith("奖项:")]
        derived = []
        md_dir = os.path.join(bdir, "md")
        if os.path.isdir(md_dir):
            chapters, bytes_ = L.count_md_dir(md_dir)
            derived.append({"kind": "md", "status": "done",
                            "method": "mineru-ocr" if uid in ocr_uids else "ebook-to-md",
                            "chapters": chapters, "bytes": bytes_})
        html_dir = os.path.join(bdir, "html")
        if os.path.isdir(html_dir):
            pages = len([f for f in os.listdir(html_dir) if f.endswith(".html")])
            derived.append({"kind": "html", "status": "done",
                            "method": "chapter-html-versions", "chapters": pages, "bytes": 0})
        doc = L.book_doc(uid, led["正式书名"], m.get("title_clean", ""), m.get("作者", ""),
                         [led["一级"], led["二级"]], rel, m.get("系列", ""), m.get("卷序", ""),
                         m.get("容器", ""), led["保留"] or "keep", led["备注"],
                         sources, awards, derived)
        L.write_json_atomic(os.path.join(bdir, "book.json"), doc)
        written += 1
    print("book.json %d 份" % written)
    return written


def write_manifests():
    """只给 OCR 产物写 .manifest.json（低可信度必须可辨）；其余 md 默认 ebook-to-md。"""
    inv = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))}
    n = 0
    for row in read_plan():
        if row["类别"] != "ocr_md":
            continue
        uid = row["uid"]
        md_dir = os.path.join(L.ROOT, os.path.dirname(row["新路径"]))
        chapters, bytes_ = L.count_md_dir(md_dir)
        doc = {"uid": uid, "method": "mineru-ocr",
               "source_sha256": inv.get(uid, {}).get("sha256", ""),
               "chapters": chapters, "bytes": bytes_, "tool_version": "", "generated_at": "",
               "legacy": True, "written_by": "migrate_layout"}
        L.write_json_atomic(os.path.join(md_dir, ".manifest.json"), doc)
        n += 1
    print("OCR manifest %d 份" % n)
    return n


def build_views():
    """视图/奖项/<奖项>/<书名> -> 库/<path> 软链。"""
    if not os.path.isfile(os.path.join(L.CAT, "library.db")):
        raise SystemExit("先建库再建视图")
    con = sqlite3.connect(DB_URI, uri=True)
    awards = defaultdict(list)
    for uid, award, title, path in con.execute(
            "SELECT b.uid, ba.award, b.title, b.path FROM book b JOIN book_award ba ON ba.uid=b.uid"):
        awards[award].append((uid, title, path))
    con.close()
    root = os.path.join(L.ROOT, "视图", "奖项")
    made = 0
    for award, books in awards.items():
        adir = os.path.join(root, award)
        os.makedirs(adir, exist_ok=True)
        seen = set()
        for uid, title, path in books:
            name = L.path_safe(title)
            if name in seen:
                name = "%s (%s)" % (name, uid)
            seen.add(name)
            link = os.path.join(adir, name)
            if os.path.islink(link) or os.path.exists(link):
                os.unlink(link)
            os.symlink(os.path.relpath(os.path.join(L.ROOT, "库", path), adir), link)
            made += 1
    print("视图/奖项 软链 %d 条" % made)
    return made


def prune_empty():
    left = []
    failed = []
    for top in OLD_TOP:
        p = os.path.join(L.ROOT, top)
        if not os.path.isdir(p):
            continue
        for dirpath, _dirs, _files in os.walk(p, topdown=False):
            try:
                os.rmdir(dirpath)
            except OSError as e:
                failed.append((os.path.relpath(dirpath, L.ROOT), e.strerror))
        if os.path.isdir(p):
            n = sum(len(f) for _, _, f in os.walk(p))
            left.append((top, n))
    return left, failed


def rebuild_db():
    exe = [sys.executable, os.path.join(L.SCRIPTS, "build_library.py"), "--emit-json"]
    out = subprocess.run(exe, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("重建 DB 失败:\n" + out.stdout + out.stderr)
    print(out.stdout.strip())


def do_apply():
    rows = read_plan()
    verify_plan(rows)
    move_rows(rows, "forward")
    write_map()
    left, failed = prune_empty()
    if left:
        print("警告：搬空后仍有内容的旧目录:", left)
    if failed:
        print("警告：%d 个旧目录 rmdir 失败（前 3 个）: %s" % (len(failed), failed[:3]))
    write_manifests()
    write_book_jsons(rows)
    os.makedirs(os.path.join(L.LIB, "_收件箱"), exist_ok=True)
    rebuild_db()
    build_views()
    print("L3 执行完毕")


def do_reverse():
    rows = L.read_csv(MAP)
    if not rows:
        raise SystemExit("没有 " + MAP)
    for r in rows:
        r["阶段"], r["序号"] = int(r["阶段"]), int(r["序号"])
    keep = os.path.join(L.ROOT, "工作区", "中间态",
                        "reverse-trash-%s" % datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
    moved = 0
    for dirpath, _dirs, files in os.walk(L.LIB, topdown=False):
        for fn in files:
            if fn in ("book.json", ".manifest.json"):
                src = os.path.join(dirpath, fn)
                dst = os.path.join(keep, os.path.relpath(src, L.ROOT))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
                moved += 1
    if moved:
        print("--reverse 前把 %d 份 book.json/.manifest.json 移到 %s"
              % (moved, os.path.relpath(keep, L.ROOT)))
    failed = []
    for dirpath, _dirs, _files in os.walk(L.LIB, topdown=False):
        if dirpath != L.LIB:
            try:
                os.rmdir(dirpath)
            except OSError as e:
                failed.append((os.path.relpath(dirpath, L.ROOT), e.strerror))
    vroot = os.path.join(L.ROOT, "视图")
    if os.path.isdir(vroot):
        for dirpath, _dirs, files in os.walk(vroot, topdown=False):
            if dirpath == vroot:
                continue
            for fn in files:
                p = os.path.join(dirpath, fn)
                if os.path.islink(p):
                    os.unlink(p)
            try:
                os.rmdir(dirpath)
            except OSError as e:
                failed.append((os.path.relpath(dirpath, L.ROOT), e.strerror))
    if failed:
        print("警告：%d 个目录 rmdir 失败（前 3 个）: %s" % (len(failed), failed[:3]))
    move_rows(rows, "reverse")
    print("已反向搬回。再跑 build_library.py 可回到 proposed 状态")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--views", action="store_true")
    args = ap.parse_args()
    if args.plan:
        rows, meta = collect_plan()
        verify_plan(rows)
        write_plan(rows, meta)
    elif args.apply:
        do_apply()
    elif args.reverse:
        do_reverse()
    elif args.views:
        build_views()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
