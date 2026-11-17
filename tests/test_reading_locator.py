"""分层读源码行号、前置页归属与 `/open` 解析器单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from bookcorpus.reading.library import Library
from bookcorpus.reading.locator import locate_blocks, resolve_open_target
from bookcorpus.reading.mdparse import parse_markdown


def test_parse_markdown_tracks_source_lines_across_front_matter_and_h1():
    raw = '---\ntitle: "测试章"\n---\n\n# 测试章\n\n第一段第一行\n第一段第二行\n\n## 小节\n\n```python\nx = 1\n```\n'
    parsed = parse_markdown(raw)
    blocks = parsed["blocks"]
    assert [b["id"] for b in blocks] == ["B01", "B02", "B03"]
    assert blocks[0]["start_line"] == 7
    assert blocks[0]["end_line"] == 8
    assert blocks[1]["start_line"] == 10
    assert blocks[1]["end_line"] == 10
    assert blocks[2]["start_line"] == 12
    assert blocks[2]["end_line"] == 14


def test_locator_handles_merged_title_page_and_line_drift(tmp_path: Path):
    root = tmp_path / "ws"
    md_dir = root / "books" / "B0010" / "md"
    md_dir.mkdir(parents=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    (root / "books" / "B0010" / "book.json").write_text(
        json.dumps({"uid": "B0010", "title": "合并测试书", "subjects": ["文学"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (md_dir / "01_题记.md").write_text("# 题记\n\n短短题记。\n", encoding="utf-8")
    (md_dir / "02_前置页 正文.md").write_text(
        "---\ntitle: 正文\n---\n\n# 正文\n\n第一段开篇交代。\n\n"
        + ("长正文铺陈。" * 120)
        + "\n\n关键一句：灯下压着一封没有署名的信。\n",
        encoding="utf-8",
    )

    lib = Library(str(root))
    target = resolve_open_target(
        lib,
        uid="B0010",
        path="books/B0010/md/02_前置页 正文.md",
        line=7,
    )
    assert target.kind == "chapter"
    assert target.n == 1
    assert target.block_ids == ("B02",)

    # 行号落在 front matter（第 2 行）时应钳位到该文件第一块 B02，而非合并页的 B01
    fm_target = resolve_open_target(
        lib,
        uid="B0010",
        path="books/B0010/md/02_前置页 正文.md",
        line=2,
    )
    assert fm_target.block_ids == ("B02",)

    # 即使行号陈旧偏移，引文探针仍可纠偏到真实块
    stale_target = resolve_open_target(
        lib,
        uid="B0010",
        path="books/B0010/md/02_前置页 正文.md",
        line=7,
        quote="灯下压着一封没有署名的信",
    )
    assert stale_target.block_ids == ("B04",)


def test_locate_blocks_spans_adjacent_blocks_and_fallbacks():
    blocks = [
        {
            "id": "B01",
            "kind": "paragraph",
            "text": "前文铺垫结束于此。",
            "source_file": "01.md",
            "start_line": 3,
            "end_line": 3,
        },
        {
            "id": "B02",
            "kind": "paragraph",
            "text": "后文承接由此展开。",
            "source_file": "01.md",
            "start_line": 5,
            "end_line": 5,
        },
    ]
    assert locate_blocks(blocks, "01.md", line=3, quote="结束于此。后文承接") == ("B01", "B02")


def test_library_refreshes_newly_added_book_and_handles_non_chapter_path(reading_root: Path):
    lib = Library(str(reading_root))
    readme = reading_root / "books" / "B0001" / "md" / "README.md"
    readme.write_text("# 目录\n\n简介。\n", encoding="utf-8")

    # 非正文章节文件退回书目页
    book_target = resolve_open_target(lib, uid="B0001", path="books/B0001/md/README.md")
    assert book_target.kind == "book"
    assert book_target.uid == "B0001"

    # 运行中新增拆章书籍，不重建 Library 实例也能解析
    new_md = reading_root / "books" / "B0099" / "md"
    new_md.mkdir(parents=True)
    (reading_root / "books" / "B0099" / "book.json").write_text(
        json.dumps({"uid": "B0099", "title": "新书", "subjects": ["历史"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (new_md / "01_开篇.md").write_text("# 开篇\n\n新书第一段。\n", encoding="utf-8")
    new_target = resolve_open_target(lib, path="books/B0099/md/01_开篇.md", line=3)
    assert new_target.kind == "chapter"
    assert new_target.uid == "B0099"
    assert new_target.n == 1
    assert new_target.block_ids == ("B01",)

    # 仅有元数据无正文返回 not_found
    missing = resolve_open_target(lib, uid="B0003")
    assert missing.kind == "not_found"
