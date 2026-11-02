# Precision Extract API (`/api/v4`) — full reference

Source: <https://mineru.net/apiManage/docs>. Token required on every call:
`Authorization: Bearer <token>` (space after `Bearer`).

Limits: <=200 MB, <=200 pages; supported source formats: PDF, images (png/jpg/jpeg/jp2/webp/gif/bmp),
Doc/Docx, Ppt/Pptx, Xls/Xlsx, HTML. <=50 URLs or files per batch request. Each account has a daily
quota of 1000 pages at the highest priority; pages beyond that are processed with lower priority.
URLs hosted on GitHub/AWS/overseas services may time out on the server side.

Model versions: `pipeline` (default), `vlm` (recommended), `MinerU-HTML` (mandatory for `.html` sources).

## Endpoints

| Purpose | Method + path |
| --- | --- |
| Single file by URL | `POST /extract/task` |
| Single task result | `GET /extract/task/{task_id}` |
| Batch by URL | `POST /extract/task/batch` |
| Local files: request upload URLs | `POST /file-urls/batch` then `PUT` each `file_url` |
| Batch task result | `GET /extract-results/batch/{batch_id}` |

Base: `https://mineru.net/api/v4`. Batch upload URLs stay valid for 24 hours, no Content-Type header
is needed for the PUT, and the extraction task is auto-submitted once the upload completes — there is
no separate submit call for the upload flow.

## Request body — single URL (`POST /extract/task`)

| Parameter | Type | Req | Example | Description |
| --- | --- | --- | --- | --- |
| `url` | string | yes | `https://cdn-mineru.openxlab.org.cn/demo/example.pdf` | File URL (pdf/doc/docx/ppt/pptx/image/html) |
| `is_ocr` | bool | no | `false` | Enable OCR (default false). pipeline/vlm only |
| `enable_formula` | bool | no | `true` | Formula recognition (default true). pipeline/vlm only; for vlm only affects inline formulas |
| `enable_table` | bool | no | `true` | Table recognition (default true). pipeline/vlm only |
| `language` | string | no | `ch` | Document language (default `ch`). pipeline/vlm only |
| `data_id` | string | no | `abc**` | Business id: letters/digits/`_`/`-`/`.`, <=128 chars; echoed back in the result |
| `callback` | string | no | `http://127.0.0.1/callback` | Result push URL (HTTP/HTTPS, POST, UTF-8, JSON). Receives `checksum` + `content`; return HTTP 200 or MinerU retries up to 5 times |
| `seed` | string | no | `abc**` | Signing seed (letters/digits/`_`, <=64 chars). **Required when `callback` is used** |
| `extra_formats` | [string] | no | `["docx","html"]` | Extra exports: `docx`, `html`, `latex`. Markdown+JSON always included. Not for HTML sources |
| `page_ranges` | string | no | `1-200` | Comma list: `"2,4-6"` -> pages 2,4,5,6; `"2--2"` -> page 2 to the second-to-last |
| `model_version` | string | no | `vlm` | `pipeline` / `vlm` / `MinerU-HTML` (default pipeline) |
| `no_cache` | bool | no | `false` | Ignore the server-side URL content cache and re-fetch |
| `cache_tolerance` | int | no | `900` | Acceptable cache age in seconds (default 900 = 15 min). Used when `no_cache=false` |

Response: `{code:0, msg:"ok", trace_id, data:{task_id}}`.

## Request body — batch by URL (`POST /extract/task/batch`)

Top-level: `files` (array), `enable_formula`, `enable_table`, `language`, `callback`, `seed`,
`extra_formats`, `model_version`, `no_cache`, `cache_tolerance` — same semantics as above.
Per file entry: `url` (required), `is_ocr`, `data_id`, `page_ranges`.

```json
{"files":[{"url":"https://.../example.pdf","data_id":"abcd"}],"model_version":"vlm"}
```

Response: `data.batch_id`. Max 50 entries per request.

## Request body — local file upload (`POST /file-urls/batch`)

Top-level identical to the batch-by-URL body. Per file entry: `name` (required, **keep the real
extension**), `is_ocr`, `data_id`, `page_ranges`.

```json
{"files":[{"name":"demo.pdf","data_id":"abcd"}],"model_version":"vlm"}
```

Response `data`: `batch_id`, `file_urls[]` (parallel to the files array). PUT each local file to its URL:

```bash
curl -X PUT -T /path/to/your/file.pdf 'https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/***'
```

## Task query (`GET /extract/task/{task_id}`)

| Field | Description |
| --- | --- |
| `data.task_id` | Task id |
| `data.data_id` | Echo of the request `data_id`, when supplied |
| `data.state` | `done` / `pending` / `running` / `failed` / `converting` |
| `data.full_zip_url` | Result zip (valid when `state=done`) |
| `data.err_msg` | Failure reason (valid when `state=failed`) |
| `data.extract_progress.{extracted_pages,total_pages,start_time}` | Progress (valid when `state=running`) |

## Batch query (`GET /extract-results/batch/{batch_id}`)

`data.batch_id` plus `data.extract_result[]`, each entry: `file_name`, `state`
(`done` / `waiting-file` / `pending` / `running` / `failed` / `converting`), `full_zip_url`,
`err_msg`, `data_id`, `extract_progress.*`.

```json
{"code":0,"data":{"batch_id":"2bb2f0ec-...","extract_result":[
  {"file_name":"example.pdf","state":"done","err_msg":"","full_zip_url":"https://...zip"},
  {"file_name":"demo.pdf","state":"running","err_msg":"",
   "extract_progress":{"extracted_pages":1,"total_pages":2,"start_time":"2025-01-20 11:43:20"}}]},
 "msg":"ok","trace_id":"..."}
```

## Result zip contents

Non-HTML sources (<https://opendatalab.github.io/MinerU/reference/output_files/>):
`full.md` (Markdown), `layout.json` (= middle.json), `*_model.json` (= model.json),
`*_content_list.json` (= content_list.json), plus extracted `images/`.
HTML sources: `full.md` (Markdown) and `main.html` (extracted main content).

## Common error codes

| Code | Meaning | Fix |
| --- | --- | --- |
| `A0202` | Invalid token | Check the token and the `Bearer ` prefix, or create a new one |
| `A0211` | Token expired | Issue a new token |
| `-500` | Parameter error | Check parameter types and `Content-Type` |
| `-10001` | Service error | Retry later |
| `-10002` | Request parameter error | Fix the request format |
| `-60001` | Failed to generate upload URL | Retry later |
| `-60002` | File format not matched | Ensure URL/filename carries a supported extension (pdf, doc, docx, ppt, pptx, xls, xlsx, png, jp(e)g) |
| `-60003` | File read failure | File may be corrupted — re-upload |
| `-60004` | Empty file | Upload a valid file |
| `-60005` | File size exceeds limit | <=200 MB |
| `-60006` | Page count exceeds limit | Split the file |
| `-60007` | Model service unavailable | Retry later / contact support |
| `-60008` | File read timeout | Check that the URL is reachable |
| `-60009` | Submission queue full | Retry later |
| `-60010` | Extract failed | Retry later |
| `-60011` | Failed to get valid file | Ensure the upload finished |
| `-60012` | Task not found | Verify `task_id` |
| `-60013` | No permission for this task | Only the submitting account can query |
| `-60014` | Deleting a running task | Wait for it to finish |
| `-60015` | File conversion failed | Convert to PDF manually and re-upload |
| `-60016` | Format conversion failed | Try another `extra_formats` value or retry |
| `-60017` | Retry limit reached | Wait for model upgrades |
| `-60018` | Daily extract task limit reached | Retry tomorrow |
| `-60019` | HTML extract quota exhausted | Retry tomorrow |
| `-60020` | File splitting failed | Retry later |
| `-60021` | Failed to read page count | Retry later |
| `-60022` | Web page read failure | Network/rate-limit issue — retry later |

## Callback verification

Push payload carries `content` (JSON string of the task-query `data` object) and `checksum` =
`SHA256(uid + seed + content)`; `uid` is in the account's personal center. Return HTTP 200 to acknowledge.

## Language values (`language`)

Standalone packs:

| Value | Included | Note |
| --- | --- | --- |
| `ch` | Chinese, English, Chinese Traditional | default |
| `ch_server` | + Japanese | Traditional Chinese / handwriting-heavy |
| `en` | English | English only |
| `japan` | Chinese, English, Chinese Traditional, Japanese | Japanese-first |
| `korean` | Korean, English | Korean |
| `chinese_cht` | Chinese, English, Chinese Traditional, Japanese | Traditional Chinese-first |
| `ta` | Tamil, English | Tamil |
| `te` | Telugu, English | Telugu |
| `ka` | Kannada | Kannada |
| `el` | Greek, English | Greek |
| `th` | Thai, English | Thai |

Family packs: `latin` (French, German, Spanish, Portuguese, Vietnamese, Turkish, ... 40+),
`arabic` (Arabic, Persian, Uyghur, Urdu, ...), `cyrillic` (Russian, Ukrainian, Bulgarian, Kazakh, ...),
`east_slavic` (Russian, Belarusian, Ukrainian, English),
`devanagari` (Hindi, Marathi, Nepali, Sanskrit, ...).
