import copy

import pytest

from bookcorpus.reading.depth.model import (
    _chunk_payload,
    _merge_chunks,
    _normalize_chunk_refs,
)
from bookcorpus.reading.depth.schema import PROMPT_VERSION, ChapterModelError


def _blocks():
    return [{"id": f"B{index:02d}", "kind": "paragraph", "text": f"第{index}段。"} for index in range(1, 7)]


def _unit(unit_id, block_id, section_id):
    return {
        "id": unit_id,
        "kind": "claim",
        "importance": "core",
        "hard_info": False,
        "summary": f"第{int(block_id[-2:])}段的判断",
        "quotes": [{"block": block_id, "text": f"第{int(block_id[-2:])}段。"}],
        "source_blocks": [block_id],
        "relations": [],
        "section": section_id,
    }


def _payload():
    return {
        "schema": 1,
        "uid": "B1848",
        "n": 5,
        "source_sha256": "sha",
        "prompt_version": PROMPT_VERSION,
        "units": [
            # deliberately wrong section: listed in C01-S01 but says C01-S02
            _unit("C01-U01", "C01-B01", "C01-S02"),
            _unit("C01-U02", "C01-B02", "C01-S02"),
            _unit("C01-U03", "C01-B03", "C01-S02"),
        ],
        "sections": [
            {"id": "C01-S01", "title": "第一节", "unit_ids": ["C01-U01", "C01-U02"]},
            {"id": "C01-S02", "title": "第二节", "unit_ids": ["C01-U03"]},
        ],
        "terms": [
            {
                "term": "测试术语",
                "definition": "测试定义",
                "source_block": "C01-B04",
                "quote": "第4段。",
            }
        ],
        "figures": [],
        "non_semantic": [
            {"block": "C01-B05", "reason": "decorative"},
            {"block": "C01-B06", "reason": "decorative"},
        ],
        "folds": [],
    }


def test_chunk_prefix_is_stripped_and_sections_reconciled():
    blocks = _blocks()
    chapter = {"uid": "B1848", "n": 5, "source_sha256": "sha"}
    raw = _payload()

    with pytest.raises(ChapterModelError, match="C01-B01"):
        _chunk_payload(copy.deepcopy(raw), blocks, chapter)

    out = _chunk_payload(copy.deepcopy(raw), blocks, chapter, id_prefix="C01-")

    assert out["units"][0]["source_blocks"] == ["B01"]
    assert out["units"][0]["quotes"][0]["block"] == "B01"
    assert out["units"][0]["section"] == "C01-S01"
    assert out["units"][1]["section"] == "C01-S01"
    assert out["units"][2]["section"] == "C01-S02"
    assert out["terms"][0]["source_block"] == "B04"
    assert [entry["block"] for entry in out["non_semantic"]] == ["B05", "B06"]

    merged = _merge_chunks([out])
    assert merged["units"]
    assert merged["sections"]


def test_fold_block_list_is_expanded_and_stripped():
    raw = {"folds": [{"block": ["C01-B05", "C01-B06"], "reason": "重复"}]}
    out = _normalize_chunk_refs(raw, id_prefix="C01-")
    assert [entry["block"] for entry in out["folds"]] == ["B05", "B06"]


def test_quote_moves_to_a_source_block_that_contains_the_text():
    blocks = _blocks()
    raw = _payload()
    raw["units"][0]["source_blocks"] = ["C01-B01", "C01-B02"]
    raw["units"][0]["quotes"][0]["text"] = "第2段。"
    out = _normalize_chunk_refs(raw, id_prefix="C01-", chunk=blocks)
    assert out["units"][0]["quotes"][0]["block"] == "B02"


def test_hard_note_is_appended_to_summary():
    blocks = _blocks()
    raw = _payload()
    unit = raw["units"][0]
    unit["hard_info"] = True
    unit["hard_info_note"] = "第1段。"
    unit["summary"] = "判断"
    out = _normalize_chunk_refs(raw, id_prefix="C01-", chunk=blocks)
    assert "第1段。" in out["units"][0]["summary"]


def test_structural_leftover_becomes_non_semantic():
    blocks = _blocks()
    blocks[-1] = {"id": "B06", "kind": "heading", "text": "小标题", "level": 3}
    raw = _payload()
    raw["non_semantic"] = [{"block": "C01-B05", "reason": "decorative"}]
    out = _normalize_chunk_refs(raw, id_prefix="C01-", chunk=blocks)
    assert {"block": "B06", "reason": "heading"} in out["non_semantic"]


def test_skim_allows_one_claim_for_a_two_unit_section():
    from bookcorpus.reading.depth.skim import _checks

    blocks = [{"id": "B01", "kind": "paragraph", "text": "甲" * 1000}]
    model = {
        "units": [{"id": "U01", "source_blocks": ["B01"], "importance": "core", "section": "S01"}],
        "sections": [{"id": "S01", "unit_ids": ["U01"]}],
    }
    payload = {
        "schema": 1,
        "prompt_version": "skim-compiler-v2",
        "kind": "claims",
        "supported": True,
        "reason": "",
        "sections": [
            {
                "id": "S01",
                "lead": "导语。",
                "claims": [{"text": "乙" * 200, "units": ["U01"], "anchor": "B01"}],
                "turn": "",
            }
        ],
        "detail_index": [],
    }
    _clean, g11, _g14, _warnings, _audit = _checks(payload, model, blocks)
    assert not any("claims 必须有" in item for item in g11)
