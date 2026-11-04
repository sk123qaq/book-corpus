#!/usr/bin/env python3
"""PDF 普查：逐本测文本层、字体、扫描图，只读。输出一行一本书的 JSONL。

用法：
  pdf_census.py --out 工作区/批次/<批次>/census.jsonl [--limit N] [--uid UID...]
  pdf_census.py --report 工作区/批次/<批次>/census.jsonl

判定类型是 census.jsonl 上的纯函数（classify），阈值改了只需重跑 --report，不必重扫。
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L

ROOT = L.ROOT
DB = L.PATHS.library_db
TIMEOUT = 300

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def run(cmd, timeout=TIMEOUT):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -9, b"", b"timeout"
    except OSError as e:
        return -1, b"", str(e).encode()


def parse_info(out):
    d = {}
    for line in out.decode("utf-8", "replace").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        d[k.strip()] = v.strip()
    return d


IMG_ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s+(\S+)\s*$"
)


def page_size_pt(info):
    m = re.match(r"([\d.]+)\s+x\s+([\d.]+)", info.get("Page size", ""))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def measure(uid, title, subj1, subj2, relpath, size):
    row = {
        "uid": uid,
        "title": title,
        "subject1": subj1,
        "subject2": subj2,
        "bytes": size,
        "relpath": relpath,
        "errors": [],
    }
    abspath = os.path.join(ROOT, relpath)
    if not os.path.exists(abspath):
        row["errors"].append("missing_file")
        return row

    rc, out, _err = run(["pdfinfo", abspath])
    info = parse_info(out)
    row["info"] = info
    row["pages"] = int(info.get("Pages", 0) or 0)
    row["encrypted"] = info.get("Encrypted", "")
    row["producer"] = info.get("Producer", "")
    row["creator"] = info.get("Creator", "")
    row["tagged"] = info.get("Tagged", "")
    row["pdf_version"] = info.get("PDF version", "")
    pw, ph = page_size_pt(info)
    row["page_w_pt"], row["page_h_pt"] = pw, ph
    if rc != 0:
        row["errors"].append("pdfinfo_rc=%d" % rc)

    rc, out, _ = run(["pdffonts", abspath])
    fonts = [line for line in out.decode("utf-8", "replace").splitlines()[2:] if line.strip()]
    row["n_fonts"] = len(fonts)
    row["n_fonts_embedded"] = sum(1 for line in fonts if re.search(r"\syes\s+\S+\s+\S+\s+\d+\s+\d+\s*$", line))
    row["font_types"] = sorted({line.split()[1:3][0] for line in fonts if len(line.split()) > 2})

    rc, out, _err = run(["pdftotext", "-q", "-enc", "UTF-8", abspath, "-"])
    if rc == -9:
        row["errors"].append("pdftotext_timeout")
        text = ""
    else:
        if rc != 0 and len(out) < 1000:
            # 能吐出正文的语法警告不算读失败，只有几乎没输出才记
            row["errors"].append("pdftotext_rc=%d" % rc)
        text = out.decode("utf-8", "replace")
    pages_txt = text.split("\f")
    per_page = [len(p.strip()) for p in pages_txt]
    if len(per_page) > 1 and per_page and per_page[-1] == 0:
        per_page = per_page[:-1]
    total = sum(per_page)
    row["text_chars"] = total
    row["cjk_chars"] = len(CJK_RE.findall(text))
    row["pages_with_text"] = sum(1 for n in per_page if n >= 30)
    row["chars_per_page"] = round(total / row["pages"], 1) if row["pages"] else None
    row["text_page_ratio"] = round(row["pages_with_text"] / len(per_page), 3) if per_page else 0.0
    with_text = [n for n in per_page if n >= 30]
    row["chars_per_text_page_median"] = round(statistics.median(with_text), 1) if with_text else 0
    row["text_head"] = re.sub(r"\s+", " ", text[:400]).strip()
    mid = len(pages_txt) // 2
    row["text_mid"] = re.sub(r"\s+", " ", " ".join(pages_txt[mid : mid + 2]))[:300].strip()

    rc, out, _ = run(["pdfimages", "-list", abspath])
    pages_img, ppis, full = {}, [], 0
    colors, bpcs, types = {}, {}, {}
    page_area_pt = (pw * ph) if (pw and ph) else None
    for line in out.decode("utf-8", "replace").splitlines()[2:]:
        m = IMG_ROW.match(line)
        if not m:
            continue
        pg, _num, typ, w, h, color, _comp, bpc = (
            int(m.group(1)),
            m.group(2),
            m.group(3),
            int(m.group(4)),
            int(m.group(5)),
            m.group(6),
            m.group(7),
            int(m.group(8)),
        )
        xppi, yppi = float(m.group(13)), float(m.group(14))
        pages_img.setdefault(pg, 0)
        pages_img[pg] += w * h
        ppis.append((xppi + yppi) / 2)
        colors[color] = colors.get(color, 0) + 1
        bpcs[bpc] = bpcs.get(bpc, 0) + 1
        types[typ] = types.get(typ, 0) + 1
        if page_area_pt and xppi > 0:
            area_pt = (w / xppi * 72) * (h / yppi * 72)
            if area_pt >= 0.8 * page_area_pt:
                full += 1
    row["n_images"] = (sum(pages_img.values()) and len(pages_img)) or 0
    row["image_objs"] = len(ppis)
    row["pages_with_image"] = len(pages_img)
    row["full_page_images"] = full
    row["img_page_ratio"] = round(len(pages_img) / row["pages"], 3) if row["pages"] else 0.0
    row["img_ppi_median"] = round(statistics.median(ppis), 1) if ppis else None
    row["img_colors"] = colors
    row["img_bpc"] = bpcs
    row["img_types"] = types
    return row


def classify(r):
    """census 行 -> 处理类型。纯函数，阈值在这里改。

    只带 owner 口令（权限受限、无 user 口令）的 PDF 不算加密：pymupdf 能正常读，
    按文本层与图片指标分类，加密事实写在 encrypted 字段里备查。
    """
    if r.get("errors"):
        if "missing_file" in r["errors"]:
            return "源文件缺失"
        return "读取失败"
    r.get("pages") or 0
    ratio = r.get("text_page_ratio") or 0
    cpp = r.get("chars_per_page") or 0
    img_ratio = r.get("img_page_ratio") or 0
    full = r.get("full_page_images") or 0
    cjk = r.get("cjk_chars") or 0
    r.get("img_ppi_median") or 0
    scanish = img_ratio >= 0.8 and full >= 0.8 * max(r.get("pages_with_image") or 1, 1)
    if ratio >= 0.9 and cpp >= 200 and (img_ratio < 0.5 or r.get("n_images") == 0):
        return "A 纯文本层" if cjk >= 0.2 * max(r.get("text_chars") or 1, 1) else "A 纯文本层(非中文)"
    if ratio < 0.2 and scanish:
        return "C 扫描件(无文本层)"
    if ratio < 0.2 and not scanish:
        if not r.get("pages") or (r.get("image_objs") or 0) == 0:
            return "D 不可读"
        return "C2 无文本层且非整页图"
    if ratio >= 0.5:
        return "B 文本层+图"
    return "B2 局部文本层"


def report(rows):
    from collections import Counter, defaultdict

    c = Counter(classify(r) for r in rows)
    print("== 类型分布（%d 本）" % len(rows))
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        print("  %-22s %4d" % (k, v))
    by = defaultdict(list)
    for r in rows:
        by[classify(r)].append(r)
    for k in sorted(by):
        g = by[k]
        gb = sum(x["bytes"] for x in g) / 2**30
        pg = sum(x.get("pages") or 0 for x in g)
        print("\n-- %s  书 %d  体积 %.1f GB  页 %d" % (k, len(g), gb, pg))
        if k.startswith("C") or k.startswith("B2"):
            d = Counter(x.get("img_ppi_median") for x in g)
            print("   扫描分辨率中位数分布:", d.most_common(6))
            print("   位深:", Counter(k2 for x in g for k2 in x.get("img_bpc", {})).most_common(4))
        for x in sorted(g, key=lambda y: -y["bytes"])[:5]:
            print(
                "   %s %s %.0fMB %s页 text=%s%% ppi=%s %s"
                % (
                    x["uid"],
                    x["title"][:28],
                    x["bytes"] / 1e6,
                    x.get("pages"),
                    round(100 * (x.get("text_page_ratio") or 0)),
                    x.get("img_ppi_median"),
                    x.get("subject1"),
                )
            )
    print("\n== 一级学科 x 类型")
    cross = Counter((r.get("subject1"), classify(r)) for r in rows)
    subs = sorted({k[0] for k in cross})
    kinds = sorted({k[1] for k in cross})
    print("    %-12s %s" % ("", " ".join("%-14s" % k.split(" ")[0] for k in kinds)))
    for s in subs:
        print("    %-12s %s" % (s, " ".join("%-14d" % cross.get((s, k), 0) for k in kinds)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--report")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--uid", action="append")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    if a.report:
        with open(a.report, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        report(rows)
        return

    import sqlite3

    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    q = """SELECT b.uid, b.title, b.subject1, b.subject2, s.file, s.bytes, s.format
           FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
           WHERE s.format = 'pdf' ORDER BY b.uid"""
    rows = con.execute(q).fetchall()
    if a.uid:
        rows = [r for r in rows if r[0] in set(a.uid)]
    if a.limit:
        rows = rows[: a.limit]
    if (a.uid or a.limit) and not a.out:
        raise SystemExit("--uid/--limit 是子集跑法，必须给 --out，别覆盖定稿普查账")
    out = a.out or os.path.join(L.batch_dir("pdf-普查-", root=ROOT, create=True), "census.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f, ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(measure, *r[:6]): r[0] for r in rows}
        for done, (fut, uid) in enumerate(futs.items(), 1):
            row = fut.result()
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print("%4d/%d %s %s" % (done, len(rows), uid, classify(row)), file=sys.stderr)
    os.replace(tmp, out)
    print(out, file=sys.stderr)


if __name__ == "__main__":
    main()
