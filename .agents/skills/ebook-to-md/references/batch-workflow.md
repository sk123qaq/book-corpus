# Batch conversion from a book inventory

Use this when a spreadsheet or manifest drives a request to fill missing Markdown outputs for multiple ebooks. The inventory defines eligibility and titles; the source files and output tree must still be checked on disk.

## 1. Build the eligible set

1. Read the inventory's actual headers and identify its missing-output flag, source filename/path, format, book title, and any existing output-directory fields.
2. Select only rows the user asked to process and count by actual filename suffix before conversion. If the user writes `aws3`, resolve it against the files: the supported Kindle format here is `.azw3`.
3. Resolve each row to an exact file under the user-named source directories and confirm it exists. Use the listed source filename when available; use title matching only to resolve a missing path, and inspect ambiguous matches instead of guessing.
4. Keep separate editions as separate inputs even when their titles normalize similarly. Exclude formats outside the request (for example PDF) and include that count in the final report.

**Gate:** every eligible row has one confirmed source file and a format; missing or ambiguous rows are recorded before any output is written.

## 2. Preflight destinations

1. Use the user's named output root and map each human-facing inventory title to a unique output folder. Pass that title with `--title` so the book README/default folder name does not inherit noisy embedded metadata.
2. Plan the complete mapping from each source row to its output directory. A collection can map to several book folders; follow `references/collection-maps.md` and dry-run every map with `verify_map.py`.
3. Check all planned book folders and collection-summary filenames for collisions before conversion. If a destination already exists, inspect its README and contents; decide whether it is already complete or needs a deliberate rebuild. Do not silently merge new chapters with leftovers from an older run.

**Gate:** all eligible rows map to unique destinations, and every existing-path conflict has an explicit resolution.

## 3. Convert with provenance

- Keep source files read-only. Put Calibre intermediates and logs in a temporary workspace; retain intermediates until audits finish, then clean them up.
- Run a single-book conversion for each single title and `split_collection.py` for each confirmed multi-book collection. Give every command a unique output path. Concurrent work is safe only when each writer has disjoint output directories.
- If a temporary EPUB is the converter input, inspect the generated YAML `source` fields and README `来源` lines. They must name the original inventory source, not a temporary filename. `--title` changes the title, not the source field; correct provenance in the generated output before final verification. See `references/mobi-azw3.md` for the Calibre case.
- Leave the inventory workbook unchanged unless the user also asked to update it. Report that choice so a stale status column is not mistaken for a failed conversion.

**Gate:** every eligible row either has a complete output or a specific recorded exception; no temporary file is presented as the original source.

## 4. Verify and report

1. Run `verify_output.py` on every single-book folder and every leaf folder produced by collection splitting. Investigate missing/orphan images and broken links. For identical chapters, compare the source TOC/spine before removing anything; keep faithful source repetitions and explain them.
2. Run `audit_volume.py` for each original EPUB. For MOBI/AZW3, audit the Calibre-produced EPUB and separately spot-check text and images against the original—the audit only measures Markdown extraction from the intermediate EPUB. Use `--map` for collections.
3. Read each output README's `未收录` section. Spot-check a middle chapter, a smallest chapter, and any image-heavy or anomalous output. Explain LOW/HIGH audit results when exclusions, image-only pages, or shared XHTML documents account for the ratio; investigate unexplained gaps.
4. Report source counts by format, output root, book-folder/chapter/image counts, formats intentionally skipped, excluded-page categories, and exceptions. List the rows that failed instead of treating a partial batch as complete.

**Completion criterion:** every eligible inventory row is accounted for; all outputs pass structural/link/image verification or have a verified, documented source-origin exception; every audit anomaly is explained. Any unresolved conversion or content-loss concern remains a blocker, not a completed batch.
