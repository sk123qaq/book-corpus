"""四档规则：结构校验、依赖闭包、确定性投影、草稿回退。"""

import pytest

from bookcorpus.reading.tiers import (
    TIER_ORDER,
    TIER_SPECS,
    draft_annotation,
    project,
    validate_annotation,
)

TEXT = "某" * 99 + "。"


def _blocks():
    return [
        {"id": "B01", "kind": "paragraph", "text": TEXT, "chars": 99},
        {"id": "B02", "kind": "paragraph", "text": TEXT, "chars": 99},
        {"id": "B03", "kind": "paragraph", "text": TEXT, "chars": 99},
        {"id": "B04", "kind": "paragraph", "text": TEXT, "chars": 99},
    ]


def _annotation():
    return {
        "blocks": [
            {"id": "B01", "role": "claim", "priority": 1, "spine": True, "deps": ["B02"]},
            {"id": "B02", "role": "evidence", "priority": 1, "spine": True},
            {"id": "B03", "role": "context", "priority": 2},
            {"id": "B04", "role": "transition", "priority": 3},
        ],
        "supports": [{"from": "B02", "to": "B01"}],
        "cards": [
            {
                "id": "C01",
                "trigger": "B01",
                "text": "解释" * 10,
                "citations": ["B01"],
                "quotes": [{"block": "B01", "text": TEXT}],
                "priority": 1,
            }
        ],
    }


def test_tier_specs_match_design():
    assert TIER_ORDER == ("skim", "standard", "deep", "research")
    assert TIER_SPECS["skim"] == {
        "label": "速览",
        "c_min": 0.40,
        "c_max": 0.55,
        "b_min": 0.30,
        "b_max": 0.65,
        "e_max": 0.10,
        "figures": 2,
        "wpm": 600,
    }
    assert TIER_SPECS["standard"]["c_min"] == 0.60
    assert TIER_SPECS["deep"]["c_max"] == 0.90
    assert TIER_SPECS["research"]["c_max"] == 1.00


def test_validate_annotation_fills_defaults():
    out = validate_annotation(_annotation(), _blocks())
    assert [entry["id"] for entry in out["blocks"]] == ["B01", "B02", "B03", "B04"]
    assert out["blocks"][2]["deps"] == []
    assert out["blocks"][3]["deps"] == []
    assert out["supports"] == [{"from": "B02", "to": "B01"}]
    assert out["cards"][0]["quotes"][0]["block"] == "B01"


def test_validate_annotation_accepts_block_level_supports():
    annotation = _annotation()
    annotation["supports"] = []
    annotation["blocks"][0]["supports"] = ["B02"]
    out = validate_annotation(annotation, _blocks())
    assert out["supports"] == [{"from": "B02", "to": "B01"}]

    fallback = _annotation()
    fallback["supports"] = [{"id": "B01", "supports": ["B02"]}]
    out = validate_annotation(fallback, _blocks())
    assert out["supports"] == [{"from": "B02", "to": "B01"}]


def test_validate_annotation_rejects_cycle_and_bad_quote():
    bad = _annotation()
    bad["blocks"][0]["deps"] = ["B02"]
    bad["blocks"][1]["deps"] = ["B01"]
    with pytest.raises(ValueError, match="依赖成环"):
        validate_annotation(bad, _blocks())
    bad = _annotation()
    bad["cards"][0]["quotes"][0]["text"] = "不在原文"
    with pytest.raises(ValueError, match="引文不在"):
        validate_annotation(bad, _blocks())


def test_project_skims_cap_drops_lowest_priority_first():
    annotation = _annotation()
    annotation["tier_blocks"] = {"standard": ["B01", "B02", "B03", "B04"]}
    annotation = validate_annotation(annotation, _blocks())
    out = project(_blocks(), annotation, "standard")
    ids = [block["id"] for block in out["blocks"]]
    assert "B04" not in ids
    assert "B02" in ids
    assert out["stats"]["c_char"] <= TIER_SPECS["standard"]["c_max"]


def test_project_skim_respects_cap_and_budget():
    annotation = validate_annotation(_annotation(), _blocks())
    out = project(_blocks(), annotation, "skim")
    assert out["draft"] is False
    assert 0.40 <= out["stats"]["c_char"] <= 0.55
    assert out["stats"]["c_block"] <= 0.55
    assert out["stats"]["notes"] == 1
    assert out["stats"]["e"] <= 0.10
    ids = [block["id"] for block in out["blocks"]]
    assert "B01" in ids
    assert "B02" in ids
    assert ids.index("B01") < ids.index("B02")


def test_project_standard_keeps_more_than_skim():
    annotation = validate_annotation(_annotation(), _blocks())
    skim = project(_blocks(), annotation, "skim")
    standard = project(_blocks(), annotation, "standard")
    assert standard["stats"]["chars"] >= skim["stats"]["chars"]
    assert standard["stats"]["c_char"] <= 0.75
    assert standard["stats"]["e"] <= 0.25


def test_project_research_keeps_all_content():
    annotation = validate_annotation(_annotation(), _blocks())
    out = project(_blocks(), annotation, "research")
    assert out["stats"]["c_char"] == 1.0
    assert [block["id"] for block in out["blocks"]] == ["B01", "B02", "B03", "B04"]


def test_tier_blocks_override_wins():
    annotation = validate_annotation({**_annotation(), "tier_blocks": {"standard": ["B04"]}}, _blocks())
    out = project(_blocks(), annotation, "standard")
    assert [block["id"] for block in out["blocks"]] == ["B04"]


def test_draft_without_annotation_keeps_everything_for_all_tiers():
    draft = draft_annotation(_blocks())
    assert draft["draft"] is True
    assert len(draft["blocks"]) == 4
    assert draft["blocks"][0]["role"] == "claim"
    for tier in TIER_ORDER:
        out = project(_blocks(), None, tier)
        assert out["draft"] is True
        assert len(out["blocks"]) == 4
        assert out["stats"]["c_char"] == 1.0
        assert out["stats"]["c_block"] == 1.0
        assert out["stats"]["e"] == 0.0
        assert out["stats"]["notes"] == 0
        assert out["stats"]["minutes"] >= 1
