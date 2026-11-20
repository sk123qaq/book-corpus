"""只读书库投影。"""

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


def test_library_explicit_root_ignores_books_root_env(tmp_path, monkeypatch):
    from conftest import write_book

    env_root = tmp_path / "env_ws"
    env_root.mkdir()
    (env_root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    write_book(env_root, "B0001", "文学", "环境书", ["环境正文。"])

    explicit_root = tmp_path / "explicit_ws"
    explicit_root.mkdir()
    (explicit_root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    write_book(explicit_root, "B0002", "文学", "显式书", ["显式正文。"])

    monkeypatch.setenv("BOOKS_ROOT", str(env_root))
    lib = Library(explicit_root)
    assert lib.root == str(explicit_root.resolve())
    assert [b["uid"] for b in lib.books()] == ["B0002"]


def test_shelf_picks_up_landed_book_without_restart(reading_root, monkeypatch):
    from conftest import write_book

    monkeypatch.setenv("READING_SHELF_TTL", "0")
    lib = Library(reading_root)
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002"]
    write_book(reading_root, "B0009", "文学", "新落地", ["新书第一段。"])
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002", "B0009"]


def test_shelf_reflects_book_json_edit(reading_root, monkeypatch):
    monkeypatch.setenv("READING_SHELF_TTL", "0")
    lib = Library(reading_root)
    assert lib.books()[0]["title"] == "甲书"
    payload_path = reading_root / "books" / "B0001" / "book.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["title"] = "甲书改名之后"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    assert lib.books()[0]["title"] == "甲书改名之后"


def test_shelf_drops_book_whose_md_is_gone(reading_root, monkeypatch):
    import shutil

    monkeypatch.setenv("READING_SHELF_TTL", "0")
    lib = Library(reading_root)
    assert "B0002" in [book["uid"] for book in lib.books()]
    shutil.rmtree(reading_root / "books" / "B0002" / "md")
    assert [book["uid"] for book in lib.books()] == ["B0001"]


def test_shelf_does_not_rebuild_when_disk_unchanged(reading_root, monkeypatch):
    monkeypatch.setenv("READING_SHELF_TTL", "0")
    lib = Library(reading_root)
    index = lib._shelf_index()
    calls = []
    original = index.ensure

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(index, "ensure", counted)
    lib.books()
    assert len(calls) == 1
    lib.books()
    assert len(calls) == 1


def test_shelf_keeps_old_list_when_index_busy(reading_root, monkeypatch):
    from conftest import write_book

    monkeypatch.setenv("READING_SHELF_TTL", "0")
    lib = Library(reading_root)
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002"]
    write_book(reading_root, "B0009", "文学", "新落地", ["新书第一段。"])
    index = lib._shelf_index()
    original = index.ensure
    state = {"busy": True}

    def flaky():
        if state["busy"]:
            return {"mode": "busy", "parsed": 0, "removed": 0, "books": 0, "chapters": 0}
        return original()

    monkeypatch.setattr(index, "ensure", flaky)
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002"]
    state["busy"] = False
    assert [book["uid"] for book in lib.books()] == ["B0001", "B0002", "B0009"]


def test_shelf_signature_scan_is_throttled(reading_root, monkeypatch):
    monkeypatch.delenv("READING_SHELF_TTL", raising=False)
    lib = Library(reading_root)
    calls = []
    original = lib._books_signature

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(lib, "_books_signature", counted)
    lib.books()
    lib.books()
    lib.books()
    assert len(calls) == 1
