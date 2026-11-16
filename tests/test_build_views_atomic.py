"""build_views.py 的旧视图保护：输入缺失或生成中途异常时，旧的 build/views/奖项 原样留着。"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "bookcorpus" / "scripts" / "build_views.py"


def _workspace(tmp_path):
    """带 books.toml 的临时工作区 + 一棵旧的奖项视图。"""
    root = tmp_path / "ws"
    (root / "build" / "views" / "奖项").mkdir(parents=True)
    (root / "books" / "B0001").mkdir(parents=True)
    (root / "books.toml").write_text('schema = 2\ntool = "book-corpus test"\n', encoding="utf-8")
    return root


def _run(root, *args):
    import os

    env = dict(os.environ)
    env.pop("BOOKS_ROOT", None)
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(root), capture_output=True, text=True, env=env)


def test_awards_missing_library_db_keeps_old_view(tmp_path):
    """缺 library.db 时旧奖项视图不动，不留 .building。"""
    root = _workspace(tmp_path)
    awards = root / "build" / "views" / "奖项"
    (awards / "README.md").write_text("# 旧奖项视图\n", encoding="utf-8")

    r = _run(root)
    assert r.returncode != 0, "缺 library.db 应当失败：\n" + r.stdout + r.stderr
    assert (awards / "README.md").read_text(encoding="utf-8") == "# 旧奖项视图\n"
    assert not (root / "build" / "views" / "奖项.building").exists()


def test_promote_finishes_leftover_backup(tmp_path):
    """上次换树崩在 rename 后：out 不在、backup 还在，promote 不能先把旧树删掉。"""
    sys.path.insert(0, str(SCRIPT.parent))
    import build_views

    out = tmp_path / "奖项"
    backup = tmp_path / "奖项.old"
    building = tmp_path / "奖项.building"
    backup.mkdir()
    (backup / "README.md").write_text("old\n", encoding="utf-8")
    building.mkdir()
    (building / "README.md").write_text("new\n", encoding="utf-8")

    build_views._promote(str(building), str(out))

    assert (out / "README.md").read_text(encoding="utf-8") == "new\n"
    assert not backup.exists(), "换树成功后旧备份应当清掉"


def test_promote_restores_leftover_backup_on_failure(tmp_path, monkeypatch):
    """out 不在、backup 还在时 replace 失败：旧树必须回到 out，不能只剩 .old。"""
    sys.path.insert(0, str(SCRIPT.parent))
    import build_views

    out = tmp_path / "奖项"
    backup = tmp_path / "奖项.old"
    building = tmp_path / "奖项.building"
    backup.mkdir()
    (backup / "README.md").write_text("old\n", encoding="utf-8")
    building.mkdir()
    (building / "README.md").write_text("new\n", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("跨设备")

    monkeypatch.setattr(build_views.os, "replace", boom)
    with pytest.raises(OSError):
        build_views._promote(str(building), str(out))

    assert (out / "README.md").read_text(encoding="utf-8") == "old\n"
    assert not backup.exists(), "失败后旧树应回到 out，不留 .old"
