"""expand 路径改写：v1 前缀按对照表换成 books/<uid>，重复跑不重复灌。"""

import csv
import sqlite3

from bookcorpus import remap
from bookcorpus.scripts import build_expand


def make_db(path, rows=()):
    con = sqlite3.connect(path)
    con.executescript(build_expand.SCHEMA)
    con.executemany(
        "INSERT INTO expand(path,kind,tok,text,model,prompt_hash,tokens_in,tokens_out,built_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def write_map(path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "阶段", "uid", "类别", "旧路径", "新路径", "备注"])
        w.writerow([1, 3, "B0001", "书", "库/文学/甲书", "books/B0001", ""])
        w.writerow([2, 3, "", "合集", "库/文学/source", "collections/C0001/source", ""])


def test_remap_in_place(tmp_path):
    db = tmp_path / "index.db"
    make_db(db, [("库/文学/甲书/md/01.md", "prefix", "x", "y", "m", "h", 1, 2, "t")])
    mapping = tmp_path / "map.csv"
    write_map(mapping)
    code, lines = remap.run(str(db), str(mapping), apply_it=False)
    assert code == 0, lines
    code, lines = remap.run(str(db), str(mapping), apply_it=True)
    assert code == 0, lines
    con = sqlite3.connect(db)
    got = con.execute("select path from expand").fetchall()
    assert got == [("books/B0001/md/01.md",)]
    assert con.execute("select count(*) from expand_fts").fetchone()[0] == 1
    con.close()
    code, lines = remap.run(str(db), str(mapping), apply_it=True)
    assert code == 0, lines
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from expand").fetchone()[0] == 1
    con.close()


def test_remap_from_old_db_is_idempotent(tmp_path):
    old = tmp_path / "old.db"
    new = tmp_path / "new.db"
    make_db(old, [("库/文学/甲书/md/01.md", "prefix", "x", "y", "m", "h", 1, 2, "t")])
    make_db(new)
    mapping = tmp_path / "map.csv"
    write_map(mapping)
    code, lines = remap.run(str(new), str(mapping), old_db=str(old), apply_it=True)
    assert code == 0, lines
    code, lines = remap.run(str(new), str(mapping), old_db=str(old), apply_it=True)
    assert code == 0, lines
    con = sqlite3.connect(new)
    assert con.execute("select path from expand").fetchall() == [("books/B0001/md/01.md",)]
    assert con.execute("select count(*) from expand_fts").fetchone()[0] == 1
    con.close()


def test_remap_reports_unmapped(tmp_path):
    db = tmp_path / "index.db"
    make_db(db, [("库/文学/乙书/md/01.md", "prefix", "x", "y", "m", "h", 1, 2, "t")])
    mapping = tmp_path / "map.csv"
    write_map(mapping)
    code, lines = remap.run(str(db), str(mapping), apply_it=True)
    assert code == 2, lines
    con = sqlite3.connect(db)
    assert con.execute("select path from expand").fetchone()[0] == "库/文学/乙书/md/01.md"
    con.close()
