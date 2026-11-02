# How Calibre finds chapters (and what this skill borrows)

Calibre is the reference implementation to compare against: GPL, still maintained, and
the only common reader that opens EPUB, MOBI/AZW3, PDF, FB2 and the rest through one
pipeline.  This note reads its source to answer one question — *where does a chapter
boundary come from in each format?* — and lists the rules `ebook_to_md.py` adopted.
Paths below are from `kovidgoyal/calibre` master, read 2026-10-28 (manual 9.9–9.13);
function names are stable enough to grep.

## The short version

| format | first source of chapters | fallback |
| --- | --- | --- |
| EPUB 3 | `nav` document (`epub:type="toc"`) | NCX if the nav is missing/empty |
| EPUB 2 | NCX (`toc.ncx` via `spine@toc`) | heading XPaths, then links, then spine files |
| MOBI 6 | `<guide><reference type="toc">` + the links after it | embedded NCX/INDX; none → no TOC |
| KF8 / AZW3 | the EPUB embedded inside the MOBI container | same nav/NCX rules as EPUB |
| PDF | embedded outline (`<outline>`) | layout heuristics → `h1`/`h2`/`h3`, then chapter XPath |

The important part is not the file format.  It is that a TOC entry names a **reading
span**, not a file.  Calibre's viewer keeps scrolling from the anchor to the next anchor;
a split document is invisible to the reader.  A converter that treats every spine file
as a chapter will break exactly the books Calibre reads fine.

## EPUB: nav first, NCX second

`calibre/ebooks/oeb/polish/toc.py`:

- `get_toc(container)` — for OPF version 3 it calls `find_existing_nav_toc()` and
  `parse_nav()`; **if the resulting tree is empty** it retries the NCX.  For OPF 2 it
  goes straight to `find_existing_ncx_toc()` / `parse_ncx()`.
- `parse_nav()` walks `<nav epub:type="toc"><ol><li>` recursively.  The link text is
  the label; `href="#frag"` stays in the nav file, anything else is resolved relative to
  the nav document.  A `<span>` (no href) is a label-only group.
- `parse_ncx()` walks `navMap/navPoint` recursively; `content@src` gives file + fragment.
- `verify_toc_destinations()` checks every entry: the file must exist, and when a
  fragment is present it must be an `id` or `a@name` in that file.  Broken entries are
  reported (`dest_exists=False`) instead of silently producing an empty chapter.
- `from_xpaths(container, ['//h:h1', '//h:h2', ...])` is the generic heading fallback.
  It scans every spine item, removes levels that are empty everywhere, then builds a
  nested TOC by heading level.  This is Calibre's "detect chapters from headings".
- `from_links(container)` takes every internal `<a href>` as a TOC candidate (deduped by
  destination and label); `from_files(container)` uses one spine file per entry and the
  first text of the body as its label.  These are the last-resort generators behind the
  "Edit TOC → Generate" menu.
- `epub_input.py: convert_epub3_nav()` converts the EPUB 3 nav into an NCX before the
  rest of the conversion pipeline runs.  The pipeline only understands NCX; the nav is
  not a second class of chapter data.

**Rule for us:** prefer the nav, fall back to the NCX, validate anchors, and when the
TOC is absent use headings/links/spine in that order — never invent a boundary between
them.

## MOBI 6: the guide, then indentation

`calibre/ebooks/mobi/reader/mobi6.py`:

- The raw MOBI HTML is parsed; `create_opf()` looks for
  `<guide><reference type="toc" href="#...">`.  Starting at that anchor it walks
  forward through `<a href>` elements and records each link's label.
- The nesting is recovered from **left whitespace**: `get_left_whitespace()` sums
  `margin-left` and `text-indent` up the ancestor chain; `structure_toc()` groups the
  distinct indent values into levels (gives up when there are fewer than 2 or more than
  6 distinct values).
- If there is no guide anchor, there is no TOC; the reader falls back to the spine.
  KF8/AZW3 is different: the file contains a complete EPUB (OPF + NCX/nav), which
  `mobi8.py` extracts and hands to the EPUB path — so AZW3 chapters are EPUB chapters.

**Rule for us:** MOBI/AZW3 should be converted to a temporary EPUB with Calibre and then
follow the EPUB rules.  Do not parse MOBI INDX by hand (see `references/mobi-azw3.md`).

## PDF: outline first, typography second

`calibre/ebooks/conversion/plugins/pdf_input.py` and `calibre/ebooks/pdf/reflow.py`:

- `PDFDocument.generate_toc()` reads the PDF's own `<outline>` tree and writes a
  `toc.ncx` when it has more than two entries.  This is the good case: the outline is
  the publisher's chapter list.
- Without an outline, `reflow.py` assigns heading tags from page layout while it
  rebuilds paragraphs: `Part X` → `h1`; a centered number, `Chapter ...`,
  `Prologue`/`Epilogue`, or a centered all-caps line at the top of a page → `h2`; a
  line that is only a Roman numeral → `h3`.  The default conversion chapter XPath is
  then:
  `//*[((name()='h1' or name()='h2') and re:test(., 'chapter|book|section|part\s+', 'i')) or @class='chapter']`
  (manual: "Chapters and page breaks").  Page breaks default to every `h1`/`h2`.
- A PDF with neither outline nor usable typography has no reliable chapter structure.
  Calibre does not invent one; it produces one long document.

**Rule for us:** a PDF is out of scope for this skill; if a PDF-derived EPUB has an
outline-derived NCX, trust it, otherwise report that the structure is a guess.

## What `ebook_to_md.py` changed because of this

1. **A TOC entry owns a reading span across spine documents.**  `build_units()` now
   attaches TOC-less documents to the preceding entry when they are empty, repeat the
   entry's title, or carry prose.  Calibre's split pages (`split_000`, `split_001`, …)
   therefore stay one chapter.  Before this, 重新定义公司 became 29 files at 71.6%: each
   calibre split page was its own `前置页` or duplicate chapter stub.
2. **A title-only anchor page is not a chapter.**  If the anchor's document has no text
   (or only the heading), the following prose is the chapter.  A short standalone heading
   with no prose starts a separate TOC-less section instead (论人类不平等的起源和基础's
   `编者注`), so the two cases do not collapse into one.
3. **Apparatus is body.**  Calibre's TOC shows `Notes`, `References`, `Afterword` and
   `Glossary` like any other entry.  The skill's body-only filter used to drop them as
   back matter, costing 20–30% of a monograph.  They are now kept by default; `Index`
   (page locators) and `Acknowledgments` still stay out.  `--drop '^参考文献$'` is the
   opt-out.
4. **The TOC is not the only structure.**  When the TOC anchors are structurally wrong
   (a chapter number page, `PART II`, or dozens of sentence-long `filepos` entries),
   `doc_units.py` still rebuilds units from a page map read off the book's own Contents
   page.  The automatic continuation rule handles calibre's ordinary split pages; it
   cannot repair a TOC that points at the wrong page for every chapter.

`scripts/selftest.py` covers the same shapes with throwaway EPUBs, so the rules can be checked without a book corpus.
