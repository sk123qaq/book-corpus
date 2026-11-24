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


def test_book_cards_gate_cli_prints_rows_and_writes_tsv(monkeypatch, tmp_path, capsys):
    from bookcorpus.scripts import book_cards

    monkeypatch.setattr(book_cards.vec_search, "search_text", lambda *a, **k: ([], {"used": False}))

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
        "关于礼治秩序与乡土社会的讨论在这里展开，传统教化维系着熟人社会的行为规范，"
        "这段正文超过四十个汉字以便通过最小长度过滤。"
    )
    con.execute("INSERT INTO para VALUES (1, 'B0001', 'books/B0001/md/01.md', 1, ?, ?)", (text, len(text)))
    con.execute("INSERT INTO para_fts(rowid, text) VALUES (1, ?)", (book_cards.cjk.index_text(text),))
    con.commit()
    con.close()

    gold_path = tmp_path / "gold.tsv"
    gold_path.write_text(
        "qid\tstratum\tquestion\tterms\ttarget_book\ttarget_chapters\n"
        "Q01\t概念\t为什么探讨礼治秩序\t礼治,秩序\tB0001\tbooks/B0001/md/01.md\n",
        encoding="utf-8",
    )
    gate_out = tmp_path / "gate.tsv"

    monkeypatch.setattr(book_cards, "DB", str(db_path))
    orig_gold_rows = book_cards.gold_rows
    monkeypatch.setattr(book_cards, "gold_rows", lambda *_a, **_k: orig_gold_rows(gold_path))
    monkeypatch.setattr(
        book_cards,
        "load_books",
        lambda *_a, **_k: {"B0001": {"title": "乡土中国", "author": "费孝通"}},
    )
    monkeypatch.setattr(
        book_cards.book_score,
        "load_meta",
        lambda *_a, **_k: {
            "B0001": book_cards.book_score.Meta(title="乡土中国", subject1="社科", subject2="社会学", chapters=5)
        },
    )
    monkeypatch.setattr(book_cards.book_score, "load_expand", lambda *_a, **_k: {})
    monkeypatch.setattr(book_cards.book_readme, "readme_toc", lambda _b: ({}, "ok"))

    rc = book_cards.main(["--gate", "--gate-out", str(gate_out)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Q01" in out
    assert "card=1" in out
    assert gate_out.exists()
    assert "card_top6" in gate_out.read_text(encoding="utf-8")


def test_book_query_main_limit_card_count_and_max_chapters(monkeypatch, tmp_path, capsys):
    import json

    import pytest

    from bookcorpus.scripts import book_cards

    db_path = tmp_path / "index.db"
    sqlite3.connect(str(db_path)).close()
    monkeypatch.setattr(book_query.book_score, "DB", str(db_path))

    seen = {}

    class FakePlanner:
        def __init__(self, con, scheme="best_support", limit=10):
            seen["planner_limit"] = limit
            self.con = con
            self.scheme = scheme
            self.limit = limit

        def plan(self, terms, max_rounds=5, chapter_selector="score", max_chapters=3):
            seen["plan_max_chapters"] = max_chapters
            return book_query.Plan(
                query=" ".join(terms),
                terms=list(terms),
                nouns=list(terms),
                action_dropped=[],
                chapters=[{"book": "B0001", "book_score": 10.0, "selected": []}],
            )

        def search(self, terms):
            return [{"book": "B0002", "path": "books/B0002/md/01.md", "line": 1, "text": "x", "score": 5.0}], 0.0

    def fake_cards_for(planner, terms, question, n):
        seen["cards_for_n"] = n
        cards = [
            book_cards.BookCard(
                book=f"B{i:04d}",
                rank=i,
                title=f"书{i}",
                type="社科",
                chapters=1,
                best_chapter=f"books/B{i:04d}/md/01.md",
                best_chapter_title="第一章",
                snippet="片段",
                matched_groups=list(terms),
                score=1.0,
                reason="命中",
                readme_status="ok",
            )
            for i in range(1, n + 1)
        ]
        cardset = book_cards.CardSet(
            query=question,
            terms=list(terms),
            trigger=True,
            reasons=["margin"],
            hints={},
            cards=cards,
        )
        return cardset, [], list(terms), []

    def fake_next_books(missing, hits, terms, max_chapters):
        seen["next_books_max_chapters"] = max_chapters
        return [{"book": c.book, "book_score": 1.0, "selected": []} for c in missing]

    monkeypatch.setattr(book_query, "Planner", FakePlanner)
    monkeypatch.setattr(book_cards, "cards_for", fake_cards_for)
    monkeypatch.setattr(book_cards, "next_books", fake_next_books)

    # 1. 默认 -n/--limit 与 book_cards.DEFAULT_CARDS 对齐
    assert book_query.main(["礼治", "秩序", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["planner_limit"] == book_cards.DEFAULT_CARDS
    assert seen["cards_for_n"] == book_cards.DEFAULT_CARDS
    assert seen["next_books_max_chapters"] == 3
    assert len(payload["cards"]["cards"]) == book_cards.DEFAULT_CARDS
    assert len(payload["chapters"]) == book_cards.DEFAULT_CARDS

    # 2. -n 3 主控弹卡与返回书数；--max-chapters 联动 next_books
    seen.clear()
    assert book_query.main(["礼治", "秩序", "-n", "3", "--max-chapters", "5", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["planner_limit"] == 3
    assert seen["cards_for_n"] == 3
    assert seen["plan_max_chapters"] == 5
    assert seen["next_books_max_chapters"] == 5
    assert len(payload["cards"]["cards"]) == 3

    # 3. -n 10 不再被硬编码截断为 6
    seen.clear()
    assert book_query.main(["礼治", "秩序", "-n", "10", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["planner_limit"] == 10
    assert seen["cards_for_n"] == 10
    assert len(payload["cards"]["cards"]) == 10

    # 4. --card-count 可独立控制弹卡张数
    seen.clear()
    assert book_query.main(["礼治", "秩序", "-n", "10", "--card-count", "4", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["planner_limit"] == 10
    assert seen["cards_for_n"] == 4
    assert len(payload["cards"]["cards"]) == 4

    # 5. 非法非正整数参数校验
    for bad_args in (["-n", "0"], ["--card-count", "-2"], ["--max-chapters", "0"]):
        with pytest.raises(SystemExit) as exc:
            book_query.main(["礼治", *bad_args])
        assert exc.value.code != 0
