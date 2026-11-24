import json
import sqlite3

from conftest import run_cli, write_ledger


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


def test_library_builds_empty_workspace(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("--root", root, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["books"] == 0
    r = run_cli("--root", root, "index", "--rebuild")
    assert r.returncode == 0, r.stdout + r.stderr


def test_library_refuses_ledger_without_book(workspace):
    write_ledger(workspace, [("B9999", "文学", "幽灵书", "")])
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


def test_library_v_path_conflict_catches_real_source_conflicts(workspace):
    from conftest import write_book, write_collection, write_ledger

    write_collection(workspace, "C0001", "合集.epub")
    write_book(workspace, "B0101", "文学", "甲卷", collection="C0001", collection_file="合集.epub", sha="a" * 64)
    write_book(workspace, "B0102", "文学", "乙卷", collection="C0001", collection_file="合集.epub", sha="a" * 64)
    write_ledger(workspace, [("B0101", "文学", "甲卷", ""), ("B0102", "文学", "乙卷", "")])

    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["conflicts"] == 0

    # 非合集源文件挂载到别的书目录下（如误填 collection="B0001"）或同书重复挂同一个源路径
    b102_json = workspace / "books" / "B0102" / "book.json"
    doc = json.loads(b102_json.read_text(encoding="utf-8"))
    doc["sources"].append(
        {"collection": "B0001", "file": "甲书.epub", "format": "epub", "bytes": 100, "sha256": "c" * 64}
    )
    doc["sources"].append(
        {"collection": "C0001", "file": "合集.epub", "format": "epub", "bytes": 100, "sha256": "a" * 64}
    )
    b102_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    r2 = run_cli("--root", workspace, "library")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    stats = json.loads(r2.stdout)
    assert stats["conflicts"] == 2, stats

    con = sqlite3.connect(str(workspace / "build" / "library.db"))
    rows = con.execute("SELECT path, n, uids FROM v_path_conflict ORDER BY path").fetchall()
    con.close()
    assert rows == [
        ("books/B0001/source/甲书.epub", 2, "B0001,B0102"),
        ("collections/C0001/source/合集.epub", 2, "B0102,B0102"),
    ]
