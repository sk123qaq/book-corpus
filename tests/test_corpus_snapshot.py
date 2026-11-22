"""corpus_snapshot：本 / 章 / 段 / 字 单一统计源的单测。

口径（owner 定，2026-11-22）：
- 藏书 = library.db book 表行数，含无正文书
- 可读 = md 目录存在且至少 1 章（0 章的图片书不算可读）
- 章 = md 文件视角（合并标题页不改变文件数）
- 段 = index para 行数（仅含有词元的段）
- 字 = 非空白正文（shelf-index full_chars 每文件恰计一次）
"""

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
    assert run_cli("library").returncode == 0
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["chapters"] == 4  # 文件视角：B0001 三个文件 + B0002 一个


def test_shelf_index_is_preferred_source_for_chapters_and_chars(stats_root):
    """伴读自建 shelf-index 后，章与字都从它来；章仍是文件视角。"""
    from bookcorpus.reading.shelf_index import ShelfIndex

    index = ShelfIndex(Layout(str(stats_root)))
    index.ensure()
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["chapters"] == 3
    assert snap["sources"]["chapters_from"] == "shelf-index"
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


def test_missing_root_gives_none_snapshot(tmp_path):
    snap = corpus_snapshot.snapshot(tmp_path / "no-such-root")
    assert snap["books_total"] is None
    assert snap["books_readable"] is None
    assert snap["chapters"] is None
    assert snap["paras"] is None
    assert snap["sources"]["library"]["ok"] is False


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


def test_locked_library_db_degrades_to_none(stats_root, monkeypatch):
    """库打不开（坏库）时不抛异常，该数给 None。"""
    lib_db = stats_root / "build" / "library.db"
    lib_db.write_bytes(b"not a sqlite file")

    def broken_connect(path, *args, **kwargs):
        raise sqlite3.OperationalError("file is not a database")

    monkeypatch.setattr(corpus_snapshot.sqlite3, "connect", broken_connect)
    snap = corpus_snapshot.snapshot(stats_root)
    assert snap["books_total"] is None
    assert snap["books_readable"] is None
