#!/usr/bin/env python3
"""B4 证据重做：热缓存端到端 + 同一组章的停止线。只读，不写库；会调模型。"""
import csv
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import book_expand as be
import book_score

OUT = os.path.join(HERE, "live", "B4")
rows = {r["qid"]: r for r in csv.DictReader(
    open(os.path.join(HERE, "book_gold.tsv"), encoding="utf-8"), delimiter="\t")}


def q(qid):
    return " ".join(t.strip() for t in rows[qid]["terms"].split(",") if t.strip())


def main():
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect("file:%s?mode=ro" % be.DB, uri=True)
    meta = book_score.load_meta(be.LIBDB)
    exp = book_score.load_expand(con)
    # 1) 热缓存：同一查询连跑两次，记录端到端 ms 与 calls
    runs = []
    for i in range(2):
        t0 = time.monotonic()
        res = be.run(q("O01"), "default", con=con, meta=meta, expand=exp, use_cache=True)
        runs.append({"run": i + 1, "ms": round((time.monotonic() - t0) * 1000, 1),
                     "calls": res["calls"], "rounds": res["rounds"],
                     "cached": res["cached"], "partial": res["partial"],
                     "chapters": [{"path": c["path"], "calls": c["calls"],
                                   "cached": c["cached"], "status": c["status"],
                                   "reason": c.get("reason", ""),
                                   "window_chars": c.get("window_chars", 0)}
                                  for c in res["chapters"]]})
    with open(os.path.join(OUT, "cache.json"), "w", encoding="utf-8") as fh:
        json.dump({"query": q("O01"), "runs": runs}, fh, ensure_ascii=False, indent=1)

    # 2) 停止线：同一批 10 题，取 default 实际选中的 3 本书/章，开/关用同一组
    per = []
    for n in range(1, 11):
        qid = "O%02d" % n
        base = be.collect_units(q(qid), con, meta, exp, "default")
        chaps = set(rows[qid]["target_chapters"].split("|"))
        hit = 0
        for b in base["books"]:
            for c in b["selected"]:
                if c["chapter_path"] in chaps:
                    hit = 1
        per.append({"qid": qid, "books": len(base["books"]),
                    "chapters": sum(len(b["selected"]) for b in base["books"]),
                    "hit_same_set": hit})
    total = sum(r["hit_same_set"] for r in per)
    with open(os.path.join(OUT, "stopline-same.tsv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["qid", "books", "chapters", "hit_same_set"],
                           delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(per)
    with open(os.path.join(OUT, "stopline-same.json"), "w", encoding="utf-8") as fh:
        json.dump({"n": len(per), "hit": total, "delta_points": 0,
                   "note": "开/关使用同一组已选章；扩展只加摘要，不改章集合，"
                           "所以证据章 hit 差值结构性为 0，不能拿 10 点线判扩展。"},
                  fh, ensure_ascii=False, indent=1)
    con.close()
    print(json.dumps({"cache": runs, "stopline": {"n": len(per), "hit": total}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
