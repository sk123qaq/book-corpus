#!/usr/bin/env python3
"""把 split_collection.py 的逐册产物装进 库/<path>/md/，旧版本改名留在批次区。

split_md_batch.py 只按单本书跑，合集（一个源文件拆多本）没有对应的进库路径。
本脚本补这一段：map 的每本书带 uid，按 uid 查 DB 拿路径，旧 md 改名到
<批次>/out/<uid>.prev-<stamp> 后把 split 产物搬进去，并写 .manifest.json。

幂等：目标已有本批次同一 map 的 manifest 且未 --force 时跳过。

用法: python3 install_collection.py --batch 旧md重拆-2026-10-18 \
        --map 工作区/批次/旧md重拆-2026-10-18/collections/渡边淳一.json \
        --split-out 工作区/批次/旧md重拆-2026-10-18/collections/渡边淳一/out
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

COUNT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]")


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tool_version():
    convert = os.path.join(L.skill_dir("ebook-to-md"), "scripts", "ebook_to_md.py")
    return "sha256:" + sha256_file(convert)[:12]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--map", required=True, dest="map_path")
    ap.add_argument("--split-out", required=True)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    root = L.ROOT
    batch = os.path.join(L.WORK, "批次", a.batch)
    map_path = os.path.join(root, a.map_path) if not os.path.isabs(a.map_path) else a.map_path
    split_out = os.path.join(root, a.split_out) if not os.path.isabs(a.split_out) else a.split_out
    spec = json.load(open(map_path, encoding="utf-8"))
    con = sqlite3.connect("file:%s?mode=ro" % L.PATHS.library_db, uri=True)
    con.row_factory = sqlite3.Row
    os.makedirs(os.path.join(batch, "out"), exist_ok=True)
    stamp = now().replace(":", "").replace("+", "-")
    map_rel = os.path.relpath(map_path, root)

    for bk in spec["books"]:
        uid = bk.get("uid")
        if not uid:
            sys.exit("map book without uid: %r" % bk.get("labels"))
        folder = os.path.join(split_out, bk["folder"])
        if not os.path.isdir(folder):
            sys.exit("split output missing: %s" % folder)
        r = con.execute(
            """SELECT b.path AS path, b.title AS title, s.file AS src, s.sha256 AS sha256,
                      s.format AS format
               FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
               WHERE b.uid=?""", (uid,)).fetchone()
        if r is None:
            sys.exit("uid not found in DB: %s" % uid)
        dest = os.path.join(root, "books", r["uid"], "md")
        man_old = L.read_md_manifest(dest)
        if (not a.force and man_old.get("batch") == a.batch
                and man_old.get("map") == map_rel):
            print("skip %s: 已是本批次同一 map 的产物" % uid)
            continue
        prev = None
        if os.path.isdir(dest):
            prev = os.path.join(batch, "out", "%s.prev-%s" % (uid, stamp))
            os.rename(dest, prev)
            print("old %s -> %s" % (os.path.relpath(dest, root), os.path.relpath(prev, root)))
        try:
            shutil.move(folder, dest)
        except Exception as exc:
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            if prev and not os.path.isdir(dest):
                os.rename(prev, dest)
                print("回滚：%s 恢复旧 md" % uid)
            sys.exit("安装失败 %s：%s: %s" % (uid, type(exc).__name__, exc))
        chapters, nbytes = L.count_md_dir(dest)
        chars = 0
        for fn in sorted(os.listdir(dest)):
            if fn.endswith(".md"):
                chars += len(COUNT_RE.findall(open(os.path.join(dest, fn),
                                                   encoding="utf-8").read()))
        images = 0
        img_dir = os.path.join(dest, "images")
        if os.path.isdir(img_dir):
            images = len([n for n in os.listdir(img_dir)
                          if os.path.isfile(os.path.join(img_dir, n))])
        manifest = {
            "uid": uid, "method": "split_collection", "tool": "split_collection.py",
            "tool_version": tool_version(), "batch": a.batch,
            "source": r["src"], "source_sha256": r["sha256"], "source_format": r["format"],
            "map": map_rel, "collection": spec.get("collection", ""),
            "chapters": chapters, "bytes": nbytes, "chars": chars, "images": images,
            "generated_at": now(), "verified": False,
            "accepted": "",   # 本脚本不做验收；验证走 collection_coverage.py / redo_compare.py
        }
        with open(os.path.join(dest, ".manifest.json") + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(os.path.join(dest, ".manifest.json") + ".tmp",
                   os.path.join(dest, ".manifest.json"))
        print("installed %s %s  章 %d 字 %d 图 %d" % (uid, r["title"], chapters, chars, images))
    return 0


if __name__ == "__main__":
    sys.exit(main())
