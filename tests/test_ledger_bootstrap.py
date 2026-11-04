"""账库自举：空文件要补 schema，坏文件要留档重建，锁住时不能把活库换走。"""

import sqlite3
import sys
from pathlib import Path

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
