"""检索演示服务的实例路径上下文。

将所有运行态目录（sessions、terms、answers、related、运行记录）从工具仓
``apps/demo/`` 收口到工作区 ``<root>/工作区/检索/``，并支持按 ``--root`` 运行期配置。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookcorpus.scripts.layout import Layout, find_root


@dataclass(frozen=True)
class DemoPaths:
    """检索服务的只读数据与工作区运行态路径集合。"""

    root: Path
    books: Path
    index_db: Path
    lib_db: Path
    work_search: Path
    sessions_dir: Path
    terms_dir: Path
    answers_dir: Path
    related_dir: Path
    runlog_dir: Path


def resolve_root(root: str | Path | None = None) -> Path:
    """显式 ``--root`` 优先，其次 ``BOOKS_ROOT`` / ``books.toml``，最后当前目录。"""
    if root is not None and str(root).strip():
        return Path(str(root)).expanduser().resolve()
    found = find_root()
    return Path(found).resolve() if found else Path.cwd().resolve()


def build_paths(root: str | Path | None = None) -> DemoPaths:
    """根据工作区根构造检索服务的完整路径映射。"""
    resolved = resolve_root(root)
    layout = Layout(str(resolved))
    return DemoPaths(
        root=Path(layout.root),
        books=Path(layout.books),
        index_db=Path(layout.index_db),
        lib_db=Path(layout.library_db),
        work_search=Path(layout.work_search),
        sessions_dir=Path(layout.search_sessions),
        terms_dir=Path(layout.search_terms),
        answers_dir=Path(layout.search_answers),
        related_dir=Path(layout.search_related),
        runlog_dir=Path(layout.search_runlog),
    )


def apply_shared_scripts(paths: DemoPaths) -> None:
    """同步更新已加载的检索脚本模块中的全局路径常量。"""
    import lib_common as L

    root_str = str(paths.root)
    layout = Layout(root_str)
    L.ROOT = root_str
    L.PATHS = layout
    L.BOOKS = layout.books
    L.COLLECTIONS = layout.collections
    L.TABLES = layout.tables
    L.BUILD = layout.build
    L.WORK = layout.work

    for name in ("book_score", "book_cards", "vec_search"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if hasattr(mod, "PATHS"):
            mod.PATHS = layout
        if hasattr(mod, "ROOT"):
            mod.ROOT = root_str
        if hasattr(mod, "DB"):
            mod.DB = layout.index_db
        if hasattr(mod, "LIBDB"):
            mod.LIBDB = layout.library_db
        if name == "vec_search" and hasattr(mod, "clear_caches"):
            mod.clear_caches()


def configure_runtime(root: str | Path | None = None) -> DemoPaths:
    """配置全部已加载的 ``apps/demo`` 子模块到指定工作区实例。"""
    paths = build_paths(root)
    apply_shared_scripts(paths)
    for mod_name in ("trace", "answer", "session", "related"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "configure"):
            mod.configure(paths)
    runlog_mod = sys.modules.get("runlog")
    if runlog_mod is not None:
        env_runlog = os.environ.get("BOOKS_RUNLOG_ROOT")
        runlog_mod.configure(env_runlog or str(paths.runlog_dir))
    return paths
