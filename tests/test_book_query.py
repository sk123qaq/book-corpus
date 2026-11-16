import sqlite3

from bookcorpus.scripts import book_query


def test_planner_runs_full_and_drop_generic_without_concept_tables():
    con = sqlite3.connect(":memory:")
    planner = book_query.Planner(
        con,
        meta={},
        expand={},
        search_fn=lambda terms: ([], 0.1),
        df_fn=lambda term: (1, 1),
    )
    plan = planner.plan(["平等", "社会"], chapters=False)
    assert plan.action_dropped == []
    assert [r.kind for r in plan.rounds] == ["full", "drop-generic"]


def test_book_cards_runs_cleanly_without_concept_tables(monkeypatch, tmp_path):
    from bookcorpus.scripts import book_cards, book_score, vec_search
    from bookcorpus.scripts import lib_fts_cjk as cjk

    monkeypatch.setattr(vec_search, "search_text", lambda *a, **k: ([], {"used": False}))

    db_path = tmp_path / "index.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE para (id INTEGER PRIMARY KEY, book TEXT, path TEXT, line INTEGER, text TEXT, chars INTEGER)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE para_fts USING fts5(text, content='para', content_rowid='id', tokenize='unicode61')"
    )
    con.execute("CREATE TABLE book_expand (book TEXT, path TEXT, line INTEGER, terms TEXT)")
    text = (
        "关于平等与社会契约的论述在这里，社会公平需要制度保障与公共讨论，"
        "这段文字必须超过四十个汉字才能通过段落最小长度过滤。"
    )
    con.execute("INSERT INTO para VALUES (1, 'B1', 'books/B1/md/01.md', 1, ?, ?)", (text, len(text)))
    con.execute("INSERT INTO para_fts(rowid, text) VALUES (1, ?)", (cjk.index_text(text),))
    con.commit()

    meta = {"B1": book_score.Meta(title="甲书", subject1="社科", subject2="政治", chapters=5)}
    books = {"B1": {"title": "甲书", "author": "作者"}}
    cardset = book_cards.run(
        "为什么探讨平等",
        terms=["平等"],
        con=con,
        meta=meta,
        expand={},
        books=books,
        toc_fn=lambda _b: ({}, "ok"),
    )
    con.close()
    assert cardset.hints["concept"] is False
    assert cardset.hints["question"] is True
    assert cardset.trigger is True
    assert "concept" in cardset.reasons
