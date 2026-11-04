"""v2 树的日常入口：入库、视图、状态。都跑真脚本，不看内部函数。"""

import json
import os
import sqlite3

from conftest import run_cli


def test_ingest_add_lands_in_uid_directory(workspace):
    inbox = workspace / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    src = inbox / "新书.epub"
    src.write_bytes(b"x" * 100)
    r = run_cli(
        "--root",
        workspace,
        "run",
        "ingest",
        "add",
        str(src),
        "--subjects",
        "文学/严肃文学小说",
        "--title",
        "新书",
        "--apply",
        "--no-rebuild",
        "--no-classify",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    doc_path = workspace / "books" / "B0004" / "book.json"
    assert doc_path.is_file()
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    assert doc["sources"][0]["file"].startswith("source/")
    assert "path" not in doc
    assert (workspace / "books" / "B0004" / "source" / "新书.epub").is_file()
    assert not (workspace / "库").exists()
    con = sqlite3.connect(str(workspace / "tables" / "ledger.db"))
    assert "B0004" in {r[0] for r in con.execute("SELECT uid FROM 分类")}
    con.close()


def test_status_reads_v2_paths(workspace):
    assert run_cli("--root", workspace, "library").returncode == 0
    r = run_cli("--root", workspace, "status", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    state = json.loads(r.stdout)
    assert state["books"] == 3


def test_views_point_into_books(workspace):
    doc_path = workspace / "books" / "B0001" / "book.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["awards"] = [{"award": "测试奖", "year": "", "edition": "", "result": "已在盘"}]
    doc_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    assert run_cli("--root", workspace, "library").returncode == 0
    r = run_cli("--root", workspace, "index", "--rebuild", "--concepts")
    assert r.returncode == 0, r.stdout + r.stderr

    r = run_cli("--root", workspace, "run", "build_views")
    assert r.returncode == 0, r.stdout + r.stderr
    award = workspace / "build" / "views" / "奖项" / "测试奖"
    links = [p for p in award.iterdir() if p.is_symlink()]
    assert links
    assert all(p.resolve().is_dir() for p in links)
    assert all(os.path.realpath(p).startswith(str((workspace / "books").resolve())) for p in links)

    topics = workspace / "build" / "views" / "主题"
    assert topics.is_dir()
    all_links = [p for p in topics.rglob("*") if p.is_symlink()]
    assert all_links, "概念视图没有产出软链"
    assert all(os.path.realpath(p).startswith(str((workspace / "books").resolve())) for p in all_links)
