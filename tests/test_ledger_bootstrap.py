"""账库自举：空文件要补 schema，坏文件要留档重建，锁住时不能把活库换走。"""

import json
import sqlite3
import sys
from pathlib import Path

from conftest import run_cli, write_book

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "bookcorpus" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import lib_common as L


def _tables(root):
    path = root / "tables"
    path.mkdir()
    return path


def _count(db):
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT count(*) FROM 分类").fetchone()[0]
    finally:
        con.close()


def test_empty_ledger_file_gets_schema(tmp_path):
    tables = _tables(tmp_path)
    db = tables / "ledger.db"
    db.write_bytes(b"")
    assert L._ensure_ledger(root=str(tmp_path), create=True) is True
    assert _count(db) == 0
    assert not list(tables.glob("ledger.db.broken-*"))


def test_corrupt_ledger_file_is_backed_up(tmp_path):
    tables = _tables(tmp_path)
    db = tables / "ledger.db"
    db.write_bytes(b"this is not a sqlite database")
    assert L._ensure_ledger(root=str(tmp_path), create=True) is True
    assert list(tables.glob("ledger.db.broken-*"))
    assert _count(db) == 0


def test_locked_ledger_is_not_renamed(tmp_path, monkeypatch):
    tables = _tables(tmp_path)
    db = tables / "ledger.db"
    db.write_bytes(b"keep this file")

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(L.sqlite3, "connect", locked)
    assert L._ensure_schema(root=str(tmp_path)) is False
    assert db.read_bytes() == b"keep this file"
    assert not list(tables.glob("ledger.db.broken-*"))


def _write_raw_book_json(root: Path, uid: str, doc: dict) -> None:
    bdir = root / "books" / uid
    (bdir / "source").mkdir(parents=True, exist_ok=True)
    (bdir / "book.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def test_missing_and_corrupt_ledger_recovers_from_book_json(tmp_path):
    _tables(tmp_path)
    _write_raw_book_json(
        tmp_path,
        "B0001",
        {"uid": "B0001", "title": "甲书", "subjects": ["文学", "类型小说"], "retention": "review", "notes": "人工注"},
    )
    _write_raw_book_json(
        tmp_path,
        "B0002",
        {"uid": "B0002", "title": "乙书", "subjects": [], "retention": "bad_value", "notes": ""},
    )

    assert L._ensure_ledger(root=str(tmp_path), create=True) is True
    rows = L.categories(root=str(tmp_path))
    assert [(r["uid"], r["一级"], r["二级"], r["作者层"], r["保留"], r["正式书名"], r["备注"]) for r in rows] == [
        ("B0001", "文学", "类型小说", "0", "review", "甲书", "人工注"),
        ("B0002", "其他", "", "0", "keep", "乙书", ""),
    ]
    con = L.ledger_connect("ro", root=str(tmp_path))
    meta = dict(con.execute("SELECT key, value FROM ledger_meta").fetchall())
    con.close()
    assert meta["recovered_from"] == "book.json"
    assert meta["imported_from"]

    # 坏库被留档并从 book.json 自动重建
    db = tmp_path / "tables" / "ledger.db"
    db.write_bytes(b"corrupt ledger bytes")
    assert L._ensure_ledger(root=str(tmp_path), create=True) is True
    assert list((tmp_path / "tables").glob("ledger.db.broken-*"))
    assert _count(db) == 2


def test_build_library_recovers_missing_ledger_from_book_json(tmp_path):
    # 1) init 后带空表头 分类表.csv 的工作区：删掉 ledger.db 也能从 book.json 恢复
    root = tmp_path / "ws_with_empty_csv"
    assert run_cli("init", root).returncode == 0
    write_book(root, "B0001", "文学", "甲书", ["正文一段。"])
    (root / "tables" / "ledger.db").unlink()

    r = run_cli("--root", root, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["books"] == 1
    assert (root / "tables" / "ledger.db").is_file()
    assert _count(root / "tables" / "ledger.db") == 1

    # 2) 完全没有 分类表.csv 的工作区：删掉 ledger.db 后也能恢复并重建 library.db
    (root / "tables" / "分类表.csv").unlink()
    (root / "tables" / "ledger.db").unlink()
    r2 = run_cli("--root", root, "library")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert json.loads(r2.stdout)["books"] == 1
    assert _count(root / "tables" / "ledger.db") == 1
