"""三个服务必须报同一份快照：8765 检索 / 8771 伴读 / 8770 操作台。

同一 tmp 根上建齐三库（library.db、index.db、shelf-index.db），分别取
demo trace.meta()、ops_status.collect()、ReadingService.shelf()，四个数
（可读 / 藏书 / 章 / 段）加字数必须完全相等。旧代码上这条会红：demo 的
books 是 count(*) FROM book（含无正文书），reading 的章是合并导航口径，
ops 的可读把 0 章 md 算进去。
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO / "apps" / "demo"
SCRIPTS_DIR = REPO / "src" / "bookcorpus" / "scripts"

import pytest

from conftest import run_cli, write_book, write_ledger

STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


@pytest.fixture(name="aligned_root")
def _aligned_root(tmp_path):
    """建齐三库的临时根：B0001 两章三段、B0002 一章一段、B0003 无 md。"""
    env = {"BOOKS_ROOT": str(tmp_path)}
    assert run_cli("init", str(tmp_path), "--no-pdf-venv", env=env).returncode == 0
    write_book(tmp_path, "B0001", "人文", "甲书", ["甲书第一段，礼治。", "甲书第二段，秩序。"])
    write_book(tmp_path, "B0002", "人文", "乙书", ["乙书唯一一段，无争。"])
    write_book(tmp_path, "B0003", "人文", "丙书", None, fmt="txt")
    write_ledger(
        tmp_path,
        [("B0001", "人文", "甲书", ""), ("B0002", "人文", "乙书", ""), ("B0003", "人文", "丙书", "")],
    )
    assert run_cli("library", env=env).returncode == 0
    assert run_cli("index", "--rebuild", env=env).returncode == 0
    # shelf-index.db：伴读服务首用自建；这里先建好，保证三页读到同一来源
    from bookcorpus.reading.shelf_index import ShelfIndex
    from bookcorpus.scripts.layout import Layout

    ShelfIndex(Layout(str(tmp_path))).ensure()
    return tmp_path


def test_three_services_report_the_same_snapshot(aligned_root):
    root = str(aligned_root)
    # ---- 8765 demo：进程内取 meta ----
    if str(DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(DEMO_DIR))
    import trace as tracer

    import context as demo_context

    demo_context.configure_runtime(root)
    meta = tracer.meta(refresh=True)

    # ---- 8770 ops：进程内取 collect ----
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import ops_status

    state = ops_status.collect(ops_status.Paths(root), deep=False)

    # ---- 8771 reading：进程内取 shelf ----
    from bookcorpus.reading.service import ReadingService

    shelf = ReadingService(root).shelf()
    stats = shelf["stats"]

    # 可读：md 目录存在且至少 1 章。B0003 无 md，不算可读。
    assert meta["books"] == stats["readable"] == state["books_readable"] == 2, (
        meta["books"],
        stats["readable"],
        state["books_readable"],
    )
    # 藏书：全库书目，含无正文书。与「可读」分列。
    assert meta["books_total"] == state["books"] == 3, (meta["books_total"], state["books"])
    # 章：md 文件视角，未合并标题页。
    assert meta["chapters"] == stats["chapters"] == state["stats_chapters"] == 3, (
        meta["chapters"],
        stats["chapters"],
        state["stats_chapters"],
    )
    # 段：索引 para 行数（仅含有词元的段）。
    assert meta["paras"] == stats["paras"] == state["index"]["paras"] == 3, (
        meta["paras"],
        stats["paras"],
        state["index"]["paras"],
    )
    # 字：非空白正文字数；三页同源，数值一致且非 0。
    chars_triple = (meta["chars"], stats["chars"], state["stats_chars"])
    assert meta["chars"] == stats["chars"] == state["stats_chars"], chars_triple
    assert (meta["chars"] or 0) > 0

    # built_at 是数据时刻（各库自身的更新戳，不是各自计算时刻）：库没变，三页必须同一秒。
    assert meta["built_at"] == stats["built_at"] == state["stats_built_at"], (
        meta["built_at"],
        stats["built_at"],
        state["stats_built_at"],
    )
    assert STAMP_RE.match(meta["built_at"] or ""), meta["built_at"]
