import json
import sqlite3

from conftest import run_cli


def test_library_builds_from_ledger(workspace):
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    stats = json.loads(r.stdout)
    assert stats["books"] == 3
    assert stats["conflicts"] == 0
    assert stats["md_done"] == 2
    assert stats["pending_md"] == 1
    con = sqlite3.connect(str(workspace / "build" / "library.db"))
    assert con.execute("select count(*) from book").fetchone()[0] == 3
    assert con.execute("select subject1 from book where uid='B0001'").fetchone()[0] == "文学"
    con.close()


def test_library_refuses_ledger_without_book(workspace):
    with open(workspace / "tables" / "分类表.csv", "a", encoding="utf-8") as f:
        f.write("B9999,文学,,0,keep,幽灵书,\n")
    r = run_cli("--root", workspace, "library")
    assert r.returncode != 0
    assert "B9999" in r.stdout + r.stderr


def test_index_and_search_round_trip(workspace):
    assert run_cli("--root", workspace, "library").returncode == 0
    r = run_cli("--root", workspace, "index", "--rebuild")
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(workspace / "build" / "检索" / "index.db"))
    meta = dict(con.execute("select key, value from meta"))
    con.close()
    assert meta["books"] == "2"

    r = run_cli("--root", workspace, "search", "铁笼 零件", "--json", "-n", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    hits = json.loads(r.stdout)["hits"]
    assert hits, "空结果说明索引或分组没接上"
    assert hits[0]["book"] == "B0001"


def test_search_reports_no_index(tmp_path, workspace):
    import shutil
    shutil.rmtree(workspace / "build" / "检索")
    r = run_cli("--root", workspace, "search", "铁笼", "--json")
    assert r.returncode != 0
