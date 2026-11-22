"""单一统计源：本 / 章 / 段 / 字 的一份快照。

8765 检索（apps/demo）、8771 伴读（apps/reading）、8770 操作台（apps/ops）
的页头数字都从这里取，三个页面同一时刻显示同一组数。

口径（2026-11-22 定，测试见 tests/test_corpus_snapshot.py）：

- 藏书 ``books_total``：library.db ``book`` 表总行数，含无正文书。
- 可读 ``books_readable``：library.db ``derived`` 里 ``kind='md'`` 且章数 > 0
  的书——即「md 目录存在且至少 1 章」；0 章的 md（图片书、图册）不算可读。
- 章 ``chapters``：md 文件视角（拆章后的 Markdown 文件数），不是合并标题页
  后的导航章数。优先取伴读自建 ``build/阅读/shelf-index.db`` 的 chapter
  行数，缺库退回 library.db derived 的 chapters 求和；两源同一文件口径。
- 段 ``paras``：``build/检索/index.db`` 的 ``para`` 行数——索引段，仅含有
  词元的可检索段落；meta 缺行时退回 ``SELECT count(*) FROM para``。
- 字 ``chars``：shelf-index chapter 表 ``full_chars`` 求和——每个 md 文件
  恰计一次、含标题页、非空白。缺 shelf-index 时为 None，不做全盘扫描兜底。

所有 SQLite 只读打开；缺库、锁库、坏库一律给 None，不抛异常——页面照常
渲染，用「—」和提示行说明缺什么。
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .scripts.layout import Layout, find_root

__all__ = ["snapshot"]


def _ro(path: Path) -> sqlite3.Connection | None:
    """只读打开一个 SQLite；打不开（缺库、锁库、坏库）给 None，不抛。"""
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _scalar(con: sqlite3.Connection | None, sql: str) -> int | None:
    if con is None:
        return None
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _meta(con: sqlite3.Connection | None) -> dict[str, str]:
    if con is None:
        return {}
    try:
        rows = con.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.Error:
        return {}
    return {str(key): str(value) for key, value in rows}


def snapshot(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """算一份快照。``root`` 给实例根（调用方已解析的路径）；None 时按 BOOKS_ROOT / books.toml 找。

    返回::

        {
            "books_total": int | None,  # 藏书（library.db book 表）
            "books_readable": int | None,  # 可读（derived md 且章数 > 0）
            "chapters": int | None,  # 章（md 文件视角）
            "paras": int | None,  # 段（index para 行数）
            "chars": int | None,  # 字（非空白正文，shelf-index full_chars）
            "built_at": str,  # 快照生成时刻（本地时间）
            "sources": {...},  # 每个数从哪个库来，含 built_at
        }
    """
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if root is None:
        found = find_root()
        if not found:
            return _empty(built_at)
        layout = Layout(found)
    else:
        layout = Layout(str(Path(root).expanduser().resolve()))

    lib_db = Path(layout.library_db)
    idx_db = Path(layout.index_db)
    shelf_db = Path(layout.root) / "build" / "阅读" / "shelf-index.db"

    lib = _ro(lib_db)
    shelf = _ro(shelf_db)
    idx = _ro(idx_db)
    idx_meta = _meta(idx)
    shelf_meta = _meta(shelf)

    books_total = _scalar(lib, "SELECT count(*) FROM book")
    books_readable = _scalar(
        lib,
        "SELECT count(*) FROM derived WHERE kind = 'md' AND status = 'done' AND coalesce(chapters, 0) > 0",
    )

    chapters = _scalar(shelf, "SELECT count(*) FROM chapter")
    chapters_from = "shelf-index" if chapters is not None else None
    if chapters is None:
        chapters = _scalar(lib, "SELECT coalesce(sum(chapters), 0) FROM derived WHERE kind = 'md'")
        chapters_from = "derived" if chapters is not None else None

    chars = _scalar(shelf, "SELECT coalesce(sum(full_chars), 0) FROM chapter")
    chars_from = "shelf-index" if chars is not None else None

    paras: int | None = None
    if idx is not None:
        try:
            paras = int(idx_meta.get("paras", ""))
        except ValueError:
            paras = None
        if paras is None:
            paras = _scalar(idx, "SELECT count(*) FROM para")

    for con in (lib, shelf, idx):
        if con is not None:
            con.close()

    return {
        "books_total": books_total,
        "books_readable": books_readable,
        "chapters": chapters,
        "paras": paras,
        "chars": chars,
        "built_at": built_at,
        "sources": {
            "library": {"db": str(lib_db), "ok": lib is not None},
            "index": {"db": str(idx_db), "ok": idx is not None, "built_at": idx_meta.get("built_at", "")},
            "shelf_index": {
                "db": str(shelf_db),
                "ok": shelf is not None,
                "built_at": shelf_meta.get("built_at", ""),
            },
            "chapters_from": chapters_from,
            "chars_from": chars_from,
        },
    }


def _empty(built_at: str) -> dict[str, Any]:
    return {
        "books_total": None,
        "books_readable": None,
        "chapters": None,
        "paras": None,
        "chars": None,
        "built_at": built_at,
        "sources": {
            "library": {"db": None, "ok": False},
            "index": {"db": None, "ok": False, "built_at": ""},
            "shelf_index": {"db": None, "ok": False, "built_at": ""},
            "chapters_from": None,
            "chars_from": None,
        },
    }
