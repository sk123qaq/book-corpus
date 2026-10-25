#!/usr/bin/env python3
"""重建被二次运行冲掉的 工作区/批次/<批次>/worklist.tsv（批次范围账本）。

背景：`split_md_batch.py` 的 `build_worklist()` 每次运行都从 `v_pending_md` 重写
worklist.tsv。批次跑完后那些书都有 md 了，于是任何一次 `--report` 都把 656 行清单
压成只剩表头。本脚本按批次自己的证据（`logs/<uid>.json` 的 uid 集合）重建它，并把
重建结果与 logs 逐本交叉核对；对不上就不写盘。

  restore_worklist.py --batch md拆分-2026-10-18            # 只核对，不写
  restore_worklist.py --batch md拆分-2026-10-18 --apply     # 写盘（旧文件改名留档）
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

FORMATS = ("epub", "mobi", "azw3", "azw")
COLS = ["uid", "format", "sha256", "path", "title", "src"]


def batch_logs(logs):
    out = {}
    for name in sorted(os.listdir(logs)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(logs, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        out[rec["uid"]] = rec
    return out


def db_rows(con, uids):
    con.row_factory = sqlite3.Row
    rows = []
    for uid in uids:
        r = con.execute(
            """SELECT b.uid AS uid, s.format AS format, s.sha256 AS sha256,
                      b.path AS path, b.title AS title, s.file AS src
               FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
               WHERE b.uid=? AND s.format IN (?,?,?,?)""", (uid, *FORMATS)).fetchone()
        rows.append(dict(r) if r else None)
    rows.sort(key=lambda d: int(d["uid"][1:]) if d and d["uid"][1:].isdigit() else 0)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--apply", action="store_true", help="写盘；不带则只核对")
    a = ap.parse_args()

    batch = os.path.join(L.WORK, "批次", a.batch)
    logs = batch_logs(os.path.join(batch, "logs"))
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    rows = db_rows(con, sorted(logs))
    print("批次 %s：logs %d 本，DB 取到 %d 本" % (a.batch, len(logs), sum(1 for r in rows if r)))

    byuid = {r["uid"]: r for r in rows if r}
    missing = [u for u in sorted(logs) if u not in byuid]
    if missing:
        sys.exit("DB 里找不到这些 uid 的 epub/mobi/azw3 主源，不写盘：%s" % " ".join(missing))

    bad = []
    for r in rows:
        rec = logs[r["uid"]]
        if r["format"] != rec.get("format"):
            bad.append((r["uid"], "format %s != logs %s" % (r["format"], rec.get("format"))))
        if r["title"] != rec.get("title"):
            bad.append((r["uid"], "title 与 logs 不符"))
        if "库/%s/md" % r["path"] != rec.get("dest"):
            bad.append((r["uid"], "dest %s != 库/%s/md" % (rec.get("dest"), r["path"])))
    if bad:
        for uid, why in bad:
            print("  不符 %s %s" % (uid, why))
        sys.exit("与 logs 交叉核对有 %d 处不符，不写盘" % len(bad))
    print("与 logs 交叉核对通过：format / title / dest 三列 %d 本全对" % len(rows))

    text = "\t".join(COLS) + "\n" + "".join(
        "\t".join(str(r[c]) for c in COLS) + "\n" for r in rows)
    path = os.path.join(batch, "worklist.tsv")
    have = None
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            have = fh.read()
    print("盘上现有 worklist.tsv %d 行，重建后 %d 行" %
          (0 if have is None else len(have.splitlines()), len(text.splitlines())))
    if have == text:
        print("内容已与重建结果一致，无需写盘")
        return 0
    if not a.apply:
        print("（未写盘；加 --apply 写）")
        return 0
    if have is not None:
        stamp = datetime.datetime.now().astimezone().replace(
            microsecond=0).strftime("%Y%m%dT%H%M%S%z")
        old = "%s.clobbered-%s" % (path, stamp)
        os.replace(path, old)
        print("旧文件改名留档 %s" % os.path.relpath(old, L.WORK))
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)
    print("已写 %s（%d 行）" % (os.path.relpath(path, L.ROOT), len(text.splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
