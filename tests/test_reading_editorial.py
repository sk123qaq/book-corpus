"""编辑层校验与真实渲染：旁批、图版、节拍、收束。"""

import pytest

from bookcorpus.reading.editorial import EditorialError, audit_editorial, validate_editorial
from bookcorpus.reading.render import render_chapter
from bookcorpus.reading.service import ReadingService
from bookcorpus.reading.tiers import project


def _payload(block_id):
    return {
        "schema": 1,
        "uid": "B0001",
        "n": 1,
        "beats": [
            {
                "id": "beat-1",
                "title": "先看一个人",
                "question": "这一节为什么从一个具体的人开始？",
                "before_block": block_id,
                "tiers": ["standard"],
            }
        ],
        "visuals": [
            {
                "id": "predict-1",
                "kind": "prediction",
                "after_block": block_id,
                "placement": "after",
                "tiers": ["standard"],
                "data": {
                    "question": "你觉得接下来会发生什么？",
                    "options": ["留下", "离开"],
                    "reveal": "记下了：{guess}。答案在下面。",
                },
                "claims": [],
            }
        ],
        "takeaways": [{"text": "具体的人先于抽象的编号。", "sources": [block_id]}],
        "note_tags": {block_id: "证据 · 开篇"},
    }


def _chapter(reading_root):
    service = ReadingService(str(reading_root), annotate=False)
    raw = service.library.read_chapter("B0001", 1)
    block = next(item for item in raw["blocks"] if item["kind"] == "paragraph")
    return service, raw, block


def test_validate_editorial_accepts_and_normalizes(reading_root):
    _, raw, block = _chapter(reading_root)
    clean = validate_editorial(_payload(block["id"]), raw["blocks"], uid="B0001", n=1)
    assert clean["schema"] == 1
    assert clean["beats"][0]["id"] == "beat-1"
    assert clean["visuals"][0]["kind"] == "prediction"
    assert clean["visuals"][0]["placement"] == "after"
    assert clean["note_tags"][block["id"]] == "证据 · 开篇"


def test_validate_editorial_rejects_unknown_ref(reading_root):
    _, raw, _block = _chapter(reading_root)
    payload = _payload("nope")
    with pytest.raises(EditorialError):
        validate_editorial(payload, raw["blocks"], uid="B0001", n=1)


def test_audit_editorial_flags_missing_anchor_in_tier(reading_root):
    _, raw, block = _chapter(reading_root)
    payload = _payload(block["id"])
    payload["visuals"][0]["tiers"] = ["skim"]
    clean = validate_editorial(payload, raw["blocks"], uid="B0001", n=1)
    projections = {tier: project(raw["blocks"], None, tier) for tier in ("skim", "standard", "deep", "research")}
    projections["skim"] = {"blocks": [], "stats": {}}
    problems = audit_editorial(clean, raw["blocks"], projections)
    assert problems
    assert any("skim" in problem for problem in problems)


def test_render_chapter_includes_editorial(reading_root):
    service, raw, block = _chapter(reading_root)
    blocks = raw["blocks"]
    projections = {tier: project(blocks, None, tier) for tier in ("skim", "standard", "deep", "research")}
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    clean = validate_editorial(_payload(block["id"]), blocks, uid="B0001", n=1)
    html = render_chapter(book, chapter, blocks, projections, editorial=clean)
    assert "has-editorial" in html
    assert 'class="spine"' in html
    assert 'id="beat-beat-1"' in html
    assert 'data-visual="prediction"' in html
    assert "具体的人先于抽象的编号" in html
    assert "第 1 章" not in html
    assert "body.only-source .flow-row{grid-template-columns:minmax(0,680px)}" in html
    assert "body.only-source .stat-notes{display:none}" in html


def test_render_chapter_without_editorial_stays_plain(reading_root):
    service, raw, _block = _chapter(reading_root)
    blocks = raw["blocks"]
    projections = {tier: project(blocks, None, tier) for tier in ("skim", "standard", "deep", "research")}
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    html = render_chapter(book, chapter, blocks, projections)
    assert "has-editorial" not in html
    assert "data-visual" not in html
    assert "证据 · 开篇" not in html


def test_card_element_uses_editorial_tag():
    from bookcorpus.reading.render import _card_element

    card = {
        "id": "c1",
        "trigger": "b1",
        "title": "",
        "text": "一句解释。",
        "citations": [],
        "quotes": [],
        "priority": 0,
    }
    html = _card_element(card, {"standard"}, tag="证据 · 开篇")
    assert "证据 · 开篇" in html
    assert "讲解 · 模型补充" not in html
