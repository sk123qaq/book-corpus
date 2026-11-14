"""章表索引：与逐文件扫描等价、增量只读变化文件、口径回归。"""

import builtins
import re
import sqlite3
from pathlib import Path

from bookcorpus.reading.chapters import book_totals
from bookcorpus.reading.library import Library
from bookcorpus.reading.mdparse import _take_h1, chapter_title, lean_meta, non_space_count, strip_front_matter
from bookcorpus.reading.shelf_index import PARSER_VERSION, ShelfIndex


def _ref_meta(raw):
    """旧口径的逐字段参考实现（空白用 re，缩进用 split）。"""
    _meta, body = strip_front_matter(raw)
    _, rest = _take_h1(body)
    return {
        "title": chapter_title(raw),
        "chars": len(re.sub(r"\s", "", rest)),
        "full_chars": len(re.sub(r"\s", "", body)),
    }


def _write_chapter(root, uid, name, text):
    md = Path(root) / "books" / uid / "md"
    md.mkdir(parents=True, exist_ok=True)
    (md / name).write_text(text, encoding="utf-8")


def _book_json(root, uid, title="索引测试"):
    path = Path(root) / "books" / uid / "book.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            f'{{"uid": "{uid}", "title": "{title}", "author": "某人", "subjects": ["文学"]}}',
            encoding="utf-8",
        )


def test_lean_meta_matches_legacy_parser(reading_root):
    cases = [
        "",
        "   \n\n",
        "第一行不是标题\n\n正文\n",
        "# 只有标题",
        "# 标题\n\n正文一 二 三\n",
        "---\ntitle: 卷首\n---\n\n# 章\n\n正文\u3000带 全角\xa0空白\n",
        '---\r\ntitle: "引号"\r\n---\r\n\r\n# 章\r\n\r\n正文\r\n',
        "\ufeff# 带 BOM 的标题\n\n正文\n",
        "\n\n\n# 隔了几行\n\n正文\n",
        "---\ntitle: 未闭合\n# 不是分隔线\n\n正文\n",
        "---\ntitle: 六十行内\n   \n---\n正文\n",
    ]
    for raw in cases:
        assert lean_meta(raw) == _ref_meta(raw), raw[:40]


def test_non_space_count_matches_regex_on_whitespace_set():
    text = "".join(chr(i) for i in range(0x3000 + 1)) + "\u3000\u2028\u2029\x85\x1c"
    assert non_space_count(text) == len(re.sub(r"\s", "", text))
    assert non_space_count("") == 0


def test_index_matches_direct_scan_and_reports_totals(reading_root):
    lib = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    books = lib.books()
    assert [(b["uid"], b["chapters"]) for b in books] == [("B0001", 2), ("B0002", 1)]
    assert books[0]["chars"] == sum(c["full_chars"] for c in lib.chapters("B0001"))
    db = Path(reading_root) / "build" / "阅读" / "shelf-index.db"
    assert db.exists()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM chapter").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM book").fetchone()[0] == 2
    conn.close()


def test_second_library_and_ensure_parse_nothing(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    first = index.ensure()
    assert first["mode"] == "fresh"
    assert first["parsed"] == 3
    again = index.ensure()
    assert again["parsed"] == 0
    assert again["removed"] == 0
    assert index.stats()["chapters"] == 3


def test_changed_chapter_reparsed_on_next_ensure(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    path = Path(reading_root) / "books" / "B0001" / "md" / "002_第2节.md"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n补一句新的正文。\n")
    result = index.ensure()
    assert result["parsed"] == 1
    assert result["removed"] == 0
    row = index.chapters("B0001")[1]
    assert row["chars"] > 0
    assert index.book_stats()["B0001"]["chapters"] == 2


def test_deleted_chapter_removed_and_book_recounted(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    (Path(reading_root) / "books" / "B0002" / "md" / "001_第1节.md").unlink()
    result = index.ensure()
    assert result["removed"] == 1
    assert "B0002" not in index.book_stats()


def test_runtime_edit_seen_when_book_opened(reading_root):
    lib = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    before = lib.book("B0001")["chars"]
    path = Path(reading_root) / "books" / "B0001" / "md" / "002_第2节.md"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + "新正文" * 50 + "\n")
    after = lib.book("B0001")["chars"]
    assert after > before
    assert lib.chapters("B0001")[1]["chars"] > 0
    assert lib.chapter("B0001", 2)["chars"] == lib.chapters("B0001")[1]["chars"]


def test_title_page_merge_shared_by_index_and_library(reading_root):
    uid = "B0009"
    _book_json(reading_root, uid, "合并测试")
    _write_chapter(reading_root, uid, "01_第一章.md", "# 第一章\n\n题记很短。\n")
    _write_chapter(reading_root, uid, "02_前置页 正文.md", "# 第一章\n\n" + "正文" * 400 + "\n")
    lib = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    chapters = lib.chapters(uid)
    assert len(chapters) == 1
    assert chapters[0]["n"] == 1
    assert chapters[0]["merged"] is True
    assert chapters[0]["body_n"] == 2
    assert lib.redirect_target(uid, 2) == 1
    assert lib.book(uid)["chapters"] == 1
    raw = lib.chapters(uid)
    assert book_totals(raw)[0] == 1


def test_single_unnumbered_md_falls_back_to_chapter_one(tmp_path):
    root = tmp_path / "single"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    _book_json(root, "B0010", "单文件")
    _write_chapter(root, "B0010", "正文.md", "# 全书\n\n正文。\n")
    lib = Library(root, overlay_root=str(root / "no-overlay"))
    chapters = lib.chapters("B0010")
    assert [(c["n"], c["stem"]) for c in chapters] == [(1, "正文")]
    assert lib.book("B0010")["chapters"] == 1


def test_books_does_not_open_chapter_files(reading_root, monkeypatch):
    first = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    first.books()
    second = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    real_open = builtins.open

    def guard(path, *args, **kwargs):
        if str(path).endswith(".md"):
            raise AssertionError(f"书目投影不应读章节文件：{path}")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard)
    books = second.books()
    assert [b["uid"] for b in books] == ["B0001", "B0002"]


def test_version_bump_triggers_full_rebuild(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    conn = sqlite3.connect(index.path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'parser_version'", (str(PARSER_VERSION + 1),))
    conn.commit()
    conn.close()
    result = index.ensure()
    assert result["mode"] == "rebuild"
    assert result["parsed"] == 3


def test_corrupt_index_is_quarantined_and_rebuilt(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    Path(index.path).write_bytes(b"not a database at all")
    result = index.ensure()
    assert result["parsed"] == 3
    assert index.stats()["chapters"] == 3
    names = [p.name for p in Path(index.path).parent.iterdir()]
    assert any(name.startswith("shelf-index.db.corrupt-") for name in names)


def test_read_heals_when_index_file_disappears(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{index.path}{suffix}")
        if path.exists():
            path.unlink()
    rows = index.chapters("B0001")
    assert [row["n"] for row in rows] == [1, 2]
    assert index.stats()["chapters"] == 3


def test_iter_chapters_heals_before_first_row(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{index.path}{suffix}")
        if path.exists():
            path.unlink()
    groups = list(index.iter_chapters())
    assert [uid for uid, _rows in groups] == ["B0001", "B0002"]
    assert sum(len(rows) for _uid, rows in groups) == 3


def test_corrupt_index_heals_on_read_not_only_ensure(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    Path(index.path).write_bytes(b"broken header")
    stats = index.book_stats()
    assert stats["B0001"]["chapters"] == 2
    assert index.stats()["chapters"] == 3


def test_rebuild_keeps_bak_not_corrupt(reading_root):
    index = ShelfIndex(Library(reading_root).layout)
    index.ensure()
    index.rebuild()
    names = [p.name for p in Path(index.path).parent.iterdir()]
    assert any(name.startswith("shelf-index.db.bak-") for name in names)
    assert not any(name.startswith("shelf-index.db.corrupt-") for name in names)


def test_shelf_payload_carries_total(reading_root):
    from bookcorpus.reading.service import ReadingService

    service = ReadingService(reading_root)
    shelf = service.shelf()
    assert shelf["total"] == 2
    assert shelf["stats"]["readable"] == 2
    assert service.catalog()["total"] == 2
