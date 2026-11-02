import json
import os
import sqlite3

from conftest import run_cli


def v1_workspace(root):
    for rel in ("目录", "库/文学/甲书/md", "库/文学/乙书/md", "库/文学/乙书/source",
                "库/文学/source", "库/_收件箱"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)

    with open(os.path.join(root, "目录", "分类表.csv"), "w", encoding="utf-8-sig") as f:
        f.write("uid,一级,二级,作者层,保留,正式书名,备注\n")
        f.write("B0001,文学,,0,keep,甲书,\n")
        f.write("B0002,文学,,0,keep,乙书,\n")

    with open(os.path.join(root, "目录", "概念表.tsv"), "w", encoding="utf-8") as f:
        f.write("键\t别名\n")
        f.write("自由\t独立判断\n")

    con = sqlite3.connect(os.path.join(root, "目录", "library.db"))
    con.execute("create table book(uid text primary key, title text, path text)")
    con.execute("create table derived(uid text, kind text, chapters int, bytes int)")
    con.execute("insert into book values('B0001','甲书','文学/甲书')")
    con.execute("insert into book values('B0002','乙书','文学/乙书')")
    con.commit()
    con.close()

    shared = {"uid": "B0001", "sources": [{"file": "../source/合集.epub",
                                           "format": "epub", "bytes": 100,
                                           "sha256": "a" * 64, "primary_src": 1}],
              "path": "文学/甲书"}
    own = {"uid": "B0002", "sources": [{"file": "source/乙.epub", "format": "epub",
                                        "bytes": 100, "sha256": "b" * 64, "primary_src": 1}],
           "path": "文学/乙书"}
    for uid, doc in (("B0001", shared), ("B0002", own)):
        base = os.path.join(root, "库", "文学", "甲书" if uid == "B0001" else "乙书")
        with open(os.path.join(base, "book.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        with open(os.path.join(base, "md", "README.md"), "w", encoding="utf-8") as f:
            f.write("# 目录\n")
        with open(os.path.join(base, "md", "001.md"), "w", encoding="utf-8") as f:
            f.write("正文\n")

    with open(os.path.join(root, "库", "文学", "source", "合集.epub"), "wb") as f:
        f.write(b"c" * 100)
    with open(os.path.join(root, "库", "文学", "乙书", "source", "乙.epub"), "wb") as f:
        f.write(b"e" * 100)
    with open(os.path.join(root, "库", "_收件箱", "README.md"), "w", encoding="utf-8") as f:
        f.write("收件箱\n")


def test_migrate_refuses_without_v1_tree(tmp_path):
    r = run_cli("migrate", "--root", str(tmp_path))
    assert r.returncode != 0
    assert "不像是 v1 工作区" in r.stdout + r.stderr


def test_migrate_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "old"
    v1_workspace(str(root))
    r = run_cli("migrate", "--root", str(root), "--dest", str(tmp_path / "new"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "只打印不落盘" in r.stdout
    assert not (tmp_path / "new").exists()
    assert (root / "库" / "文学" / "甲书" / "book.json").exists()


def test_migrate_copy_builds_v2_tree(tmp_path):
    root = tmp_path / "old"
    v1_workspace(str(root))
    new = tmp_path / "new"
    r = run_cli("migrate", "--root", str(root), "--dest", str(new),
                "--mode", "copy", "--apply", "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr

    assert (new / "books" / "B0001" / "md" / "001.md").exists()
    assert (new / "books" / "_收件箱" / "README.md").exists()
    assert (new / "collections" / "C0001" / "source" / "合集.epub").exists()
    assert (new / "tables" / "分类表.csv").exists()
    assert (new / "build" / "library.db").exists()

    manifest = (new / "books.toml").read_text(encoding="utf-8")
    assert "schema = 2" in manifest
    assert (tmp_path / "对照表-0002.csv").exists()

    shared = json.loads((new / "books" / "B0001" / "book.json").read_text(encoding="utf-8"))
    assert shared["sources"][0]["collection"] == "C0001"
    assert shared["sources"][0]["file"] == "合集.epub"
    assert "path" not in shared

    own = json.loads((new / "books" / "B0002" / "book.json").read_text(encoding="utf-8"))
    assert own["sources"][0]["file"] == "source/乙.epub"
    assert "path" not in own

    assert (root / "库" / "文学" / "甲书" / "book.json").exists(), "copy 不该动旧树"


def test_migrated_tree_builds_library(tmp_path):
    root = tmp_path / "old"
    v1_workspace(str(root))
    new = tmp_path / "new"
    assert run_cli("migrate", "--root", str(root), "--dest", str(new), "--mode", "copy",
                   "--apply", "--out", str(tmp_path)).returncode == 0
    r = run_cli("--root", str(new), "library")
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(new / "build" / "library.db"))
    rows = con.execute("select uid, file from source order by uid").fetchall()
    con.close()
    assert rows == [
        ("B0001", "collections/C0001/source/合集.epub"),
        ("B0002", "books/B0002/source/乙.epub"),
    ]
    r = run_cli("--root", str(new), "doctor")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fail" not in r.stdout
