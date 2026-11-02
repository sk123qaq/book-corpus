---
name: mineru
description: >
  Parse PDFs, Office docs and scans to Markdown via the MinerU cloud API (layout, tables,
  formulas, batch). Use when a document needs high-fidelity structure extraction, OCR for a
  scanned PDF, or Markdown output from a URL that local pymupdf/marker cannot handle.
argument-hint: "<url|file>... [-o OUTDIR]"
---

# MinerU Document Extraction API

MinerU (OpenDataLab) is a **cloud** document-extraction service. Use it when a job needs high-accuracy
structure: complex/scanned layouts, tables, formulas, multi-column PDFs, or batch processing — without
installing ~3-5GB of local models (contrast local pymupdf/marker pipelines).

Docs: <https://mineru.net/apiManage/docs> · Token & quota page: <https://mineru.net/apiManage> ·
Self-host repo: <https://github.com/opendatalab/MinerU>

## Pick the API first

| Dimension | Precision `/api/v4` | Agent `/api/v1/agent` |
| --- | --- | --- |
| Token | **required** (Bearer) | none (per-IP rate limit, HTTP 429) |
| Endpoints | `/extract/task`, `/extract/task/batch`, `/file-urls/batch` | `/parse/url`, `/parse/file`, `/parse/{task_id}` |
| Max file size | 200 MB | 10 MB |
| Max pages | 200 | 20 |
| Batch | up to 50 per request | single file only |
| Output | zip: `full.md`, `layout.json`, `*_model.json`, `*_content_list.json`, `images/`; optional docx/html/latex | Markdown only (CDN URL) |
| Models | `pipeline` (default) / `vlm` (recommended) / `MinerU-HTML` | fixed lightweight pipeline (Office files use the native Office API) |

Rule of thumb: **precision by default** (token present), agent for quick single small files or no token.

## Credentials

The token is read in this order: `--token`, `$MINERU_API_KEY` / `$MINERU_TOKEN`,
`~/.config/mineru/api_key`, `~/.config/mineru/token`.

```bash
mkdir -p ~/.config/mineru && chmod 700 ~/.config/mineru
printf '%s' 'sk-...' > ~/.config/mineru/api_key && chmod 600 ~/.config/mineru/api_key
export MINERU_API_KEY='sk-...'   # alternative, no file needed
```

The key on this machine is already stored at `~/.config/mineru/api_key`. Create or rotate one at
<https://mineru.net/apiManage>. Header format is `Authorization: Bearer <token>` — the space matters.
Never print the key; the CLI redacts it.

## Use the CLI (preferred)

`scripts/mineru.py` submits, polls, downloads and unpacks in one shot. Deps: Python 3.9+ and
`requests` (`pip install --user requests`, or `uv run --with requests python3 scripts/mineru.py ...`).

```bash
python3 scripts/mineru.py https://cdn-mineru.openxlab.org.cn/demo/example.pdf -o out/
python3 scripts/mineru.py report.pdf --mode precision --model vlm --lang en --pages 1-20
python3 scripts/mineru.py paper.pdf --mode agent            # token-free, <=10MB/20p
python3 scripts/mineru.py a.pdf b.pdf c.pdf -o out/         # precision batch (<=50/request)
python3 scripts/mineru.py https://x/report.pdf --json --print-md 2000
python3 scripts/mineru.py archive.html --extra-formats docx latex
```

`--mode auto` (default) = precision when a token is found, otherwise agent. Output lands in
`-o/--out/<name>/` (`full.md` plus images/json for precision). Exit code 1 if any input failed;
`--json` prints a machine-readable summary on stdout; `--ocr` for scanned documents.

## Raw endpoints (when not using the script)

**Precision — remote URL, single file**
```bash
curl -X POST https://mineru.net/api/v4/extract/task \
  -H 'Authorization: Bearer ***' -H 'Content-Type: application/json' \
  -d '{"url":"https://.../x.pdf","model_version":"vlm"}'          # -> data.task_id
curl -H 'Authorization: Bearer ***' https://mineru.net/api/v4/extract/task/{task_id}
```

**Precision — local files (no separate submit call needed)**
```bash
curl -X POST https://mineru.net/api/v4/file-urls/batch \
  -H 'Authorization: Bearer ***' -H 'Content-Type: application/json' \
  -d '{"files":[{"name":"demo.pdf","data_id":"abcd"}],"model_version":"vlm"}'
# -> data.batch_id + data.file_urls[]; PUT each file to its URL (24h validity), then:
curl -H 'Authorization: Bearer ***' https://mineru.net/api/v4/extract-results/batch/{batch_id}
```

**Precision — batch by URL**: `POST /api/v4/extract/task/batch` with `{"files":[{"url":...}]}` →
`batch_id` → same batch results endpoint.

**Agent — URL / signed upload / query**
```bash
curl -X POST https://mineru.net/api/v1/agent/parse/url  -H 'Content-Type: application/json' -d '{"url":"https://.../x.pdf","language":"ch"}'
curl -X POST https://mineru.net/api/v1/agent/parse/file -H 'Content-Type: application/json' -d '{"file_name":"x.pdf"}'   # -> task_id + file_url (PUT to it)
curl https://mineru.net/api/v1/agent/parse/{task_id}                                                                      # -> markdown_url when done
```

## Task states (poll until terminal)

- Precision: `pending` → `running` → `done` | `failed`, plus `converting` (format conversion) and
  `waiting-file` (upload not seen yet); progress in `extract_progress.{extracted_pages,total_pages,start_time}`.
- Agent: `waiting-file` → `uploading` → `pending` → `running` → `done` | `failed` (`err_code` + `err_msg`).
- Poll every 3-5s; a small PDF finishes in seconds, 200 pages takes minutes. Allow long timeouts and
  report `task_id` whenever you time out, so the result can be fetched later.

## Scanned books: pages silently come back empty

On 1-bit CCITT scans MinerU returns `done` with the right page count while **whole pages**
(3% of a 518-page book in testing) come back holding only a page number. Nothing flags it;
you have to audit. Detect candidates from ink density + text-line bands (`scripts/page_health.py`),
then rescue in this order: re-request those pages with `--model pipeline --pages "..."`, and for
whatever stays empty extract each page to its own 1-page PDF (`scripts/rescue_pages.py`) and submit
those as separate files. Also note `full.md` **drops `page_footnote` blocks** on scanned books, so
rebuild from `*_content_list.json` when the notes matter. Details, the page-index mapping trap and
the completeness checks: `references/scanned-book-qa.md`.

## Pitfalls that actually bite

- **Batch upload URLs expire in 24h**, and uploading the file is what creates the task — do not hunt for
  a submit endpoint afterwards.
- **HTML sources require `model_version: MinerU-HTML`** explicitly, and `extra_formats` does not apply to them.
- **GitHub/AWS/overseas URLs often time out** on the server side. Re-host the file or use the upload flow;
  pass `no_cache: true` when the URL content changed within the last 15 minutes (default cache tolerance 900s).
- **Page-range syntax differs**: precision `page_ranges` takes comma lists (`"2,4-6"`; `"2--2"` = page 2 to
  the second-to-last); agent `page_range` takes only `from-to` or a single page.
- **Quota/limits**: v4 allows 200MB / 200 pages / ≤50 entries per batch request, and each account gets
  1000 pages/day at highest priority (beyond that, lower priority). Agent: 10MB / 20 pages / one file, HTTP 429 on IP rate limit.
- **`is_ocr` defaults to false** — scanned or image-only documents come back nearly empty unless you pass
  `is_ocr: true`. Tables and formulas default to true.
- **`language` defaults to `ch`** (Chinese + English); use `en`, `japan`, `korean`, or a family pack
  (`latin`, `arabic`, `cyrillic`, ...) for other scripts. Full list in `references/precision-api.md`.
- **Callback mode instead of polling**: pass `callback` + `seed`; the push body carries `content` (JSON string)
  and `checksum` = SHA256(uid + seed + content). Return HTTP 200 or MinerU retries 5 times then gives up.
- **Zip layout**: `full.md` is the Markdown; `layout.json` = middle.json, `*_model.json` = model.json,
  `*_content_list.json` = content_list.json, `images/` holds extracted images. HTML sources also carry `main.html`.
- **Pick the right tool**: plain text-layer PDFs, quick greps, or offline work → local `pymupdf`
  (or `pdftotext`). MinerU wins on scanned pages, tables, formulas, and layout fidelity.

## Errors

Codes come back in `code` (v4) / `data.err_code` (agent) with a message in `msg` / `data.err_msg`:

- Auth: `A0202` invalid token (check the `Bearer ` prefix), `A0211` token expired.
- Files: `-60002` format not matched, `-60005` >200MB, `-60006` >200 pages, `-60012` task not found,
  `-60018` daily task limit, `-60019` HTML quota exhausted.
- Agent: `-30001` >10MB, `-30002` unsupported type, `-30003` >20 pages (pass `page_range` or use precision),
  `-30004` bad parameters.

Full tables: `references/precision-api.md`, `references/agent-api.md`.

## Files

- `scripts/mineru.py` — end-to-end CLI (submit → poll → download → unpack) for both APIs.
- `references/precision-api.md` — every v4 parameter, response field, error code, language value.
- `references/agent-api.md` — token-free v1 agent API, signed-upload flow, error codes.
