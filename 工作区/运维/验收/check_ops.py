#!/usr/bin/env python3
"""统一操作台验收：随机端口、临时根、stub 脚本，不碰真库与真模型。

  python3 工作区/运维/验收/check_ops.py [-v]

做这些事：起服务，看 /api/status 与静态页；跑任务生命周期（queued → running →
判门槛）、写任务互斥与取消、上传、文件白名单与路径穿越、stub 导入与 PDF 分诊、
收尾门槛、删书预览与应用、重启接管与孤儿。末行 ALL PASS。
"""
import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
OPS_DIR = os.path.dirname(HERE)
SERVER = os.path.join(OPS_DIR, "server.py")
VERBOSE = False
FAILS = []
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(name)
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


STUB_INGEST = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d


def item(name, action, uid, note=""):
    return {"file": name, "path": "库/_收件箱/" + name, "sha256": name * 8, "bytes": 11,
            "format": os.path.splitext(name)[1].lstrip("."), "title": name,
            "author": "", "uid": uid, "target": "", "subjects": ["文学"],
            "retention": "keep", "action": action, "note": note,
            "classify": {"level1": "文学", "conf1": 0.91, "level2": "", "conf2": 0.91,
                         "needs_review": False}}


cmd = args[0]
if cmd == "scan":
    out = opt("--out")
    plan = [item("A.epub", "add", "B0001"),
            item("B.epub", "replace", "B0002", "匹配《B》，epub 替 mobi"),
            item("C.pdf", "conflict", "", "同名候选 B0003")]
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False)
        print("plan -> " + out)
    print("add 1 replace 1 conflict 1")
    sys.exit(0)
if cmd == "apply":
    plan = json.load(open(args[1], encoding="utf-8"))
    if any("FAILGATE" in (i.get("note") or "") for i in plan):
        print("apply 缺 subjects，停下")
        sys.exit(3)
    print("已入库 %d 条" % len(plan))
    sys.exit(0)
sys.exit(2)
'''

STUB_BUILD_LIBRARY = '''#!/usr/bin/env python3
import json
print(json.dumps({"books": 3, "sources": 3, "conflicts": 0, "md_done": 1}))
'''

STUB_CATALOG = '''#!/usr/bin/env python3
print("未匹配 0 条")
'''

STUB_SPLIT = '''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
if os.path.exists(".slow-split"):
    print("慢批次：开始")
    time.sleep(60)
batch = opt("--batch")
uids = [u for u in (opt("--uids") or "").split(",") if u]
print("批次 %s，%d 本" % (batch, len(uids)))
books = [{"uid": u, "title": "书 " + u, "status": "landed", "verify_rc": 0,
          "audit_rc": 0, "audit_ratio": 0.99, "chapters": 12} for u in uids]
print(json.dumps({"batch": batch, "status_counts": {"landed": len(books)},
                  "books": books, "pending_review": []}, ensure_ascii=False))
'''

STUB_PDF = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
route = opt("--route", "ocr")
uids = [u for u in (opt("--uids") or "").split(",") if u]
print("批次 %s 路线 %s 本数 %d" % (opt("--batch"), route, len(uids)))
print(json.dumps({"route": route, "batch": opt("--batch"), "queued": len(uids),
                  "landed": len(uids), "remaining_books": 0, "remaining_pages": 0,
                  "status_counts": {"landed": len(uids)}}, ensure_ascii=False))
'''

STUB_TRIAGE = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
uid, out = opt("--uid"), opt("--out")
os.makedirs(os.path.join(out, "thumbs"), exist_ok=True)
thumbs = []
for page in (1, 160, 320):
    rel = "thumbs/%03d.png" % page
    with open(os.path.join(out, rel), "wb") as fh:
        fh.write(b"\\x89PNG\\r\\n\\x1a\\n")
    thumbs.append(rel)
result = {"uid": uid, "title": "书 " + uid, "path": "库/x/source/a.pdf", "pages": 320,
          "class": "C 扫描件(无文本层)",
          "metrics": {"text_page_share": 0.0, "ink": 0.42, "bands": 2,
                      "image_share": 1.0},
          "thumbs": thumbs,
          "model": {"route": "OCR", "confidence": 0.86, "reason": "有字迹无文本层"},
          "prompt_hash": "abc123"}
with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False)
with open(os.path.join(out, "triage.csv"), "w", encoding="utf-8") as fh:
    fh.write("uid,title,subject1,subject2,class,route,pages,mb,ppi,text_page_share,ink,bands,note,verdict,image_share,measure,route_metric\\n")
    fh.write('"%s","书 %s","文学","","C 扫描件","OCR","320","1.0","","0.0","0.42","2","stub","","1.0","stub","OCR"\\n' % (uid, uid))
print(json.dumps(result, ensure_ascii=False))
'''

STUB_APPLY_REVIEW = '''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
print("写回 %s 的 review，四列已并。" % opt("--batch"))
'''

STUB_RETIRE = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
uid = opt("--uid")
preview = {"uid": uid, "title": "书 " + uid, "path": "文学/x/书 " + uid,
           "book_dir": "库/文学/x/书 " + uid,
           "sources": [{"path": "库/文学/x/书 " + uid + "/source/a.epub",
                        "sha256": "deadbeef", "bytes": 11, "shared_with": 2,
                        "action": "hardlink"}],
           "delete": ["md/", "html/", "book.json"], "ledger": True, "db": True,
           "awards": [], "views": [], "series_docs": [], "md_chapters": 12,
           "md_bytes": 4096, "expand_rows": 3, "index_rows": 0,
           "cold_path": "库/_冷宫/书 " + uid + "/source/a.epub",
           "snapshot": "../books.snapshot-2026-10-24", "snapshot_ok": True,
           "plan_hash": (opt("--plan-hash") or "hash-" + uid)}
if "--apply" in args:
    if opt("--plan-hash") == "bad":
        print(json.dumps({"ok": False, "error": "plan_hash 与现库不一致，拒绝"},
                         ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({"ok": True, "uid": uid, "cold": "库/_冷宫/书 " + uid,
                      "retired_tsv": "工作区/批次/移出-2026-10-24/retired.tsv"},
                     ensure_ascii=False))
    sys.exit(0)
print(json.dumps(preview, ensure_ascii=False))
'''

STUB_VERIFY = '''#!/usr/bin/env python3
import os, sys
if os.path.exists(".verify-bad"):
    print("门槛不过：1 项")
    sys.exit(1)
print("全过")
'''

STUB_P1 = '''#!/usr/bin/env python3
print("失败 0 项")
'''

STUB_AUDIT = '''#!/usr/bin/env python3
print("FAIL 0 项 / WARN 1 项")
'''

STUB_QUIET = '''#!/usr/bin/env python3
print("done")
'''

STUB_STATUS = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"at": "2026-10-24T23:00:00+08:00", "books": 42, "md_done": 41,
                  "pending": {"pdf": 1, "txt": 0, "djvu": 0},
                  "inbox": {"files": 0, "items": []},
                  "index": {"lag_books": 2}, "views": {"awards": 0, "topics": 0,
                                                       "broken": 0},
                  "audit": {"fail": 0, "warn": 0, "items": []},
                  "ocr": {"remaining_books": 0, "remaining_pages": 0},
                  "ledger": {"rows": 42}, "catalog": {"unmatched": 0}}))
'''

STUBS = {
    "ingest.py": STUB_INGEST,
    "build_library.py": STUB_BUILD_LIBRARY,
    "gen_book_catalog.py": STUB_CATALOG,
    "split_md_batch.py": STUB_SPLIT,
    "pdf_md_batch.py": STUB_PDF,
    "ops_pdf_triage.py": STUB_TRIAGE,
    "pdf_apply_review.py": STUB_APPLY_REVIEW,
    "retire_book.py": STUB_RETIRE,
    "verify_phase.py": STUB_VERIFY,
    "check_p1.py": STUB_P1,
    "audit_state.py": STUB_AUDIT,
    "build_para_index.py": STUB_QUIET,
    "build_topic_views.py": STUB_QUIET,
    "migrate_layout.py": STUB_QUIET,
    "build_index.py": STUB_QUIET,
}

FIXTURE_STATUS = {
    "at": "2026-10-24T22:00:00+08:00", "books": 11, "md_done": 7,
    "pending": {"pdf": 2, "txt": 1, "djvu": 1},
    "inbox": {"files": 2, "items": []},
    "ledger": {"rows": 11, "mtime": "2026-10-24T21:00:00+08:00"},
    "catalog": {"mtime": "2026-10-24T21:07:00+08:00", "unmatched": 0},
    "index": {"built_at": "2026-10-24T20:00:00+08:00", "paras": 100, "chapters": 20,
              "books": 7, "lag_books": 3, "lag_chapters": 9},
    "views": {"awards": 2, "topics": 3, "broken": 0},
    "audit": {"fail": 0, "warn": 1, "items": ["warn: 批次在跑"]},
    "ocr": {"batch": "pdf-ocr-2026-10-19", "remaining_books": 0,
            "remaining_pages": 0, "quota": "20000/天"},
    "jobs": {"running": 0, "awaiting_review": 0, "last": None},
}


def build_root():
    root = tempfile.mkdtemp(prefix="ops-check-")
    for rel in ("工作区/运维/jobs", "工作区/脚本", "工作区/批次", "目录",
                "库/_收件箱", "视图/奖项", "视图/主题"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    for name, body in STUBS.items():
        path = os.path.join(root, "工作区", "脚本", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)
    with open(os.path.join(root, "目录", "书目总表.csv"), "w", encoding="utf-8") as fh:
        fh.write("uid,title\nB0001,A\n")
    with open(os.path.join(root, "目录", "分类预填.csv"), "w", encoding="utf-8") as fh:
        fh.write("uid,一级模型\nB0001,文学\n")
    with open(os.path.join(root, "目录", "书目总表.xlsx"), "wb") as fh:
        fh.write(b"PK\x03\x04fake")
    make_library_db(os.path.join(root, "目录", "library.db"))
    with open(os.path.join(root, "库", "_收件箱", "A.epub"), "wb") as fh:
        fh.write(b"epub-a")
    with open(os.path.join(root, "库", "_收件箱", "B.epub"), "wb") as fh:
        fh.write(b"epub-b")
    with open(os.path.join(root, "库", "_收件箱", "C.pdf"), "wb") as fh:
        fh.write(b"pdf-c")
    fixture = os.path.join(root, "status-fixture.json")
    with open(fixture, "w", encoding="utf-8") as fh:
        json.dump(FIXTURE_STATUS, fh, ensure_ascii=False)
    return root, fixture


def server_schema():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ops_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SCHEMA


def ensure_ops_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(server_schema())
    conn.commit()
    return conn


def make_library_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
CREATE TABLE book(uid TEXT PRIMARY KEY, title TEXT, author TEXT, subject1 TEXT,
  subject2 TEXT, path TEXT, series TEXT, volume TEXT, retention TEXT, path_state TEXT);
CREATE TABLE source(uid TEXT, file TEXT, format TEXT, bytes INT, sha256 TEXT,
  primary_src INT);
CREATE TABLE derived(uid TEXT, kind TEXT, status TEXT, chapters INT, bytes INT,
  source_sha256 TEXT);
CREATE TABLE book_award(uid TEXT, award TEXT, year TEXT, result TEXT);
CREATE VIEW v_pending_md AS
  SELECT b.uid, b.subject1, b.subject2, b.title, s.format, s.sha256
  FROM book b JOIN source s ON s.uid = b.uid AND s.primary_src = 1
  LEFT JOIN derived d ON d.uid = b.uid AND d.kind = 'md'
  WHERE d.uid IS NULL OR d.status != 'done';
""")
    conn.execute("INSERT INTO book VALUES ('B0001','书 一','某人','文学','小说',"
                 "'文学/小说/书 一','','','keep','active')")
    conn.execute("INSERT INTO book VALUES ('B0002','书 二','另一人','人文社科','',"
                 "'人文社科/书 二','某系列','1','keep','active')")
    conn.execute("INSERT INTO source VALUES ('B0001','库/文学/小说/书 一/source/a.epub',"
                 "'epub',11,'aa',1)")
    conn.execute("INSERT INTO source VALUES ('B0002','库/人文社科/书 二/source/b.pdf',"
                 "'pdf',22,'bb',1)")
    conn.execute("INSERT INTO derived VALUES ('B0002','md','done',12,4096,'bb')")
    conn.execute("INSERT INTO book_award VALUES ('B0002','某奖','2020','longlist')")
    conn.commit()
    conn.close()


def http(port, method, path, payload=None, headers=None, raw=None, timeout=30):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data, hdrs = None, dict(headers or {})
    if raw is not None:
        data = raw
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(body.decode("utf-8"))
            return resp.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def get(port, path, **kw):
    return http(port, "GET", path, **kw)


def post(port, path, payload=None, **kw):
    return http(port, "POST", path, payload=payload if payload is not None else {}, **kw)


def wait_ready(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, body = get(port, "/api/status", timeout=5)
            if code == 200:
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.15)
    return False


def start(root, fixture, port=0):
    env = dict(os.environ)
    env["OPS_STATUS_FIXTURE"] = fixture
    env.pop("OPS_HTTP_LOG", None)
    proc = subprocess.Popen([sys.executable, SERVER, "--root", root, "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=env, start_new_session=True)
    line = proc.stdout.readline()
    match = re.search(r"127\.0\.0\.1:(\d+)", line or "")
    if not match:
        proc.kill()
        raise SystemExit("服务没打印端口：%r" % line)
    real_port = int(match.group(1))
    if not wait_ready(real_port):
        proc.kill()
        raise SystemExit("服务起不来：%r" % line)
    return proc, real_port


def stop(proc):
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_job(port, job_id, states, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, body = get(port, "/api/jobs/" + job_id)
        if code != 200:
            return None
        last = body
        if body["job"]["state"] in states:
            return body
        time.sleep(0.15)
    return last


def job_log(port, job_id):
    code, body = get(port, "/api/jobs/%s/log?offset=0" % job_id)
    return body.get("text", "") if code == 200 else ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="留着临时根排查")
    args = ap.parse_args(argv)
    global VERBOSE
    VERBOSE = args.verbose
    if not os.path.exists(SERVER):
        raise SystemExit("缺 server.py：%s" % SERVER)

    root, fixture = build_root()
    print("临时根：%s" % root)
    proc, port = start(root, fixture)
    try:
        run_checks(root, os.path.join(root, "工作区", "运维"), port, fixture)
    finally:
        stop(proc)
        if args.keep:
            print("保留临时根：%s" % root)
        else:
            shutil.rmtree(root, ignore_errors=True)

    adopt_root, adopt_fixture = build_root()
    try:
        adoption_checks(adopt_root, adopt_fixture)
    finally:
        if args.keep:
            print("保留临时根：%s" % adopt_root)
        else:
            shutil.rmtree(adopt_root, ignore_errors=True)
    return finish()


def finish():
    print("%d 项检查，失败 %d 项" % (len(CHECKS), len(FAILS)))
    if FAILS:
        for name in FAILS:
            print("  失败：" + name)
        return 1
    print("ALL PASS")
    return 0


def run_checks(root, ops, port, fixture):
    # ---- 只读总览 --------------------------------------------------
    code, body = get(port, "/api/status")
    status = body.get("status") or {}
    check("status 200 且用 fixture", code == 200 and body.get("source") == "fixture",
          "source=%s error=%s" % (body.get("source"), body.get("error")))
    check("status 字段与 fixture 一致",
          status.get("books") == 11 and status.get("md_done") == 7
          and status.get("pending", {}).get("pdf") == 2
          and status.get("index", {}).get("lag_books") == 3,
          "books=%s md=%s lag=%s" % (status.get("books"), status.get("md_done"),
                                     (status.get("index") or {}).get("lag_books")))
    check("ops 计数由 ops.db 提供",
          (body.get("ops") or {}).get("writer") is False
          and status.get("jobs", {}).get("running") == 0)

    code, html = get(port, "/")
    check("首页渲染", code == 200 and "统一操作台" in html, "len=%d" % len(html or ""))
    for name in ("app.js", "style.css"):
        code, _ = get(port, "/" + name)
        check("静态文件 " + name, code == 200)

    code, body = get(port, "/api/jobs")
    check("空任务表", code == 200 and body.get("jobs") == [])

    # ---- 只读数据接口 ----------------------------------------------
    code, body = get(port, "/api/pending")
    check("待拆列表读 library.db",
          code == 200 and any(r["uid"] == "B0001" for r in body.get("items", [])),
          "n=%d" % len(body.get("items") or []))
    code, body = get(port, "/api/inbox")
    check("收件箱三只", code == 200 and len(body.get("items", [])) == 3)
    code, body = get(port, "/api/retire/search?q=B0002")
    book = (body.get("books") or [{}])[0]
    check("删书搜索带源与奖项",
          code == 200 and book.get("uid") == "B0002" and book.get("sources")
          and book.get("awards") and (book.get("md") or {}).get("chapters") == 12)
    code, body = get(port, "/api/pdf/queue")
    check("PDF 队列容忍空账", code == 200 and body.get("ok") is True,
          "groups=%d" % len(body.get("groups") or []))

    # ---- 跨站与文件边界 --------------------------------------------
    code, _ = post(port, "/api/import/scan", {},
                   headers={"Origin": "http://evil.example"})
    check("跨站 Origin 被拒", code == 403)
    code, _ = http(port, "POST", "/api/import/scan", payload={},
                   headers={"Host": "evil.example"})
    check("外站 Host 被拒", code == 403)

    code, body = get(port, "/api/file?path=" + urllib.request.quote("目录/书目总表.csv"))
    check("白名单 CSV 可下", code == 200 and "uid,title" in body,
          "code=%s body=%r" % (code, str(body)[:40]))
    for bad in ("目录/library.db", "工作区/脚本/ingest.py",
                "工作区/运维/ops.db", "../etc/passwd", "/etc/passwd",
                "工作区/运维/jobs/../../../../etc/passwd"):
        code, _ = get(port, "/api/file?path=" + urllib.request.quote(bad))
        check("白名单拒绝 %s" % bad, code == 404)

    # ---- 上传 ------------------------------------------------------
    code, body = post(port, "/api/import/upload",
                      raw=b"epub-bytes-here",
                      headers={"X-Filename": urllib.request.quote("测试 书.epub")})
    inbox_file = os.path.join(root, "库", "_收件箱", "测试 书.epub")
    check("上传落收件箱",
          code == 200 and os.path.exists(inbox_file)
          and open(inbox_file, "rb").read() == b"epub-bytes-here",
          str(body.get("name") if isinstance(body, dict) else body))
    check("上传不留 .part",
          not [n for n in os.listdir(os.path.join(root, "库", "_收件箱"))
               if n.endswith(".part")])
    code, body = post(port, "/api/import/upload", raw=b"x",
                      headers={"X-Filename": urllib.request.quote("../../pwn.epub")})
    check("上传文件名去路径",
          code == 200 and os.path.exists(os.path.join(root, "库", "_收件箱", "pwn.epub"))
          and not os.path.exists(os.path.join(root, "..", "pwn.epub")))
    code, _ = post(port, "/api/import/upload", raw=b"x", headers={"X-Filename": ".."})
    check("上传拒绝 ..", code == 400)

    # ---- 导入扫描 --------------------------------------------------
    code, body = post(port, "/api/import/scan")
    scan_id = body.get("job_id")
    check("扫描返回 job_id", code == 200 and bool(scan_id))
    detail = wait_job(port, scan_id, ("awaiting_review", "failed"))
    job = (detail or {}).get("job") or {}
    check("扫描落到 awaiting_review", job.get("state") == "awaiting_review",
          "%s · %s" % (job.get("state"), job.get("summary")))
    check("扫描门槛读计划",
          (job.get("summary") or "").startswith(
              "计划待确认：add 1 / replace 1 / conflict 1 / skip 0"),
          str(job.get("summary")))
    check("步骤都跑完", all(s["state"] == "ok" for s in (detail or {}).get("steps", [])))
    plan_rel = "工作区/运维/jobs/%s/plan.json" % scan_id
    code, plan = get(port, "/api/file?path=" + urllib.request.quote(plan_rel))
    check("计划文件可读且三行", code == 200 and isinstance(plan, list) and len(plan) == 3)
    code, body = get(port, "/api/jobs?state=awaiting_review")
    check("待确认队列能查到", any(j["id"] == scan_id for j in body.get("jobs", [])))
    check("日志接口有内容", "add 1 replace 1 conflict 1" in job_log(port, scan_id))
    check("一个任务只派发一次", job_log(port, scan_id).count("---- step scan ----") == 1,
          "step 标记 %d 个" % job_log(port, scan_id).count("---- step scan ----"))
    code, body = get(port, "/api/import/latest")
    check("最近计划能载入", code == 200 and body.get("job_id") == scan_id
          and len(body.get("plan") or []) == 3,
          "%s · %s 行" % (body.get("job_id"), len(body.get("plan") or [])))
    inbox_a = os.path.join(root, "库", "_收件箱", "A.epub")
    os.replace(inbox_a, inbox_a + ".away")
    try:
        code, body = get(port, "/api/import/latest")
        check("源文件不在就不给计划", code == 200 and body.get("plan") is None,
              str(body.get("job_id")))
    finally:
        os.replace(inbox_a + ".away", inbox_a)
    code, _ = post(port, "/api/jobs/%s/cancel" % scan_id)
    check("待确认任务不能停止", code == 409)
    code, body = get(port, "/api/jobs/%s" % scan_id)
    check("拒绝停止后仍是待确认", (body.get("job") or {}).get("state") == "awaiting_review",
          str((body.get("job") or {}).get("state")))

    edited = json.loads(json.dumps(plan))
    edited[0]["subjects"] = ["文学", "类型小说"]
    edited[0]["title"] = "改过的书名"
    code, body = post(port, "/api/import/plan", {"job_id": scan_id, "items": edited})
    check("计划编辑写回", code == 200 and body.get("items") == 3)
    saved = get(port, "/api/file?path=" + urllib.request.quote(plan_rel))[1]
    check("写回的是改过的书名与分类",
          saved[0]["title"] == "改过的书名"
          and saved[0]["subjects"] == ["文学", "类型小说"], str(saved[0]["title"]))
    code, _ = post(port, "/api/import/plan", {"job_id": scan_id, "items": edited[:2]})
    check("计划行数与磁盘不一致被拒", code == 400)

    # ---- 导入应用：门槛过 ------------------------------------------
    code, body = post(port, "/api/import/apply", {"plan_path": plan_rel})
    apply_id = body.get("job_id")
    check("应用返回 job_id", code == 200 and bool(apply_id))
    detail = wait_job(port, apply_id, ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("应用成功看 conflicts 与未匹配",
          job.get("state") == "succeeded"
          and job.get("summary") == "conflicts=0，未匹配 0 条",
          "%s · %s" % (job.get("state"), job.get("summary")))
    check("应用三步骤", [s["name"] for s in (detail or {}).get("steps", [])]
          == ["ingest apply", "build_library", "gen_book_catalog"])
    commands = get(port, "/api/jobs/" + apply_id)[1]["commands"]
    check("应用 argv 用契约命令",
          commands[-1]["argv"][1].endswith("gen_book_catalog.py")
          and "--emit-json" in commands[1]["argv"],
          " · ".join(" ".join(c["argv"][2:]) for c in commands))

    # ---- 导入应用：门槛不过 ----------------------------------------
    bad_dir = os.path.join(ops, "jobs", "bad-plan")
    os.makedirs(bad_dir, exist_ok=True)
    with open(os.path.join(bad_dir, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump([{"file": "X.epub", "action": "add", "note": "FAILGATE",
                    "subjects": [], "uid": "B0009"}], fh)
    code, body = post(port, "/api/import/apply",
                      {"plan_path": "工作区/运维/jobs/bad-plan/plan.json"})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("应用失败判失败", job.get("state") == "failed"
          and "ingest apply" in (job.get("summary") or ""),
          "%s · %s" % (job.get("state"), job.get("summary")))

    # ---- 写任务互斥与取消 ------------------------------------------
    open(os.path.join(root, ".slow-split"), "w").close()
    code, body = post(port, "/api/split/run",
                      {"uids": ["B0001", "B0002"], "workers": 2, "replace": False})
    slow_id = body.get("job_id")
    slow_batch = body.get("batch") or ""
    check("拆章返回 job_id 与批次", code == 200 and slow_id
          and slow_batch.startswith("split-ops-"), str(slow_batch))
    detail = wait_job(port, slow_id, ("running",))
    check("拆章进入 running", (detail or {}).get("job", {}).get("state") == "running")
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("拆章 argv 含 --uids 与 --workers",
          "--uids" in argv and argv[argv.index("--uids") + 1] == "B0001,B0002"
          and argv[argv.index("--workers") + 1] == "2" and "--report-json" in argv,
          " ".join(argv[2:]))
    import fcntl
    lock_path = os.path.join(ops, ".writer.lock")
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = False
    except OSError:
        held = True
    finally:
        os.close(holder)
    check("写任务期间 flock 被占", held)
    code, body = post(port, "/api/split/run", {"uids": ["B0001"]})
    check("写任务在跑时第二个写任务被拒", code == 409, str(body.get("error")))
    code, body = post(port, "/api/jobs/%s/cancel" % slow_id)
    check("停止接口接受", code == 200)
    detail = wait_job(port, slow_id, ("cancelled", "failed"), timeout=40)
    check("取消后是 cancelled",
          (detail or {}).get("job", {}).get("state") == "cancelled",
          str((detail or {}).get("job", {}).get("state")))
    os.remove(os.path.join(root, ".slow-split"))
    code, body = post(port, "/api/jobs/%s/cancel" % slow_id)
    check("已结束的任务不能再取消", code == 409)

    # ---- 拆章门槛读 report-json ------------------------------------
    code, body = post(port, "/api/split/run", {"uids": ["B0003"], "workers": 1})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("拆章成功读统计",
          job.get("state") == "succeeded" and "landed" in (job.get("summary") or ""),
          "%s · %s" % (job.get("state"), job.get("summary")))
    check("拆章步骤名", [s["name"] for s in (detail or {}).get("steps", [])[:3]]
          == ["split", "build_library", "gen_book_catalog"])

    os.makedirs(os.path.join(root, "工作区", "批次", slow_batch), exist_ok=True)
    code, body = post(port, "/api/split/run",
                      {"uids": ["B0004"], "only": "B0004", "batch": slow_batch,
                       "workers": 1})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("单本重跑带 --only 且复用批次",
          code == 200 and "--only" in argv and argv[argv.index("--only") + 1] == "B0004"
          and argv[argv.index("--batch") + 1] == slow_batch, " ".join(argv[2:]))

    # ---- PDF 分诊 --------------------------------------------------
    code, body = post(port, "/api/pdf/triage", {"uid": "B0001"})
    triage_id = body.get("job_id")
    detail = wait_job(port, triage_id, ("awaiting_review", "failed"))
    job = (detail or {}).get("job") or {}
    check("分诊落到 awaiting_review", job.get("state") == "awaiting_review")
    check("分诊摘要带模型建议", "OCR" in (job.get("summary") or ""),
          str(job.get("summary")))
    triage_dir = os.path.join(ops, "jobs", triage_id)
    check("分诊产物齐全",
          os.path.exists(os.path.join(triage_dir, "result.json"))
          and os.path.exists(os.path.join(triage_dir, "thumbs", "160.png")))
    code, result = get(port, "/api/file?path=" + urllib.request.quote(
        "工作区/运维/jobs/%s/result.json" % triage_id))
    check("分诊结果可读且带 model",
          code == 200 and (result.get("model") or {}).get("route") == "OCR")
    code, _ = get(port, "/api/file?path=" + urllib.request.quote(
        "工作区/运维/jobs/%s/thumbs/001.png" % triage_id))
    check("缩略图可下", code == 200)
    triage_argv = ((detail or {}).get("commands") or [{}])[0]["argv"]
    check("分诊 argv 用契约命令",
          triage_argv[1].endswith("ops_pdf_triage.py")
          and triage_argv[triage_argv.index("--uid") + 1] == "B0001"
          and triage_argv[triage_argv.index("--out") + 1] ==
          "工作区/运维/jobs/" + triage_id, " ".join(triage_argv[2:]))

    # ---- PDF 确认 --------------------------------------------------
    code, body = post(port, "/api/pdf/confirm",
                      {"uid": "B0001", "route": "OCR", "reason": "人工看有字迹",
                       "verdict": "文字书",
                       "model": {"route": "OCR", "confidence": 0.86, "reason": "有字迹"}})
    confirm_id = body.get("job_id")
    check("确认返回 batch", code == 200 and str(body.get("batch", "")).startswith(
        "pdf-ocr-ops-"), str(body.get("batch")))
    detail = wait_job(port, confirm_id, ("succeeded", "failed"))
    check("确认任务成功", (detail or {}).get("job", {}).get("state") == "succeeded",
          str((detail or {}).get("job", {}).get("summary")))
    review_rel = body.get("review") or ""
    review = json.load(open(os.path.join(root, review_rel), encoding="utf-8"))
    check("review.json 形状对",
          review.get("uid") == "B0001" and review.get("route") == "OCR"
          and (review.get("model") or {}).get("confidence") == 0.86)
    code, body = post(port, "/api/pdf/confirm", {"uid": "B0001", "route": "瞎写"})
    check("非法 route 被拒", code == 400)

    # ---- PDF 转 md -------------------------------------------------
    batch_dir = os.path.join(root, "工作区", "批次")
    triage_batch = [n for n in os.listdir(batch_dir) if n.startswith("pdf-ocr-ops-")][0]
    with open(os.path.join(batch_dir, triage_batch, "triage_final.csv"), "w",
              encoding="utf-8") as fh:
        fh.write("uid,title,subject1,subject2,class,route,pages\n")
        fh.write("B0001,书 一,文学,,C 扫描件,OCR,30\n")
        fh.write("B0002,书 二,文学,,C 扫描件,不转-记图册,20\n")
    code, body = post(port, "/api/pdf/run", {"uids": ["B0001"], "workers": 1})
    pdf_id = body.get("job_id")
    check("PDF 运行按 route 分组",
          code == 200 and (body.get("routes") or {}).get("ocr") == 1)
    detail = wait_job(port, pdf_id, ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("PDF 成功读统计", job.get("state") == "succeeded"
          and "落地" in (job.get("summary") or ""), str(job.get("summary")))
    check("PDF 步骤接重建", [s["name"] for s in (detail or {}).get("steps", [])][-2:]
          == ["build_library", "gen_book_catalog"])
    argv = ((detail or {}).get("commands") or [{}])[0]["argv"]
    check("PDF argv 带 --triage 与新批次",
          "--route" in argv and argv[argv.index("--route") + 1] == "ocr"
          and argv[argv.index("--batch") + 1] == triage_batch, " ".join(argv[2:]))
    code, body = post(port, "/api/pdf/run", {"uids": ["B0002"]})
    check("没有可转 route 时拒绝", code == 400, str(body.get("error")))

    # ---- 收尾 ------------------------------------------------------
    code, body = post(port, "/api/maintain", {"steps": ["verify", "p1", "catalog"]})
    maintain_id = body.get("job_id")
    detail = wait_job(port, maintain_id, ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("收尾门槛全过", job.get("state") == "succeeded", str(job.get("summary")))
    check("收尾按既定顺序",
          [s["name"] for s in (detail or {}).get("steps", [])] == ["verify", "p1",
                                                                   "catalog"])
    open(os.path.join(root, ".verify-bad"), "w").close()
    code, body = post(port, "/api/maintain", {"steps": ["verify"]})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("验收不过判失败", job.get("state") == "failed"
          and "verify" in (job.get("summary") or ""), str(job.get("summary")))
    os.remove(os.path.join(root, ".verify-bad"))
    code, body = post(port, "/api/maintain", {"steps": ["nope"]})
    check("不认识的步骤被拒", code == 400)

    # ---- 删书 ------------------------------------------------------
    code, body = post(port, "/api/delete/dry-run", {"uid": "B0002", "reason": "重复"})
    dry_id = body.get("job_id")
    detail = wait_job(port, dry_id, ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("删书预览成功", job.get("state") == "succeeded", str(job.get("summary")))
    code, preview = get(port, "/api/file?path=" + urllib.request.quote(
        "工作区/运维/jobs/%s/dry-run.json" % dry_id))
    check("预览 JSON 落盘",
          code == 200 and preview.get("plan_hash") == "hash-B0002"
          and preview.get("sources"), str(preview.get("plan_hash")))
    code, body = post(port, "/api/delete/apply",
                      {"uid": "B0002", "reason": "重复", "plan_hash": "hash-B0002",
                       "confirm_uid": "B0003"})
    check("uid 不一致拒绝应用", code == 400)
    code, body = post(port, "/api/delete/apply",
                      {"uid": "B0002", "reason": "重复", "plan_hash": "bad",
                       "confirm_uid": "B0002"})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("plan_hash 不一致判失败", job.get("state") == "failed"
          and "plan_hash" in (job.get("summary") or ""), str(job.get("summary")))
    code, body = post(port, "/api/delete/apply",
                      {"uid": "B0002", "reason": "重复", "plan_hash": "hash-B0002",
                       "confirm_uid": "B0002"})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("删书应用成功", job.get("state") == "succeeded", str(job.get("summary")))
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("删书 argv 带 --plan-hash 与 --apply",
          "--plan-hash" in argv and "--apply" in argv and "--dry-run" not in argv)

    # ---- 重跑与清理 ------------------------------------------------
    code, body = post(port, "/api/jobs/%s/rerun" % scan_id)
    check("重跑开新任务", code == 200 and body.get("job_id") not in (None, scan_id))
    code, body = post(port, "/api/jobs/clean")
    check("清理接口可用", code == 200 and "removed" in body)
    code, _ = get(port, "/api/jobs/nope")
    check("未知任务 404", code == 404)

    # ---- 搜索入口 --------------------------------------------------
    code, health = get(port, "/api/search/health")
    check("搜索健康点可查",
          code == 200 and isinstance(health.get("running"), bool)
          and "8765" in (health.get("url") or ""),
          "running=%s" % health.get("running"))
    code, body = post(port, "/api/search/start")
    if health.get("running"):
        check("已在跑时启动幂等", code == 200 and body.get("ok") is True
              and body.get("already") is True)
    else:
        check("演示页不在时报错",
              code == 200 and body.get("ok") is False
              and "演示页" in (body.get("error") or ""), str(body.get("error")))

    # ---- 状态脚本优先级 --------------------------------------------
    with open(os.path.join(root, "工作区", "脚本", "ops_status.py"), "w",
              encoding="utf-8") as fh:
        fh.write(STUB_STATUS)
    code, body = get(port, "/api/status?refresh=1")
    check("有脚本时用脚本",
          body.get("source") == "script" and body["status"].get("books") == 42,
          "source=%s books=%s" % (body.get("source"),
                                  (body.get("status") or {}).get("books")))


def adoption_checks(root, fixture):
    """重启接管：PID 对得上的继续 running，对不上标 orphaned，两个都不杀进程。"""
    ops_db = os.path.join(root, "工作区", "运维", "ops.db")
    sleeper = subprocess.Popen(["sleep", "60"], start_new_session=True)
    script = os.path.join(root, "工作区", "脚本", "split_md_batch.py")
    slow = os.path.join(root, ".slow-split")
    open(slow, "w").close()
    argv = [sys.executable, script, "--batch", "adopt-probe", "--uids", "B0001"]
    matched = subprocess.Popen(argv, cwd=root, start_new_session=True,
                               stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.8)
        ensure_ops_db(ops_db).close()
        conn = sqlite3.connect(ops_db)
        for job_id, pid, cmd_argv in (
                ("adopt-miss", sleeper.pid, ["/bin/echo", "/nope/not-ours.py"]),
                ("adopt-hit", matched.pid, argv)):
            conn.execute(
                "INSERT OR REPLACE INTO job (id, kind, title, argv, state, pid,"
                " created, started, ended, exit_code, summary, log_path, batch_dir,"
                " plan_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "split.run", "接管探针", json.dumps(cmd_argv), "running", pid,
                 time.strftime("%Y-%m-%dT%H:%M:%S"), time.strftime("%Y-%m-%dT%H:%M:%S"),
                 None, None, "", "工作区/运维/jobs/%s/log.txt" % job_id, None, None))
        conn.commit()
        conn.close()
        proc, port = start(root, fixture)
        try:
            code, body = get(port, "/api/jobs/adopt-miss")
            job = body.get("job") or {}
            check("PID 对不上标孤儿", job.get("state") == "orphaned",
                  str(job.get("summary")))
            code, body = get(port, "/api/jobs/adopt-hit")
            job = body.get("job") or {}
            check("PID 对得上继续 running",
                  job.get("state") == "running" and "接管" in (job.get("summary") or ""),
                  "%s · %s" % (job.get("state"), job.get("summary")))
            check("接管没杀进程", sleeper.poll() is None and matched.poll() is None)
            code, body = post(port, "/api/jobs/adopt-hit/cancel")
            check("接管的活任务能停", code == 200, str(body.get("error")))
            deadline, state = time.time() + 30, None
            while time.time() < deadline:
                code, body = get(port, "/api/jobs/adopt-hit")
                state = (body.get("job") or {}).get("state")
                if state in ("cancelled", "failed", "orphaned"):
                    break
                time.sleep(0.3)
            check("停止后状态是 cancelled", state == "cancelled", str(state))
            check("停止真的退了进程", wait_exit(matched, 10))
        finally:
            stop(proc)
    finally:
        for proc_ in (sleeper, matched):
            if proc_.poll() is None:
                try:
                    os.killpg(os.getpgid(proc_.pid), 9)
                except OSError:
                    proc_.kill()
        if os.path.exists(slow):
            os.remove(slow)


def wait_exit(proc, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    sys.exit(main())
