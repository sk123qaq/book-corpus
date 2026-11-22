"""corpus_snapshot：本 / 章 / 段 / 字 单一统计源的单测。

口径（owner 定，2026-11-22）：
- 藏书 = library.db book 表行数，含无正文书
- 可读 = md 目录存在且至少 1 章（0 章的图片书不算可读）
- 章 = md 文件视角（合并标题页不改变文件数）
- 段 = index para 行数（仅含有词元的段）
- 字 = 非空白正文（shelf-index full_chars 每文件恰计一次）
"""

import datetime
import re
import sqlite3

import pytest

from bookcorpus import corpus_snapshot
from bookcorpus.scripts.layout import Layout
from conftest import run_cli, write_book, write_ledger


@pytest.fixture
def stats_root(tmp_path):
    """一个四本小库：两本有章、一本无 md、一本 md 目录里只有 README（0 章，图片书）。"""
    root = tmp_path / "ws"
    assert run_cli("init", root).returncode == 0
    write_book(root, "B0001", "文学", "甲书", paragraphs=["第一段。", "第二段。"])
    write_book(root, "B0002", "文学", "乙书", paragraphs=["第三段。"])
    write_book(root, "B0003", "人文社科", "丙书")  # 无 md：待转
    write_book(root, "B0005", "其他", "图册")  # 图片书：md 目录里只有 README
    (root / "books" / "B0005" / "md").mkdir(parents=True, exist_ok=True)
    (root / "books" / "B0005" / "md" / "README.md").write_text("# 图册\n", encoding="utf-8")
    write_ledger(
        root,
        [
            ("B0001", "文学", "甲书", ""),
            ("B0002", "文学", "乙书", ""),
            ("B0003", "人文社科", "丙书", ""),
            ("B0005", "其他", "图册", ""),
        ],
    )
    assert run_cli("library").returncode == 0
    return root


def test_counts_follow_the_owner_definition(stats_root):
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["books_total"] == 4  # 藏书：含无 md 与 0 章
    assert snap["books_readable"] == 2  # 可读：B0003 无 md、B0005 0 章，都不算
    assert snap["chapters"] == 3  # 章：md 文件视角（2 + 1）
    assert snap["paras"] == 0  # 段：索引还没建，para 空
    assert snap["chars"] is None  # 字：缺 shelf-index 不兜底
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", snap["built_at"])
    assert snap["sources"]["library"]["ok"] is True
    assert snap["sources"]["chapters_from"] == "derived"


def test_chapter_count_is_file_view_not_merged_nav(stats_root):
    """标题页合并只影响导航，不改变 md 文件数：01 题记 + 02 正文仍是 2 章。"""
    md = stats_root / "books" / "B0001" / "md"
    (md / "02_前置页_正文.md").write_text("正文内容够长。\n\n" * 30, encoding="utf-8")
    assert run_cli("library").returncode == 0
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["chapters"] == 4  # 文件视角：B0001 三个文件 + B0002 一个


def test_shelf_index_serves_chars_not_chapters(stats_root):
    """伴读自建 shelf-index 后，只有「字」从它来；章永远取 derived，免得 shelf 懒扫描造成三页分叉。"""
    from bookcorpus.reading.shelf_index import ShelfIndex

    index = ShelfIndex(Layout(str(stats_root)))
    index.ensure()
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["chapters"] == 3
    assert snap["sources"]["chapters_from"] == "derived"
    assert snap["chars"] > 0
    assert snap["sources"]["chars_from"] == "shelf-index"
    # 字 = 非空白：拿盘上正文手算一遍
    text = (stats_root / "books" / "B0001" / "md" / "001_第1节.md").read_text(encoding="utf-8")
    assert snap["chars"] >= len("".join(text.split()))


def test_empty_workspace_reports_zeroes(stats_root):
    """init 空根：藏书与可读都是 0，不是「—」。"""
    empty = stats_root.parent / "empty-ws"
    assert run_cli("init", empty).returncode == 0
    snap = corpus_snapshot.snapshot(empty)
    assert snap["books_total"] == 0
    assert snap["books_readable"] == 0
    assert snap["chapters"] == 0
    assert snap["paras"] == 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", snap["built_at"])
    # 空根仍有 init 建的空索引：built_at = 索引的数据时刻（不是快照计算时刻）
    assert snap["sources"]["index"]["built_at"]
    assert snap["sources"]["computed_at"]


def test_derived_chapters_win_over_stale_shelf_index(stats_root):
    """shelf-index 落后于盘上（懒扫描）时，章数必须仍取 derived，三页不分叉。"""
    from bookcorpus.reading.shelf_index import ShelfIndex

    ShelfIndex(Layout(str(stats_root))).ensure()  # 此时 shelf-index 记 B0001=2 + B0002=1
    md = stats_root / "books" / "B0001" / "md"
    (md / "003_新章.md").write_text("新章内容够长。\n\n" * 20, encoding="utf-8")
    assert run_cli("library").returncode == 0  # derived 更新成 4，shelf-index 没重建
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["chapters"] == 4  # derived 的数，不是 shelf-index 里的 3
    assert snap["sources"]["chapters_from"] == "derived"
    # built_at = 数据时刻（library.db 重建时刻），不是本次快照的计算时刻：
    # 快照的 built_at 与库里原样戳同秒同格式；计算时刻只会更晚（同格式字典序可比）
    assert snap["built_at"] == _lib_built_at(stats_root)
    assert snap["sources"]["computed_at"] >= snap["built_at"]


def _lib_built_at(root):
    """library.db 的 built_at（isoformat 原样），格式化成快照统一的「YYYY-MM-DD HH:MM:SS」。"""
    con = sqlite3.connect(root / "build" / "library.db")
    try:
        raw = con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0]
    finally:
        con.close()
    return datetime.datetime.fromisoformat(raw).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def test_missing_root_gives_none_snapshot(tmp_path):
    snap = corpus_snapshot.snapshot(tmp_path / "no-such-root")
    assert snap["books_total"] is None
    assert snap["books_readable"] is None
    assert snap["chapters"] is None
    assert snap["paras"] is None
    assert snap["sources"]["library"]["ok"] is False
    # 一个库都没有：built_at 只能是计算时刻
    assert snap["built_at"] == snap["sources"]["computed_at"]


def test_none_root_follows_books_root(stats_root, monkeypatch):
    monkeypatch.setenv("BOOKS_ROOT", str(stats_root))
    snap = corpus_snapshot.snapshot(None)
    assert snap["books_readable"] == 2


def test_snapshot_is_read_only(stats_root):
    """快照只读打开各库，不写盘、不建库。"""
    before = sorted(
        (str(p.relative_to(stats_root)), p.stat().st_mtime_ns) for p in stats_root.rglob("*") if p.is_file()
    )
    corpus_snapshot.snapshot(stats_root)
    after = sorted((str(p.relative_to(stats_root)), p.stat().st_mtime_ns) for p in stats_root.rglob("*") if p.is_file())
    assert before == after
    # 缺 shelf-index 时也不会被顺手建出来
    assert not (stats_root / "build" / "阅读" / "shelf-index.db").exists()


def test_corrupt_library_db_degrades_to_none(stats_root):
    """真坏库（文件损坏）时不抛异常，library 的数给 None。"""
    (stats_root / "build" / "library.db").write_bytes(b"not a sqlite file at all")
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["books_total"] is None
    assert snap["books_readable"] is None
    assert snap["chapters"] is None
    assert snap["sources"]["library"]["ok"] is False
