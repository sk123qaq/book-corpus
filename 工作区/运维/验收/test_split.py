#!/usr/bin/env python3
"""split_md_batch.py 验收：批次范围账本、--report-json、land() 旧 md 归档。

全部在 tempfile 临时根里跑，临时 sqlite 只建 book/source/derived 与 v_pending_md 这
几个被驱动读的表视图。不跑转换器、不调模型、不碰真库。PI_BIN 指到 stub，万一走到
agent 修复也只会拿到伪 JSON。

用法: python3 工作区/运维/验收/test_split.py
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
SCRIPTS = os.path.join(os.path.dirname(OPS), "脚本")
SCRIPT = os.path.join(SCRIPTS, "split_md_batch.py")
STUB = os.path.join(HERE, "fixtures", "pi_stub.py")
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
                (uid, "书" + uid, "文学", "文学/书" + uid, path_state))
    con.execute("INSERT INTO source(uid,file,format,bytes,sha256,primary_src)"
                " VALUES (?,?,?,?,?,1)",
                (uid, "库/文学/书%s/source/书%s.%s" % (uid, uid, fmt), fmt, 1024, sha(uid)))
    if md:
        con.execute("INSERT INTO derived(uid,kind,status,source_sha256)"
                    " VALUES (?,?,?,?)", (uid, "md", md, sha(uid)))


def make_db(root):
    cat = os.path.join(root, "目录")
    os.makedirs(cat, exist_ok=True)
    con = sqlite3.connect(os.path.join(cat, "library.db"))
    con.executescript(SCHEMA)
    for uid, fmt, state, md in BOOKS:
        add_book(con, uid, fmt, state, md)
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def make_root():
    root = tempfile.mkdtemp(prefix="split-test-")
    os.makedirs(os.path.join(root, "工作区", "批次"))
    os.makedirs(os.path.join(root, "库"))
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
              "dest": "库/文学/书B0001/md", "verify_rc": 0, "audit_rc": 0,
              "audit_ratio": 0.98, "chapters": 12, "chars": 250000},
    "B0002": {"uid": "B0002", "title": "书B0002", "status": "staged-review",
              "dest": "库/文学/书B0002/md", "verify_rc": 1, "audit_rc": 0,
              "note": "verify rc=1, audit rc=0", "fix": [{"rc": 1, "ms": 5}]},
    "B0003": {"uid": "B0003", "title": "书B0003", "status": "replaced",
              "dest": "库/文学/书B0003/md", "previous": "工作区/批次/x/out/B0003.prev-2026",
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
        assert books["B0001"]["dest"] == "库/文学/书B0001/md", books["B0001"]
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
    dest = os.path.join(root, "库", "文学", "书" + uid, "md")
    return b, stage, dest


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
