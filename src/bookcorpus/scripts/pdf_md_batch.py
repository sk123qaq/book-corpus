#!/usr/bin/env python3
"""批量把 primary 源是扫描 PDF 的书 OCR 成 md（方案 §7 的转换接口）。

每本书一条流水线：切片 → paddleocr_pilot.py 提交/续跑/下载 → 合并成连续页序 →
pdf_ocr_to_md.py 拼装（--structure single）→ verify_output.py 加 PDF 侧体检 →
原子 rename 进 库/<path>/md/。产物先落 工作区/批次/<批次>/out/<uid>/，门槛全过才进库，
没过留在暂存区等人看。

每本书的状态都从磁盘推出来，崩了能接着跑：
  work/<uid>/chunks.json              分片账：range/jobId/attempts/status，jobId 一拿到就落盘
  work/<uid>/chunks/c%04d/            pilot 的 --out，里面有 paddle_run.json 就算这一段完成
  work/<uid>/merged/pages + run.json  连续页序的逐页输入，交给拼装器
  out/<uid>/                          拼装好的暂存 crate
  logs/<uid>.json / logs/<uid>.log    结果记录与工具输出
分片目录名 c%04d 按 range 在 chunks.json 里的次序定（账是只追加的，重启后仍指同一个目录）。
已受理的例外放 工作区/批次/<批次>/overrides/<uid>.json，内容是 {"accept": "原因"}，过检后照样入库。

可疑页（OCR 一个字都没读出、纸面上却有字迹）先交 MinerU 补投一次：读出来的正文写回
merged/pages 再重拼装，门槛自然过；MinerU 也读不出的页记进 批次/skipped-pages.tsv 后按跳过
受理，不再挂人工复核。判据与实测见批次 说明.md 的「可疑页兜底」。

--route text 是纯文本层那 56 本的另一条流水线：没有切片、不调接口，pdf_text_to_md.py 抽文本
（--structure auto，PDF 自带可用目录的按目录切章，否则一个内容文件）→ verify_output.py →
pdf_audit_volume.py 做 PDF 侧字数比与逐页锚点覆盖率，门槛全过才入库。两条路线的暂存、落地、
日志、report 是同一套。

范围账本 worklist.tsv 记着一批要跑的书：triage_final.csv 里 route=OCR（或 route=本地抽文本）
的书（skip_list.tsv 里的不排），按页数升序排，页数相同按 uid。账建好之后 --uids 只往里补书，
不改已有的行。一次运行只跑点名的那几本，没点名就跑账上属于这条 route 的书。--report-json
在 stdout 最后一行给页面一份统计，范围是这次要跑的书（route 收窄、点名再收窄），不受
--limit 与 --only 影响。

用法:
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --workers 3
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --only B0540,B0534
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --no-land
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --assemble-only B0540 --no-land
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --report
  python3 pdf_md_batch.py --batch pdf-text-2026-10-19 --route text --workers 3
  python3 pdf_md_batch.py --batch pdf-ocr-2026-10-19 --report --report-json
  python3 pdf_md_batch.py --batch pdf-ocr-ops-2026-10-25 --uids B1234,B2345 \
      --triage 工作区/批次/pdf-ocr-ops-2026-10-25/triage_final.csv
"""
import argparse
import collections
import concurrent.futures as futures
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(L.WORK, ".venv-pdf", "bin", "python")
PILOT = os.path.join(HERE, "paddleocr_pilot.py")
ASSEMBLER = os.path.join(HERE, "pdf_ocr_to_md.py")
TEXT_ASSEMBLER = os.path.join(HERE, "pdf_text_to_md.py")
AUDIT = os.path.join(HERE, "pdf_audit_volume.py")
VERIFY = os.path.join(L.skill_dir("ebook-to-md"), "scripts", "verify_output.py")
CENSUS_DIR = os.path.join(L.WORK, "批次", "pdf-普查-2026-10-19")
TRIAGE = os.path.join(CENSUS_DIR, "triage_final.csv")
SKIP_LIST = os.path.join(CENSUS_DIR, "skip_list.tsv")
DB = L.PATHS.library_db

MAX_SLICE_MB = 15          # 官方上限是本地文件 50 MB；实测 29.9 MB 切片第一次上传就 180 秒超时，
                           # 11.8 MB 与 14.2 MB 能拿到干净回话，而 56.6 MB/118 MB 直接断，所以压到 15 MB
MIN_CHUNK_PAGES = 25
MAX_CHUNK_PAGES = 150      # 单任务实测 150 页能全解析（官方上限 1000 页）
MAX_CHUNK_ATTEMPTS = 2     # 同一段范围两次都失败就切半（小于 25 页的记 failed）
MAX_RESUME_ATTEMPTS = 3    # 任务已受理但没下完，续跑同一个 job 的次数上限
MAX_DEAD_JOB_RESUBMIT = 3  # 平台把任务判 failed（state=failed）：续跑同一 jobId 永远是 failed，
                           # 必须丢掉 jobId 重提一个新任务；2026-10-20 实测 B1623 末段 5 页就这样卡死
QUEUE_BACKOFF = (20, 45, 90, 180, 300)   # 10010 队列满的退避阶梯，不切分，等它腾出来
CANCEL_GRACE = 60          # 额度用尽后给在飞分片的宽限：已受理的任务可能马上就 done，能落就落；
                           # 宽限过后 terminate，免得 driver 陪它等到 pilot 的 1800 秒超时
MIN_INK, MAX_INK, MIN_BANDS = 0.004, 0.35, 8      # 同 pdf_scan_probe.py
DARK_MAX = int(0.62 * 255)                        # 灰度样本 <= 这个值算暗像素
SUSPECT_CHARS = 20
PROBE_DPI, REVIEW_DPI, MAX_REVIEW_PAGES = 70, 100, 12
JOBID_RE = re.compile(r"^jobId\s+(\S+)")
DOC_RE = re.compile(r"(\d+)")
IMG_REF_RE = re.compile(r"(?:imgs|images)/[^\"')\s>]+")
WORKLIST_COLS = ["uid", "title", "path", "src", "sha256", "format", "pages", "class",
                 "verdict", "subject1", "subject2", "author", "series"]
DEFER_LEDGER = os.path.join("defer", "lang.tsv")   # pdf_lang_probe.py 按内容探页定的语种账
ROUTE_TRIAGE = {"ocr": "OCR", "text": "本地抽文本"}
MIN_AUDIT_RATIO, MIN_AUDIT_ANCHORS = 0.95, 0.90   # 与 pdf_audit_volume.py 的默认门槛一致
# MinerU 兜底：Paddle 读出 0 字但有字迹的页交它补投。官方上限单文件 200 页 / 200 MB，
# 每账号每天 1000 页最高优先级、超出降速、5000 页封顶。
MINERU_CLI = os.path.join(L.skill_dir("mineru"), "scripts", "mineru.py")
MINERU_MAX_PAGES, MINERU_MAX_MB = 200, 200
MINERU_FREE_PAGES, MINERU_DAY_PAGES = 1000, 5000
MINERU_SKIP_TYPES = ("page_number", "page_footnote", "footer", "footer_image")
SKIP_LEDGER = "skipped-pages.tsv"
SKIP_COLS = ["at", "uid", "page", "paddle_chars", "mineru_chars", "action", "note"]
RATIO_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)\s+([\d.]+)%\s+\d+\s+md\b")
ANCHOR_RE = re.compile(r"锚点\s*:\s*([\d,]+)\s*/\s*([\d,]+)\s+窗命中\s+([\d.]+)%")
GREEN = ("landed", "replaced", "accepted", "staged-green")


class MergeError(Exception):
    """分片之间对不上：页数、页号、重复页。继续拼会静默出错，所以直接停。"""


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def rel(path):
    return os.path.relpath(path, L.ROOT)


def brel(ctx, path):
    return os.path.relpath(path, ctx["batch"])


def sha12(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def tool_version():
    return "assembler=sha256:%s;pilot=sha256:%s" % (sha12(ASSEMBLER), sha12(PILOT))


def text_tool_version():
    return "text=sha256:%s;audit=sha256:%s" % (sha12(TEXT_ASSEMBLER), sha12(AUDIT))


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(path + ".tmp", path)


# ------------------------------------------------------------------ 范围账本
def db_row(con, uid):
    r = con.execute(
        """SELECT b.uid AS uid, b.title AS title, b.path AS path, b.author AS author,
                  b.series AS series, s.file AS src, s.format AS format, s.sha256 AS sha256
           FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
           WHERE b.uid=?""", (uid,)).fetchone()
    return dict(r) if r else None


def skip_uids(path=None):
    path = path or SKIP_LIST
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        fh.readline()
        return {line.split("\t")[0] for line in fh if line.strip()}


def defer_uids(path, lang="ja"):
    """读 pdf_lang_probe.py 的语种账，返回暂缓的 uid -> 观测行。没有账就当没暂缓。"""
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("lang") == lang:
                out[row["uid"]] = row
    return out


def read_worklist(batch_path):
    path = os.path.join(batch_path, "worklist.tsv")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t")))
                for line in fh if line.strip()]


def triage_routes(triage):
    """uid -> 分诊表里的 route。表里没有的书不出现；表不在就返回空。"""
    path = triage or TRIAGE
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {t["uid"]: t.get("route") for t in csv.DictReader(fh)}


def worklist_scope(rows, route_of, want, wanted):
    """分诊表里没有的书 route 未知，照跑；有的只留这条线。"""
    out = [r for r in rows if route_of.get(r["uid"], want) == want]
    if wanted:
        out = [r for r in out if r["uid"] in wanted]
    return out


def build_worklist(con, batch_path, route, dry=False, uids=None, triage=None, skip=None):
    """点名（--uids）往账里补书，已有的行不改，没点名的书不动。

    点名里有 skip_list 的书、或分的不是这条 route 的书，直接退出，不写账。
    dry=True 时只算不写。
    """
    want = ROUTE_TRIAGE[route]
    path = os.path.join(batch_path, "worklist.tsv")
    triage_rows = list(csv.DictReader(open(triage or TRIAGE, encoding="utf-8")))
    route_of = triage_routes(triage)
    skipped = skip_uids() if skip is None else skip_uids(skip)
    wanted = {u.strip() for u in (uids or []) if u.strip()}
    rows = read_worklist(batch_path) or []
    known = {r["uid"] for r in rows}
    added, dropped = [], 0
    for t in triage_rows:
        uid = t["uid"]
        if uid in known or t.get("route") != want:
            continue
        if wanted:
            if uid not in wanted:
                continue
        elif rows:
            continue        # 账已在且没点名：重跑账上的书，不往里添
        if uid in skipped:
            dropped += 1
            continue
        d = db_row(con, uid)
        if not d or d["format"] != "pdf":
            sys.exit("triage 里 route=%s 的 %s 在库里拿不到 pdf 主源: %r" % (want, uid, d))
        added.append({"uid": uid, "title": d["title"], "path": d["path"], "src": d["src"],
                      "sha256": d["sha256"], "format": d["format"], "pages": t["pages"],
                      "class": t["class"], "verdict": t.get("verdict", ""),
                      "subject1": t["subject1"], "subject2": t["subject2"],
                      "author": d["author"] or "", "series": d["series"] or ""})
        known.add(uid)
    rows += added
    rows.sort(key=lambda r: (int(r["pages"] or 0), r["uid"]))
    out = worklist_scope(rows, route_of, want, wanted)
    if wanted:
        in_skip = sorted(wanted & skipped)
        if in_skip:
            sys.exit("--uids 里这些 uid 列在 skip_list.tsv（不转），不能排进这批：%s"
                     % ", ".join(in_skip))
        missing = sorted(wanted - {r["uid"] for r in out})
        if missing:
            sys.exit("--uids 里这些 uid 不在 route=%s 的范围里（分诊表里没有，或分的不是这条线）：%s"
                     % (route, ", ".join(missing)))
    if not dry and (added or not known):
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write("\t".join(WORKLIST_COLS) + "\n")
            for r in rows:
                fh.write("\t".join(str(r[c]) for c in WORKLIST_COLS) + "\n")
        os.replace(path + ".tmp", path)
    if dropped:
        print("注意：triage_final 里 %d 本 route=%s 但也列在 skip_list.tsv，未排进队列"
              % (dropped, want))
    return out


# ------------------------------------------------------------------ 分片账
def plan_ranges(npages, src):
    per_page = os.path.getsize(src) / float(max(1, npages))
    per_chunk = max(MIN_CHUNK_PAGES, min(MAX_CHUNK_PAGES,
                                         int(MAX_SLICE_MB * 1e6 / per_page)))
    return [(a, min(a + per_chunk - 1, npages)) for a in range(1, npages + 1, per_chunk)], per_chunk


def ledger_path(ctx, uid):
    return os.path.join(ctx["work"], uid, "chunks.json")


def load_ledger(ctx, uid):
    p = ledger_path(ctx, uid)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("分片账坏了（%s）：%s；先修账再跑，否则已付的 jobId 会失联"
                           % (os.path.relpath(p, ctx["batch"]), exc))


def save_ledger(ctx, uid, ledger):
    write_json(ledger_path(ctx, uid), ledger)


def entry_for(ledger, a, b):
    for e in ledger:
        if list(e.get("range") or []) == [a, b]:
            return e
    e = {"range": [a, b], "jobId": None, "attempts": 0, "status": "pending"}
    ledger.append(e)
    return e


def chunk_dir(ctx, uid, ledger, a, b):
    """c%04d 按 range 在账里的位次定，账只追加，所以这个映射重启后不变。"""
    for i, e in enumerate(ledger):
        if list(e.get("range") or []) == [a, b]:
            return os.path.join(ctx["work"], uid, "chunks", "c%04d" % (i + 1))
    raise MergeError("分片账里没有 %d-%d" % (a, b))


def chunk_done(cdir):
    return os.path.isfile(os.path.join(cdir, "paddle_run.json"))


def done_ranges(ctx, uid, ledger):
    return [(e["range"][0], e["range"][1]) for e in ledger if e.get("status") == "done"]


def run_pilot(ctx, uid, a, b, cdir, job, on_job, log):
    """跑 pilot 并逐行转出 stdout。加 -u 让 jobId 那行立刻写出来，不在管道里攒着。

    子进程登记在 ctx["pilots"]，额度用尽时 cancel_pilots() 能把它收掉：否则它会一直轮询
    到 pilot 自己的 1800 秒超时，driver 就得干等（2026-10-22 实测干等 14 分钟）。
    """
    os.makedirs(cdir, exist_ok=True)
    cmd = [VENV_PY, "-u", PILOT, "--uid", uid, "--pages", "%d-%d" % (a, b), "--out", cdir]
    if job:
        cmd += ["--job", str(job)]
    log.write("$ %s\n" % " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=L.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, env=dict(os.environ, PYTHONUNBUFFERED="1"))
    with ctx["pilot_lock"]:
        ctx["pilots"][uid] = p
    tail = []
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            log.write("  " + line + "\n")
            print("    %s | %s" % (uid, line), flush=True)
            tail.append(line)
            m = JOBID_RE.match(line)
            if m:
                on_job(m.group(1))
    finally:
        with ctx["pilot_lock"]:
            if ctx["pilots"].get(uid) is p:
                ctx["pilots"].pop(uid, None)
    rc = p.wait()
    log.write("  rc=%d\n" % rc)
    return rc, tail


def cancel_pilots(ctx, grace=None):
    """额度用尽时收掉在飞的 pilot。先给 grace 秒（已受理的任务可能马上就 done），
    再 terminate，不退就 kill。幂等：只起一个取消器。

    不丢工作：jobId 在收到那一刻就落账了，杀掉的只是轮询进程，次日 --job 续跑同一个任务。
    """
    grace = CANCEL_GRACE if grace is None else grace
    with ctx["pilot_lock"]:
        if ctx.get("cancelling"):
            return None
        if not ctx["pilots"]:
            return None            # 没有在飞的，不用等宽限期
        ctx["cancelling"] = True

    def run():
        if grace:
            time.sleep(grace)
        with ctx["pilot_lock"]:
            live = list(ctx["pilots"].values())
        for p in live:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()

    t = threading.Thread(target=run, name="cancel-pilots", daemon=True)
    ctx["canceller"] = t
    t.start()
    return t


def dead_job(tail):
    """平台把任务本身判了 failed：这个 jobId 已经死了，续跑它永远是 failed。"""
    return any(re.search(r"state=failed", x or "") for x in tail)


def classify_failure(tail):
    """接口回话分五类。**日额度用尽要先认出来**（HTTP 403 code 12001「已达每日页数上限」），
    它是账号级状态，直接中止整批；队列满（官方错误码 10010，5xx 是服务端）退避重试同一段；
    鉴权与参数类（其余 401/403、token）再试与切分都没用，直接停并报出来；
    上传/网络断切小再传；其余（超页、文件过大 10003）切分才有意义。

    2026-10-19 实测更正：额度用尽返回的是 HTTP 403 code 12001，不是 429。那天没认出这个码，
    被后一条当鉴权错，148 本记成 api-fatal 空转到批末（见 决议记录 与 pdf-ocr-2026-10-19/说明.md）。
    """
    text = "\n".join(tail)
    if re.search(r"12001|每日页数上限", text):
        return "quota"
    if re.search(r"10010|队列已满|稍后重试|HTTP\s*429|HTTP\s*5\d\d", text):
        return "queue-full"
    if re.search(r"HTTP\s*40[13]|Invalid token|token 不对|Token Invalid", text):
        return "fatal"
    if re.search(r"Traceback", text) and re.search(
            r"ConnectionError|Timeout|RemoteDisconnected|Broken pipe|Max retries", text):
        return "transport"
    return "other"


def work_chunk(ctx, uid, ledger, a, b, log, result):
    """把一段页范围的产物落进它的分片目录。返回 True 表示这一段目录已完整。

    失败分两类：接口说队列满（10010/429/5xx）就退避重试同一段，切小不解决排队；
    上传/网络断才切半，下次拿更小的文件再传。
    """
    e = entry_for(ledger, a, b)
    save_ledger(ctx, uid, ledger)
    cdir = chunk_dir(ctx, uid, ledger, a, b)
    if chunk_done(cdir):
        e["status"] = "done"
        save_ledger(ctx, uid, ledger)
        return True

    def on_job(jid):
        # 一拿到 jobId 就落账：之后崩了也能用 --job 续跑，不重花额度
        e["jobId"], e["status"] = jid, "running"
        save_ledger(ctx, uid, ledger)

    n = b - a + 1
    while True:
        e["attempts"] = e.get("attempts", 0) + 1
        e["status"] = "running"
        save_ledger(ctx, uid, ledger)
        rc, tail = run_pilot(ctx, uid, a, b, cdir, e.get("jobId"), on_job, log)
        if rc == 0 and chunk_done(cdir):
            e["status"] = "done"
            save_ledger(ctx, uid, ledger)
            return True
        if ctx["abort"]:
            # 额度用尽时在飞的 pilot 被 cancel_pilots() 收掉。这一段不算失败：jobId 留着，
            # 次日 --job 续跑；attempts 退回，免得连着几天中断把续跑名额耗尽后重新提交白花额度。
            e["status"] = "interrupted"
            e["attempts"] = max(0, e.get("attempts", 1) - 1)
            save_ledger(ctx, uid, ledger)
            result.update(status="interrupted",
                          note="额度用尽中断：分片 %d-%d %s，账已留，下次续跑不加失败次数"
                               % (a, b, ("已受理 jobId=%s" % e["jobId"]) if e.get("jobId")
                                  else "未受理，下次重提"))
            log.write("  %s：%d-%d 因 %s 中断（jobId=%s，attempts 退回 %d）\n"
                      % (uid, a, b, ctx["abort"], e.get("jobId"), e["attempts"]))
            return False
        kind = classify_failure(tail)
        if kind == "fatal":
            e["status"] = "api-fatal"
            save_ledger(ctx, uid, ledger)
            result.update(status="api-fatal",
                          note="接口鉴权/参数类错误，重试与切分都没用：%s"
                               % " | ".join(x for x in tail[-3:] if x.strip())[:180])
            log.write("  %s：接口回了不可重试的错误，停下来\n" % uid)
            return False
        if kind == "quota":
            e["status"] = "quota"
            save_ledger(ctx, uid, ledger)
            result.update(status="deferred",
                          note="接口报「已达每日页数上限」（HTTP 403 code 12001），额度已尽，"
                               "本次未消耗；次日复跑即续")
            ctx["abort"] = "日额度用尽"
            log.write("  %s：接口报每日页数上限，本批停在这里，剩下的书没提交\n" % uid)
            cancel_pilots(ctx)
            return False
        if kind == "queue-full" and not e.get("jobId"):
            rounds = e.get("queue_rounds", 0)
            if rounds < min(ctx["queue_rounds"], len(QUEUE_BACKOFF)):
                wait = QUEUE_BACKOFF[rounds]
                e["queue_rounds"], e["status"] = rounds + 1, "queued"
                save_ledger(ctx, uid, ledger)
                log.write("  %s：%d-%d 提交队列已满，等 %d 秒再试（第 %d 次）\n"
                          % (uid, a, b, wait, rounds + 1))
                time.sleep(wait)
                continue
            e["status"] = "queue-full"
            save_ledger(ctx, uid, ledger)
            result.update(status="deferred",
                          note="接口提交队列已满，%d-%d 退避 %d 次仍未受理；额度未消耗，下次跑接着试"
                               % (a, b, rounds))
            # 队列满是账号/平台级的状态，不是这一本的事：再排下一本只是接着等
            ctx["abort"] = "队列满"
            log.write("  %s：队列满退避用完，本批停在这里，剩下的书没提交\n" % uid)
            return False
        if dead_job(tail):
            resubmits = e.get("dead_jobs", 0) + 1
            e["dead_jobs"], e["jobId"] = resubmits, None
            save_ledger(ctx, uid, ledger)
            log.write("  %s：%d-%d 平台把任务判 failed，jobId 作废重新提交（第 %d 次）\n"
                      % (uid, a, b, resubmits))
            if resubmits <= MAX_DEAD_JOB_RESUBMIT:
                continue
        if e.get("jobId") and e["attempts"] < MAX_RESUME_ATTEMPTS:
            log.write("  %s：%d-%d 任务已受理（jobId=%s）但没下完，续跑同一任务\n"
                      % (uid, a, b, e["jobId"]))
            continue
        if kind == "transport" and e["attempts"] < MAX_CHUNK_ATTEMPTS:
            log.write("  %s：%d-%d 第 %d 次上传/网络没成（rc=%d），重试同一段\n"
                      % (uid, a, b, e["attempts"], rc))
            continue
        log.write("  %s：%d-%d 第 %d 次没成（rc=%d，%s，jobId=%s）\n"
                  % (uid, a, b, e["attempts"], rc, kind, e.get("jobId")))
        break
    if n >= MIN_CHUNK_PAGES:
        e["status"] = "split"
        save_ledger(ctx, uid, ledger)
        mid = (a + b) // 2
        log.write("  %s：%d-%d 切分重试 %d-%d 与 %d-%d\n" % (uid, a, b, a, mid, mid + 1, b))
        left = work_chunk(ctx, uid, ledger, a, mid, log, result)
        right = work_chunk(ctx, uid, ledger, mid + 1, b, log, result)
        return left and right
    e["status"] = "failed"
    save_ledger(ctx, uid, ledger)
    result.update(status="chunk-failed", note="分片 %d-%d 只剩 %d 页，多次都没成" % (a, b, n))
    return False


# ------------------------------------------------------------------ 合并
def _bands(profile):
    n, in_band = 0, False
    for r in profile:
        if r >= 0.03 and not in_band:
            n += 1
            in_band = True
        elif r < 0.03:
            in_band = False
    return n


def gray_stats(pix):
    """(ink, 行带数, 列带数)。口径与 pdf_scan_probe.py 相同，纯 Python 遍历灰度样本。"""
    w, h, stride, n = pix.width, pix.height, pix.stride, pix.n
    if n != 1:
        raise RuntimeError("不是灰度位图: n=%d" % n)
    s = pix.samples
    rows, cols, dark = [0] * h, [0] * w, 0
    for y in range(h):
        base = y * stride
        rd = 0
        for x in range(w):
            if s[base + x] <= DARK_MAX:
                rd += 1
                cols[x] += 1
        rows[y] = rd
        dark += rd
    return (dark / float(w * h), _bands([c / float(w) for c in rows]),
            _bands([c / float(h) for c in cols]))


def page_files(pages_dir):
    names = [n for n in os.listdir(pages_dir) if n.endswith(".md")]
    names.sort(key=lambda n: (int(DOC_RE.search(n).group(1)) if DOC_RE.search(n) else 0, n))
    return names


def check_chunk(ctx, cdir, a, b):
    """一段分片的页码与页数必须和账里的 range 一致，不然拼出来是错位的书。"""
    run = json.load(open(os.path.join(cdir, "paddle_run.json"), encoding="utf-8"))
    pp = run.get("per_page") or []
    if len(pp) != b - a + 1:
        raise MergeError("%s: per_page %d 条，range %d-%d 要 %d 条"
                         % (brel(ctx, cdir), len(pp), a, b, b - a + 1))
    pages = [p.get("page") for p in pp]
    if pages != list(range(a, b + 1)):
        raise MergeError("%s: per_page 页码 %s…%s 与 range %d-%d 不符"
                         % (brel(ctx, cdir), pages[:2], pages[-2:], a, b))
    files = page_files(os.path.join(cdir, "pages"))
    if len(files) != len(pp):
        raise MergeError("%s: %d 个页文件，per_page %d 条" % (brel(ctx, cdir), len(files), len(pp)))
    return pp, files


def rewrite_page_images(text, pages_dir, dst_imgs, page, missing):
    """页内图片引用 imgs/x.jpg 改成 p%04d_x.jpg 再搬过去，两页不会撞同一个文件名。"""
    used = set()

    def repl(m):
        src = m.group(0)
        new = "imgs/p%04d_%s" % (page, os.path.basename(src))
        if not os.path.isfile(os.path.join(pages_dir, src)):
            missing.append(src)
            return src
        os.makedirs(dst_imgs, exist_ok=True)
        shutil.copyfile(os.path.join(pages_dir, src), os.path.join(dst_imgs, os.path.basename(new)))
        used.add(new)
        return new

    return IMG_REF_RE.sub(repl, text), len(used)


def merge(ctx, uid, row, ledger, log, result):
    """把各分片的逐页产物并成连续页序的 merged/。返回 (merged 目录, run.json 内容)。"""
    merged = os.path.join(ctx["work"], uid, "merged")
    run_path = os.path.join(merged, "run.json")
    if os.path.isfile(run_path) and not ctx["force"]:
        doc = json.load(open(run_path, encoding="utf-8"))
        log.write("复用已有合并产物：%d 页（第 %s 到 %s 页），覆盖 1-%d：%s；逐页字数见 %s\n"
                  % (doc["pages"], doc["first_page"], doc["last_page"], doc["book_pages"],
                     doc["coverage_ok"], brel(ctx, run_path)))
        return merged, doc

    done = [e for e in ledger if e.get("status") == "done"]
    if not done:
        raise MergeError("没有已完成的分片")
    fresh = merged + ".new"
    if os.path.isdir(fresh):
        shutil.rmtree(fresh)
    pages_dir = os.path.join(fresh, "pages")
    imgs_dir = os.path.join(pages_dir, "imgs")
    os.makedirs(pages_dir)
    per_page, seen, missing, seq = [], set(), [], 0
    for e in sorted(done, key=lambda x: x["range"][0]):
        a, b = e["range"]
        cdir = chunk_dir(ctx, uid, ledger, a, b)
        pp, files = check_chunk(ctx, cdir, a, b)
        cpages = os.path.join(cdir, "pages")
        for page, fname in zip((p["page"] for p in pp), files):
            if page in seen:
                raise MergeError("第 %d 页出现两次" % page)
            seen.add(page)
            seq += 1
            text = open(os.path.join(cpages, fname), encoding="utf-8").read()
            text, nimg = rewrite_page_images(text, cpages, imgs_dir, page, missing)
            with open(os.path.join(pages_dir, "doc_%04d.md" % seq), "w", encoding="utf-8") as fh:
                fh.write(text)
            per_page.append({"page": page, "seq": seq, "chars": len(text), "images": nimg,
                             "head": " ".join(text.split())[:160]})
    got = sorted(seen)
    if got != list(range(got[0], got[-1] + 1)):
        raise MergeError("页号不连续：%d…%d 之间缺页" % (got[0], got[-1]))
    npages = int(row["pages"])
    doc = {"uid": uid, "source": row["src"], "pages": len(per_page), "book_pages": npages,
           "first_page": got[0], "last_page": got[-1], "coverage_ok": got == list(range(1, npages + 1)),
           "chunks": [{"range": e["range"], "jobId": e.get("jobId")} for e in done],
           "missing_images": missing, "generated_at": now(), "per_page": per_page}
    if os.path.isdir(merged):
        shutil.rmtree(merged)
    os.rename(fresh, merged)
    write_json(run_path, doc)
    log.write("合并 %d 页（第 %d 到 %d 页），覆盖 1-%d：%s；缺图 %d 张；逐页字数见 %s\n"
              % (len(per_page), got[0], got[-1], npages, doc["coverage_ok"], len(missing),
                 brel(ctx, run_path)))
    return merged, doc


# ------------------------------------------------------------------ 拼装与体检
def assemble(ctx, row, merged, stage, log, result):
    pages_dir = os.path.join(merged, "pages")
    run_json = os.path.join(merged, "run.json")
    method = ctx.get("method") or "paddle-ocr"
    cmd = [VENV_PY, ASSEMBLER, "--pages-dir", pages_dir, "--run-json", run_json,
           "--out", stage, "--uid", row["uid"], "--title", row["title"],
           "--author", row.get("author") or "", "--collection", row.get("series") or "",
           "--source", row["src"], "--source-sha256", row["sha256"],
           "--structure", "single", "--method", method,
           "--tool-version", ctx["tool_version"]]
    log.write("$ %s\n" % " ".join(cmd))
    p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True)
    log.write(p.stdout + p.stderr)
    result["commands"].append("pdf_ocr_to_md.py --pages-dir work/%s/merged/pages --out out/%s "
                              "--structure single --method %s" % (row["uid"], row["uid"], method))
    return p.returncode


def parse_verify(out):
    m = re.search(r"markdown\s*:\s*(\d+) chapters", out)
    c = re.search(r"characters\s*:\s*([\d,]+)", out)
    i = re.search(r"images used\s*:\s*(\d+) \(on disk: (\d+)\)", out)
    p = re.search(r"problems\s*:\s*(\d+)", out)
    return {"chapters": int(m.group(1)) if m else None,
            "chars": int(c.group(1).replace(",", "")) if c else None,
            "images": int(i.group(1)) if i else None,
            "problems": int(p.group(1)) if p else None}


def verify(ctx, stage, log):
    p = subprocess.run([sys.executable, VERIFY, stage], cwd=L.ROOT, capture_output=True, text=True)
    log.write("$ verify_output.py %s\n%s%s\n" % (brel(ctx, stage), p.stdout, p.stderr))
    return p.returncode, parse_verify(p.stdout + p.stderr)


def parse_audit(out):
    """PDF 侧审计的两行：md/源 字数比，与锚点窗命中数。形如
    1,234,567 / 1,240,000  99.56%  14 md 与 锚点 : 40/42 窗命中 95.24%。
    """
    m, k = RATIO_RE.search(out), ANCHOR_RE.search(out)
    return {"md_chars": int(m.group(1).replace(",", "")) if m else None,
            "pdf_chars": int(m.group(2).replace(",", "")) if m else None,
            "ratio": float(m.group(3)) / 100.0 if m else None,
            "anchor_hits": int(k.group(1).replace(",", "")) if k else None,
            "anchor_total": int(k.group(2).replace(",", "")) if k else None,
            "anchors": float(k.group(3)) / 100.0 if k else None}


def audit_pdf(ctx, src, stage, log):
    p = subprocess.run([VENV_PY, AUDIT, src, "-o", stage], cwd=L.ROOT,
                       capture_output=True, text=True)
    log.write("$ pdf_audit_volume.py %s -o %s\n%s%s\n"
              % (rel(src), brel(ctx, stage), p.stdout, p.stderr))
    return p.returncode, parse_audit(p.stdout + p.stderr)


def assemble_text(ctx, row, src, stage, log, result):
    cmd = [VENV_PY, TEXT_ASSEMBLER, "--pdf", src, "--out", stage, "--uid", row["uid"],
           "--title", row["title"], "--author", row.get("author") or "",
           "--collection", row.get("series") or "", "--date", row.get("date") or "",
           "--source-sha256", row["sha256"], "--method", "pdf-text",
           "--structure", "auto", "--tool-version", ctx["tool_version"]]
    log.write("$ %s\n" % " ".join(cmd))
    p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True)
    log.write(p.stdout + p.stderr)
    result["commands"].append("pdf_text_to_md.py --pdf %s --out out/%s --structure auto "
                              "--method pdf-text" % (row["src"], row["uid"]))
    return p.returncode


def rebuild_stage(ctx, row, merged, stage, log, result):
    """用 merged/pages 重拼一次暂存 crate（MinerU 补页后要走这一遍）。"""
    if os.path.isdir(stage):
        moved = os.path.join(ctx["tmp"], "%s.prev-%s" % (row["uid"], ctx["stamp"]))
        if os.path.exists(moved):
            moved = "%s-%d" % (moved, int(time.time()))
        os.rename(stage, moved)
        result["stage_previous"] = brel(ctx, moved)
    return assemble(ctx, row, merged, stage, log, result)


def pdf_checks(ctx, row, per_page, log, result):
    """逐页字数与纸面复查。可疑页（拦落地的）= OCR 一个字符都没读出、而纸面上有字迹结构，
    这正是 MinerU 静默丢页那一种。字数少但读出了字的（篇章扉页、图版页）只记数不拦。
    """
    import pymupdf as fitz
    blank, suspect, short = [], [], []
    src = os.path.join(L.ROOT, row["src"])
    doc = fitz.open(src)
    try:
        for p in per_page:
            chars = p.get("chars") or 0
            if chars >= SUSPECT_CHARS:
                continue
            page = doc[p["page"] - 1]
            ink, br, bc = gray_stats(page.get_pixmap(
                matrix=fitz.Matrix(PROBE_DPI / 72.0, PROBE_DPI / 72.0),
                colorspace=fitz.csGRAY))
            rec = {"page": p["page"], "chars": p["chars"], "ink": round(ink, 4),
                   "bands_row": br, "bands_col": bc}
            if not chars:
                blank.append(rec)
                if MIN_INK <= ink <= MAX_INK and max(br, bc) >= MIN_BANDS:
                    suspect.append(rec)
            else:
                short.append(rec)
        review = os.path.join(ctx["review"], row["uid"])
        for s in suspect[:MAX_REVIEW_PAGES]:
            page = doc[s["page"] - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(REVIEW_DPI / 72.0, REVIEW_DPI / 72.0))
            os.makedirs(review, exist_ok=True)
            pix.save(os.path.join(review, "p%04d.png" % s["page"]))
    finally:
        doc.close()
    log.write("空页 %d 个；读出 0 字但有字迹的可疑页 %d 个%s；读出 1-%d 字的短页 %d 个%s\n"
              % (len(blank), len(suspect),
                 ("：" + ", ".join(str(s["page"]) for s in suspect[:MAX_REVIEW_PAGES])) if suspect else "",
                 SUSPECT_CHARS - 1, len(short),
                 ("：" + ", ".join(str(s["page"]) for s in short[:MAX_REVIEW_PAGES])) if short else ""))
    result["blank_pages"] = len(blank)
    result["suspect_miss_pages"] = len(suspect)
    result["suspect_pages"] = [s["page"] for s in suspect]
    result["short_pages"] = len(short)
    result["short_page_list"] = [s["page"] for s in short]
    if suspect:
        result["review"] = brel(ctx, os.path.join(ctx["review"], row["uid"]))
    return blank, suspect, short


# ------------------------------------------------------------------ MinerU 兜底
# Paddle 的版面检没认出正文时会把整页返回空（实测 B1503 三首诗页、B1603 四页目录、B1478
# 两页分卷扉页都是这样，原始作业 JSON 里只有一个 label=number 的块）。MinerU 对同一页能读出
# 正文，所以兜底路径是：切片 → 提交 → 读回来的正文写回 merged/pages → 重拼装。仍读不出的页
# 记进 skipped-pages.tsv 后按跳过受理，不再挂人工复核。
def mineru_pages_today(ctx):
    path = os.path.join(ctx["batch"], SKIP_LEDGER)
    if not os.path.isfile(path):
        return 0
    today = now()[:10]
    with open(path, encoding="utf-8") as fh:
        return sum(1 for r in csv.DictReader(fh, delimiter="\t")
                   if (r.get("at") or "").startswith(today))


def skip_rows(ctx, rows):
    path = os.path.join(ctx["batch"], SKIP_LEDGER)
    new = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("\t".join(SKIP_COLS) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(c, "")) for c in SKIP_COLS) + "\n")


def mineru_page_text(blocks, page_idx):
    """一页的正文：按 bbox 从上到下拼文字块。页码与页脚不取（它们本来就不进 markdown）。"""
    def key(b):
        box = b.get("bbox") or [0, 0, 0, 0]
        return (box[1], box[0])
    picked = [b for b in blocks
              if b.get("page_idx") == page_idx
              and (b.get("text") or "").strip()
              and b.get("type") not in MINERU_SKIP_TYPES]
    return "\n\n".join((b.get("text") or "").strip() for b in sorted(picked, key=key))


def mineru_rescue(ctx, row, merged, doc, suspects, log, result):
    """把可疑页交 MinerU 补投，读到的正文写回 merged/pages 并更新逐页账。

    返回 {页: MinerU 字数}，0 表示它也没读出来。只补单页，不动分片账：可疑页不会到
    200 页/200 MB 这两个单文件上限。
    """
    pages = [s["page"] for s in suspects]
    if not pages:
        return {}
    if not os.path.isfile(MINERU_CLI):
        result["mineru_skip_reason"] = "找不到 %s" % MINERU_CLI
        log.write("MinerU 兜底跳过：找不到 %s\n" % MINERU_CLI)
        return None
    used = mineru_pages_today(ctx)
    if used >= MINERU_DAY_PAGES:
        result["mineru_skip_reason"] = "今日已投 %d 页，到 %d 页封顶" % (used, MINERU_DAY_PAGES)
        log.write("MinerU 兜底跳过：今日已投 %d 页，到 %d 页封顶\n" % (used, MINERU_DAY_PAGES))
        return None
    batch = pages[:MINERU_MAX_PAGES]
    if used + len(batch) > MINERU_FREE_PAGES:
        log.write("MinerU 今日已投 %d 页，本次 %d 页会超出 %d 页最高优先级额度（降速，不影响正确性）\n"
                  % (used, len(batch), MINERU_FREE_PAGES))

    import pymupdf as fitz
    src = os.path.join(L.ROOT, row["src"])
    outd = os.path.join(ctx["tmp"], "mineru", row["uid"])
    os.makedirs(outd, exist_ok=True)
    slice_pdf = os.path.join(outd, "%s_suspect.pdf" % row["uid"])
    doc_in = fitz.open(src)
    try:
        out = fitz.open()
        for p in batch:
            out.insert_pdf(doc_in, from_page=p - 1, to_page=p - 1)
        out.save(slice_pdf, garbage=3, deflate=True)
        out.close()
    finally:
        doc_in.close()
    size_mb = os.path.getsize(slice_pdf) / 1e6
    if size_mb > MINERU_MAX_MB:
        result["mineru_skip_reason"] = "切片 %.1f MB 超过单文件 %d MB 上限" % (size_mb, MINERU_MAX_MB)
        log.write("MinerU 兜底跳过：切片 %.1f MB 超过单文件 %d MB 上限\n" % (size_mb, MINERU_MAX_MB))
        return None

    defer = defer_uids(os.path.join(ctx["batch"], DEFER_LEDGER))
    lang = "japan" if row["uid"] in defer else "ch"
    cmd = [VENV_PY, MINERU_CLI, slice_pdf, "-o", outd, "--mode", "precision", "--model", "vlm",
           "--ocr", "--lang", lang, "--json", "--timeout", "900"]
    log.write("$ %s\n" % " ".join(cmd))
    p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True, timeout=1800)
    log.write(p.stdout[-4000:] + p.stderr[-4000:])
    result["commands"].append("mineru.py %s --mode precision --model vlm --ocr --lang %s"
                              % (rel(slice_pdf), lang))

    hits = []
    for root, _dirs, names in os.walk(outd):
        hits += [os.path.join(root, n) for n in names
                 if n.endswith("_content_list.json") and not n.endswith("_v2_content_list.json")]
    if p.returncode != 0 or not hits:
        tail = " ".join((p.stdout or "").strip().splitlines()[-3:])[:300] or "无输出"
        log.write("MinerU 兜底失败：rc=%d，%s\n" % (p.returncode, tail))
        skip_rows(ctx, [{"at": now(), "uid": row["uid"], "page": page,
                         "paddle_chars": 0, "mineru_chars": -1, "action": "mineru-failed",
                         "note": tail} for page in batch])
        return {page: -1 for page in batch}

    blocks = json.load(open(sorted(hits, key=os.path.getmtime)[-1], encoding="utf-8"))
    run_path = os.path.join(merged, "run.json")
    run = json.load(open(run_path, encoding="utf-8"))
    seq = {e["page"]: e["seq"] for e in run["per_page"]}
    got, rows = {}, []
    for idx, page in enumerate(batch):
        text = mineru_page_text(blocks, idx)
        got[page] = len(text)
        entry = next((e for e in run["per_page"] if e["page"] == page), None)
        if text.strip() and page in seq:
            page_path = os.path.join(merged, "pages", "doc_%04d.md" % seq[page])
            with open(page_path, "w", encoding="utf-8") as fh:
                fh.write(text.rstrip() + "\n")
        if entry is not None and text.strip():
            entry["chars"] = len(text)
            entry["head"] = " ".join(text.split())[:160]
            entry["rescued"] = "mineru"
            for e in doc["per_page"]:
                if e.get("page") == page:
                    e["chars"], e["head"] = entry["chars"], entry["head"]
        rows.append({"at": now(), "uid": row["uid"], "page": page, "paddle_chars": 0,
                     "mineru_chars": len(text),
                     "action": "rescued" if text.strip() else "skip",
                     "note": "MinerU 补投 %s 页" % len(batch)})
    write_json(run_path, run)
    skip_rows(ctx, rows)
    result["mineru_pages"] = batch
    result["mineru_rescued"] = [p for p in batch if got.get(p, 0) > 0]
    log.write("MinerU 补投 %d 页：读出 %s；仍空 %s\n"
              % (len(batch),
                 ", ".join("%d(%d 字)" % (p, got[p]) for p in batch if got.get(p, 0) > 0) or "无",
                 ", ".join(str(p) for p in batch if not got.get(p, 0)) or "无"))
    return got


def gate_text(v, audit_rc, audit):
    fails = []
    if v["verify_rc"] != 0:
        fails.append("verify rc=%d（problems=%s）" % (v["verify_rc"], v["problems"]))
    if audit_rc != 0:
        fails.append("PDF 侧审计 rc=%d（字数比 %s，锚点 %s）"
                     % (audit_rc, audit.get("ratio"), audit.get("anchors")))
    if (v["chapters"] or 0) < 1 or not (v["chars"] or 0):
        fails.append("内容文件 %s 个、%s 字" % (v["chapters"], v["chars"]))
    if (audit.get("ratio") or 0) < MIN_AUDIT_RATIO:
        fails.append("md 字数比 %s < %s" % (audit.get("ratio"), MIN_AUDIT_RATIO))
    if (audit.get("anchors") or 0) < MIN_AUDIT_ANCHORS:
        fails.append("锚点覆盖 %s < %s" % (audit.get("anchors"), MIN_AUDIT_ANCHORS))
    return fails


def enrich_text_manifest(stage, ctx, row, v, audit_rc, audit, accept):
    """在拼装器写好的 manifest 上补批次、审计、受理这几段。structure 与 toc_* 是拼装器的判断，
    原样留着。
    """
    man = L.read_md_manifest(stage)
    chapters, bytes_ = L.count_md_dir(stage)
    man.update({
        "method": "pdf-text", "tool": "pdf_text_to_md.py",
        "tool_version": ctx["tool_version"], "batch": ctx["batch_name"],
        "source": row["src"], "source_sha256": row["sha256"], "source_format": "pdf",
        "chapters": chapters, "bytes": bytes_, "chars": v["chars"],
        "images": v["images"], "structure": man.get("structure"),
        "single_file": man.get("single_file"),
        "toc_entries": man.get("toc_entries"),
        "toc_rejected": man.get("toc_rejected", ""),
        "audit_chars": audit.get("md_chars"), "audit_ratio": audit.get("ratio"),
        "audit_anchors": audit.get("anchors"), "audit_rc": audit_rc,
        "verify_rc": v["verify_rc"], "generated_at": now(),
        "verified": v["verify_rc"] == 0 and audit_rc == 0, "accepted": accept or "",
    })
    write_json(os.path.join(stage, ".manifest.json"), man)
    return man


def gate(v, coverage_ok, suspect, missing):
    fails = []
    if not coverage_ok:
        fails.append("逐页覆盖不全")
    if v["verify_rc"] != 0:
        fails.append("verify rc=%d（problems=%s）" % (v["verify_rc"], v["problems"]))
    if (v["chapters"] or 0) < 1 or not (v["chars"] or 0):
        fails.append("内容文件 %s 个、%s 字" % (v["chapters"], v["chars"]))
    if suspect:
        fails.append("可疑页 %d 个" % len(suspect))
    if missing:
        fails.append("缺图 %d 张" % len(missing))
    return fails


def enrich_manifest(stage, ctx, row, v, doc, coverage_ok, blank, suspect, short, accept,
                    rescued=(), skipped=()):
    man = L.read_md_manifest(stage)
    chapters, bytes_ = L.count_md_dir(stage)
    man.update({
        "method": "paddle-ocr", "tool": "paddleocr_pilot.py",
        "tool_version": ctx["tool_version"], "batch": ctx["batch_name"],
        "source": row["src"], "source_sha256": row["sha256"], "source_format": "pdf",
        "pages": doc["pages"], "book_pages": doc["book_pages"],
        "chapters": chapters, "bytes": bytes_, "chars": v["chars"], "images": v["images"],
        "verify_rc": v["verify_rc"], "coverage_ok": coverage_ok,
        "blank_pages": len(blank), "suspect_miss_pages": len(suspect),
        "structure": "single", "single_file": True,
        "suspect_pages": [s["page"] for s in suspect],
        "short_pages": len(short),
        "mineru_rescued_pages": list(rescued),
        "skipped_unreadable_pages": list(skipped),
        "generated_at": now(), "verified": v["verify_rc"] == 0, "accepted": accept or "",
    })
    write_json(os.path.join(stage, ".manifest.json"), man)
    return man


def land(ctx, stage, dest, result):
    """把过检的暂存目录搬进库。已存在的旧版本改名留在批次区，不删。"""
    if os.path.isdir(dest):
        old = "%s.prev-%s" % (stage, ctx["stamp"])
        os.rename(dest, old)
        result["previous"] = rel(old)
    os.rename(stage, dest)
    result["status"] = "replaced" if result.get("previous") else "landed"
    return result


# ------------------------------------------------------------------ 单本流水线
def run_one_text(row, ctx):
    """文本层路线：不切片、不调接口。抽文本 → verify → PDF 侧审计 → 门槛 → 落地。"""
    uid = row["uid"]
    dest = L.PATHS.md(row["uid"])
    stage = os.path.join(ctx["out"], uid)
    result = {"uid": uid, "title": row["title"], "format": row["format"], "route": "text",
              "pages": int(row["pages"]), "dest": rel(dest), "at": now(), "commands": []}
    os.makedirs(ctx["logs"], exist_ok=True)
    logpath = os.path.join(ctx["logs"], uid + ".log")
    if os.path.isdir(dest) and not ctx["replace"]:
        result["status"] = "skipped-has-md"
        return result

    with open(logpath, "w", encoding="utf-8") as log:
        src = os.path.join(L.ROOT, row["src"])
        if not os.path.isfile(src):
            result.update(status="source-missing", note="源文件不在: " + row["src"])
            log.write("源文件不在: %s\n" % row["src"])
            write_result(ctx, result)
            return result
        if L.sha256_file(src) != row["sha256"]:
            result.update(status="source-changed",
                          note="源文件 sha256 与库内人账不符，没抽文本：%s" % row["src"])
            log.write("源文件 sha256 与库内人账不符，没抽文本\n")
            write_result(ctx, result)
            return result

        have_stage = os.path.isdir(stage) and os.path.isfile(
            os.path.join(stage, ".manifest.json"))
        if have_stage and not ctx["force"]:
            result["note"] = "暂存区已有抽好的产物，只复查门槛"
        else:
            if os.path.isdir(stage):
                moved = os.path.join(ctx["tmp"], "%s.prev-%s" % (uid, ctx["stamp"]))
                os.rename(stage, moved)
                result["stage_previous"] = brel(ctx, moved)
            rc = assemble_text(ctx, row, src, stage, log, result)
            if rc != 0 or not os.path.isdir(stage):
                result.update(status="assemble-failed", note="pdf_text_to_md rc=%d" % rc)
                write_result(ctx, result)
                return result

        rc, v = verify(ctx, stage, log)
        v["verify_rc"] = rc
        audit_rc, audit = audit_pdf(ctx, src, stage, log)
        man = L.read_md_manifest(stage)
        result.update(verify_rc=rc, audit_rc=audit_rc, audit_ratio=audit.get("ratio"),
                      audit_anchors=audit.get("anchors"), audit_chars=audit.get("md_chars"),
                      chapters=v["chapters"], chars=v["chars"], images=v["images"],
                      problems=v["problems"], structure=man.get("structure"),
                      single_file=man.get("single_file"), pdf_pages=man.get("pages"),
                      toc_entries=man.get("toc_entries"),
                      toc_rejected=man.get("toc_rejected", ""),
                      empty_chapters=man.get("empty_chapters"))
        override = os.path.join(ctx["batch"], "overrides", uid + ".json")
        accept = ""
        if os.path.isfile(override):
            spec = json.load(open(override, encoding="utf-8"))
            accept = (spec.get("accept") if isinstance(spec, dict) else "") or ""
            result["override"] = spec
        enrich_text_manifest(stage, ctx, row, v, audit_rc, audit, accept)
        result["accepted"] = accept
        fails = gate_text(v, audit_rc, audit)
        want_land = ctx["land"] and (not os.path.isdir(dest) or ctx["replace"])
        if not fails:
            result["status"] = "staged-green"
            if want_land:
                land(ctx, stage, dest, result)
            elif ctx["land"]:
                result["note"] = "目标已有 md，未入库（要 --replace 才替换）"
        elif accept:
            result["status"] = "staged-green"
            result["note"] = "已受理例外：%s（问题：%s）" % (accept, "；".join(fails))
            if want_land:
                land(ctx, stage, dest, result)
                result["status"] = "accepted"
        else:
            result.update(status="staged-review", note="；".join(fails))
        log.write("门槛：%s；状态 %s\n" % ("过" if not fails else "；".join(fails), result["status"]))
        write_result(ctx, result)
        return result


def run_one(row, ctx):
    if ctx["route"] == "text":
        return run_one_text(row, ctx)
    uid = row["uid"]
    dest = L.PATHS.md(row["uid"])
    stage = os.path.join(ctx["out"], uid)
    result = {"uid": uid, "title": row["title"], "format": row["format"],
              "pages": int(row["pages"]), "dest": rel(dest), "at": now(), "commands": []}
    os.makedirs(ctx["work"], exist_ok=True)
    os.makedirs(ctx["logs"], exist_ok=True)
    logpath = os.path.join(ctx["logs"], uid + ".log")
    if os.path.isdir(dest) and not ctx["replace"] and not ctx["assemble_only"]:
        result["status"] = "skipped-has-md"
        return result

    with open(logpath, "w", encoding="utf-8") as log:
        ledger = load_ledger(ctx, uid)
        if ctx["api"]:
            src = os.path.join(L.ROOT, row["src"])
            if not os.path.isfile(src):
                result.update(status="source-missing", note="源文件不在: " + row["src"])
                log.write("源文件不在: %s\n" % row["src"])
                write_result(ctx, result)
                return result
            if L.sha256_file(src) != row["sha256"]:
                result.update(status="source-changed",
                              note="源文件 sha256 与库内人账不符，未提交：%s" % row["src"])
                log.write("源文件 sha256 与库内人账不符，没提交 OCR\n")
                write_result(ctx, result)
                return result
            ranges, per_chunk = plan_ranges(int(row["pages"]), src)
            plan = [list(r) for r in ranges]
            for e in ledger:
                if list(e.get("range") or []) in plan or e.get("status") == "done":
                    continue
                e["status"] = "superseded"   # 切片计划变了（页数或上限变了），旧账作废
            for a, b in ranges:
                e = entry_for(ledger, a, b)
                if e.get("status") != "done":
                    # 每跑一次给一段新的阶梯预算：退避与重试都在本次运行内有界
                    e["attempts"], e["queue_rounds"], e["status"] = 0, 0, "pending"
            save_ledger(ctx, uid, ledger)
            result.update(chunks_planned=len(ranges), pages_per_chunk=per_chunk,
                          slice_mb_estimate=round(per_chunk * os.path.getsize(src)
                                                  / int(row["pages"]) / 1e6, 1))
            log.write("切片计划 %d 段，每段 %d 页，约 %.1f MB/段\n"
                      % (len(ranges), per_chunk, result["slice_mb_estimate"]))
            for a, b in ranges:
                if not work_chunk(ctx, uid, ledger, a, b, log, result):
                    write_result(ctx, result)
                    return result
                result["chunks_done"] = len(done_ranges(ctx, uid, ledger))
                write_result(ctx, result)
        else:
            done = [e for e in ledger if e.get("status") == "done"]
            if not done:
                result.update(status="no-chunks",
                              note="assemble-only 要 work/%s/chunks.json 里有完成分片" % uid)
                return result
            bad = ["%s" % e["range"] for e in done
                   if not chunk_done(chunk_dir(ctx, uid, ledger, *e["range"]))]
            if bad:
                result.update(status="incomplete-chunks",
                              note="分片目录里没有 paddle_run.json: " + ", ".join(bad))
                return result

        try:
            merged, doc = merge(ctx, uid, row, ledger, log, result)
        except MergeError as exc:
            result.update(status="chunk-mismatch", note=str(exc))
            log.write("合并停住：%s\n" % exc)
            write_result(ctx, result)
            return result
        result.update(merged_pages=doc["pages"], coverage_ok=doc["coverage_ok"],
                      merged_run_json=os.path.relpath(os.path.join(merged, "run.json"),
                                                      ctx["batch"]))

        have_stage = os.path.isdir(stage) and os.path.isfile(
            os.path.join(stage, ".manifest.json"))
        if have_stage and not ctx["force"]:
            result["note"] = "暂存区已有拼装产物，只复查门槛"
        else:
            if os.path.isdir(stage):
                # 旧暂存产物挪进 tmp/，不删：那里面可能有上次替换留下的 .prev 留档
                moved = os.path.join(ctx["tmp"], "%s.prev-%s" % (uid, ctx["stamp"]))
                os.rename(stage, moved)
                result["stage_previous"] = brel(ctx, moved)
            rc = assemble(ctx, row, merged, stage, log, result)
            if rc != 0 or not os.path.isdir(stage):
                result.update(status="assemble-failed", note="pdf_ocr_to_md rc=%d" % rc)
                write_result(ctx, result)
                return result

        rc, v = verify(ctx, stage, log)
        v["verify_rc"] = rc
        result.update(verify_rc=rc, chapters=v["chapters"], chars=v["chars"],
                      images=v["images"], problems=v["problems"])
        blank, suspect, short = pdf_checks(ctx, row, doc["per_page"], log, result)
        skipped = []
        if suspect and ctx["api"]:
            got = mineru_rescue(ctx, row, merged, doc, suspect, log, result)
            if got is None:
                # 没投过就不能当「MinerU 也读不出」，可疑页仍留人工复核
                result["mineru_not_run"] = result.get("mineru_skip_reason", "")
                log.write("MinerU 未投：%s；可疑页不按跳过受理\n" % result["mineru_not_run"])
            else:
                if any(got.get(s["page"], 0) > 0 for s in suspect):
                    if rebuild_stage(ctx, row, merged, stage, log, result) == 0:
                        rc, v = verify(ctx, stage, log)
                        v["verify_rc"] = rc
                        blank, suspect, short = pdf_checks(ctx, row, doc["per_page"], log, result)
                    else:
                        log.write("MinerU 补页后重拼装失败，重新拼装一次再复查\n")
                        if assemble(ctx, row, merged, stage, log, result) == 0 \
                                and os.path.isdir(stage):
                            rc, v = verify(ctx, stage, log)
                            v["verify_rc"] = rc
                            blank, suspect, short = pdf_checks(ctx, row, doc["per_page"], log, result)
                        else:
                            result["note"] = "MinerU 补页后重新拼装仍失败，页数待查"
                skipped = [s["page"] for s in suspect]
                result["skipped_unreadable_pages"] = skipped
            result.update(verify_rc=v["verify_rc"], chapters=v["chapters"], chars=v["chars"],
                          images=v["images"], problems=v["problems"])
        override = os.path.join(ctx["batch"], "overrides", uid + ".json")
        human = ""
        if os.path.isfile(override):
            spec = json.load(open(override, encoding="utf-8"))
            human = (spec.get("accept") if isinstance(spec, dict) else "") or ""
            result["override"] = spec
        auto = ("MinerU 也读不出，按跳过受理：第 %s 页"
                % ", ".join(str(p) for p in skipped)) if skipped else ""
        accept = "；".join(x for x in (human, auto) if x)
        man = enrich_manifest(stage, ctx, row, v, doc, doc["coverage_ok"], blank, suspect, short,
                              accept, result.get("mineru_rescued") or [], skipped)
        result["accepted"] = man["accepted"]
        fails = gate(v, doc["coverage_ok"], suspect, doc["missing_images"])
        if skipped:
            # 自动跳过只免「可疑页」这一条，verify/覆盖/缺图的门槛照旧拦
            fails = [f for f in fails if not f.startswith("可疑页")]
        want_land = ctx["land"] and (not os.path.isdir(dest) or ctx["replace"])
        if not fails:
            result["status"] = "staged-green"
            if skipped:
                result["note"] = "%d 页按跳过受理（%s）" % (len(skipped), auto)
            if want_land:
                land(ctx, stage, dest, result)
                if skipped:
                    result["status"] = "accepted"
            elif ctx["land"]:
                result["note"] = "目标已有 md，未入库（要 --replace 才替换）"
        elif human:
            result["status"] = "staged-green"
            result["note"] = "已受理例外：%s（问题：%s）" % (accept, "；".join(fails))
            if want_land:
                land(ctx, stage, dest, result)
                result["status"] = "accepted"
        else:
            result.update(status="staged-review", note="；".join(fails))
        log.write("门槛：%s；状态 %s\n" % ("过" if not fails else "；".join(fails), result["status"]))
        write_result(ctx, result)
        return result


def write_result(ctx, result):
    write_json(os.path.join(ctx["logs"], result["uid"] + ".json"), result)


def pct(v):
    return ("%.2f%%" % (v * 100)) if isinstance(v, (int, float)) else "-"


def load_results(ctx):
    results, broken = [], []
    if not os.path.isdir(ctx["logs"]):
        return results, broken
    for name in sorted(os.listdir(ctx["logs"])):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(ctx["logs"], name), encoding="utf-8") as fh:
                results.append(json.load(fh))
        except (OSError, json.JSONDecodeError):
            broken.append(name)
    return results, broken


def report(ctx):
    results, broken = load_results(ctx)
    if not os.path.isdir(ctx["logs"]):
        print("没有日志目录: %s" % ctx["logs"])
        return
    if broken:
        print("警告：%d 份 logs 读不出，统计不含它们：%s"
              % (len(broken), " ".join(broken[:5])))
    counts = collections.Counter(r.get("status") for r in results)
    print("\n状态统计")
    for k, v in sorted(counts.items(), key=lambda x: str(x[0])):
        print("  %-18s %d" % (k, v))
    dpath = os.path.join(ctx["batch"], DEFER_LEDGER)
    deferred = defer_uids(dpath)
    if deferred:
        print("\n暂缓（语种账 %s）" % os.path.relpath(dpath, ctx["batch"]))
        print("  ja%-16s %d 本 / %d 页"
              % ("", len(deferred),
                 sum(int(r.get("pages") or 0) for r in deferred.values())))
    if not results:
        return
    print("\n逐本")
    for r in sorted(results, key=lambda x: (int(x["pages"]), x["uid"])):
        if r.get("route") == "text":
            print("  %-6s %-13s %-30.30s 页 %4s/%s %-8s 章 %-3s 字 %s 比 %s 锚 %s rc=%s/%s %s"
                  % (r["uid"], r.get("status"), r.get("title", ""), r.get("pdf_pages"),
                     r.get("pages"), r.get("structure"), r.get("chapters"), r.get("chars"),
                     pct(r.get("audit_ratio")), pct(r.get("audit_anchors")),
                     r.get("verify_rc"), r.get("audit_rc"),
                     ("受理：" + r["accepted"]) if r.get("accepted") else r.get("note", "")))
            continue
        print("  %-6s %-7s %-30.30s 页 %4s/%s 章 %s 字 %s rc=%s 覆盖=%s 空页=%s 可疑=%s 短页=%s %s"
              % (r["uid"], r.get("status"), r.get("title", ""), r.get("merged_pages"),
                 r.get("pages"), r.get("chapters"), r.get("chars"), r.get("verify_rc"),
                 r.get("coverage_ok"), r.get("blank_pages"), r.get("suspect_miss_pages"),
                 r.get("short_pages"),
                 ("受理：" + r["accepted"]) if r.get("accepted") else r.get("note", "")))
    bad = [r for r in results if r.get("status") not in GREEN + ("skipped-has-md",)]
    if bad:
        print("\n待查 %d 本" % len(bad))
        for r in bad:
            print("  %-6s %-18s %-38.38s %s"
                  % (r["uid"], r.get("status"), r.get("title", ""), r.get("note", "")))


def report_json(ctx):
    """landed 只算真落地的状态，skipped-has-md 算已完成但不算落地。范围是这次要跑的书。"""
    results, broken = load_results(ctx)
    rows = ctx.get("rows")
    if rows is None:
        rows = read_worklist(ctx["batch"]) or []
    results = [r for r in results if r.get("uid") in {x["uid"] for x in rows}]
    finished = {r.get("uid") for r in results
                if r.get("status") in GREEN + ("skipped-has-md",)}
    left = [r for r in rows if r["uid"] not in finished]
    counts = collections.Counter(str(r.get("status")) for r in results)
    return {"batch": ctx["batch_name"], "route": ctx["route"],
            "queued": len(rows),
            "landed": sum(1 for r in results if r.get("status") in GREEN),
            "pages": sum(int(r.get("pages") or 0) for r in rows),
            "remaining_books": len(left),
            "remaining_pages": sum(int(r.get("pages") or 0) for r in left),
            "status_counts": dict(sorted(counts.items())),
            "broken_logs": len(broken)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True, help="批次目录名，在 工作区/批次/ 下")
    ap.add_argument("--route", choices=("ocr", "text"), default="ocr",
                    help="ocr 走扫描件那条（切片+接口），text 抽纯文本层 PDF 的文本层")
    ap.add_argument("--batch-dir", dest="batch_dir",
                    help="把批次目录指到别处（离线演练/测试用），默认 工作区/批次/<批次>")
    ap.add_argument("--workers", type=int, default=3, help="同时跑几本；一本书的分片是串行的")
    ap.add_argument("--only", help="只跑这些 uid，逗号分隔")
    ap.add_argument("--limit", type=int, help="最多跑几本（调试用）")
    ap.add_argument("--no-land", action="store_true", dest="noland", help="只做暂存，不进 books/")
    ap.add_argument("--force", action="store_true",
                    help="重拼装：忽略暂存区已有产物。缺的分片会重新提交，那是要花 OCR 额度的")
    ap.add_argument("--replace", action="store_true",
                    help="目标 md 已存在时也重做并替换，旧版本改名留在批次区")
    ap.add_argument("--assemble-only", dest="assemble_only", metavar="UID",
                    help="只合并已落盘的分片并拼装这一本，不调接口；要求分片目录完整")
    ap.add_argument("--queue-rounds", dest="queue_rounds", type=int, default=len(QUEUE_BACKOFF),
                    help="接口报提交队列已满（10010）时，同一段最多退避重试几次（默认 %d）"
                         % len(QUEUE_BACKOFF))
    ap.add_argument("--report", action="store_true", help="只读 logs/ 出统计，不干活")
    ap.add_argument("--report-json", dest="report_json", action="store_true",
                    help="跑完（或 --report）在 stdout 最后一行再给一份 JSON，给页面读")
    ap.add_argument("--uids", help="本次要跑哪些 uid（账上没有的补进账），逗号分隔")
    ap.add_argument("--triage", metavar="CSV",
                    help="分诊表路径，默认全局 triage_final.csv；新批次可指自己的"
                         "（同目录的 skip_list.tsv 优先，没有就用全局的）")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="只打印本次会跑哪些书，不调接口、不改账")
    ap.add_argument("--defer", metavar="TSV",
                    help="语种账路径，默认 <批次>/%s；账里 lang=ja 的书本次不跑"
                         % DEFER_LEDGER)
    ap.add_argument("--ignore-defer", dest="ignore_defer", action="store_true",
                    help="忽略语种账，日文原版也跑")
    a = ap.parse_args()

    if a.route == "text" and a.assemble_only:
        sys.exit("--assemble-only 是 OCR 分片的路径；文本层路线直接抽文本，不用它")
    batch_path = a.batch_dir or os.path.join(L.WORK, "批次", a.batch)
    out = os.path.join(batch_path, "out")
    tmp = os.path.join(batch_path, "tmp")
    ctx = {"batch": batch_path, "batch_name": a.batch, "out": out, "tmp": tmp,
           "logs": os.path.join(batch_path, "logs"), "work": os.path.join(batch_path, "work"),
           "review": os.path.join(batch_path, "review"), "land": not a.noland,
           "force": a.force, "replace": a.replace, "api": not a.assemble_only,
           "route": a.route,
           "assemble_only": a.assemble_only, "queue_rounds": a.queue_rounds, "abort": "",
           "pilots": {}, "pilot_lock": threading.Lock(), "cancelling": False, "canceller": None,
           "stamp": now().replace(":", "").replace("+", "-"),
           "tool_version": text_tool_version() if a.route == "text" else tool_version()}

    picked_uids = [u.strip() for u in (a.uids or "").split(",") if u.strip()]
    if a.report:
        ledger = read_worklist(batch_path) or []
        ctx["rows"] = worklist_scope(ledger, triage_routes(a.triage), ROUTE_TRIAGE[a.route],
                                     picked_uids)
        if ledger and not ctx["rows"]:
            print("账上 %d 本都不在 route=%s 这条线上，换 --route 再看"
                  % (len(ledger), a.route))
        report(ctx)
        if a.report_json:
            print(json.dumps(report_json(ctx), ensure_ascii=False))
        return 0
    if not os.path.exists(VENV_PY):
        sys.exit("缺 %s（pypdf/pymupdf/requests）" % rel(VENV_PY))
    if subprocess.run([sys.executable, "-c", "import pymupdf"], capture_output=True).returncode:
        sys.exit("当前解释器没有 pymupdf，PDF 侧体检跑不了，请用 %s 跑" % rel(VENV_PY))
    for d in ("out", "tmp", "logs", "work", "review"):
        if not a.dry_run:
            os.makedirs(ctx[d], exist_ok=True)

    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    triage_path = a.triage or TRIAGE
    skip_path = os.path.join(os.path.dirname(os.path.abspath(triage_path)), "skip_list.tsv")
    if not os.path.isfile(skip_path):
        skip_path = SKIP_LIST
    if picked_uids and os.path.exists(os.path.join(batch_path, "worklist.tsv")):
        print("批次已有 worklist.tsv，点名的书补进账，本次只跑点名的")
    rows = build_worklist(con, batch_path, a.route, dry=a.dry_run, uids=picked_uids,
                          triage=triage_path, skip=skip_path)
    ctx["rows"] = rows
    known = {r["uid"]: r for r in rows}
    deferred = {} if a.ignore_defer else defer_uids(
        a.defer or os.path.join(batch_path, DEFER_LEDGER))
    pick = a.only or a.assemble_only
    if pick:
        wanted = [u.strip() for u in pick.split(",") if u.strip()]
        rows = [known[u] for u in wanted if u in known]
        missing = [u for u in wanted if u not in known]
        if missing:
            sys.exit("worklist 里没有 route=%s 的 %s（账上是另一条 route 的书？）"
                     % (a.route, ", ".join(missing)))
        picked = [r["uid"] for r in rows if r["uid"] in deferred]
        if picked:
            print("注意：%s 在语种账里是日文原版，--only 点名了就跑" % ", ".join(picked))
    elif deferred:
        keep = [r for r in rows if r["uid"] not in deferred]
        if len(keep) != len(rows):
            print("暂缓 %d 本日文原版（%d 页），本次不跑"
                  % (len(rows) - len(keep),
                     sum(int(r["pages"] or 0) for r in rows if r["uid"] in deferred)))
        rows = keep
    if a.limit is not None:
        rows = rows[:a.limit]
    if a.dry_run:
        keep_existing = a.replace or (a.assemble_only and a.route != "text")
        run, skip_n, skip_pages = [], 0, 0
        for r in rows:
            pages = int(r["pages"] or 0)
            if not keep_existing and os.path.isdir(L.PATHS.md(r["uid"])):
                skip_n += 1
                skip_pages += pages
                continue
            run.append(r)
        if skip_n:
            print("已有 md 跳过 %d 本（%d 页）" % (skip_n, skip_pages))
        print("本次会跑 %d 本，%d 页" % (len(run), sum(int(r["pages"] or 0) for r in run)))
        for r in run:
            print("  %-6s 页 %4s  %s" % (r["uid"], r["pages"], r["title"][:50]))
        return 0
    print("批次 %s%s，%s %d 本，%d 并发，%s"
          % (a.batch, ("（assemble-only，不调接口）" if a.assemble_only else ""),
             "待抽文本" if a.route == "text" else "待 OCR", len(rows),
             a.workers, "入库" if ctx["land"] else "只暂存"))

    results, done = [], 0
    lock = threading.Lock()
    pending = iter(rows)

    def take():
        with lock:
            if ctx["abort"]:
                return None
            return next(pending, None)

    def work():
        nonlocal done
        while True:
            r = take()
            if r is None:
                return
            try:
                res = run_one(r, ctx)
            except Exception as exc:  # 单本炸了不拖住整批
                res = {"uid": r["uid"], "title": r["title"], "format": r["format"],
                       "pages": int(r["pages"]), "dest": rel(L.PATHS.md(r["uid"])), "at": now(),
                       "status": "error", "note": "%s: %s" % (type(exc).__name__, exc)}
            with lock:
                write_result(ctx, res)
                results.append(res)
                done += 1
                print("[%d/%d] %s %-16s %-38.38s %s" %
                      (done, len(rows), res["uid"], res.get("status"), res.get("title", ""),
                       "章 %s 字 %s rc %s" % (res.get("chapters"), res.get("chars"),
                                              res.get("verify_rc"))), flush=True)

    with futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(lambda _: work(), range(min(a.workers, len(rows)))))
    if ctx["abort"]:
        left = len(rows) - done
        print("\n本批中止：%s。%d 本没轮到（日志里没有它们的记录）" % (ctx["abort"], left))
    report(ctx)
    if a.report_json:
        print(json.dumps(report_json(ctx), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
