#!/usr/bin/env python3
"""扫描件的纸面性质探针：抽样页看是「文字页」还是「图片页」，只读。

只对 census 判为 C/C2 的书跑。每本抽 N 页，用 pdftoppm 70dpi 灰度渲染，
按 MinerU 技能 page_health.py 同一类指标算 ink（暗像素占比）与 bands（文字行带数），
阈值按本库实测调过：
  文字页：ink 0.4%-35% 且 bands >= 8
  图片页：其余（照片、整页图版、封面、空白）

输出一行一本书的 JSONL，含 text_page_share，用来把 census 判为 C/C2 的扫描件里
「值得 OCR 的扫描文字书」与「图册/写真/画册」分开。跑法同 census，纯 poppler + numpy + PIL。
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L

ROOT = L.ROOT
MIN_INK, MAX_INK, MIN_BANDS = 0.004, 0.35, 8


def _bands(profile):
    n, in_band = 0, False
    for r in profile:
        if r >= 0.03 and not in_band:
            n += 1
            in_band = True
        elif r < 0.03:
            in_band = False
    return n


def ink_bands(png):
    """(ink, 行带数, 列带数)。行带取横排文字行，列带取竖排文字的列。"""
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(png).convert("L"), dtype=np.float32) / 255.0
    dark = a < 0.62
    return float(dark.mean()), _bands(dark.mean(axis=1)), _bands(dark.mean(axis=0))


def sample_pages(pages, n=5):
    if pages <= n:
        return list(range(1, pages + 1))
    if n == 5:
        picks = [max(1, min(pages, int(pages * f))) for f in (0.15, 0.3, 0.5, 0.7, 0.85)]
    else:
        picks = [max(1, min(pages, int(pages * (0.08 + 0.84 * i / (n - 1))))) for i in range(n)]
    return sorted(set(picks))


def probe(relpath, pages, sample=5):
    abspath = os.path.join(ROOT, relpath)
    tmp = tempfile.mkdtemp()
    out = {"relpath": relpath, "sampled": [], "errors": []}
    try:
        for p in sample_pages(pages, sample):
            rc = subprocess.run(
                ["pdftoppm", "-r", "70", "-gray", "-png", "-f", str(p), "-l", str(p), abspath, os.path.join(tmp, "p")],
                capture_output=True,
                timeout=180,
            ).returncode
            pngs = [f for f in os.listdir(tmp) if f.endswith(".png")]
            if rc != 0 or not pngs:
                out["errors"].append("page %d render rc=%d" % (p, rc))
                continue
            ink, bands_r, bands_c = ink_bands(os.path.join(tmp, pngs[0]))
            for f in pngs:
                os.remove(os.path.join(tmp, f))
            out["sampled"].append(
                {
                    "page": p,
                    "ink": round(ink, 4),
                    "bands_row": bands_r,
                    "bands_col": bands_c,
                    "text_page": bool(MIN_INK <= ink <= MAX_INK and max(bands_r, bands_c) >= MIN_BANDS),
                }
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(out["sampled"])
    if n:
        out["ink_median"] = round(statistics.median(s["ink"] for s in out["sampled"]), 4)
        out["bands_median"] = statistics.median(max(s["bands_row"], s["bands_col"]) for s in out["sampled"])
        out["text_page_share"] = round(sum(s["text_page"] for s in out["sampled"]) / n, 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kinds", default="C,C2")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--uid", action="append", help="只跑这些 uid（重跑个别本用）")
    ap.add_argument("--sample", type=int, default=5, help="每本抽几页（图册复核用更大值）")
    a = ap.parse_args()
    from pdf_census import classify

    with open(a.census, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    kinds = tuple(a.kinds.split(","))
    if a.uid:
        rows = [r for r in rows if r["uid"] in set(a.uid)]
    want = [r for r in rows if any(classify(r).startswith(k) for k in kinds)]
    if a.limit:
        want = want[: a.limit]
    print("待探 %d 本" % len(want), file=sys.stderr)

    def job(r):
        try:
            p = probe(r["relpath"], r.get("pages") or 1, a.sample)
        except Exception as e:
            p = {"relpath": r["relpath"], "errors": ["%s: %s" % (type(e).__name__, e)]}
        p.update(
            {
                "uid": r["uid"],
                "title": r["title"],
                "subject1": r["subject1"],
                "subject2": r["subject2"],
                "pages": r.get("pages"),
                "bytes": r["bytes"],
                "ppi": r.get("img_ppi_median"),
            }
        )
        return p

    with open(a.out + ".tmp", "w", encoding="utf-8") as fh, ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(job, r) for r in want]
        for i, f in enumerate(futs, 1):
            p = f.result()
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            print(
                "[%d/%d] %s share=%s ink=%s bands=%s %s"
                % (
                    i,
                    len(want),
                    p["uid"],
                    p.get("text_page_share"),
                    p.get("ink_median"),
                    p.get("bands_median"),
                    (p.get("errors") or [""])[0],
                ),
                file=sys.stderr,
            )
    os.replace(a.out + ".tmp", a.out)
    print(a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
