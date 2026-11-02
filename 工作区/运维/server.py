#!/usr/bin/env python3
"""统一操作台的本地服务，127.0.0.1:8770，只用标准库。

页面读 ops_status.py 的 JSON 与 ops.db；按钮排队调 工作区/脚本/ 里的脚本。
不写 tables/、build/，不重建真索引；除上传落 books/_收件箱/ 外不碰 books/。

  python3 工作区/运维/server.py [--port 8770] [--root .]
"""
import argparse
import collections
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(CODE_DIR, "web")
REAL_ROOT = os.path.dirname(os.path.dirname(CODE_DIR))

def _package_scripts():
    try:
        from bookcorpus.paths import scripts_dir
        return scripts_dir()
    except ImportError:
        return os.path.join(os.path.dirname(os.path.dirname(CODE_DIR)), "src", "bookcorpus", "scripts")



STATES = ("queued", "running", "awaiting_review", "succeeded", "failed",
          "cancelled", "orphaned")
DONE_STATES = ("succeeded", "failed", "cancelled", "orphaned")
READ_SLOTS = 2
JSON_MAX = 1024 * 1024
UPLOAD_MAX = 2 * 1024 ** 3
STATUS_TTL = 6.0
KILL_WAIT = 10
LOG_TAIL_LINES = 200
SEARCH_URL = "http://127.0.0.1:8765"
TRIAGE_BATCH_PREFIX = "pdf-ocr-ops-"
TRIAGE_PDF_DIR = os.path.join("工作区", "批次", "pdf-普查-2026-10-19")
OCR_BATCH = os.path.join("工作区", "批次", "pdf-ocr-2026-10-19")
PDF_SKIP_LIST = os.path.join(TRIAGE_PDF_DIR, "skip_list.tsv")
PDF_DEFER = os.path.join(OCR_BATCH, "defer", "lang.tsv")
PDF_WORKLIST = os.path.join(OCR_BATCH, "worklist.tsv")
VENV_PDF = os.path.join("工作区", ".venv-pdf", "bin", "python")
VENV_PDF_SCRIPTS = ("pdf_md_batch.py",)
PDF_DISPOSITIONS = ("convert", "gallery", "ja", "untriaged")
PDF_CONVERT_ROUTES = ("OCR", "本地抽文本")
PENDING_PDF_SQL = (
    "SELECT b.uid, b.title, b.subject1, b.subject2, s.file FROM book b"
    " JOIN source s ON s.uid = b.uid AND s.primary_src = 1"
    " WHERE s.format = 'pdf' AND NOT EXISTS (SELECT 1 FROM derived d"
    " WHERE d.uid = b.uid AND d.kind = 'md' AND d.status = 'done')")
PROBE_TEXT_MIN_CHARS = 100
PDF_PAGE_SCRIPT = '''import json, sys
import pymupdf
TEXT_MIN = %d
out = {}
for item in json.load(sys.stdin):
    try:
        doc = pymupdf.open(item["path"])
    except Exception:
        continue
    pages = doc.page_count
    picks = sorted({1, (pages + 1) // 4, (pages + 1) // 2, (3 * (pages + 1)) // 4, pages})
    picks = [p for p in picks if 1 <= p <= pages]
    hits = sum(1 for p in picks if len(doc[p - 1].get_text().strip()) >= TEXT_MIN)
    out[item["uid"]] = {"pages": pages,
                        "text": int(bool(picks) and hits * 2 >= len(picks))}
print(json.dumps(out))
''' % PROBE_TEXT_MIN_CHARS
PDF_DAILY_CAP = 20000
PDF_SETTLE_ROUTES = ("OCR", "本地抽文本", "不转-记图册", "日文暂缓")
PDF_RECHECK_METRICS = ("OCR-复看", "复看-可能OCR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  argv TEXT NOT NULL,
  state TEXT NOT NULL,
  pid INTEGER,
  created TEXT NOT NULL,
  started TEXT,
  ended TEXT,
  exit_code INTEGER,
  summary TEXT,
  log_path TEXT,
  batch_dir TEXT,
  plan_hash TEXT
);
CREATE TABLE IF NOT EXISTS step (
  job_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT,
  started TEXT,
  ended TEXT,
  PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS job_state ON job (state, created);
"""


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[A-Za-z0-9._\-]{8,}"),
)


def redact(text):
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("[redacted]", out)
    return out


def tail_bytes(path, limit=300_000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def tail_lines(text, count=LOG_TAIL_LINES):
    lines = text.splitlines()
    return "\n".join(lines[-count:])


def json_blobs(text):
    """日志末尾能解析的 JSON 值，按结束位置升序。"""
    dec = json.JSONDecoder()
    window = text[-200_000:]
    out = []
    i = 0
    while i < len(window):
        if window[i] not in "{[":
            i += 1
            continue
        try:
            value, end = dec.raw_decode(window, i)
        except ValueError:
            i += 1
            continue
        out.append((value, end))
        i = end if end > i else i + 1
    return out


def last_json_blob(text):
    """日志里最后一个能解析的 JSON 值。"""
    best, best_end = None, -1
    for value, end in json_blobs(text):
        if end > best_end:
            best, best_end = value, end
    return best


def json_blob_with(text, key):
    """日志里最后一个带指定字段的 JSON 对象。重建步骤的 JSON 排在报告后面。"""
    found = None
    for value, _end in json_blobs(text):
        if isinstance(value, dict) and key in value:
            found = value
    return found


def pid_alive(pid):
    """活着才算活着：僵尸已经有退出码了，不能再当在跑。"""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % int(pid), encoding="utf-8") as fh:
            stat = fh.read()
    except OSError:
        return False
    tail = stat[stat.rfind(")") + 1:].split()
    return not (tail and tail[0] in ("Z", "X", "x"))


def cmdline_of(pid):
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def rc_of_detail(detail):
    """步骤表里写的 rc=2 这种；读不出来就是 None，不拿 -1 冒充。"""
    match = re.match(r"^rc=(-?\d+)$", (detail or "").strip())
    return int(match.group(1)) if match else None


class Env:
    """一个数据根下的全部路径。--root 换根，代码目录仍指本文件所在处。"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.ops = os.path.join(self.root, "工作区", "运维")
        self.jobs = os.path.join(self.ops, "jobs")
        self.scripts = _package_scripts()
        self.batches = os.path.join(self.root, "工作区", "批次")
        self.lib = os.path.join(self.root, "books")
        self.inbox = os.path.join(self.lib, "_收件箱")
        self.cat = os.path.join(self.root, "tables")
        self.views = os.path.join(self.root, "build", "views")
        self.libdb = os.path.join(self.root, "build", "library.db")
        self.indexdb = os.path.join(self.root, "build", "检索", "index.db")
        self.catalog_csv = os.path.join(self.root, "build", "书目总表.csv")
        self.ops_db = os.path.join(self.ops, "ops.db")
        self.lock = os.path.join(self.ops, ".writer.lock")
        self.reader_lock = os.path.join(self.ops, ".reader.lock")
        self.search_log = os.path.join(self.ops, "search.log")
        self.search_pid = os.path.join(self.ops, "search.pid")
        self.audit_cache = os.path.join(self.ops, "last-audit.json")
        self.status_script = os.path.join(self.scripts, "ops_status.py")

    def prepare(self):
        for path in (self.ops, self.jobs):
            os.makedirs(path, exist_ok=True)

    def job_dir(self, job_id):
        return os.path.join(self.jobs, job_id)

    def rel(self, path):
        try:
            return os.path.relpath(path, self.root)
        except ValueError:
            return path


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.lock = threading.RLock()

    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def create(self, row):
        self._exec(
            "INSERT INTO job (id, kind, title, argv, state, pid, created, started,"
            " ended, exit_code, summary, log_path, batch_dir, plan_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["kind"], row["title"], row["argv"], row["state"],
             row.get("pid"), row["created"], row.get("started"), row.get("ended"),
             row.get("exit_code"), row.get("summary"), row.get("log_path"),
             row.get("batch_dir"), row.get("plan_hash")))

    def update(self, job_id, **fields):
        if not fields:
            return
        keys = sorted(fields)
        self._exec("UPDATE job SET %s WHERE id=?" % ", ".join("%s=?" % k for k in keys),
                   tuple(fields[k] for k in keys) + (job_id,))

    def get(self, job_id):
        rows = self._rows("SELECT * FROM job WHERE id=?", (job_id,))
        return rows[0] if rows else None

    def queued(self, limit=50):
        return self._rows("SELECT * FROM job WHERE state='queued' ORDER BY created ASC"
                          " LIMIT ?", (limit,))

    def list(self, state=None, limit=200):
        if state:
            return self._rows("SELECT rowid AS seq, * FROM job WHERE state=?"
                              " ORDER BY created DESC LIMIT ?", (state, limit))
        return self._rows("SELECT rowid AS seq, * FROM job ORDER BY created DESC LIMIT ?",
                          (limit,))

    def counts(self):
        rows = self._rows("SELECT state, count(*) n FROM job GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    def add_step(self, job_id, seq, name, state="pending", detail=None):
        self._exec("INSERT OR REPLACE INTO step (job_id, seq, name, state, detail,"
                   " started) VALUES (?,?,?,?,?,?)",
                   (job_id, seq, name, state, detail, now_iso()))

    def step_state(self, job_id, seq, state, detail=None):
        self._exec("UPDATE step SET state=?, detail=?, ended=? WHERE job_id=? AND seq=?",
                   (state, detail, now_iso(), job_id, seq))

    def steps(self, job_id):
        return self._rows("SELECT seq, name, state, detail, started, ended FROM step"
                          " WHERE job_id=? ORDER BY seq", (job_id,))

    def delete(self, job_id):
        self._exec("DELETE FROM step WHERE job_id=?", (job_id,))
        self._exec("DELETE FROM job WHERE id=?", (job_id,))


class Ctx:
    """一个正在收尾的任务：跑过的命令、日志、产物。"""

    def __init__(self, env, job, commands, rcs):
        self.env = env
        self.job = job
        self.commands = commands
        self.rcs = rcs

    @property
    def job_dir(self):
        return self.env.job_dir(self.job["id"])

    def artifact(self, name):
        return read_json(os.path.join(self.job_dir, name))

    def text(self):
        return tail_bytes(os.path.join(self.job_dir, "log.txt"))

    def section(self, name):
        text = self.text()
        head = "---- step %s ----" % name
        start = text.rfind(head)
        if start < 0:
            return ""
        nxt = text.find("\n---- step ", start + len(head))
        return text[start:] if nxt < 0 else text[start:nxt]

    def failed(self):
        return [(n, rc) for n, rc in self.rcs if rc not in (0, None)]


def gate_rc(ctx):
    blob = last_json_blob(ctx.text())
    why = ""
    if isinstance(blob, dict):
        if blob.get("ok") is False or blob.get("error"):
            why = "：" + str(blob.get("error") or blob.get("ok"))[:200]
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s%s" % (
            "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad), why)
    return "succeeded", "%d 步完成%s" % (len(ctx.rcs), why)


def gate_import_scan(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "扫描失败：%s" % "；".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    plan = ctx.artifact("plan.json")
    if plan is None:
        return "failed", "计划文件没写出来（jobs/<id>/plan.json）"
    if not isinstance(plan, list):
        return "failed", "计划不是数组"
    counts = collections.Counter(str(i.get("action") or "?") for i in plan)
    summary = "计划待确认：add %d / replace %d / conflict %d / skip %d" % (
        counts.get("add", 0), counts.get("replace", 0),
        counts.get("conflict", 0), counts.get("skip", 0))
    if counts.get("conflict") or counts.get("replace"):
        summary += "；有需要逐条定的行"
    return "awaiting_review", summary


def gate_import_apply(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    text = ctx.text()
    conflicts = re.findall(r'"conflicts":\s*(\d+)', text)
    if not conflicts:
        return "failed", "没取到 conflicts 读数（build_library 输出不在日志里）"
    if int(conflicts[-1]) != 0:
        return "failed", "路径冲突 conflicts=%s，不是 0" % conflicts[-1]
    if "未匹配 0 条" not in text:
        found = re.findall(r"未匹配 (\d+) 条", text)
        return "failed", "书目总表门槛未过：未匹配 %s 条" % (found[-1] if found else "?")
    return "succeeded", "conflicts=0，未匹配 0 条"


def gate_split(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    report = ctx.artifact("report.json")
    if report is None:
        report = json_blob_with(ctx.text(), "status_counts")
    if report is None:
        return "failed", "没取到 --report-json 统计，判不了 verify 与 audit"
    pending = report.get("pending_review")
    if not isinstance(pending, list):
        return "failed", "统计里没有待复核清单，重跑一次"
    counts = report.get("status_counts") or {}
    summary = "批次 %s：%s" % (report.get("batch") or "?",
                             "，".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    if pending:
        return "awaiting_review", summary + "；待复核 %d 本（%s）" % (
            len(pending), "、".join(pending[:6]))
    return "succeeded", summary


def gate_inspect(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    rows = ctx.artifact("result.json")
    if not isinstance(rows, list) or not rows:
        return "failed", "没取到这本书的核对证据（只查还留在暂存区的书）"
    row = rows[0]
    bits = []
    ratio = row.get("ratio")
    if isinstance(ratio, (int, float)):
        bits.append("审计比 %.1f%%" % (ratio * 100))
    uncovered = row.get("uncovered")
    if isinstance(uncovered, list):
        bits.append("未收录 %d 页" % len(uncovered))
    if row.get("dup_shingles"):
        bits.append("跨文件重复 %d 处" % row["dup_shingles"])
    if not bits:
        bits.append("没有发现疑点")
    return "succeeded", "《%s》%s" % (row.get("title") or row.get("uid") or "这本书",
                                    "，".join(bits))


def gate_pdf(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    report = ctx.artifact("report.json")
    if report is None:
        report = json_blob_with(ctx.text(), "remaining_books")
    if report is None:
        return "failed", "没取到 --report-json 统计"
    landed = report.get("landed")
    remaining = report.get("remaining_books")
    pages = report.get("remaining_pages")
    counts = report.get("status_counts") or {}
    summary = "本次落地 %s 本" % landed
    if counts.get("skipped-has-md"):
        summary += "，%s 本已有 md 跳过" % counts["skipped-has-md"]
    summary += "，本次范围剩 %s 本 / %s 页" % (remaining, pages)
    if counts.get("quota"):
        return "awaiting_review", summary + "；额度用尽，明日续跑"
    if counts.get("failed"):
        return "failed", summary + "；失败 %d 本" % counts["failed"]
    if remaining:
        return "awaiting_review", summary
    return "succeeded", summary


def gate_triage(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    result = ctx.artifact("result.json")
    if result is None:
        return gate_triage_batch(ctx)
    model = result.get("model") or {}
    thumbs = result.get("thumbs") or []
    if model.get("route"):
        summary = "模型建议 %s（%s）待人工确认" % (
            model["route"], format_conf(model.get("confidence")))
    else:
        summary = "无模型建议，待人工判断"
    summary += "；缩略图 %d 张" % len(thumbs)
    return "awaiting_review", summary


def gate_triage_batch(ctx):
    try:
        names = sorted(os.listdir(ctx.job_dir))
    except OSError:
        names = []
    results = []
    for name in names:
        one = read_json(os.path.join(ctx.job_dir, name, "result.json"))
        if isinstance(one, dict) and one.get("uid") == name:
            results.append(one)
    if not results:
        return "failed", "分诊没有结果文件 result.json"
    routed = sum(1 for one in results if (one.get("model") or {}).get("route"))
    thumbs = sum(len(one.get("thumbs") or []) for one in results)
    return "awaiting_review", "%d 本出建议，%d 本有模型路线；缩略图共 %d 张" % (
        len(results), routed, thumbs)


def gate_dryrun(ctx):
    bad = ctx.failed()
    if bad:
        return "failed", "失败步：%s" % "、".join("%s(rc=%s)" % (n, rc) for n, rc in bad)
    blob = ctx.artifact("dry-run.json")
    if blob is None:
        return "failed", "预览没有输出 JSON（retire_book --dry-run --json）"
    if blob.get("ok") is False:
        return "failed", str(blob.get("error") or "预览拒绝")[:200]
    sources = blob.get("sources") or []
    summary = "预览 %s：源 %d 个，md %s 章，plan_hash %s" % (
        blob.get("title") or blob.get("uid"), len(sources),
        blob.get("md_chapters"), str(blob.get("plan_hash") or "")[:12])
    if blob.get("snapshot_ok") is False:
        summary += "；快照不可用，不能应用"
    return "succeeded", summary


def gate_maintain(ctx):
    misses = []
    ok_steps = 0
    for cmd in ctx.commands:
        name, marker = cmd["name"], cmd.get("marker")
        rc = dict(ctx.rcs).get(name)
        if rc is None:
            misses.append("%s 没跑到" % name)
            continue
        if rc != 0:
            misses.append("%s rc=%s" % (name, rc))
            continue
        if marker:
            text = ctx.section(name)
            if not re.search(marker, text):
                misses.append("%s 未出现门槛 %s" % (name, marker))
                continue
        ok_steps += 1
    if misses:
        return "failed", "门槛未过：" + "；".join(misses)
    return "succeeded", "%d 步全过" % ok_steps


def format_conf(value):
    try:
        return "%.2f" % float(value)
    except (TypeError, ValueError):
        return "-"


def recheck_row(row):
    if not row:
        return False
    if (row.get("route_metric") or "").strip() in PDF_RECHECK_METRICS:
        return True
    return "复看" in (row.get("verdict") or "")


def untriaged_split(books):
    out = {"books": 0, "pages": 0,
           "text": {"books": 0, "pages": 0}, "scan": {"books": 0, "pages": 0}}
    for row in books.values():
        if row["disposition"] != "untriaged":
            continue
        pages = int(row["pages"] or 0)
        out["books"] += 1
        out["pages"] += pages
        if row.get("text_layer") is None:
            continue
        key = "text" if row["text_layer"] else "scan"
        out[key]["books"] += 1
        out[key]["pages"] += pages
    return out


# readonly=True 的承诺：任务只写自己的 jobs/<id>/，读的共享文件都是 tmp+replace
# 原子写。锁按它分文件（.reader.lock / .writer.lock）；凡要动 库/、批次账、目录 的
# 步骤必须开成写任务，标错了锁就白分了。
KINDS = {
    "import.scan": {"label": "扫收件箱", "readonly": True, "gate": gate_import_scan},
    "import.apply": {"label": "导入入库", "readonly": False, "gate": gate_import_apply},
    "split.run": {"label": "电子书拆章", "readonly": False, "gate": gate_split},
    "split.inspect": {"label": "查看问题", "readonly": True, "gate": gate_inspect},
    "pdf.triage": {"label": "PDF 分诊", "readonly": True, "gate": gate_triage},
    "pdf.confirm": {"label": "分诊确认", "readonly": False, "gate": gate_rc},
    "pdf.run": {"label": "PDF 转 md", "readonly": False, "gate": gate_pdf},
    "maintain": {"label": "维护收尾", "readonly": False, "gate": gate_maintain},
    "delete.dry-run": {"label": "删书预览", "readonly": True, "gate": gate_dryrun},
    "delete.apply": {"label": "删书应用", "readonly": False, "gate": gate_rc},
}


MAINTAIN_STEPS = {
    "db": {"label": "重建 library.db", "script": "build_library.py",
           "args": ["--emit-json"], "marker": r'"conflicts":\s*0'},
    "index": {"label": "检索索引", "script": "build_para_index.py",
              "args": ["--rebuild", "--concepts"]},
    "topics": {"label": "主题软链", "script": "build_views.py", "args": ["--only", "topics"]},
    "awards": {"label": "奖项软链", "script": "build_views.py", "args": ["--only", "awards"]},
    "audit": {"label": "深审计", "script": "audit_state.py", "args": ["--deep"],
              "marker": r"FAIL 0 项"},
    "verify": {"label": "六箱验收", "script": "verify_phase.py", "args": [],
               "marker": r"(?m)^全过\s*$"},
    "p1": {"label": "检索验收", "script": "check_p1.py", "args": [],
           "marker": r"失败 0 项"},
    "catalog": {"label": "书目总表", "script": "gen_book_catalog.py", "args": [],
                "marker": r"未匹配 0 条"},
    "doc": {"label": "阅读版 html", "script": "build_index.py", "args": []},
}
MAINTAIN_ORDER = ("db", "index", "topics", "awards", "audit", "verify", "p1",
                  "catalog", "doc")
FINISH_DEFAULT = ("db", "index", "topics", "awards", "audit", "verify", "p1",
                  "catalog")


def rebuild_commands(env):
    return [make_cmd(env, "build_library", "build_library.py", ["--emit-json"]),
            make_cmd(env, "gen_book_catalog", "gen_book_catalog.py", [])]


def pdf_interpreter(env):
    return os.path.join(env.root, VENV_PDF)


def script_interpreter(env, script):
    """这几个脚本要 pymupdf，服务自己的解释器里没有：不换就死在脚本自己的守卫上。"""
    if script in VENV_PDF_SCRIPTS:
        return pdf_interpreter(env)
    return sys.executable


def make_cmd(env, name, script, args, accepts_root=False, marker=None, capture=None):
    path = os.path.join(env.scripts, script)
    argv = [script_interpreter(env, script), path] + list(args)
    if accepts_root:
        argv += ["--root", env.root]
    return {"name": name, "script": script, "argv": argv, "marker": marker,
            "capture": capture}


def retarget(env, cmd):
    """重跑时按当前根重算解释器：旧 commands.json 可能留着跑不了 PDF 的那个。"""
    argv = list(cmd.get("argv") or [])
    if argv:
        argv[0] = script_interpreter(env, cmd.get("script") or "")
    return dict(cmd, argv=argv)


class Runner:
    """写任务互斥于批次账读改写（.writer.lock 独占）；只读任务共用 .reader.lock，
    与写任务互不阻塞，最多 READ_SLOTS 个并行。"""

    def __init__(self, env, store):
        self.env = env
        self.store = store
        self.cv = threading.Condition()
        self.active = {}
        self.active_read = 0
        self.active_write = 0
        self.procs = {}
        self.cancels = set()
        self.stopping = False
        self.search_proc = None
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- public ----------------------------------------------------
    def submit(self, kind, title, commands, batch_dir=None, plan_hash=None):
        """commands 可以是列表，也可以是吃 job_id 出列表的函数：路径要带 job id。"""
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        job_dir = self.env.job_dir(job_id)
        os.makedirs(job_dir, exist_ok=True)
        if callable(commands):
            commands = commands(job_id)
        write_json_atomic(os.path.join(job_dir, "commands.json"), commands)
        row = {"id": job_id, "kind": kind, "title": title,
               "argv": json.dumps(commands[0]["argv"], ensure_ascii=False)
               if commands else "[]",
               "state": "queued", "created": now_iso(),
               "log_path": self.env.rel(os.path.join(job_dir, "log.txt")),
               "batch_dir": batch_dir, "plan_hash": plan_hash}
        self.store.create(row)
        for seq, cmd in enumerate(commands):
            self.store.add_step(job_id, seq, cmd["name"], "pending")
        with self.cv:
            self.cv.notify_all()
        return job_id

    def snapshot(self):
        with self.cv:
            return {"writer": bool(self.active_write),
                    "readers": int(self.active_read),
                    "active": sorted(self.active)}

    def cancel(self, job_id):
        job = self.store.get(job_id)
        if not job or job["state"] in DONE_STATES:
            return False
        self.cancels.add(job_id)
        pid = self.procs[job_id].pid if job_id in self.procs else job.get("pid")
        if not pid:
            self.store.update(job_id, state="cancelled", summary="已取消（还没起进程）",
                              ended=now_iso())
            self.cancels.discard(job_id)
            return True
        threading.Thread(target=self._terminate, args=(job_id, int(pid)),
                         daemon=True).start()
        return True

    def adopt(self):
        """服务重启后核对 running 任务：对得上就接管，对不上标孤儿，不杀进程。"""
        for row in self.store.list(state="running", limit=200):
            argv = []
            try:
                argv = json.loads(row.get("argv") or "[]")
            except ValueError:
                argv = []
            pid = row.get("pid")
            if pid and pid_alive(pid) and self._matches(int(pid), argv):
                spec = KINDS.get(row["kind"]) or {}
                with self.cv:
                    self.active[row["id"]] = row["kind"]
                    if spec.get("readonly"):
                        self.active_read += 1
                    else:
                        self.active_write += 1
                    self.cv.notify_all()
                self.store.update(row["id"], summary="接管：进程仍在跑")
                threading.Thread(target=self._watch_adopted, args=(row["id"], int(pid)),
                                 daemon=True).start()
            else:
                self.store.update(row["id"], state="orphaned", ended=now_iso(),
                                  summary="服务重启，PID 核对不上，已标孤儿")

    # ---- internals -------------------------------------------------
    @staticmethod
    def _lock_file(env, spec):
        """读任务锁 .reader.lock（与写互不阻塞），写任务与批次账读改写锁 .writer.lock。"""
        return env.reader_lock if spec.get("readonly") else env.lock

    @staticmethod
    def _matches(pid, argv):
        parts = [os.path.basename(p) for p in cmdline_of(pid)]
        if not argv:
            return False
        script = os.path.basename(argv[1]) if len(argv) > 1 else os.path.basename(argv[0])
        return script in parts

    def _loop(self):
        while not self.stopping:
            picked = None
            try:
                with self.cv:
                    for row in self.store.queued():
                        if row["id"] in self.active:
                            continue
                        spec = KINDS.get(row["kind"]) or {}
                        if spec.get("readonly"):
                            if self.active_read >= READ_SLOTS:
                                continue
                            self.active_read += 1
                        else:
                            if self.active_write:
                                continue
                            self.active_write += 1
                        self.active[row["id"]] = row["kind"]
                        picked = row
                        break
                    if picked is None:
                        self.cv.wait(0.4)
                        continue
            except Exception:  # noqa: BLE001
                # 调度线程死了 = 队列永久停摆（ops.db 被外部锁住等）。吞掉记日志，
                # 缓一缓再回来；HTTP 侧还能照常受理与查询。
                traceback.print_exc()
                if self.stopping:
                    break
                time.sleep(1.0)
                continue
            threading.Thread(target=self._run, args=(picked,), daemon=True).start()

    def _release(self, job_id, spec):
        with self.cv:
            self.active.pop(job_id, None)
            if spec.get("readonly"):
                self.active_read = max(0, self.active_read - 1)
            else:
                self.active_write = max(0, self.active_write - 1)
            self.cv.notify_all()

    def _run(self, job):
        job_id = job["id"]
        spec = KINDS.get(job["kind"]) or {"readonly": False, "gate": gate_rc}
        lock_fd = None
        try:
            commands = read_json(os.path.join(self.env.job_dir(job_id), "commands.json")) or []
            lock_fd = os.open(self._lock_file(self.env, spec),
                              os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(lock_fd, fcntl.LOCK_SH if spec.get("readonly")
                        else fcntl.LOCK_EX)
            if job_id in self.cancels:
                self.store.update(job_id, state="cancelled", ended=now_iso(),
                                  summary="已取消（拿锁前）")
                self.cancels.discard(job_id)
                return
            self.store.update(job_id, state="running", started=now_iso(), summary="")
            self._log(job_id, "=== %s %s %s\n" % (job_id, job["kind"], job["title"]),
                      truncate=True)
            rcs = self._play(job_id, commands)
            self._finalize(job_id, job, commands, rcs, spec=spec)
        except Exception:  # noqa: BLE001
            detail = traceback.format_exc(limit=4)
            self._log(job_id, "[服务异常]\n" + detail + "\n")
            self.store.update(job_id, state="failed", ended=now_iso(),
                              summary="服务端异常：" + detail.strip().splitlines()[-1][:160])
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                except OSError:
                    pass
            self.procs.pop(job_id, None)
            self._release(job_id, spec)
            try:
                os.remove(os.path.join(self.env.job_dir(job_id), "pid"))
            except OSError:
                pass

    def _play(self, job_id, commands, start=0, rcs=None):
        """从 start 开始按顺序跑命令，逐步记状态；rcs 里是前面几步已经知道的退出码。"""
        rcs = list(rcs or [])
        for seq in range(start, len(commands)):
            cmd = commands[seq]
            self.store.add_step(job_id, seq, cmd["name"], "running")
            self.store.update(job_id, argv=json.dumps(cmd["argv"], ensure_ascii=False))
            rc = self._spawn(job_id, cmd)
            rcs.append((cmd["name"], rc))
            self.store.step_state(job_id, seq, "ok" if rc == 0 else "fail", "rc=%d" % rc)
            if cmd.get("capture"):
                self._capture(job_id, cmd["capture"])
            if job_id in self.cancels:
                break
            if rc != 0:
                break
        return rcs

    def _log(self, job_id, text, truncate=False):
        path = os.path.join(self.env.job_dir(job_id), "log.txt")
        with open(path, "wb" if truncate else "ab") as fh:
            fh.write(text.encode("utf-8", "replace"))

    def _spawn(self, job_id, cmd):
        argv = cmd["argv"]
        self._log(job_id, "\n---- step %s ----\n$ %s\n" % (cmd["name"],
                                                          " ".join(argv)))
        if not os.path.exists(argv[0]):
            self._log(job_id, "[缺] 解释器不存在：%s\n" % argv[0])
            return 127
        if not os.path.exists(argv[1]):
            self._log(job_id, "[缺] 脚本不存在：%s\n" % argv[1])
            return 127
        path = os.path.join(self.env.job_dir(job_id), "log.txt")
        with open(path, "ab") as fh:
            proc = subprocess.Popen(argv, cwd=self.env.root, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        self.procs[job_id] = proc
        self.store.update(job_id, pid=proc.pid)
        write_text_atomic(os.path.join(self.env.job_dir(job_id), "pid"),
                          "%d\n" % proc.pid)
        return proc.wait()

    def _capture(self, job_id, name):
        text = tail_bytes(os.path.join(self.env.job_dir(job_id), "log.txt"))
        blob = last_json_blob(text)
        if blob is not None:
            write_json_atomic(os.path.join(self.env.job_dir(job_id), name), blob)

    def _finalize(self, job_id, job, commands, rcs, spec=None):
        spec = spec or KINDS.get(job["kind"]) or {"gate": gate_rc}
        if job_id in self.cancels:
            self.store.update(job_id, state="cancelled", ended=now_iso(),
                              summary="已取消")
            self.cancels.discard(job_id)
            return
        ctx = Ctx(self.env, job, commands, rcs)
        try:
            state, summary = spec["gate"](ctx)
        except Exception:  # noqa: BLE001
            state, summary = "failed", "门槛判定出错：" + traceback.format_exc(
                limit=2).strip().splitlines()[-1][:160]
        unknown = [name for name, rc in rcs if rc is None]
        if unknown:
            summary += "；接管：%s 的退出码拿不到" % "、".join(unknown)
            if state == "succeeded":
                state = "awaiting_review"
        self.store.update(job_id, state=state, ended=now_iso(), summary=summary,
                          exit_code=(rcs[-1][1] if rcs else None))
        self._log(job_id, "\n---- 判定 %s：%s ----\n" % (state, summary))
        if job["kind"] == "maintain" and state in ("succeeded", "awaiting_review"):
            threading.Thread(target=self._refresh_audit, args=(job_id,),
                             daemon=True).start()

    def _refresh_audit(self, job_id):
        """收尾跑完就重写体检缓存。页面关着也照样刷新，下次打开不再显示跑之前的读数。"""
        argv = [sys.executable, self.env.status_script, "--json", "--deep",
                "--root", self.env.root]
        try:
            proc = subprocess.run(argv, cwd=self.env.root, capture_output=True,
                                  text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(job_id, "[体检缓存] 刷新失败：%s\n" % exc)
            return
        if proc.returncode != 0:
            self._log(job_id, "[体检缓存] ops_status rc=%d：%s\n"
                      % (proc.returncode, (proc.stderr or proc.stdout).strip()[-200:]))

    def _watch_adopted(self, job_id, pid):
        """等接管来的进程退出，再把剩下的步骤跑完，最后按产物收尾。"""
        row = self.store.get(job_id) or {}
        spec = KINDS.get(row.get("kind")) or {}
        try:
            while pid_alive(pid):
                if job_id in self.cancels:
                    self._terminate(job_id, pid)
                time.sleep(1.0)
            self.resume(job_id, spec)
        except Exception:  # noqa: BLE001
            detail = traceback.format_exc(limit=4)
            self._log(job_id, "[接管异常]\n" + detail + "\n")
            self.store.update(job_id, state="failed", ended=now_iso(),
                              summary="接管后收尾失败："
                                      + detail.strip().splitlines()[-1][:160])
        finally:
            self._release(job_id, spec)
            try:
                os.remove(os.path.join(self.env.job_dir(job_id), "pid"))
            except OSError:
                pass

    def resume(self, job_id, spec):
        """接管后的续跑：跑到一半的那一步没人知道退出码，记 unknown；剩下的命令接着跑。"""
        job = self.store.get(job_id)
        if not job:
            return
        lock_fd = None
        try:
            commands = read_json(os.path.join(self.env.job_dir(job_id),
                                              "commands.json")) or []
            lock_fd = os.open(self._lock_file(self.env, spec),
                              os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(lock_fd, fcntl.LOCK_SH if spec.get("readonly")
                        else fcntl.LOCK_EX)
            rcs, start = self._adopted_rcs(job_id)
            if job_id in self.cancels:
                self._finalize(job_id, job, commands, rcs, spec=spec or None)
                return
            if start < len(commands):
                self._log(job_id, "\n[接管] 服务重启过，剩下的 %d 步接着跑\n"
                          % (len(commands) - start))
            rcs = self._play(job_id, commands, start, rcs)
            self._finalize(job_id, job, commands, rcs, spec=spec or None)
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                except OSError:
                    pass

    def _adopted_rcs(self, job_id):
        """接管前那几步的结果从步骤表还原；跑到一半的那步记 None，没人知道它的退出码。"""
        rcs, start = [], 0
        for step in self.store.steps(job_id):
            if step["state"] in ("ok", "fail"):
                rc = 0 if step["state"] == "ok" else rc_of_detail(step.get("detail"))
                rcs.append((step["name"], rc))
                start = step["seq"] + 1
                continue
            if step["state"] == "running":
                self.store.step_state(job_id, step["seq"], "unknown",
                                      "接管：进程退出，退出码未知")
                rcs.append((step["name"], None))
                start = step["seq"] + 1
            break
        return rcs, start

    def _terminate(self, job_id, pid):
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
            try:
                pgid = os.getpgid(pid)
            except OSError:
                pgid = None
        for _ in range(KILL_WAIT * 4):
            if not pid_alive(pid):
                break
            time.sleep(0.25)
        if pgid is None:
            return
        # 组长退了不等于组空了：孙进程可能吞了 SIGTERM 还在写日志/烧额度。
        # 组里还有活口就补 SIGKILL；全退干净（ESRCH）就到此为止。
        try:
            os.killpg(pgid, 0)
        except OSError:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


class App:
    def __init__(self, env):
        self.env = env
        env.prepare()
        self.store = Store(env.ops_db)
        self.runner = Runner(env, self.store)
        self.status_cache = (0.0, None)
        self.status_lock = threading.Lock()
        self.pdf_page_cache = {}
        self.pdf_page_lock = threading.Lock()
        self.runner.adopt()

    # ---- status ----------------------------------------------------
    def status(self, refresh=False, deep=False):
        if deep:
            refresh = True
        with self.status_lock:
            at, value = self.status_cache
            if not refresh and value is not None and time.time() - at < STATUS_TTL:
                return value
            value = self._load_status(deep=deep)
            self.status_cache = (time.time(), value)
            return value

    def _load_status(self, deep=False):
        script = self.env.status_script
        source, payload, error = "missing", None, None
        if os.path.exists(script):
            argv = [sys.executable, script, "--json", "--root", self.env.root]
            if deep:
                argv.append("--deep")
            try:
                proc = subprocess.run(argv, cwd=self.env.root, capture_output=True,
                                      text=True, timeout=120)
                if proc.returncode == 0:
                    blob = last_json_blob(proc.stdout)
                    if isinstance(blob, dict):
                        payload, source = blob, "script"
                    else:
                        error = "ops_status.py 输出不是 JSON"
                else:
                    error = (proc.stderr or proc.stdout or "").strip()[-300:]
            except subprocess.TimeoutExpired:
                error = "ops_status.py 超时"
            except OSError as exc:
                error = "ops_status.py 起不来：%s" % exc
        fixture = os.environ.get("OPS_STATUS_FIXTURE")
        if payload is None and fixture:
            blob = read_json(fixture)
            if isinstance(blob, dict):
                payload, source = blob, "fixture"
            else:
                error = (error or "") + " fixture 读不出"
        if payload is None:
            payload = {"at": now_iso(), "books": 0, "md_done": 0,
                       "pending": {"pdf": 0, "txt": 0, "djvu": 0},
                       "inbox": {"files": 0, "items": []},
                       "ledger": {"rows": 0, "mtime": None},
                       "catalog": {"mtime": None, "unmatched": 0},
                       "index": {"built_at": None, "paras": 0, "chapters": 0,
                                 "books": 0, "lag_books": 0, "lag_chapters": 0},
                       "views": {"awards": 0, "topics": 0, "broken": 0},
                       "audit": {"fail": 0, "warn": 0, "items": []},
                       "ocr": {"remaining_books": 0, "remaining_pages": 0,
                               "quota": "20000/天"},
                       "jobs": {"running": 0, "awaiting_review": 0, "last": None}}
            error = error or "ops_status.py 未就绪，页面显示空状态"
        counts = self.store.counts()
        payload["jobs"] = {
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "awaiting_review": counts.get("awaiting_review", 0),
            "failed": counts.get("failed", 0),
            "last": (self.store.list(limit=1) or [None])[0],
        }
        snap = self.runner.snapshot()
        return {"ok": error is None, "source": source, "error": error,
                "status": payload, "ops": snap, "state_counts": counts,
                "at": now_iso()}

    # ---- search ----------------------------------------------------
    def search_health(self):
        out = {"ok": True, "running": False, "url": SEARCH_URL, "pid": None,
               "meta": None, "error": None}
        pid_file = read_json(self.env.search_pid)
        if isinstance(pid_file, dict):
            out["pid"] = pid_file.get("pid")
        try:
            with urllib.request.urlopen(SEARCH_URL + "/api/health", timeout=1.5) as r:
                blob = json.loads(r.read().decode("utf-8"))
            out["running"] = True
            out["meta"] = blob.get("meta")
        except (urllib.error.URLError, ValueError, OSError) as exc:
            out["error"] = "%s" % getattr(exc, "reason", exc)
        return out

    def search_start(self):
        health = self.search_health()
        if health["running"]:
            return {"ok": True, "already": True, "health": health}
        script = os.path.join(self.env.root, "工作区", "演示-检索流程", "server.py")
        if not os.path.exists(script):
            return {"ok": False, "error": "演示页脚本不存在：%s" % self.env.rel(script)}
        os.makedirs(self.env.ops, exist_ok=True)
        with open(self.env.search_log, "ab") as fh:
            fh.write(("\n=== %s 启动 8765 ===\n" % now_iso()).encode("utf-8"))
            proc = subprocess.Popen([sys.executable, script], cwd=self.env.root,
                                    stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        write_json_atomic(self.env.search_pid,
                          {"pid": proc.pid, "at": now_iso(), "argv": [sys.executable, script]})
        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(0.3)
            if self.search_health()["running"]:
                return {"ok": True, "started": True, "pid": proc.pid}
        return {"ok": False, "error": "起来了但 8765 健康检查还没通，看 search.log", "pid": proc.pid}

    # ---- read-only data --------------------------------------------
    def db_rows(self, sql, args=()):
        path = self.env.libdb
        if not os.path.exists(path):
            raise FileNotFoundError("library.db 不存在")
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def pending(self):
        rows = self.db_rows(
            "SELECT p.uid, p.title, p.subject1, p.subject2, p.format, s.bytes,"
            " s.file, b.path, b.series, b.volume, b.author"
            " FROM v_pending_md p JOIN book b ON b.uid = p.uid"
            " LEFT JOIN source s ON s.uid = p.uid AND s.primary_src = 1"
            " ORDER BY p.uid")
        return rows

    def inbox(self):
        out = []
        if os.path.isdir(self.env.inbox):
            for name in sorted(os.listdir(self.env.inbox)):
                path = os.path.join(self.env.inbox, name)
                if not os.path.isfile(path) or name.startswith(".") or name.endswith(".part"):
                    continue
                st = os.stat(path)
                out.append({"name": name, "bytes": st.st_size,
                            "format": os.path.splitext(name)[1].lstrip(".").lower(),
                            "mtime": datetime.fromtimestamp(
                                st.st_mtime).astimezone().isoformat(timespec="seconds")})
        return out

    def split_books(self, batch):
        if not batch:
            return []
        batch_dir = os.path.join(self.env.batches, batch)
        logs = os.path.join(batch_dir, "logs")
        out = []
        if not os.path.isdir(logs):
            return out
        for name in sorted(os.listdir(logs)):
            if not name.endswith(".json"):
                continue
            row = read_json(os.path.join(logs, name))
            if not isinstance(row, dict):
                continue
            out.append({"uid": row.get("uid") or name[:-5], "title": row.get("title"),
                        "status": row.get("status"), "verify_rc": row.get("verify_rc"),
                        "audit_rc": row.get("audit_rc"),
                        "audit_ratio": row.get("audit_ratio"),
                        "chapters": row.get("chapters"), "images": row.get("images"),
                        "fix": len(row.get("fix") or []) if isinstance(row.get("fix"),
                                                                      list) else row.get("fix"),
                        "dest": row.get("dest"), "previous": row.get("previous"),
                        "note": row.get("note")})
        return out

    def pdf_queue(self):
        triage = read_table(self.env.root, os.path.join(TRIAGE_PDF_DIR,
                                                        "triage_final.csv"), ",")
        skip = read_table(self.env.root, os.path.join(TRIAGE_PDF_DIR, "skip_list.tsv"))
        defer = read_table(self.env.root, os.path.join(OCR_BATCH, "defer", "lang.tsv"))
        work = read_table(self.env.root, os.path.join(OCR_BATCH, "worklist.tsv"))
        logs_dir = os.path.join(self.env.root, OCR_BATCH, "logs")
        done = {}
        if os.path.isdir(logs_dir):
            for name in os.listdir(logs_dir):
                if name.endswith(".json"):
                    row = read_json(os.path.join(logs_dir, name)) or {}
                    done[name[:-5]] = row.get("status")
        groups = collections.OrderedDict(
            (k, {"route": k, "books": 0, "pages": 0, "uids": []})
            for k in ("OCR", "本地抽文本", "不转-记图册", "人工确认", "日文暂缓"))
        for row in triage:
            route = (row.get("route") or "").strip()
            if not route:
                continue
            key = route if route in groups else "人工确认"
            groups[key]["books"] += 1
            groups[key]["pages"] += int(float(row.get("pages") or 0))
            groups[key]["uids"].append(row.get("uid"))
        ja_rows = [r for r in defer if (r.get("lang") or "").strip() == "ja"]
        deferred = {r.get("uid") for r in ja_rows}
        groups["日文暂缓"] = {
            "route": "日文暂缓", "books": len(ja_rows),
            "pages": sum(int(float(r.get("pages") or 0)) for r in ja_rows),
            "uids": [r.get("uid") for r in ja_rows]}
        batch_landed = sum(1 for uid, status in done.items()
                           if status in ("landed", "skipped-has-md", "accepted"))
        remaining = [r for r in work if r.get("uid") not in done]
        counts = collections.Counter(s for s in done.values() if s)
        landed = sum(n for status, n in counts.items()
                     if status in ("landed", "skipped-has-md", "accepted", "replaced"))
        failed = sum(n for status, n in counts.items()
                     if status in ("api-fatal", "failed"))
        staged = counts.get("staged-review", 0)
        untouched = max(0, len(work) - landed - failed - staged)
        batch = {"name": os.path.basename(OCR_BATCH), "readonly": True,
                 "worklist": len(work), "landed": batch_landed,
                 "remaining_books": len(remaining),
                 "remaining_pages": sum(int(float(r.get("pages") or 0))
                                        for r in remaining),
                 "quota": "20000/天",
                 "partition": {"landed": landed, "failed": failed, "staged": staged,
                               "untouched": untouched,
                               "left": failed + staged + untouched},
                 "status_counts": counts}
        return {"groups": list(groups.values()), "skip": len(skip),
                "deferred": len(deferred), "batch": batch,
                "triage_path": os.path.join(TRIAGE_PDF_DIR, "triage_final.csv")}

    def pdf_batches(self):
        """后建的批次盖前面的那一份。"""
        root = self.env.batches
        if not os.path.isdir(root):
            return []
        return [os.path.join("工作区", "批次", name) for name in sorted(os.listdir(root))
                if name.startswith(TRIAGE_BATCH_PREFIX)
                and os.path.isdir(os.path.join(root, name))]

    def pdf_last_status(self):
        out = {}
        for rel in [OCR_BATCH] + self.pdf_batches():
            logs = os.path.join(self.env.root, rel, "logs")
            if not os.path.isdir(logs):
                continue
            for name in sorted(os.listdir(logs)):
                if not name.endswith(".json"):
                    continue
                row = read_json(os.path.join(logs, name)) or {}
                out[row.get("uid") or name[:-5]] = row.get("status")
        return out

    def job_argvs(self, job_id):
        commands = read_json(os.path.join(self.env.job_dir(job_id), "commands.json")) or []
        argvs = [c.get("argv") or [] for c in commands if isinstance(c, dict)]
        row = self.store.get(job_id) or {}
        if row.get("argv"):
            try:
                argvs.append(json.loads(row["argv"]))
            except ValueError:
                pass
        return argvs

    def pdf_running(self):
        out = set()
        for row in self.store.list(limit=500):
            if row.get("kind") != "pdf.run" or row.get("state") not in ("queued", "running"):
                continue
            for argv in self.job_argvs(row["id"]):
                for seq, part in enumerate(argv):
                    if part in ("--uids", "--only") and seq + 1 < len(argv):
                        out.update(u.strip() for u in str(argv[seq + 1]).split(",") if u.strip())
        return out

    def pdf_probe(self, items):
        out, todo = {}, []
        with self.pdf_page_lock:
            for uid, rel in items:
                try:
                    st = os.stat(os.path.join(self.env.root, rel) if rel else "")
                except OSError:
                    continue
                stamp = (st.st_mtime_ns, st.st_size)
                hit = self.pdf_page_cache.get(uid)
                if hit and hit[0] == stamp:
                    out[uid] = {"pages": hit[1], "text": hit[2]}
                else:
                    todo.append((uid, rel, stamp))
        interpreter = pdf_interpreter(self.env)
        if not todo or not os.path.isfile(interpreter):
            return out
        payload = json.dumps([{"uid": uid, "path": rel} for uid, rel, _stamp in todo],
                             ensure_ascii=False)
        try:
            proc = subprocess.run([interpreter, "-c", PDF_PAGE_SCRIPT], input=payload,
                                  cwd=self.env.root, capture_output=True, text=True,
                                  timeout=30)
        except (OSError, subprocess.SubprocessError):
            return out
        blob = {}
        tail = (proc.stdout or "").strip().splitlines()
        if proc.returncode == 0 and tail:
            try:
                blob = json.loads(tail[-1])
            except ValueError:
                blob = {}
        if not isinstance(blob, dict):
            blob = {}
        with self.pdf_page_lock:
            for uid, _rel, stamp in todo:
                item = blob.get(uid)
                if not isinstance(item, dict):
                    continue
                try:
                    pages = int(item["pages"])
                except (KeyError, TypeError, ValueError):
                    continue
                text = 1 if item.get("text") else 0
                self.pdf_page_cache[uid] = (stamp, pages, text)
                out[uid] = {"pages": pages, "text": text}
        return out

    def pdf_books_index(self):
        try:
            pending = self.db_rows(PENDING_PDF_SQL)
            landed = {r["uid"] for r in self.db_rows(
                "SELECT uid FROM derived WHERE kind='md' AND status='done'")}
        except FileNotFoundError:
            return {}
        pages, route = {}, {}
        for row in read_table(self.env.root, os.path.join(TRIAGE_PDF_DIR,
                                                         "triage_final.csv"), ","):
            uid = row.get("uid")
            if uid:
                pages[uid] = row.get("pages")
        for row in (read_table(self.env.root, PDF_WORKLIST)
                    + read_table(self.env.root, PDF_SKIP_LIST)
                    + read_table(self.env.root, PDF_DEFER)):
            uid = row.get("uid")
            if uid and not pages.get(uid):
                pages[uid] = row.get("pages")
        for rel in self.pdf_batches():
            for row in read_table(self.env.root, os.path.join(rel, "triage_final.csv"), ","):
                uid = row.get("uid")
                if not uid:
                    continue
                route[uid] = row.get("route") or ""
                if row.get("pages"):
                    pages[uid] = row.get("pages")
        skipped = {r["uid"]: r for r in read_table(self.env.root, PDF_SKIP_LIST)
                   if r.get("uid")}
        japanese = {r.get("uid") for r in read_table(self.env.root, PDF_DEFER)
                    if (r.get("lang") or "").strip() == "ja"}
        last = self.pdf_last_status()
        running = self.pdf_running()
        books = {}
        for row in pending:
            uid = row["uid"]
            if route.get(uid) in PDF_CONVERT_ROUTES:
                disposition = "convert"
            elif route.get(uid) == "不转-记图册" or uid in skipped:
                disposition = "gallery"
            elif route.get(uid) == "日文暂缓" or uid in japanese:
                disposition = "ja"
            else:
                disposition = "untriaged"
            if uid in landed:
                progress = "landed"
            elif uid in running:
                progress = "running"
            elif last.get(uid) in ("failed", "api-fatal", "staged-review"):
                progress = "failed"
            else:
                progress = "todo"
            try:
                count = int(float(pages.get(uid) or 0)) or None
            except (TypeError, ValueError):
                count = None
            books[uid] = {
                "uid": uid, "title": row.get("title"),
                "subject1": row.get("subject1") or "",
                "subject2": row.get("subject2") or "",
                "pages": count, "disposition": disposition,
                "route": route.get(uid) if disposition == "convert" else None,
                "progress": progress,
                "recheck": disposition == "gallery" and recheck_row(skipped.get(uid)),
                "why": (skipped.get(uid) or {}).get("why") if disposition == "gallery" else None,
                "last": last.get(uid)}
        todo = [(row["uid"], row.get("file") or "") for row in pending
                if books[row["uid"]]["pages"] is None
                or books[row["uid"]]["disposition"] == "untriaged"]
        probe = self.pdf_probe(todo)
        for uid, info in probe.items():
            if uid not in books:
                continue
            if books[uid]["pages"] is None:
                books[uid]["pages"] = info["pages"]
            books[uid]["text_layer"] = (info["text"] == 1) if info["text"] is not None else None
        suggestions = self.pdf_suggestions(
            [uid for uid, row in books.items() if row["disposition"] == "untriaged"])
        for uid, row in books.items():
            row.setdefault("text_layer", None)
            hit = suggestions.get(uid)
            row["suggestion"] = ({"route": hit["route"], "confidence": hit["confidence"],
                                  "reason": hit["reason"]} if hit else None)
            row["suggest_job"] = hit["job"] if hit else None
        return dict(sorted(books.items()))

    def pdf_today(self):
        """skipped-has-md 的书没走 OCR，不计入当天额度。"""
        logs = os.path.join(self.env.batches,
                            TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d"), "logs")
        used = 0
        if os.path.isdir(logs):
            for name in os.listdir(logs):
                if not name.endswith(".json"):
                    continue
                row = read_json(os.path.join(logs, name)) or {}
                if row.get("status") == "skipped-has-md":
                    continue
                try:
                    used += int(float(row.get("pages") or 0))
                except (TypeError, ValueError):
                    pass
        return {"used": used, "cap": PDF_DAILY_CAP}

    def pdf_triage_jobs(self):
        """全部分诊任务，新的在前。不按状态筛：结果落盘就属于那本书，任务后来

        失败、被取消、还在跑，都不该让已经出来的建议和分诊行消失。
        同一秒建的任务按 id 排是随机的，rowid 才是真的先后。
        """
        jobs = [row for row in self.store.list(limit=500)
                if row.get("kind") == "pdf.triage"]
        return sorted(jobs, key=lambda row: (row.get("seq") or 0,
                                             row.get("created") or ""),
                      reverse=True)

    def pdf_suggestions(self, uids):
        out, want = {}, set(uids)
        for job in self.pdf_triage_jobs():
            left = want - set(out)
            if not left:
                break
            job_dir = self.env.job_dir(job["id"])
            results = {}
            single = read_json(os.path.join(job_dir, "result.json"))
            if isinstance(single, dict) and single.get("uid"):
                results[single["uid"]] = single
            try:
                names = os.listdir(job_dir)
            except OSError:
                names = []
            for name in names:
                if name not in left:
                    continue
                batch_one = read_json(os.path.join(job_dir, name, "result.json"))
                if isinstance(batch_one, dict) and batch_one.get("uid") == name:
                    results[name] = batch_one
            for uid, result in results.items():
                if uid not in left:
                    continue
                model = result.get("model") or {}
                route = model.get("route")
                if route not in PDF_SETTLE_ROUTES:
                    continue
                out[uid] = {"route": route, "confidence": model.get("confidence"),
                            "reason": model.get("reason") or "", "job": job["id"]}
        return out

    def pdf_triage_row(self, uid, index):
        for job in self.pdf_triage_jobs():
            for rel in (os.path.join("jobs", job["id"], uid, "triage.csv"),
                        os.path.join("jobs", job["id"], "triage.csv")):
                for row in read_table(self.env.ops, rel, ","):
                    if row.get("uid") == uid:
                        return row, (row.get("verdict") or "").strip()
        sources = [os.path.join(rel, "triage_final.csv")
                   for rel in reversed(self.pdf_batches())]
        sources.append(os.path.join(TRIAGE_PDF_DIR, "triage_final.csv"))
        for rel in sources:
            for row in read_table(self.env.root, rel, ","):
                if row.get("uid") == uid:
                    return row, (row.get("verdict") or "").strip()
        work = {r.get("uid"): r for r in read_table(self.env.root, PDF_WORKLIST)}
        book = index.get(uid) or {}
        return {"uid": uid, "title": book.get("title") or "",
                "subject1": book.get("subject1") or "",
                "subject2": book.get("subject2") or "",
                "class": (work.get(uid) or {}).get("class") or "", "route": "",
                "pages": "" if book.get("pages") is None else book["pages"],
                "mb": "", "note": "", "verdict": ""}, ""

    def pdf_settle(self, items, index):
        batch = TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d")
        batch_dir = os.path.join(self.env.batches, batch)
        os.makedirs(batch_dir, exist_ok=True)
        review_dir = os.path.join(batch_dir, "review")
        batch_triage = os.path.join(batch_dir, "triage.csv")
        for item in items:
            uid, route = item["uid"], item["route"]
            row, verdict = self.pdf_triage_row(uid, index)
            row["route"] = route
            upsert_csv(batch_triage, row, list(row.keys()))
            suggestion = item.get("suggestion") or {}
            review = {"uid": uid, "verdict": item.get("verdict") or verdict or route,
                      "evidence": "", "route": route,
                      "reason": item.get("reason") or "在 PDF 清单里改处置",
                      "model": ({"route": suggestion.get("route"),
                                 "confidence": suggestion.get("confidence"),
                                 "reason": suggestion.get("reason") or "",
                                 "at": now_iso()} if suggestion.get("route") else None),
                      "at": now_iso()}
            write_json_atomic(os.path.join(review_dir, uid + ".json"), review)
        command = make_cmd(self.env, "apply_review", "pdf_apply_review.py",
                           ["--batch", batch,
                            "--triage", self.env.rel(batch_triage),
                            "--out-dir", self.env.rel(batch_dir),
                            "--review", self.env.rel(review_dir)])
        return batch, command

    def retire_search(self, query):
        like = "%%%s%%" % query
        rows = self.db_rows(
            "SELECT uid, title, author, path, subject1, subject2, series, volume,"
            " retention FROM book WHERE uid=? OR title LIKE ? OR author LIKE ?"
            " ORDER BY uid LIMIT 30", (query, like, like))
        for row in rows:
            row["sources"] = self.db_rows(
                "SELECT file, format, bytes, sha256, primary_src FROM source"
                " WHERE uid=?", (row["uid"],))
            md = self.db_rows("SELECT chapters, bytes, status FROM derived"
                              " WHERE uid=? AND kind='md'", (row["uid"],))
            row["md"] = md[0] if md else None
            row["awards"] = self.db_rows(
                "SELECT award, year, result FROM book_award WHERE uid=?", (row["uid"],))
            row["retired"] = self._retired(row["uid"])
        return rows

    def _retired(self, uid):
        batch_root = os.path.join(self.env.batches)
        if not os.path.isdir(batch_root):
            return False
        for name in sorted(os.listdir(batch_root), reverse=True):
            path = os.path.join(batch_root, name, "retired.tsv")
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        if any(line.split("\t")[1:2] == [uid] for line in fh):
                            return True
                except OSError:
                    continue
        return False

    # ---- ops.db maintenance ----------------------------------------
    def clean(self, keep_days=90, keep_jobs=200, thumb_days=30):
        jobs = self.store.list(limit=10000)
        keep = set(j["id"] for j in jobs[:keep_jobs])
        cutoff = time.time() - keep_days * 86400
        removed = []
        for job in jobs:
            if job["state"] not in DONE_STATES:
                continue
            try:
                ended = os.path.getmtime(os.path.join(self.env.job_dir(job["id"]),
                                                      "log.txt"))
            except OSError:
                ended = 0
            stale = bool(ended) and ended <= cutoff
            # 90 天或 200 个任务，先到为准：最新 200 个之外的一律清，
            # 之内的也要够老才清（不是无条件保 200 个）
            if job["id"] in keep and not stale:
                continue
            job_dir = self.env.job_dir(job["id"])
            thresh = time.time() - thumb_days * 86400
            if self._has_fresh_thumbs(job_dir, thresh):
                # 缩略图还在保留期：整本任务跳过，行与建议都留着，过期后再清
                continue
            shutil.rmtree(job_dir, ignore_errors=True)
            self.store.delete(job["id"])
            removed.append(job["id"])
        return {"removed": len(removed), "ids": removed[:20]}

    @staticmethod
    def _has_fresh_thumbs(job_dir, thresh):
        thumbs = os.path.join(job_dir, "thumbs")
        try:
            for name in os.listdir(thumbs):
                if os.path.getmtime(os.path.join(thumbs, name)) > thresh:
                    return True
        except OSError:
            pass
        return False


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


FILE_ALLOW = (
    re.compile(r"^工作区/运维/jobs/[A-Za-z0-9._-]+/(log\.txt|plan\.json|commands\.json|"
               r"result\.json|review\.json|dry-run\.json|report\.json|status\.json)$"),
    re.compile(r"^工作区/运维/jobs/[A-Za-z0-9._-]+/review/[A-Za-z0-9._-]+\.json$"),
    re.compile(r"^工作区/运维/jobs/[A-Za-z0-9._-]+/thumbs/[A-Za-z0-9._-]+\.png$"),
    re.compile(r"^目录/(书目总表\.xlsx|书目总表\.csv|分类预填\.csv)$"),
)
CONTENT_TYPES = {".json": "application/json; charset=utf-8",
                 ".txt": "text/plain; charset=utf-8",
                 ".csv": "text/csv; charset=utf-8",
                 ".png": "image/png",
                 ".xlsx": "application/vnd.openxmlformats-officedocument."
                          "spreadsheetml.sheet"}
# 这些走 /api/file 时按文本解码头再脱敏；png/xlsx 是二进制，原样下发
TEXT_DOWNLOAD_EXTS = {".txt", ".json", ".csv", ".tsv", ".log", ".md"}


class Handler(BaseHTTPRequestHandler):
    server_version = "BookOps/1.0"
    protocol_version = "HTTP/1.1"
    app = None

    def log_message(self, fmt, *args):
        if os.environ.get("OPS_HTTP_LOG"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- plumbing --------------------------------------------------
    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if code >= 400:
            # 出错路径常常没读尽请求体（403 拒站外、413 超限、400 超大 JSON）。
            # HTTP/1.1 keep-alive 下残余 body 字节会被下一条请求当请求行解析，
            # 连接直接失步；出错一律断开，代价只是重连。
            self.close_connection = True
        if isinstance(body, (dict, list)):
            body = json_bytes(body)
        elif isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            if code >= 400:
                self.send_header("Connection", "close")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        except (UnicodeError, ValueError) as exc:
            # 头部写过一半就不补第二条响应，只能断开
            sys.stderr.write("响应头写失败：%s\n" % exc)
            self.close_connection = True

    def send_json(self, code, value):
        self.send(code, value)

    def fail(self, code, message):
        self.send_json(code, {"ok": False, "error": message})

    def static(self, name, ctype):
        path = os.path.join(WEB_DIR, name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.fail(404, "静态文件缺失：" + name)
            return
        self.send(200, body, ctype)

    def local_ok(self):
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        host_ok = bool(re.match(r"^(127\.0\.0\.1|localhost)(:\d+)?$", host))
        origin_ok = origin is None or bool(
            re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin))
        return host_ok and origin_ok

    def body_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > JSON_MAX:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    # ---- GET -------------------------------------------------------
    def do_GET(self):
        if not self.local_ok():
            # GET 也得校验：DNS rebinding 的页面一样能读 /api/jobs 与日志下载
            self.fail(403, "只接受本机页面的请求")
            return
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                self.static("index.html", "text/html; charset=utf-8")
            elif route == "/app.js":
                self.static("app.js", "application/javascript; charset=utf-8")
            elif route == "/style.css":
                self.static("style.css", "text/css; charset=utf-8")
            elif route == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
            elif route == "/api/status":
                refresh = (query.get("refresh") or ["0"])[0] not in ("0", "", "false")
                deep = (query.get("deep") or ["0"])[0] not in ("0", "", "false")
                self.send_json(200, self.app.status(refresh=refresh, deep=deep))
            elif route == "/api/jobs":
                self.api_jobs(query)
            elif re.match(r"^/api/jobs/[^/]+/log$", route):
                self.api_job_log(route.split("/")[3], query)
            elif re.match(r"^/api/jobs/[^/]+$", route):
                self.api_job(route.split("/")[3])
            elif route == "/api/inbox":
                self.send_json(200, {"ok": True, "items": self.app.inbox()})
            elif route == "/api/import/latest":
                self.api_import_latest()
            elif route == "/api/pending":
                self.send_json(200, {"ok": True, "items": self.app.pending()})
            elif route == "/api/split/books":
                batch = (query.get("batch") or [""])[0]
                self.send_json(200, {"ok": True, "batch": batch,
                                     "books": self.app.split_books(batch)})
            elif route == "/api/pdf/queue":
                self.send_json(200, dict(self.app.pdf_queue(), ok=True))
            elif route == "/api/pdf/books":
                books = self.app.pdf_books_index()
                counts = dict.fromkeys(PDF_DISPOSITIONS, 0)
                for row in books.values():
                    counts[row["disposition"]] += 1
                self.send_json(200, {"ok": True, "books": list(books.values()),
                                     "counts": counts,
                                     "today": self.app.pdf_today(),
                                     "untriaged": untriaged_split(books)})
            elif route == "/api/retire/search":
                q = (query.get("q") or [""])[0].strip()
                if not q:
                    self.fail(400, "q 为空")
                else:
                    self.send_json(200, {"ok": True, "books": self.app.retire_search(q)})
            elif route == "/api/search/health":
                self.send_json(200, self.app.search_health())
            elif route == "/api/file":
                self.api_file((query.get("path") or [""])[0])
            elif route.startswith("/api/"):
                self.fail(404, "没有这个接口")
            else:
                self.fail(404, "not found")
        except FileNotFoundError as exc:
            self.fail(404, str(exc))
        except ValueError as exc:
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))

    def api_jobs(self, query):
        state = (query.get("state") or [""])[0] or None
        if state and state not in STATES:
            self.fail(400, "state 不认识：%s" % state)
            return
        try:
            limit = int((query.get("limit") or ["200"])[0])
        except ValueError:
            limit = 200
        limit = max(1, min(limit, 1000))
        jobs = self.app.store.list(state=state, limit=limit)
        for job in jobs:
            job["steps"] = self.app.store.steps(job["id"])
        self.send_json(200, {"ok": True, "jobs": jobs,
                             "counts": self.app.store.counts()})

    def api_job(self, job_id):
        job = self.app.store.get(job_id)
        if not job:
            self.fail(404, "没有这个任务")
            return
        log_path = os.path.join(self.app.env.job_dir(job_id), "log.txt")
        payload = {"ok": True, "job": job, "steps": self.app.store.steps(job_id),
                   "log_tail": redact(tail_lines(tail_bytes(log_path))),
                   "commands": read_json(os.path.join(self.app.env.job_dir(job_id),
                                                      "commands.json")) or [],
                   "files": self.job_files(job_id)}
        self.send_json(200, payload)

    def job_files(self, job_id):
        out = {}
        for name in ("plan.json", "result.json", "review.json", "dry-run.json",
                     "report.json", "commands.json"):
            path = os.path.join(self.app.env.job_dir(job_id), name)
            if os.path.exists(path):
                out[name] = self.app.env.rel(path)
        return out

    def api_job_log(self, job_id, query):
        job = self.app.store.get(job_id)
        if not job:
            self.fail(404, "没有这个任务")
            return
        path = os.path.join(self.app.env.job_dir(job_id), "log.txt")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        try:
            offset = int((query.get("offset") or ["0"])[0])
        except ValueError:
            offset = 0
        offset = max(0, min(offset, size))
        text = ""
        consumed = 0
        if offset < size:
            with open(path, "rb") as fh:
                fh.seek(offset)
                raw = fh.read(400_000)
            # 结尾的不完整多字节序列退回给下一页，不变成 U+FFFD；
            # next_offset 按实际消费的字节算，页与页之间不丢不重
            cut = len(raw)
            while cut > 0:
                try:
                    text = raw[:cut].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    cut -= 1
            if cut == 0 and raw:
                # 段首就是坏字节：按 replace 吞掉一个字节，offset 不能原地踏步
                text = raw[:1].decode("utf-8", "replace")
                cut = 1
            consumed = cut
        next_offset = offset + consumed
        self.send_json(200, {"ok": True, "offset": offset, "next": next_offset,
                             "size": size, "eof": next_offset >= size,
                             "text": redact(text)})

    def api_file(self, relpath):
        if not relpath:
            self.fail(400, "path 为空")
            return
        rel = relpath.strip().lstrip("/")
        if not any(pat.match(rel) for pat in FILE_ALLOW):
            self.fail(404, "不在白名单")
            return
        root = os.path.realpath(self.app.env.root)
        path = os.path.realpath(os.path.join(root, rel))
        if not (path == root or path.startswith(root + os.sep)):
            self.fail(404, "路径越界")
            return
        if not os.path.isfile(path):
            self.fail(404, "文件不存在")
            return
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            self.fail(404, "读不出：%s" % exc)
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in TEXT_DOWNLOAD_EXTS:
            # 文本产物（日志/计划/结果）走下载口也要脱敏，与 /api/jobs/<id>/log 同口径
            body = redact(body.decode("utf-8", "replace")).encode("utf-8")
        extra = {"X-File-Name": quote(os.path.basename(path))}
        self.send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"), extra)

    # ---- POST ------------------------------------------------------
    def do_POST(self):
        if not self.local_ok():
            self.fail(403, "只接受本机页面的请求")
            return
        route = unquote(urlparse(self.path).path)
        try:
            if route == "/api/import/upload":
                self.api_upload()
                return
            payload = self.body_json()
            if route in WRITE_ROUTES and self.writer_busy():
                self.fail(409, "有写任务在跑，等它结束再发")
                return
            handler = {
                "/api/import/scan": lambda: self.api_import_scan(payload),
                "/api/import/plan": lambda: self.api_import_plan(payload),
                "/api/import/apply": lambda: self.api_import_apply(payload),
                "/api/split/run": lambda: self.api_split_run(payload),
                "/api/split/recheck": lambda: self.api_split_recheck(payload),
                "/api/split/inspect": lambda: self.api_split_inspect(payload),
                "/api/pdf/triage": lambda: self.api_pdf_triage(payload),
                "/api/pdf/triage-batch": lambda: self.api_pdf_triage_batch(payload),
                "/api/pdf/settle": lambda: self.api_pdf_settle(payload),
                "/api/pdf/confirm": lambda: self.api_pdf_confirm(payload),
                "/api/pdf/run": lambda: self.api_pdf_run(payload),
                "/api/maintain": lambda: self.api_maintain(payload),
                "/api/delete/dry-run": lambda: self.api_delete_dry(payload),
                "/api/delete/apply": lambda: self.api_delete_apply(payload),
                "/api/jobs/clean": lambda: self.api_clean(payload),
                "/api/search/start": lambda: self.send_json(200, self.app.search_start()),
            }.get(route)
            if handler:
                handler()
                return
            if re.match(r"^/api/jobs/[^/]+/cancel$", route):
                self.api_cancel(route.split("/")[3], payload)
                return
            if re.match(r"^/api/jobs/[^/]+/rerun$", route):
                self.api_rerun(route.split("/")[3])
                return
            self.fail(404, "没有这个接口")
        except ValueError as exc:
            self.fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.fail(500, "%s: %s" % (exc.__class__.__name__, exc))

    def writer_busy(self):
        snap = self.app.runner.snapshot()
        if snap["writer"]:
            return True
        # 只看排队中的「写」任务：只读任务排队不该让写按钮报「有写任务在跑」
        for row in self.app.store.queued(limit=200):
            if not (KINDS.get(row["kind"]) or {}).get("readonly"):
                return True
        return False

    def api_upload(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.fail(400, "空上传")
            return
        if length > UPLOAD_MAX:
            self.fail(413, "超过 2 GB 上限，用文件管理器放进收件箱")
            return
        raw = self.headers.get("X-Filename") or ""
        name = unquote(raw).replace("\\", "/").split("/")[-1].strip()
        if (not name or name in (".", "..") or name.startswith(".")
                or any(ch in name for ch in "\x00\r\n")):
            self.fail(400, "文件名不合法：%r" % raw)
            return
        if ".." in name:
            self.fail(400, "文件名不能含 ..")
            return
        os.makedirs(self.app.env.inbox, exist_ok=True)
        target = os.path.join(self.app.env.inbox, name)
        stem, ext = os.path.splitext(name)
        n = 2
        while os.path.exists(target):
            target = os.path.join(self.app.env.inbox, "%s-%d%s" % (stem, n, ext))
            n += 1
        part = "%s.%d.part" % (target, threading.get_ident())
        # 并发同名上传各有各的 .part，不会交错写同一个临时文件；最后 os.replace
        # 谁后到谁生效。中断残留的 .part 不进收件箱清单（见 inbox）。
        got = 0
        with open(part, "wb") as fh:
            while got < length:
                chunk = self.rfile.read(min(262_144, length - got))
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
        if got != length:
            try:
                os.remove(part)
            except OSError:
                pass
            self.fail(400, "上传中断：收到 %d / %d" % (got, length))
            return
        os.replace(part, target)
        self.send_json(200, {"ok": True, "name": os.path.basename(target),
                             "path": self.app.env.rel(target), "bytes": got})

    def api_import_scan(self, _payload=None):
        def build(job_id):
            out = os.path.join(self.app.env.rel(self.app.env.job_dir(job_id)),
                               "plan.json")
            return [make_cmd(self.app.env, "scan", "ingest.py",
                             ["scan", "--json", "--out", out], accepts_root=True)]
        job_id = self.app.runner.submit("import.scan", "扫收件箱", build)
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_import_plan(self, payload):
        job_id = str(payload.get("job_id") or "")
        if not re.match(r"^[A-Za-z0-9._-]+$", job_id):
            self.fail(400, "job_id 不合法")
            return
        job = self.app.store.get(job_id)
        if not job or job["kind"] != "import.scan":
            self.fail(404, "没有这个扫描任务")
            return
        items = payload.get("items")
        if not isinstance(items, list):
            self.fail(400, "items 必须是数组")
            return
        path = os.path.join(self.app.env.job_dir(job_id), "plan.json")
        plan = read_json(path)
        if not isinstance(plan, list):
            self.fail(409, "原计划不存在")
            return
        if len(items) != len(plan):
            self.fail(400, "计划行数与磁盘上的不一致")
            return
        merged = []
        for old, new in zip(plan, items):
            if not isinstance(new, dict):
                self.fail(400, "每行必须是对象")
                return
            row = dict(old)
            action = new.get("action", old.get("action"))
            if action not in ("add", "replace", "conflict", "skip"):
                self.fail(400, "动作不认识：%s" % action)
                return
            retention = new.get("retention", old.get("retention"))
            if retention not in ("keep", "review", "drop"):
                self.fail(400, "保留策略不认识：%s" % retention)
                return
            uid = str(new.get("uid", old.get("uid")) or "").strip()
            if uid and not re.match(r"^B\d{4}$", uid):
                self.fail(400, "uid 形如 B1234：%r" % uid)
                return
            subjects = new.get("subjects", old.get("subjects") or [])
            if isinstance(subjects, str):
                subjects = [s.strip() for s in re.split(r"[/,]", subjects) if s.strip()]
            if not isinstance(subjects, list):
                self.fail(400, "subjects 必须是数组或 一级/二级")
                return
            title = str(new.get("title", old.get("title")) or "").strip()
            if not title:
                self.fail(400, "书名不能空：%s" % (old.get("file") or ""))
                return
            row.update(action=action, retention=retention, uid=uid,
                       subjects=[str(x).strip() for x in subjects if str(x).strip()],
                       title=title)
            merged.append(row)
        write_json_atomic(path, merged)
        self.send_json(200, {"ok": True, "items": len(merged),
                             "plan_path": self.app.env.rel(path)})

    def api_import_latest(self):
        jobs = self.app.store.list(state="awaiting_review", limit=50)
        job = next((j for j in jobs if j.get("kind") == "import.scan"), None)
        plan = None
        if job is not None:
            plan = read_json(os.path.join(self.app.env.job_dir(job["id"]), "plan.json"))
        if not isinstance(plan, list):
            self.send_json(200, {"ok": True, "job_id": None, "plan": None})
            return
        root = self.app.env.root
        for item in plan:
            if item.get("action") not in ("add", "replace"):
                continue
            src = str(item.get("path") or "")
            if not src:
                self.send_json(200, {"ok": True, "job_id": None, "plan": None})
                return
            path = src if os.path.isabs(src) else os.path.join(root, src)
            if not os.path.isfile(path):
                self.send_json(200, {"ok": True, "job_id": None, "plan": None})
                return
        self.send_json(200, {"ok": True, "job_id": job["id"],
                             "created": job.get("created"), "plan": plan})

    def api_import_apply(self, payload):
        plan = plan_path(self.app.env, payload.get("plan_path"))
        commands = [make_cmd(self.app.env, "ingest apply", "ingest.py",
                             ["apply", plan, "--apply"], accepts_root=True)]
        commands += rebuild_commands(self.app.env)
        job_id = self.app.runner.submit(
            "import.apply", "导入入库 %s" % os.path.basename(os.path.dirname(plan)),
            commands, plan_hash=plan_file_hash(plan))
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_split_run(self, payload):
        uids = uid_list(payload.get("uids"))
        only = (payload.get("only") or "").strip()
        batch = (payload.get("batch") or "").strip()
        if only:
            if not re.match(r"^B\d{4}$", only):
                self.fail(400, "uid 形如 B1234")
                return
            if not re.match(r"^[A-Za-z0-9._-]+$", batch):
                self.fail(400, "只重跑一本要给已有批次名")
                return
            if not os.path.isdir(os.path.join(self.app.env.batches, batch)):
                self.fail(404, "批次不存在：%s" % batch)
                return
            uids = [only]
        if not uids:
            self.fail(400, "uids 为空")
            return
        try:
            workers = max(1, min(int(payload.get("workers") or 4), 16))
        except (TypeError, ValueError):
            workers = 4
        replace = bool(payload.get("replace"))
        if not only:
            batch = "resplit-ops-" if replace else "split-ops-"
            batch += time.strftime("%Y%m%d-%H%M%S")
        args = ["--batch", batch]
        args += ["--only", only] if only else ["--uids", ",".join(uids)]
        args += ["--workers", str(workers), "--report-json"]
        if replace:
            args.append("--replace")
        if only:
            # 单本入口就是“真的重拆”，不然暂存区有产物时空转
            args.append("--force")
        argv_args = args
        commands = [make_cmd(self.app.env, "split", "split_md_batch.py", argv_args)]
        commands += rebuild_commands(self.app.env)
        job_id = self.app.runner.submit(
            "split.run", "%s %d 本" % ("重拆" if replace else "拆章", len(uids)),
            commands,
            batch_dir=os.path.join("工作区", "批次", batch))
        self.send_json(200, {"ok": True, "job_id": job_id, "batch": batch})

    def split_target(self, payload):
        uid = (payload.get("uid") or "").strip()
        batch = (payload.get("batch") or "").strip()
        if not re.match(r"^B\d{4}$", uid):
            self.fail(400, "uid 形如 B1234")
            return None
        if not re.match(r"^[A-Za-z0-9._-]+$", batch):
            self.fail(400, "批次名不合法")
            return None
        if not os.path.isdir(os.path.join(self.app.env.batches, batch, "logs")):
            self.fail(404, "这个批次没有记录")
            return None
        return batch, uid

    def api_split_recheck(self, payload):
        target = self.split_target(payload)
        if target is None:
            return
        batch, uid = target
        commands = [make_cmd(self.app.env, "split", "split_md_batch.py",
                             ["--batch", batch, "--only", uid, "--workers", "1",
                              "--report-json", "--recheck"])]
        commands += rebuild_commands(self.app.env)
        job_id = self.app.runner.submit(
            "split.run", "重新检查 %s" % uid, commands,
            batch_dir=os.path.join("工作区", "批次", batch))
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_split_inspect(self, payload):
        target = self.split_target(payload)
        if target is None:
            return
        batch, uid = target

        def build(job_id):
            out = os.path.join(self.app.env.rel(self.app.env.job_dir(job_id)),
                               "result.json")
            return [make_cmd(self.app.env, "review", "md_review_report.py",
                             [batch, uid, "--json", out])]
        job_id = self.app.runner.submit("split.inspect", "查看问题 %s" % uid, build)
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_pdf_triage(self, payload):
        uid = (payload.get("uid") or "").strip()
        if not re.match(r"^B\d{4}$", uid):
            self.fail(400, "uid 形如 B1234")
            return
        def build(job_id):
            out = self.app.env.rel(self.app.env.job_dir(job_id))
            return [make_cmd(self.app.env, "triage", "ops_pdf_triage.py",
                             ["--uid", uid, "--out", out], accepts_root=True)]
        job_id = self.app.runner.submit("pdf.triage", "分诊 %s" % uid, build)
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_pdf_confirm(self, payload):
        uid = (payload.get("uid") or "").strip()
        route = (payload.get("route") or "").strip()
        reason = (payload.get("reason") or "").strip()
        model = payload.get("model") or {}
        if not re.match(r"^B\d{4}$", uid):
            self.fail(400, "uid 形如 B1234")
            return
        if route not in PDF_ROUTES:
            self.fail(400, "route 只收：%s" % "、".join(PDF_ROUTES))
            return
        job_id = (payload.get("job_id") or "").strip()
        job = self.app.store.get(job_id) if job_id else None
        if job is None or job.get("kind") != "pdf.triage":
            job = next((row for row in self.app.store.list(limit=500)
                        if row.get("kind") == "pdf.triage"
                        and row.get("title") == "分诊 " + uid), None)
        if not job:
            self.fail(400, "先对这个 uid 做分诊，分诊结果里才有 triage.csv")
            return
        job_dir = self.app.env.job_dir(job["id"])
        triage_csv = os.path.join(job_dir, "triage.csv")
        if not os.path.isfile(triage_csv):
            self.fail(400, "分诊结果没有 triage.csv，重跑分诊")
            return
        result = read_json(os.path.join(job_dir, "result.json")) or {}
        if result.get("uid") != uid:
            self.fail(400, "分诊结果与 uid 不一致")
            return
        if job.get("state") == "awaiting_review":
            self.app.store.update(job["id"], state="succeeded", ended=now_iso(),
                                  summary="已确认 route=%s" % route)
        with open(triage_csv, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            triage_fields = reader.fieldnames or []
            triage_row = next(reader, None)
        if not triage_row or not triage_fields:
            self.fail(400, "分诊 triage.csv 没有数据行")
            return
        batch = TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d")
        batch_dir = os.path.join(self.app.env.batches, batch)
        review_dir = os.path.join(batch_dir, "review")
        os.makedirs(batch_dir, exist_ok=True)
        batch_triage = os.path.join(batch_dir, "triage.csv")
        with ledger_lock(self.app.env):
            upsert_csv(batch_triage, triage_row, triage_fields)
            for name in ("census.jsonl", "text.jsonl", "scan.jsonl"):
                src = os.path.join(job_dir, name)
                if not os.path.isfile(src):
                    continue
                rows = []
                with open(src, encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
                merge_jsonl(os.path.join(batch_dir, name), rows, uid)
            review = {"uid": uid, "verdict": payload.get("verdict") or route,
                      "evidence": payload.get("evidence") or "",
                      "route": route, "reason": reason,
                      "model": ({"route": model.get("route"),
                                 "confidence": model.get("confidence"),
                                 "reason": model.get("reason"),
                                 "at": model.get("at") or now_iso()}
                                if isinstance(model, dict) and model.get("route") else None),
                      "at": now_iso()}
            write_json_atomic(os.path.join(review_dir, uid + ".json"), review)
            job_id = self.app.runner.submit(
                "pdf.confirm", "分诊确认 %s → %s" % (uid, route),
                [make_cmd(self.app.env, "apply_review", "pdf_apply_review.py",
                          ["--batch", batch,
                           "--triage", self.app.env.rel(batch_triage),
                           "--out-dir", self.app.env.rel(batch_dir),
                           "--review", self.app.env.rel(review_dir)])],
                batch_dir=os.path.join("工作区", "批次", batch))
        self.send_json(200, {"ok": True, "job_id": job_id,
                             "review": self.app.env.rel(
                                 os.path.join(review_dir, uid + ".json")),
                             "batch": batch})

    def api_pdf_triage_batch(self, payload):
        uids = uid_list(payload.get("uids"))
        index = self.app.pdf_books_index()
        explicit = bool(uids)
        if not uids:
            uids = [uid for uid, row in index.items() if row["disposition"] == "untriaged"]
        if not uids:
            self.fail(400, "没有未分诊的 PDF")
            return
        unknown = [uid for uid in uids if uid not in index]
        if unknown:
            self.fail(400, "这些不在待转 PDF 里：%s" % "、".join(unknown))
            return
        if not explicit:
            # 已经有建议的书不再跑一次，模型要钱，建议又不会变
            have = self.app.pdf_suggestions(uids)
            uids = [uid for uid in uids if uid not in have]
            if not uids:
                self.fail(400, "未分诊的书都已经有建议，去清单里确认")
                return
        def build(job_id):
            job_dir = self.app.env.job_dir(job_id)
            return [make_cmd(self.app.env, "triage " + uid, "ops_pdf_triage.py",
                             ["--uid", uid,
                              "--out", self.app.env.rel(os.path.join(job_dir, uid))],
                             accepts_root=True) for uid in uids]
        job_id = self.app.runner.submit("pdf.triage", "批量分诊 %d 本" % len(uids), build)
        self.send_json(200, {"ok": True, "job_id": job_id, "uids": uids})

    def api_pdf_settle(self, payload):
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            self.fail(400, "items 为空")
            return
        clean, routes, seen = [], collections.Counter(), set()
        for item in items:
            if not isinstance(item, dict):
                self.fail(400, "items 里每一项要是 {uid, route}")
                return
            uid = str(item.get("uid") or "").strip()
            route = str(item.get("route") or "").strip()
            if not re.match(r"^B\d{4}$", uid):
                self.fail(400, "uid 形如 B1234")
                return
            if route not in PDF_SETTLE_ROUTES:
                self.fail(400, "route 只收：%s" % "、".join(PDF_SETTLE_ROUTES))
                return
            if uid in seen:
                continue
            seen.add(uid)
            clean.append({"uid": uid, "route": route})
            routes[route] += 1
        index = self.app.pdf_books_index()
        unknown = [item["uid"] for item in clean if item["uid"] not in index]
        if unknown:
            self.fail(400, "这些不在待转 PDF 里：%s" % "、".join(unknown))
            return
        for item in clean:
            suggestion = index[item["uid"]].get("suggestion") or {}
            follow = suggestion.get("route") == item["route"]
            item["reason"] = "按分诊建议确认" if follow else "在 PDF 清单里改处置"
            item["suggestion"] = suggestion if follow else None
        # 批次账读改写与 confirm 同一把锁：并发 settle 之间、settle 与 confirm
        # 之间都不能互相抹行。
        with ledger_lock(self.app.env):
            batch, command = self.app.pdf_settle(clean, index)
            job_id = self.app.runner.submit(
                "pdf.confirm", "改处置 %d 本" % len(clean), [command],
                batch_dir=os.path.join("工作区", "批次", batch))
        self.send_json(200, {"ok": True, "job_id": job_id, "batch": batch,
                             "routes": dict(routes)})

    def api_pdf_run(self, payload):
        uids = uid_list(payload.get("uids"))
        if not uids:
            self.fail(400, "uids 为空")
            return
        try:
            limit = int(payload.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        try:
            workers = max(1, min(int(payload.get("workers") or 3), 8))
        except (TypeError, ValueError):
            workers = 3
        force = payload.get("force") is True
        index = self.app.pdf_books_index()
        unknown = [uid for uid in uids if uid not in index]
        if unknown:
            self.fail(400, "这些不在待转 PDF 里：%s" % "、".join(unknown))
            return
        groups = collections.defaultdict(list)
        for uid in uids:
            groups[index[uid]["disposition"]].append(uid)
        gallery, japanese = groups["gallery"], groups["ja"]
        convert, untriaged = groups["convert"], groups["untriaged"]
        if (gallery or japanese) and not force:
            self.fail(400, "选中的 %d 本里有 %d 本只记图册、%d 本日文暂缓，默认不转；"
                           "要转就再确认一次"
                      % (len(gallery) + len(japanese), len(gallery), len(japanese)))
            return
        batch = TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d")
        batch_dir = os.path.join(self.app.env.batches, batch)
        batch_triage = os.path.join(batch_dir, "triage_final.csv")
        triage = self.app.env.rel(batch_triage)
        routes = {"ocr": [], "text": []}
        commands = []
        carried = []
        if convert:
            routes.update(split_by_route(batch_triage, convert))
            placed = set(routes.get("ocr") or []) | set(routes.get("text") or [])
            carried = [uid for uid in convert if uid not in placed]
            if carried:
                commands.append(self.carry_pdf(carried, index))
                for uid in carried:
                    key = "ocr" if index[uid]["route"] == "OCR" else "text"
                    routes[key].append(uid)
                    placed.add(uid)
            missing = [uid for uid in convert if uid not in placed]
            if missing:
                self.fail(400, "这些 uid 在 %s 里没有可转的 route，先做分诊确认：%s"
                          % (triage, "、".join(missing)))
                return
        forced = gallery + japanese
        if forced:
            command = self.forced_pdf(forced, index)
            if not any(c["name"] == command["name"] for c in commands):
                commands.append(command)
            routes["ocr"] = (routes.get("ocr") or []) + forced
        for name in sorted(routes):
            group = routes[name]
            if not group:
                continue
            args = ["--batch", batch, "--route", name, "--uids", ",".join(group),
                    "--workers", str(workers), "--triage", triage,
                    "--report-json"]
            if limit:
                args += ["--limit", str(limit)]
            commands.append(make_cmd(self.app.env, "pdf %s" % name, "pdf_md_batch.py",
                                     args))
        triage_jobs = []
        for uid in untriaged:
            def build(job_id, uid=uid):
                out = self.app.env.rel(self.app.env.job_dir(job_id))
                return [make_cmd(self.app.env, "triage", "ops_pdf_triage.py",
                                 ["--uid", uid, "--out", out], accepts_root=True)]
            triage_jobs.append(self.app.runner.submit("pdf.triage", "分诊 %s" % uid, build))
        job_id = None
        if commands:
            commands += rebuild_commands(self.app.env)
            job_id = self.app.runner.submit(
                "pdf.run", "PDF 转 md %d 本" % (len(convert) + len(forced)), commands,
                batch_dir=os.path.join("工作区", "批次", batch))
        self.send_json(200, {"ok": True, "job_id": job_id,
                             "batch": batch if job_id else "",
                             "routes": {k: len(v) for k, v in routes.items() if v},
                             "forced": len(forced), "triaged": len(untriaged),
                             "carried": len(carried),
                             "triage_jobs": triage_jobs})

    def carry_pdf(self, uids, index):
        """今天的分诊表里没有这些书：沿用之前批次那条 route，先落一次 apply_review。

        triage_final.csv 与 skip_list.tsv 都由 apply_review 写，驱动读的是它写完的那份。
        """
        items = [{"uid": uid, "route": index[uid]["route"],
                  "reason": "今天这张表里没有这本书，沿用之前批次的 route"}
                 for uid in uids]
        return self.app.pdf_settle(items, index)[1]

    def forced_pdf(self, uids, index):
        """改处置成 OCR，返回 apply_review 那条命令。它排在驱动前面：triage_final.csv 与
        skip_list.tsv 都由它写。"""
        items = [{"uid": uid, "route": "OCR", "verdict": "文字书",
                  "reason": "在 PDF 清单里二次确认按扫描识别转"} for uid in uids]
        return self.app.pdf_settle(items, index)[1]

    def api_maintain(self, payload):
        steps = payload.get("steps") or list(FINISH_DEFAULT)
        if not isinstance(steps, list):
            self.fail(400, "steps 必须是数组")
            return
        unknown = [s for s in steps if s not in MAINTAIN_STEPS]
        if unknown:
            self.fail(400, "不认识的步骤：%s" % "、".join(map(str, unknown)))
            return
        ordered = [s for s in MAINTAIN_ORDER if s in steps]
        if not ordered:
            self.fail(400, "没有选步骤")
            return
        commands = []
        for key in ordered:
            spec = MAINTAIN_STEPS[key]
            commands.append(make_cmd(self.app.env, key, spec["script"], spec["args"],
                                     marker=spec.get("marker")))
        job_id = self.app.runner.submit(
            "maintain", "收尾 %d 步" % len(ordered), commands)
        self.send_json(200, {"ok": True, "job_id": job_id, "steps": ordered})

    def api_delete_dry(self, payload):
        uid = (payload.get("uid") or "").strip()
        reason = (payload.get("reason") or "").strip()
        if not re.match(r"^B\d{4}$", uid):
            self.fail(400, "uid 形如 B1234")
            return
        if not reason:
            self.fail(400, "理由不能空")
            return
        job_id = self.app.runner.submit(
            "delete.dry-run", "删书预览 %s" % uid,
            [make_cmd(self.app.env, "dry-run", "retire_book.py",
                      ["--uid", uid, "--reason", reason, "--dry-run", "--json"],
                      accepts_root=True, capture="dry-run.json")])
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_delete_apply(self, payload):
        uid = (payload.get("uid") or "").strip()
        reason = (payload.get("reason") or "").strip()
        plan_hash = (payload.get("plan_hash") or "").strip()
        confirm = (payload.get("confirm_uid") or "").strip()
        if not re.match(r"^B\d{4}$", uid):
            self.fail(400, "uid 形如 B1234")
            return
        if confirm != uid:
            self.fail(400, "输入的 uid 与目标不一致")
            return
        if not plan_hash:
            self.fail(400, "缺 plan_hash，先做预览")
            return
        if not reason:
            self.fail(400, "理由不能空")
            return
        if self.writer_busy():
            self.fail(409, "有写任务在跑，等它结束再发")
            return
        job_id = self.app.runner.submit(
            "delete.apply", "删书 %s" % uid,
            [make_cmd(self.app.env, "retire", "retire_book.py",
                      ["--uid", uid, "--reason", reason, "--plan-hash", plan_hash,
                       "--apply"], accepts_root=True)],
            plan_hash=plan_hash)
        self.send_json(200, {"ok": True, "job_id": job_id})

    def api_clean(self, payload):
        return self.send_json(200, dict(ok=True, **self.app.clean()))

    def api_cancel(self, job_id, payload):
        job = self.app.store.get(job_id)
        if not job:
            self.fail(404, "没有这个任务")
            return
        if job["state"] in DONE_STATES or job["state"] == "awaiting_review":
            self.fail(409, "任务已结束：%s" % job["state"])
            return
        if self.app.runner.cancel(job_id):
            self.send_json(200, {"ok": True, "job_id": job_id, "state": "cancelling"})
        else:
            self.fail(409, "取消不了")

    def api_rerun(self, job_id):
        job = self.app.store.get(job_id)
        if not job:
            self.fail(404, "没有这个任务")
            return
        commands = read_json(os.path.join(self.app.env.job_dir(job_id),
                                          "commands.json"))
        if not commands:
            self.fail(409, "找不到原来的命令行")
            return
        commands = [retarget(self.app.env, c) for c in commands if isinstance(c, dict)]
        if not commands:
            self.fail(409, "原来的命令行里没有可重跑的步骤")
            return
        if not KINDS.get(job["kind"], {}).get("readonly") and self.writer_busy():
            self.fail(409, "有写任务在跑，等它结束再发")
            return
        new_id = self.app.runner.submit(job["kind"], job["title"] + "（重跑）", commands,
                                        batch_dir=job.get("batch_dir"),
                                        plan_hash=job.get("plan_hash"))
        self.send_json(200, {"ok": True, "job_id": new_id})


WRITE_ROUTES = ("/api/import/apply", "/api/split/run", "/api/split/recheck",
                "/api/pdf/settle", "/api/pdf/confirm", "/api/pdf/run", "/api/maintain",
                "/api/delete/apply")
PDF_ROUTES = ("OCR", "本地抽文本", "不转-记图册", "人工确认", "日文暂缓")


def plan_path(env, raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("plan_path 为空")
    rel = raw.strip()
    if not rel.startswith("工作区/运维/jobs/"):
        raise ValueError("plan_path 必须在 工作区/运维/jobs/ 下")
    path = os.path.realpath(os.path.join(env.root, rel))
    root = os.path.realpath(env.root)
    if not path.startswith(root + os.sep):
        raise ValueError("plan_path 越界")
    if not os.path.exists(path):
        raise ValueError("plan 文件不存在：%s" % rel)
    return path


def plan_file_hash(path):
    """plan 内容哈希，给 job.plan_hash。这个字段在删书那边是预览哈希，
    导入这边以前错存了文件路径，排查时误导。"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return ""


def uid_list(raw):
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        uid = str(item).strip()
        if not uid:
            continue
        if not re.match(r"^B\d{4}$", uid):
            raise ValueError("uid 形如 B1234：%r" % uid)
        out.append(uid)
    seen = set()
    return [u for u in out if not (u in seen or seen.add(u))]


def split_by_route(triage_path, uids):
    if not os.path.exists(triage_path):
        return {}
    want = set(uids)
    routes = {"ocr": [], "text": []}
    for row in read_table("", triage_path, ","):
        uid = (row.get("uid") or "").strip()
        if uid not in want:
            continue
        route = (row.get("route") or "").strip()
        if route == "OCR":
            routes["ocr"].append(uid)
        elif route == "本地抽文本":
            routes["text"].append(uid)
    return {k: v for k, v in routes.items() if v}


@contextmanager
def ledger_lock(env):
    """批次账读改写的互斥锁：与写任务共用 .writer.lock。确认与改处置在
    HTTP 线程里直接改批次账，两个标签页并发时会互相抹行，必须串行。"""
    fd = os.open(env.lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def upsert_csv(path, row, cols):
    """按 uid 合并一行；已有同 uid 行替换，其余保留。"""
    rows = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or cols
            rows = [r for r in reader if (r.get("uid") or "") != row["uid"]]
    rows.append({c: row.get(c, "") for c in cols})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def merge_jsonl(path, rows, uid):
    """按 uid 合并 JSONL；同 uid 旧行丢弃，保留其它书。"""
    keep = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if (obj.get("uid") or "") != uid:
                    keep.append(obj)
    keep.extend(rows)
    write_text_atomic(path, "".join(json.dumps(x, ensure_ascii=False) + "\n"
                                     for x in keep))


def read_table(root, rel, delim="\t"):
    """读批次账：csv 走 csv 模块（书名里常有逗号），tsv 按制表符切。"""
    path = rel if os.path.isabs(rel) else os.path.join(root, rel)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        if delim == ",":
            reader = csv.DictReader(fh)
            return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
                    for row in reader]
        head = [h.strip() for h in fh.readline().rstrip("\n").split(delim)]
        rows = []
        for line in fh:
            cells = line.rstrip("\n").split(delim)
            if len(cells) != len(head):
                continue
            rows.append(dict(zip(head, [c.strip() for c in cells])))
        return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="统一操作台服务（127.0.0.1:8770）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770,
                    help="0 表示随机端口（测试用）")
    ap.add_argument("--root", default=REAL_ROOT, help="数据根，测试指向临时库")
    args = ap.parse_args(argv)

    env = Env(args.root)
    app = App(env)
    Handler.app = app
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    print("统一操作台：http://%s:%d  root=%s" % (args.host, port, env.root), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止", flush=True)
    finally:
        app.runner.stopping = True
        httpd.server_close()


if __name__ == "__main__":
    main()
