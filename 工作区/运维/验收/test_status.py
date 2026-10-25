#!/usr/bin/env python3
"""ops_status.py 与 audit_state.py --json 的验收：临时根 fixture + 真库只读。

  python3 工作区/运维/验收/test_status.py     # 末行 ALL PASS

fixture 造一个能过 audit_state 的三本小库：两本有 md，一本待转 pdf，索引只覆盖一本，
好让「索引落后」有非零读数。真库部分只读打开 library.db 与 index.db，不跑模型、不重建索引。
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(REPO, "工作区", "脚本")
OPS_STATUS = os.path.join(SCRIPTS, "ops_status.py")
AUDIT = os.path.join(SCRIPTS, "audit_state.py")
BACKUP_AUDIT = os.path.join(REPO, "工作区", "运维", "backup-2026-10-24", "audit_state.py")

LIB_SCHEMA = """
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
  uid TEXT NOT NULL, file TEXT, format TEXT, bytes INT,
  sha256 TEXT, origin TEXT, batch TEXT, primary_src INT DEFAULT 0);
CREATE TABLE derived(
  uid TEXT, kind TEXT, status TEXT, method TEXT, tool_version TEXT,
  source_sha256 TEXT, chapters INT, bytes INT, generated_at TEXT, manifest TEXT,
  PRIMARY KEY (uid, kind));
CREATE TABLE award(name TEXT PRIMARY KEY, books INT DEFAULT 0);
CREATE TABLE book_award(uid TEXT, award TEXT, year TEXT, edition TEXT, result TEXT);
CREATE VIEW v_pending_md AS
  SELECT b.uid, b.subject1, b.subject2, b.title, s.format, s.sha256
  FROM book b JOIN source s ON s.uid = b.uid AND s.primary_src = 1
  LEFT JOIN derived d ON d.uid = b.uid AND d.kind = 'md'
  WHERE d.uid IS NULL OR (d.status != 'done')
     OR (d.source_sha256 IS NOT NULL AND d.source_sha256 != s.sha256);
"""

INDEX_SCHEMA = """
CREATE TABLE para(id INTEGER PRIMARY KEY, path TEXT NOT NULL, book TEXT NOT NULL,
  line INTEGER NOT NULL, chars INTEGER NOT NULL, tok TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE book_concept(book TEXT NOT NULL, key TEXT NOT NULL, hits INTEGER NOT NULL,
  book_chars INTEGER NOT NULL, score REAL NOT NULL, PRIMARY KEY(book, key));
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE expand(path TEXT, kind TEXT, tok TEXT, text TEXT, model TEXT,
  prompt_hash TEXT, tokens_in INT, tokens_out INT, built_at TEXT);
CREATE TABLE expand_fts(path TEXT, tok TEXT, text TEXT);
"""

OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, argv TEXT NOT NULL,
  state TEXT NOT NULL, pid INTEGER, created TEXT NOT NULL, started TEXT, ended TEXT,
  exit_code INTEGER, summary TEXT, log_path TEXT, batch_dir TEXT, plan_hash TEXT);
"""

BOOKS = [
    ("B0001", "甲书", "文学/甲书", "a.epub", "epub", 2),
    ("B0002", "乙书", "文学/乙书", "b.epub", "epub", 2),
    ("B0003", "丙书", "人文社科/丙书", "c.pdf", "pdf", 0),
    ("B0004", "图册", "其他/图册", "d.epub", "epub", 0),
]
EMPTY_MD = {"B0004"}

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("PASS  " + name)
    else:
        FAILS.append(name)
        print("FAIL  %s%s" % (name, (" - " + str(detail)) if detail else ""))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run(args, root=None):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          cwd=root or REPO)


def tree_state(root, skip=()):
    """整棵树的 (相对路径, 字节数, mtime_ns, sha256)，用来证明只读。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            fp = os.path.join(dirpath, name)
            rel = os.path.relpath(fp, root)
            if rel in skip or os.path.islink(fp):
                continue
            st = os.stat(fp)
            with open(fp, "rb") as fh:
                out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest())
    return out


def build_root(root):
    cat = os.path.join(root, "目录")
    lib = os.path.join(root, "库")
    for uid, title, rel, src, fmt, chapters in BOOKS:
        book = os.path.join(lib, rel)
        write(os.path.join(book, "book.json"),
              json.dumps({"uid": uid, "title": title, "path": rel}, ensure_ascii=False))
        write(os.path.join(book, "source", src), "源文件 %s\n" % uid)
        for i in range(1, chapters + 1):
            write(os.path.join(book, "md", "%02d.md" % i), "# %s 第 %d 章\n正文。\n" % (title, i))
        if chapters:
            write(os.path.join(book, "md", "README.md"), "# %s\n\n- [第1章](01.md)\n" % title)
        elif uid in EMPTY_MD:
            write(os.path.join(book, "md", "README.md"), "# %s\n\n图片书，不索引。\n" % title)
    write(os.path.join(lib, "_收件箱", "新书.epub"), "epub\n")

    libdb = os.path.join(cat, "library.db")
    os.makedirs(cat, exist_ok=True)
    con = sqlite3.connect(libdb)
    con.executescript(LIB_SCHEMA)
    for uid, title, rel, src, fmt, chapters in BOOKS:
        con.execute("INSERT INTO book(uid,title,subject1,path,path_state) VALUES(?,?,?,?,?)",
                    (uid, title, rel.split("/")[0], rel, "active"))
        con.execute("INSERT INTO source(uid,file,format,bytes,sha256,primary_src) "
                    "VALUES(?,?,?,?,?,1)",
                    (uid, "库/%s/source/%s" % (rel, src), fmt, 12, "sha-" + uid))
        if chapters or uid in EMPTY_MD:
            con.execute("INSERT INTO derived(uid,kind,status,chapters,bytes) "
                        "VALUES(?,?,?,?,?)", (uid, "md", "done", chapters, 100))
    con.commit()
    con.close()

    idx = os.path.join(cat, "检索", "index.db")
    os.makedirs(os.path.dirname(idx), exist_ok=True)
    con = sqlite3.connect(idx)
    con.executescript(INDEX_SCHEMA)
    for key, value in (("built_at", "2026-10-24T18:35:38+0800"), ("books", "1"),
                       ("chapters", "2"), ("paras", "2")):
        con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, value))
    for i in (1, 2):
        path = "库/文学/甲书/md/%02d.md" % i
        con.execute("INSERT INTO para(path,book,line,chars,tok,text) VALUES(?,?,?,?,?,?)",
                    (path, "文学/甲书", 1, 4, "正文", "正文。"))
        con.execute("INSERT INTO expand(path,kind,tok,text,built_at) VALUES(?,?,?,?,?)",
                    (path, "prefix", "正文", "正文。", "2026-10-24T18:35:38+0800"))
        con.execute("INSERT INTO expand_fts(path,tok,text) VALUES(?,?,?)",
                    (path, "正文", "正文。"))
    con.commit()
    con.close()

    write(os.path.join(cat, "分类表.csv"),
          "uid,一级,二级,作者层,保留,正式书名,备注\n"
          "B0001,文学,,0,keep,甲书,\nB0002,文学,,0,keep,乙书,\n"
          "B0003,人文社科,,0,keep,丙书,\nB0004,其他,,0,keep,图册,图片书，不索引\n")
    write(os.path.join(cat, "对照表.csv"),
          "旧路径,新路径,uid\n旧/甲,库/文学/甲书,B0001\n旧/乙,库/文学/乙书,B0002\n"
          "旧/丙,库/人文社科/丙书,B0003\n旧/图册,库/其他/图册,B0004\n")
    write(os.path.join(cat, "书目总表.csv"),
          "uid,书名\nB0001,甲书\nB0002,乙书\nB0003,丙书\nB0004,图册\n")

    views = os.path.join(root, "视图")
    for sub, book, rel, link in (("奖项", "测试奖", "甲书", "文学/甲书"),
                                 ("主题", "测试主题", "乙书", "文学/乙书")):
        target = os.path.join(views, sub, book, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(os.path.join("..", "..", "..", "库", link), target)

    write(os.path.join(root, "工作区", "批次", "pdf-ocr-2026-10-19", "worklist.tsv"),
          "uid\ttitle\tpath\tsrc\tsha256\tformat\tpages\tclass\n"
          "B0003\t丙书\t人文社科/丙书\t库/人文社科/丙书/source/c.pdf\tsha-B0003\tpdf\t10\tC\n")
    os.makedirs(os.path.join(root, "工作区", "运维"), exist_ok=True)


def add_ops_db(root):
    db = os.path.join(root, "工作区", "运维", "ops.db")
    con = sqlite3.connect(db)
    con.executescript(OPS_SCHEMA)
    for jid, kind, state, created in (
            ("j1", "import", "succeeded", "2026-10-24T20:00:00+08:00"),
            ("j2", "split", "running", "2026-10-24T21:00:00+08:00"),
            ("j3", "pdf", "awaiting_review", "2026-10-24T22:00:00+08:00")):
        con.execute("INSERT INTO job(id,kind,title,argv,state,created,log_path) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (jid, kind, kind + " 任务", "[]", state, created, "jobs/%s/log.txt" % jid))
    con.commit()
    con.close()


def read_json(argv, root):
    proc = run(argv + ["--json"], root)
    try:
        return json.loads(proc.stdout), proc
    except ValueError:
        check("输出可解析 JSON", False, proc.stdout[:200] + proc.stderr[-300:])
        return None, proc


def fixture_checks(root):
    print("\n== fixture: 轻量状态 ==")
    before = tree_state(root)
    state, proc = read_json([OPS_STATUS, "--root", root], root)
    check("ops_status --json 退出码 0", proc.returncode == 0, proc.stderr[-300:])
    if state is None:
        return
    check("书 4 本", state["books"] == 4, state["books"])
    check("md 3 本 / 4 章 / 可索引 2 本",
          (state["md_done"], state["md_chapters"], state["md_indexable"]) == (3, 4, 2),
          (state["md_done"], state["md_chapters"], state["md_indexable"]))
    check("待转 pdf 1 本", state["pending"]["pdf"] == 1, state["pending"])
    check("待转 txt/djvu 0", state["pending"]["txt"] == 0 and state["pending"]["djvu"] == 0,
          state["pending"])
    check("收件箱 1 个文件", state["inbox"]["files"] == 1
          and state["inbox"]["items"][0]["name"] == "新书.epub", state["inbox"])
    check("人账 4 行", state["ledger"]["rows"] == 4, state["ledger"])
    check("人账 mtime 有值", bool(state["ledger"]["mtime"]), state["ledger"])
    check("书目表 mtime 有值", bool(state["catalog"]["mtime"]), state["catalog"])
    check("书目表未匹配 0", state["catalog"]["unmatched"] == 0, state["catalog"])
    check("索引 built_at/paras/chapters/books",
          (state["index"]["built_at"], state["index"]["paras"], state["index"]["chapters"],
           state["index"]["books"]) == ("2026-10-24T18:35:38+0800", 2, 2, 1),
          state["index"])
    check("索引落后 1 本 / 2 章（零章 md 不算落后）",
          (state["index"]["lag_books"], state["index"]["lag_chapters"],
           state["index"]["lag_exact"]) == (1, 2, False), state["index"])
    check("视图 奖项 1 / 主题 1 / 断链 0",
          (state["views"]["awards"], state["views"]["topics"],
           state["views"]["broken"]) == (1, 1, 0), state["views"])
    check("没审计缓存时 audit 全 0 且 at 为空",
          (state["audit"]["fail"], state["audit"]["warn"], state["audit"]["at"],
           state["audit"]["items"]) == (0, 0, None, []), state["audit"])
    check("OCR 批次与剩余 1 本 / 10 页",
          (state["ocr"]["batch"], state["ocr"]["remaining_books"],
           state["ocr"]["remaining_pages"]) == ("pdf-ocr-2026-10-19", 1, 10), state["ocr"])
    check("没 ops.db 时 jobs 全空",
          state["jobs"] == {"running": 0, "awaiting_review": 0, "last": None}, state["jobs"])
    check("轻量状态没报问题", state["problems"] == [], state["problems"])
    check("轻量状态没写盘", tree_state(root) == before,
          set(tree_state(root).items()) ^ set(before.items()))

    print("\n== fixture: ops.db 任务 ==")
    add_ops_db(root)
    state, _ = read_json([OPS_STATUS, "--root", root], root)
    if state:
        last = state["jobs"]["last"] or {}
        check("jobs running 1 / awaiting_review 1",
              (state["jobs"]["running"], state["jobs"]["awaiting_review"]) == (1, 1),
              state["jobs"])
        check("jobs.last 是最近一条", (last.get("id"), last.get("state")) == ("j3", "awaiting_review"),
              last)

    print("\n== fixture: --deep 审计 ==")
    before = tree_state(root, skip=(os.path.join("工作区", "运维", "last-audit.json"),))
    deep, proc = read_json([OPS_STATUS, "--root", root, "--deep"], root)
    check("--deep 退出码 0", proc.returncode == 0, proc.stderr[-300:])
    cache_path = os.path.join(root, "工作区", "运维", "last-audit.json")
    check("--deep 写了 last-audit.json", os.path.isfile(cache_path))
    if deep and os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        check("缓存记了根与 deep 标记",
              cache.get("root") == os.path.abspath(root) and cache.get("deep") is True,
              {k: cache.get(k) for k in ("root", "deep")})
        check("审计 FAIL 0", deep["audit"]["fail"] == 0, deep["audit"]["items"])
        check("审计 WARN 至少 1 项", deep["audit"]["warn"] >= 1, deep["audit"])
        check("审计 at 有值", bool(deep["audit"]["at"]), deep["audit"])
        check("审计 items 带 FAIL/WARN 前缀，条数与计数一致",
              len(deep["audit"]["items"]) == deep["audit"]["warn"] + deep["audit"]["fail"]
              and all(i.startswith(("FAIL ", "WARN ")) for i in deep["audit"]["items"]),
              deep["audit"]["items"])
        check("--deep 精确落后仍 1 本 / 2 章",
              (deep["index"]["lag_books"], deep["index"]["lag_chapters"],
               deep["index"]["lag_exact"]) == (1, 2, True), deep["index"])
        check("--deep 索引书数按倒排算（1 本 / 2 章 / 2 段）",
              (deep["index"]["books"], deep["index"]["chapters"],
               deep["index"]["paras"]) == (1, 2, 2), deep["index"])
        check("--deep 除 last-audit.json 外没写盘", tree_state(
            root, skip=(os.path.join("工作区", "运维", "last-audit.json"),)) == before)

    print("\n== fixture: 轻量读缓存 ==")
    light, _ = read_json([OPS_STATUS, "--root", root], root)
    if light and deep:
        check("轻量状态读回缓存里的审计",
              light["audit"] == deep["audit"], (light["audit"], deep["audit"]))
        check("轻量状态不改缓存读数", light["index"]["lag_exact"] is False)

    print("\n== audit_state --json ==")
    data, proc = read_json([AUDIT, "--root", root], root)
    check("audit_state --json 退出码 0", proc.returncode == 0, proc.stderr[-300:])
    check("audit_state --json 只有三段 JSON",
          isinstance(data, dict) and sorted(data) == ["fail", "ok", "warn"], data)
    if data:
        check("fixture 审计 FAIL 空", data["fail"] == [], data["fail"])
        check("PASS 行进了 ok 列表", len(data["ok"]) >= 5, len(data["ok"]))

    if os.path.isfile(BACKUP_AUDIT):
        old = os.path.join(root, "工作区", "脚本", "audit_state_before.py")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        shutil.copyfile(BACKUP_AUDIT, old)
        human_old = run([old], root)
        human_new = run([AUDIT, "--root", root], root)
        os.remove(old)
        check("人读输出与改前逐字节相同", human_old.stdout == human_new.stdout,
              len(human_old.stdout) - len(human_new.stdout))
    else:
        print("SKIP  人读输出对照：没有 backup-2026-10-24/audit_state.py")

    print("\n== fixture: 断链与退出码 ==")
    os.symlink(os.path.join("..", "..", "..", "库", "文学", "没有这本"),
               os.path.join(root, "视图", "主题", "测试主题", "坏链"))
    light, _ = read_json([OPS_STATUS, "--root", root], root)
    if light:
        check("断链计入 views.broken",
              (light["views"]["topics"], light["views"]["broken"]) == (2, 1), light["views"])
    proc = run([AUDIT, "--root", root, "--json"], root)
    check("有 FAIL 时 --json 退出码 1", proc.returncode == 1, proc.returncode)
    try:
        body = json.loads(proc.stdout)
    except ValueError:
        body = None
    check("有 FAIL 时 JSON 仍可解析且有内容",
          bool(body) and any("断链" in m for m in body["fail"]), proc.stdout[:200])
    human = run([AUDIT, "--root", root], root)
    check("有 FAIL 时人读输出保留", "FAIL  视图软链" in human.stdout, human.stdout[-300:])


def con_indexable(libdb):
    con = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    try:
        return con.execute("SELECT count(*) FROM derived WHERE kind='md' AND status='done' "
                           "AND coalesce(chapters, 0) > 0").fetchone()[0]
    finally:
        con.close()


def real_checks():
    print("\n== 真库：只读 ==")
    libdb = os.path.join(REPO, "目录", "library.db")
    indexdb = os.path.join(REPO, "目录", "检索", "index.db")
    watched = [libdb, indexdb, os.path.join(REPO, "目录", "分类表.csv"),
               os.path.join(REPO, "目录", "书目总表.csv"),
               os.path.join(REPO, "工作区", "运维", "last-audit.json")]
    before = {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in watched}
    state, proc = read_json([OPS_STATUS], REPO)
    check("真库 ops_status --json 成功", proc.returncode == 0 and state is not None,
          proc.stderr[-300:])
    if state is None:
        return
    con = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    books = con.execute("SELECT count(*) FROM book").fetchone()[0]
    md_done = con.execute(
        "SELECT count(*) FROM derived WHERE kind='md' AND status='done'").fetchone()[0]
    chapters = con.execute(
        "SELECT coalesce(sum(chapters),0) FROM derived WHERE kind='md'").fetchone()[0]
    pdf = con.execute("SELECT count(*) FROM v_pending_md WHERE lower(format)='pdf'").fetchone()[0]
    con.close()
    check("真库 books 与 library.db 一致", state["books"] == books, (state["books"], books))
    check("真库 md_done 与 derived 一致", state["md_done"] == md_done,
          (state["md_done"], md_done))
    check("真库 md 章数与 derived 一致", state["md_chapters"] == chapters,
          (state["md_chapters"], chapters))
    check("真库待转 pdf 与 v_pending_md 一致", state["pending"]["pdf"] == pdf,
          (state["pending"]["pdf"], pdf))
    idx = sqlite3.connect("file:%s?mode=ro" % indexdb, uri=True)
    meta = dict(idx.execute("SELECT key, value FROM meta"))
    idx.close()
    check("真库索引读数与 index.db meta 一致",
          (state["index"]["books"], state["index"]["chapters"], state["index"]["paras"])
          == (int(meta["books"]), int(meta["chapters"]), int(meta["paras"])), state["index"])
    indexable = con_indexable(libdb)
    check("真库可索引 md 本数 = md done 里章数 > 0 的",
          state["md_indexable"] == indexable, (state["md_indexable"], indexable))
    check("真库索引落后 = 可索引 md 本数 - 索引书数",
          state["index"]["lag_books"] == indexable - int(meta["books"]), state["index"])
    check("真库索引 built_at 有值", bool(state["index"]["built_at"]), state["index"])
    check("真库人账行数与分类表行数一致",
          state["ledger"]["rows"] == sum(
              1 for _ in open(os.path.join(REPO, "目录", "分类表.csv"), encoding="utf-8-sig"))
          - 1, state["ledger"])
    broken = total = 0
    for sub in ("奖项", "主题"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, "视图", sub)):
            for name in dirnames + filenames:
                fp = os.path.join(dirpath, name)
                if os.path.islink(fp):
                    total += 1
                    broken += 0 if os.path.exists(fp) else 1
    check("真库视图软链计数一致",
          state["views"]["awards"] + state["views"]["topics"] == total, state["views"])
    check("真库视图断链计数一致", state["views"]["broken"] == broken, state["views"])
    check("真库 OCR 批次有值且剩余页数非负",
          bool(state["ocr"]["batch"]) and state["ocr"]["remaining_pages"] >= 0, state["ocr"])
    check("真库跑完没报问题", state["problems"] == [], state["problems"])

    audit_json, proc = read_json([AUDIT, "--root", REPO], REPO)
    check("真库 audit_state --json 可解析", audit_json is not None, proc.stderr[-300:])
    if audit_json:
        check("真库审计 FAIL 为空", audit_json["fail"] == [], audit_json["fail"])
        check("真库审计 ok 非空", len(audit_json["ok"]) > 0, len(audit_json["ok"]))
    after = {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in watched}
    check("真库跑完账本与产物 mtime 未变", after == before,
          {os.path.basename(p): (before[p], after[p]) for p in watched if before[p] != after[p]})


def main():
    for path in (OPS_STATUS, AUDIT):
        if not os.path.isfile(path):
            print("缺 %s" % path)
            return 2
    root = tempfile.mkdtemp(prefix="ops-status-fixture-")
    try:
        build_root(root)
        fixture_checks(root)
    finally:
        shutil.rmtree(root)
    real_checks()
    print()
    if FAILS:
        print("FAILED %d 项: %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
