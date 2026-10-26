#!/usr/bin/env python3
"""统一操作台验收：随机端口、临时根、stub 脚本，不碰真库与真模型。

  python3 工作区/运维/验收/check_ops.py [-v]

做这些事：起服务，看 /api/status 与静态页；跑任务生命周期（queued → running →
判门槛）、写任务互斥与取消、上传、文件白名单与路径穿越、stub 导入与 PDF 分诊、
拆章的单本重拆/重新检查/查看问题、页面状态词与驱动状态表对齐、
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
only = opt("--only")
uids = [u for u in (opt("--uids") or "").split(",") if u]
if only and only not in uids:
    uids.append(only)
with open(".split-args.json", "w", encoding="utf-8") as fh:
    json.dump({"force": "--force" in args, "recheck": "--recheck" in args,
               "only": only, "argv": args}, fh, ensure_ascii=False)
print("批次 %s，%d 本" % (batch, len(uids)))
books, pending = [], []
for u in uids:
    if u == "B0005":
        books.append({"uid": u, "title": "书 " + u, "status": "staged",
                      "note": "暂存区已有产物，本次没重拆；读数是上次留下的"})
        pending.append(u)
        continue
    books.append({"uid": u, "title": "书 " + u, "status": "landed", "verify_rc": 0,
                  "audit_rc": 0, "audit_ratio": 0.99, "chapters": 12})
counts = {}
for b in books:
    counts[b["status"]] = counts.get(b["status"], 0) + 1
print(json.dumps({"batch": batch, "status_counts": counts, "books": books,
                  "pending_review": pending}, ensure_ascii=False))
'''

STUB_REVIEW = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
batch, uid, out = args[0], args[1], opt("--json")
rows = [] if uid == "B0009" else [{
    "uid": uid, "title": "书 " + uid, "status": "staged-review", "format": "epub",
    "chapters": 12, "ratio": 0.808, "files": 13, "text_chars": 250000,
    "image_path_chars": 1200, "dup_shingles": 2, "self_links": 1,
    "excluded": {"卷首": 1, "卷尾": 2},
    "verify": {"identical": ["identical content a.md b.md"]},
    "uncovered": [[120, "text/p003.xhtml"], [80, "text/p009.xhtml"],
                  [40, "text/p012.xhtml"]],
}]
if out:
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    os.replace(out + ".tmp", out)
print("核对 %s %s：%s 本" % (batch, uid, len(rows)))
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
import json, os, sys, time
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
uid, out = opt("--uid"), opt("--out")
time.sleep(float(os.environ.get("STUB_TRIAGE_SLEEP") or "0"))
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
import csv, os, sys
args = sys.argv[1:]
opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
triage, out_dir = opt("--triage"), opt("--out-dir")
rows = list(csv.DictReader(open(triage, encoding="utf-8"))) if triage else []
path = os.path.join(out_dir, "triage_final.csv") if out_dir else ""
old = list(csv.DictReader(open(path, encoding="utf-8"))) \
    if path and os.path.isfile(path) else []
if rows and path:
    fresh = {r["uid"]: r for r in rows}
    cols = list(old[0].keys()) if old else list(rows[0].keys())
    keep = [r for r in old if r.get("uid") not in fresh] + list(fresh.values())
    for r in keep:
        for c in cols:
            r.setdefault(c, "")
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(keep)
    os.replace(tmp, path)
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
    "md_review_report.py": STUB_REVIEW,
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
    "audit": {"fail": 0, "warn": 2, "at": "2026-10-24T21:30:00+08:00",
              "items": ["WARN 缺 README 的 md 1 本: B0002", "warn: 批次在跑"]},
    "ocr": {"batch": "pdf-ocr-2026-10-19", "remaining_books": 0,
            "remaining_pages": 0, "quota": "20000/天"},
    "jobs": {"running": 0, "awaiting_review": 0, "last": None},
}


def build_root():
    root = tempfile.mkdtemp(prefix="ops-check-")
    for rel in ("工作区/运维/jobs", "工作区/脚本", "工作区/批次", "目录",
                "库/_收件箱", "视图/奖项", "视图/主题"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    os.makedirs(os.path.join(root, "工作区", ".venv-pdf", "bin"), exist_ok=True)
    os.symlink(sys.executable,
               os.path.join(root, "工作区", ".venv-pdf", "bin", "python"))
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
    led = os.path.join(root, "工作区", "批次", "pdf-普查-2026-10-19")
    os.makedirs(led, exist_ok=True)
    with open(os.path.join(led, "triage_final.csv"), "w", encoding="utf-8") as fh:
        fh.write("uid,title,subject1,subject2,class,route,pages\n")
        fh.write("B0002,书 二,人文社科,,C 扫描件,不转-记图册,22\n")
        fh.write("B0003,书 三,艺术与图像,,A 图册,不转-记图册,33\n")
    with open(os.path.join(led, "skip_list.tsv"), "w", encoding="utf-8") as fh:
        fh.write("uid\ttitle\tsubject1\tsubject2\tpages\tmb\timage_share\twhy"
                 "\tverdict\troute_metric\n")
        fh.write("B0002\t书 二\t人文社科\t\t22\t1.0\t0.9\t图片占比 90% 超过一半"
                 "\t图片书\t图册不转\n")
        fh.write("B0003\t书 三\t艺术与图像\t\t33\t2.0\t0.9\t设定集与美术集"
                 "\t图片书\t图册不转\n")
    ocr = os.path.join(root, "工作区", "批次", "pdf-ocr-2026-10-19")
    os.makedirs(os.path.join(ocr, "logs"), exist_ok=True)
    with open(os.path.join(ocr, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write("uid\ttitle\tsrc\tformat\tpages\tclass\n")
        fh.write("B9001\t已落地的一本\t库/x/source/a.pdf\tpdf\t40\tC 扫描件\n")
        fh.write("B9002\t失败的一本\t库/x/source/b.pdf\tpdf\t60\tC 扫描件\n")
        fh.write("B9003\t没开跑的一本\t库/x/source/c.pdf\tpdf\t80\tC 扫描件\n")
    for uid, status in (("B9001", "landed"), ("B9002", "api-fatal")):
        with open(os.path.join(ocr, "logs", uid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"uid": uid, "status": status, "pages": 10}, fh)
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


def server_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ops_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def server_schema():
    return server_module().SCHEMA


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
    conn.execute("INSERT INTO book VALUES ('B0003','书 三','某人','艺术与图像','',"
                 "'艺术与图像/书 三','','','keep','active')")
    conn.execute("INSERT INTO source VALUES ('B0003','库/艺术与图像/书 三/source/c.pdf',"
                 "'pdf',33,'cc',1)")
    conn.execute("INSERT INTO book VALUES ('B0004','书 四','某人','文学','小说',"
                 "'文学/小说/书 四','','','keep','active')")
    conn.execute("INSERT INTO source VALUES ('B0004','库/文学/小说/书 四/source/d.pdf',"
                 "'pdf',44,'dd',1)")
    conn.execute("INSERT INTO book VALUES ('B0005','书 五','某人','文学','小说',"
                 "'文学/小说/书 五','','','keep','active')")
    conn.execute("INSERT INTO source VALUES ('B0005','库/文学/小说/书 五/source/e.pdf',"
                 "'pdf',55,'ee',1)")
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


def split_flags(root):
    """stub 驱动把自己收到的参数写在这里，证明服务端真是这么发的。"""
    path = os.path.join(root, ".split-args.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def clear_split_flags(root):
    try:
        os.remove(os.path.join(root, ".split-args.json"))
    except OSError:
        pass


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
        adoption_resume_check(adopt_root, adopt_fixture)
    finally:
        if args.keep:
            print("保留临时根：%s" % adopt_root)
        else:
            shutil.rmtree(adopt_root, ignore_errors=True)
    word_coverage_checks()
    return finish()


def finish():
    print("%d 项检查，失败 %d 项" % (len(CHECKS), len(FAILS)))
    if FAILS:
        for name in FAILS:
            print("  失败：" + name)
        return 1
    print("ALL PASS")
    return 0


def word_coverage_checks():
    """驱动加了新状态，页面必须也认识：两张表对不上时页面会直接打出英文。"""
    driver = os.path.join(os.path.dirname(os.path.dirname(OPS_DIR)), "工作区", "脚本",
                          "split_md_batch.py")
    page = os.path.join(OPS_DIR, "web", "app.js")
    dblock = re.search(r"^STATUS = \{(.*?)^\}",
                       open(driver, encoding="utf-8").read(), re.S | re.M)
    status = set(re.findall(r'"([A-Za-z][\w-]*)"\s*:', dblock.group(1))) if dblock else set()
    wblock = re.search(r"const SPLIT_WORD = \{(.*?)\n\};",
                       open(page, encoding="utf-8").read(), re.S)
    words = set(re.findall(r"['\"]?([A-Za-z][\w-]*)['\"]?\s*:\s*'",
                           wblock.group(1))) if wblock else set()
    missing = sorted(status - words)
    check("页面认得每个拆章状态", bool(status) and not missing,
          "驱动 %d 个状态，页面缺 %s" % (len(status), "、".join(missing) or "无"))


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
    check("首页渲染", code == 200 and "书库操作台" in html, "len=%d" % len(html or ""))
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
    os.makedirs(os.path.join(root, "工作区", "批次", slow_batch, "logs"), exist_ok=True)
    code, body = post(port, "/api/split/inspect", {"uid": "B0001", "batch": slow_batch})
    check("查看问题不进写任务入口", code == 200 and body.get("job_id"),
          str(body.get("error")))
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

    os.makedirs(os.path.join(root, "工作区", "批次", slow_batch, "logs"), exist_ok=True)
    clear_split_flags(root)
    code, body = post(port, "/api/split/run",
                      {"uids": ["B0004"], "only": "B0004", "batch": slow_batch,
                       "workers": 1})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("单本重跑带 --only 且复用批次",
          code == 200 and "--only" in argv and argv[argv.index("--only") + 1] == "B0004"
          and argv[argv.index("--batch") + 1] == slow_batch, " ".join(argv[2:]))
    flags = split_flags(root)
    check("单本重跑真的重拆",
          flags.get("force") is True and flags.get("only") == "B0004"
          and "--force" in (flags.get("argv") or []), str(flags))

    clear_split_flags(root)
    code, body = post(port, "/api/split/recheck", {"uid": "B0004", "batch": slow_batch})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    flags = split_flags(root)
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("重新检查只复核不重拆",
          code == 200 and flags.get("recheck") is True and flags.get("only") == "B0004"
          and flags.get("force") is False and "--recheck" in argv
          and "--force" not in argv, str(flags))
    check("重新检查读完统计判完成",
          job.get("state") == "succeeded" and "待复核" not in (job.get("summary") or ""),
          "%s · %s" % (job.get("state"), job.get("summary")))

    code, body = post(port, "/api/split/run",
                      {"uids": ["B0005"], "only": "B0005", "batch": slow_batch,
                       "workers": 1})
    detail = wait_job(port, body.get("job_id"), ("awaiting_review", "succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("暂存区有产物时停在待复核",
          job.get("state") == "awaiting_review" and "待复核" in (job.get("summary") or ""),
          "%s · %s" % (job.get("state"), job.get("summary")))

    code, body = post(port, "/api/split/inspect", {"uid": "B0004", "batch": slow_batch})
    inspect_id = body.get("job_id")
    detail = wait_job(port, inspect_id, ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("查看问题给出审计比与未收录页",
          job.get("state") == "succeeded" and "审计比" in (job.get("summary") or "")
          and "未收录" in (job.get("summary") or ""),
          "%s · %s" % (job.get("state"), job.get("summary")))
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("查看问题 argv 用契约命令",
          len(argv) == 6 and argv[1].endswith("md_review_report.py")
          and argv[2] == slow_batch and argv[3] == "B0004" and argv[4] == "--json"
          and argv[5] == "工作区/运维/jobs/%s/result.json" % inspect_id,
          " ".join(argv[2:]))
    code, rows = get(port, "/api/file?path=" + urllib.request.quote(
        "工作区/运维/jobs/%s/result.json" % inspect_id))
    check("核对证据能从文件接口读回",
          code == 200 and isinstance(rows, list) and rows[0].get("uid") == "B0004"
          and rows[0].get("dup_shingles") == 2, "code=%s" % code)
    code, body = post(port, "/api/split/inspect", {"uid": "B0009", "batch": slow_batch})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    job = (detail or {}).get("job") or {}
    check("没有证据时查看问题判失败", job.get("state") == "failed"
          and "暂存区" in (job.get("summary") or ""), str(job.get("summary")))
    for route in ("/api/split/inspect", "/api/split/recheck"):
        code, body = post(port, route, {"uid": "B0004", "batch": "nope-20260101"})
        check("批次不存在时 %s 回 404" % route, code == 404, str(body.get("error")))

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
        fh.write("B0005,书 五,文学,,C 扫描件,OCR,40\n")
        fh.write("B0002,书 二,文学,,C 扫描件,不转-记图册,20\n")
    code, body = post(port, "/api/pdf/run", {"uids": ["B0005"], "workers": 1})
    pdf_id = body.get("job_id")
    check("PDF 运行按 route 分组",
          code == 200 and (body.get("routes") or {}).get("ocr") == 1, str(body))
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
    venv_py = os.path.join(root, "工作区", ".venv-pdf", "bin", "python")
    check("PDF 转写用 .venv-pdf 的解释器",
          os.path.abspath(argv[0]) == venv_py, argv[0])
    cpath = os.path.join(root, "工作区", "运维", "jobs", pdf_id, "commands.json")
    stale = json.load(open(cpath, encoding="utf-8"))
    stale[0]["argv"][0] = sys.executable
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(stale, fh, ensure_ascii=False)
    deadline = time.time() + 20
    while True:
        code, body = post(port, "/api/jobs/%s/rerun" % pdf_id)
        if code == 200 or time.time() > deadline:
            break
        time.sleep(0.2)
    rerun_id = body.get("job_id")
    check("PDF 重跑开新任务", code == 200 and rerun_id not in (None, pdf_id), str(body))
    detail = wait_job(port, rerun_id, ("succeeded", "failed"))
    rerun_argv = ((detail or {}).get("commands") or [{}])[0]["argv"]
    check("PDF 重跑也用 .venv-pdf 的解释器",
          os.path.abspath(rerun_argv[0]) == venv_py, rerun_argv[0])
    try:
        os.remove(venv_py)
        code, body = post(port, "/api/pdf/run", {"uids": ["B0005"], "workers": 1})
        detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    finally:
        os.symlink(sys.executable, venv_py)
    job = (detail or {}).get("job") or {}
    check("缺 PDF 解释器时日志写明，不抛服务端异常",
          job.get("state") == "failed" and "解释器不存在" in str((detail or {}).get("log_tail")),
          str(job.get("summary")))
    code, body = post(port, "/api/pdf/run", {"uids": ["B0002"]})
    check("没有可转 route 时拒绝", code == 400, str(body.get("error")))

    # ---- 收尾摘要 ---------------------------------------------------
    mod = server_module()
    env = mod.Env(root)
    gid = "gate-pdf-skip"
    os.makedirs(env.job_dir(gid), exist_ok=True)
    with open(os.path.join(env.job_dir(gid), "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"landed": 0, "remaining_books": 0, "remaining_pages": 0,
                   "status_counts": {"skipped-has-md": 3}}, fh)
    state, summary = mod.gate_pdf(mod.Ctx(env, {"id": gid}, [], [("pdf ocr", 0)]))
    check("空转那次的摘要写明是已有 md 跳过",
          state == "succeeded" and "落地 0 本" in summary and "3 本已有 md 跳过" in summary,
          "%s · %s" % (state, summary))

    # ---- 待转 PDF 清单 ---------------------------------------------
    code, body = get(port, "/api/pdf/books")
    books = (body or {}).get("books") or []
    counts = (body or {}).get("counts") or {}
    check("待转清单给 books 与四类计数",
          code == 200 and isinstance(books, list)
          and set(counts) == {"convert", "gallery", "ja", "untriaged"},
          "code=%s 本数=%d 计数=%s" % (code, len(books), counts))
    row = next((r for r in books if r.get("uid") == "B0003"), {})
    check("不转清单里的书处置判为只记图册", row.get("disposition") == "gallery",
          "%s → %s" % (row.get("uid"), row.get("disposition")))
    code, body = post(port, "/api/pdf/run", {"uids": ["B0003"]})
    check("只记图册未二次确认就不转",
          code == 400 and "只记图册" in str(body.get("error")), str(body.get("error")))
    code, body = post(port, "/api/pdf/run", {"uids": ["B0003"], "force": True})
    detail = wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    commands = (detail or {}).get("commands") or []
    names = [c.get("name") for c in commands]
    batch_argv = next((c.get("argv") or [] for c in commands
                       if len(c.get("argv") or []) > 1
                       and str(c["argv"][1]).endswith("pdf_md_batch.py")), [])
    review = os.path.join(root, "工作区", "批次", body.get("batch") or "", "review",
                          "B0003.json")
    check("二次确认后先改判再按扫描识别转",
          code == 200 and body.get("forced") == 1 and names[:1] == ["apply_review"]
          and (detail or {}).get("job", {}).get("state") == "succeeded"
          and batch_argv
          and batch_argv[batch_argv.index("--route") + 1] == "ocr"
          and "B0003" in batch_argv[batch_argv.index("--uids") + 1].split(",")
          and str(body.get("batch") or "").startswith("pdf-ocr-ops-")
          and os.path.isfile(review),
          "步骤=%s 判定=%s 批次=%s" % (names, (detail or {}).get("job", {}).get("summary"),
                                  body.get("batch")))
    code, body = post(port, "/api/pdf/run", {"uids": ["B0004"]})
    triage_id = (body.get("triage_jobs") or [None])[0]
    detail = wait_job(port, triage_id, ("awaiting_review", "failed")) if triage_id else None
    argv = (((detail or {}).get("commands") or [{}])[0] or {}).get("argv") or []
    check("未分诊的书只送分诊不建转写任务",
          code == 200 and body.get("job_id") is None and body.get("triaged") == 1
          and len(body.get("triage_jobs") or []) == 1
          and argv[argv.index("--uid") + 1] == "B0004", str(body))

    # ---- 批量分诊与改处置 -------------------------------------------
    code, body = get(port, "/api/pdf/books")
    untriaged = [r["uid"] for r in (body.get("books") or [])
                 if r.get("disposition") == "untriaged"]
    target = untriaged[0] if untriaged else ""
    check("清单里还有未分诊的书", bool(untriaged), str(untriaged))
    code, body = post(port, "/api/pdf/triage-batch", {})
    check("已经有了建议的书，默认不再重跑分诊", code == 400
          and "建议" in str(body.get("error")), str(body.get("error")))
    code, body = post(port, "/api/pdf/triage-batch", {"uids": untriaged})
    batch_id = body.get("job_id")
    detail = wait_job(port, batch_id, ("awaiting_review", "failed"))
    steps = (detail or {}).get("steps") or []
    argvs = [c.get("argv") or [] for c in ((detail or {}).get("commands") or [])]
    check("批量分诊按未分诊本数出步，每步 --out 落在自己的目录",
          code == 200 and len(steps) == len(untriaged) and bool(argvs)
          and all("--out" in argv_one
                  and argv_one[argv_one.index("--out") + 1].endswith("/" + uid)
                  for argv_one, uid in zip(argvs, untriaged)),
          "步数=%d 未分诊=%s" % (len(steps), untriaged))
    job = (detail or {}).get("job") or {}
    summary = job.get("summary") or ""
    check("批量分诊停在待确认，摘要写本数与建议",
          job.get("state") == "awaiting_review" and "本" in summary
          and "建议" in summary, str(summary))
    code, body = get(port, "/api/pdf/books")
    row = next((r for r in (body.get("books") or []) if r.get("uid") == target), {})
    suggestion = row.get("suggestion") or {}
    check("未分诊的行带建议，并指向出建议的那个任务",
          suggestion.get("route") == "OCR" and row.get("suggest_job") == batch_id,
          "%s 建议=%s 任务=%s" % (target, suggestion, row.get("suggest_job")))
    ops_db = os.path.join(root, "工作区", "运维", "ops.db")
    set_job_state(ops_db, batch_id, "failed")
    code, body = get(port, "/api/pdf/books")
    row = next((r for r in (body.get("books") or []) if r.get("uid") == target), {})
    suggestion = row.get("suggestion") or {}
    check("任务后来判失败，已落盘的建议仍然算",
          suggestion.get("route") == "OCR" and row.get("suggest_job") == batch_id,
          "%s 建议=%s 来自=%s 期望=%s" % (target, suggestion.get("route"),
                                     row.get("suggest_job"), batch_id))
    job_csv = os.path.join(ops, "jobs", batch_id, target, "triage.csv")
    with open(job_csv, "w", encoding="utf-8") as fh:
        fh.write("uid,title,subject1,subject2,class,route,pages,mb,ppi,text_page_share,"
                 "ink,bands,note,verdict,image_share,measure,route_metric\n")
        fh.write("%s,书 %s,文学,,C 扫描件 批次标记,OCR,320,1.0,,0.0,0.42,2,stub,,"
                 "1.0,stub,OCR\n" % (target, target))
    code, body = post(port, "/api/pdf/settle",
                      {"items": [{"uid": target, "route": "OCR"}]})
    wait_job(port, body.get("job_id"), ("succeeded", "failed"))
    review_path = os.path.join(root, "工作区", "批次", body.get("batch") or "",
                               "review", target + ".json")
    review = json.load(open(review_path, encoding="utf-8")) \
        if os.path.isfile(review_path) else {}
    batch_csv = os.path.join(root, "工作区", "批次", body.get("batch") or "",
                             "triage.csv")
    line = next((ln for ln in open(batch_csv, encoding="utf-8")
                 if ln.split(",", 1)[0] == target), "") \
        if os.path.isfile(batch_csv) else ""
    check("失败任务的分诊行还是改处置的底表",
          (review.get("model") or {}).get("route") == "OCR"
          and "批次标记" in line,
          "review=%s 行=%s" % ((review.get("model") or {}).get("route"),
                               line.strip()[:80]))
    set_job_state(ops_db, batch_id, "awaiting_review")
    code, body = post(port, "/api/pdf/settle",
                      {"items": [{"uid": target, "route": "本地抽文本"}]})
    settle_id = body.get("job_id")
    detail = wait_job(port, settle_id, ("succeeded", "failed"))
    review_path = os.path.join(root, "工作区", "批次", body.get("batch") or "",
                              "review", target + ".json")
    review = json.load(open(review_path, encoding="utf-8")) \
        if os.path.isfile(review_path) else {}
    settle_steps = [s["name"] for s in ((detail or {}).get("steps") or [])]
    check("改处置先写 review，再跑 apply_review",
          code == 200 and settle_steps[:1] == ["apply_review"]
          and review.get("route") == "本地抽文本"
          and (detail or {}).get("job", {}).get("state") == "succeeded",
          "步骤=%s route=%s" % (settle_steps, review.get("route")))
    code, body = get(port, "/api/pdf/books")
    row = next((r for r in (body.get("books") or []) if r.get("uid") == target), {})
    check("改处置之后那本书算可转", row.get("disposition") == "convert",
          "%s → %s" % (target, row.get("disposition")))
    code, body = post(port, "/api/pdf/settle",
                      {"items": [{"uid": target, "route": "人工确认"}]})
    check("人工确认不算一个处置，改处置拒绝它", code == 400, str(body.get("error")))
    code, body = get(port, "/api/pdf/books")
    today = (body or {}).get("today") or {}
    check("清单给今天的额度", today.get("cap") == 20000
          and isinstance(today.get("used"), int), str(today))
    code, body = get(port, "/api/pdf/queue")
    part = ((body or {}).get("batch") or {}).get("partition") or {}
    check("旧批次给落地与未落地的分解",
          part.get("landed") == 1 and part.get("failed") == 1
          and part.get("staged") == 0 and part.get("untouched") == 1
          and part.get("left") == 2, str(part))

    # ---- 跨日的分诊表 -----------------------------------------------
    today_name = mod.TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d")
    today_final = os.path.join(root, "工作区", "批次", today_name, "triage_final.csv")
    with open(today_final, encoding="utf-8") as fh:
        kept = [ln for ln in fh if not ln.startswith(target + ",")]
    with open(today_final, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    old_name = mod.TRIAGE_BATCH_PREFIX + time.strftime(
        "%Y-%m-%d", time.localtime(time.time() - 86400))
    old_dir = os.path.join(root, "工作区", "批次", old_name)
    os.makedirs(old_dir, exist_ok=True)
    with open(os.path.join(old_dir, "triage_final.csv"), "w", encoding="utf-8") as fh:
        fh.write("uid,title,subject1,subject2,class,route,pages\n")
        fh.write("%s,书 %s,文学,,C 扫描件,OCR,320\n" % (target, target))
    code, body = post(port, "/api/pdf/run", {"uids": [target], "workers": 1})
    detail = (wait_job(port, body.get("job_id"), ("succeeded", "failed"))
              if body.get("job_id") else None)
    names = [c.get("name") for c in ((detail or {}).get("commands") or [])]
    argv = next((c.get("argv") or [] for c in ((detail or {}).get("commands") or [])
                 if str((c.get("argv") or ["", ""])[1]).endswith("pdf_md_batch.py")), [])
    check("昨天的分诊行今天照样能转",
          code == 200 and body.get("carried") == 1 and names[:1] == ["apply_review"]
          and "--uids" in argv and target in argv[argv.index("--uids") + 1].split(","),
          "%s 步骤=%s" % (body, names))

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


def adoption_resume_check(root, fixture):
    """接管后续跑：跑到一半那一步记 unknown，剩下的步骤接着跑，拿不到退出码就不冒充成功。"""
    ops_db = os.path.join(root, "工作区", "运维", "ops.db")
    scripts = os.path.join(root, "工作区", "脚本")
    probe = os.path.join(scripts, "adopt-probe.py")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("import time\ntime.sleep(2)\n")
    finish = os.path.join(scripts, "adopt-finish.py")
    with open(finish, "w", encoding="utf-8") as fh:
        fh.write("import os\n"
                 "open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"
                 " '.adopt-ran'), 'w').close()\n")
    job_id = "adopt-resume"
    job_dir = os.path.join(root, "工作区", "运维", "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    commands = [
        {"name": "pdf ocr", "script": "adopt-probe.py",
         "argv": [sys.executable, probe], "marker": None, "capture": None},
        {"name": "pdf text", "script": "adopt-finish.py",
         "argv": [sys.executable, finish], "marker": None, "capture": None}]
    with open(os.path.join(job_dir, "commands.json"), "w", encoding="utf-8") as fh:
        json.dump(commands, fh, ensure_ascii=False)
    child = subprocess.Popen(commands[0]["argv"], cwd=root, start_new_session=True,
                             stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        conn = ensure_ops_db(ops_db)
        conn.execute(
            "INSERT OR REPLACE INTO job (id, kind, title, argv, state, pid,"
            " created, started, ended, exit_code, summary, log_path, batch_dir,"
            " plan_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "pdf.confirm", "接管续跑探针",
             json.dumps(commands[0]["argv"]), "running", child.pid, stamp, stamp,
             None, None, "", "工作区/运维/jobs/%s/log.txt" % job_id, None, None))
        for seq, name, state in ((0, "pdf ocr", "running"), (1, "pdf text", "pending")):
            conn.execute("INSERT OR REPLACE INTO step (job_id, seq, name, state,"
                         " detail, started) VALUES (?,?,?,?,?,?)",
                         (job_id, seq, name, state, None, stamp))
        conn.commit()
        conn.close()
        proc, port = start(root, fixture)
        try:
            detail = wait_job(port, job_id,
                              ("succeeded", "failed", "awaiting_review"), timeout=40)
            job = (detail or {}).get("job") or {}
            steps = {s["seq"]: s for s in (detail or {}).get("steps") or []}
            check("接管来的那一步记 unknown",
                  (steps.get(0) or {}).get("state") == "unknown"
                  and "退出码" in ((steps.get(0) or {}).get("detail") or ""),
                  str((steps.get(0) or {}).get("state")))
            check("接管后剩下的步骤接着跑",
                  (steps.get(1) or {}).get("state") == "ok"
                  and os.path.isfile(os.path.join(scripts, ".adopt-ran")),
                  str((steps.get(1) or {}).get("state")))
            check("退出码拿不到时不冒充成功",
                  job.get("state") == "awaiting_review"
                  and "接管" in (job.get("summary") or "")
                  and "退出码" in (job.get("summary") or ""), str(job.get("summary")))
            check("接管续跑写进日志",
                  "[接管]" in ((detail or {}).get("log_tail") or ""))
        finally:
            stop(proc)
    finally:
        if child.poll() is None:
            try:
                os.killpg(os.getpgid(child.pid), 9)
            except OSError:
                child.kill()
        for name in ("adopt-probe.py", "adopt-finish.py", ".adopt-ran"):
            path = os.path.join(scripts, name)
            if os.path.exists(path):
                os.remove(path)


def set_job_state(ops_db, job_id, state):
    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE job SET state=? WHERE id=?", (state, job_id))
    conn.commit()
    conn.close()


def wait_exit(proc, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    sys.exit(main())
