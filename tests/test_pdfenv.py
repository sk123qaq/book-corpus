import os
from pathlib import Path

from conftest import run_cli


def _requirements():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "requirements.txt")
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_requirements_txt_matches_pdf_venv_packages():
    from bookcorpus import pdfenv

    assert tuple(_requirements()) == pdfenv.REQUIREMENTS, "改 requirements.txt 记得同步 pdfenv.REQUIREMENTS"


def test_init_creates_pdf_venv_unless_disabled(tmp_path, monkeypatch):
    from bookcorpus import pdfenv, workspace

    calls = []
    monkeypatch.setattr(pdfenv, "create", lambda root, **kw: (calls.append(root), (True, "ok"))[1])
    monkeypatch.delenv("BOOKS_NO_PDF_VENV", raising=False)
    workspace.init(str(tmp_path / "on"), pdf_venv=True)
    assert calls == [str(tmp_path / "on")]

    workspace.init(str(tmp_path / "off"), pdf_venv=False)
    monkeypatch.setenv("BOOKS_NO_PDF_VENV", "1")
    workspace.init(str(tmp_path / "env"), pdf_venv=True)
    assert calls == [str(tmp_path / "on")]


def test_doctor_reports_pdf_env(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("doctor", "--root", root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PDF 环境" in r.stdout


def test_cli_venv_check_reports_missing(tmp_path):
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    r = run_cli("venv", "--check", "--root", root)
    assert r.returncode != 0
    assert ".venv-pdf" in r.stdout
