# Scanned books: verify pages, rescue the empty ones

**When to use:** converting a scanned/scanned-OCR book (especially 1-bit CCITT/G4 DJVU-derived
PDFs) with MinerU, or when a converted book turns out to have gaps — pages that came back with
nothing but a page number, or a text flow that jumps mid-sentence.

MinerU (vlm **and** pipeline) silently returns **empty pages** on some scans — usually 1-bit
CCITT/G4 bitmaps. Nothing in the API result says "this page failed": the task is `done`, the
page count is right, the markdown just skips whole pages. On a 518-page scan of 《中国神话史》
44 of 1509 pages (3%) came back with only a page number.

## 1. Never trust "state: done" — audit every page

Two signals, both cheap, per (chunk dir, page):

| signal | how | meaning |
|---|---|---|
| ink density + text-line bands | render the page with `pdftoppm -r 70 -gray -png`, then `ink = mean(pixels<0.62)`, `bands` = runs of rows with >=3% dark pixels | text page: ink 5-12%, bands 15-30 (dictionary index pages: 40-80). Blank plate page: ink <2% |
| chars in `*_content_list.json` | sum `text` + `table_body` per `page_idx` | a page with ink>2.5%, bands>=12 and <250 chars is a **candidate loss** |

`scripts/page_health.py` does exactly this and prints the candidate list.

## 2. Rescue ladder (cheapest first, verified by character count)

1. **Re-submit the same file with `--pages "2,16,109"` and `--model pipeline`** — recovers
   roughly half the pages in one batch request.
2. **Extract each still-empty page to its own 1-page PDF and submit those as separate files**
   (one batch, each file = its own task). This recovered **every** remaining page in testing
   (2-5 KB of markdown each), including dictionary index pages that both models refused inside
   a bigger request. `scripts/rescue_pages.py` builds the 1-page PDFs; feed them to `mineru.py`
   in one call.
3. Prefer the **vlm** result for Chinese print; fall back to the pipeline result when the vlm
   text is <70% of the pipeline text. Keep the longest text per page across all attempts — but
   re-run the completeness check afterwards, because a longer reading may be a *different*
   transcription rather than more content.

## 3. `page_ranges` retries: map indexes correctly

When a retry uses `--pages`, the returned content_list restarts `page_idx` at 0 for the
selected pages **in the order you listed them**. Mapping that back onto the chunk's 0-based
`page_idx` with a naive `if p in requested` is off by one and plants page N's text at page N+1.
Keep the page-number→local-index map explicit, and after assembly re-check that every
paragraph of the untouched MinerU markdown still appears in your rebuilt text.

## 4. MinerU's `full.md` drops `page_footnote` blocks

On scanned books the ① / ② page-bottom notes exist in `*_content_list.json`
(`type: page_footnote`) but are **absent from `full.md`** — 46 notes in one 200-page chunk.
`header` / `aside_text` / `page_number` / `footer` are also handled inconsistently (running
heads sometimes kept, sometimes dropped). Rebuilding the markdown from the content_list is the
way to keep the footnotes; if you do, emit `header`/`footer`/`aside_text` **only when the same
normalized text is attested in MinerU's own `full.md`** (that keeps real section titles and
drops running heads), and skip `page_number`.

Rebuilt-from-content_list output is not byte-identical to `full.md` (image links, `<sup>`
markup, block joining) — verify by text, not by diffing.

## 5. Verification that actually proves completeness

1. For every page: `page_idx` present in the content_lists covers 0..N-1 (no gaps).
2. For every substantial paragraph (>25 chars) of the whole-chunk pass: at least one 12-char
   slice occurs verbatim in the final markdown (fast, no difflib on megabytes). 0 missing on
   1491 + 10531 + 72 paragraphs was the pass bar for the three books above.
3. Per-page audit table: page → chosen source → character count; every rescued page must be
   non-trivial. A page where **both** models return ~100 chars is usually genuinely sparse
   (chapter-end verso), not a loss.

## 6. Local sanity tooling on this machine

`pypdf` (pip) splits/merges pages; `pdftoppm` renders; no `tesseract`, no `qpdf`. Do **not**
use `gs`/`pdftk` for exact page-range extraction — `pypdf.PdfWriter.add_page` preserves the
original bitmaps byte-for-byte, which keeps the 200 MB API limit comfortable.
