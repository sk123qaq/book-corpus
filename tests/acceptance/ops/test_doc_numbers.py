#!/usr/bin/env python3
"""doc_numbers.py 的验收：渲染格式、换块、补块、--check 退出码，以及 audit_state 的文档 WARN。

  python3 tests/acceptance/ops/test_doc_numbers.py     # 末行 ALL PASS

fixture 六本书：两本有 md，四本待拆章（pdf/txt/docx/azw3 各一），奖项记录两条、奖项名一个，
视图软链三条（奖项两条、主题一条）。全部读写都在 tempfile 里，真 AGENTS.md 只记指纹不写。
"""

import datetime
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
SCRIPTS = os.path.join(REPO, "src", "bookcorpus", "scripts")
DOC_NUMBERS = os.path.join(SCRIPTS, "doc_numbers.py")
AUDIT = os.path.join(SCRIPTS, "audit_state.py")

sys.path.insert(0, SCRIPTS)
import doc_numbers

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

BOOKS = [
    ("B0001", "甲书", "文学/甲书", "epub", 2),
    ("B0002", "乙书", "文学/乙书", "epub", 2),
    ("B0003", "丙书", "人文社科/丙书", "pdf", 0),
    ("B0004", "丁书", "人文社科/丁书", "txt", 0),
    ("B0005", "戊书", "其他/戊书", "docx", 0),
    ("B0006", "己书", "其他/己书", "azw3", 0),
]
MD_BOOKS = [b for b in BOOKS if b[4]]

STALE_BLOCK = (
    "<!-- numbers:start -->\n"
    "As of 2026-10-24：1,693 本书，1,571 本有 md，122 本待拆章（115 pdf、5 txt、2 docx）；"
    "1,009 条奖项记录，视图 1,009 条软链；坑账 6 类。\n"
    "<!-- numbers:end -->"
)

SPEC_COUNTS = {
    "books": 1779,
    "md": 1647,
    "pending_total": 132,
    "pending": {"pdf": 125, "txt": 5, "docx": 2, "other": 0},
    "awards": 1009,
    "award_names": 10,
    "view_total": 2066,
    "view": {"奖项": 1009, "主题": 1057},
    "pitfall_classes": 7,
    "as_of": "2026-10-26",
}
SPEC_BLOCK = (
    "<!-- numbers:start -->\n"
    "As of 2026-10-26：1,779 本书，1,647 本有 md，132 本待拆章（125 pdf、5 txt、2 docx）；"
    "1,009 条奖项记录，视图 2,066 条软链（奖项 1,009、主题 1,057）；坑账 7 类。\n"
    "<!-- numbers:end -->"
)

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


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run(args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def build_root(root):
    lib = os.path.join(root, "books")
    for uid, title, _rel, fmt, chapters in BOOKS:
        book = os.path.join(lib, uid)
        write(os.path.join(book, "book.json"), json.dumps({"uid": uid, "title": title}, ensure_ascii=False))
        write(os.path.join(book, "source", "书." + fmt), "源文件 %s\n" % uid)
        for i in range(1, chapters + 1):
            write(os.path.join(book, "md", "%02d.md" % i), "# %s 第 %d 章\n正文。\n" % (title, i))
        if chapters:
            write(os.path.join(book, "md", "README.md"), "# %s\n\n- [第1章](01.md)\n" % title)

    cat = os.path.join(root, "tables")
    os.makedirs(cat, exist_ok=True)
    os.makedirs(os.path.join(root, "build"), exist_ok=True)
    con = sqlite3.connect(os.path.join(root, "build", "library.db"))
    con.executescript(LIB_SCHEMA)
    for uid, title, rel, fmt, chapters in BOOKS:
        con.execute(
            "INSERT INTO book(uid,title,subject1,path,path_state) VALUES(?,?,?,?,?)",
            (uid, title, rel.split("/")[0], uid, "active"),
        )
        con.execute(
            "INSERT INTO source(uid,file,format,bytes,sha256,primary_src) VALUES(?,?,?,?,?,1)",
            (uid, "books/%s/source/书.%s" % (uid, fmt), fmt, 12, "sha-" + uid),
        )
        if chapters:
            con.execute(
                "INSERT INTO derived(uid,kind,status,chapters,bytes) VALUES(?,?,?,?,?)",
                (uid, "md", "done", chapters, 100),
            )
    con.execute("INSERT INTO award(name,books) VALUES(?,?)", ("测试奖", 2))
    for uid in ("B0001", "B0002"):
        con.execute("INSERT INTO book_award(uid,award,year,result) VALUES(?,?,?,?)", (uid, "测试奖", "2020", "获奖"))
    con.commit()
    con.close()

    write(
        os.path.join(cat, "分类表.csv"),
        "uid,一级,二级,作者层,保留,正式书名,备注\n"
        + "".join("%s,%s,,0,keep,%s,\n" % (uid, rel.split("/")[0], title) for uid, title, rel, _, _ in BOOKS),
    )
    write(
        os.path.join(cat, "对照表.csv"),
        "旧路径,新路径,uid\n" + "".join("旧/%s,books/%s,%s\n" % (title, uid, uid) for uid, title, rel, _, _ in BOOKS),
    )
    write(
        os.path.join(root, "build", "检索", "踩坑记录.md"),
        "# 踩坑记录\n\n## A 类 模型接口\n\n## B 类 SQLite 与全文索引\n\n"
        "## 已修（2026-10-20 下午）\n\n## 已知未修（按优先级）\n",
    )

    idx = sqlite3.connect(os.path.join(root, "build", "检索", "index.db"))
    idx.executescript(INDEX_SCHEMA)
    for key, value in (("built_at", "2026-10-24T18:35:38+0800"), ("books", "2"), ("chapters", "4"), ("paras", "4")):
        idx.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, value))
    for uid, _title, _rel, _, chapters in MD_BOOKS:
        for i in range(1, chapters + 1):
            path = "books/%s/md/%02d.md" % (uid, i)
            idx.execute(
                "INSERT INTO para(path,book,line,chars,tok,text) VALUES(?,?,?,?,?,?)",
                (path, uid, 1, 4, "正文", "正文。"),
            )
    for i in (1, 2):
        path = "books/B0001/md/%02d.md" % i
        idx.execute(
            "INSERT INTO expand(path,kind,tok,text,built_at) VALUES(?,?,?,?,?)",
            (path, "prefix", "正文", "正文。", "2026-10-24T18:35:38+0800"),
        )
        idx.execute("INSERT INTO expand_fts(path,tok,text) VALUES(?,?,?)", (path, "正文", "正文。"))
    idx.commit()
    idx.close()

    views = os.path.join(root, "build", "views")
    for sub, name, uid in (("奖项", "测试奖", "B0001"), ("奖项", "测试奖", "B0002"), ("主题", "测试主题", "B0001")):
        target = os.path.join(views, sub, name, uid)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(os.path.join("..", "..", "..", "..", "books", uid), target)
    write(os.path.join(views, "奖项", "说明.txt"), "不是软链，不计数\n")


def write_doc(root, text):
    path = os.path.join(root, "AGENTS.md")
    write(path, text)
    return path


def fingerprint(path):
    if not os.path.exists(path):
        return None
    st = os.stat(path)
    with open(path, "rb") as fh:
        return (st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest())


def unit_checks():
    print("\n== 渲染 ==")
    check("render 逐字等于约定的块", doc_numbers.render(SPEC_COUNTS) == SPEC_BLOCK, doc_numbers.render(SPEC_COUNTS))
    check(
        "千位分隔符落在每个四位数上",
        all(s in doc_numbers.render(SPEC_COUNTS) for s in ("1,779", "1,647", "1,009", "2,066", "1,057")),
    )
    shuffled = dict(SPEC_COUNTS, pending={"docx": 2, "txt": 5, "other": 0, "pdf": 125})
    check(
        "待拆章分组顺序固定为 pdf、txt、docx（与入参键序无关）",
        doc_numbers.render(shuffled) == SPEC_BLOCK,
        doc_numbers.render(shuffled),
    )
    other = dict(SPEC_COUNTS, pending_total=135, pending={"pdf": 125, "txt": 5, "docx": 2, "other": 3})
    line = doc_numbers.render(other).splitlines()[1]
    check("other 非零时排在 docx 之后", "（125 pdf、5 txt、2 docx、3 other）" in line, line)
    check("有 other 时不给 0 other", "0 other" not in doc_numbers.render(SPEC_COUNTS))
    check(
        "标点全角、日期取 as_of",
        "As of 2026-10-26：" in doc_numbers.render(SPEC_COUNTS) and "；坑账 7 类。" in doc_numbers.render(SPEC_COUNTS),
    )
    check(
        "块首尾各是标记，中间只有一行",
        doc_numbers.render(SPEC_COUNTS).splitlines()[0] == doc_numbers.START
        and doc_numbers.render(SPEC_COUNTS).splitlines()[-1] == doc_numbers.END
        and len(doc_numbers.render(SPEC_COUNTS).splitlines()) == 3,
    )

    print("\n== 换块与补块 ==")
    doc = "前言\n\n" + STALE_BLOCK + "\n\n后记\n"
    new, action = doc_numbers.sync(doc, SPEC_BLOCK)
    check("有块时动作是 replaced", action == "replaced", action)
    check("旧块被换掉，块外一字不动", new == "前言\n\n" + SPEC_BLOCK + "\n\n后记\n", new)
    check("再换一次结果不变（幂等）", doc_numbers.sync(new, SPEC_BLOCK)[0] == new)

    head_doc = "# 书库项目\n\n## 现状（2026-10-24 实测）\n\n1693 本书，1571 本有 md。\n"
    inserted, action = doc_numbers.sync(head_doc, SPEC_BLOCK)
    check("没有块时动作是 inserted", action == "inserted", action)
    check(
        "块插在「## 现状」标题之后、原标题行保留",
        "## 现状（2026-10-24 实测）\n\n" + SPEC_BLOCK + "\n\n1693 本书" in inserted,
        inserted,
    )
    check(
        "插入后再同步不再插第二份",
        doc_numbers.sync(inserted, SPEC_BLOCK)[0] == inserted and inserted.count(doc_numbers.START) == 1,
    )

    for name, bad_doc in (
        ("没有块也没有标题", "# 书库项目\n\n正文。\n"),
        ("只有开始标记", doc_numbers.START + "\n正文。\n"),
        ("只有结束标记", "正文。\n" + doc_numbers.END + "\n"),
    ):
        try:
            doc_numbers.sync(bad_doc, SPEC_BLOCK)
            check("%s时报错" % name, False, "没报错")
        except doc_numbers.DocError:
            check("%s时报错且不改盘" % name, True)


def fixture_checks(root):
    print("\n== 取数：fixture 六本 ==")
    counts = doc_numbers.collect(root)
    check("books 6", counts["books"] == 6, counts["books"])
    check("md 2 本（看 books/<uid>/md 是不是目录）", counts["md"] == 2, counts["md"])
    check(
        "待拆章 pdf/txt/docx/other 各 1",
        {k: v for k, v in counts["pending"].items() if v} == {"pdf": 1, "txt": 1, "docx": 1, "other": 1},
        counts["pending"],
    )
    check("待拆章合计 4", counts["pending_total"] == 4, counts["pending_total"])
    check("奖项记录 2 / 奖项名 1", (counts["awards"], counts["award_names"]) == (2, 1), counts)
    check(
        "视图 奖项 2 / 主题 1 / 合计 3（普通文件不计数）",
        (counts["view"], counts["view_total"]) == ({"奖项": 2, "主题": 1}, 3),
        counts["view"],
    )
    check("坑账按 ^## [A-Z] 类 数到 2（「已知未修」不算）", counts["pitfall_classes"] == 2, counts["pitfall_classes"])
    check("as_of 取本地日期", counts["as_of"] == datetime.date.today().isoformat(), counts["as_of"])
    check("显式 as_of 覆盖今天", doc_numbers.collect(root, as_of="2026-10-26")["as_of"] == "2026-10-26")
    check(
        "fixture 渲染出的那一行",
        "As of %s：6 本书，2 本有 md，4 本待拆章（1 pdf、1 txt、1 docx、1 other）" % datetime.date.today().isoformat()
        in doc_numbers.render(counts),
        doc_numbers.render(counts),
    )

    print("\n== CLI: 默认与 --json ==")
    proc = run([DOC_NUMBERS, "--root", root])
    check(
        "默认只打印现库的块，退出 0",
        proc.returncode == 0 and proc.stdout.strip() == doc_numbers.render(counts),
        proc.returncode,
    )
    proc = run([DOC_NUMBERS, "--root", root, "--json"])
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    check("--json 输出单行 counts", data is not None and len(proc.stdout.strip().splitlines()) == 1, proc.stdout[:200])
    check(
        "--json 字段与 collect 一致",
        data == json.loads(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        or (isinstance(data, dict) and sorted(data) == sorted(counts) and all(data[k] == counts[k] for k in counts)),
        data,
    )
    check("--json 不写盘", not os.path.exists(os.path.join(root, "AGENTS.md")))
    proc = run([DOC_NUMBERS, "--root", os.path.join(root, "没有这个根"), "--check"])
    check(
        "--root 指错时退出 2，不吐 traceback",
        proc.returncode == 2 and "Traceback" not in proc.stderr,
        proc.stderr[-300:],
    )

    print("\n== CLI: --check ==")
    fresh = doc_numbers.render(counts)
    doc = write_doc(root, "# 书库项目\n\n" + fresh + "\n\n正文。\n")
    before = read(doc)
    proc = run([DOC_NUMBERS, "--root", root, "--check"])
    check("块与现库一致时退出 0", proc.returncode == 0, proc.stdout + proc.stderr[-200:])
    check("一致时说明是哪天", "as_of %s" % counts["as_of"] in proc.stdout, proc.stdout)
    check("--check 不改文件", read(doc) == before)

    write_doc(root, "# 书库项目\n\n" + doc_numbers.render(dict(counts, as_of="2000-01-01")) + "\n")
    proc = run([DOC_NUMBERS, "--root", root, "--check"])
    check("只有日期旧时退出 0", proc.returncode == 0, proc.stdout)
    check("只有日期旧时说明可刷新", "日期旧" in proc.stdout, proc.stdout)

    write_doc(root, "# 书库项目\n\n" + STALE_BLOCK + "\n")
    proc = run([DOC_NUMBERS, "--root", root, "--check"])
    check("块过期时退出 1", proc.returncode == 1, proc.returncode)
    check("过期时打印两份", STALE_BLOCK in proc.stdout and fresh in proc.stdout, proc.stdout)
    check("过期时给出修法", "--write" in proc.stdout)

    write_doc(root, "# 书库项目\n\n正文。\n")
    proc = run([DOC_NUMBERS, "--root", root, "--check"])
    check("没有块时退出 1 并说是缺失", proc.returncode == 1 and "缺失" in proc.stdout, proc.stdout)

    print("\n== CLI: --write ==")
    write_doc(root, "# 书库项目\n\n" + STALE_BLOCK + "\n\n正文。\n")
    proc = run([DOC_NUMBERS, "--root", root, "--write"])
    body = read(doc)
    check("--write 换掉旧块并退出 0", proc.returncode == 0, proc.stdout + proc.stderr[-200:])
    check("换块后块外不动、旧块不剩", body == "# 书库项目\n\n" + fresh + "\n\n正文。\n", body)
    check("换块后 --check 通过", run([DOC_NUMBERS, "--root", root, "--check"]).returncode == 0)
    again = run([DOC_NUMBERS, "--root", root, "--write"])
    check("--write 再跑一次不写盘、字节不变", read(doc) == body and "没改" in again.stdout, again.stdout)

    write_doc(root, "# 书库项目\n\n## 现状（2026-10-24 实测）\n\n1693 本书。\n")
    proc = run([DOC_NUMBERS, "--root", root, "--write"])
    body = read(doc)
    check(
        "没有块时 --write 插在「## 现状」之后并退出 0",
        proc.returncode == 0 and "## 现状（2026-10-24 实测）\n\n" + fresh in body,
        body,
    )
    check("插入后旧段落还在", body.endswith("1693 本书。\n"), body)
    check("插入后 --check 通过", run([DOC_NUMBERS, "--root", root, "--check"]).returncode == 0)

    broken = "# 书库项目\n\n" + doc_numbers.START + "\n正文。\n"
    write_doc(root, broken)
    proc = run([DOC_NUMBERS, "--root", root, "--write"])
    check("只有一个标记时 --write 退出 1 且不改盘", proc.returncode == 1 and read(doc) == broken, proc.stdout)

    print("\n== audit_state 的文档一节 ==")
    write_doc(root, "# 书库项目\n\n" + STALE_BLOCK + "\n")
    proc = run([AUDIT, "--root", root, "--json"])
    try:
        audit = json.loads(proc.stdout)
    except ValueError:
        audit = None
    check("audit_state --json 仍可解析", audit is not None, proc.stdout[:200] + proc.stderr[-300:])
    if audit:
        check(
            "文档块过期是一条 WARN，不是 FAIL",
            any("AGENTS.md 的数字块过期" in w for w in audit["warn"])
            and not any("数字块" in f for f in audit["fail"])
            and proc.returncode == 0,
            (audit["fail"], audit["warn"], proc.returncode),
        )
    write_doc(root, "# 书库项目\n\n" + fresh + "\n")
    proc = run([AUDIT, "--root", root, "--json"])
    audit = json.loads(proc.stdout) if proc.returncode == 0 else None
    check("块一致时记一条 PASS", bool(audit) and any("数字块与现库一致" in m for m in audit["ok"]), proc.stdout[:300])


def main():
    real_doc = os.path.join(REPO, "AGENTS.md")
    real_before = fingerprint(real_doc)
    unit_checks()
    root = tempfile.mkdtemp(prefix="doc-numbers-")
    try:
        build_root(root)
        fixture_checks(root)
        check("真 AGENTS.md 一个字节都没动", fingerprint(real_doc) == real_before)
    finally:
        shutil.rmtree(root)
    if FAILS:
        print("\nFAILED %d 项: %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
