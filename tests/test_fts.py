import sqlite3

from bookcorpus.scripts import lib_fts_cjk as cjk
from bookcorpus.scripts import qsearch


def test_tokens_match_query_sides():
    assert cjk.tokens("自由") == ["自由"]
    assert cjk.tokens("自由自在") == ["自由", "由自", "自在"]
    assert cjk.tokens("1984 自由") == ["1984", "自由"]
    assert cjk.tokens("１９８４ ＡＢＣ") == ["1984", "abc"]
    assert cjk.index_text("ＡＢＣ １９８４ 自由") == "abc 1984 自由"


def test_query_expr_shape():
    assert cjk.query_expr("自由") == '"自由"'
    assert cjk.query_expr("礼") == "礼*"
    assert cjk.query_expr("现代的铁笼") == '"现代 代的 的铁 铁笼"'
    assert cjk.query_expr("COVID 19") == '"covid" AND "19"'
    assert cjk.query_expr("ＡＢＣ") == '"abc"'
    assert cjk.query_expr("１９８４") == '"1984"'
    assert cjk.query_expr("") is None


def test_split_groups_normalizes_fullwidth():
    assert cjk.split_groups("自由，平等、博爱") == ["自由", "平等", "博爱"]
    assert cjk.split_groups("１９８４ ａｂｃ") == ["1984", "abc"]


def test_fts_fullwidth_and_halfwidth_round_trip():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE para ("
        "id INTEGER PRIMARY KEY, path TEXT, book TEXT, line INTEGER, chars INTEGER, tok TEXT, text TEXT)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE para_fts USING fts5("
        "tok, content='para', content_rowid='id', tokenize='unicode61 remove_diacritics 0')"
    )
    text = "１９８４年的ＡＢＣ计划与社会结构讨论，这里是一段足够长的测试正文。"
    con.execute(
        "INSERT INTO para VALUES (1, 'books/B0001/md/01.md', 'B0001', 1, ?, ?, ?)",
        (len(text), cjk.index_text(text), text),
    )
    con.execute("INSERT INTO para_fts(para_fts) VALUES('rebuild')")

    for q in ("1984", "１９８４", "abc", "ＡＢＣ"):
        groups = cjk.split_groups(q)
        expr = cjk.query_expr(" ".join(groups))
        hits, _ms = qsearch.search(con, expr, groups, [], limit=5, min_chars=10)
        assert len(hits) == 1, q
        assert hits[0]["coverage"] == 1, (q, hits[0])
    con.close()
