"""build_views.py 的旧视图保护：输入缺失或生成中途异常时，旧的 build/views/主题 原样留着。

旧版直接往 build/views/主题/ 里写，跑到一半挂掉就剩半棵树（README 里有目录、软链却没建）。
现在先建 主题.building/，成功后 _promote 才换上去；失败必须把 building 清掉、旧树不动。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "bookcorpus" / "scripts" / "build_views.py"

OLD_VIEW = "# 旧主题视图\n\n- [文学](文学/)\n"


def _workspace(tmp_path):
    """带 books.toml 的临时工作区 + 一棵旧的主题视图。"""
    root = tmp_path / "ws"
    views = root / "build" / "views"
    (views / "主题").mkdir(parents=True)
    (views / "主题" / "README.md").write_text(OLD_VIEW, encoding="utf-8")
    (views / "主题" / "文学").mkdir()
    os.symlink("../../../../books/B0001", views / "主题" / "文学" / "甲书")
    (root / "build" / "检索").mkdir(parents=True)
    (root / "books" / "B0001").mkdir(parents=True)
    (root / "books.toml").write_text('schema = 2\ntool = "book-corpus test"\n',
                                     encoding="utf-8")
    return root


def _run(root, *args):
    env = dict(os.environ)
    env.pop("BOOKS_ROOT", None)
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(root),
                          capture_output=True, text=True, env=env)


def _assert_old_view_intact(root):
    readme = root / "build" / "views" / "主题" / "README.md"
    assert readme.is_file(), "旧视图 README 被删了"
    assert readme.read_text(encoding="utf-8") == OLD_VIEW, "旧视图 README 被改写了"
    assert (root / "build" / "views" / "主题" / "文学").is_dir(), "旧视图目录被删了"
    assert not (root / "build" / "views" / "主题.building").exists(), \
        "失败后 .building 暂存目录没清掉"


def test_topics_missing_index_db_keeps_old_view(tmp_path):
    """index.db 不存在：还没读到任何书就失败，旧视图必须不被动过。"""
    root = _workspace(tmp_path)
    r = _run(root, "--only", "topics")
    assert r.returncode != 0, "缺 index.db 应当失败：\n" + r.stdout + r.stderr
    _assert_old_view_intact(root)


def test_topics_missing_library_db_keeps_old_view(tmp_path):
    """index.db 在、library.db 缺：生成中途（title_map）抛异常，旧视图照样不动。"""
    root = _workspace(tmp_path)
    con = sqlite3.connect(root / "build" / "检索" / "index.db")
    con.executescript("""
CREATE TABLE book_concept(key TEXT, book TEXT);
CREATE INDEX book_concept_key ON book_concept(key);
""")
    con.execute("INSERT INTO book_concept VALUES ('文学', 'B0001')")
    con.commit()
    con.close()

    r = _run(root, "--only", "topics")
    assert r.returncode != 0, "缺 library.db 应当失败：\n" + r.stdout + r.stderr
    assert "library.db" in (r.stderr or ""), r.stderr
    _assert_old_view_intact(root)


def test_awards_missing_library_db_keeps_old_view(tmp_path):
    """奖项分支同一套原子换树逻辑：缺 library.db 时旧奖项视图不动。"""
    root = _workspace(tmp_path)
    awards = root / "build" / "views" / "奖项"
    awards.mkdir(parents=True)
    (awards / "README.md").write_text("# 旧奖项视图\n", encoding="utf-8")

    r = _run(root, "--only", "awards")
    assert r.returncode != 0, "缺 library.db 应当失败：\n" + r.stdout + r.stderr
    assert (awards / "README.md").read_text(encoding="utf-8") == "# 旧奖项视图\n"
    assert not (root / "build" / "views" / "奖项.building").exists()


def test_promote_finishes_leftover_backup(tmp_path):
    """上次换树崩在 rename 后：out 不在、backup 还在，promote 不能先把旧树删掉。"""
    sys.path.insert(0, str(SCRIPT.parent))
    import build_views

    out = tmp_path / "主题"
    backup = tmp_path / "主题.old"
    building = tmp_path / "主题.building"
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

    out = tmp_path / "主题"
    backup = tmp_path / "主题.old"
    building = tmp_path / "主题.building"
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

