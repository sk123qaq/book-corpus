import sqlite3
import sys

from bookcorpus.scripts import book_query, lib_common


def _point_tables_at(monkeypatch, tmp_path):
    """book_query 读 sys.path 上的顶层 lib_common，与包内 lib_common 是两个模块实例。"""
    tables = tmp_path / "tables"
    tables.mkdir()
    for mod in (lib_common, sys.modules["lib_common"]):
        monkeypatch.setattr(mod, "TABLES", str(tables))
    return tables


def test_read_table_splits_aliases_and_drops_wu_exclude(monkeypatch, tmp_path):
    tables = _point_tables_at(monkeypatch, tmp_path)
    (tables / "概念表.tsv").write_text("key\taliases\texclude\tnote\n平等\t公平,均等\t无\t\n", encoding="utf-8")

    assert book_query.read_table("main") == {"平等": {"aliases": ["公平", "均等"], "exclude": []}}


def test_load_actions_reads_action_word_table(monkeypatch, tmp_path):
    tables = _point_tables_at(monkeypatch, tmp_path)
    (tables / "概念表-动作词.tsv").write_text("term\tkind\tnote\n收拾\t动作\t\n", encoding="utf-8")

    assert book_query.load_actions() == {"收拾"}


def test_planner_defaults_skip_concepts_broad_and_actions(monkeypatch, tmp_path):
    tables = _point_tables_at(monkeypatch, tmp_path)
    (tables / "概念表.tsv").write_text("key\taliases\texclude\tnote\n平等\t公平,均等\t无\t\n", encoding="utf-8")
    (tables / "概念表-broad.tsv").write_text("key\taliases\texclude\tnote\n平等\t社会\t无\t\n", encoding="utf-8")
    (tables / "概念表-动作词.tsv").write_text("term\tkind\tnote\n收拾\t动作\t\n", encoding="utf-8")

    con = sqlite3.connect(":memory:")
    planner = book_query.Planner(
        con,
        meta={},
        expand={},
        search_fn=lambda terms: ([], 0.1),
        df_fn=lambda term: (1, 1),
    )
    assert planner.actions == set()
    assert planner.concepts == {}
    assert planner.broad == {}

    plan = planner.plan(["平等", "收拾"], broad=True, chapters=False)
    assert plan.action_dropped == []
    assert [r.kind for r in plan.rounds] == ["noun", "drop-generic"]
    assert all(r.kind not in ("noun+concept", "full+broad") for r in plan.rounds)


def test_book_cards_default_skip_concepts(monkeypatch, tmp_path):
    from bookcorpus.scripts import book_cards, vec_search
    from bookcorpus.scripts import lib_fts_cjk as cjk

    tables = _point_tables_at(monkeypatch, tmp_path)
    (tables / "概念表.tsv").write_text("key\taliases\texclude\tnote\n平等\t公平,均等\t无\t\n", encoding="utf-8")
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
    text = "关于平等与社会契约的论述在这里，至少需要四十个汉字才能通过长度过滤。"
    con.execute("INSERT INTO para VALUES (1, 'B1', 'books/B1/md/01.md', 1, ?, ?)", (text, len(text)))
    con.execute("INSERT INTO para_fts(rowid, text) VALUES (1, ?)", (cjk.index_text(text),))
    con.commit()

    meta = {"B1": {"title": "甲书", "author": "作者", "type": "社科", "chapters": 5}}
    books = {"B1": {"title": "甲书", "author": "作者"}}
    cardset = book_cards.run("平等", terms=["平等"], con=con, meta=meta, expand={}, books=books)
    con.close()
    assert cardset.hints["concept"] is False
