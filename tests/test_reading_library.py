"""只读书库投影与真实实例读法。"""

import json

from bookcorpus.reading.library import Library


def test_library_projects_readable_books_and_counts(reading_root):
    lib = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002"]
    assert lib.book("B0003") is None
    assert lib.book("B0001")["author_display"] == "鲁迅"
    assert lib.book("B0001")["chapters"] == 2
    assert lib.book("B0001")["subject"] == "文学"
    assert lib.book("B0001")["readable"] is True
    assert lib.book("B0001")["chars"] > 0
    assert lib.chapter("B0001", 1)["n"] == 1
    assert lib.chapter("B0001", 9) is None


def test_read_chapter_keeps_blocks_and_hashes(reading_root):
    lib = Library(reading_root, overlay_root=str(reading_root.parent / "no-overlay"))
    raw = lib.read_chapter("B0001", 1)
    assert raw["uid"] == "B0001"
    assert raw["n"] == 1
    assert raw["title"] == "第1节"
    assert len(raw["blocks"]) == 1
    assert raw["blocks"][0]["id"] == "B01"
    assert len(raw["source_sha256"]) == 64


def test_catalog_search_and_metadata_overlay(tmp_path):
    from conftest import write_book

    root = tmp_path / "root"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    write_book(root, "B0001", "文学", "甲书", ["正文一。"])
    write_book(root, "B0002", "人文社科", "乙书", ["正文二。"])
    overlay = tmp_path / "overlay"
    for uid, title, author in (("B0001", "甲书", "某人"), ("M0009", "丁书", "另一个人")):
        target = overlay / "books" / uid
        target.mkdir(parents=True)
        (target / "book.json").write_text(
            json.dumps({"uid": uid, "title": title, "author": author, "subjects": ["文学"]}, ensure_ascii=False),
            encoding="utf-8",
        )
    lib = Library(root, overlay_root=str(overlay))
    data = lib.catalog("甲")
    assert [book["uid"] for book in data["books"]] == ["B0001"]
    assert data["total"] == 2
    assert [record["uid"] for record in data["metadata"]] == []
    data = lib.catalog("丁")
    assert [record["key"] for record in data["metadata"]] == ["meta:M0009"]
    assert lib.subjects()[0]["name"] in ("人文社科", "文学")


def test_library_ignores_work_overlay_by_default(tmp_path, monkeypatch):
    from conftest import write_book

    root = tmp_path / "root"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    write_book(root, "B0001", "文学", "甲书", ["正文一。"])
    overlay = tmp_path / "overlay"
    for uid in ("M0001", "M0002"):
        target = overlay / "books" / uid
        target.mkdir(parents=True)
        (target / "book.json").write_text(
            json.dumps({"uid": uid, "title": uid, "author": "某人", "subjects": ["文学"]}, ensure_ascii=False),
            encoding="utf-8",
        )
    monkeypatch.setenv("READING_OVERLAY", str(overlay))
    lib = Library(root)
    data = lib.catalog()
    assert data["total"] == 1
    assert data["metadata"] == []
