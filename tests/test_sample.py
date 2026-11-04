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
    md = sorted(p for p in (root / "books" / "B0001" / "md").glob("*.md")
                if p.name != "README.md")
    assert len(md) == 9
    assert (root / "books" / "B0001" / "md" / "README.md").exists()
    assert (root / "books" / "B0001" / "source" / "阿Q正传.txt").exists()

    con = sqlite3.connect(root / "tables" / "ledger.db")
    rows = con.execute("select uid, 正式书名 from 分类 order by uid").fetchall()
    con.close()
    assert rows == [("B0001", "阿Q正传")]

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


def test_audit_runs_on_fresh_library(tmp_path):
    """新建的库没有迁移留下的 对照表.csv，深审计不能因此报错、也不该提这茬。"""
    assert run_cli("init", tmp_path).returncode == 0
    assert not (tmp_path / "tables" / "对照表.csv").exists()
    assert run_cli("sample", "--root", tmp_path).returncode == 0

    r = run_cli("run", "audit_state", "--deep", "--root", tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "对照表" not in out
    assert "FAIL 0 项" in out
