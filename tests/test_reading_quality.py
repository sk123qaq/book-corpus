"""阅读版 HTML 质量检查：真实渲染、阈值回归、图注与图文门禁。"""

import struct

from bookcorpus.reading.mdparse import parse_markdown
from bookcorpus.reading.model import _payload_blocks
from bookcorpus.reading.quality import LIMITS, check_html
from bookcorpus.reading.render import render_chapter
from bookcorpus.reading.service import ReadingService, audit_pack
from bookcorpus.reading.tiers import project, validate_annotation

TIERS = ("skim", "standard", "deep")

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0d"
    + b"IHDR"
    + struct.pack(">II", 1, 1)
    + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
)


def _render(root, extra_blocks=None):
    service = ReadingService(str(root))
    raw = service.library.read_chapter("B0001", 1)
    blocks = [dict(block) for block in raw["blocks"]]
    blocks.extend(extra_blocks or [])
    draft = {tier: project(blocks, None, tier) for tier in TIERS}
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    html = render_chapter(book, chapter, blocks, draft, resolver=lambda rel: rel)
    return blocks, draft, html


def _levels(findings, key):
    return [finding["level"] for finding in findings if finding["key"] == key]


def _figure(source, caption, alt, size=' width="10" height="10"'):
    return (
        '<figure class="fig" data-caption-src="%s"><img src="assets/x.png" alt="%s"%s>'
        "<figcaption>%s</figcaption></figure>" % (source, alt, size, caption)
    )


def _inject(html, snippet):
    assert "</body>" in html
    return html.replace("</body>", snippet + "</body>")


def test_real_render_has_no_fail(reading_root):
    _, _, html = _render(reading_root)
    findings = check_html(html)
    fails = [finding for finding in findings if finding["level"] == "fail"]
    assert fails == []
    assert any(finding["key"] == "stats" for finding in findings)


def test_old_ink_3_fails_contrast(reading_root):
    _, _, html = _render(reading_root)
    assert "--ink-3:#69665C" in html
    old = html.replace("--ink-3:#69665C", "--ink-3:#8A877C", 1)
    assert "fail" in _levels(check_html(old), "contrast")
    assert "fail" not in _levels(check_html(html), "contrast")


def test_loose_line_height_fails(reading_root):
    _, _, html = _render(reading_root)
    loose = html.replace(".src{font-size:17.5px;line-height:1.9;", ".src{font-size:17.5px;line-height:2.02;", 1)
    assert loose != html
    assert "fail" in _levels(check_html(loose), "line-height")


def test_indent_plus_margin_fails_paragraph_marker(reading_root):
    _, _, html = _render(reading_root)
    doubled = html.replace("text-indent:2em;margin:0;", "text-indent:2em;margin:0 0 1.1em;", 1)
    assert doubled != html
    assert "fail" in _levels(check_html(doubled), "paragraph-marker")
    assert "fail" not in _levels(check_html(html), "paragraph-marker")


def test_model_caption_equal_to_alt_fails(reading_root):
    _, _, html = _render(reading_root)
    doc = _inject(html, _figure("model", "一张展示流程的示意图", "一张展示流程的示意图"))
    findings = check_html(doc)
    assert "fail" in _levels(findings, "figure-caption")


def test_alt_caption_fallback_warns(reading_root):
    _, _, html = _render(reading_root)
    doc = _inject(html, _figure("alt", "一张展示流程的示意图", "一张展示流程的示意图"))
    findings = check_html(doc)
    assert "warn" in _levels(findings, "figure-caption")
    assert "fail" not in _levels(findings, "figure-caption")


def test_image_without_size_fails(reading_root):
    _, _, html = _render(reading_root)
    doc = _inject(html, _figure("model", "流程图说明三个阶段", "一张展示流程的示意图", size=""))
    assert "fail" in _levels(check_html(doc), "image-size")


def test_filename_alt_fails(reading_root):
    _, _, html = _render(reading_root)
    doc = _inject(html, _figure("model", "流程图说明三个阶段", "figure-01.png"))
    assert "fail" in _levels(check_html(doc), "alt")


def test_media_query_font_size_does_not_leak():
    doc = (
        "<!doctype html><html><head><style>"
        ".src{font-size:17.5px}"
        "@media(max-width:720px){.src{font-size:10px}}"
        '</style></head><body><p class="src">短文</p></body></html>'
    )
    assert "fail" not in _levels(check_html(doc), "font-size")


def test_two_value_margin_fails_paragraph_marker(reading_root):
    _, _, html = _render(reading_root)
    doubled = html.replace("text-indent:2em;margin:0;", "text-indent:2em;margin:0 1.1em;", 1)
    assert doubled != html
    assert "fail" in _levels(check_html(doubled), "paragraph-marker")


def test_missing_caption_source_warns(reading_root):
    _, _, html = _render(reading_root)
    doc = _inject(
        html,
        '<figure class="fig"><img src="assets/x.png" alt="流程" width="10" height="10">'
        "<figcaption>流程图说明三个阶段</figcaption></figure>",
    )
    assert "warn" in _levels(check_html(doc), "figure-caption")


def test_mdparse_reads_png_size(tmp_path):
    image = tmp_path / "img" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_1X1)
    raw = "# 标题\n\n![示意图](img/a.png)\n\n正文。\n"
    parsed = parse_markdown(raw, "01_第一节", base_dir=str(tmp_path))
    block = next(block for block in parsed["blocks"] if block["kind"] == "image")
    assert block["width"] == 1
    assert block["height"] == 1


def test_mdparse_skips_unreadable_image(tmp_path):
    raw = "# 标题\n\n![示意图](img/missing.png)\n"
    parsed = parse_markdown(raw, base_dir=str(tmp_path))
    block = next(block for block in parsed["blocks"] if block["kind"] == "image")
    assert "width" not in block
    assert "height" not in block


def test_annotation_caption_reaches_projection():
    blocks = [{"id": "B01", "kind": "paragraph", "text": "某" * 30, "chars": 30}]
    annotation = {
        "blocks": [{"id": "B01", "role": "claim", "priority": 1, "spine": True, "caption": "  一句图注  "}],
        "supports": [],
        "cards": [],
    }
    checked = validate_annotation(annotation, blocks)
    assert checked["blocks"][0]["caption"] == "一句图注"
    assert project(blocks, checked, "standard")["captions"] == {"B01": "一句图注"}
    assert project(blocks, None, "standard")["captions"] == {}


def test_render_marks_model_caption(reading_root):
    service = ReadingService(str(reading_root))
    raw = service.library.read_chapter("B0001", 1)
    blocks = [dict(block) for block in raw["blocks"]]
    blocks.append(
        {
            "id": "B09",
            "kind": "image",
            "text": "一张展示流程的示意图",
            "rel": "assets/x.png",
            "width": 120,
            "height": 80,
            "chars": 0,
        }
    )
    caption = "流程图把三个阶段串成一条链"
    annotation = {
        "blocks": [
            {"id": "B01", "role": "claim", "priority": 1, "spine": True},
            {"id": "B09", "role": "evidence", "priority": 1, "spine": True, "caption": caption},
        ],
        "supports": [],
        "cards": [],
    }
    checked = validate_annotation(annotation, blocks)
    projection = project(blocks, checked, "deep")
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    html = render_chapter(book, chapter, blocks, {"deep": projection}, resolver=lambda rel: rel)
    assert 'data-caption-src="model"' in html
    assert '<span class="fig-cap">%s</span>' % caption in html
    assert 'width="120"' in html
    assert 'height="80"' in html


def test_audit_pack_has_layout_and_figure_gates(reading_root):
    blocks, draft, html = _render(reading_root)
    audit = audit_pack(blocks, draft, html, None, True)
    gates = {gate["id"]: gate for gate in audit["gates"]}
    assert "G5" in gates
    assert "G7" in gates
    assert gates["G5"]["status"] == "pass"
    assert gates["G7"]["status"] == "pass"
    assert gates["G5"]["detail"] == "对比度、字号、行长、行高、分段均在范围内"
    assert gates["G7"]["detail"] == "图注、alt、尺寸、图表密度均合规"


def test_audit_pack_marks_layout_fail(reading_root):
    blocks, draft, html = _render(reading_root)
    loose = html.replace(".src{font-size:17.5px;line-height:1.9;", ".src{font-size:17.5px;line-height:2.02;", 1)
    audit = audit_pack(blocks, draft, loose, None, True)
    gate = next(gate for gate in audit["gates"] if gate["id"] == "G5")
    assert gate["status"] == "fail"
    assert "行高" in gate["detail"]


def test_missing_style_fails():
    findings = check_html('<p class="src">正文</p>')
    assert "fail" in _levels(findings, "font-size")


def test_h4_resets_heading_gap():
    filler = '<p class="src">' + "字" * 900 + "</p>"
    doc = (
        "<!doctype html><html><body>"
        + filler
        + '<h4 class="sec">小节</h4>'
        + filler
        + '<h2 class="sec">结尾</h2>'
        + "</body></html>"
    )
    assert "warn" not in _levels(check_html(doc), "heading-gap")


def test_long_paragraph_is_not_a_finding():
    doc = '<p class="src">' + "字" * 900 + "</p>"
    assert "paragraph-length" not in {finding["key"] for finding in check_html(doc)}


def test_bare_image_is_checked():
    findings = check_html('<img src="a.png">')
    assert "warn" in _levels(findings, "figure-caption")
    assert "fail" in _levels(findings, "image-size")


def test_limits_are_pinned():
    assert LIMITS["body_px"] == (16.0, 22.0)
    assert LIMITS["caption_px"] == (12.0, 18.0)
    assert LIMITS["footnote_px"] == (11.0, 16.0)
    assert LIMITS["cjk_measure"] == (14.0, 42.0)
    assert LIMITS["caption_measure"] == (14.0, 40.0)
    assert LIMITS["line_height"] == (1.5, 2.0)
    assert LIMITS["contrast"] == 4.5
    assert LIMITS["chars_per_figure"] == 1200
    assert LIMITS["heading_gap_chars"] == 1600


def test_g7_fails_on_missing_size(reading_root):
    blocks, draft, html = _render(reading_root)
    doc = _inject(html, _figure("model", "流程图说明三个阶段", "一张展示流程的示意图", size=""))
    audit = audit_pack(blocks, draft, doc, None, True)
    gate = next(gate for gate in audit["gates"] if gate["id"] == "G7")
    assert gate["status"] == "fail"
    assert "width/height" in gate["detail"]


def test_payload_sends_images_with_figure_hint():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "正文"},
        {"id": "B02", "kind": "paragraph", "text": ""},
        {"id": "B03", "kind": "image", "text": "", "rel": "img/x.png"},
        {"id": "B04", "kind": "heading", "level": 3, "text": "图1 示意"},
    ]
    payload = _payload_blocks(blocks)
    assert [item["id"] for item in payload] == ["B01", "B03", "B04"]
    assert payload[1]["kind"] == "image"
    assert payload[1]["rel"] == "img/x.png"
    assert payload[1]["hint"] == "图1 示意"
