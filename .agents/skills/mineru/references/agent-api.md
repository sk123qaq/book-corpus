# Agent Lightweight Extract API (`/api/v1/agent`) — full reference

Source: <https://mineru.net/apiManage/docs>. **No login, no token**; rate limited per IP (HTTP 429
when exceeded). Designed for AI-agent workflows; Markdown output only, returned as a CDN link.

Limits: <=10 MB, <=20 pages, single file per request. Supported: PDF, images (png/jpg/jpeg/jp2/webp/gif/bmp),
Docx, PPTx, Xlsx. PDF/images run the lightweight pipeline model; Word/PPT are parsed by the native
Office API. HTML is not supported. `enable_table`/`enable_formula` apply to PDF only.

| Purpose | Method + path |
| --- | --- |
| Extract a remote URL | `POST /parse/url` |
| Upload a local file (signed upload) | `POST /parse/file` then `PUT` to the returned `file_url` |
| Query result | `GET /parse/{task_id}` |

Base: `https://mineru.net/api/v1/agent`. Request bodies are JSON (`Content-Type: application/json`);
multipart/form-data is not supported, and no `Authorization` header is needed.

## `POST /parse/url`

| Parameter | Type | Req | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Remote file URL (PDF, image, Doc/Docx, PPT/PPTx, Xlsx). HTML unsupported |
| `file_name` | string | no | Name with extension to disambiguate the type; otherwise parsed from the URL |
| `enable_table` | bool | no | Default `true`. PDF only |
| `is_ocr` | bool | no | Default `false`. PDF only |
| `enable_formula` | bool | no | Default `true`. PDF only |
| `language` | string | no | Default `ch`. PDF only |
| `page_range` | string | no | PDF only: `from-to` (e.g. `1-10`) or a single page (e.g. `5`). **No comma lists** |

```bash
curl -X POST 'https://mineru.net/api/v1/agent/parse/url' \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://cdn-mineru.openxlab.org.cn/demo/example.pdf","language":"ch","page_range":"1-10","enable_table":true,"is_ocr":false,"enable_formula":true}'
```

Response: `data.task_id`.

## `POST /parse/file` (signed upload)

Body: `file_name` (required, with extension) plus the same optional parameters as above
(`language`, `enable_table`, `is_ocr`, `enable_formula`, `page_range`).

Response `data`: `task_id` and `file_url` (OSS signed URL). PUT the file to `file_url`; the backend then
detects the upload and starts extraction. One file per request — batch upload is not supported.

```python
res = requests.post("https://mineru.net/api/v1/agent/parse/file",
                    json={"file_name": "document.pdf", "language": "ch"})
data = res.json()["data"]
with open("document.pdf", "rb") as f:
    requests.put(data["file_url"], data=f)   # 200/201 means success
```

```bash
curl -X PUT '<file_url>' --data-binary '@document.pdf'
```

## `GET /parse/{task_id}`

| Field | Description |
| --- | --- |
| `data.task_id` | Task id (same as at submission) |
| `data.state` | `waiting-file` (upload mode, file not seen yet) / `uploading` (downloading file) / `pending` / `running` / `done` / `failed` |
| `data.markdown_url` | CDN link to the Markdown result (valid when `state=done`) |
| `data.err_msg` | Error message (valid when `state=failed`) |
| `data.err_code` | Error code (valid when `state=failed`) |

```json
{"code":0,"data":{"task_id":"a90e6ab6-...","state":"done",
  "markdown_url":"https://cdn-mineru.openxlab.org.cn/pdf/a90e6ab6-.../full.md"},
 "msg":"ok","trace_id":"..."}
```

Poll every ~3 s; download `markdown_url` when done.

## Error codes

| Code | Meaning | Strategy |
| --- | --- | --- |
| `-30001` | File exceeds 10 MB | Use the precision API or split the file |
| `-30002` | File type unsupported | PDF/image/Doc/PPT/Excel only |
| `-30003` | Page count exceeds the limit | Use the precision API or pass `page_range` |
| `-30004` | Request parameter error | Check for missing/invalid parameters |
| HTTP 429 | Per-IP rate limit hit | Back off and retry later |

## Minimal end-to-end example

```python
import requests, time
BASE = "https://mineru.net/api/v1/agent"

task_id = requests.post(f"{BASE}/parse/url",
    json={"url": url, "language": "ch", "is_ocr": False}).json()["data"]["task_id"]

while True:
    data = requests.get(f"{BASE}/parse/{task_id}").json()["data"]
    if data["state"] == "done":
        print(requests.get(data["markdown_url"]).text)
        break
    if data["state"] == "failed":
        raise SystemExit(f"{data.get('err_code')}: {data.get('err_msg')}")
    time.sleep(3)
```
