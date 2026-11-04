"""入库清单的动作、保留写入与回收站。都跑真脚本，不看内部函数。"""

import hashlib
import json
import os
import sqlite3

from conftest import run_cli, write_book, write_ledger

STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acceptance", "fixtures", "pi_stub.py")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_source(root, uid, data, fmt):
    """给已有书写一个真源文件，并把 book.json 的 sha/大小改成它的。"""
    bdir = root / "books" / uid
    name = "old." + fmt
    src = bdir / "source" / name
    src.write_bytes(data)
    doc = json.loads((bdir / "book.json").read_text(encoding="utf-8"))
    doc["sources"][0].update(
        {
            "file": "source/" + name,
            "format": fmt,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "primary_src": 1,
        }
    )
    (bdir / "book.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return hashlib.sha256(data).hexdigest()


def _scan(root):
    r = run_cli("--root", root, "run", "ingest", "scan", "--json", "--no-classify")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _apply(root, items):
    plan = root / "plan.json"
    plan.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return run_cli("--root", root, "run", "ingest", "apply", str(plan), "--apply", "--no-rebuild")


def _inbox(root, name, data):
    inbox = root / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_bytes(data)
    return path


def test_same_sha_is_deleted_to_trash(workspace):
    src = workspace / "books" / "B0001" / "source" / "old.epub"
    src.write_bytes(b"same-content" * 10)
    sha = _set_source(workspace, "B0001", src.read_bytes(), "epub")
    dup = _inbox(workspace, "重复.epub", src.read_bytes())

    items = _scan(workspace)
    row = next(i for i in items if i["file"] == "重复.epub")
    assert row["action"] == "delete"
    assert row["status"] == "in_library"
    assert row["uid"] == "B0001"
    assert row["target_title"] == "甲书"
    assert row["target_subjects"] == ["文学"]

    r = _apply(workspace, items)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not dup.exists()
    trash = list((workspace / "工作区" / "批次").glob("收件箱删除-*"))
    assert trash, "没有建回收站"
    moved = trash[0] / "重复.epub"
    assert moved.is_file()
    manifest = (trash[0] / "deleted.tsv").read_text(encoding="utf-8")
    assert sha in manifest
    assert (workspace / "books" / "B0001" / "book.json").is_file()


def test_replace_writes_retention_and_marks_old_md(workspace):
    old = _set_source(workspace, "B0003", b"old-txt", "txt")
    md = workspace / "books" / "B0003" / "md"
    md.mkdir(exist_ok=True)
    (md / ".manifest.json").write_text(json.dumps({"uid": "B0003", "method": "test"}), encoding="utf-8")

    new = _inbox(workspace, "丙书.epub", b"new-epub" * 10)
    new_sha = _sha(new)

    items = _scan(workspace)
    row = next(i for i in items if i["file"] == "丙书.epub")
    assert row["action"] == "replace"
    assert row["uid"] == "B0003"
    assert row["retention"] == "keep"
    row["retention"] = "drop"

    r = _apply(workspace, items)
    assert r.returncode == 0, r.stdout + r.stderr

    bid = workspace / "books" / "B0003"
    doc = json.loads((bid / "book.json").read_text(encoding="utf-8"))
    assert doc["retention"] == "drop"
    assert doc["sources"][0]["sha256"] == new_sha
    assert doc["sources"][0]["primary_src"] == 1
    old_entry = next(s for s in doc["sources"] if s["sha256"] == old)
    assert old_entry["primary_src"] == 0
    assert (bid / "source" / "丙书.epub").is_file()
    assert (bid / "source" / "old.txt").is_file(), "旧源不该被删"

    con = sqlite3.connect(str(workspace / "tables" / "ledger.db"))
    got = con.execute("SELECT 一级, 保留, 正式书名 FROM 分类 WHERE uid='B0003'").fetchone()
    con.close()
    assert got == ("写作与编剧", "drop", "丙书")

    man = json.loads((md / ".manifest.json").read_text(encoding="utf-8"))
    assert man["source_sha256"] == old
    assert man["uid"] == "B0003"


def test_same_format_is_review_and_blocks_apply(workspace):
    src = workspace / "books" / "B0001" / "source" / "old.epub"
    src.write_bytes(b"old" * 40)
    _set_source(workspace, "B0001", src.read_bytes(), "epub")
    inbox = _inbox(workspace, "甲书.epub", b"different" * 20)

    items = _scan(workspace)
    row = next(i for i in items if i["file"] == "甲书.epub")
    assert row["action"] == "review"
    assert row["status"] == "same_format"
    assert row["uid"] == "B0001"

    r = _apply(workspace, items)
    assert r.returncode == 2
    assert "待核" in r.stderr
    assert inbox.is_file()


def test_fuzzy_title_is_review_not_delete(workspace):
    write_book(workspace, "B0004", "文学", "一句顶一万句", None)
    write_ledger(workspace, [("B0004", "文学", "一句顶一万句", "")])
    src = workspace / "books" / "B0004" / "source" / "old.epub"
    src.write_bytes(b"liu" * 50)
    _set_source(workspace, "B0004", src.read_bytes(), "epub")
    inbox = _inbox(workspace, "一句顶一万句全集.epub", b"collection" * 20)

    items = _scan(workspace)
    row = next(i for i in items if i["file"].startswith("一句顶一万句全集"))
    assert row["action"] == "review"
    assert row["status"] == "fuzzy"
    assert row["target"] == "B0004"
    assert row["target_title"] == "一句顶一万句"

    r = _apply(workspace, items)
    assert r.returncode == 2
    assert inbox.is_file()


def test_old_plan_and_path_escape_are_refused(workspace):
    inbox = _inbox(workspace, "x.epub", b"x" * 10)
    old = [{"plan_version": 1, "file": "x.epub", "action": "delete", "title": "x", "sha256": _sha(inbox), "bytes": 10}]
    plan = workspace / "plan.json"
    plan.write_text(json.dumps(old), encoding="utf-8")
    r = run_cli("--root", workspace, "run", "ingest", "apply", str(plan), "--apply", "--no-rebuild")
    assert r.returncode == 2
    assert "旧清单" in r.stderr

    bad = [
        {"plan_version": 2, "file": "../x.epub", "action": "delete", "title": "x", "sha256": _sha(inbox), "bytes": 10}
    ]
    plan.write_text(json.dumps(bad), encoding="utf-8")
    r = run_cli("--root", workspace, "run", "ingest", "apply", str(plan), "--apply", "--no-rebuild")
    assert r.returncode == 1
    assert "文件名" in r.stderr
    assert inbox.is_file()


def test_add_keeps_retention_and_rerun_skips(workspace):
    _inbox(workspace, "丁书.epub", b"ding" * 30)
    items = _scan(workspace)
    row = next(i for i in items if i["file"] == "丁书.epub")
    assert row["action"] == "add"
    row["subjects"] = ["文学"]
    row["retention"] = "review"

    r = _apply(workspace, items)
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads((workspace / "books" / row["uid"] / "book.json").read_text(encoding="utf-8"))
    assert doc["retention"] == "review"
    con = sqlite3.connect(str(workspace / "tables" / "ledger.db"))
    got = con.execute("SELECT 保留 FROM 分类 WHERE uid=?", (row["uid"],)).fetchone()
    con.close()
    assert got == ("review",)

    r = _apply(workspace, items)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list((workspace / "books").glob(row["uid"]))) == 1
    con = sqlite3.connect(str(workspace / "tables" / "ledger.db"))
    n = con.execute("SELECT count(*) FROM 分类 WHERE uid=?", (row["uid"],)).fetchone()[0]
    con.close()
    assert n == 1


def test_scan_cache_honours_given_path(workspace):
    """--cache 指到任务目录：分类缓存不落共享的 工作区/批次/ingest/classify.json。"""
    _inbox(workspace, "己书.epub", b"ji" * 30)
    job = workspace / "工作区" / "运维" / "jobs" / "scan-cache"
    plan, cache = job / "plan.json", job / "classify.json"
    payload = json.dumps(
        [{"level1": "文学", "level2": "类型小说", "confidence": 0.93, "reason": "测试"}], ensure_ascii=False
    )

    r = run_cli(
        "--root",
        workspace,
        "run",
        "ingest",
        "scan",
        "--json",
        "--out",
        str(plan),
        "--cache",
        str(cache),
        env={"PI_BIN": STUB, "PI_STUB_JSON": payload},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert plan.is_file(), r.stdout + r.stderr
    assert cache.is_file(), r.stdout + r.stderr
    assert json.loads(cache.read_text(encoding="utf-8")), cache.read_text(encoding="utf-8")

    row = next(i for i in json.loads(plan.read_text(encoding="utf-8")) if i["file"] == "己书.epub")
    assert row["action"] == "add"
    assert row["subjects"] == ["文学", "类型小说"], row
    shared = workspace / "工作区" / "批次" / "ingest" / "classify.json"
    assert not shared.exists(), "共享缓存不该被写：%s" % shared


def test_empty_category_falls_back_to_other(workspace):
    _inbox(workspace, "戊书.epub", b"wu" * 30)
    items = _scan(workspace)
    row = next(i for i in items if i["file"] == "戊书.epub")
    assert row["action"] == "add"
    assert row["subjects"] == ["其他"], row

    # 手改计划把分类清空，apply 也必须落到「其他」，不能写空。
    row["subjects"] = []
    r = _apply(workspace, items)
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(workspace / "tables" / "ledger.db"))
    got = con.execute("SELECT 一级 FROM 分类 WHERE uid=?", (row["uid"],)).fetchone()
    con.close()
    assert got == ("其他",)
    doc = json.loads((workspace / "books" / row["uid"] / "book.json").read_text(encoding="utf-8"))
    assert doc["subjects"] == ["其他"]
