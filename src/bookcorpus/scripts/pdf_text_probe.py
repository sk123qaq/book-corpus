#!/usr/bin/env python3
"""文本层 PDF 的深测：目录书签、逐页文本分布、乱码率、字号分布。只读。

跑 census 里判为 A/B 类的书，回答三件事：
  1. 这本书有没有 PDF 目录（决定能不能照书自身的章节切文件）；
  2. 文本层是不是全文覆盖（还是只有部分页）；
  3. 抽出来的字可不可信（无 ToUnicode 的 CID 字体会出乱码或私用区字符）。

需要 pymupdf，用带它的解释器跑，例如：
  工作区/.venv-pdf/bin/python 工作区/脚本/pdf_text_probe.py --census <census.jsonl> --out probe.jsonl
"""
import argparse
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
ROOT = L.ROOT
BAD_RE = re.compile(r"[\ufffd\ue000-\uf8ff\x00-\x08\x0b\x0e-\x1f]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def probe(relpath):
    import pymupdf
    out = {"relpath": relpath}
    doc = pymupdf.open(os.path.join(ROOT, relpath))
    try:
        toc = doc.get_toc(simple=True)
        out["toc_entries"] = len(toc)
        out["toc_levels"] = len({t[0] for t in toc})
        out["toc_sample"] = [t[1][:40] for t in toc[:5]]
        sizes = {}
        per_page, bad, cjk, total = [], 0, 0, 0
        images = 0
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            t = page.get_text("text")
            per_page.append(len(t.strip()))
            total += len(t)
            bad += len(BAD_RE.findall(t))
            cjk += len(CJK_RE.findall(t))
            images += len(page.get_images(full=True))
            for b in page.get_text("dict").get("blocks", []):
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            s = round(span["size"], 1)
                            sizes[s] = sizes.get(s, 0) + len(span["text"])
    finally:
        doc.close()
    with_text = [n for n in per_page if n >= 30]
    out["total_chars"] = total
    out["pages"] = len(per_page)
    out["pages_with_text"] = len(with_text)
    out["text_page_ratio"] = round(len(with_text) / len(per_page), 3) if per_page else 0
    out["chars_per_text_page_median"] = round(statistics.median(with_text), 1) if with_text else 0
    out["bad_char_ratio"] = round(bad / total, 5) if total else 0
    out["cjk_ratio"] = round(cjk / total, 3) if total else 0
    out["n_images"] = images
    top_sizes = sorted(sizes.items(), key=lambda kv: -kv[1])[:8]
    out["font_sizes"] = [{"size": s, "chars": c} for s, c in top_sizes]
    out["distinct_font_sizes"] = len(sizes)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kinds", default="A,B")
    a = ap.parse_args()
    from pdf_census import classify
    rows = [json.loads(l) for l in open(a.census, encoding="utf-8")]
    kinds = tuple(a.kinds.split(","))
    want = [r for r in rows if any(classify(r).startswith(k) for k in kinds)]
    print("待测 %d 本" % len(want), file=sys.stderr)
    with open(a.out + ".tmp", "w", encoding="utf-8") as fh:
        for i, r in enumerate(want, 1):
            try:
                p = probe(r["relpath"])
            except Exception as e:
                p = {"relpath": r["relpath"], "error": "%s: %s" % (type(e).__name__, e)}
            p["uid"] = r["uid"]
            p["title"] = r["title"]
            p["subject1"] = r["subject1"]
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            print("[%d/%d] %s toc=%s text=%s bad=%s" % (
                i, len(want), r["uid"], p.get("toc_entries"), p.get("chars_per_text_page_median"),
                p.get("bad_char_ratio")), file=sys.stderr)
    os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    main()
