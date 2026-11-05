"""docs 环境（工作区/.venv-docs）：包清单不漂移、禁用开关、缺环境的提示。

不联网、不装 markitdown：这里只验清单与开关，真建环境交给 `book-corpus venv`。
"""

from pathlib import Path

from conftest import run_cli


def _requirements():
    here = Path(__file__).resolve().parents[1]
    path = here / "requirements-docs.txt"
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_requirements_docs_matches_docenv_packages():
    from bookcorpus import docenv

    assert tuple(_requirements()) == docenv.REQUIREMENTS, "改 requirements-docs.txt 记得同步 docenv.REQUIREMENTS"
    assert "markitdown[docx,xlsx,xls,pptx]==0.1.9" in docenv.REQUIREMENTS


def test_venv_paths_live_under_workspace(tmp_path):
    from bookcorpus import docenv

    root = str(tmp_path)
    assert docenv.venv_dir(root) == str(Path(root) / "工作区" / ".venv-docs")
    assert docenv.python(root) == str(Path(root) / "工作区" / ".venv-docs" / "bin" / "python")


def test_disabled_reads_env(monkeypatch):
    from bookcorpus import docenv

    for value in ("1", "true", "YES", " on "):
        monkeypatch.setenv(docenv.DISABLE_ENV, value)
        assert docenv.disabled() is True, value
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv(docenv.DISABLE_ENV, value)
        assert docenv.disabled() is False, value
    monkeypatch.delenv(docenv.DISABLE_ENV)
    assert docenv.disabled() is False


def test_ensure_reports_missing_venv_without_network(tmp_path, monkeypatch):
    """BOOKS_NO_DOCS_VENV=1 且没有 venv：直接说缺，不建、不装。"""
    from bookcorpus import docenv

    monkeypatch.setenv(docenv.DISABLE_ENV, "1")
    calls = []
    monkeypatch.setattr(docenv, "create", lambda root, **kw: (calls.append(root), (True, "不该被调用"))[1])
    ok, detail = docenv.ensure(str(tmp_path))
    assert ok is False
    assert calls == []
    assert ".venv-docs" in detail


def test_ensure_builds_when_missing_and_allowed(tmp_path, monkeypatch):
    from bookcorpus import docenv

    monkeypatch.delenv(docenv.DISABLE_ENV, raising=False)
    built = []

    def fake_create(root, **kw):
        built.append(root)
        return True, "装好了"

    monkeypatch.setattr(docenv, "create", fake_create)
    monkeypatch.setattr(docenv, "versions", lambda root, **kw: (True, "markitdown 0.1.9"))
    ok, detail = docenv.ensure(str(tmp_path))
    assert ok is True
    assert built == [str(tmp_path)]
    assert "0.1.9" in detail


def test_init_builds_docs_venv_unless_disabled(tmp_path, monkeypatch):
    from bookcorpus import docenv, pdfenv, workspace

    monkeypatch.setattr(pdfenv, "create", lambda root, **kw: (True, "ok"))
    calls = []
    monkeypatch.setattr(docenv, "create", lambda root, **kw: (calls.append(root), (True, "ok"))[1])
    monkeypatch.delenv(docenv.DISABLE_ENV, raising=False)
    monkeypatch.delenv(pdfenv.DISABLE_ENV, raising=False)

    workspace.init(str(tmp_path / "on"))
    assert calls == [str(tmp_path / "on")]

    monkeypatch.setenv(docenv.DISABLE_ENV, "1")
    workspace.init(str(tmp_path / "off"))
    assert calls == [str(tmp_path / "on")]


def test_doctor_warns_but_does_not_fail_without_docs_venv(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("doctor", "--root", root)
    assert "文档环境" in r.stdout
    assert ".venv-docs" in r.stdout
    # 缺 docs 环境只是 warn，不影响 doctor 退出码（PDF 环境那边也是 warn）
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_venv_check_reports_missing_docs_env(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("venv", "--check", "--root", root)
    assert r.returncode != 0
    assert ".venv-docs" in r.stdout
    assert "docs 环境" in r.stdout
