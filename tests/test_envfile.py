import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bookcorpus import envfile
from conftest import run_cli

REPO = Path(__file__).resolve().parents[1]


def _clear_env(monkeypatch, *keys: str) -> None:
    """Clear env keys now and record their pre-test state for teardown restore."""
    for key in keys:
        monkeypatch.setenv(key, "")
        os.environ.pop(key, None)


def test_project_env_beats_cwd_and_user_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("A=project\nB=project\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("B=cwd\nC=cwd\n", encoding="utf-8")

    _clear_env(monkeypatch, "BOOK_CORPUS_ENV", "A", "B", "C", "D")
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert envfile.load(repo_root=project) == project / ".env"
    # 只读生效的那一个文件：仓库根存在时，cwd 的 .env 不参与。
    assert os.environ["A"] == "project"
    assert os.environ["B"] == "project"
    assert "C" not in os.environ

    os.environ.pop("A", None)
    os.environ.pop("B", None)
    assert envfile.load(repo_root=tmp_path / "absent") == work / ".env"
    assert os.environ["C"] == "cwd"


def test_explicit_override_is_authoritative(tmp_path, monkeypatch):
    target = tmp_path / "custom.env"
    target.write_text("X=from-custom\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("X=from-project\n", encoding="utf-8")
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(target))
    _clear_env(monkeypatch, "X")

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


def test_resolve_books_root_reads_env_only_without_fallbacks(tmp_path, monkeypatch):
    """默认只读 .env 里的 BOOKS_ROOT，不回退 ~/书库、BOOK_CORPUS_DATA 或 cwd。"""

    ws = tmp_path / "ws"
    ws.mkdir()
    env_path = tmp_path / "app.env"
    env_path.write_text(f"BOOKS_ROOT={ws}\n", encoding="utf-8")

    _clear_env(monkeypatch, "BOOKS_ROOT")
    monkeypatch.setenv("BOOK_CORPUS_DATA", str(tmp_path / "legacy-data"))
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(env_path))
    assert envfile.resolve_books_root() == ws.resolve()

    explicit = tmp_path / "explicit"
    assert envfile.resolve_books_root(explicit) == explicit.resolve()

    os.environ.pop("BOOKS_ROOT", None)
    monkeypatch.setenv("BOOK_CORPUS_ENV", str(tmp_path / "absent.env"))
    with pytest.raises(SystemExit):
        envfile.resolve_books_root()


@pytest.mark.parametrize(
    "script",
    [
        "apps/home/server.py",
        "apps/reading/server.py",
        "apps/daily/server.py",
        "apps/ops/server.py",
        "apps/demo/server.py",
    ],
)
def test_all_app_servers_reject_missing_books_root_without_fallback(tmp_path, script):
    """不传 --root 且 .env/环境里没有 BOOKS_ROOT 时，五个服务都不回退其它路径而直接退出。"""

    env = dict(os.environ)
    env.pop("BOOKS_ROOT", None)
    env["BOOK_CORPUS_DATA"] = str(tmp_path / "ignored-legacy")
    env["BOOK_CORPUS_ENV"] = str(tmp_path / "missing.env")
    proc = subprocess.run(
        [sys.executable, str(REPO / script)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "BOOKS_ROOT" in (proc.stdout + proc.stderr)


def test_env_guard_allows_monkeypatch_and_catches_direct_os_environ(tmp_path, monkeypatch):
    """monkeypatch 改受保护前缀不会触发泄漏告警；resolve_books_root 写 os.environ 后也应自动清理。"""
    env_path = tmp_path / "guard.env"
    ws = tmp_path / "ws"
    ws.mkdir()
    env_path.write_text(f"BOOKS_ROOT={ws}\n", encoding="utf-8")

    monkeypatch.setenv("BOOK_CORPUS_ENV", str(env_path))
    monkeypatch.setenv("BOOKS_EXTRA_FLAG", "temp")
    assert envfile.resolve_books_root() == ws.resolve()
    assert os.environ["BOOKS_ROOT"] == str(ws)


def test_init_preserves_existing_different_books_root(tmp_path):
    """已有不同 BOOKS_ROOT 时，默认不覆盖既有配置；显式 --set-default 才改写。"""
    env_path = tmp_path / "app.env"
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"

    r1 = run_cli("init", ws1, env={"BOOK_CORPUS_ENV": str(env_path)})
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert envfile.parse(env_path.read_text(encoding="utf-8"))["BOOKS_ROOT"] == str(ws1)

    r2 = run_cli("init", ws2, env={"BOOK_CORPUS_ENV": str(env_path)})
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "保留既有 BOOKS_ROOT" in r2.stdout
    assert envfile.parse(env_path.read_text(encoding="utf-8"))["BOOKS_ROOT"] == str(ws1)

    r3 = run_cli("init", ws2, "--set-default", "--force", env={"BOOK_CORPUS_ENV": str(env_path)})
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert envfile.parse(env_path.read_text(encoding="utf-8"))["BOOKS_ROOT"] == str(ws2)


def test_init_no_env_skips_writing_env(tmp_path):
    env_path = tmp_path / "custom.env"
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"

    r1 = run_cli("init", ws1, "--no-env", env={"BOOK_CORPUS_ENV": str(env_path)})
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert not env_path.exists()

    env_path.write_text(f"BOOKS_ROOT={ws1}\n", encoding="utf-8")
    r2 = run_cli("init", ws2, "--no-env", env={"BOOK_CORPUS_ENV": str(env_path)})
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert envfile.parse(env_path.read_text(encoding="utf-8"))["BOOKS_ROOT"] == str(ws1)
