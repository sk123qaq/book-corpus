#!/usr/bin/env python3
"""PaddleOCR-VL 云端试跑：切片、提交、轮询、下载，每页 md 与图片落盘。只读库，写批次目录。

接口约束（服务方说明）：单文件页数控制在 100 页内，超出部分不解析；当日额度用尽返回
HTTP 403 code 12001（2026-10-19 16:23 实测，不是 429）。
轮询遇网络抖动自动重试；中断后用 --job <jobId> 续跑同一任务，不重复消耗额度。
token 读 ~/.config/PaddleOCR/api_key，脚本不打印密钥。

用法（要 pypdf 与 requests，用 ~/teleocr/.venv/bin/python）：
  paddleocr_pilot.py --uid B0540 --pages 3-8 --out <dir>
  paddleocr_pilot.py --job 105481013747945472 --pages 3-8 --out <dir>
"""
import argparse
import json
import os
import sys
import time

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
KEY_PATH = os.path.expanduser("~/.config/PaddleOCR/api_key")
ROOT = "/home/shike/文档/books"


def token():
    with open(KEY_PATH, encoding="utf-8") as fh:
        return fh.read().strip()


def slice_pdf(src, pages, dest):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(src)
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    with open(dest + ".tmp", "wb") as fh:
        writer.write(fh)
    os.replace(dest + ".tmp", dest)
    return len(pages)


def parse_pages(spec):
    """`3-8`、`3,5,7-9` 都可以；解析后排序去重，页码从 1 起。"""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        try:
            if sep:
                lo, hi = sorted((int(a), int(b)))
                out.update(range(lo, hi + 1))
            else:
                out.add(int(a))
        except ValueError:
            sys.exit("--pages 格式不对：%r，写法如 3-8 或 3,5,7-9" % spec)
    pages = sorted(out)
    if not pages or pages[0] < 1:
        sys.exit("--pages 解析后没有有效页码（从 1 起）：%r" % spec)
    return pages


def fetch(jid, args, requests, pages, elapsed):
    headers = {"Authorization": "bearer %s" % token()}
    d = None
    t0 = time.time()
    last = None
    while time.time() - t0 < args.timeout:
        try:
            rr = requests.get("%s/%s" % (JOB_URL, jid), headers=headers, timeout=60)
        except requests.exceptions.RequestException as e:
            print("[%.0fs] 轮询异常，10 秒后重试（%s）" % (time.time() - t0, type(e).__name__), flush=True)
            time.sleep(10)
            continue
        if rr.status_code != 200:
            if rr.status_code == 403 and "12001" in rr.text:
                raise RuntimeError("日额度用尽：HTTP 403 code 12001")
            print("轮询 HTTP %s %s" % (rr.status_code, rr.text[:200]))
            time.sleep(10)
            continue
        d = rr.json()["data"]
        state = d.get("state")
        prog = d.get("extractProgress") or {}
        cur = (state, prog.get("extractedPages"), prog.get("totalPages"))
        if cur != last:
            print("[%.0fs] %s %s/%s" % (time.time() - t0, state, prog.get("extractedPages"), prog.get("totalPages")), flush=True)
            last = cur
        if state in ("done", "failed"):
            break
        time.sleep(5)
    if d is None or d.get("state") != "done":
        print("未完成，state=%s" % (d or {}).get("state"))
        sys.exit(1)
    if elapsed is None:
        elapsed = time.time() - t0
    print("完成，用时 %.1fs，%d 页，%.1f 秒/页" % (elapsed, len(pages), elapsed / len(pages)))

    text = requests.get(d["resultUrl"]["jsonUrl"], timeout=600).text.strip()
    pages_dir = os.path.join(args.out, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    ledger, total_chars, images = [], 0, 0
    for line in [l for l in text.split("\n") if l.strip()]:
        result = json.loads(line)["result"]
        for res in result.get("layoutParsingResults", []):
            idx = len(ledger) + 1
            md = res["markdown"]["text"]
            total_chars += len(md)
            md_path = os.path.join(pages_dir, "doc_%03d.md" % idx)
            with open(md_path + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(md)
            os.replace(md_path + ".tmp", md_path)
            for img_path, url in (res["markdown"].get("images") or {}).items():
                dst = os.path.join(pages_dir, img_path)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst + ".tmp", "wb") as fh:
                    fh.write(requests.get(url, timeout=120).content)
                os.replace(dst + ".tmp", dst)
                images += 1
            ledger.append({
                "page": pages[idx - 1] if idx <= len(pages) else None,
                "chars": len(md),
                "images": len(res["markdown"].get("images") or {}),
                "head": " ".join(md.split())[:160],
            })
    run_json = os.path.join(args.out, "paddle_run.json")
    with open(run_json + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({
            "uid": args.uid, "source": getattr(args, "source_label", args.pdf),
            "pages": pages, "model": args.model, "jobId": jid,
            "elapsed_s": round(elapsed, 1), "chars_total": total_chars,
            "images": images, "per_page": ledger,
        }, fh, ensure_ascii=False, indent=1)
    os.replace(run_json + ".tmp", run_json)
    print("落盘 %s，页数 %d，正文 %d 字，图片 %d 张" % (pages_dir, len(ledger), total_chars, images))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid")
    ap.add_argument("--pdf")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="PaddleOCR-VL-1.6")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--job", help="只轮询并下载这个 jobId，不重新提交")
    a = ap.parse_args()
    import requests

    if a.uid:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "目录/library.db"), uri=True)
        row = con.execute("SELECT file FROM source WHERE uid=? AND format='pdf'", (a.uid,)).fetchone()
        if not row:
            sys.exit("库里找不到 %s 的 PDF" % a.uid)
        src, base = os.path.join(ROOT, row[0]), a.uid
    else:
        src, base = a.pdf, os.path.splitext(os.path.basename(a.pdf or "x"))[0][:40]
    pages = parse_pages(a.pages)
    os.makedirs(a.out, exist_ok=True)
    a.source_label = os.path.relpath(src, ROOT) if a.uid else src

    if a.job:
        print("续跑 job", a.job)
        fetch(a.job, a, requests, pages, None)
        return

    chunk = os.path.join(a.out, "%s_p%04d-%04d.pdf" % (base, pages[0], pages[-1]))
    n = slice_pdf(src, pages, chunk)
    print("切片 %s，%d 页，%.2f MB" % (os.path.basename(chunk), n, os.path.getsize(chunk) / 1e6))
    headers = {"Authorization": "bearer %s" % token()}
    payload = {
        "model": a.model,
        "optionalPayload": json.dumps({
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }),
    }
    t0 = time.time()
    with open(chunk, "rb") as fh:
        r = requests.post(JOB_URL, headers=headers, data=payload, files={"file": fh}, timeout=180)
    print("提交 HTTP", r.status_code)
    if r.status_code != 200:
        print(r.text[:800])
        sys.exit(1)
    jid = r.json()["data"]["jobId"]
    print("jobId", jid, "| 提交耗时 %.1fs" % (time.time() - t0))
    fetch(jid, a, requests, pages, None)


if __name__ == "__main__":
    main()
