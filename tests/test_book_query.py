import sqlite3

from bookcorpus.scripts import book_query, book_score


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


def test_planner_scope_filters_hits_before_ranking():
    hits = []
    for i in range(1, 13):
        book = f"B{i:04d}"
        for line in range(1, 2 if i == 12 else 11):
            hits.append(
                {
                    "book": book,
                    "path": f"books/{book}/md/01.md",
                    "line": line,
                    "text": "铁笼 礼治 秩序",
                    "score": 1.0 if i < 12 else 0.1,
                }
            )
    meta = {
        f"B{i:04d}": book_score.Meta(title=f"书{i}", subject1="社科", subject2="", chapters=1) for i in range(1, 13)
    }

    def planner(scope=None):
        return book_query.Planner(
            None,
            meta=meta,
            expand={},
            scope=scope,
            limit=8,
            search_fn=lambda terms: (hits, 0.1),
            df_fn=lambda term: (1, 1),
        )

    unscoped = planner().plan(["铁笼"], chapters=True)
    assert "B0012" not in [row.book for row in unscoped.ranking[:8]]

    scoped = planner(scope={"B0012"}).plan(["铁笼"], chapters=True)
    assert [row.book for row in scoped.ranking] == ["B0012"]
    assert [entry["book"] for entry in scoped.chapters] == ["B0012"]
    assert scoped.chapters[0]["selected"]


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


def test_planner_excludes_source_book_spot_and_duplicate_passage():
    source_para = "礼治社会依靠长老教化与传统习惯维持秩序，不需要外在的法律强力压迫，就像一套自然习得的规矩。"
    commentary_para = (
        "费孝通曾指出："
        + source_para[:22]
        + "，但在现代都市陌生人社会中，契约与法治逐渐取代了熟人间的默契，两种秩序逻辑有着本质区别。"
    )
    hits = [
        {"book": "B0001", "path": "books/B0001/md/01.md", "line": 12, "text": source_para, "score": 12.0},
        {"book": "B0002", "path": "books/B0002/md/01.md", "line": 5, "text": source_para, "score": 11.0},
        {"book": "B0003", "path": "books/B0003/md/02.md", "line": 20, "text": commentary_para, "score": 8.0},
    ]
    meta = {
        "B0001": book_score.Meta(title="甲书", subject1="社科", subject2="", chapters=1),
        "B0002": book_score.Meta(title="甲书复本", subject1="社科", subject2="", chapters=1),
        "B0003": book_score.Meta(title="乙书", subject1="社科", subject2="", chapters=1),
    }

    p = book_query.Planner(
        None,
        meta=meta,
        expand={},
        exclude_books=["B0001"],
        exclude_spots=[("books/B0003/md/02.md", 99)],
        exclude_text=source_para,
        search_fn=lambda terms: (hits, 0.1),
        df_fn=lambda term: (1, 1),
    )
    plan = p.plan(["礼治", "秩序"], chapters=True)
    # B0001 excluded by book UID; B0002 suppressed as verbatim duplicate; B0003 commentary kept
    assert [row.book for row in plan.ranking] == ["B0003"]
    assert plan.chapters[0]["book"] == "B0003"
