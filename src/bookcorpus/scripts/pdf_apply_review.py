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

模型建议只进 model_route/model_confidence/model_reason/model_at 四列，永不改 route：
route 是人工复核的结果，模型是参考。四列数据来自复核 JSON 的 model 对象。

用法：
  python3 pdf_apply_review.py --batch 工作区/批次/pdf-普查-2026-10-19 [--per-day 20000]
  python3 pdf_apply_review.py --batch 工作区/批次/pdf-ocr-ops-<日期> \
      --triage <源分诊表.csv> --out-dir <新批次目录>
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
ROOT = L.ROOT
SKIP_SUBJECT2 = {"设定集与美术集", "图册与写真", "漫画与画集"}
VERDICT_ROUTE = {"文字书": "OCR", "混合": "OCR", "图片书": "不转-记图册", "读不出": "人工确认"}
HUMAN_ROUTES = {"OCR", "本地抽文本", "不转-记图册", "人工确认", "日文暂缓"}
MODEL_COLS = ["model_route", "model_confidence", "model_reason", "model_at"]
SKIP_COLS = ["uid", "title", "subject1", "subject2", "pages", "mb", "image_share",
             "why", "verdict", "route_metric"]
REASON_MAX = 200


def load_verdicts(*dirs):
    """返回 (uid -> 复核记录, 坏 JSON 文件数)。"""
    out, bad = {}, 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                v = json.load(open(os.path.join(d, name), encoding="utf-8"))
            except json.JSONDecodeError:
                bad += 1
                continue
            if v.get("uid"):
                out[v["uid"]] = v
    return out, bad


def model_cols(verdict):
    """复核 JSON 的 model 对象 -> 四列。没有就用空串，不编造。"""
    m = verdict.get("model") or {}
    reason = " ".join(str(m.get("reason") or "").split())[:REASON_MAX]
    conf = m.get("confidence")
    return {"model_route": m.get("route") or "",
            "model_confidence": "" if conf is None else conf,
            "model_reason": reason,
            "model_at": m.get("at") or ""}


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
    ap.add_argument("--triage", help="输入分诊表，默认 <批次>/triage.csv，其次 <批次>/triage_final.csv")
    ap.add_argument("--out-dir", dest="out_dir", help="写账目录，默认批次目录")
    ap.add_argument("--review", action="append",
                    help="复核 JSON 目录，可多次给；默认 <批次>/review 与 <批次>/review_pic")
    ap.add_argument("--per-day", type=int, default=20000)
    a = ap.parse_args()
    bp = os.path.join(ROOT, a.batch)
    out_dir = os.path.abspath(a.out_dir) if a.out_dir else bp
    triage = a.triage or next(
        (p for p in (os.path.join(bp, "triage.csv"), os.path.join(bp, "triage_final.csv"))
         if os.path.exists(p)), "")
    rows = list(csv.DictReader(open(triage, encoding="utf-8"))) if triage else []
    if not rows:
        sys.exit("分诊表没有数据行: %s" % (triage or "<批次>/triage.csv 不存在"))
    review_dirs = a.review or [os.path.join(bp, "review"), os.path.join(bp, "review_pic")]
    verdicts, bad_verdicts = load_verdicts(*review_dirs)
    # 分诊表在别的批次时（ops 新批次指着普查表），census 与探针也跟着分诊表走
    data_dir = os.path.dirname(os.path.abspath(triage)) if a.triage else bp
    census = load_jsonl(os.path.join(data_dir, "census.jsonl"))
    probes = [load_jsonl(os.path.join(data_dir, f)) for f in
              ("scan_probe_pic15.jsonl", "scan_probe2.jsonl", "scan_probe_enc.jsonl",
               "scan.jsonl")]

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
        r.update(model_cols(v))

        human = (v.get("route") or "").strip()
        if human in HUMAN_ROUTES:
            r["verdict"] = v.get("verdict") or human
            r["route"] = human
            why_human = (v.get("reason") or v.get("evidence") or "").strip()[:REASON_MAX]
            parts = [r["note"], "人工确认 route=%s" % human]
            if why_human:
                parts.append(why_human)
            r["note"] = "；".join(x for x in parts if x)
            if human == "不转-记图册":
                label = ("复核判为%s" % r["verdict"]) if r["verdict"] else "人工确认不转"
                why = "；".join(x for x in (label, why_human) if x)
                skip.append({"uid": uid, "title": r["title"], "subject1": r["subject1"],
                             "subject2": r["subject2"], "pages": r["pages"], "mb": r["mb"],
                             "image_share": r["image_share"],
                             "why": why,
                             "verdict": r["verdict"], "route_metric": r["route_metric"]})
            continue

        why = None
        vd = v.get("verdict")
        # 复核是看样页得出的直接证据，比纸面指标可信：纸面探针会把稀疏排版的诗歌页算成图片页，
        # 所以判为文字书的书不被图片占比推开。
        if r["subject2"] in SKIP_SUBJECT2:
            why = "二级学科是 %s" % r["subject2"]
        elif vd == "文字书":
            r["route"] = "OCR"
            r["note"] = "%s；复核判为文字书（图占比指标 %s 不可信）" % (r["note"], share_img)
            continue
        elif vd == "图片书":
            why = "复核判为图片书"
        elif vd == "混合" and share_img is not None and share_img > 0.5:
            why = "复核判为混合且图片占比 %d%% 超过一半" % round(share_img * 100)
        elif share_img is not None and share_img > 0.5:
            why = "无复核结论，图片占比 %d%% 超过一半" % round(share_img * 100)
        if why:
            r["route"] = "不转-记图册"
            ev = (v.get("evidence") or "").strip()[:60]
            parts = [r["note"], "不转：%s" % why] + (["复核：%s" % ev] if ev else [])
            r["note"] = "；".join(p for p in parts if p).strip("；")
            skip.append({"uid": uid, "title": r["title"], "subject1": r["subject1"],
                         "subject2": r["subject2"], "pages": r["pages"], "mb": r["mb"],
                         "image_share": r["image_share"], "why": why,
                         "verdict": r["verdict"], "route_metric": r["route_metric"]})
            continue
        if v.get("verdict") in VERDICT_ROUTE and r["route"] != "OCR":
            r["route"] = VERDICT_ROUTE[v["verdict"]]
            r["note"] = "%s；复核：%s" % (r["note"], (v.get("evidence") or "")[:60])

    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "triage_final.csv")
    cols = list(rows[0]) + [c for c in MODEL_COLS if c not in rows[0]]
    with open(out + ".tmp", "w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(cols) + "\n")
        w = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator="\n")
        for r in rows:
            w.writerow([str(r.get(c, "")) for c in cols])
    os.replace(out + ".tmp", out)

    sl = os.path.join(out_dir, "skip_list.tsv")
    with open(sl + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\t".join(SKIP_COLS) + "\n")
        for s in sorted(skip, key=lambda x: (x["subject2"], -(int(x["pages"] or 0)))):
            fh.write("\t".join(str(s[k]) for k in SKIP_COLS) + "\n")
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
    agree = sum(1 for r in rows if r["model_route"]
                and r["model_route"] == r["route"])
    judged = sum(1 for r in rows if r["model_route"])
    print("模型建议 %d 本，与人工 route 一致 %d 本" % (judged, agree))
    print("坏复核 JSON %d 个（已跳过）" % bad_verdicts)


if __name__ == "__main__":
    main()
