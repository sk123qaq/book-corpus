"""mdparse 扩展：表格、代码围栏、分结。"""

import os

from bookcorpus.reading.mdparse import parse_markdown


def _kinds(parsed):
    return [block["kind"] for block in parsed["blocks"]]


def test_pipe_table_with_header():
    raw = "## 标题\n\n| 从0到1 | 从1到n |\n| --- | --- |\n| 创新 | 复制 |\n| 垂直 | 水平 |\n"
    parsed = parse_markdown(raw)
    assert _kinds(parsed) == ["heading", "table"]
    table = parsed["blocks"][1]
    assert table["head"] == ["从0到1", "从1到n"]
    assert table["rows"] == [["创新", "复制"], ["垂直", "水平"]]
    assert table["align"] == ["---", "---"]
    # text 扁平保留所有单元格文本，供原文覆盖门禁做子串检查
    assert table["text"] == "从0到1 从1到n 创新 复制 垂直 水平"


def test_pipe_table_without_header():
    raw = "| 甲 | 乙 |\n| 丙 | 丁 |\n"
    parsed = parse_markdown(raw)
    table = parsed["blocks"][0]
    assert table["kind"] == "table"
    assert "head" not in table
    assert table["rows"] == [["甲", "乙"], ["丙", "丁"]]


def test_code_fence_with_blank_lines():
    raw = "看代码\n\n```python\nx = 1\n\ny = x + 1\nreturn y\n```\n\n后文\n"
    parsed = parse_markdown(raw)
    assert _kinds(parsed) == ["paragraph", "code", "paragraph"]
    code = parsed["blocks"][1]
    assert code["lang"] == "python"
    assert code["text"] == "x = 1\n\ny = x + 1\nreturn y"
    assert "后文" in parsed["blocks"][2]["text"]


def test_code_fence_without_lang_keeps_order():
    raw = "前\n\n```\nid = 42\n```\n\n后\n"
    parsed = parse_markdown(raw)
    assert _kinds(parsed) == ["paragraph", "code", "paragraph"]
    assert "lang" not in parsed["blocks"][1]
    assert parsed["blocks"][1]["text"] == "id = 42"
    assert [block["id"] for block in parsed["blocks"]] == ["B01", "B02", "B03"]


def test_table_and_code_counts_in_chars():
    raw = "| 甲 | 乙 |\n| --- | --- |\n\n```python\nprint(1)\n```\n"
    parsed = parse_markdown(raw)
    assert parsed["body_han"] > 0
    assert len(parsed["blocks"]) == 2


def test_sections_no_h2_single():
    parsed = parse_markdown("第一段\n\n第二段\n")
    assert parsed["sections"] == [{"id": "S01", "title": "", "start": 0, "end": 2}]


def test_sections_split_on_h2():
    raw = "开头\n\n## 甲节\n\n甲一段\n\n甲二段\n\n## 乙节\n\n乙一段\n"
    parsed = parse_markdown(raw)
    sections = parsed["sections"]
    assert [s["title"] for s in sections] == ["", "甲节", "乙节"]
    assert [s["start"] for s in sections] == [0, 1, 4]
    assert [s["end"] for s in sections] == [1, 4, 6]
    assert [s["id"] for s in sections] == ["S01", "S02", "S03"]


def test_sections_h3_does_not_split():
    raw = "## 甲节\n\n甲一段\n\n### 子题\n\n子一段\n"
    parsed = parse_markdown(raw)
    assert len(parsed["sections"]) == 1
    assert parsed["sections"][0]["title"] == "甲节"


def test_table_not_confused_with_paragraph():
    raw = "这一行提到了 | 竖线 | 但不是表格。\n"
    parsed = parse_markdown(raw)
    assert parsed["blocks"][0]["kind"] == "paragraph"


def test_golden_ch04_block_ids_stable():
    """B0003/04 的编辑层锚点依赖现有块序；表格/代码不能改变无此内容的章。"""
    path = os.path.expanduser("~/书库/books/B0003/md/04_第2章机会成本和禀赋效应.md")
    if not os.path.exists(path):
        return  # 数据实例不在本机时跳过
    with open(path, encoding="utf-8") as fh:
        parsed = parse_markdown(fh.read())
    ids = {block["id"] for block in parsed["blocks"]}
    anchors = {"B03", "B08", "B18", "B24", "B29"}
    assert anchors <= ids, "四读黄金锚点丢了：%s" % (anchors - ids)
