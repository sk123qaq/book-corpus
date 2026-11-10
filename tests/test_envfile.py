import os

from bookcorpus import envfile


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
