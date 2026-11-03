#!/usr/bin/env python3
"""split_md_batch.py 验收：批次范围账本、--report-json、land() 旧 md 归档。

全部在 tempfile 临时根里跑，临时 sqlite 只建 book/source/derived 与 v_pending_md 这
几个被驱动读的表视图。不跑转换器、不调模型、不碰真库。PI_BIN 指到 stub，万一走到
agent 修复也只会拿到伪 JSON。

用法: python3 tests/acceptance/ops/test_split.py
"""
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OPS = os.path.dirname(HERE)
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(OPS)), "src", "bookcorpus", "scripts")
SCRIPT = os.path.join(SCRIPTS, "split_md_batch.py")
STUB = os.path.join(os.path.dirname(HERE), "fixtures", "pi_stub.py")
sys.path.insert(0, SCRIPTS)
import split_md_batch as S  # noqa: E402

SCHEMA = """
CREATE TABLE book(
  uid TEXT PRIMARY KEY, title TEXT NOT NULL, title_from_file TEXT,
  author TEXT, translator TEXT, publisher TEXT, language TEXT,
  subject1 TEXT NOT NULL, subject2 TEXT,
  path TEXT NOT NULL, path_state TEXT NOT NULL DEFAULT 'proposed'
    CHECK (path_state IN ('proposed','active')),
  series TEXT, volume TEXT, container TEXT, html_pages INT,
  retention TEXT NOT NULL DEFAULT 'keep'
    CHECK (retention IN ('keep','review','drop')),
  notes TEXT);
CREATE TABLE source(
  uid TEXT NOT NULL REFERENCES book(uid), file TEXT, format TEXT, bytes INT,
  sha256 TEXT, origin TEXT, batch TEXT, primary_src INT DEFAULT 0);
CREATE TABLE derived(
  uid TEXT, kind TEXT CHECK (kind IN ('md','html','ocr')), status TEXT
    CHECK (status IN ('none','partial','done','failed')),
  method TEXT, tool_version TEXT, source_sha256 TEXT, chapters INT, bytes INT,
  generated_at TEXT, manifest TEXT, PRIMARY KEY (uid, kind));
CREATE VIEW v_pending_md AS
  SELECT b.uid, b.subject1, b.subject2, b.title, s.format, s.sha256
  FROM book b JOIN source s ON s.uid = b.uid AND s.primary_src = 1
  LEFT JOIN derived d ON d.uid = b.uid AND d.kind = 'md'
  WHERE d.uid IS NULL OR (d.status != 'done')
     OR (d.source_sha256 IS NOT NULL AND d.source_sha256 != s.sha256);
"""

# 拆章批次实际会遇到的四种书：正常待拆、待拆但格式不是电子书、已有 md、路径未定
BOOKS = [
    ("B0001", "epub", "active", None),
    ("B0002", "mobi", "active", None),
    ("B0003", "pdf", "active", None),
    ("B0004", "epub", "active", "done"),
    ("B0005", "epub", "proposed", None),
    ("B0010", "azw3", "active", None),
]


def sha(uid):
    return "sha-" + uid


def add_book(con, uid, fmt, path_state, md):
    con.execute("INSERT INTO book(uid,title,subject1,path,path_state,retention)"
                " VALUES (?,?,?,?,?,'keep')",
                (uid, "书" + uid, "文学", uid, path_state))
    con.execute("INSERT INTO source(uid,file,format,bytes,sha256,primary_src)"
                " VALUES (?,?,?,?,?,1)",
                (uid, "books/%s/source/书%s.%s" % (uid, uid, fmt), fmt, 1024, sha(uid)))
    if md:
        con.execute("INSERT INTO derived(uid,kind,status,source_sha256)"
                    " VALUES (?,?,?,?)", (uid, "md", md, sha(uid)))


def make_db(root):
    os.makedirs(os.path.join(root, "build"), exist_ok=True)
    con = sqlite3.connect(os.path.join(root, "build", "library.db"))
    con.executescript(SCHEMA)
    for uid, fmt, state, md in BOOKS:
        add_book(con, uid, fmt, state, md)
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def make_root():
    root = tempfile.mkdtemp(prefix="split-test-")
    os.makedirs(os.path.join(root, "工作区", "批次"))
    os.makedirs(os.path.join(root, "books"))
    return root


def batch_dir(root, name):
    p = os.path.join(root, "工作区", "批次", name)
    os.makedirs(p, exist_ok=True)
    return p


def read_tsv(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@contextlib.contextmanager
def patched(**attrs):
    old = {k: getattr(S.L, k) for k in attrs}
    for k, v in attrs.items():
        setattr(S.L, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(S.L, k, v)


def test_pending_worklist():
    root = make_root()
    try:
        con = make_db(root)
        b = batch_dir(root, "md拆分-2026-10-18")
        rows = S.build_worklist(con, b)
        uids = [r["uid"] for r in rows]
        assert uids == ["B0001", "B0002", "B0010"], uids
        text = read_tsv(os.path.join(b, "worklist.tsv"))
        assert text.splitlines()[0] == "uid\tformat\tsha256\tpath\ttitle\tsrc", text
        assert len(text.strip().splitlines()) == 4, text
        assert "pdf" not in text and "B0004" not in text and "B0005" not in text, text
        assert S.read_worklist(os.path.join(b, "worklist.tsv"))[0]["title"] == "书B0001"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_uids_worklist():
    root = make_root()
    try:
        con = make_db(root)
        b = batch_dir(root, "resplit-ops-20261024T2200")
        rows = S.build_worklist(con, b, ["B0004", "B0001", "B0004"])
        assert [r["uid"] for r in rows] == ["B0004", "B0001"], rows
        assert rows[0]["title"] == "书B0004", rows
        lines = read_tsv(os.path.join(b, "worklist.tsv")).strip().splitlines()
        assert [ln.split("\t")[0] for ln in lines[1:]] == ["B0004", "B0001"], lines
        assert lines[0] == "uid\tformat\tsha256\tpath\ttitle\tsrc", lines[0]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_existing_worklist_wins():
    root = make_root()
    try:
        con = make_db(root)
        b = batch_dir(root, "split-ops-20261024T2201")
        S.build_worklist(con, b, ["B0001"])
        path = os.path.join(b, "worklist.tsv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("uid\tformat\tsha256\tpath\ttitle\tsrc\nB9999\tepub\ts\t文学/手加\t手加\tx\n")
        before = read_tsv(path)
        st = os.stat(path).st_mtime_ns
        rows = S.build_worklist(con, b, ["B0002", "B0010"])
        assert [r["uid"] for r in rows] == ["B9999"], rows
        assert read_tsv(path) == before, "已有 worklist 被改写"
        assert os.stat(path).st_mtime_ns == st, "已有 worklist 被重写"
        rows = S.build_worklist(con, b)
        assert [r["uid"] for r in rows] == ["B9999"], rows
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_source_rejected():
    root = make_root()
    try:
        con = make_db(root)
        b = batch_dir(root, "split-ops-20261024T2202")
        try:
            S.build_worklist(con, b, ["B0003", "B0404"])
        except ValueError as exc:
            msg = str(exc)
        else:
            raise AssertionError("缺源文件时没报错")
        assert "B0003" in msg and "B0404" in msg, msg
        assert not os.path.exists(os.path.join(b, "worklist.tsv")), "失败时仍写了 worklist"
    finally:
        shutil.rmtree(root, ignore_errors=True)


FAKE_LOGS = {
    "B0001": {"uid": "B0001", "title": "书B0001", "status": "landed",
              "dest": "books/B0001/md", "verify_rc": 0, "audit_rc": 0,
              "audit_ratio": 0.98, "chapters": 12, "chars": 250000},
    "B0002": {"uid": "B0002", "title": "书B0002", "status": "staged-review",
              "dest": "books/B0002/md", "verify_rc": 1, "audit_rc": 0,
              "note": "verify rc=1, audit rc=0", "fix": [{"rc": 1, "ms": 5}]},
    "B0003": {"uid": "B0003", "title": "书B0003", "status": "replaced",
              "dest": "books/B0003/md", "previous": "工作区/批次/x/out/B0003.prev-2026",
              "verify_rc": 0, "audit_rc": 0, "audit_ratio": 1.02},
}


def test_report_json_cli():
    root = make_root()
    try:
        make_db(root).close()
        name = "split-ops-20261024T2203"
        logs = os.path.join(batch_dir(root, name), "logs")
        os.makedirs(logs)
        for uid, res in FAKE_LOGS.items():
            with open(os.path.join(logs, uid + ".json"), "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False)
        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import split_md_batch as S\n"
            "S.L.ROOT = %r\nS.L.WORK = %r\nS.L.CAT = %r\n"
            "sys.argv = ['split_md_batch.py', '--batch', %r, '--report', '--report-json']\n"
            "sys.exit(S.main())\n"
        ) % (SCRIPTS, root, os.path.join(root, "工作区"), os.path.join(root, "目录"), name)
        env = dict(os.environ, PI_BIN=STUB, PI_STUB_JSON="[]")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=env, cwd=root)
        assert p.returncode == 0, p.stderr
        out = p.stdout.rstrip("\n").splitlines()
        data = json.loads(out[-1])
        assert "状态统计" in p.stdout, p.stdout
        assert data["batch"] == name, data
        assert data["status_counts"] == {"landed": 1, "replaced": 1, "staged-review": 1}, data
        assert data["pending_review"] == ["B0002"], data
        books = {b["uid"]: b for b in data["books"]}
        assert set(books) == {"B0001", "B0002", "B0003"}, books
        assert books["B0002"]["fix"] == 1 and books["B0002"]["verify_rc"] == 1, books["B0002"]
        assert books["B0003"]["previous"] == "工作区/批次/x/out/B0003.prev-2026", books["B0003"]
        assert books["B0001"]["dest"] == "books/B0001/md", books["B0001"]
        assert books["B0001"]["fix"] == 0, books["B0001"]
        assert [b["uid"] for b in data["books"]] == ["B0001", "B0002", "B0003"], data["books"]
        # 文本报告不能被 --report-json 顶掉
        assert out[-1].startswith("{"), out[-3:]
        assert "--report-json" not in p.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def stage_and_dest(root, uid="B0001"):
    b = batch_dir(root, "md拆分-2026-10-18")
    stage = os.path.join(b, "out", uid)
    dest = os.path.join(root, "books", uid, "md")
    return b, stage, dest


def test_status_table_drives_done_status():
    expect = tuple(name for name, done in S.STATUS.items() if done)
    assert S.DONE_STATUS == expect, S.DONE_STATUS
    assert set(S.DONE_STATUS) == {"landed", "replaced", "accepted",
                                 "skipped-has-md", "staged-green"}, S.DONE_STATUS
    for name in ("staged", "staged-review", "no-stage", "split-failed",
                 "convert-failed", "error"):
        assert name not in S.DONE_STATUS, name
        assert S.STATUS[name] is False, name


def test_write_result_rejects_unregistered_status():
    root = make_root()
    try:
        logs = os.path.join(batch_dir(root, "split-ops-probe"), "logs")
        os.makedirs(logs)
        ctx = {"logs": logs, "stamp": "20261024T221000+0800"}
        S.write_result(ctx, {"uid": "B0001", "status": "landed"})
        saved = json.load(open(os.path.join(logs, "B0001.json"), encoding="utf-8"))
        assert saved["status"] == "landed", saved
        try:
            S.write_result(ctx, {"uid": "B0002", "status": "半成品"})
        except AssertionError as exc:
            assert "半成品" in str(exc), exc
        else:
            raise AssertionError("未登记状态没抛 AssertionError")
        assert not os.path.exists(os.path.join(logs, "B0002.json")), "抛错前已落盘"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_previous_readings_prefers_log_then_manifest():
    root = make_root()
    try:
        b, stage, _ = stage_and_dest(root)
        os.makedirs(stage)
        with open(os.path.join(stage, ".manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"chapters": 9, "chars": 1200, "images": 4,
                       "audit_ratio": 0.7, "problems": 2}, fh)
        logs = os.path.join(b, "logs")
        os.makedirs(logs)
        prev = os.path.join(logs, "B0001.json")
        with open(prev, "w", encoding="utf-8") as fh:
            json.dump({"uid": "B0001", "status": "staged", "verify_rc": 1,
                       "audit_rc": 0, "audit_ratio": 0.81, "chapters": 11}, fh)
        got = S.previous_readings(stage, prev)
        assert got == {"verify_rc": 1, "audit_rc": 0, "audit_ratio": 0.81,
                       "chapters": 11, "chars": 1200, "images": 4,
                       "problems": 2}, got
        # 日志不在时只剩 manifest，读数为空的字段不出现
        got = S.previous_readings(stage, os.path.join(logs, "B0099.json"))
        assert got == {"audit_ratio": 0.7, "chapters": 9, "chars": 1200,
                       "images": 4, "problems": 2}, got
        # 两边都没有时不报错
        assert S.previous_readings(os.path.join(b, "out", "B0099"),
                                   os.path.join(logs, "B0099.json")) == {}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_summary_counts_staged_as_pending():
    ctx = {"batch_name": "split-ops-20261024T2204"}
    results = [
        {"uid": "B0001", "title": "书B0001", "status": "landed",
         "verify_rc": 0, "audit_rc": 0, "audit_ratio": 0.99},
        {"uid": "B0002", "title": "书B0002", "status": "staged",
         "note": "暂存区已有产物，本次没重拆；读数是上次留下的"},
        {"uid": "B0003", "title": "书B0003", "status": "no-stage",
         "note": "暂存区没有产物，先重拆"},
    ]
    data = S.summary(ctx, results)
    assert data["pending_review"] == ["B0002", "B0003"], data
    assert data["status_counts"] == {"landed": 1, "no-stage": 1, "staged": 1}, data
    assert [b["uid"] for b in data["books"]] == ["B0001", "B0002", "B0003"], data


def test_recheck_and_force_conflict():
    p = subprocess.run([sys.executable, SCRIPT, "--batch", "split-ops-probe",
                        "--only", "B0001", "--recheck", "--force"],
                       capture_output=True, text=True, cwd=HERE)
    assert p.returncode != 0, "--recheck 与 --force 同时给时没报错"
    msg = p.stdout + p.stderr
    assert "不能同时用" in msg, msg


def test_recheck_without_stage_says_no_stage():
    root = make_root()
    try:
        con = make_db(root)
        log_dir = os.path.join(root, "工作区", "批次", "split-ops-20261024T2205", "logs")
        os.makedirs(log_dir)
        codes = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import split_md_batch as S\n"
            "S.L.ROOT = %r\nS.L.WORK = %r\nS.L.CAT = %r\n"
            "row = {'uid': 'B0001', 'title': '书B0001', 'format': 'epub',\n"
            "       'sha256': 'sha-B0001', 'path': '文学/书B0001',\n"
            "       'src': 'books/B0001/source/书B0001.epub'}\n"
            "ctx = {'batch': %r, 'batch_name': 'split-ops-20261024T2205',\n"
            "       'out': %r, 'tmp': %r, 'logs': %r, 'land': True,\n"
            "       'force': False, 'replace': False, 'recheck': True,\n"
            "       'stamp': '20261024T220500+0800', 'tool_version': 'sha256:probe'}\n"
            "print(S.run_one(row, ctx)['status'])\n"
        ) % (SCRIPTS, root, os.path.join(root, "工作区"), os.path.join(root, "目录"),
             os.path.join(root, "工作区", "批次", "split-ops-20261024T2205"),
             os.path.join(root, "工作区", "批次", "split-ops-20261024T2205", "out"),
             os.path.join(root, "工作区", "批次", "split-ops-20261024T2205", "tmp"),
             log_dir)
        env = dict(os.environ, PI_BIN=STUB, PI_STUB_JSON="[]")
        p = subprocess.run([sys.executable, "-c", codes], capture_output=True, text=True,
                           env=env, cwd=root)
        con.close()
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == "no-stage", p.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def recheck_env(root, uid="B0001", fmt="epub"):
    """一个能真跑 recheck 的最小 ctx：暂存区有产物，manifest 记着上一次的读数。"""
    b, stage, dest = stage_and_dest(root, uid)
    os.makedirs(stage, exist_ok=True)
    with open(os.path.join(stage, "第一章.md"), "w", encoding="utf-8") as fh:
        fh.write("正文")
    with open(os.path.join(stage, ".manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"uid": uid, "chapters": 1, "chars": 10, "audit_ratio": 0.5,
                   "audit_chars": 20, "verified": False}, fh)
    for name in ("out", "tmp", "logs"):
        os.makedirs(os.path.join(b, name), exist_ok=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    row = {"uid": uid, "title": "书" + uid, "format": fmt, "sha256": sha(uid),
           "path": "文学/书" + uid,
           "src": "books/%s/source/书%s.%s" % (uid, uid, fmt)}
    ctx = {"batch": b, "batch_name": "split-ops-recheck",
           "out": os.path.join(b, "out"), "tmp": os.path.join(b, "tmp"),
           "logs": os.path.join(b, "logs"), "land": True, "force": False,
           "replace": False, "recheck": True,
           "stamp": "20261024T221500+0800", "tool_version": "sha256:probe"}
    return b, stage, dest, row, ctx


def fake_tool(root, name, body):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import sys\n%s\n" % body)
    return path


VERIFY_OK = ("print('markdown    : 1 chapters')\n"
             "print('characters  : 1,000')\n"
             "print('images used : 0 (on disk: 0)')\n"
             "print('problems    : 0')")
AUDIT_OK = "print('  out      950 / 1,000   95.0%    1 md   ')"


def test_recheck_only_lands_after_audit_passes():
    root = make_root()
    old = (S.VERIFY, S.AUDIT, S.CALIBRE)
    try:
        b, stage, dest, row, ctx = recheck_env(root)
        verify = fake_tool(root, "verify.py", VERIFY_OK)
        audit_bad = fake_tool(root, "audit-bad.py", AUDIT_OK + "\nsys.exit(1)")
        audit_ok = fake_tool(root, "audit-ok.py", AUDIT_OK)
        with patched(ROOT=root):
            S.VERIFY, S.AUDIT = verify, audit_bad
            first = S.recheck(row, ctx, {"uid": "B0001", "title": "书B0001",
                                        "dest": "books/B0001/md"})
            assert first["status"] == "staged-review", first
            assert first["verify_rc"] == 0 and first["audit_rc"] == 1, first
            assert not os.path.isdir(dest), "内容检查没过就进了库"
            man = json.load(open(os.path.join(stage, ".manifest.json"), encoding="utf-8"))
            assert man["verified"] is False, man

            S.AUDIT = audit_ok
            second = S.recheck(row, ctx, {"uid": "B0001", "title": "书B0001",
                                         "dest": "books/B0001/md"})
            assert second["status"] == "landed", second
            assert "第一章.md" in os.listdir(dest), os.listdir(dest)
            man = json.load(open(os.path.join(dest, ".manifest.json"), encoding="utf-8"))
            assert man["verified"] is True and man["audit_ratio"] == 0.95, man
            log = open(os.path.join(b, "logs", "B0001.log"), encoding="utf-8").read()
            assert "---- verify ----" in log and "---- audit ----" in log, log
    finally:
        S.VERIFY, S.AUDIT, S.CALIBRE = old
        shutil.rmtree(root, ignore_errors=True)


def test_recheck_without_epub_never_lands():
    root = make_root()
    old = (S.VERIFY, S.AUDIT, S.CALIBRE)
    try:
        b, stage, dest, row, ctx = recheck_env(root, uid="B0010", fmt="mobi")
        verify = fake_tool(root, "verify.py", VERIFY_OK)
        audit = fake_tool(root, "audit-ok.py", AUDIT_OK)
        with patched(ROOT=root):
            S.VERIFY, S.AUDIT = verify, audit
            S.CALIBRE = os.path.join(root, "no-such-calibre")
            got = S.recheck(row, ctx, {"uid": "B0010", "title": "书B0010",
                                       "dest": "books/B0010/md"})
            assert got["status"] == "staged-review", got
            assert got["audit_rc"] is None, got
            assert not os.path.isdir(dest), "没跑内容检查就进了库"
            assert "转不出" in got["note"], got
            assert got["audit_ratio"] == 0.5, got
    finally:
        S.VERIFY, S.AUDIT, S.CALIBRE = old
        shutil.rmtree(root, ignore_errors=True)


def test_land_archives_old_md():
    root = make_root()
    try:
        b, stage, dest = stage_and_dest(root)
        os.makedirs(stage)
        os.makedirs(dest)
        with open(os.path.join(stage, "新章.md"), "w", encoding="utf-8") as fh:
            fh.write("新")
        with open(os.path.join(dest, "旧章.md"), "w", encoding="utf-8") as fh:
            fh.write("旧")
        ctx = {"stamp": "20261024T220000+0800", "out": os.path.join(b, "out")}
        result = {"uid": "B0001", "title": "书B0001",
                  "dest": os.path.relpath(dest, root)}
        with patched(ROOT=root):
            S.land(ctx, stage, dest, result)
        assert result["status"] == "replaced", result
        prev = stage + ".prev-20261024T220000+0800"
        assert os.path.isdir(prev), prev
        assert os.path.isfile(os.path.join(prev, "旧章.md")), os.listdir(prev)
        assert os.listdir(dest) == ["新章.md"], os.listdir(dest)
        assert not os.path.exists(stage), "暂存目录没搬走"
        assert result["previous"] == os.path.relpath(prev, root), result["previous"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_land_without_old_md():
    root = make_root()
    try:
        b, stage, dest = stage_and_dest(root)
        os.makedirs(stage)
        os.makedirs(os.path.dirname(dest))
        with open(os.path.join(stage, "第一章.md"), "w", encoding="utf-8") as fh:
            fh.write("正文")
        ctx = {"stamp": "20261024T220500+0800", "out": os.path.join(b, "out")}
        result = {"uid": "B0001", "title": "书B0001", "dest": os.path.relpath(dest, root)}
        with patched(ROOT=root):
            S.land(ctx, stage, dest, result)
        assert result["status"] == "landed", result
        assert "previous" not in result, result
        assert os.listdir(dest) == ["第一章.md"], os.listdir(dest)
        assert [n for n in os.listdir(os.path.join(b, "out")) if ".prev-" in n] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001 - 单项报错不掩住其余项
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
        else:
            print("ok   %s" % fn.__name__)
    print("\n%d 项，失败 %d 项" % (len(tests), failed))
    print("ALL PASS" if failed == 0 else "FAILED")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
