#!/usr/bin/env python3
"""交叉核对：pdf_md_batch.gray_stats（pymupdf 70dpi + 纯 Python）对 pdf_scan_probe.py 的
pdftoppm 70dpi + numpy 实测值（scan_probe2.jsonl / triage_final.csv 里的 ink 与 bands）。
"""
import importlib.util
import json
import sys

REPO = "/home/shike/文档/books"
spec = importlib.util.spec_from_file_location("pmb", REPO + "/工作区/脚本/pdf_md_batch.py")
pmb = importlib.util.module_from_spec(spec)
sys.modules["pmb"] = pmb
spec.loader.exec_module(pmb)
import pymupdf as fitz  # noqa: E402

C = REPO + "/工作区/批次/pdf-普查-2026-10-19"
probe = [json.loads(l) for l in open(C + "/scan_probe2.jsonl", encoding="utf-8")]
mine = [r for r in probe if r["uid"] == "B0540"][0]
src = REPO + "/" + mine["relpath"]
print("B0540 sampled:", json.dumps(mine["sampled"], ensure_ascii=False))
doc = fitz.open(src)
print("\n页  ink(探针) ink(本脚本)  bands(探针) bands(本脚本)")
for s in mine["sampled"]:
    page = doc[s["page"] - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(70 / 72.0, 70 / 72.0), colorspace=fitz.csGRAY)
    ink, br, bc = pmb.gray_stats(pix)
    print("%4d  %8.4f %10.4f  %10s %13s  (w=%d h=%d n=%d stride=%d)"
          % (s["page"], s["ink"], ink, max(s["bands_row"], s["bands_col"]), max(br, bc),
             pix.width, pix.height, pix.n, pix.stride))
doc.close()
