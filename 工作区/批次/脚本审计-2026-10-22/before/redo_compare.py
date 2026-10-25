#!/usr/bin/env python3
"""重拆批次的验收闸门：逐个比「旧 md」与「暂存新 md」的源页覆盖率。

口径与 audit_old_md.py 相同（每页 10 个 20 字 CJK 锚点）。旧 md 与暂存区用同一组
锚点，逐页噪声两边一样，只看差值。判据（broken-toc-anchors.md §5）：
覆盖率不得下降；下降的书不落地，覆盖率持平或上升的才考虑落地。

输出 comparison.tsv 与逐页明细 comparison-details.tsv。

用法: python3 redo_compare.py [--batch 旧md重拆-2026-10-18]
"""
import argparse
import collections
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
from audit_old_md import Epub, md_text_of, page_anchors  # noqa: E402

COLS = ["uid", "title", "format", "stage", "pages", "anchors", "old_hits", "new_hits",
        "old_cover", "new_cover", "delta", "fs_identical", "verdict"]


def dir_sig(folder):
    out = {}
    for root, _sub, files in os.walk(folder):
        for name in files:
            if name == ".manifest.json":
                continue
            p = os.path.join(root, name)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def source_epub(row, batch):
    src = os.path.join(L.ROOT, row["src"])
    if row["format"] == "epub":
        return src
    tmp = os.path.join(batch, "tmp", row["uid"], "book.epub")
    return tmp if os.path.isfile(tmp) else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", default="旧md重拆-2026-10-18")
    a = ap.parse_args()
    batch = os.path.join(L.WORK, "批次", a.batch)
    wl = [l.rstrip("\n").split("\t") for l in open(
        os.path.join(batch, "worklist.tsv"), encoding="utf-8")]
    cols = wl[0]
    rows = [dict(zip(cols, r)) for r in wl[1:]]

    out_rows, details = [], []
    for row in rows:
        old_dir = os.path.join(L.ROOT, "库", row["path"], "md")
        new_dir = os.path.join(batch, "out", row["uid"])
        rec = {"uid": row["uid"], "title": row["title"], "format": row["format"],
               "stage": "yes" if os.path.isdir(new_dir) else "missing"}
        if not os.path.isdir(new_dir):
            rec.update(pages=0, anchors=0, old_hits=0, new_hits=0, old_cover=0.0,
                       new_cover=0.0, delta=0, fs_identical="", verdict="no-stage")
            out_rows.append(rec)
            continue
        epub_path = source_epub(row, batch)
        if epub_path is None:
            rec.update(pages=0, anchors=0, old_hits=0, new_hits=0, old_cover=0.0,
                       new_cover=0.0, delta=0, fs_identical="", verdict="no-source")
            out_rows.append(rec)
            continue
        _tot, old_cjk, _f = md_text_of(old_dir)
        _tot2, new_cjk, _f2 = md_text_of(new_dir)
        ep = Epub(os.path.abspath(epub_path))
        anchors = pages = old_hits = new_hits = 0
        for doc in ep.content_docs():
            soup = ep.soup(doc)
            body = soup.find("body") if soup else None
            text = body.get_text(" ", strip=True) if body else ""
            cjk = "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
            a_list = page_anchors(cjk)
            if not a_list:
                continue
            pages += 1
            anchors += len(a_list)
            ho = sum(1 for x in a_list if x in old_cjk)
            hn = sum(1 for x in a_list if x in new_cjk)
            old_hits += ho
            new_hits += hn
            if ho != hn:
                miss = [x for x in a_list if (x in old_cjk) != (x in new_cjk)]
                details.append({"uid": row["uid"], "doc": doc, "cjk": len(cjk),
                                "old_hits": ho, "new_hits": hn,
                                "head": cjk[:24], "n_diff": len(miss),
                                "diff_sample": " | ".join(m[:20] for m in miss[:3])})
        sig_old = dir_sig(old_dir)
        sig_new = dir_sig(new_dir)
        same = "yes" if sig_old == sig_new else "no"
        rec.update(pages=pages, anchors=anchors, old_hits=old_hits, new_hits=new_hits,
                   old_cover=old_hits / anchors if anchors else 1.0,
                   new_cover=new_hits / anchors if anchors else 1.0,
                   delta=new_hits - old_hits, fs_identical=same)
        if new_hits < old_hits:
            rec["verdict"] = "drop"
        elif new_hits > old_hits:
            rec["verdict"] = "up"
        elif same == "yes":
            rec["verdict"] = "identical"
        else:
            rec["verdict"] = "same-coverage"
        out_rows.append(rec)

    tsv = os.path.join(batch, "comparison.tsv")
    with open(tsv + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\t".join(COLS) + "\n")
        for r in out_rows:
            fh.write("\t".join(str(r[c]) for c in COLS) + "\n")
    os.replace(tsv + ".tmp", tsv)
    det = os.path.join(batch, "comparison-details.tsv")
    with open(det + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("uid\tdoc\tcjk\told_hits\tnew_hits\tn_diff\thead\tdiff_sample\n")
        for d in details:
            vals = [str(d[k]).replace("\t", " ").replace("\n", " ") for k in
                    ("uid", "doc", "cjk", "old_hits", "new_hits", "n_diff",
                     "head", "diff_sample")]
            fh.write("\t".join(vals) + "\n")
    os.replace(det + ".tmp", det)
    print("结果 %s / 明细 %s" % (os.path.relpath(tsv, L.ROOT), os.path.relpath(det, L.ROOT)))
    print("status:", dict(collections.Counter(r["verdict"] for r in out_rows)))
    for v in ("drop", "up", "same-coverage", "no-stage", "no-source"):
        sel = [r for r in out_rows if r["verdict"] == v]
        if not sel:
            continue
        print("\n%s %d 本" % (v, len(sel)))
        for r in sel:
            print("  %-6s %-38.38s 页%4d 旧 %6.1f%% 新 %6.1f%% Δ%+d %s" % (
                r["uid"], r["title"], r["pages"], r["old_cover"] * 100,
                r["new_cover"] * 100, r["delta"],
                "" if r["fs_identical"] == "yes" else "(文件不同)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
