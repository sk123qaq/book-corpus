import os

from conftest import run_cli


def test_init_creates_layout(tmp_path):
    root = tmp_path / "ws"
    r = run_cli("init", root)
    assert r.returncode == 0, r.stderr
    assert (root / "books.toml").exists()
    assert (root / "tables" / "分类表.csv").exists()
    assert (root / "tables" / "概念表.tsv").exists()
    assert (root / "books" / "_收件箱" / "README.md").exists()
    assert (root / "books" / "_冷宫" / "README.md").exists()
    header = (root / "tables" / "分类表.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == "uid,一级,二级,作者层,保留,正式书名,备注"


def test_init_refuses_existing_manifest(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("init", root)
    assert r.returncode != 0
    assert "--force" in r.stdout + r.stderr


def test_doctor_accepts_fresh_workspace(tmp_path):
    root = tmp_path / "ws"
    run_cli("init", root)
    r = run_cli("doctor", "--root", root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "分类表.csv 0 行" in r.stdout
    assert "无 build/library.db" in r.stdout


def test_doctor_rejects_empty_dir(tmp_path):
    r = run_cli("doctor", "--root", tmp_path)
    assert r.returncode != 0
    assert "不是工作区" in r.stdout


def test_root_found_by_walking_up(workspace):
    nested = os.path.join(workspace, "books")
    r = run_cli("doctor", cwd=nested)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "分类表.csv 3 行" in r.stdout


def test_list_marks_aliases():
    r = run_cli("list")
    assert r.returncode == 0
    assert "qsearch" in r.stdout and "search" in r.stdout


def test_init_ships_full_concept_tables(tmp_path):
    root = tmp_path / "ws"
    run_cli("init", root)
    for name, rows in (("概念表.tsv", 126), ("概念表-broad.tsv", 10), ("概念表-动作词.tsv", 15)):
        lines = (root / "tables" / name).read_text(encoding="utf-8-sig").splitlines()
        assert len(lines) - 1 == rows, "%s 只有 %d 行" % (name, len(lines) - 1)
