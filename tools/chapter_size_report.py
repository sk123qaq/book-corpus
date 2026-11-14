#!/usr/bin/env python3
"""只读扫描全库章级规模，复现设计文档的分箱与分位数。

用法：
    uv run --frozen python tools/chapter_size_report.py --root /home/shike/书库 \
        --out /tmp/chapter_sizes.csv
    uv run --frozen python tools/chapter_size_report.py --limit 20

CSV 写到 ``--out``，JSONL 写到同名的 ``.jsonl``。不写 ``books/``、``build/`` 或人账。
"""

import argparse
import csv
import json
import math
import os
import sys
from datetime import date
from pathlib import Path

from bookcorpus.reading.depth.model import plan_chunks
from bookcorpus.reading.library import Library

FIELDS = (
    "uid",
    "n",
    "title",
    "file",
    "chars",
    "blocks",
    "chunks18",
    "max_chunk_blocks18",
    "chunks9",
    "max_chunk_blocks9",
)
CHAR_BINS = (9000, 18000, 27000, 36000, 54000, 72000, 108000, 144000)
BLOCK_BINS = (20, 40, 60, 80, 100, 150, 200)
MAX_CHUNK_THRESHOLDS = (40, 80, 200, 500, 1000)
PERCENTILES = (("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99), ("p99.9", 99.9))


def _row(library, uid, chapter):
    n = chapter["n"]
    raw = library.read_chapter(uid, n)
    if not raw:
        return None
    blocks = raw.get("blocks") or []
    plans18 = plan_chunks(blocks, 18000, None)
    plans9 = plan_chunks(blocks, 9000, None)
    return {
        "uid": uid,
        "n": n,
        "title": chapter.get("title") or "",
        "file": chapter.get("file") or "",
        "chars": sum(len(block.get("text") or "") for block in blocks),
        "blocks": len(blocks),
        "chunks18": len(plans18),
        "max_chunk_blocks18": max((len(plan.get("blocks") or []) for plan in plans18), default=0),
        "chunks9": len(plans9),
        "max_chunk_blocks9": max((len(plan.get("blocks") or []) for plan in plans9), default=0),
    }


def _bins(values, cuts, *, include_zero=False):
    counts = [0] * (len(cuts) + 1)
    zero = 0
    for value in values:
        if value <= 0 and not include_zero:
            zero += 1
            continue
        index = 0
        while index < len(cuts) and value > cuts[index]:
            index += 1
        counts[index] += 1
    labels = []
    low = 0
    for cut in cuts:
        labels.append(f"{low:,} 到 {cut:,}")
        low = cut
    labels.append(f"{low:,} 以上")
    return list(zip(labels, counts, strict=True)), zero


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[int(position)])
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _print_bins(title, bins, total, zero=0):
    print(title)
    for label, count in bins:
        share = (count / total * 100.0) if total else 0.0
        print(f"  {label:<18} {count:>8,}  {share:5.2f}%")
    if zero:
        share = (zero / total * 100.0) if total else 0.0
        print(f"  {'0（未入箱）':<18} {zero:>8,}  {share:5.2f}%")


def _summary(rows):
    total = len(rows)
    print(f"章数 {total:,}")
    if not total:
        return
    char_bins, char_zero = _bins([row["chars"] for row in rows], CHAR_BINS)
    block_bins, _block_zero = _bins([row["blocks"] for row in rows], BLOCK_BINS, include_zero=True)
    _print_bins("字符数分箱", char_bins, total, char_zero)
    _print_bins("块数分箱", block_bins, total)
    print("18k 计划最大块块数")
    for threshold in MAX_CHUNK_THRESHOLDS:
        count = sum(1 for row in rows if row["max_chunk_blocks18"] > threshold)
        print(f"  超过 {threshold:<6,} {count:>8,}  {count / total * 100.0:5.2f}%")
    for key, label in (("max_chunk_blocks18", "18k"), ("max_chunk_blocks9", "9k")):
        values = [row[key] for row in rows]
        parts = [f"{name} {_percentile(values, pct):.1f}" for name, pct in PERCENTILES]
        parts.append(f"最大 {max(values):,}")
        print(f"{label} 最大块分位：" + " · ".join(parts))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("BOOKS_ROOT") or None,
        help="实例根；默认按 BOOKS_ROOT 或当前目录向上找",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="CSV 输出路径；缺省写到 <root>/工作区/阅读/规模报告-YYYY-MM-DD.csv",
    )
    parser.add_argument("--limit", type=int, default=0, help="只扫前 N 章，用于快速验证")
    args = parser.parse_args(argv)

    library = Library(args.root)
    rows = []
    for book in library.books():
        uid = book["uid"]
        for chapter in library.chapters(uid):
            row = _row(library, uid, chapter)
            if row is not None:
                rows.append(row)
            if args.limit and len(rows) >= args.limit:
                break
        if args.limit and len(rows) >= args.limit:
            break

    out = (
        Path(args.out)
        if args.out
        else Path(library.layout.root) / "工作区" / "阅读" / f"规模报告-{date.today().isoformat()}.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    jsonl = out.with_suffix(".jsonl")
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    _summary(rows)
    print(f"CSV {out} · JSONL {jsonl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
