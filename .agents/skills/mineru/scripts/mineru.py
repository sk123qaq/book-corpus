#!/usr/bin/env python3
"""MinerU document-extraction CLI: precision /api/v4 and token-free agent /api/v1.

Examples:
  python3 mineru.py https://cdn-mineru.openxlab.org.cn/demo/example.pdf -o out/
  python3 mineru.py report.pdf --model vlm --lang en --pages 1-20
  python3 mineru.py small.pdf --mode agent          # no token, <=10MB / 20 pages
  python3 mineru.py a.pdf b.pdf c.pdf -o out/       # precision batch (<=50 per request)
  python3 mineru.py https://x/y.pdf --json --print-md 2000

Token lookup: --token, $MINERU_API_KEY/$MINERU_TOKEN, ~/.config/mineru/api_key,
~/.config/mineru/token. With no token, --mode auto falls back to the agent API.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import zipfile

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("missing dependency: pip install requests")

PRECISION_BASE = "https://mineru.net/api/v4"
AGENT_BASE = "https://mineru.net/api/v1/agent"
TOKEN_PATHS = (
    pathlib.Path(os.path.expanduser("~/.config/mineru/api_key")),
    pathlib.Path(os.path.expanduser("~/.config/mineru/token")),
)
BATCH_MAX = 50
PRECISION_MAX_BYTES = 200 * 1024 * 1024
AGENT_MAX_BYTES = 10 * 1024 * 1024
PRECISION_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".html",
                 ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"}
AGENT_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xlsx",
             ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"}


class MineruError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[mineru] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- helpers

def resolve_token(explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip()
    env = (os.environ.get("MINERU_API_KEY") or os.environ.get("MINERU_TOKEN") or "").strip()
    if env:
        return env
    for path in TOKEN_PATHS:
        if not path.is_file():
            continue
        try:
            if path.stat().st_mode & 0o077:
                log(f"warning: {path} is readable by other users; chmod 600 it")
        except OSError:
            pass
        value = path.read_text().strip()
        if value:
            log(f"using token from {path}")
            return value
    return None


def _request(method, url, *, headers=None, json_body=None, timeout=60, attempts=3, stream=False):
    for attempt in range(attempts):
        try:
            resp = requests.request(method, url, headers=headers, json=json_body,
                                    timeout=timeout, stream=stream)
        except requests.RequestException as exc:
            if attempt < attempts - 1:
                log(f"network error ({exc.__class__.__name__}), retry {attempt + 1}/{attempts - 1}")
                time.sleep(2 * (attempt + 1))
                continue
            raise MineruError(f"{method} {url} failed: {exc}") from exc
        if resp.status_code >= 500 and attempt < attempts - 1:
            log(f"HTTP {resp.status_code}, retry {attempt + 1}/{attempts - 1}")
            time.sleep(2 * (attempt + 1))
            continue
        return resp
    raise MineruError(f"{method} {url} failed after {attempts} attempts")


def _ok(resp, action: str) -> dict:
    if resp.status_code == 429:
        raise MineruError(f"{action}: HTTP 429 — rate limited (agent API is per-IP); back off and retry")
    try:
        body = resp.json()
    except ValueError:
        raise MineruError(f"{action}: HTTP {resp.status_code}, non-JSON body: {resp.text[:200]!r}")
    if resp.status_code != 200 or body.get("code") != 0:
        raise MineruError(
            f"{action} failed: HTTP {resp.status_code} code={body.get('code')} "
            f"msg={body.get('msg')} trace_id={body.get('trace_id')}"
        )
    return body.get("data") or {}


def _safe(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name).strip("._")
    return keep[:120] or "document"


def _stem(source: str) -> str:
    tail = source.split("?")[0].rstrip("/").split("/")[-1]
    return _safe(pathlib.Path(tail).stem or "document")


def _progress(state, prog, started) -> None:
    elapsed = int(time.time() - started)
    if prog:
        log(f"[{elapsed}s] {state} {prog.get('extracted_pages', '?')}/{prog.get('total_pages', '?')} pages")
    else:
        log(f"[{elapsed}s] {state}")


# ------------------------------------------------------------------ precision API

def _precision_headers(token: str) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _precision_model(name: str, requested: str) -> str:
    return "MinerU-HTML" if name.lower().endswith(".html") else requested


def _precision_common(args) -> dict:
    common: dict = {}
    if args.lang:
        common["language"] = args.lang
    if args.no_formula:
        common["enable_formula"] = False
    if args.no_table:
        common["enable_table"] = False
    if args.extra_formats:
        common["extra_formats"] = args.extra_formats
    if args.no_cache:
        common["no_cache"] = True
    return common


def _finish_precision_body(body: dict, name: str, requested_model: str) -> dict:
    body["model_version"] = _precision_model(name, requested_model)
    if body["model_version"] == "MinerU-HTML":
        body.pop("extra_formats", None)  # not applicable to HTML sources
    return body


def submit_precision_url(url: str, token: str, args) -> str:
    body = _precision_common(args)
    body["url"] = url
    if args.pages:
        body["page_ranges"] = args.pages
    if args.ocr:
        body["is_ocr"] = True
    if args.data_id:
        body["data_id"] = args.data_id
    _finish_precision_body(body, url, args.model)
    data = _ok(_request("POST", f"{PRECISION_BASE}/extract/task",
                        headers=_precision_headers(token), json_body=body), "submit task")
    return data["task_id"]


def submit_precision_url_batch(urls: list[str], token: str, args) -> str:
    files = []
    for url in urls:
        entry = {"url": url}
        if args.pages:
            entry["page_ranges"] = args.pages
        if args.ocr:
            entry["is_ocr"] = True
        files.append(entry)
    body = _precision_common(args)
    body["files"] = files
    _finish_precision_body(body, urls[0], args.model)
    data = _ok(_request("POST", f"{PRECISION_BASE}/extract/task/batch",
                        headers=_precision_headers(token), json_body=body), "submit batch")
    return data["batch_id"]


def submit_precision_files(paths: list[pathlib.Path], token: str, args) -> str:
    files = []
    for path in paths:
        entry = {"name": path.name}
        if args.pages:
            entry["page_ranges"] = args.pages
        if args.ocr:
            entry["is_ocr"] = True
        files.append(entry)
    body = _precision_common(args)
    body["files"] = files
    _finish_precision_body(body, paths[0].name, args.model)
    data = _ok(_request("POST", f"{PRECISION_BASE}/file-urls/batch",
                        headers=_precision_headers(token), json_body=body), "request upload urls")
    upload_urls = data.get("file_urls") or []
    if len(upload_urls) != len(paths):
        raise MineruError(f"expected {len(paths)} upload urls, got {len(upload_urls)}")
    for path, upload_url in zip(paths, upload_urls):
        with open(path, "rb") as fh:  # no Content-Type header is required for the PUT
            resp = requests.put(upload_url, data=fh, timeout=600)
        if resp.status_code not in (200, 201, 204):
            raise MineruError(f"upload of {path.name} failed: HTTP {resp.status_code}")
        log(f"uploaded {path.name} ({path.stat().st_size / 1e6:.1f}MB)")
    return data["batch_id"]


def poll_precision_task(task_id: str, token: str, timeout: int, interval: int) -> dict:
    url = f"{PRECISION_BASE}/extract/task/{task_id}"
    started = time.time()
    while True:
        data = _ok(_request("GET", url, headers=_precision_headers(token)), "query task")
        state = data.get("state")
        if state == "done":
            return data
        if state == "failed":
            raise MineruError(f"task {task_id} failed: {data.get('err_msg')}")
        _progress(state, data.get("extract_progress"), started)
        if time.time() - started > timeout:
            raise MineruError(f"timed out after {timeout}s; query later: GET {url}")
        time.sleep(interval)


def poll_precision_batch(batch_id: str, token: str, timeout: int, interval: int) -> list[dict]:
    url = f"{PRECISION_BASE}/extract-results/batch/{batch_id}"
    started = time.time()
    while True:
        data = _ok(_request("GET", url, headers=_precision_headers(token)), "query batch")
        results = data.get("extract_result") or []
        settled = [r for r in results if r.get("state") in ("done", "failed")]
        if results and len(settled) == len(results):
            return results
        elapsed = int(time.time() - started)
        log(f"[{elapsed}s] {len(settled)}/{len(results) or '?'} files settled")
        if time.time() - started > timeout:
            raise MineruError(f"timed out after {timeout}s; query later: GET {url}")
        time.sleep(interval)


def download_zip(zip_url: str, dest: pathlib.Path, keep_zip: bool) -> pathlib.Path:
    dest.mkdir(parents=True, exist_ok=True)
    resp = _request("GET", zip_url, timeout=600, stream=True)
    if resp.status_code != 200:
        raise MineruError(f"result download failed: HTTP {resp.status_code}")
    archive = dest / (pathlib.Path(zip_url.split("?")[0]).name or "result.zip")
    with open(archive, "wb") as fh:
        for chunk in resp.iter_content(65536):
            fh.write(chunk)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    if not keep_zip:
        archive.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------------- agent API

def _agent_opts(body: dict, args) -> dict:
    if args.lang:
        body["language"] = args.lang
    if args.pages:
        body["page_range"] = args.pages
    if args.ocr:
        body["is_ocr"] = True
    if args.no_table:
        body["enable_table"] = False
    if args.no_formula:
        body["enable_formula"] = False
    return body


def agent_submit_url(url: str, args) -> str:
    body = _agent_opts({"url": url}, args)
    data = _ok(_request("POST", f"{AGENT_BASE}/parse/url", json_body=body), "submit agent task")
    return data["task_id"]


def agent_submit_file(path: pathlib.Path, args) -> str:
    body = _agent_opts({"file_name": path.name}, args)
    data = _ok(_request("POST", f"{AGENT_BASE}/parse/file", json_body=body), "request signed upload url")
    with open(path, "rb") as fh:
        resp = requests.put(data["file_url"], data=fh, timeout=600)
    if resp.status_code not in (200, 201, 204):
        raise MineruError(f"signed upload failed: HTTP {resp.status_code}")
    log(f"uploaded {path.name} ({path.stat().st_size / 1e6:.1f}MB)")
    return data["task_id"]


def poll_agent(task_id: str, timeout: int, interval: int) -> dict:
    url = f"{AGENT_BASE}/parse/{task_id}"
    started = time.time()
    while True:
        data = _ok(_request("GET", url), "query agent task")
        state = data.get("state")
        if state == "done":
            return data
        if state == "failed":
            raise MineruError(f"task {task_id} failed [{data.get('err_code')}]: {data.get('err_msg')}")
        _progress(state, None, started)
        if time.time() - started > timeout:
            raise MineruError(f"timed out after {timeout}s; query later: GET {url}")
        time.sleep(interval)


def download_markdown(markdown_url: str, dest_file: pathlib.Path) -> pathlib.Path:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    resp = _request("GET", markdown_url, timeout=300)
    if resp.status_code != 200:
        raise MineruError(f"markdown download failed: HTTP {resp.status_code}")
    dest_file.write_bytes(resp.content)
    return dest_file


# --------------------------------------------------------------------------- main

def _record(source: str, mode: str, task_id: str, dest: pathlib.Path | None = None,
            error: str | None = None) -> dict:
    rec = {"input": source, "mode": mode, "task_id": task_id}
    if dest is not None:
        markdown = dest / "full.md"
        if markdown.is_file():
            rec["markdown"] = str(markdown)
            rec["markdown_bytes"] = markdown.stat().st_size
        rec["output_dir"] = str(dest)
        rec["files"] = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    if error:
        rec["error"] = error
    return rec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MinerU document extraction (precision /api/v4 + agent /api/v1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="URL(s) or local file path(s)")
    parser.add_argument("--mode", choices=["auto", "precision", "agent"], default="auto",
                        help="auto = precision when a token is available, else agent")
    parser.add_argument("--model", choices=["pipeline", "vlm"], default="vlm",
                        help="precision model (HTML files are forced to MinerU-HTML)")
    parser.add_argument("--lang", default="ch", help="ch/en/japan/korean/latin/arabic/...")
    parser.add_argument("--pages", default=None,
                        help="page range: '2,4-6' (precision) or '1-10'/single page (agent)")
    parser.add_argument("--ocr", action="store_true", help="enable OCR for scanned/image docs")
    parser.add_argument("--no-table", action="store_true", help="disable table recognition")
    parser.add_argument("--no-formula", action="store_true", help="disable formula recognition")
    parser.add_argument("--extra-formats", nargs="*", default=None,
                        choices=["docx", "html", "latex"], help="precision only")
    parser.add_argument("--no-cache", action="store_true", help="precision only: bypass URL cache")
    parser.add_argument("--data-id", default=None, help="precision only: business id for the task")
    parser.add_argument("--token", default=None, help="overrides env/secret file")
    parser.add_argument("-o", "--out", default="mineru_out", help="output directory")
    parser.add_argument("--timeout", type=int, default=900, help="poll timeout in seconds")
    parser.add_argument("--interval", type=int, default=3, help="poll interval in seconds")
    parser.add_argument("--keep-zip", action="store_true", help="keep the downloaded zip archive")
    parser.add_argument("--print-md", type=int, default=0, metavar="N",
                        help="print the first N characters of each markdown on stdout")
    parser.add_argument("--json", action="store_true", help="print a JSON summary on stdout")
    return parser


def run_precision(urls, files, token, args, out_root) -> list[dict]:
    records: list[dict] = []

    def consume_batch(batch_id: str) -> None:
        log(f"batch_id={batch_id}")
        results = poll_precision_batch(batch_id, token, args.timeout, args.interval)
        for item in results:
            name = item.get("file_name") or "document"
            if item.get("state") == "failed":
                records.append(_record(name, "precision", batch_id, error=item.get("err_msg")))
                continue
            dest = out_root / _safe(pathlib.Path(name).stem or "document")
            download_zip(item["full_zip_url"], dest, args.keep_zip)
            records.append(_record(name, "precision", batch_id, dest=dest))

    if len(urls) == 1 and not files:
        task_id = submit_precision_url(urls[0], token, args)
        log(f"task_id={task_id}")
        data = poll_precision_task(task_id, token, args.timeout, args.interval)
        dest = out_root / _safe(data.get("data_id") or _stem(urls[0]))
        download_zip(data["full_zip_url"], dest, args.keep_zip)
        records.append(_record(urls[0], "precision", task_id, dest=dest))
    elif urls:
        if len(urls) > BATCH_MAX:
            raise MineruError(f"max {BATCH_MAX} URLs per batch request (got {len(urls)})")
        consume_batch(submit_precision_url_batch(urls, token, args))

    if files:
        if len(files) > BATCH_MAX:
            raise MineruError(f"max {BATCH_MAX} files per batch request (got {len(files)})")
        for path in files:
            size = path.stat().st_size
            if size > PRECISION_MAX_BYTES:
                raise MineruError(f"{path.name} is {size / 1e6:.1f}MB > 200MB precision limit")
            if path.suffix.lower() not in PRECISION_EXT:
                log(f"warning: {path.suffix or 'no extension'} may be unsupported "
                    f"(pdf/doc/docx/ppt/pptx/xls/xlsx/image/html)")
        consume_batch(submit_precision_files(files, token, args))
    return records


def run_agent(urls, files, args, out_root) -> list[dict]:
    if urls:
        source = urls[0]
        task_id = agent_submit_url(source, args)
    else:
        path = files[0]
        size = path.stat().st_size
        if size > AGENT_MAX_BYTES:
            raise MineruError(f"{path.name} is {size / 1e6:.1f}MB > 10MB agent limit; use --mode precision")
        if path.suffix.lower() not in AGENT_EXT:
            raise MineruError(f"agent API does not support {path.suffix or 'extensionless files'}"
                              f" (pdf/image/doc/docx/ppt/pptx/xlsx only)")
        source = path.name
        task_id = agent_submit_file(path, args)
    log(f"task_id={task_id}")
    data = poll_agent(task_id, args.timeout, args.interval)
    dest = out_root / _safe(data.get("data_id") or _stem(source))
    download_markdown(data["markdown_url"], dest / "full.md")
    return [_record(source, "agent", task_id, dest=dest)]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    urls = [i for i in args.inputs if i.startswith(("http://", "https://"))]
    files = []
    for raw in args.inputs:
        if raw.startswith(("http://", "https://")):
            continue
        path = pathlib.Path(raw).expanduser()
        if not path.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            return 2
        files.append(path)

    token = None if args.mode == "agent" else resolve_token(args.token)
    mode = args.mode
    if mode == "auto":
        mode = "precision" if token else "agent"
        log(f"mode=auto -> {mode}" + ("" if token else " (no token found)"))
    if mode == "precision" and not token:
        print("precision API needs a token: export MINERU_API_KEY=... or write it to "
              "~/.config/mineru/api_key (create one at https://mineru.net/apiManage)",
              file=sys.stderr)
        return 2
    if mode == "agent":
        if len(urls) + len(files) != 1:
            print("agent API is single-file only; pass one input or use --mode precision", file=sys.stderr)
            return 2
        if args.pages and ("," in args.pages):
            print("agent page_range accepts 'from-to' or a single page, not comma lists", file=sys.stderr)
            return 2

    out_root = pathlib.Path(args.out).expanduser()
    records: list[dict] = []
    failure: str | None = None
    try:
        if mode == "precision":
            records = run_precision(urls, files, token, args, out_root)
        else:
            records = run_agent(urls, files, args, out_root)
    except MineruError as exc:
        failure = str(exc)
        log(f"ERROR: {exc}")

    failed = [r for r in records if r.get("error")]
    if args.json:
        print(json.dumps({"ok": not failure and not failed, "mode": mode,
                          "results": records, "error": failure}, indent=2, ensure_ascii=False))
    else:
        for rec in records:
            if rec.get("error"):
                print(f"FAILED  {rec['input']}: {rec['error']}")
            else:
                size = rec.get("markdown_bytes", 0)
                print(f"OK      {rec['input']} -> {rec.get('markdown', rec.get('output_dir'))} ({size} bytes)")
                if args.print_md and rec.get("markdown"):
                    text = pathlib.Path(rec["markdown"]).read_text(errors="replace")
                    print(f"--- {rec['markdown']} (first {args.print_md} chars) ---")
                    print(text[:args.print_md])
        if failure:
            print(f"ERROR: {failure}", file=sys.stderr)

    return 1 if (failure or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
