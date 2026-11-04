#!/usr/bin/env python3
"""探页定语种：给批次里还没有 md 的 PDF 各抽 1-3 页做 OCR，按假名/汉字/拉丁比例判语种。

为什么需要它：日文原版书（竖排、假名多）与中文译本在书名上分不出来。「日蝕」「運転士」
是日文原版，「少女的告密」「海峡之光」是中文译本，两者都是「日本作者 + 汉字书名」。
已落地的 10 本芥川奖书给出了实测参照：日文原版正文假名占假名+汉字 63-76%，中文译本 0.0%。
所以探一页正文就能定，成本 1 页额度，比按书名猜准，也比整本 OCR 完再发现便宜三个数量级。

产物 工作区/批次/<批次>/defer/lang.tsv（一个 uid 一行，只写观测值与判定；同一 uid 覆盖、其余保留，整表原子重写）：
  uid title pages lang sample_page chars kana han latin kana_share latin_share job at
lang ∈ ja / zh / en / empty（该页无字）/ unknown（探页失败，留在范围内）

用法：
  PY=工作区/.venv-pdf/bin/python
  $PY book-corpus run pdf_lang_probe --batch pdf-ocr-2026-10-19                # 探完整个待办集
  $PY book-corpus run pdf_lang_probe --batch pdf-ocr-2026-10-19 --uids B1051,B1064
  $PY book-corpus run pdf_lang_probe --batch pdf-ocr-2026-10-19 --list         # 只列会探谁，不花额度
"""

import argparse
import concurrent.futures as futures
import csv
import datetime
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L

HERE = os.path.dirname(os.path.abspath(__file__))
PILOT = os.path.join(HERE, "paddleocr_pilot.py")
VENV_PY = os.path.join(L.WORK, ".venv-pdf", "bin", "python")
DB = L.PATHS.library_db

KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
HAN = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
DEPTHS = (0.40, 0.60, 0.20)  # 先探正文中段，空页/图版就换一处
JA_KANA_SHARE = 0.15  # 日文正文实测 63-76%，中文译本是 0.0%
EN_LATIN_SHARE = 0.60
MIN_CHARS = 20
RETRY_BACKOFF = (5, 15, 45, 90)  # 队列满与偶发 job failed 都走这条；2026-10-20 实测
# 单页 job 会偶发 state=failed，同一页重提即成功
QUOTA_RE = re.compile(r"12001|每日页数上限")
LEDGER_COLS = [
    "uid",
    "title",
    "pages",
    "lang",
    "sample_page",
    "chars",
    "kana",
    "han",
    "latin",
    "kana_share",
    "latin_share",
    "job",
    "at",
]


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def batch_ctx(batch):
    path = os.path.join(L.WORK, "批次", batch)
    if not os.path.isdir(path):
        sys.exit("没有这个批次目录: %s" % path)
    return path


def worklist(path):
    p = os.path.join(path, "worklist.tsv")
    with open(p, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t"), strict=False)) for line in fh if line.strip()]


def has_md(uid):
    import sqlite3

    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        row = con.execute("SELECT path FROM book WHERE uid=?", (uid,)).fetchone()
    finally:
        con.close()
    return bool(row) and os.path.isdir(L.PATHS.md(row[0]))


def sample_pages(npages):
    out = []
    for d in DEPTHS:
        p = round(npages * d)
        p = max(1, min(npages, p))
        if p not in out:
            out.append(p)
    return out


def ratio(a, b):
    return (a / float(b)) if b else 0.0


def read_doc(out_dir):
    for d in ("pages", ""):
        p = os.path.join(out_dir, d, "doc_001.md")
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read()
    return ""


class QuotaExhaustedError(Exception):
    """日额度用尽：探页和 OCR 都不该继续，直接停。"""


def probe_page(uid, page, work, log):
    """探一页。队列满（10010）与偶发 state=failed 退避重提同一页；额度用尽立刻停整批。"""
    out = os.path.join(work, "%s_p%04d" % (uid, page))
    cmd = [
        VENV_PY,
        "-u",
        PILOT,
        "--uid",
        uid,
        "--pages",
        "%d-%d" % (page, page),
        "--out",
        out,
        "--timeout",
        "120",
    ]  # 一页 10 秒的事；平台回 500 时别让它卡满 30 分钟
    last = ""
    for i in range(len(RETRY_BACKOFF) + 1):
        p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True)
        text = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            job = ""
            m = re.search(r"jobId\s+(\S+)", text)
            if m:
                job = m.group(1)
            return read_doc(out), job
        last = " ".join(text.split())[-160:]
        if QUOTA_RE.search(text):
            raise QuotaExhaustedError(last)
        if i < len(RETRY_BACKOFF):
            wait = RETRY_BACKOFF[i]
            log("%s p%d 探页失败，%ds 后重提: %s" % (uid, page, wait, last))
            time.sleep(wait)
            continue
    log("%s p%d 探页失败: %s" % (uid, page, last))
    return None, ""


def classify(chars):
    kana, han, latin = len(KANA.findall(chars)), len(HAN.findall(chars)), len(LATIN.findall(chars))
    if kana + han + latin < MIN_CHARS:
        return "empty", kana, han, latin
    if ratio(kana, kana + han) >= JA_KANA_SHARE:
        return "ja", kana, han, latin
    if ratio(latin, kana + han + latin) >= EN_LATIN_SHARE:
        return "en", kana, han, latin
    return "zh", kana, han, latin


def probe_book(row, work, log):
    uid, npages = row["uid"], int(row["pages"] or 0)
    rec = {
        "uid": uid,
        "title": row["title"],
        "pages": npages,
        "lang": "unknown",
        "sample_page": "",
        "chars": 0,
        "kana": 0,
        "han": 0,
        "latin": 0,
        "kana_share": 0.0,
        "latin_share": 0.0,
        "job": "",
        "at": now(),
    }
    got = False
    for page in sample_pages(npages):
        text, job = probe_page(uid, page, work, log)
        if text is None:
            continue
        got = True
        lang, kana, han, latin = classify(text)
        rec.update(
            {
                "sample_page": page,
                "chars": len(text),
                "kana": kana,
                "han": han,
                "latin": latin,
                "kana_share": round(ratio(kana, kana + han), 4),
                "latin_share": round(ratio(latin, kana + han + latin), 4),
                "job": job,
            }
        )
        if lang != "empty":
            rec["lang"] = lang
            return rec
    rec["lang"] = "empty" if got else "unknown"
    return rec


def write_ledger(path, records):
    """只追加新观测：同一 uid 用最新一行覆盖旧行，其余原样保留。"""
    old = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                old[r["uid"]] = r
    for r in records:
        prev = old.get(r["uid"]) or {}
        # unknown/empty 不得覆盖已判定的终态语种，否则 --force 失败一次就把日文书放回范围
        if prev.get("lang") in ("ja", "zh", "en") and str(r.get("lang")) in ("unknown", "empty"):
            continue
        old[r["uid"]] = {c: str(r.get(c, "")) for c in LEDGER_COLS}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\t".join(LEDGER_COLS) + "\n")
        for uid in sorted(old):
            fh.write("\t".join(old[uid].get(c, "") for c in LEDGER_COLS) + "\n")
    os.replace(path + ".tmp", path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--uids", help="只探这些，逗号分隔")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--work", help="探页产物目录，默认 <批次>/tmp/langprobe")
    ap.add_argument("--list", action="store_true", help="只列待探书目，不调接口")
    ap.add_argument("--force", action="store_true", help="已探过的也重探")
    a = ap.parse_args()

    batch = batch_ctx(a.batch)
    ledger = os.path.join(batch, "defer", "lang.tsv")
    work = a.work or os.path.join(batch, "tmp", "langprobe")
    rows = worklist(batch)
    if a.uids:
        want = {u.strip() for u in a.uids.split(",") if u.strip()}
        rows = [r for r in rows if r["uid"] in want]
    rows = [r for r in rows if not has_md(r["uid"])]
    if not a.force and os.path.isfile(ledger):
        with open(ledger, encoding="utf-8") as fh:
            seen = {r["uid"] for r in csv.DictReader(fh, delimiter="\t") if r["lang"] not in ("", "unknown")}
        before = len(rows)
        rows = [r for r in rows if r["uid"] not in seen]
        if before != len(rows):
            print("账上已探过 %d 本，跳过" % (before - len(rows)))

    if a.list:
        print(
            "待探 %d 本，%d 页书，约花 %d 页额度上限"
            % (len(rows), sum(int(r["pages"] or 0) for r in rows), len(rows) * len(DEPTHS))
        )
        for r in rows:
            print("  %-6s 页 %4s  %s" % (r["uid"], r["pages"], r["title"][:50]))
        return 0
    if not rows:
        print("没有要探的书")
        return 0
    if not os.path.isfile(VENV_PY):
        sys.exit("缺 %s" % VENV_PY)
    print("待探 %d 本，%d 并发，产物 %s" % (len(rows), a.workers, os.path.relpath(ledger, L.ROOT)))

    lock = threading.Lock()
    done, records = [0], []

    def log(msg):
        with lock:
            print("    " + msg, flush=True)

    abort = []

    def one(r):
        if abort:
            return
        try:
            rec = probe_book(r, work, log)
        except QuotaExhaustedError as exc:
            with lock:
                abort.append(str(exc))
            return
        with lock:
            done[0] += 1
            records.append(rec)
            write_ledger(ledger, [rec])
            print(
                "[%d/%d] %-6s %-7s 页 %4s/%s 假名 %5.1f%% 拉丁 %4.1f%% 字 %5d  %.40s"
                % (
                    done[0],
                    len(rows),
                    rec["uid"],
                    rec["lang"],
                    rec["sample_page"],
                    rec["pages"],
                    rec["kana_share"] * 100,
                    rec["latin_share"] * 100,
                    rec["chars"],
                    rec["title"],
                ),
                flush=True,
            )

    with futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(one, rows))

    if abort:
        print("\n日额度用尽，探页停下：%s" % abort[0])
        print("已探到的账在 %s" % os.path.relpath(ledger, L.ROOT))
        return 3

    counts = {}
    for rec in records:
        counts[rec["lang"]] = counts.get(rec["lang"], 0) + 1
    pages_by_lang = {}
    for rec in records:
        pages_by_lang[rec["lang"]] = pages_by_lang.get(rec["lang"], 0) + int(rec["pages"] or 0)
    print("\n语种判定")
    for lang in sorted(counts):
        print("  %-8s %3d 本  %6d 页" % (lang, counts[lang], pages_by_lang[lang]))
    print("账: %s" % os.path.relpath(ledger, L.ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
