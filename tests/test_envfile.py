import json
import os

from bookcorpus import envfile
from conftest import run_cli


def test_project_env_beats_cwd_and_user_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("A=project\nB=project\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("B=cwd\nC=cwd\n", encoding="utf-8")

    monkeypatch.delenv("BOOK_CORPUS_ENV", raising=False)
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("A", "B", "C", "D"):
        monkeypatch.delenv(key, raising=False)

    assert envfile.load(repo_root=project) == project / ".env"
    # 只读生效的那一个文件：仓库根存在时，cwd 的 .env 不参与。
    assert os.environ["A"] == "project"
    assert os.environ["B"] == "project"
    assert "C" not in os.environ

    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    assert envfile.load(repo_root=tmp_path / "absent") == work / ".env"
    assert os.environ["C"] == "cwd"


def test_explicit_override_is_authoritative(tmp_path, monkeypatch):
    target = tmp_path / "custom.env"
    target.write_text("X=from-custom\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("X=from-project\n", encoding="utf-8")
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(target))
    monkeypatch.delenv("X", raising=False)

    assert envfile.load(repo_root=project) == target
    assert os.environ["X"] == "from-custom"

    monkeypatch.setenv("BOOK_CORPUS_ENV", str(tmp_path / "missing.env"))
    assert envfile.path(repo_root=project) is None  # 指了就不回退


def test_real_environment_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("KEY", "real")
    envfile.load(repo_root=tmp_path)
    assert os.environ["KEY"] == "real"


def test_parse_handles_export_quotes_and_comments():
    text = "# note\nexport A=1\nB='two two'\nC=\"three\"\nD=unquoted # trailing comment\nEMPTY=\nnot a pair\n"
    assert envfile.parse(text) == {
        "A": "1",
        "B": "two two",
        "C": "three",
        "D": "unquoted",
        "EMPTY": "",
    }


def test_set_value_replaces_in_place(tmp_path, monkeypatch):
    """只换 BOOKS_ROOT 那一行，注释、其它键与顺序都不动。"""

    target = tmp_path / "e.env"
    target.write_text("# 注释留原样\nA=1\nexport BOOKS_ROOT=/old\nB=2\n", encoding="utf-8")
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(target))

    assert envfile.set_value("BOOKS_ROOT", "/srv/库") == target
    assert target.read_text(encoding="utf-8") == "# 注释留原样\nA=1\nBOOKS_ROOT=/srv/库\nB=2\n"


def test_set_value_creates_missing_file_and_quotes(tmp_path, monkeypatch):
    """BOOK_CORPUS_ENV 指了还不存在的文件：写它就新建，不回落仓库根。"""

    target = tmp_path / "nested" / "e.env"
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(target))

    assert envfile.set_value("BOOKS_ROOT", "/srv/my 库") == target
    assert envfile.parse(target.read_text(encoding="utf-8")) == {"BOOKS_ROOT": "/srv/my 库"}

    # 再写一次：原地换，不重复追加；不含空白就不加引号
    envfile.set_value("BOOKS_ROOT", "/srv/books")
    assert target.read_text(encoding="utf-8") == "BOOKS_ROOT=/srv/books\n"


def test_init_records_books_root(tmp_path):
    """init 建完工作区就把根记进 .env（测试里是 conftest 指的那份临时文件）。"""

    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    assert envfile.parse((tmp_path / "missing.env").read_text(encoding="utf-8"))["BOOKS_ROOT"] == str(root)


def test_cli_takes_root_from_env_file(tmp_path):
    """记下来的根真的被认：不带 --root、不 export BOOKS_ROOT 也能跑 doctor。"""

    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    done = run_cli("doctor", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    rows = json.loads(done.stdout)
    assert any(row["label"] == "清单" and row["level"] == "ok" for row in rows), rows
