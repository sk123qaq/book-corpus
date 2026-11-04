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
