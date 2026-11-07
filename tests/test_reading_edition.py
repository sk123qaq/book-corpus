import pytest

from bookcorpus.reading.edition.brief import Brief, decide_template
from bookcorpus.reading.edition.content import ContentError, number_warnings, validate_content
from bookcorpus.reading.edition.gate import audit_edition
from bookcorpus.reading.edition.render import render_edition
from bookcorpus.reading.mdparse import parse_markdown


def _blocks():
    return [
        {"id": "B01", "kind": "heading", "level": 2, "text": "一、起点", "chars": 4},
        {"id": "B02", "kind": "paragraph", "text": "第一段原文，数字 42。", "chars": 10},
        {"id": "B03", "kind": "image", "rel": "images/x.png", "text": "", "chars": 0},
        {"id": "B04", "kind": "paragraph", "text": "第二段原文。", "chars": 6},
        {"id": "B05", "kind": "footnote", "text": "注释原文", "chars": 4},
    ]


def _content():
    return {
        "hero": {"kicker": "测试", "title_lines": ["一页测试"], "lead": "导语", "foot_left": "原文逐字"},
        "overview": [{"no": "01", "title": "概览", "text": "说明"}],
        "sections": [
            {"id": "s1", "anchor": "B01", "title": "一、起点", "intro": "小节导语"},
            {"id": "s2", "anchor": "B04", "title": "二、继续", "intro": ""},
        ],
        "figures": {"B03": {"title": "示意图", "caption": "图注", "note": "", "redraw": False}},
        "charts": [
            {
                "id": "c1",
                "anchor": "B02",
                "after": True,
                "kind": "bars",
                "title": "数字",
                "caption": "",
                "note": "",
                "data": {"labels": ["甲", "乙"], "values": [42, 7], "unit": "", "highlight": 0},
            }
        ],
        "callouts": [{"anchor": "B02", "after": True, "kicker": "注意", "title": "提示", "text": "正文"}],
        "takeaways": {
            "anchor": "B04",
            "title": "带走",
            "items": [{"title": "一", "text": "甲"}, {"title": "二", "text": "乙"}, {"title": "三", "text": "丙"}],
        },
    }


def test_content_accepts_minimal_and_rejects_bad_anchor():
    content = validate_content(_content(), _blocks())
    assert content["sections"][0]["anchor"] == "B01"
    bad = _content()
    bad["sections"][0]["anchor"] = "B99"
    with pytest.raises(ContentError):
        validate_content(bad, _blocks())


def test_content_rejects_figure_key_that_is_not_image():
    bad = _content()
    bad["figures"] = {"B02": {"title": "x", "caption": "y", "note": "", "redraw": False}}
    with pytest.raises(ContentError):
        validate_content(bad, _blocks())


def test_number_warnings_ignore_metadata_and_flag_new_numbers():
    content = validate_content(_content(), _blocks())
    content["model"] = "deepseek-v4.1-flash"
    content["source_sha256"] = "a1b2c3"
    assert number_warnings(content, _blocks()) == []
    content["hero"]["lead"] = "新数字 999"
    assert any("999" in warning for warning in number_warnings(content, _blocks()))


def test_render_and_gate_round_trip():
    content = validate_content(_content(), _blocks())
    html = render_edition(
        {"uid": "B0000", "title": "测试书", "author": "作者"},
        {"title": "测试章", "body_han": 20},
        _blocks(),
        content,
    )
    assert "第一段原文" in html
    assert 'class="plate"' in html
    audit = audit_edition(_blocks(), html, content)
    assert audit["errors"] == []
    assert audit["stats"]["charts"] == 1


def test_gate_catches_missing_source_text():
    content = validate_content(_content(), _blocks())
    html = render_edition(
        {"uid": "B0000", "title": "测试书", "author": "作者"},
        {"title": "测试章", "body_han": 20},
        _blocks(),
        content,
    )
    broken = html.replace("第二段原文。", "")
    audit = audit_edition(_blocks(), broken, content)
    assert any("B04" in error for error in audit["errors"])


@pytest.mark.parametrize(
    ("subject", "han", "ratio", "reason"),
    [
        ("文学", 5000, 0.0, "文学类"),
        ("人文社科", 1000, 0.0, "撑不起"),
        ("人文社科", 30001, 0.0, "超过单页读本上限"),
        ("艺术", 5000, 0.6, "图册模板"),
    ],
)
def test_template_fallback(subject, han, ratio, reason):
    brief = Brief("B0000", 1, "标题", subject, han, 20, 0, ratio, 1, False, False, False)
    decision = decide_template(brief)
    assert decision["template"] is None
    assert reason in decision["reason"]


def test_inline_image_is_stripped_from_heading():
    parsed = parse_markdown("# 书名\n\n## 缘起![](images/note.png)：副题\n\n正文。\n")
    heading = next(block for block in parsed["blocks"] if block["kind"] == "heading")
    assert "![" not in heading["text"]
    assert heading["text"] == "缘起：副题"


def test_orphan_footnotes_are_filled_from_trailing_list():
    parsed = parse_markdown(
        "# 书名\n\n正文一![](images/note.png)。正文二![](images/note.png)。\n\n- 注释一\n- 注释二\n"
    )
    footnotes = [block for block in parsed["blocks"] if block["kind"] == "footnote"]
    assert [block["text"] for block in footnotes] == ["注释一", "注释二"]
    assert not any(block["kind"] == "list" for block in parsed["blocks"])


def test_markdown_separators_quote_code_and_links_are_clean():
    parsed = parse_markdown(
        "# 标题\n\n---\n\n> `cd /tmp && python setup.py install`\n\n---\n\n见 [文档](https://example.com/docs)。\n"
    )
    kinds = [block["kind"] for block in parsed["blocks"]]
    assert "code" in kinds
    assert parsed["blocks"][0]["text"] == "cd /tmp && python setup.py install"
    assert parse_markdown("> `rm -rf *`\n")["blocks"][0]["text"] == "rm -rf *"
    assert parsed["blocks"][-1]["text"] == "见 文档。"
