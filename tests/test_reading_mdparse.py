"""Markdown 源文解析的边界：front matter、标题、空行、图片与脚注。"""

from bookcorpus.reading.mdparse import chapter_title, han_count, non_space_count, parse_markdown


def test_front_matter_h1_and_block_kinds():
    raw = (
        "---\n"
        "title: 章一\n"
        "author: 某人\n"
        "---\n"
        "# 第一章 标题\n\n"
        "第一段第一行\n"
        "第一段第二行\n\n"
        "![](images/note.png)\n\n"
        "第二段正文。\n\n"
        "> 引文一\n"
        "> 引文二\n\n"
        "- 条目一\n"
        "- 条目二\n"
    )
    parsed = parse_markdown(raw, "01_第一章")
    assert parsed["title"] == "章一"
    assert parsed["h1"] == "第一章 标题"
    assert [block["kind"] for block in parsed["blocks"]] == [
        "paragraph",
        "footnote",
        "paragraph",
        "quote",
        "list",
    ]
    assert parsed["blocks"][0]["text"] == "第一段第一行 第一段第二行"
    assert parsed["blocks"][1]["text"] == ""
    assert parsed["blocks"][1]["rel"] == "images/note.png"
    assert parsed["blocks"][3]["text"] == "引文一 引文二"
    assert parsed["blocks"][4]["text"] == "条目一 条目二"


def test_blank_line_stays_two_blocks_no_merge():
    raw = "# 标题\n\n第一段。\n\n第二段。\n"
    parsed = parse_markdown(raw)
    assert len(parsed["blocks"]) == 2
    assert [block["text"] for block in parsed["blocks"]] == ["第一段。", "第二段。"]
    assert [block["id"] for block in parsed["blocks"]] == ["B01", "B02"]


def test_inline_image_splits_paragraph_in_order():
    parsed = parse_markdown("# 标题\n\n前文![](images/x.png)后文\n")
    assert [block["kind"] for block in parsed["blocks"]] == ["paragraph", "image", "paragraph"]
    assert [block["text"] for block in parsed["blocks"]] == ["前文", "", "后文"]
    assert parsed["blocks"][1]["rel"] == "images/x.png"


def test_heading_level_and_emphasis_cleanup():
    parsed = parse_markdown("# 标题\n\n正文里的 **重音** 与 `代码`。\n\n## 小节\n")
    assert parsed["blocks"][0]["text"] == "正文里的 重音 与 代码。"
    assert parsed["blocks"][1]["kind"] == "heading"
    assert parsed["blocks"][1]["level"] == 2


def test_counts_and_title_fallback():
    raw = "# 无 front matter\n\n甲乙丙。\n"
    parsed = parse_markdown(raw, "07_备用名")
    assert parsed["title"] == "无 front matter"
    assert parsed["body_han"] == 3
    assert parsed["body_chars"] == 4
    assert han_count("甲a乙") == 2
    assert non_space_count("甲 a\n乙") == 3
    assert chapter_title(raw, "备用") == "无 front matter"
