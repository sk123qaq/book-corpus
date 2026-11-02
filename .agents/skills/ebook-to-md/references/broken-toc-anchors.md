# Broken / missing TOC anchors — failure modes and recipes

Calibre-made epubs from shadow libraries often carry a TOC (NCX or nav) whose anchors do
not match the HTML. Five failure modes appeared across two library batches (62 + 81
books): §1–§3 are handled by `scripts/ebook_to_md.py` itself and show up in
`audit_volume.py` ratios; §6–§7 came out of the 81-book batch.

## 1. No-fragment entries + sub-sections on the same page → text written twice

Symptom: audit ratio above ~115% (银河帝国 197%, 中国神话大词典 189%, 博弈论 160%,
围城 106%).

Cause: a 篇/章 entry whose href has no `#fragment` points at the top of a document that
also holds its numbered sub-sections. `slice_root()` used to return the whole `<body>`
for it, and every sub-section then wrote its own span — each text block landed in the
parent file and again in a child file.

Fix (in `build_units` / `slice_root`):

- `next_fragment == ""` marks a run of entries sharing one anchor — only the last of the
  run owns the span, the earlier ones own nothing (empty element, skipped as "no text of
  its own").
- A missing start with a real next anchor is bounded at that anchor instead of taking the
  whole document.

Interplay with `merge_numeric_sections`: merged numbered sub-sections are sliced as
separate sources and concatenated at emit time, so parent + children spans tile the
document exactly once. When auditing slicing in-process, count `merge_children` sources
too — forgetting them makes a merged book look ~45% and sends you chasing a phantom.

## 2. Anchor in a small inner wrapper → slice truncated

Symptom: audit LOW with no front/back matter to blame; files exist but hold only the
start of their page (高效能人士的七个习惯: its 最后访问 / 索引 / 家族信 files were ~5% of
their documents). Calibre wraps the anchor paragraph in a small `div` and the prose
continues as later siblings of that div, so `[start → end of start's parent]` loses it.

Fix: when an entry is the last of its page (`end is None`), move the start node plus
every following sibling at each level up to `<body>`, skipping the intermediate
containers. When `next_fragment` was set but its anchor cannot be found, the old shallow
bound is kept (a missing anchor is garbage input; do not over-take and duplicate the
next entry).

## 3. Labels sharing one anchor / anchors missing → labels collapse

Symptom: 百年孤独 — 20 章 labels, only 6 anchors, most pages anchored `#None`; blocks of
labels collapse into one file and later chapters lose their names.

Recipe when the spine is chapter-granular (one document per chapter — check by reading
the first line of each `content_docs()` entry): rebuild the units document-per-chapter in
reading order instead of trusting the TOC:

```python
units = []
for i, doc in enumerate(chapter_docs, 1):          # spine reading order
    units.append(Unit(i, f"第{i}章", doc, None, 1))
emit_book(epub, units, outdir, title, os.path.basename(src), args)
```

Then verify (~99.6%) and audit. If the spine is NOT chapter-granular, do not invent
boundaries — report that the book's anchors are broken. The same rebuild driven by a
map file is `scripts/doc_units.py` (see §8).

## 4. How to detect each mode quickly

- Ratio > 115% → mode 1 (duplicated text).
- Ratio LOW with an incomplete-looking 未收录 list → mode 2 (truncation); slice a few
  suspect pages in-process and compare with what the files hold.
- Many chapter labels but the files come out unnamed / merged → mode 3.
- Files named `10`/`PART I`, or a chapter count far below the book's own Contents page →
  §8 (anchors sit one page before the body).
- In-process check that catches modes 1–2: for every doc, take 40-character windows of the
  page's text and count how many appear in the output corpus; a page under ~90% is
  suspect. (Window probes must run on normalized text — CJK + latin + digits only.)

Regression pair for any change to `slice_root`/`build_units`: 竞争优势 / 竞争战略 —
their outputs must stay byte-identical; 银河帝国 and 高效能人士的七个习惯 are the
observed-failure samples.

## 5. Re-running older output after a slicer fix

A slicer fix must be re-run over already-converted folders (a fix that removes
duplication changes every affected file). Do it safely by comparing per-document
window coverage of the old and new output first: for every epub document, count how
many sample windows of its text appear anywhere in the folder set. Coverage must be
EQUAL old-vs-new (duplicates removed do not change coverage); any document whose
coverage drops would be real content loss — stop and investigate. Observed on the
62-book library: 4 sample sets (理想国译丛×32, 余华×13, 盐铁论, 思考快与慢) — 0
coverage drops, corpus sizes down 3–58% (= duplicate text only).

## 6. Misaligned calibre split docs → every document's prefix is unowned

Symptom: audit LOW (邓小平时代 73.8%) with a 未收录 list far too short to explain it;
window coverage shows, for EVERY document, the text *before its first anchor* missing
(26% of the book ≈ 100k chars).  That text is the previous chapter's tail plus the
chapter's endnotes: the chapter's own span ended at its own document's end, and the
next document starts mid-chapter because calibre split the file at fixed byte offsets
(`index_split_NNN`) that do not line up with the NCX anchor positions.

Detect: for consecutive units `prev, nxt` with `prev.doc != nxt.doc`, the prefix
`slice_root(epub, nxt.doc, None, nxt.fragment)` is a real span that no unit owns.

Fix: hand each prefix to the previous document's last unit as a `merge_children`
source — the engine renders merged children after the parent, in order, which is
exactly where the tail belongs.  `scripts/carry_doc_prefixes.py` does this as a
one-shot re-run; the core of it is:

```python
for prev, nxt in zip(units, units[1:]):
    if prev.doc == nxt.doc or not nxt.fragment:
        continue
    if epub.soup(nxt.doc).find(id=nxt.fragment) is None:
        continue                      # stale anchor: slicing it takes the whole document
    prev.merge_children.append(
        Unit(prev.index, "", nxt.doc, None, 1, untoced=True, next_fragment=nxt.fragment))
```

Pitfall: do NOT probe the prefix with `slice_root()` first — it extracts nodes out of
the cached soup, so the emitted file comes out with an empty prefix while the probe
looked fine.  After the re-run, re-verify + re-audit (邓小平时代 73.8% → 98.0%) and
delete the leftover files of the earlier numbering (README-link rule).

## 7. Junk pages the title filter keeps (drop patterns seen in the wild)

- `--drop '^前置页 \d+$'` — a TOC-less, image-only page (calibre 版权页 as one GIF:
  劳伦斯三部曲; a QR code + site URL: 一句顶一万句). It has no TOC entry, so the title
  guess fails and it becomes a 40-char “前置页 N” file.
- `--drop '^章节 \d+$'` — untoced calibre junk doc (`cover_page` etc.) that carries a
  stray caption fragment; same shape as 前置页, different autogenerated name.
- `--drop '^本书由公众号'` — piracy stamp pages inserted by the packager (identical copy
  can appear both near the front and near the end; verify_output flags the pair as
  “identical content”).

A dropped page never reaches the README 未收录 list — the run report prints it as
`matched --drop`, so keep the reason in the report when telling the user what was left
out.

## 8. Anchors land one document before the body → every chapter unowned

Symptom: the run writes many 10–50 character files and far fewer chapters than the book
has — The Lever of Riches, 3 chapters of 12 across 54 files, 29.6% LOW; Who Gets What, 23
files for 12 chapters, 87.7% LOW.

What the NCX looks like: each navPoint targets a page that holds only a part divider
(`PART II`), a chapter number (`CHAPTER ONE`, or a bare `10`), or an ornament image, and
the prose is in the next spine document (`index_split_009`, or the same slice's
`_split_001.html`). Every anchor owns a page with no text, so the real chapter documents
belong to nobody. A second pathology rides along in some books: dozens of extra navPoints
whose labels are whole sentences and whose `filepos` steps ~100 bytes — anchors inside an
image-only Index page, not chapters.

This is not §6. There each chapter's document still starts with that chapter and only the
prefix before its first anchor is lost. Here the whole document is lost.

Before writing a map, run the default converter once. `build_units()` now attaches
TOC-less documents to the preceding TOC entry, so the common calibre shape — an empty or
title-only anchor page with the prose in the next spine document — becomes one chapter
without a map (重新定义公司 29 files / 71.6% → 13 / 99.8%). This recipe is for the harder
case: the anchor page itself carries the chapter number / `PART II`, or the TOC has dozens
of sentence-long `filepos` anchors, so no amount of continuation attachment can recover
the intended boundaries.

Detect before touching the converter:

- `verify_output.py` reports N files; the book's own Contents page lists M chapters, and
  either N ≫ M (stub pages) or N ≪ M (lost chapters).
- The file list holds titles that are just a number, `PART I`, or `CHAPTER ONE`.
- `audit_volume.py` is LOW and the 未收录 list cannot explain it.

Fix: ignore the NCX. Take chapter names from the book's own Contents page and write a page
map in spine order, one entry per page, body document first:

```json
{"pages": [
  {"title": "PART I — MARKETS ARE EVERYWHERE",
   "docs": ["part0005_split_000.html", "part0005_split_001.html"],
   "exclude": "只有 PART I 标记与部名", "reason": "part divider"},
  {"title": "Signaling", "docs": ["part0017_split_001.html"]},
  {"title": "Introduction", "docs": ["index_split_009.html"],
   "part": "Part I Economic Growth and Technological Progress"}]}
```

```bash
python3 "<SKILL_DIR>/scripts/doc_units.py" book.epub -o OUT --map pages.json --all-pages
```

- The script exits when the map skips, repeats, or reorders a spine document. That check
  is what catches a Contents page read wrong.
- An `exclude` page becomes no unit at all. Do not merely mark it excluded: slicing it
  consumes nodes the next chapter's link rewriting reads, which moved a `[` across two
  paragraphs when The Lever of Riches was rebuilt with those units present.
- `--all-pages` here means "trust this map", not "keep front matter": `exclude` pages stay
  out; `Notes`/`References`/`Epilogue` are body now, so `--all-pages` is only for
  front-matter pages you deliberately want.
- `part` writes `part:` into that chapter's YAML and a bold line into the README index;
  `exclude` fills the README 未收录 list with your `reason`.
- Expected numbers: The Lever of Riches 12 chapters / 90.2%, Who Gets What 14 files /
  95.5%. Both reproduce their library folders byte-for-byte; diff them after any change to
  this script.