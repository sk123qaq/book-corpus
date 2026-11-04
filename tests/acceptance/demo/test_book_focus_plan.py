#!/usr/bin/env python3
"""显式书名的后端验收：解析、plan 钉位与默认结果。

  BOOKS_ROOT=~/文档/book-corpus-data python -m pytest \
      tests/acceptance/demo/test_book_focus_plan.py -q

离线跑，不调模型。真实数据只在 library.db 与 index.db 都在时用作集成用例，
缺库时跳过；其余用合成簿与手写 plan，断言解析规则与 _pick_default 的优先级。
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
INSTANCE = os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/文档/book-corpus-data")
os.environ.setdefault("BOOKS_ROOT", INSTANCE)
for _p in (
    os.path.join(REPO, "src", "bookcorpus", "scripts"),
    os.path.join(REPO, "src"),
    os.path.join(REPO, "apps", "demo"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trace

import book_cards
import book_query
import book_score
import book_title
import pytest

LIB_DB = os.path.join(INSTANCE, "build", "library.db")
INDEX_DB = os.path.join(INSTANCE, "build", "检索", "index.db")
QUESTION = "《万历十五年》里万历为什么对张居正的态度转变非常大"
FOCUS_TERMS = ["万历", "张居正", "万历十五年", "夺情", "抄家", "首辅"]
FOCUS_BOOK = "B0019"

SYNTH_BOOKS = {
    "X1": {"title": "山海经", "author": "甲"},
    "X2": {"title": "山海经", "author": "乙"},
    "Y1": {"title": "归途", "author": "丙"},
    "Y2": {"title": "归途", "author": "丙"},
    "Z1": {"title": "夜航", "author": ""},
    "Z2": {"title": "夜航", "author": "丁"},
    "W1": {"title": "万历十五年", "author": "黄仁宇"},
}
SYNTH_META = {
    "Y1": book_score.Meta(chapters=5),
    "Y2": book_score.Meta(chapters=12),
}


def _real():
    if not (os.path.exists(LIB_DB) and os.path.exists(INDEX_DB)):
        pytest.skip("缺少 library.db 或 index.db，跳过真实数据用例")
    return book_cards.load_books(), book_score.load_meta()


def test_exact_quoted_title_picks_book():
    books, meta = _real()
    if FOCUS_BOOK not in books:
        pytest.skip("当前语料没有 B0019")
    focus = book_title.resolve_title(QUESTION, books, meta)
    assert focus["status"] == "exact"
    assert focus["book"] == FOCUS_BOOK
    assert focus["title"] == "万历十五年"
    assert focus["chapters"] > 0
    assert focus["matched_by"] == "quoted"
    assert focus["candidates"] == []


def test_quoted_missing_title_stays_missing():
    books, meta = _real()
    focus = book_title.resolve_title("《不存在的书名》讲了什么", books, meta)
    assert focus["status"] == "missing"
    assert focus["query_title"] == "不存在的书名"
    assert focus["book"] is None
    assert focus["candidates"] == []


def test_quoted_missing_does_not_fall_back_to_known_title():
    focus = book_title.resolve_title("《不存在的书》里万历十五年讲了什么", SYNTH_BOOKS)
    assert focus["status"] == "missing"
    assert focus["query_title"] == "不存在的书"


def test_same_title_different_authors_is_ambiguous():
    focus = book_title.resolve_title("山海经讲了什么", SYNTH_BOOKS)
    assert focus["status"] == "ambiguous"
    assert focus["book"] is None
    assert {c["book"] for c in focus["candidates"]} == {"X1", "X2"}


def test_same_title_one_author_empty_is_ambiguous():
    focus = book_title.resolve_title("夜航讲了什么", SYNTH_BOOKS)
    assert focus["status"] == "ambiguous"
    assert {c["book"] for c in focus["candidates"]} == {"Z1", "Z2"}


def test_same_title_same_author_versions_pick_most_chapters():
    focus = book_title.resolve_title("归途", SYNTH_BOOKS, SYNTH_META)
    assert focus["status"] == "exact"
    assert focus["book"] == "Y2"
    assert focus["chapters"] == 12


def test_no_title_is_missing():
    focus = book_title.resolve_title("为什么人们会相信谣言", SYNTH_BOOKS)
    assert focus["status"] == "missing"
    assert focus["book"] is None
    assert focus["candidates"] == []


def _focus_plan():
    return {
        "ranking": [
            {"book": "B1114", "title": "别的书", "best_path": "books/B1114/md/01.md", "best_line": 3},
            {
                "book": FOCUS_BOOK,
                "title": "万历十五年",
                "best_path": "books/B0019/md/06_第一章万历皇帝.md",
                "best_line": 7,
            },
        ],
        "chapters": [
            {
                "book": FOCUS_BOOK,
                "title": "万历十五年",
                "book_score": 0.0,
                "pinned": True,
                "selected": [
                    {
                        "chapter_path": "books/B0019/md/06_第一章万历皇帝.md",
                        "chapter_title": "第一章 万历皇帝",
                        "line": 7,
                        "score": 1.0,
                    }
                ],
            },
            {
                "book": "B1114",
                "title": "别的书",
                "book_score": 2.0,
                "selected": [
                    {"chapter_path": "books/B1114/md/01.md", "chapter_title": "第一章", "line": 3, "score": 2.0}
                ],
            },
        ],
    }


def test_pick_default_exact_focus_beats_ranking_top1():
    focus = {"status": "exact", "book": FOCUS_BOOK, "title": "万历十五年"}
    pick = trace._pick_default(_focus_plan(), {"cards": [], "next": []}, 0, focus)
    assert pick["book"] == FOCUS_BOOK
    assert pick["pinned"] is True
    assert pick["path"] == "books/B0019/md/06_第一章万历皇帝.md"
    assert pick["line"] == 7


def test_pick_default_focus_without_path_is_empty_not_top1():
    plan = {
        "ranking": [{"book": "B1114", "title": "别的书", "best_path": "books/B1114/md/01.md", "best_line": 3}],
        "chapters": [],
    }
    focus = {"status": "exact", "book": FOCUS_BOOK, "title": "万历十五年"}
    pick = trace._pick_default(plan, {"cards": [], "next": []}, 0, focus)
    assert pick["book"] == FOCUS_BOOK
    assert pick["path"] == ""
    assert pick["focus_empty"] is True


def test_planner_pins_focus_book_and_body_chapter():
    books, meta = _real()
    if FOCUS_BOOK not in books:
        pytest.skip("当前语料没有 B0019")
    focus = book_title.resolve_title(QUESTION, books, meta)
    assert focus["status"] == "exact"
    con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    try:
        planner = book_query.Planner(con, meta, limit=8)
        plan = planner.plan(FOCUS_TERMS, focus=focus, max_rounds=2)
    finally:
        con.close()
    assert plan.ranking[0].book == FOCUS_BOOK
    assert plan.ranking[0].pinned is True
    assert plan.chapters[0]["book"] == FOCUS_BOOK
    assert plan.chapters[0]["pinned"] is True
    selected = plan.chapters[0]["selected"]
    assert selected
    names = [os.path.basename(r["chapter_path"]) for r in selected]
    assert any(not n.startswith(("23_", "22_", "05_")) for n in names)
    assert focus["best_path"] == selected[0]["chapter_path"]
