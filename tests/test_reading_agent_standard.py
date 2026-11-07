from bookcorpus.reading.agent_standard import (
    _inject_images,
    _norm,
    render_agent_page,
    validate_fragment,
)


def _blocks():
    return [
        {"id": "B01", "kind": "paragraph", "text": "第一段原文。"},
        {"id": "B02", "kind": "paragraph", "text": "第二段原文。"},
        {"id": "B03", "kind": "image", "text": "", "rel": "images/a.png"},
        {"id": "B04", "kind": "quote", "text": "引文原文。"},
        {"id": "B05", "kind": "heading", "text": "小标题"},
    ]


def _valid():
    return (
        '<section class="beat"><p class="beat-no">01 / 01</p><h3>题</h3>'
        '<p class="beat-q">问？</p></section>'
        '<div class="flow-row"><div class="block">'
        '<p class="src" data-block="B01">第一段原文。</p>'
        '<p class="src" data-block="B02">第二段原文。</p>'
        '<figure class="fig" data-block="B03"><figcaption>一张图。</figcaption></figure>'
        '<blockquote class="src" data-block="B04">引文原文。</blockquote>'
        '<h2 class="sec" data-block="B05">小标题</h2>'
        '</div><div class="margin"></div></div>'
    )


def test_valid_fragment_passes():
    assert validate_fragment(_valid(), _blocks()) == []


def test_changed_source_is_rejected():
    bad = _valid().replace("第二段原文。", "第二段被改了。")
    problems = validate_fragment(bad, _blocks())
    assert any("B02" in item and "改写" in item for item in problems)


def test_duplicate_and_order_are_rejected():
    bad = _valid().replace(
        '<blockquote class="src" data-block="B04">引文原文。</blockquote>',
        '<blockquote class="src" data-block="B04">引文原文。</blockquote>'
        '<p class="src" data-block="B01">第一段原文。</p>',
    )
    problems = validate_fragment(bad, _blocks())
    assert any("重复" in item for item in problems)


def test_unmarked_body_prose_is_rejected():
    bad = _valid().replace(
        '<p class="src" data-block="B02">第二段原文。</p>',
        '<p class="src" data-block="B02">第二段原文。</p><p>模型自己的总结。</p>',
    )
    problems = validate_fragment(bad, _blocks())
    assert any("未标记来源" in item for item in problems)


def test_forbidden_script_and_external_url():
    bad = _valid() + '<script>alert(1)</script><a href="https://x.test">x</a>'
    problems = validate_fragment(bad, _blocks())
    assert any("script" in item for item in problems)
    assert any("外部地址" in item for item in problems)


def test_image_is_injected_after_validation():
    fragment = _valid()
    assert validate_fragment(fragment, _blocks()) == []
    out = _inject_images(fragment, _blocks(), {"B03": "data:image/png;base64,AA=="})
    assert "<img" in out
    assert "data:image/png;base64,AA==" in out
    assert "一张图。" in out


def test_render_page_has_title_and_stats():
    page = render_agent_page(
        {"title_short": "书", "title": "书"},
        {"title": "章"},
        _valid(),
        stats={"chars": 1200, "plates": 1, "minutes": 3},
    )
    assert "章 · Agent 标准档" in page
    assert "1,200 字" in page
    assert "图版 1" in page
    assert _norm(" 第一 段 ") == "第一段"
