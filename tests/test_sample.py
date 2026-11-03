import csv
import sqlite3

from conftest import run_cli


def _books(root):
    return sorted(p for p in (root / "books").glob("B*/book.json"))


def test_sample_seeds_a_searchable_corpus(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0

    r = run_cli("sample", "--root", root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "样书入库" in r.stdout

    assert len(_books(root)) == 1
    md = sorted((root / "books" / "B0001" / "md").glob("*.md"))
    assert len(md) == 9
    assert (root / "books" / "B0001" / "source" / "阿Q正传.txt").exists()

    with open(root / "tables" / "分类表.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["uid"], r["正式书名"]) for r in rows] == [("B0001", "阿Q正传")]

    con = sqlite3.connect(root / "build" / "检索" / "index.db")
    meta = dict(con.execute("select key, value from meta").fetchall())
    con.close()
    assert int(meta["books"]) == 1
    assert int(meta["chapters"]) == 9

    again = run_cli("sample", "--root", root)
    assert again.returncode == 0
    assert "已在库" in again.stdout
    assert len(_books(root)) == 1

    hit = run_cli("search", "你那里配姓赵", "--root", root)
    assert hit.returncode == 0, hit.stdout + hit.stderr
    assert "B0001" in hit.stdout
    assert "配姓赵" in hit.stdout


def test_sample_requires_a_workspace(tmp_path):
    r = run_cli("sample", "--root", tmp_path)
    assert r.returncode != 0
    assert "不是工作区" in r.stdout + r.stderr
