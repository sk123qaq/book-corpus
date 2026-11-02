#!/usr/bin/env python3
"""Flag pages where a scan clearly holds text but MinerU returned almost none.

Usage:
  python3 page_health.py <mineru_out_dir> <source_chunk.pdf> [...]
    --min-ink 0.025   page ink fraction above which a page is expected to have text
    --min-bands 12    text-line bands above which a page is expected to have text
    --min-chars 250   extracted chars below which a page counts as suspicious
  python3 page_health.py --json out.json <out_dir> <pdf> ...   # machine-readable

Each argument pair is (directory holding <name>_content_list.json, the PDF that was sent).
Requires: pdftoppm (poppler), PIL, numpy.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image


def page_chars(d: pathlib.Path) -> dict[int, int]:
    files = sorted(d.glob("*_content_list.json"))
    if not files:
        raise SystemExit(f"no *_content_list.json in {d}")
    per: dict[int, int] = {}
    for it in json.loads(files[0].read_text()):
        p = it.get("page_idx")
        if p is None:
            continue
        total = len(it.get("text") or "") + len(it.get("table_body") or "")
        per[p] = per.get(p, 0) + total
    return per


def ink_profile(pdf: pathlib.Path, tmp: pathlib.Path) -> list[tuple[float, int]]:
    subprocess.run(["pdftoppm", "-r", "70", "-gray", "-png", str(pdf), str(tmp / "p")],
                   check=True)
    pngs = sorted(tmp.glob("p-*.png")) or sorted(tmp.glob("p*.png"))
    out = []
    for png in pngs:
        a = np.asarray(Image.open(png).convert("L"), dtype=np.float32) / 255.0
        dark = a < 0.62
        rows = dark.mean(axis=1)
        bands, in_band = 0, False
        for r in rows:
            if r >= 0.03 and not in_band:
                bands += 1
                in_band = True
            elif r < 0.03:
                in_band = False
        out.append((float(dark.mean()), bands))
    return out


def check(out_dir: pathlib.Path, pdf: pathlib.Path, min_ink: float, min_bands: int,
          min_chars: int) -> list[dict]:
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        prof = ink_profile(pdf, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    chars = page_chars(out_dir)
    suspects = []
    for idx, (ink, bands) in enumerate(prof):
        n = chars.get(idx, 0)
        if ink > min_ink and bands >= min_bands and n < min_chars:
            suspects.append({"page": idx + 1, "ink": round(ink, 4), "bands": bands,
                             "chars": n})
    return suspects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+",
                    help="<mineru_out_dir> <source.pdf> [<out_dir> <pdf> ...]")
    ap.add_argument("--min-ink", type=float, default=0.025)
    ap.add_argument("--min-bands", type=int, default=12)
    ap.add_argument("--min-chars", type=int, default=250)
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()
    if len(a.pairs) % 2:
        return 2
    report = {}
    for i in range(0, len(a.pairs), 2):
        out_dir, pdf = pathlib.Path(a.pairs[i]), pathlib.Path(a.pairs[i + 1])
        s = check(out_dir, pdf, a.min_ink, a.min_bands, a.min_chars)
        report[out_dir.name] = s
        print(f"{out_dir.name}: {len(s)} suspicious page(s) of {len(page_chars(out_dir))}")
        for row in s:
            print(f"   p{row['page']:5d} ink={row['ink']:.3f} bands={row['bands']:3d} "
                  f"chars={row['chars']}")
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("total suspicious:", sum(len(v) for v in report.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
