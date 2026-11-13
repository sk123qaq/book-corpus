"""读法账、派生文件、建议与解析。"""

import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bookcorpus.reading import profile
from bookcorpus.reading.service import ReadingService
from bookcorpus.scripts import lib_common as L
from conftest import run_cli, write_ledger
from test_reading_server import _start_server, _stop_server


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _put(base, path, body):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(base, path, body):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def server(reading_root):
    proc, base = _start_server(reading_root, {"READING_DEPTH": "0", "READING_ANNOTATE": "0"})
    try:
        yield base
    finally:
        _stop_server(proc)


@pytest.fixture
def depth_server(reading_root):
    proc, base = _start_server(reading_root, {"READING_DEPTH": "1", "READING_ANNOTATE": "0"})
    try:
        yield base
    finally:
        _stop_server(proc)


def test_reading_ledger_roundtrip(reading_root):
    L.reading_set_book("B0001", "narrative", root=reading_root)
    row = L.reading_get("B0001", "*", root=reading_root)
    assert row["mode"] == "narrative"
    assert row["panels"] == []

    with pytest.raises(ValueError, match="先设本书"):
        L.reading_put("B0002", "argument", chapter="1", root=reading_root)

    L.reading_set_book("B0002", "argument", root=reading_root)
    L.reading_put("B0002", "narrative", chapter="1", source_sha256="abc", root=reading_root)
    assert L.reading_get("B0002", "1", root=reading_root)["mode"] == "narrative"
    assert len(L.reading_get("B0002", root=reading_root)) == 2

    L.reading_set_book("B0002", "practical", root=reading_root)
    assert L.reading_get("B0002", "1", root=reading_root)["mode"] == "narrative"
    L.reading_set_book("B0002", "narrative", root=reading_root)
    assert L.reading_get("B0002", "1", root=reading_root) is None

    L.reading_delete("B0002", "1", root=reading_root)
    assert L.reading_get("B0002", "1", root=reading_root) is None
    L.reading_delete("B0002", "*", root=reading_root)
    assert L.reading_get("B0002", root=reading_root) == []


def test_build_library_emits_reading_json(reading_root):
    write_ledger(
        reading_root,
        [
            ("B0001", "文学", "甲书", ""),
            ("B0002", "人文社科", "乙书", ""),
            ("B0003", "写作与编剧", "丙书", "无正文"),
        ],
    )
    L.reading_set_book("B0001", "narrative", root=reading_root)
    L.reading_put("B0001", "argument", chapter="1", source_sha256="abc", root=reading_root)
    result = run_cli("library", "--emit-json", "--root", str(reading_root), cwd=str(reading_root))
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(reading_root) / "books" / "B0001" / "reading.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["generated"] is True
    assert doc["mode"] == "narrative"
    assert doc["chapters"]["1"]["mode"] == "argument"
    assert "default_tier" not in doc
    assert "mode_source" not in doc
    assert not (Path(reading_root) / "books" / "B0002" / "reading.json").exists()


def test_emit_reading_json_removes_file_without_book_row(reading_root):
    write_ledger(
        reading_root,
        [
            ("B0001", "文学", "甲书", ""),
            ("B0002", "人文社科", "乙书", ""),
            ("B0003", "写作与编剧", "丙书", "无正文"),
        ],
    )
    L.reading_set_book("B0001", "narrative", root=reading_root)
    L.reading_put("B0001", "argument", chapter="1", source_sha256="abc", root=reading_root)
    result = run_cli("library", "--emit-json", "--root", str(reading_root), cwd=str(reading_root))
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(reading_root) / "books" / "B0001" / "reading.json"
    assert path.is_file()

    conn = sqlite3.connect(Path(reading_root) / "tables" / "ledger.db")
    conn.execute("DELETE FROM 读法 WHERE uid=? AND chapter='*'", ("B0001",))
    conn.commit()
    conn.close()

    result = run_cli("library", "--emit-json", "--root", str(reading_root), cwd=str(reading_root))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not path.exists()
    assert L.reading_get("B0001", "1", root=reading_root)["mode"] == "argument"


def test_profile_resolution_precedence_and_hash():
    doc = {
        "mode": "narrative",
        "panels": ["timeline"],
        "chapters": {"1": {"mode": "argument", "source_sha256": "abc", "panels": ["argument_map"]}},
    }
    got = profile.resolve(doc, 1, "abc", None)
    assert got["effective_mode"] == "argument"
    assert got["effective_source"] == "chapter"
    assert got["panels"] == ["argument_map"]

    stale = profile.resolve(doc, 1, "changed", None)
    assert stale["effective_mode"] == "narrative"
    assert stale["effective_source"] == "book"
    assert stale["chapter_override"] is None
    assert stale["chapter_override_pending"] == "argument"
    assert stale["chapter_override_stale"] is True
    assert got["chapter_override_pending"] is None
    assert got["chapter_override_stale"] is False

    suggestion = {"mode": "practical", "confidence": 0.7}
    assert profile.resolve(doc, 2, "x", suggestion)["effective_mode"] == "narrative"
    assert profile.resolve(None, 2, "x", suggestion)["effective_mode"] == "practical"
    assert profile.resolve(None, 2, "x", None)["effective_mode"] == "argument"


def test_rule_suggestions_and_cache(reading_root):
    service = ReadingService(reading_root, annotate=False)
    narrative = service.reading("B0001", 1)
    argument = service.reading("B0002", 1)
    assert narrative["effective_mode"] == "narrative"
    assert argument["effective_mode"] == "argument"
    assert narrative["suggestion"]["source"] == "rule"
    cache = Path(reading_root) / "build" / "reading" / "suggestions" / "B0001" / "1.json"
    assert cache.is_file()


def test_reading_api_put_and_409(server, reading_root, depth_server):
    status, chapter = _get(server, "/api/chapter/B0001/1")
    assert status == 200
    assert chapter["reading"]["effective_source"] == "suggestion"
    assert "default_tier" not in chapter["reading"]
    body_before = chapter["blocks"]

    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "book_default", "mode": "narrative"})
    assert status == 200
    assert data["effective_source"] == "book"

    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "override", "mode": "argument"})
    assert status == 200
    assert data["effective_source"] == "chapter"
    status, after_override = _get(server, "/api/chapter/B0001/1")
    assert status == 200
    assert after_override["blocks"] == body_before

    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "follow"})
    assert status == 200
    assert data["effective_source"] == "book"
    assert data["chapter_override"] is None

    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "override", "mode": "practical"})
    assert status == 200
    assert data["chapter_override"] == "practical"
    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "book_default", "mode": "argument"})
    assert status == 200
    assert data["book_mode"] == "argument"
    assert data["chapter_override"] == "practical"

    status, data = _put(server, "/api/chapter/B0001/1/reading", {"action": "override", "mode": "argument"})
    assert status == 200
    assert data["chapter_override"] is None

    status, data = _put(server, "/api/chapter/B0002/1/reading", {"action": "override", "mode": "argument"})
    assert status == 409
    assert data["code"] == "reading_profile_required"

    status, data = _post(depth_server, "/api/depth/B0002/1", {"layers": ["skim"]})
    assert status == 409
    assert data["code"] == "reading_profile_required"


def test_reading_profile_rejects_hand_edited_file(reading_root):
    path = Path(reading_root) / "books" / "B0001" / "reading.json"
    path.write_text(json.dumps({"version": 1, "mode": "narrative"}), encoding="utf-8")
    assert profile.read_profile("B0001", reading_root) is None
    path.write_text(
        json.dumps({"version": 1, "generated": True, "mode": "narrative", "chapters": {}}),
        encoding="utf-8",
    )
    assert profile.read_profile("B0001", reading_root)["mode"] == "narrative"


def test_book_reading_put_sets_default_without_job(server):
    _, before = _get(server, "/api/jobs")
    status, data = _put(server, "/api/book/B0001/reading", {"mode": "narrative"})
    assert status == 200
    assert data["book_mode"] == "narrative"
    _, after = _get(server, "/api/jobs")
    assert len(after["jobs"]) == len(before["jobs"])

    status, book = _get(server, "/api/book/B0001")
    assert status == 200
    assert book["reading"]["book_mode"] == "narrative"

    status, data = _put(server, "/api/book/B0001/reading", {"mode": "bogus"})
    assert status == 400
    status, data = _put(server, "/api/book/B9999/reading", {"mode": "narrative"})
    assert status == 404
