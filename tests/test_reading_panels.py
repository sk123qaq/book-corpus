import pytest

from bookcorpus.reading.depth.panels import (
    PANELS_PROMPT_VERSION,
    PANELS_SCHEMA_VERSION,
    PanelsError,
    audit_panels,
    empty_panels,
    validate_panels,
)
from bookcorpus.reading.depth.render import _panels_html


def _fixture():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "张三在2020年进入市场，先做渠道，再做品牌。"},
        {"id": "B02", "kind": "paragraph", "text": "他没有自己的工厂，靠代工和经销商。"},
    ]
    model = {
        "uid": "B0001",
        "n": 1,
        "units": [
            {"id": "U01", "source_blocks": ["B01"]},
            {"id": "U02", "source_blocks": ["B02"]},
        ],
    }
    return blocks, model


def _payload(kind="argument_map"):
    return {
        "schema": PANELS_SCHEMA_VERSION,
        "prompt_version": PANELS_PROMPT_VERSION,
        "mode": "argument",
        "source_sha256": "abc",
        "panels": [
            {
                "id": "P01",
                "kind": kind,
                "title": "论证地图",
                "summary": "从渠道到品牌",
                "items": [
                    {
                        "text": "张三在2020年进入市场",
                        "blocks": ["B01"],
                        "units": ["U01"],
                        "source": "本章",
                    }
                ],
            }
        ],
        "warnings": [],
    }


def test_panels_valid_payload_keeps_anchor_and_quote():
    blocks, model = _fixture()
    payload = _payload()
    payload["panels"][0]["items"][0]["quote"] = "张三在2020年进入市场"
    clean = validate_panels(payload, model, blocks, mode="argument", source_sha256="abc")
    assert clean["panels"][0]["items"][0]["blocks"] == ["B01"]
    assert clean["panels"][0]["items"][0]["quote"] == "张三在2020年进入市场"
    assert audit_panels(payload, model, blocks, mode="argument", source_sha256="abc")["status"] == "pass"


def test_panels_rejects_wrong_mode_kind():
    blocks, model = _fixture()
    payload = _payload(kind="timeline")
    with pytest.raises(PanelsError, match="不属于 argument"):
        validate_panels(payload, model, blocks, mode="argument")


def test_panels_rejects_missing_block_anchor():
    blocks, model = _fixture()
    payload = _payload()
    payload["panels"][0]["items"][0]["blocks"] = []
    with pytest.raises(PanelsError, match="至少需要一个 blocks 锚点"):
        validate_panels(payload, model, blocks, mode="argument")


def test_panels_rejects_number_not_in_source():
    blocks, model = _fixture()
    payload = _payload()
    payload["panels"][0]["items"][0]["text"] = "张三在2021年进入市场"
    with pytest.raises(PanelsError, match="2021"):
        validate_panels(payload, model, blocks, mode="argument")


def test_panels_rejects_quote_not_verbatim():
    blocks, model = _fixture()
    payload = _payload()
    payload["panels"][0]["items"][0]["quote"] = "张三在2019年进入市场"
    with pytest.raises(PanelsError, match="逐字"):
        validate_panels(payload, model, blocks, mode="argument")


def test_panels_duplicate_kind_is_warning():
    blocks, model = _fixture()
    payload = _payload()
    payload["panels"].append({**payload["panels"][0], "id": "P02"})
    clean = validate_panels(payload, model, blocks, mode="argument")
    assert any("同一类型" in warning for warning in clean["warnings"])


def test_empty_panels_render_message():
    panels = empty_panels("narrative", "本章没有足够的人物与时间线")
    html = _panels_html(panels, "standard")
    assert "本章没有足够的结构信息" in html
    assert panels["panels"] == []
