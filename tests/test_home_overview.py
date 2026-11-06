"""8773 首页的取数与模板：真实 Library、转义、空日课。"""

import datetime as dt
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "apps" / "home"))

import overview

from bookcorpus.reading.library import Library

TEMPLATE = (REPO / "apps" / "home" / "web" / "index.html").read_text(encoding="utf-8")
URLS = {
    "search": "http://127.0.0.1:9999/search",
    "reading": "http://127.0.0.1:9998/read",
    "daily": "http://127.0.0.1:9997/daily",
    "ops": "http://127.0.0.1:9996/ops",
}


def _issue(**changes):
    payload = {
        "version": 1,
        "date": "2026-11-06",
        "issue": 1,
        "kind": "文学",
        "kindLabel": "文学",
        "subject": "测试",
        "book": {"uid": "B0001", "title": "甲书", "author": "鲁迅"},
        "chapter": {
            "index": 1,
            "title": "第1节",
            "path": "books/B0001/md/001_第1节.md",
        },
        "headline": "灯下的一封信",
        "body": ["第一段写一个人走进屋子，看见桌上有一盏灯，灯下压着一封没有署名的信。"],
        "quote": {"text": "灯下压着一封没有署名的信。", "line": 1},
        "why": "测试用。",
        "research": {"chapters": "无", "web": 0, "queries": []},
    }
    payload.update(changes)
    return payload


def _render(library, root, issue=None, today=dt.date(2026, 11, 6)):
    issues = [] if issue is None else [issue]
    data = overview.build_overview(
        library,
        root,
        now=today,
        feed={"issues": issues, "errors": []},
    )
    return data, overview.render_page(TEMPLATE, data, URLS)


def test_overview_uses_library_and_daily(reading_root):
    library = Library(str(reading_root))
    data, page = _render(library, reading_root, _issue())

    assert data["readable_books"] == 2
    assert data["chapters"] == 3
    assert data["chars"] > 0
    assert data["issue_count"] == 1
    assert data["today"] == "2026.11.06"
    assert data["weekday"] == "星期五"

    assert "{{" not in page
    assert "2 本可读" in page
    assert "本在架" not in page
    assert "1 期" in page
    assert "灯下的一封信" in page
    assert "第 001 期" in page
    assert "《甲书》 · 第1节" in page
    assert "「灯下压着一封没有署名的信。」" in page
    assert "读这一期" in page
    assert "2026.11.06" in page
    assert "星期五" in page
    assert 'href="http://127.0.0.1:9999/search"' in page
    assert 'href="http://127.0.0.1:9998/read"' in page
    assert 'href="http://127.0.0.1:9997/daily"' in page
    assert 'href="http://127.0.0.1:9996/ops"' in page
    assert "管理" in page


def test_render_escapes_daily_text(reading_root):
    library = Library(str(reading_root))
    issue = _issue(
        headline="<b>标题</b>",
        book={"uid": "B0001", "title": "甲<书>", "author": "某人"},
        chapter={"index": 1, "title": "第<1>节", "path": "books/B0001/md/001_第1节.md"},
        quote={"text": "<script>alert(1)</script>", "line": 1},
        body=["<em>摘要</em>"],
    )
    _, page = _render(library, reading_root, issue)

    assert "<b>标题</b>" not in page
    assert "&lt;b&gt;标题&lt;/b&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "《甲&lt;书&gt;》" in page
    assert "第&lt;1&gt;节" in page
    assert "&lt;em&gt;摘要&lt;/em&gt;" in page


def test_overview_without_daily_shows_empty_card(reading_root):
    library = Library(str(reading_root))
    data, page = _render(library, reading_root, issue=None)

    assert data["issue_count"] == 0
    assert "今天还没有日课" in page
    assert "尚无" in page
    assert "去日课页看看" in page
    assert "日课生成后，这里会更新。" in page
    assert "第 001 期" not in page
    assert "0 期" in page


def test_older_issue_is_marked_recent(reading_root):
    library = Library(str(reading_root))
    data, page = _render(
        library,
        reading_root,
        _issue(date="2026-11-05"),
        today=dt.date(2026, 11, 6),
    )

    assert data["issue_count"] == 1
    assert "最近一期" in page
    assert "从最近这一期开始" in page
    assert "今日日课" not in page


def test_unknown_placeholder_is_an_error(reading_root):
    library = Library(str(reading_root))
    data, _ = _render(library, reading_root, _issue())

    with pytest.raises(ValueError, match="NOPE"):
        overview.render_page("<p>{{NOPE}}</p>", data, URLS)
