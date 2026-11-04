import json
import sqlite3

from conftest import run_cli, write_book, write_collection, write_ledger

SHARED = "合集甲乙丙（套装三册）.epub"


def _two_books_in_one_collection(root):
    write_collection(root, "C0001", SHARED)
    rows = []
    for uid, title in (("B0101", "甲卷"), ("B0102", "乙卷")):
        write_book(root, uid, "文学", title, ["正文一段。"], collection="C0001", collection_file=SHARED, sha="a" * 64)
        rows.append((uid, "文学", title, ""))
    write_ledger(root, rows)


def test_shared_source_points_at_collection(workspace):
    _two_books_in_one_collection(workspace)
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(workspace / "build" / "library.db"))
    rows = con.execute("select uid, file, primary_src from source where uid like 'B010%' order by uid").fetchall()
    assert rows == [
        ("B0101", "collections/C0001/source/" + SHARED, 1),
        ("B0102", "collections/C0001/source/" + SHARED, 1),
    ]
    assert con.execute("select count(*) from v_dup_sha").fetchone()[0] == 1
    con.close()


def test_book_json_keeps_collection_not_path(workspace):
    _two_books_in_one_collection(workspace)
    assert run_cli("--root", workspace, "library").returncode == 0
    r = run_cli("--root", workspace, "library", "--emit-json")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads((workspace / "books" / "B0101" / "book.json").read_text(encoding="utf-8"))
    assert doc["sources"][0]["collection"] == "C0001"
    assert doc["sources"][0]["file"] == SHARED
    assert "path" not in doc, "v2 的 book.json 不再记 path，目录名就是 uid"
