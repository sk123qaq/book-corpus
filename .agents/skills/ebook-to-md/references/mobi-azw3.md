# MOBI/AZW3 conversion through Calibre

The Markdown scripts accept EPUB, not MOBI or AZW3. Convert each source into a distinct temporary EPUB, then use the standard single-book or collection path.

## Standard path

```bash
tmpdir=$(mktemp -d)
src="book.mobi"             # or: src="book.azw3"
ebook-convert "$src" "$tmpdir/book.epub"
python3 "<SKILL_DIR>/scripts/ebook_to_md.py" "$tmpdir/book.epub" \
  -o "/path/to/书md/book-title" --title "book-title"
# First correct generated `source` / `来源` fields to the original filename;
# then verify and audit the intermediate EPUB:
python3 "<SKILL_DIR>/scripts/verify_output.py" "/path/to/书md/book-title"
python3 "<SKILL_DIR>/scripts/audit_volume.py" "$tmpdir/book.epub" \
  -o "/path/to/书md/book-title"
```

Use a unique temporary EPUB for each source. Keep it until verification and audit complete; then remove the temporary workspace. For a collection whose converted EPUB has multiple books, use `split_collection.py` and a dry-run-verified map instead of writing one monolithic book folder.

### Preserve the original source name

The converter records the basename of the EPUB it reads in each chapter's YAML `source` and the book README's `来源`. `--title` does not change that field. When the EPUB is temporary, replace only that exact temporary basename in `source:` and `来源` fields with the original `.mobi`/`.azw3` basename before final verification; avoid global replacements in chapter prose. For a collection, update every leaf README/front matter and the collection summary. Keep titles and image links unchanged, then rerun `verify_output.py`.

The volume audit compares Markdown to the intermediate EPUB, not to the original MOBI/AZW3. Spot-check text and image presence against the original before treating that audit as proof of the format conversion itself.

## Inspect the converted structure

Calibre may preserve a usable TOC, generate one, or lose it. Check the converted EPUB before choosing the split path. One quick read-only probe is:

```bash
python3 - "$tmpdir/book.epub" <<'PY'
import sys
sys.path.insert(0, "<SKILL_DIR>/scripts")
from ebook_to_md import Epub
book = Epub(sys.argv[1])
print("TOC entries:", len(book.toc()))
print("spine documents:", len(book.content_docs()))
PY
```

When a usable TOC remains, trust it and use the normal chapter/collection workflow. When there is no TOC, the main script emits one file per spine document. If Calibre's only boundaries are arbitrary flow-size chunks, regenerate that input with `--flow-size 0 --dont-split-on-page-breaks`; this avoids presenting file-size chunks as chapters, but can produce one very large Markdown file. Report that the converted source has no recoverable chapter structure. Do not infer new chapter boundaries from prose or filenames.

## Calibre's MOBI6 missing-guide failure

On Calibre 9.13.0, one readable MOBI6 file failed with `AssertionError` at `assert guide is not None` in `Mobi6Reader.extract_content`. This exact failure can be worked around without changing the installed Calibre package:

1. Confirm the exception is this missing-guide assertion. `ebook-meta` can check metadata; `calibre-debug --inspect-mobi` can inspect/decompile the source. Run inspection from a temporary working directory because it creates a `decompiled_<title>` directory there.
2. Only for this exact stack trace, copy the Calibre Python package into a disposable temporary directory. On the tested Fedora installation:

   ```bash
   mkdir -p /tmp/calibre-patched
   cp -a /usr/lib64/calibre/calibre /tmp/calibre-patched/calibre
   ```

   In the copied `calibre/ebooks/mobi/reader/mobi6.py`, replace the assertion with a guard so guide-link rewriting runs only when `guide is not None`:

   ```python
   if guide is not None:
       for ref in guide.xpath('descendant::reference'):
           if 'href' in ref.attrib:
               ref.attrib['href'] = os.path.basename(htmlfile) + ref.attrib['href']
   ```

3. On the tested Fedora installation, invoke the copied package with:

   ```bash
   CALIBRE_PYTHON_PATH="/tmp/calibre-patched" \
   PYTHONPATH=/usr/lib64/calibre \
   ebook-convert "book.mobi" "/tmp/book.epub" \
     --flow-size 0 --dont-split-on-page-breaks
   ```

This narrow workaround only skips a rewrite that requires a guide; it does **not** reconstruct the MOBI's navigation. The tested result had no usable TOC, so it correctly became one large Markdown file. Keep the original MOBI untouched, audit and spot-check the EPUB, disclose the lost navigation, and remove the temporary patched package and debug output afterward. If the exception differs, or the copied-package workaround cannot be verified, stop and report the exact error rather than patching the installed Calibre files or guessing chapter boundaries.
