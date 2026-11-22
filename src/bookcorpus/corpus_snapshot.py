"""单一统计源：本 / 章 / 段 / 字 的一份快照。

8765 检索（apps/demo）、8771 伴读（apps/reading）、8770 操作台（apps/ops）
的页头数字都从这里取，三个页面同一时刻显示同一组数。

口径（2026-11-22 定，测试见 tests/test_corpus_snapshot.py）：

- 藏书 ``books_total``：library.db ``book`` 表总行数，含无正文书。
- 可读 ``books_readable``：library.db ``derived`` 里 ``kind='md'`` 且章数 > 0
  的书——即「md 目录存在且至少 1 章」；0 章的 md（图片书、图册）不算可读。
- 章 ``chapters``：md 文件视角（拆章后的 Markdown 文件数），不是合并标题页
  后的导航章数。只取 library.db derived 的 chapters 求和——``book-corpus
  library`` 一跑就更新，三个页面跟着同一时刻对齐。伴读自建的
  shelf-index.db 落后于 derived 时（逐书扫描是懒的），若章数优先取它会
  出现「8765/8770 显示 3、8771 新进程显示 4」的分叉，所以章不用它。
- 段 ``paras``：``build/检索/index.db`` 的 ``para`` 行数——索引段，仅含有
  词元的可检索段落；meta 缺行时退回 ``SELECT count(*) FROM para``。
- 字 ``chars``：shelf-index chapter 表 ``full_chars`` 求和——每个 md 文件
  恰计一次、含标题页、非空白。缺 shelf-index 时为 None，不做全盘扫描兜底。

``built_at`` 是数据时刻：各库记录的最新一次更新时间（library.db meta 的
``built_at``，缺了退 derived ``generated_at`` 最大值；index.db / shelf-index
各自的 ``built_at``）里最新的那个。数字没变时间就不动，页面不会拿计算
时刻冒充数据时刻；一个时间戳都拿不到时才退回快照计算时刻（记在
``sources.computed_at``）。

所有 SQLite 只读打开；缺库、锁库、坏库一律给 None，不抛异常——页面照常
渲染，用「—」和提示行说明缺什么。
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .scripts.layout import Layout, find_root

__all__ = ["snapshot"]


def _ro(path: Path) -> sqlite3.Connection | None:
    """只读打开一个 SQLite；打不开（缺库、锁库、坏库）给 None，不抛。

    connect 是惰性的，坏库文件要到第一次查询才报错，所以补一条探针
    查询，让 ok 标志真正代表「能读」，而不是「文件存在」。
    """
    if not path.is_file():
        return None
    con = None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        con.execute("SELECT 1").fetchone()
        return con
    except sqlite3.Error:
        if con is not None:
            con.close()
        return None


def _scalar(con: sqlite3.Connection | None, sql: str) -> int | None:
    if con is None:
        return None
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _text(con: sqlite3.Connection | None, sql: str) -> str:
    """取一个文本标量；表不存在、行不存在都给空串。"""
    if con is None:
        return ""
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row and row[0] is not None else ""


def _meta(con: sqlite3.Connection | None) -> dict[str, str]:
    if con is None:
        return {}
    try:
        rows = con.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.Error:
        return {}
    return {str(key): str(value) for key, value in rows}


def _newest_stamp(*stamps: str) -> str:
    """若干时间戳里最新的那个，统一格式化为本地 ``YYYY-MM-DD HH:MM:SS``。

    数据源的时间戳形状不一（library/index 是带时区的 isoformat，
    shelf-index 是无时区的 ``YYYY-MM-DD HH:MM:SS``），这里先比大小再统一
    出同一形状，让三页「统计于」长得一样。naive 时间戳按本地时区算。
    """
    best_dt = None
    for raw in stamps:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.astimezone()
        if best_dt is None or dt > best_dt:
            best_dt = dt
    if best_dt is None:
        return ""
    return best_dt.astimezone().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def snapshot(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """算一份快照。``root`` 给实例根（调用方已解析的路径）；None 时按 BOOKS_ROOT / books.toml 找。

    返回::

        {
            "books_total": int | None,  # 藏书（library.db book 表）
            "books_readable": int | None,  # 可读（derived md 且章数 > 0）
            "chapters": int | None,  # 章（md 文件视角，derived 求和）
            "paras": int | None,  # 段（index para 行数）
            "chars": int | None,  # 字（非空白正文，shelf-index full_chars）
            "built_at": str,  # 数据时刻（各库最新一次更新）；拿不到时是计算时刻
            "sources": {...},  # 每个数从哪个库来，含各库自己的时间戳
        }
    """
    computed_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if root is None:
        found = find_root()
        if not found:
            return _empty(computed_at)
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

    # 章：derived md 文件求和。shelf-index 落后于盘上时（懒扫描）不能当章数，
    # 否则三页分叉；library.db 一跑就更新，是三页共享的权威。
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

    # 数据时刻：library.db 自己的 built_at（build_library 每次重建都写），
    # 旧库没这行就退 derived 里 generated_at 的最大值。
    lib_stamp = _text(lib, "SELECT value FROM meta WHERE key = 'built_at'")
    if not lib_stamp:
        lib_stamp = _text(lib, "SELECT max(generated_at) FROM derived WHERE kind = 'md'")
    built_at = _newest_stamp(lib_stamp, idx_meta.get("built_at", ""), shelf_meta.get("built_at", "")) or computed_at

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
            "library": {"db": str(lib_db), "ok": lib is not None, "built_at": lib_stamp},
            "index": {"db": str(idx_db), "ok": idx is not None, "built_at": idx_meta.get("built_at", "")},
            "shelf_index": {
                "db": str(shelf_db),
                "ok": shelf is not None,
                "built_at": shelf_meta.get("built_at", ""),
            },
            "chapters_from": chapters_from,
            "chars_from": chars_from,
            "computed_at": computed_at,
        },
    }


def _empty(computed_at: str) -> dict[str, Any]:
    return {
        "books_total": None,
        "books_readable": None,
        "chapters": None,
        "paras": None,
        "chars": None,
        "built_at": computed_at,
        "sources": {
            "library": {"db": None, "ok": False, "built_at": ""},
            "index": {"db": None, "ok": False, "built_at": ""},
            "shelf_index": {"db": None, "ok": False, "built_at": ""},
            "chapters_from": None,
            "chars_from": None,
            "computed_at": computed_at,
        },
    }
