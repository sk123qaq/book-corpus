#!/usr/bin/env python3
"""把 census 与两个探针合成一张分诊表：每本书走哪条转换路线。只读输入，写 triage.csv。

路线规则（阈值都在这里，改完重跑即可）：
  文本层书（A/B/B2）        -> 本地抽文本（pdftotext/pymupdf），不花 OCR 额度
  扫描件 text_page_share>=0.6 -> MinerU OCR 队列
  扫描件 0.2-0.6            -> OCR 但复看（多为正文+图版混排，抽样会漏判）
  扫描件 <0.2 且学科偏文字    -> 复看（竖排/稀疏排版的诗歌等会被带数判低）
  扫描件 <0.2 且学科偏图      -> 图册，不转 md

用法：
  python3 pdf_triage.py --batch 工作区/批次/pdf-普查-2026-10-19 [--csv 路径]
"""
import argparse
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
ROOT = L.ROOT
TEXT_SUBJECTS = {"文学", "人文社科", "科学技术", "商业与管理", "写作与编剧", "影视动画漫画"}
PICTURE_SUBJECT2 = {"图册与写真", "设定集与美术集", "漫画与画集", "绘画与概念设计"}


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="工作区/批次/pdf-普查-2026-10-19")
    ap.add_argument("--csv")
    ap.add_argument("--per-day", type=int, default=20000, help="日额度页数，按平台硬额度算排期")
    a = ap.parse_args()
    bp = os.path.join(ROOT, a.batch)
    census = {r["uid"]: r for r in load(os.path.join(bp, "census.jsonl"))}
    probes = {}
    for f in ("scan_probe2.jsonl", "scan_probe_enc.jsonl"):
        p = os.path.join(bp, f)
        if os.path.exists(p):
            for r in load(p):
                probes[r["uid"]] = r
    import sys
    sys.path.insert(0, os.path.join(ROOT, "工作区/脚本"))
    from pdf_census import classify

    rows = []
    for uid, c in sorted(census.items()):
        cls = classify(c)
        p = probes.get(uid, {})
        share = p.get("text_page_share")
        subj1, subj2 = c.get("subject1") or "", c.get("subject2") or ""
        if cls.startswith(("A", "B")):
            route, why = "本地抽文本", cls
        elif "missing" in " ".join(c.get("errors") or []):
            route, why = "源文件缺失", "文件不在磁盘"
        elif share is None:
            route, why = "人工确认", "无探针数据"
        elif share >= 0.6:
            route, why = "OCR", "抽样 %.0f%% 页是文字页" % (share * 100)
        elif share >= 0.2:
            route, why = "OCR-复看", "抽样 %.0f%% 页是文字页，正文与图版混排" % (share * 100)
        elif subj1 in TEXT_SUBJECTS and subj2 not in PICTURE_SUBJECT2:
            route, why = "复看-可能OCR", "抽样低但学科是 %s，竖排或稀疏版式会被判低" % subj1
        else:
            route, why = "图册不转", "抽样无文字页，%s/%s" % (subj1, subj2)
        rows.append({
            "uid": uid, "title": c["title"], "subject1": subj1, "subject2": subj2,
            "class": cls, "route": route, "pages": c.get("pages") or 0,
            "mb": round((c.get("bytes") or 0) / 1e6, 1), "ppi": c.get("img_ppi_median") or "",
            "text_page_share": "" if share is None else share,
            "ink": p.get("ink_median", ""), "bands": p.get("bands_median", ""),
            "note": why,
        })

    out = a.csv or os.path.join(bp, "triage.csv")
    if not rows:
        raise SystemExit("分诊表为空：%s 里没有一条记录" % os.path.join(bp, "census.jsonl"))
    cols = list(rows[0])
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join('"%s"' % str(r[c]).replace('"', "'") for c in cols) + "\n")
    os.replace(out + ".tmp", out)

    print("== 路线分布（%d 本）" % len(rows))
    for k, v in Counter(r["route"] for r in rows).most_common():
        pg = sum(r["pages"] for r in rows if r["route"] == k)
        mb = sum(r["mb"] for r in rows if r["route"] == k)
        print("  %-14s %4d 本  %7d 页  %7.1f MB" % (k, v, pg, mb))
    print("\n== OCR 队列按学科（页数是 OCR 额度，摊到每天 %d 页）" % a.per_day)
    q = [r for r in rows if r["route"] in ("OCR", "OCR-复看")]
    for s, v in Counter(r["subject1"] for r in q).most_common():
        g = [r for r in q if r["subject1"] == s]
        pg = sum(r["pages"] for r in g)
        print("  %-12s %4d 本 %7d 页 = %4.1f 天" % (s, len(g), pg, pg / a.per_day))
    print("  合计 %d 本 %d 页 = %.1f 天（%d 页/天 高优先额度）"
          % (len(q), sum(r["pages"] for r in q), sum(r["pages"] for r in q) / a.per_day,
             a.per_day))
    print("\n== 本地抽文本 的书")
    for r in rows:
        if r["route"] == "本地抽文本":
            print("  %-6s %-34s %5d 页 %6.1f MB %s" % (r["uid"], r["title"][:34], r["pages"], r["mb"], r["class"]))
    print("\n== 图册不转（前 20）")
    pic = [r for r in rows if r["route"] == "图册不转"]
    for r in pic[:20]:
        print("  %-6s %-34s %5d 页 %s" % (r["uid"], r["title"][:34], r["pages"], r["subject2"] or r["subject1"]))
    print("  共 %d 本 %d 页" % (len(pic), sum(r["pages"] for r in pic)))
    print("\nCSV:", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
