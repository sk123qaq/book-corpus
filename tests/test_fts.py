from bookcorpus.scripts import lib_fts_cjk as cjk


def test_tokens_match_query_sides():
    assert cjk.tokens("自由") == ["自由"]
    assert cjk.tokens("自由自在") == ["自由", "由自", "自在"]
    assert cjk.tokens("1984 自由") == ["1984", "自由"]


def test_query_expr_shape():
    assert cjk.query_expr("自由") == '"自由"'
    assert cjk.query_expr("礼") == "礼*"
    assert cjk.query_expr("现代的铁笼") == '"现代 代的 的铁 铁笼"'
    assert cjk.query_expr("COVID 19") == '"covid" AND "19"'
    assert cjk.query_expr("") is None


def test_split_groups_normalizes_fullwidth():
    assert cjk.split_groups("自由，平等、博爱") == ["自由", "平等", "博爱"]
    assert cjk.split_groups("１９８４ ａｂｃ") == ["1984", "abc"]
