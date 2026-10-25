#!/usr/bin/env python3
"""把人工复核的 verdict 与图片占比并回分诊表，出最终路线表 triage_final.csv 与不转清单 skip_list.tsv。

图片占比口径（先看纸面，没有纸面数据再看文本层）：
  扫描件    1 - 探针的 text_page_share（15 页抽样优先，其次 5 页）
  文本层书  1 - census 的 text_page_ratio（有文本层的页占比）

不转的判据（按操作人 2026-10-19 的决定）：
  设定集、画集、写真这类二级学科，一律不转；
  图片占比超过 50% 的，不转；
  复核判为图片书的，不转。
  其余按复核 verdict 走：文字书与混合进 OCR，本地抽文本保持原路线。
不转的书只记进 skip_list.tsv 与 triage_final.csv，不产出 md。

用法：
  python3 pdf_apply_review.py --batch 工作区/批次/pdf-普查-2026-10-19 [--per-day 20000]
"""
import argparse
import csv
import json
import os
from collections import Counter

ROOT = "/home/shike/文档/books"
SKIP_SUBJECT2 = {"设定集与美术集", "图册与写真", "漫画与画集"}
VERDICT_ROUTE = {"文字书": "OCR", "混合": "OCR", "图片书": "不转-记图册", "读不出": "人工确认"}


def load_verdicts(*dirs):
    out = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                v = json.load(open(os.path.join(d, name), encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if v.get("uid"):
                out[v["uid"]] = v
    return out


def load_jsonl(path):
    if not os.path.exists(path):
        return {}
    return {r["uid"]: r for r in (json.loads(l) for l in open(path, encoding="utf-8"))}


def text_share(uid, census, probes):
    """有文本层的页占比，优先用纸面探针（页数抽样），其次用文本层统计。"""
    for p in probes:
        r = p.get(uid)
        if r and r.get("text_page_share") is not None:
            return r["text_page_share"], "探针 %d 页抽样" % len(r.get("sampled") or [])
    ts = census.get(uid, {}).get("text_page_ratio")
    if ts is not None:
        return ts, "文本层逐页统计"
    return None, "无数据"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="工作区/批次/pdf-普查-2026-10-19")
    ap.add_argument("--per-day", type=int, default=20000)
    a = ap.parse_args()
    bp = os.path.join(ROOT, a.batch)
    rows = list(csv.DictReader(open(os.path.join(bp, "triage.csv"), encoding="utf-8")))
    verdicts = load_verdicts(os.path.join(bp, "review"), os.path.join(bp, "review_pic"))
    census = load_jsonl(os.path.join(bp, "census.jsonl"))
    probes = [load_jsonl(os.path.join(bp, f)) for f in
              ("scan_probe_pic15.jsonl", "scan_probe2.jsonl", "scan_probe_enc.jsonl")]

    skip = []
    for r in rows:
        uid = r["uid"]
        v = verdicts.get(uid) or {}
        r["verdict"] = v.get("verdict", "")
        ts, how = text_share(uid, census, probes)
        share_img = None if ts is None else round(1 - ts, 2)
        r["image_share"] = "" if share_img is None else share_img
        r["measure"] = how
        r["route_metric"] = r["route"]

        why = None
        vd = v.get("verdict")
        # 复核是看样页得出的直接证据，比纸面指标可信：纸面探针会把稀疏排版的诗歌页算成图片页，
        # 所以判为文字书的书不被图片占比推开。
        if vd == "文字书":
            r["route"] = "OCR"
            r["note"] = "%s；复核判为文字书（图占比指标 %s 不可信）" % (r["note"], share_img)
            continue
        if r["subject2"] in SKIP_SUBJECT2:
            why = "二级学科是 %s" % r["subject2"]
        elif vd == "混合" and share_img is not None and share_img > 0.5:
            why = "复核判为混合且图片占比 %d%% 超过一半" % round(share_img * 100)
        elif vd != "混合" and share_img is not None and share_img > 0.5:
            why = "无复核结论，图片占比 %d%% 超过一半" % round(share_img * 100)
        elif vd == "图片书":
            why = "复核判为图片书"
        if why:
            r["route"] = "不转-记图册"
            r["note"] = ("%s；不转：%s；复核：%s"
                         % (r["note"], why, (v.get("evidence") or "")[:60])).strip("；")
            skip.append({"uid": uid, "title": r["title"], "subject1": r["subject1"],
                         "subject2": r["subject2"], "pages": r["pages"], "mb": r["mb"],
                         "image_share": r["image_share"], "why": why,
                         "verdict": r["verdict"], "route_metric": r["route_metric"]})
            continue
        if v.get("verdict") in VERDICT_ROUTE and r["route"] != "OCR":
            r["route"] = VERDICT_ROUTE[v["verdict"]]
            r["note"] = "%s；复核：%s" % (r["note"], (v.get("evidence") or "")[:60])

    out = os.path.join(bp, "triage_final.csv")
    cols = list(rows[0])
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join('"%s"' % str(r[c]).replace('"', "'") for c in cols) + "\n")
    os.replace(out + ".tmp", out)

    sl = os.path.join(bp, "skip_list.tsv")
    with open(sl + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("uid\ttitle\tsubject1\tsubject2\tpages\tmb\timage_share\twhy\tverdict\troute_metric\n")
        for s in sorted(skip, key=lambda x: (x["subject2"], -(int(x["pages"] or 0)))):
            fh.write("\t".join(str(s[k]) for k in
                               ("uid", "title", "subject1", "subject2", "pages", "mb",
                                "image_share", "why", "verdict", "route_metric")) + "\n")
    os.replace(sl + ".tmp", sl)

    print("== 复核覆盖 %d/%d 本" % (len(verdicts), len(rows)))
    print("== 最终路线")
    for k, v in Counter(r["route"] for r in rows).most_common():
        g = [r for r in rows if r["route"] == k]
        print("  %-12s %4d 本 %7d 页" % (k, v, sum(int(r["pages"]) for r in g)))
    ocr = [r for r in rows if r["route"] == "OCR"]
    pages = sum(int(r["pages"]) for r in ocr)
    print("\nOCR 队列 %d 本 %d 页 = %.1f 天（%d 页/天）" % (len(ocr), pages, pages / a.per_day, a.per_day))
    small = [r for r in ocr if int(r["pages"]) <= 300]
    print("其中 ≤300 页的 %d 本 %d 页 = %.1f 天"
          % (len(small), sum(int(r["pages"]) for r in small),
             sum(int(r["pages"]) for r in small) / a.per_day))
    print("\n不转 %d 本，按原因：" % len(skip))
    for k, v in Counter(s["why"] for s in skip).most_common():
        g = [s for s in skip if s["why"] == k]
        print("  %-24s %3d 本 %6d 页" % (k.replace("图片占比", "图占比"), v, sum(int(s["pages"]) for s in g)))
    print("\nCSV: %s\n不转清单: %s" % (os.path.relpath(out, ROOT), os.path.relpath(sl, ROOT)))


if __name__ == "__main__":
    main()
