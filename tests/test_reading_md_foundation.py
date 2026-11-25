"""MD 解析层（L1）与伴读预处理层（L2）的回归测试。"""

import json
from pathlib import Path

import pytest

from bookcorpus.reading import chapmap
from bookcorpus.reading.mdparse import BLOCK_VERSION, _attach_orphan_footnotes, _clean_inline, parse_markdown
from bookcorpus.reading.service import ReadingService
from bookcorpus.reading.store import Store

# ---- L1：行内脚注图标不切句 ---------------------------------------------


def test_inline_note_icons_fold_into_one_paragraph(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "note.png").write_bytes(b"x")
    (images / "note_3.png").write_bytes(b"x")
    raw = "他说，^![note](images/note.png)^这句话还没完，[![](images/note_3.png)]()后面继续。\n"
    parsed = parse_markdown(raw, base_dir=str(tmp_path))
    blocks = parsed["blocks"]
    assert len(blocks) == 1
    block = blocks[0]
    assert block["kind"] == "paragraph"
    assert block["text"] == "他说，这句话还没完，后面继续。"
    assert "^" not in block["text"]
    assert "](" not in block["text"]
    assert [ref["marker"] for ref in block["fn_refs"]] == ["note", "3"]
    assert all(ref["_style"] == "icon" for ref in block["fn_refs"])


def test_inline_note_icon_in_list_item_stays_single_item(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "note_2.png").write_bytes(b"x")
    raw = "- 第一条^![note](images/note_2.png)^继续说完\n- 第二条\n"
    parsed = parse_markdown(raw, base_dir=str(tmp_path))
    assert [block["kind"] for block in parsed["blocks"]] == ["list"]
    block = parsed["blocks"][0]
    assert block["items"] == ["第一条继续说完", "第二条"]
    assert block["item_fn_refs"][0][0]["marker"] == "2"


def test_normal_small_picture_still_splits_as_image(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "photo.png").write_bytes(b"x")
    raw = "前一句![照片](images/photo.png)后一句\n"
    parsed = parse_markdown(raw, base_dir=str(tmp_path))
    assert [block["kind"] for block in parsed["blocks"]] == ["paragraph", "image", "paragraph"]


def test_clean_inline_strips_html_entities_and_pandoc_attrs():
    assert _clean_inline("<sup>“</sup>绝地天通<sup>”</sup>") == "“绝地天通”"
    assert _clean_inline("A &mdash; B &amp; C&nbsp;D") == "A — B & C D"
    assert _clean_inline("标题 {#x .calibre1}") == "标题"


# ---- L1：裸注号 + 门控 ---------------------------------------------------


def test_bare_note_number_requires_definition_or_note_title():
    raw = "正文提到注33，还有年份2020与[2020]。\n"
    parsed = parse_markdown(raw)
    assert not parsed["blocks"][0].get("fn_refs")


def test_bare_note_number_resolves_when_definition_exists():
    raw = "正文提到注33，也提到[7]。\n\n## 注释\n\n注33 这里是作者注。\n\n[7] 这里是文献注。\n"
    parsed = parse_markdown(raw)
    refs = parsed["blocks"][0]["fn_refs"]
    assert [ref["marker"] for ref in refs] == ["注33", "7"]
    assert all(ref["target_block"] for ref in refs)
    kinds = {block["id"]: block["kind"] for block in parsed["blocks"]}
    assert kinds[refs[0]["target_block"]] == "footnote"


def test_trailing_note_run_is_promoted_and_pairs_ref():
    raw = "正文引用注2。\n\n注1 第一条注释。\n\n注2 第二条注释。\n\n注3 第三条注释。\n"
    parsed = parse_markdown(raw)
    footnotes = [block for block in parsed["blocks"] if block["kind"] == "footnote"]
    assert [block["marker"] for block in footnotes] == ["注1", "注2", "注3"]
    ref = parsed["blocks"][0]["fn_refs"][0]
    assert ref["marker"] == "注2"
    assert ref["target_block"] == footnotes[1]["id"]


# ---- L1：NOTE 标题与尾注提升 --------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["作者注", "译者注", "编者注", "译注", "注1", "全书脚注除特殊标注之外", "版本和注释说明", "〈ご注意〉"],
)
def test_note_headings_promote_following_numbered_paragraphs(title):
    raw = f"正文。\n\n## {title}\n\n① 第一条注释。\n\n② 第二条注释。\n"
    parsed = parse_markdown(raw)
    footnotes = [block for block in parsed["blocks"] if block["kind"] == "footnote"]
    assert len(footnotes) == 2
    assert [block["marker"] for block in footnotes] == ["①", "②"]


def test_note_heading_with_circled_run_marks_all_footnotes():
    raw = "正文。\n\n## 脚注\n\n" + "\n\n".join(f"{chr(0x2460 + i)} 注文{i}。" for i in range(20)) + "\n"
    parsed = parse_markdown(raw)
    footnotes = [block for block in parsed["blocks"] if block["kind"] == "footnote"]
    assert len(footnotes) == 20
    assert footnotes[0]["marker"] == "①"


def test_orphan_footnotes_backfill_loosely_when_list_has_extra_items():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "正文", "chars": 2},
        {"id": "B02", "kind": "footnote", "text": "", "chars": 0, "marker": "1"},
        {"id": "B03", "kind": "footnote", "text": "", "chars": 0, "marker": "2"},
        {
            "id": "B04",
            "kind": "list",
            "text": "1 甲 2 乙 3 丙",
            "chars": 6,
            "items": ["1 甲", "2 乙", "3 丙"],
        },
    ]
    _attach_orphan_footnotes(blocks)
    assert [block["text"] for block in blocks if block["kind"] == "footnote"] == ["甲", "乙"]
    assert blocks[-1]["items"] == ["3 丙"]


# ---- L1：HTML 表格 -------------------------------------------------------


def test_html_table_becomes_structured_block():
    raw = (
        "前面一句\n\n"
        '<table border="1"><tr><th>年份</th><th>值</th></tr>'
        "<tr><td>2020</td><td>1 &amp; 2</td></tr></table>后面一句\n"
    )
    parsed = parse_markdown(raw)
    kinds = [block["kind"] for block in parsed["blocks"]]
    assert kinds == ["paragraph", "table", "paragraph"]
    table = parsed["blocks"][1]
    assert table["head"] == ["年份", "值"]
    assert table["rows"] == [["2020", "1 & 2"]]
    assert "<table" not in table["text"]
    assert parsed["blocks"][2]["text"] == "后面一句"


# ---- BLOCK_VERSION 缓存隔离 ---------------------------------------------


def test_block_version_invalidates_chapter_map_cache(tmp_path):
    class _Layout:
        work = str(tmp_path)

    store = Store(_Layout())
    store.save_chapter_tool("B0001", 1, "k", {"source_sha256": "abc", "result": 1})
    path = store.chapter_tool_path("B0001", 1, "k")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    assert doc["block_version"] == BLOCK_VERSION
    assert store.chapter_tool("B0001", 1, "k", source_sha="abc")["result"] == 1

    doc["block_version"] = BLOCK_VERSION - 1
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    stale = store.chapter_tool("B0001", 1, "k", source_sha="abc")
    assert stale["stale"] is True
    assert store.list_chapter_tools("B0001", 1, source_sha="abc") == []


def test_chapmap_cache_key_changes_with_block_version(monkeypatch):
    before = chapmap.cache_key("a", "b", "m", "e")
    monkeypatch.setattr(chapmap, "BLOCK_VERSION", BLOCK_VERSION + 1)
    assert chapmap.cache_key("a", "b", "m", "e") != before


# ---- L2：伴读上下文公共预处理 -------------------------------------------


class _FakeLibrary:
    def __init__(self, chapters=None):
        self._chapters = chapters or {}

    def read_chapter(self, uid, n):
        return self._chapters.get(int(n))


class _FakeStore:
    def chapter_map(self, uid, n, source_sha=None):
        return None


def _service(library=None):
    service = object.__new__(ReadingService)
    service.library = library or _FakeLibrary()
    service.store = _FakeStore()
    return service


def test_cross_file_note_text_is_injected():
    library = _FakeLibrary({3: {"blocks": [{"id": "B01", "kind": "footnote", "marker": "33", "text": "跨章注文内容"}]}})
    service = _service(library)
    blocks = [
        {
            "id": "B01",
            "kind": "paragraph",
            "text": "正文引用注33。",
            "fn_refs": [{"marker": "注33", "target_block": "", "target_chapter": {"n": "3", "block_id": "B01"}}],
        }
    ]
    text, _, _ = service._chapter_scope_context("B9999", 1, blocks, budget=4000)
    assert "跨章注文内容" in text
    assert "B01·注33" in text


def test_pure_note_chapter_gets_header():
    service = _service()
    blocks = [
        {"id": "B01", "kind": "footnote", "marker": "1", "text": "注文一"},
        {"id": "B02", "kind": "footnote", "marker": "2", "text": "注文二"},
    ]
    text, _, _ = service._chapter_scope_context("B9999", 314, blocks, budget=4000)
    assert text.startswith("【本章为全书/分卷注释汇总章】")
    assert "注文一" in text


def test_page_number_and_pua_are_skipped():
    service = _service()
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "40"},
        {"id": "B02", "kind": "paragraph", "text": "正文\ue000片段"},
    ]
    text, _, _ = service._chapter_scope_context("B9999", 1, blocks, budget=4000)
    assert "[B01]" not in text
    assert "\ue000" not in text
    assert "正文片段" in text


def test_image_block_uses_adjacent_caption():
    service = _service()
    blocks = [
        {"id": "B01", "kind": "image", "text": "", "rel": "images/pic.png"},
        {"id": "B02", "kind": "paragraph", "text": "图1 数据来源：作者整理"},
    ]
    text, _, _ = service._chapter_scope_context("B9999", 1, blocks, budget=4000)
    assert "图片：pic.png" in text
    assert "数据来源" in text


def test_tail_note_run_moves_to_footnotes():
    service = _service()
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "正文。"},
        {"id": "B02", "kind": "paragraph", "text": "注1 第一条"},
        {"id": "B03", "kind": "paragraph", "text": "注2 第二条"},
        {"id": "B04", "kind": "paragraph", "text": "注3 第三条"},
    ]
    text, _, _ = service._chapter_scope_context("B9999", 1, blocks, budget=4000)
    body, _, foot = text.partition("【本章自带脚注")
    assert "注1 第一条" not in body
    assert "第一条" in foot


def test_long_short_line_chapter_merges_ranges():
    service = _service()
    blocks = [{"id": f"B{idx:02d}", "kind": "paragraph", "text": f"台词{idx}"} for idx in range(1, 71)]
    text, count, sampled = service._chapter_scope_context("B9999", 32, blocks, budget=100)
    assert sampled is True
    assert count == 70
    assert "[B01–B" in text


def test_chapmap_keeps_dialogue_lines_as_dialogue():
    chunk = [{"id": f"B{idx:02d}", "kind": "paragraph", "text": f"第{idx}句台词"} for idx in range(1, 25)]
    data = {"units": [], "sections": [], "terms": [], "figures": [], "non_semantic": [], "folds": []}
    chapmap._cover_structural_blocks(data, chunk)
    assert {entry["reason"] for entry in data["non_semantic"]} == {"dialogue"}


def test_trailing_notes_without_space_are_promoted():
    raw = (
        "正文提到丹纳注18写道这是错的。\n\n"
        "注16 第十六条注释。\n\n"
        "注17 第十七条注释。\n\n"
        "注18 伊波利特·丹纳，法国历史学家。\n"
    )
    parsed = parse_markdown(raw, stem="01_测试")
    footnotes = [b for b in parsed["blocks"] if b["kind"] == "footnote"]
    assert [b["marker"] for b in footnotes[-3:]] == ["注16", "注17", "注18"]
    refs = [r for b in parsed["blocks"] for r in (b.get("fn_refs") or [])]
    assert refs
    assert refs[-1]["target_block"] == footnotes[-1]["id"]


def test_trailing_endnote_list_from_item_refs_is_promoted():
    raw = "正文。\n\n- [[1]]()第一条注文。\n- [[2]]()第二条注文。\n- [[3]]()第三条注文。\n"
    parsed = parse_markdown(raw, stem="01_测试")
    footnotes = [b for b in parsed["blocks"] if b["kind"] == "footnote"]
    assert [b["marker"] for b in footnotes] == ["1", "2", "3"]
    assert "第一条注文" in footnotes[0]["text"]
    assert "item_fn_refs" not in parsed["blocks"][-1]


def test_unlabelled_note_icons_pair_to_numbered_endnotes():
    raw = (
        "第一句[![](images/note.png)]()。\n\n"
        "第二句[![](images/note.png)]()。\n\n"
        "## 注释\n\n"
        "[71] 注释七十一。\n\n"
        "[72] 注释七十二。\n"
    )
    parsed = parse_markdown(raw, stem="01_测试", base_dir=None)
    refs = [r for b in parsed["blocks"] for r in (b.get("fn_refs") or [])]
    assert [r["marker"] for r in refs] == ["71", "72"]
    assert all(r["target_block"] for r in refs)
    footnotes = [b for b in parsed["blocks"] if b["kind"] == "footnote"]
    assert [b["marker"] for b in footnotes] == ["71", "72"]
