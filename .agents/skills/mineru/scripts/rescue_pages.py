#!/usr/bin/env python3
"""Turn flagged pages into one single-page PDF each, so MinerU parses them standalone.

Submitting a page alone (its own file in a batch) is what recovers pages that come back
empty inside a bigger request - including dense index pages both models otherwise skip.

Usage:
  # suspects.json shape: {"<chunk name>": [{"page": 140}, ...], ...}
  python3 rescue_pages.py suspects.json --chunk-pdf-dir chunks/ --out single_pages/
  python3 mineru.py single_pages/*.pdf -o rescued/ --model vlm --lang ch --ocr

Files are named <chunk>_p<page 1-based, zero padded>.pdf so the page can be parsed back.
Requires: pypdf.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from pypdf import PdfReader, PdfWriter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suspects", help="JSON from page_health.py (chunk -> [{'page': n}, ...])")
    ap.add_argument("--chunk-pdf-dir", required=True,
                    help="directory holding <chunk>.pdf, one file per chunk name")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()

    data = json.loads(pathlib.Path(a.suspects).read_text())
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pdf_dir = pathlib.Path(a.chunk_pdf_dir)
    written = 0
    for chunk, rows in data.items():
        src = pdf_dir / f"{chunk}.pdf"
        if not src.is_file():
            print(f"missing {src} - skipped", file=sys.stderr)
            continue
        reader = PdfReader(str(src))
        for row in rows:
            page = row["page"] if isinstance(row, dict) else int(row)
            dest = out / f"{chunk}_p{page:04d}.pdf"
            if a.skip_existing and dest.exists():
                continue
            w = PdfWriter()
            w.add_page(reader.pages[page - 1])
            with open(dest, "wb") as fh:
                w.write(fh)
            written += 1
    print(f"wrote {written} single-page PDFs to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
