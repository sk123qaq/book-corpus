"""Phase 1 深度包：结构模型、守恒账、存储、CLI 与服务接线。"""

import copy
import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from bookcorpus.reading.depth import (
    DEEP_PROMPT_VERSION,
    NOTE_TYPES_BY_MODE,
    SKIM_PROMPT_VERSION,
    SYSTEM_PROMPT,
    ChapterModelError,
    DepthModelError,
    SkimError,
    audit_conservation,
    audit_skim,
    generate_chapter_model,
    generate_skim,
    plan_dispositions,
    render_depth_chapter,
    validate_chapter_model,
    validate_deep,
    validate_skim,
)
from bookcorpus.reading.depth import audit as depth_audit
from bookcorpus.reading.depth import cli as depth_cli
from bookcorpus.reading.depth.cli import _state_text
from bookcorpus.reading.depth.cli import main as depth_main
from bookcorpus.reading.depth.deep import DeepError


@pytest.fixture(autouse=True)
def _skip_preflight(monkeypatch):
    """这一组测分层与落盘；配置预检由 tests/test_preflight.py 单独测。"""
    from bookcorpus import preflight

    monkeypatch.setattr(preflight, "require", lambda *args, **kwargs: None)


from bookcorpus.reading import events as reading_events
from bookcorpus.reading import service as service_module
from bookcorpus.reading.depth import skim as skim_module
from bookcorpus.reading.depth.diagrams import audit_figures
from bookcorpus.reading.depth.disposition import plan_all_dispositions
from bookcorpus.reading.depth.skim import unsupported_skim
from bookcorpus.reading.depth.standard import STANDARD_PROMPT_VERSION, build_standard
from bookcorpus.reading.mdparse import parse_markdown
from bookcorpus.reading.model import ModelProviderError
from bookcorpus.reading.service import DEPTH_LAYERS, JobConflictError, ReadingService
from bookcorpus.reading.store import Store
from bookcorpus.scripts.layout import Layout

FIXTURES = Path(__file__).parent / "fixtures" / "depth"
GOLD = FIXTURES / "b0003_ch06_gold.json"
CHAPTER = FIXTURES / "b0003_ch06.md"
PACK_KEYS = {
    "reading",
    "book",
    "chapter",
    "jobs",
    "blocks",
    "tiers",
    "standard_blocks",
    "preview_blocks",
    "pack",
    "gates",
    "edition",
    "edition_fit",
    "path",
    "fallback_reason",
}


def _parsed():
    raw = CHAPTER.read_bytes()
    blocks = parse_markdown(raw.decode("utf-8"), stem="006_第6章")["blocks"]
    return blocks, hashlib.sha256(raw).hexdigest()


def _gold_fixture(sha=None):
    fixture = json.loads(GOLD.read_text(encoding="utf-8"))
    if sha is not None:
        fixture["source_sha256"] = sha
    return fixture


def _gold(sha=None):
    model = _gold_fixture(sha)
    model.pop("tiers", None)
    return model


def _valid(sha=None):
    blocks, actual = _parsed()
    if sha is None:
        sha = actual
    model = validate_chapter_model(_gold(sha), blocks, uid="B0003", n=6, source_sha256=sha)
    return model, blocks


def _deep_payload(blocks, units=None):
    ids = [block["id"] for block in blocks if block["kind"] != "heading"]
    units = units or ["S01-U01", "S01-U02", "S01-U03"]
    text = (
        "这条深读注解说明该段论证成立所依赖的前提与边界，并指出可能的反例，"
        "供读者回到同一份逐字原文核对；深读层只叠加解释，不复制任何原文句子，"
        "也不引入本章之外的史实来支撑结论。"
    )
    return {
        "schema": 1,
        "prompt_version": DEEP_PROMPT_VERSION,
        "notes": [
            {
                "id": "N01",
                "type": "assumption",
                "section": "S01",
                "units": [units[0]],
                "blocks": [ids[0]],
                "text": text,
                "origin": "editor",
                "source_scope": "本章",
            },
            {
                "id": "N02",
                "type": "boundary",
                "section": "S01",
                "units": [units[1]],
                "blocks": [ids[1]],
                "text": text.replace("前提", "边界"),
                "origin": "editor",
                "source_scope": "本章",
            },
        ],
        "chains": [
            {
                "id": "C01",
                "family": "argument",
                "section": "S01",
                "question": "这段论证如何成立？",
                "units": [units[0], units[1]],
                "origin": "derived",
                "caption": "论证链",
                "reading_note": "沿箭头读。",
                "data": {
                    "nodes": [
                        {"id": "n1", "label": "前提", "unit": units[0], "block": ids[0]},
                        {"id": "n2", "label": "结论", "unit": units[1], "block": ids[1]},
                    ],
                    "edges": [{"from": "n1", "to": "n2", "label": "推出"}],
                },
                "fallback": "按原文顺序读。",
            }
        ],
    }


def test_skim_scene_mode_requires_scenes_without_claim_chain():
    blocks, model, _ledger = _long_skim_fixture()
    payload = {
        "schema": 1,
        "prompt_version": SKIM_PROMPT_VERSION,
        "kind": "scenes",
        "supported": True,
        "reason": "",
        "scenes": [
            {"text": "场景甲", "anchor": "B02", "turn": "变化"},
            {"text": "场景乙", "anchor": "B02", "turn": "变化"},
            {"text": "场景丙", "anchor": "B02", "turn": "变化"},
            {"text": "场景丁", "anchor": "B02", "turn": "变化"},
            {"text": "场景戊", "anchor": "B02", "turn": "变化"},
        ],
    }
    cleaned = validate_skim(payload, model, blocks, mode="narrative")
    assert cleaned["kind"] == "scenes"
    assert len(cleaned["scenes"]) == 5
    payload["scenes"] = payload["scenes"][:2]
    with pytest.raises(ValueError, match="至少需要 3"):
        validate_skim(payload, model, blocks, mode="narrative")


def test_deep_mode_accepts_narrative_types_and_rejects_argument_type():
    model, blocks = _valid()
    units = [unit["id"] for unit in model["units"]][:3]
    payload = _deep_payload(blocks, units)
    payload["mode"] = "narrative"
    payload["notes"][0]["type"] = "scene"
    payload["notes"][1]["type"] = "character"
    cleaned = validate_deep(payload, model, blocks, mode="narrative")
    assert cleaned["mode"] == "narrative"
    assert {note["type"] for note in cleaned["notes"]} <= set(NOTE_TYPES_BY_MODE["narrative"])

    payload["notes"][0]["type"] = "assumption"
    with pytest.raises(ValueError, match="type 非法"):
        validate_deep(payload, model, blocks, mode="narrative")


def _unit(model, unit_id):
    return next(unit for unit in model["units"] if unit["id"] == unit_id)


def _load_gold_fixture(stem, uid, n):
    raw = (FIXTURES / f"{stem}.md").read_bytes()
    blocks = parse_markdown(raw.decode("utf-8"), stem=stem)["blocks"]
    fixture = json.loads((FIXTURES / f"{stem}_gold.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    assert fixture["source_sha256"] == sha
    tiers = fixture.pop("tiers")
    model = validate_chapter_model(fixture, blocks, uid=uid, n=n, source_sha256=sha)
    ledger = plan_all_dispositions(model, blocks)
    return model, blocks, ledger, tiers


def _tier_map(ledger):
    return {
        tier: {unit_id: entry["disposition"] for unit_id, entry in account["units"].items()}
        for tier, account in ledger["tiers"].items()
    }


def _long_skim_fixture():
    blocks = [
        {"id": "B01", "kind": "heading", "text": "第一节", "level": 2},
        {"id": "B02", "kind": "paragraph", "text": "源" * 1200},
    ]
    model = {
        "schema": 1,
        "uid": "B0003",
        "n": 6,
        "sections": [{"id": "S01", "title": "第一节", "unit_ids": ["U01", "U02", "U03", "U04"]}],
        "units": [
            {"id": "U01", "importance": "core", "source_blocks": ["B02"]},
            {"id": "U02", "importance": "support", "source_blocks": ["B02"]},
            {"id": "U03", "importance": "support", "source_blocks": ["B02"]},
            {"id": "U04", "importance": "support", "source_blocks": ["B02"]},
        ],
        "core_units": ["U01"],
    }
    skim = {
        "schema": 1,
        "prompt_version": SKIM_PROMPT_VERSION,
        "supported": True,
        "reason": "",
        "sections": [
            {
                "id": "S01",
                "lead": "本节交代核心命题与三项支撑材料，并标出适用边界。",
                "claims": [
                    {
                        "text": "核心命题说明理论在哪些条件下成立，先看前提再看结论，避免把局部经验当成普遍规律。",
                        "units": ["U01", "U02"],
                        "anchor": "B02",
                    },
                    {
                        "text": "第一项支撑材料给出一个正例，说明条件满足时判断标准如何落到具体选择上。",
                        "units": ["U03"],
                        "anchor": "B02",
                    },
                    {
                        "text": "第二项支撑材料给出一个反例，说明条件缺失时同一判断标准会导向相反结论。",
                        "units": ["U04"],
                        "anchor": "B02",
                    },
                    {
                        "text": (
                            "第三项支撑材料把正例与反例并置，提示读者注意边界而不是记住口号，"
                            "也不要把单一案例当成全部证据。"
                        ),
                        "units": ["U01"],
                        "anchor": "B02",
                    },
                ],
                "turn": "代价是例外情形会被压缩，读者需要回原文核对，先检查前提是否仍然成立。",
            }
        ],
        "detail_index": [],
    }
    return blocks, model, skim


@pytest.fixture
def depth_root(reading_root):
    md = reading_root / "books" / "B0003" / "md"
    md.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CHAPTER, md / "006_第6章.md")
    return reading_root


def test_real_gold_fixture_validates_and_has_explicit_core_units():
    blocks, sha = _parsed()
    fixture = _gold_fixture(sha)
    assert fixture["core_units"] == ["U01"]
    assert len(fixture["units"]) == 4
    model = validate_chapter_model(_gold(sha), blocks, uid="B0003", n=6, source_sha256=sha)
    assert model["prompt_version"] == "chapter-model-v1"
    assert {unit["id"] for unit in model["units"]} == {"U01", "U02", "U03", "U04"}
    assert model["non_semantic"] == [{"block": "B01", "reason": "节标题"}]
    ledger = plan_all_dispositions(model, blocks)
    actual = {
        tier: {unit_id: entry["disposition"] for unit_id, entry in account["units"].items()}
        for tier, account in ledger["tiers"].items()
    }
    assert actual == fixture["tiers"]


def test_schema_rejects_forged_quote():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U01")["quotes"][0]["text"] = "这是伪造的引文。"
    with pytest.raises(ChapterModelError, match="逐字"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_cross_block_concatenation():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U01")["quotes"][0]["text"] = "组织原则包含两种理论。大多数人认为，既然铁轨延展了1英寸。"
    with pytest.raises(ChapterModelError, match="逐字"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_unknown_block():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U02")["source_blocks"] = ["B99"]
    with pytest.raises(ChapterModelError, match="不存在的块"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_duplicate_unit_id():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U02")["id"] = "U01"
    with pytest.raises(ChapterModelError, match="unit id 重复"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_unknown_relation_target():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U02")["relations"][0]["to"] = "U99"
    with pytest.raises(ChapterModelError, match="不存在的单元"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_section_that_does_not_cover_a_unit():
    blocks, sha = _parsed()
    model = _gold(sha)
    model["sections"][0]["unit_ids"].remove("U04")
    with pytest.raises(ChapterModelError, match="未覆盖单元"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_model_supplied_tiers():
    blocks, sha = _parsed()
    model = _gold(sha)
    model["tiers"] = {}
    with pytest.raises(ChapterModelError, match="tiers"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_rejects_wrong_prompt_version():
    blocks, sha = _parsed()
    model = _gold(sha)
    model["prompt_version"] = "depth-v1"
    with pytest.raises(ChapterModelError, match="prompt_version"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_requires_boolean_hard_info_and_valid_note():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U02")["hard_info"] = ["底边为1英里"]
    with pytest.raises(ChapterModelError, match="hard_info 必须是布尔值"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)
    model = _gold(sha)
    _unit(model, "U02")["hard_info_note"] = "斜边为2英里"
    with pytest.raises(ChapterModelError, match="hard_info_note"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_schema_outputs_relations_to_and_terms_source_block():
    model, _ = _valid()
    assert _unit(model, "U02")["relations"] == [{"type": "supports", "to": "U01"}]
    assert "target" not in _unit(model, "U02")["relations"][0]
    assert model["terms"][0]["source_block"] == "B02"
    assert "block_id" not in model["terms"][0]


def test_schema_returns_copy_and_leaves_input_untouched():
    blocks, sha = _parsed()
    model = _gold(sha)
    before = copy.deepcopy(model)
    validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)
    assert model == before


def test_strict_verbatim_rejects_inserted_whitespace():
    blocks, sha = _parsed()
    model = _gold(sha)
    _unit(model, "U01")["quotes"][0]["text"] = _unit(model, "U01")["quotes"][0]["text"].replace(
        "规范性理论", "规范性 理论", 1
    )
    with pytest.raises(ChapterModelError, match="逐字"):
        validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=sha)


def test_disposition_verbatim_condensed_indexed_omitted_per_tier():
    model, blocks = _valid()
    skim = plan_dispositions(model, "skim", blocks)
    assert skim["units"]["U01"]["disposition"] == "condensed"
    assert skim["units"]["U02"]["disposition"] == "condensed"
    assert skim["units"]["U03"]["disposition"] == "indexed"
    assert skim["units"]["U04"]["disposition"] == "omitted"
    assert skim["units"]["U04"]["duplicate_of"] == "U02"
    assert skim["omitted_duplicates"] == 1
    assert skim["core_coverage"] == 1.0
    assert skim["hard_info_covered"] == 2
    assert skim["unmapped_blocks"] == 0
    for tier in ("standard", "deep"):
        account = plan_dispositions(model, tier, blocks)
        assert all(entry["disposition"] == "verbatim" for entry in account["units"].values())
    assert set(plan_all_dispositions(model, blocks)["tiers"]) == {"skim", "standard", "deep"}


def test_disposition_wires_folds_into_counts_and_unmapped():
    model, blocks = _valid()
    blocks = [*blocks, {"id": "B05", "kind": "paragraph", "text": "折叠的旁注。"}]
    model["folds"] = [{"block": "B05", "summary": "旁注", "reason": "非论证材料"}]
    model = validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=model["source_sha256"])
    ledger = plan_all_dispositions(model, blocks)
    skim = ledger["tiers"]["skim"]
    assert skim["folds_total"] == 1
    assert skim["blocks"]["B05"]["presentation"] == "folded"
    assert skim["unmapped_blocks"] == 0
    assert audit_conservation(model, ledger, blocks)["status"] == "pass"


def test_g10_with_expected_core_detects_deleted_core_and_unmapped_block():
    model, blocks = _valid()
    model["units"] = [unit for unit in model["units"] if unit["id"] != "U01"]
    model["sections"][0]["unit_ids"].remove("U01")
    for unit in model["units"]:
        unit["relations"] = [relation for relation in unit["relations"] if relation["to"] != "U01"]
    model = validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=model["source_sha256"])
    ledger = plan_all_dispositions(model, blocks)
    gate = audit_conservation(model, ledger, blocks, expected_core=["U01"])
    assert gate["status"] == "fail"
    assert gate["core_total"] == 1
    assert gate["core_covered"] == 0
    assert ledger["tiers"]["skim"]["unmapped_blocks"] == 1
    assert any("核心" in problem for problem in gate["problems"])
    assert any("未映射" in problem for problem in gate["problems"])


def test_g10_without_expected_core_documents_limitation():
    model, blocks = _valid()
    ledger = plan_all_dispositions(model, blocks)
    gate = audit_conservation(model, ledger, blocks)
    assert gate["status"] == "pass"
    assert "expected_core" in gate["limitation"]


def test_g10_passes_when_only_a_duplicate_is_deleted():
    model, blocks = _valid()
    model["units"] = [unit for unit in model["units"] if unit["id"] != "U04"]
    model["sections"][0]["unit_ids"].remove("U04")
    model = validate_chapter_model(model, blocks, uid="B0003", n=6, source_sha256=model["source_sha256"])
    ledger = plan_all_dispositions(model, blocks)
    assert ledger["tiers"]["skim"]["unmapped_blocks"] == 0
    assert audit_conservation(model, ledger, blocks)["status"] == "pass"


def test_g10_fails_when_condensed_summary_drops_hard_info():
    model, blocks = _valid()
    ledger = plan_all_dispositions(model, blocks)
    ledger["tiers"]["skim"]["units"]["U02"]["summary"] = "没有关键信息。"
    gate = audit_conservation(model, ledger, blocks)
    assert gate["status"] == "fail"
    assert any("硬信息" in problem for problem in gate["problems"])


def test_g10_condensed_allows_terminal_punctuation_difference():
    model, blocks = _valid()
    ledger = plan_all_dispositions(model, blocks)
    summary = ledger["tiers"]["skim"]["units"]["U01"]["summary"]
    assert summary.endswith("。")
    ledger["tiers"]["skim"]["units"]["U01"]["summary"] = summary[:-1]
    assert audit_conservation(model, ledger, blocks)["status"] == "pass"


def test_g10_fails_when_duplicate_canonical_is_also_omitted():
    model, blocks = _valid()
    ledger = plan_all_dispositions(model, blocks)
    ledger["tiers"]["skim"]["units"]["U02"]["disposition"] = "omitted"
    gate = audit_conservation(model, ledger, blocks)
    assert gate["status"] == "fail"
    assert any("规范" in problem or "U02" in problem for problem in gate["problems"])


def test_store_depth_state_missing_stale_model_ready(tmp_path):
    store = Store(Layout(tmp_path))
    assert store.depth_state("B0009", 1)["status"] == "missing"
    store.save_depth(
        "B0009",
        1,
        {
            "chapter_model.json": {"label": "one"},
            "state.json": {"status": "model", "source_sha256": "sha-one"},
        },
    )
    state = store.depth_state("B0009", 1, "sha-one")
    assert state["status"] == "model"
    assert "layers_sha" not in state
    assert store.depth_state("B0009", 1, "sha-two")["status"] == "stale"
    store.save_depth(
        "B0009",
        1,
        {
            "chapter_model.json": {"label": "two"},
            "state.json": {"status": "ready", "source_sha256": "sha-two", "layers_sha": {"figures": "x"}},
        },
    )
    assert store.depth_state("B0009", 1, "sha-two")["layers_sha"] == {"figures": "x"}


def test_save_without_depth_keeps_existing_depth_and_cache(tmp_path):
    store = Store(Layout(tmp_path))
    uid, n = "B0009", 1
    store.save(
        uid,
        n,
        {"status": "ok", "source_sha256": "sha"},
        {"version": 1},
        {"status": "ok"},
        "<p>old</p>",
        {"standard": "<p>old standard</p>"},
    )
    store.save_depth(
        uid,
        n,
        {
            "chapter_model.json": {"label": "old"},
            "ledger.json": {"label": "old"},
            "chapter.html": "<html>old depth</html>",
            "notes/a.txt": "keep",
            "state.json": {"status": "ready", "source_sha256": "sha"},
        },
    )
    cache = Path(store.depth_cache_dir(uid, n))
    cache.mkdir(parents=True)
    (cache / "key.json").write_text("{}", encoding="utf-8")
    store.save(
        uid,
        n,
        {"status": "ok", "source_sha256": "sha"},
        {"version": 2},
        {"status": "ok"},
        "<p>new</p>",
        {"standard": "<p>new standard</p>"},
    )
    pack = Path(store.pack_dir(uid, n))
    assert store.depth_state(uid, n, "sha")["status"] == "ready"
    assert store.depth_json(uid, n, "chapter_model.json") == {"label": "old"}
    assert (pack / "depth" / "notes" / "a.txt").read_text(encoding="utf-8") == "keep"
    assert (pack / "depth" / "cache" / "key.json").exists()
    assert store.annotation(uid, n) == {"version": 2}
    assert Path(store.chapter_html_path(uid, n)).read_text(encoding="utf-8") == "<p>new</p>"


def test_save_with_depth_replaces_only_depth(tmp_path):
    store = Store(Layout(tmp_path))
    uid, n = "B0009", 1
    store.save(uid, n, {"status": "ok"}, {"version": 1}, None, "<p>root</p>", {"standard": "x"})
    store.save(
        uid,
        n,
        {"status": "ok"},
        {"version": 2},
        None,
        "<p>root</p>",
        {"standard": "x"},
        depth={
            "chapter_model.json": {"label": "new"},
            "state.json": {"status": "ready", "source_sha256": "sha"},
        },
    )
    assert store.depth_json(uid, n, "chapter_model.json") == {"label": "new"}
    assert store.annotation(uid, n) == {"version": 1}


def test_save_depth_failure_keeps_old_depth(tmp_path):
    store = Store(Layout(tmp_path))
    uid, n = "B0009", 1
    old = {
        "chapter_model.json": {"label": "old"},
        "chapter.html": "<html>old depth</html>",
        "state.json": {"status": "ready", "source_sha256": "sha"},
    }
    store.save_depth(uid, n, old)
    with pytest.raises(TypeError):
        store.save_depth(
            uid,
            n,
            {
                "chapter_model.json": object(),
                "state.json": {"status": "ready", "source_sha256": "new"},
            },
        )
    assert store.depth_state(uid, n, "sha")["status"] == "ready"
    assert store.depth_json(uid, n, "chapter_model.json") == {"label": "old"}


def test_service_flag_off_keeps_old_keys_plus_path(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    data = service.chapter("B0001", 1)
    assert set(data) == PACK_KEYS
    assert "depth" not in data
    assert data["path"] == "none"
    assert data["fallback_reason"] == "尚未生成"


def _layer_statuses(depth):
    return {key: value["status"] for key, value in depth["layers"].items()}


def test_service_depth_missing_and_model_pack(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    depth = service.chapter("B0001", 1)["depth"]
    assert depth["state"] == "missing"
    assert depth["jobs"] == []
    assert set(depth["layers"]) == set(DEPTH_LAYERS)
    assert set(_layer_statuses(depth).values()) == {"missing"}
    raw = service.library.read_chapter("B0001", 1)
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "ledger.json": {"label": "ledger"},
            "state.json": {"status": "model", "source_sha256": raw["source_sha256"]},
        },
    )
    data = service.chapter("B0001", 1)
    depth = data["depth"]
    assert depth["state"] == "model"
    assert depth["model"] == {"label": "model"}
    assert depth["ledger"] == {"label": "ledger"}
    assert depth["layers"]["model"]["status"] == "ready"
    assert depth["layers"]["skim"]["status"] == "missing"
    assert depth["jobs"] == []
    assert set(data) == PACK_KEYS | {"depth"}


def test_chapter_route_only_claims_depth_when_page_is_servable(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0001", 1)
    sha = raw["source_sha256"]
    assert service.store.chapter_route("B0001", 1, sha) == ("none", "尚未生成")

    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "ledger.json": {"label": "ledger"},
            "state.json": {"status": "model", "source_sha256": sha},
        },
    )
    assert service.store.chapter_route("B0001", 1, sha) == ("none", "结构模型已建，深度页未出")
    assert service.chapter("B0001", 1)["path"] == "none"

    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter.html": "<html>compiled</html>",
            "state.json": {"status": "ready", "source_sha256": sha},
        },
    )
    assert service.store.chapter_route("B0001", 1, sha) == ("depth", None)
    assert service.chapter("B0001", 1)["path"] == "depth"
    assert service.store.chapter_route("B0001", 1, "outdated") == ("none", "源文已改，深度页过期")

    pack = Path(service.store.pack_dir("B0002", 1))
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps({"uid": "B0002", "n": 1}), encoding="utf-8")
    assert service.store.chapter_route("B0002", 1, "whatever") == ("legacy", "旧包按旧路径打开")


def test_service_depth_ready_pack_exposes_skim_and_html(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0001", 1)
    skim = {"schema": 1, "prompt_version": SKIM_PROMPT_VERSION, "supported": False, "reason": "短章"}
    deep = {"schema": 1, "prompt_version": DEEP_PROMPT_VERSION, "notes": [{"id": "N01"}]}
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "ledger.json": {"label": "ledger"},
            "layers/skim.json": skim,
            "layers/deep.json": deep,
            "chapter.html": "<html>compiled</html>",
            "state.json": {"status": "ready", "source_sha256": raw["source_sha256"]},
        },
    )
    data = service.chapter("B0001", 1)
    depth = data["depth"]
    assert depth["state"] == "ready"
    assert depth["model"] == {"label": "model"}
    assert depth["ledger"] == {"label": "ledger"}
    assert depth["skim"] == skim
    assert depth["deep"] == deep
    assert depth["html"] == "/reader/B0001/1/depth"
    statuses = _layer_statuses(depth)
    assert statuses["model"] == "ready"
    assert statuses["skim"] == "ready"
    assert statuses["standard"] == "missing"
    assert statuses["deep"] == "ready"
    assert depth["jobs"] == []


def test_service_depth_stale_hides_layers(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "old-model"},
            "ledger.json": {"label": "old-ledger"},
            "layers/skim.json": {"supported": False, "reason": "旧"},
            "chapter.html": "<html>old</html>",
            "state.json": {"status": "ready", "source_sha256": "outdated"},
        },
    )
    depth = service.chapter("B0001", 1)["depth"]
    assert depth["state"] == "stale"
    assert depth["layers"]["model"]["status"] == "stale"
    assert depth["layers"]["skim"]["status"] == "stale"
    assert depth["layers"]["standard"]["status"] == "missing"
    assert "html" not in depth


class _FakeQueue:
    def __init__(self, jobs=None, active=None, conflicts=None):
        self.jobs = jobs or []
        self.active = active or []
        self.blocking = conflicts or []
        self.submitted = []

    def list(self, limit=50):
        return self.jobs[:limit]

    def active_for(self, uid=None, n=None):
        if uid is None:
            return self.active
        return [item for item in self.active if item.get("uid") == uid and int(item.get("n")) == int(n)]

    def conflicts(self, uid, n):
        return self.blocking

    def submit(self, uid, n, tiers, **kwargs):
        job = {"uid": uid, "n": f"{int(n):02d}", "tiers": tiers, **kwargs}
        self.submitted.append(job)
        return job


def _ready_depth(depth_root, *, status="ready"):
    _, blocks, ledger, _ = _load_gold_fixture("b0003_ch06", "B0003", 6)
    raw = ReadingService(root=depth_root, annotate=False, depth=True).library.read_chapter("B0003", 6)
    model = _valid(raw["source_sha256"])[0]
    ledger = plan_all_dispositions(model, blocks)
    deep = _deep_payload(blocks)
    files = {
        "chapter_model.json": model,
        "ledger.json": ledger,
        "layers/panels.json": {
            "schema": 1,
            "prompt_version": "reading-panels-v1",
            "mode": "argument",
            "source_sha256": raw["source_sha256"],
            "panels": [],
            "warnings": [],
        },
        "layers/skim.json": unsupported_skim("这一章太短，适合读原文。"),
        "layers/standard.json": {"schema": 1, "units": []},
        "layers/deep.json": deep,
        "chapter.html": "<html>old depth</html>",
        "state.json": {
            "status": status,
            "source_sha256": raw["source_sha256"],
            "chapter_model_sha": "model-sha",
            "layer_model_sha": {
                "panels": "model-sha",
                "skim": "model-sha",
                "standard": "model-sha",
                "deep": "model-sha",
            },
            "layer_usage": {"deep": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}},
            "layer_updated_at": {"deep": "2026-11-09T00:00:00"},
            "updated_at": "2026-11-09T00:00:00",
        },
    }
    return files, blocks, ledger, model, deep, raw


def test_depth_status_ready_layers_usage_and_estimates(depth_root):
    files, *_ = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    # 旧包没记层级参数，fresh() 拿整包参数做代理；不写旧模型名就等于「参数一致」，会被当成可复用。
    files["state.json"]["model"] = {"model": "旧模型", "effort": "high"}
    # 结构模型默认判定本章「不适合编译速览」，这里要验的是用量记账，绕开那条判定。
    files["chapter_model.json"]["skim"] = {"supported": True}
    service.store.save_depth("B0003", 6, files)
    print("DEBUG saved state model", service.store.depth_json("B0003", 6, "state.json").get("model"))
    status = service.depth_status("B0003", 6)
    assert status["state"] == "ready"
    assert set(status["layers"]) == set(DEPTH_LAYERS)
    assert set(_layer_statuses(status).values()) == {"ready"}
    deep = status["layers"]["deep"]
    assert deep["usage"]["total_tokens"] == 1500
    assert deep["tokens"] == 1500
    assert deep["usd"] == 0.002
    assert deep["updated_at"] == "2026-11-09T00:00:00"
    model_estimate = status["layers"]["model"]["estimate"]
    assert model_estimate["tokens"] > 0
    assert status["layers"]["deep"]["estimate"]["tokens"] == round(model_estimate["tokens"] * 0.6)
    assert status["layers"]["standard"]["estimate"] == {"tokens": 0, "usd": 0.0}


def test_depth_status_source_change_marks_present_layers_stale(depth_root):
    files, *_ = _ready_depth(depth_root)
    files["state.json"]["source_sha256"] = "outdated"
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    status = service.depth_status("B0003", 6)
    assert status["state"] == "stale"
    assert status["layers"]["model"]["status"] == "stale"
    assert status["layers"]["skim"]["status"] == "stale"


def test_depth_status_running_and_failed_layers(depth_root):
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    failed = {
        "id": "r-failed",
        "uid": "B0003",
        "n": "06",
        "kind": "depth",
        "layers": ["deep"],
        "state": "failed",
        "error": "模型超时",
        "updated": "2026-11-09T01:00:00",
    }
    active = {
        "id": "r-active",
        "uid": "B0003",
        "n": "06",
        "kind": "depth",
        "layers": ["skim"],
        "state": "annotating",
        "updated": "2026-11-09T02:00:00",
    }
    service.queue = _FakeQueue(jobs=[failed], active=[active])
    status = service.depth_status("B0003", 6)
    assert status["layers"]["skim"]["status"] == "running"
    assert status["layers"]["deep"]["status"] == "failed"
    assert status["layers"]["deep"]["error"] == "模型超时"
    assert [job["id"] for job in status["jobs"]] == ["r-failed"]


def test_depth_status_model_hash_change_stales_old_layers(depth_root):
    files, *_ = _ready_depth(depth_root)
    files["state.json"]["chapter_model_sha"] = "new-model-sha"
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    status = service.depth_status("B0003", 6)
    assert status["state"] == "stale"
    assert status["layers"]["model"]["status"] == "ready"
    for layer in ("skim", "standard", "deep"):
        assert status["layers"][layer]["status"] == "stale"


def test_submit_depth_reports_active_conflict(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    service.queue = _FakeQueue(conflicts=[{"id": "r-running"}])
    with pytest.raises(JobConflictError):
        service.submit_depth("B0001", 1, ["skim"])


def test_depth_job_only_model_writes_no_html_and_keeps_editorial(depth_root):
    _, blocks = _valid()
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    editorial = {"uid": "B0003", "n": 6, "beats": [], "visuals": [], "takeaways": []}
    service.store.save_editorial("B0003", 6, editorial)
    service.client = FakeClient([_chunk_payload(blocks)])
    result = service._run_depth_job(
        "r-depth-model",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["model"],
            "model": "fake-model",
            "effort": "low",
            "rebuild": True,
        },
    )
    assert result["status"] == "model"
    assert result["pack_id"] == "B0003/ch/06"
    assert service.store.depth_json("B0003", 6, "chapter_model.json") is not None
    assert service.store.depth_json("B0003", 6, "layers/skim.json") is None
    assert not Path(service.store.depth_path("B0003", 6, "chapter.html")).exists()
    assert service.store.editorial("B0003", 6) == editorial
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["status"] == "model"
    assert state["chapter_model_sha"] == state["layer_sha"]["model"]
    assert "skim" not in state["layer_sha"]


def test_depth_job_only_skim_preserves_deep_and_editorial(depth_root):
    files, _blocks, _ledger, _model, deep, _raw = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    editorial = {"uid": "B0003", "n": 6, "beats": [], "visuals": [], "takeaways": []}
    service.store.save_editorial("B0003", 6, editorial)
    service.client = FakeClient([])
    result = service._run_depth_job(
        "r-depth-skim",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["skim"],
            "model": "fake-model",
            "effort": "low",
            "rebuild": False,
        },
    )
    assert result["status"] == "ready"
    assert service.store.depth_json("B0003", 6, "layers/deep.json") == deep
    assert service.store.editorial("B0003", 6) == editorial
    html = Path(service.store.depth_path("B0003", 6, "chapter.html"))
    assert html.is_file()
    assert html.read_text(encoding="utf-8") != "<html>old depth</html>"
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["layer_model_sha"]["skim"] == state["chapter_model_sha"]
    assert "deep" in state["layer_sha"]


def test_depth_job_audit_failure_is_quality_kind(depth_root, monkeypatch):
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    files.pop("layers/skim.json")
    files["chapter_model.json"]["skim"] = {"supported": True}
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    _blocks, _model, payload = _long_skim_fixture()
    service.client = FakeClient([payload])

    def fake_audit(*_args, **_kwargs):
        return {"errors": ["模拟审计失败"], "stats": {"body_han": 1522, "section_gaps": []}}

    monkeypatch.setattr(service_module, "audit_skim", fake_audit)
    with pytest.raises(SkimError) as caught:
        service._run_depth_job(
            "r-depth-audit",
            {
                "uid": "B0003",
                "n": "06",
                "kind": "depth",
                "layers": ["skim"],
                "model": "fake-model",
                "effort": "low",
                "rebuild": False,
            },
        )
    assert caught.value.kind == "quality"
    assert caught.value.stats["body_han"] == 1522
    assert caught.value.payload["schema"] == 1
    assert "模拟审计失败" in str(caught.value)


def test_depth_job_model_rebuild_stales_old_layers(depth_root):
    files, blocks, *_ = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    editorial = {"uid": "B0003", "n": 6, "beats": [], "visuals": [], "takeaways": []}
    service.store.save_editorial("B0003", 6, editorial)
    service.client = FakeClient([_chunk_payload(blocks)])
    result = service._run_depth_job(
        "r-depth-rebuild",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["model"],
            "model": "fake-model",
            "effort": "low",
            "rebuild": True,
        },
    )
    assert result["status"] == "model"
    # 重建结构不再删页面：路由已经不认它，删掉只是让回滚少一份东西。
    assert Path(service.store.depth_path("B0003", 6, "chapter.html")).is_file()
    assert service.store.chapter_route("B0003", 6)[0] == "none"
    status = service.depth_status("B0003", 6)
    assert status["layers"]["model"]["status"] == "ready"
    for layer in ("skim", "standard", "deep"):
        assert status["layers"][layer]["status"] == "stale"
    assert service.store.editorial("B0003", 6) == editorial
    result = service._run_depth_job(
        "r-depth-standard",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["standard"],
            "model": "fake-model",
            "effort": "low",
        },
    )
    # 结构 + 标准两层的现状是 partial：旧代码把所有非成页状态都报成 model，P2 起细分。
    assert result["status"] == "partial"
    # 页面留在盘上但路由不再指向它：重建过结构，旧页已经不配这个模型。
    assert Path(service.store.depth_path("B0003", 6, "chapter.html")).is_file()
    assert service.store.chapter_route("B0003", 6)[0] == "none"


def test_depth_job_standard_only_rerenders_ready_page(depth_root):
    files, *_ = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True, client=FakeClient([]))
    service.store.save_depth("B0003", 6, files)
    html_path = Path(service.store.depth_path("B0003", 6, "chapter.html"))
    html_path.write_text("old", encoding="utf-8")
    result = service._run_depth_job(
        "r-depth-standard-only",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["standard"],
            "model": "fake-model",
            "effort": "low",
        },
    )
    assert result["status"] == "ready"
    assert html_path.read_text(encoding="utf-8") != "old"
    assert service.depth_status("B0003", 6)["layers"]["standard"]["status"] == "ready"


class _DropClient:
    """对端在返回状态行前关连接：每次调用都抛分类过的传输错误。"""

    def __init__(self, error):
        self.error = error
        self.calls = []

    def complete_json(self, messages, *, model=None, effort=None):
        self.calls.append(copy.deepcopy(messages))
        raise self.error


def _all_layers_job():
    return {
        "uid": "B0003",
        "n": "06",
        "kind": "depth",
        "layers": list(DEPTH_LAYERS),
        "model": "fake-model",
        "effort": "low",
    }


def _partial_after_drop(depth_root):
    """跑到深读层时断连，盘上只剩前四层：P2 续跑的真实现场。"""
    files, blocks, _ledger, model, _deep, _raw = _ready_depth(depth_root)
    files.pop("layers/deep.json")
    files.pop("chapter.html")
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    service.client = _DropClient(
        ModelProviderError(
            "模型调用失败（对端在返回状态行前关闭了连接，阶段 read）：transport_closed",
            kind="transport_closed",
            retryable=True,
        )
    )
    with pytest.raises(DeepError):
        service._run_depth_job("r-depth-drop", _all_layers_job())
    return service, blocks, model


def test_depth_job_interrupted_leaves_partial_pack(depth_root):
    service, _blocks, _model = _partial_after_drop(depth_root)
    assert len(service.client.calls) == 2  # 服务层重试一次，第二次仍断连才失败
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["status"] == "partial"
    assert state["schema"] == 2
    assert service.store.depth_json("B0003", 6, "layers/deep.json") is None
    assert not Path(service.store.depth_path("B0003", 6, "chapter.html")).exists()
    info = service.store.depth_state("B0003", 6)
    assert info["status"] == "partial"
    assert info["layers_ready"] == ["model", "panels", "skim", "standard"]
    assert info["layers_missing"] == ["deep"]
    route, reason = service.store.chapter_route("B0003", 6)
    assert route == "none"
    assert "4/5" in reason
    assert "深读" in reason


def test_depth_job_resume_only_runs_the_missing_layer(depth_root):
    service, blocks, model = _partial_after_drop(depth_root)
    units = [unit["id"] for unit in model["units"]][:3]
    client = FakeClient([_deep_payload(blocks, units)] * 3)
    service.client = client
    result = service._run_depth_job("r-depth-resume", _all_layers_job())
    assert len(client.calls) == 1  # 前四层一次调用都不发
    assert result["skipped"] == ["model", "panels", "skim", "standard"]
    assert result["status"] == "ready"
    assert Path(service.store.depth_path("B0003", 6, "chapter.html")).is_file()
    info = service.store.depth_state("B0003", 6)
    assert info["status"] == "ready"
    assert info["layers_missing"] == []


def test_chunk_cache_survives_interruption(depth_root, tmp_path, monkeypatch):
    """第一块落盘后中断，重跑时第一块不再发给模型。"""
    from bookcorpus.reading.depth import model as depth_model

    _files, blocks, *_ = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0003", 6)
    chapter = {"uid": "B0003", "n": 6, "source_sha256": raw["source_sha256"]}
    book = service.library.book("B0003")
    plans = [
        {"index": 1, "total": 2, "blocks": blocks, "prev_title": None, "next_title": "下节", "id_prefix": "C01-"},
        {"index": 2, "total": 2, "blocks": blocks, "prev_title": "上节", "next_title": None, "id_prefix": "C02-"},
    ]
    monkeypatch.setattr(depth_model, "_chunk_plans", lambda blocks, limit: plans)
    cache_dir = tmp_path / "cache"
    payload = _chunk_payload(blocks)
    seen = []

    def _serve_one(messages, *, model=None, effort=None):
        if seen:
            raise ModelProviderError("模型调用失败：transport_closed", kind="transport_closed")
        seen.append(copy.deepcopy(messages))
        return copy.deepcopy(payload), {"total_tokens": 5}

    first = _DropClient(ModelProviderError("flood"))
    first.complete_json = _serve_one
    with pytest.raises(DepthModelError):
        generate_chapter_model(first, book, chapter, blocks, model="fake-model", effort="low", cache_dir=cache_dir)
    cached = sorted((cache_dir / "chunks").glob("*.json"))
    assert len(cached) == 1  # 第一块已落盘，第二块失败
    assert cached[0].name.endswith("-01.json")

    second = _DropClient(ModelProviderError("模型调用失败：transport_closed", kind="transport_closed"))
    with pytest.raises(DepthModelError):
        generate_chapter_model(second, book, chapter, blocks, model="fake-model", effort="low", cache_dir=cache_dir)
    assert len(second.calls) == 2  # 只重发第二块，失败后自己重试一次
    chunk_one = seen[0][1]["content"]
    assert all(chunk_one not in call[1]["content"] for call in second.calls)


def test_chunk_cache_key_covers_chunk_size_and_output_cap(depth_root):
    from bookcorpus.reading.depth.model import cache_key

    base = cache_key("src", "prompt", "m", "low", 18000, 32768)
    assert base == cache_key("src", "prompt", "m", "low", 18000, 32768)
    assert base != cache_key("src", "prompt", "m", "low", 9000, 32768)
    assert base != cache_key("src", "prompt", "m", "low", 18000, 8000)
    assert base != cache_key("src", "prompt", "m", "high", 18000, 32768)

    _files, blocks, *_ = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0003", 6)
    chapter = {"uid": "B0003", "n": 6, "source_sha256": raw["source_sha256"]}
    book = service.library.book("B0003")
    cache_dir = depth_root / "cache-key"
    payload = _chunk_payload(blocks)
    client = FakeClient([copy.deepcopy(payload), copy.deepcopy(payload)])
    _model, meta = generate_chapter_model(
        client, book, chapter, blocks, model="fake-model", effort="low", cache_dir=cache_dir
    )
    assert meta["cache_hit"] is False
    _model, meta = generate_chapter_model(
        client, book, chapter, blocks, model="fake-model", effort="low", chunk_chars=9000, cache_dir=cache_dir
    )
    assert meta["cache_hit"] is False  # 分块大小变了就不能当命中
    assert len(client.calls) == 2
    _model, meta = generate_chapter_model(
        client, book, chapter, blocks, model="fake-model", effort="low", cache_dir=cache_dir
    )
    assert meta["cache_hit"] is True
    assert len(client.calls) == 2


def test_reading_service_depth_default_on(reading_root, monkeypatch):
    monkeypatch.delenv("READING_DEPTH", raising=False)
    service = ReadingService(root=reading_root, annotate=False)
    assert service.depth is True
    assert "depth" in service.chapter("B0001", 1)
    monkeypatch.setenv("READING_DEPTH", "0")
    assert ReadingService(root=reading_root, annotate=False).depth is False
    assert ReadingService(root=reading_root, annotate=False, depth=False).depth is False


def test_cli_state_is_model_not_ready_and_records_cache_key():
    _, state_text = _state_text(
        {"prompt_version": "chapter-model-v1"},
        {"source_sha256": "sha"},
        {"cache_key": "cache-1"},
        "B0003",
        6,
    )
    state = json.loads(state_text)
    assert state["status"] == "model"
    assert state["cache_key"] == "cache-1"
    assert "layers_sha" not in state


def test_cli_validate_dispose_report_with_gold(depth_root, tmp_path, capsys):
    raw = ReadingService(root=depth_root, annotate=False, depth=False).library.read_chapter("B0003", 6)
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps(_gold(raw["source_sha256"]), ensure_ascii=False), encoding="utf-8")
    base = ["--root", str(depth_root), "--uid", "B0003", "--n", "6", "--file", str(gold)]

    assert depth_main(["validate", *base]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    assert (
        depth_main(
            [
                "validate",
                "--root",
                str(depth_root),
                "--uid",
                "B0003",
                "--n",
                "6",
                "--file",
                str(GOLD),
                "--gold",
                str(GOLD),
            ]
        )
        == 0
    )
    validated_gold = json.loads(capsys.readouterr().out)
    assert validated_gold["core_total"] == 1
    assert validated_gold["core_present"] == 1

    assert depth_main(["dispose", *base]) == 0
    disposed = json.loads(capsys.readouterr().out)
    assert disposed["gate"]["status"] == "pass"
    assert disposed["ledger"]["tiers"]["skim"]["omitted_duplicates"] == 1

    assert depth_main(["report", *base, "--gold", str(gold)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tiers"]["skim"]["units"] == 4
    assert report["core_total"] == 1
    assert report["core_covered"] == 1
    assert report["core_coverage"] == 1.0
    assert report["tiers"]["skim"]["hard_info_covered"] == 2
    assert report["tiers"]["skim"]["unmapped_blocks"] == 0
    assert report["tiers"]["skim"]["omitted_duplicates"] == 1
    assert report["gate"] == "pass"


def test_cli_report_gold_fails_when_core_unit_deleted(depth_root, tmp_path, capsys):
    raw = ReadingService(root=depth_root, annotate=False, depth=False).library.read_chapter("B0003", 6)
    gold = _gold(raw["source_sha256"])
    trimmed = copy.deepcopy(gold)
    trimmed["units"] = [unit for unit in trimmed["units"] if unit["id"] != "U01"]
    trimmed["sections"][0]["unit_ids"] = [unit_id for unit_id in trimmed["sections"][0]["unit_ids"] if unit_id != "U01"]
    for unit in trimmed["units"]:
        unit["relations"] = [relation for relation in unit["relations"] if relation.get("to") != "U01"]
    gold_file = tmp_path / "gold-deleted.json"
    gold_file.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    model_file = tmp_path / "model-deleted.json"
    model_file.write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")

    code = depth_main(
        [
            "report",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--file",
            str(model_file),
            "--gold",
            str(gold_file),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["gate"] == "fail"
    assert report["core_total"] == 1
    assert report["core_covered"] == 0
    assert report["core_coverage"] == 0.0
    assert report["tiers"]["skim"]["unmapped_blocks"] >= 1
    blocks, sha = _parsed()
    model = _valid(sha)[0]
    ledger = plan_all_dispositions(model, blocks)
    for tier in ("skim", "standard", "deep"):
        ledger["tiers"][tier]["units"]["U01"]["disposition"] = "omitted"
    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    store = Store(Layout(depth_root))
    store.save_depth(
        "B0003",
        6,
        {
            "chapter_model.json": json.dumps(model, ensure_ascii=False),
            "ledger.json": json.dumps(ledger, ensure_ascii=False),
            "state.json": json.dumps({"status": "model", "source_sha256": sha}, ensure_ascii=False),
        },
    )
    code = depth_main(["report", "--root", str(depth_root), "--uid", "B0003", "--n", "6", "--file", str(ledger_file)])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["gate"] == "fail"
    assert report["core_covered"] == 0


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []
        self.model = "fake-model"
        self.effort = "low"

    def complete_json(self, messages, *, model=None, effort=None):
        self.calls.append(copy.deepcopy(messages))
        return self.payloads.pop(0), {"total_tokens": 10}

    def prompt_sha256(self):
        return "fake-prompt"


def _chunk_payload(blocks, *, relation=False):
    units = []
    for block in blocks:
        if block["kind"] == "heading":
            continue
        unit_id = f"U{len(units) + 1:02d}"
        units.append(
            {
                "id": unit_id,
                "kind": "claim",
                "source_blocks": [block["id"]],
                "quotes": [{"block": block["id"], "text": block["text"]}],
                "summary": block["text"],
                "importance": "support",
                "hard_info": False,
                "relations": ([{"type": "supports", "to": "U01"}] if relation and unit_id == "U02" else []),
            }
        )
    headings = [block for block in blocks if block["kind"] == "heading"]
    title = headings[0]["text"] if headings else "本节"
    section = {
        "id": "S01",
        "title": title,
        "unit_ids": [unit["id"] for unit in units],
        "lead": "本节提要。",
    }
    non_semantic = [{"block": block["id"], "reason": "节标题"} for block in headings]
    figures = []
    if units:
        figures.append(
            {
                "id": "F01",
                "family": "flow",
                "question": "顺序",
                "units": [units[0]["id"]],
                "origin": "derived",
                "caption": "流程",
                "reading_note": "按序阅读。",
                "data": {},
                "fallback": "读正文。",
            }
        )
    return {
        "schema": 1,
        "units": units,
        "sections": [section],
        "terms": [],
        "figures": figures,
        "non_semantic": non_semantic,
        "folds": [],
    }


def test_generate_small_multi_h2_chapter_uses_one_call():
    blocks = [
        {"id": "B01", "kind": "heading", "text": "第一节", "level": 2},
        {"id": "B02", "kind": "paragraph", "text": "第一节正文。"},
        {"id": "B03", "kind": "heading", "text": "第二节", "level": 2},
        {"id": "B04", "kind": "paragraph", "text": "第二节正文。"},
    ]
    client = FakeClient([_chunk_payload(blocks)])
    model, meta = generate_chapter_model(
        client,
        {"uid": "B0003", "title": "测试书"},
        {"uid": "B0003", "n": 6, "title": "第六章", "source_sha256": "sha"},
        blocks,
        model="fake-model",
        effort="low",
    )
    assert meta["chunks"] == 1
    assert len(client.calls) == 1
    payload = json.loads(client.calls[0][1]["content"])
    assert payload["chunk"]["prev_section_title"] is None
    assert payload["chunk"]["next_section_title"] is None
    assert payload["chunk"]["id_prefix"] == ""
    assert len(model["sections"]) == 1
    assert model["uid"] == "B0003"


def test_generate_large_chapter_splits_into_multiple_calls():
    blocks = []
    for section in range(3):
        blocks.append({"id": f"B{section * 2 + 1:02d}", "kind": "heading", "text": f"第{section + 1}节", "level": 2})
        blocks.append({"id": f"B{section * 2 + 2:02d}", "kind": "paragraph", "text": "长" * 7000})
    client = FakeClient([_chunk_payload([blocks[index], blocks[index + 1]]) for index in range(0, len(blocks), 2)])
    model, meta = generate_chapter_model(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6, "title": "第六章", "source_sha256": "sha"},
        blocks,
        model="fake-model",
        effort="low",
    )
    assert meta["chunks"] == 3
    assert len(client.calls) == 3
    payloads = [json.loads(call[1]["content"]) for call in client.calls]
    assert [item["chunk"]["prev_section_title"] for item in payloads] == [None, "第1节", "第2节"]
    assert [item["chunk"]["next_section_title"] for item in payloads] == ["第2节", "第3节", None]
    assert [item["chunk"]["id_prefix"] for item in payloads] == ["C01-", "C02-", "C03-"]
    assert [section["id"] for section in model["sections"]] == ["S01", "S02", "S03"]
    assert model["units"][0]["id"] == "S01-U01"


def test_generate_retries_only_once_after_repeated_invalid_json(tmp_path, monkeypatch):
    class BadClient:
        def __init__(self):
            self.calls = 0
            self.model = "bad"
            self.effort = "low"

        def complete_json(self, messages, *, model=None, effort=None):
            self.calls += 1
            raise json.JSONDecodeError("bad", "", 0)

    blocks, _ = _parsed()
    client = BadClient()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DepthModelError):
        generate_chapter_model(client, {}, {"n": 6}, blocks, model="bad", effort="low")
    assert client.calls == 2
    assert list(tmp_path.iterdir()) == []


def test_generate_feeds_validation_error_back_and_retries():
    blocks, _ = _parsed()
    bad = copy.deepcopy(_chunk_payload(blocks))
    bad["units"][0]["quotes"][0]["text"] = "伪造引文"
    good = _chunk_payload(blocks)
    client = FakeClient([bad, good])
    model, meta = generate_chapter_model(
        client,
        {"uid": "B0003", "title": "测试书"},
        {"uid": "B0003", "n": 6, "title": "第六章", "source_sha256": "sha"},
        blocks,
        model="fake-model",
        effort="low",
    )
    assert len(client.calls) == 2
    assert any("未通过结构校验" in message["content"] for message in client.calls[1])
    assert meta["chunks"] == 1
    assert model["units"][0]["id"] == "S01-U01"


def test_generate_cache_hit_avoids_second_model_call(tmp_path):
    blocks, sha = _parsed()
    chapter = {"uid": "B0003", "n": 6, "title": "第六章", "source_sha256": sha}
    cache_dir = tmp_path / "cache"
    first = FakeClient([_chunk_payload(blocks)])
    first_model, first_meta = generate_chapter_model(
        first,
        {"uid": "B0003"},
        chapter,
        blocks,
        model="fake-model",
        effort="low",
        cache_dir=cache_dir,
    )
    assert first_meta["cache_key"]
    assert list(cache_dir.glob("*.json"))
    second = FakeClient([])
    second_model, second_meta = generate_chapter_model(
        second,
        {"uid": "B0003"},
        chapter,
        blocks,
        model="fake-model",
        effort="low",
        cache_dir=cache_dir,
    )
    assert second.calls == []
    assert second_meta["cache_hit"] is True
    assert second_model == first_model


def test_generate_cache_refresh_forces_new_model_call(tmp_path):
    blocks, sha = _parsed()
    chapter = {"uid": "B0003", "n": 6, "title": "第六章", "source_sha256": sha}
    cache_dir = tmp_path / "cache"
    first = FakeClient([_chunk_payload(blocks)])
    generate_chapter_model(
        first,
        {"uid": "B0003"},
        chapter,
        blocks,
        model="fake-model",
        effort="low",
        cache_dir=cache_dir,
    )
    refreshed = FakeClient([_chunk_payload(blocks)])
    _model, meta = generate_chapter_model(
        refreshed,
        {"uid": "B0003"},
        chapter,
        blocks,
        model="fake-model",
        effort="low",
        cache_dir=cache_dir,
        refresh=True,
    )
    assert len(refreshed.calls) == 1
    assert meta["cache_hit"] is False
    assert meta["refreshed"] is True
    # 强制重建后把新结果写回缓存：下一次普通调用依旧命中，不会重复花钱。
    again = FakeClient([])
    _again_model, again_meta = generate_chapter_model(
        again,
        {"uid": "B0003"},
        chapter,
        blocks,
        model="fake-model",
        effort="low",
        cache_dir=cache_dir,
    )
    assert again.calls == []
    assert again_meta["cache_hit"] is True


def test_skim_validate_and_audit_counts():
    blocks, model, payload = _long_skim_fixture()
    skim = validate_skim(payload, model, blocks)
    report = audit_skim(skim, model, blocks)
    assert report["errors"] == []
    assert report["gates"]["G11"]["status"] == "pass"
    assert report["gates"]["G14"]["status"] == "pass"
    assert report["stats"]["sections"] == 1
    assert report["stats"]["claims"] == 4
    assert report["stats"]["detail_count"] == 0
    assert report["stats"]["core_total"] == 1
    assert report["stats"]["core_covered"] == 1
    assert report["stats"]["core_claim_coverage"] == 1.0
    assert report["stats"]["units_in_claims"] == 4
    assert report["stats"]["units_covered"] == 4
    assert report["stats"]["ratio_ok"] is True


def test_skim_unsupported_requires_reason_and_empty_body():
    with pytest.raises(SkimError, match="reason"):
        validate_skim(unsupported_skim(""), {"sections": [], "units": []}, [])
    payload = unsupported_skim("太短")
    payload["sections"] = [{"id": "S01", "lead": "多余", "claims": []}]
    with pytest.raises(SkimError, match="sections"):
        validate_skim(payload, {"sections": [], "units": []}, [])


def test_skim_rejects_missing_core_and_bad_anchor():
    blocks, model, payload = _long_skim_fixture()
    missing_core = copy.deepcopy(payload)
    claim_units = [["U02"], ["U03"], ["U04"], ["U02", "U03"]]
    for claim, units in zip(missing_core["sections"][0]["claims"], claim_units, strict=True):
        claim["units"] = units
    with pytest.raises(SkimError, match="core"):
        validate_skim(missing_core, model, blocks)

    bad_anchor = copy.deepcopy(payload)
    bad_anchor["sections"][0]["claims"][0]["anchor"] = "B99"
    with pytest.raises(SkimError, match="anchor"):
        validate_skim(bad_anchor, model, blocks)


def test_schema_keeps_optional_skim_hint_and_rejects_bad_reason():
    blocks, sha = _parsed()
    payload = _gold(sha)
    payload["skim"] = {"supported": False, "reason": "纯叙事章，没有可陈述的命题链。"}
    model = validate_chapter_model(payload, blocks, uid="B0003", n=6, source_sha256=sha)
    assert model["skim"] == {"supported": False, "reason": "纯叙事章，没有可陈述的命题链。"}

    payload["skim"] = {"supported": False, "reason": ""}
    with pytest.raises(ChapterModelError, match="reason"):
        validate_chapter_model(payload, blocks, uid="B0003", n=6, source_sha256=sha)


def test_skim_rejects_uncovered_unit_and_number_not_in_source():
    blocks, model, payload = _long_skim_fixture()
    uncovered = copy.deepcopy(payload)
    uncovered["sections"][0]["claims"][2]["units"] = ["U02"]
    uncovered["sections"][0]["claims"][3]["units"] = ["U01"]
    with pytest.raises(SkimError, match="既不在 claim 也不在 detail_index"):
        validate_skim(uncovered, model, blocks)

    number = copy.deepcopy(payload)
    number["sections"][0]["claims"][0]["text"] += " 12345"
    with pytest.raises(SkimError, match="数字不在源文"):
        validate_skim(number, model, blocks)


def test_skim_rejects_banned_phrase_ratio_and_forged_quote():
    blocks, model, payload = _long_skim_fixture()
    banned = copy.deepcopy(payload)
    banned["sections"][0]["claims"][0]["text"] += "值得注意的是"
    with pytest.raises(SkimError, match="导读腔"):
        validate_skim(banned, model, blocks)

    thin = copy.deepcopy(payload)
    for claim in thin["sections"][0]["claims"]:
        claim["text"] = "短"
    with pytest.raises(SkimError, match="15%"):
        validate_skim(thin, model, blocks)

    forged = copy.deepcopy(payload)
    forged["detail_index"] = [{"label": "细节", "unit": "U01", "anchor": "B02", "quote": "原文没有这句"}]
    with pytest.raises(SkimError, match="quote"):
        validate_skim(forged, model, blocks)


def test_generate_skim_short_chapter_is_unsupported_without_model_call():
    model, blocks = _valid()
    client = FakeClient([])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
    )
    assert client.calls == []
    assert skim["supported"] is False
    assert skim["reason"]
    assert skim["sections"] == []
    assert skim["detail_index"] == []


def test_generate_skim_retries_once_after_validation_error():
    blocks, model, payload = _long_skim_fixture()
    bad = copy.deepcopy(payload)
    bad["sections"][0]["claims"] = bad["sections"][0]["claims"][:1]
    client = FakeClient([bad, payload])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
    )
    assert len(client.calls) == 2
    assert skim["supported"] is True


def _ratio_fixture(source_han, body_han):
    """造一个只差正文长度的速览：结构合法，比例由 body_han 决定。"""
    blocks = [{"id": "B02", "kind": "paragraph", "text": "源" * source_han}]
    model = {
        "schema": 1,
        "uid": "B0003",
        "n": 6,
        "sections": [{"id": "S01", "title": "第一节", "unit_ids": ["U01", "U02", "U03", "U04"]}],
        "units": [
            {"id": "U01", "importance": "core", "source_blocks": ["B02"]},
            {"id": "U02", "importance": "support", "source_blocks": ["B02"]},
            {"id": "U03", "importance": "support", "source_blocks": ["B02"]},
            {"id": "U04", "importance": "support", "source_blocks": ["B02"]},
        ],
        "core_units": ["U01"],
    }
    payload = {
        "schema": 1,
        "prompt_version": SKIM_PROMPT_VERSION,
        "supported": True,
        "reason": "",
        "sections": [
            {
                "id": "S01",
                "lead": "本节",
                "claims": [
                    {"text": "甲" * (body_han - 5), "units": ["U01", "U02"], "anchor": "B02"},
                    {"text": "乙", "units": ["U03"], "anchor": "B02"},
                    {"text": "丙", "units": ["U04"], "anchor": "B02"},
                    {"text": "丁", "units": ["U01"], "anchor": "B02"},
                ],
                "turn": "",
            }
        ],
        "detail_index": [],
    }
    return blocks, model, payload


def test_skim_prompt_budget_matches_source_han():
    blocks, model, payload = _long_skim_fixture()
    client = FakeClient([payload])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
    )
    assert skim["supported"] is True
    content = json.loads(client.calls[0][-1]["content"])
    assert content["budget"] == skim_module._budget(blocks)
    assert content["budget"]["source_han"] == skim_module.source_han(blocks) == 1203
    assert content["budget"]["body_min"] == 181
    assert content["budget"]["body_max"] == 300
    assert "181–300" in content["reminder"]
    assert "1203" in content["reminder"]


@pytest.mark.parametrize(
    ("body_han", "ok", "match"),
    [
        (1522, False, "15%"),
        (1523, True, None),
        (2537, True, None),
        (2538, False, "25%"),
    ],
)
def test_skim_ratio_boundaries(body_han, ok, match):
    blocks, model, payload = _ratio_fixture(10150, body_han)
    if ok:
        assert validate_skim(payload, model, blocks)["supported"] is True
    else:
        with pytest.raises(SkimError, match=match):
            validate_skim(payload, model, blocks)


def test_skim_narrative_budget_has_only_upper_bound():
    blocks, model, _payload = _long_skim_fixture()
    messages = skim_module._scene_messages({"uid": "B0003"}, {"uid": "B0003", "n": 6}, model, blocks)
    content = json.loads(messages[-1]["content"])
    assert "body_min" not in content["budget"]
    assert content["budget"]["body_max"] == 300
    assert "不超过源文 300 汉字" in content["reminder"]
    assert "须在" not in content["reminder"]


def _scenes_payload():
    return {
        "schema": 1,
        "prompt_version": SKIM_PROMPT_VERSION,
        "supported": True,
        "reason": "",
        "scenes": [
            {"text": "先看前提，再看结论。", "units": ["U01"], "anchor": "B02"},
            {"text": "正例成立，反例反证。", "units": ["U02"], "anchor": "B02"},
            {"text": "代价是边界被压缩。", "units": ["U03"], "anchor": "B02"},
        ],
    }


def test_skim_narrative_still_retries_after_structure_error():
    """场景档没有比例下限，但结构错误仍该拿到一次返修机会。"""
    blocks, model, _payload = _long_skim_fixture()
    client = FakeClient([{}, _scenes_payload()])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
        mode="narrative",
    )
    assert skim["supported"] is True
    assert len(client.calls) == 2
    retry = client.calls[1][-1]["content"]
    assert "上一次输出未通过速览校验" in retry
    assert not any(message["role"] == "assistant" for message in client.calls[1])


def test_skim_structure_error_does_not_echo_payload():
    """只有比例越界才回灌上一份输出；导读腔这类结构错误只回错误文本。"""
    blocks, model, payload = _ratio_fixture(1203, 200)
    payload["sections"][0]["claims"][0]["text"] = "作者认为" + payload["sections"][0]["claims"][0]["text"]
    client = FakeClient([payload, copy.deepcopy(payload)])
    with pytest.raises(SkimError, match="导读腔"):
        generate_skim(
            client,
            {"uid": "B0003"},
            {"uid": "B0003", "n": 6},
            model,
            blocks,
            model="fake-model",
            effort="low",
        )
    assert len(client.calls) == 2
    assert not any(message["role"] == "assistant" for message in client.calls[1])
    assert "导读腔" in client.calls[1][-1]["content"]


def test_skim_oversize_repair_drops_detail_index_instead_of_faking_it():
    """超长回灌只能删掉 detail_index，不能塞一个占位对象让模型照抄。"""
    payload = _scenes_payload()
    payload["detail_index"] = [
        {"label": f"D{i}", "unit": "U01", "anchor": "B02", "quote": "源" * 2000} for i in range(40)
    ]
    text, chars, dropped, still_too_long = skim_module._repair_payload_text(payload)
    assert dropped is True
    assert still_too_long is False
    assert chars > skim_module.REPAIR_PAYLOAD_LIMIT
    assert text is not None
    assert "detail_index" not in json.loads(text)
    gap = skim_module._gap_message({"source_han": 1203, "body_han": 100}, "正文汉字比例 8.3% 越界", detail_dropped=True)
    assert "detail_index 因过长已省略" in gap


def test_skim_repair_payload_reports_dropped_even_when_still_too_long():
    """省掉 detail_index 仍然放不下，也要如实上报「省过」，不能说自己没动。"""
    payload = _scenes_payload()
    payload["detail_index"] = [{"label": f"D{i}", "quote": "源" * 500} for i in range(5)]
    payload["scenes"][0]["text"] = "源" * 70000
    text, chars, dropped, still_too_long = skim_module._repair_payload_text(payload)
    assert text is None
    assert chars > skim_module.REPAIR_PAYLOAD_LIMIT
    assert dropped is True
    assert still_too_long is True


def test_skim_gap_message_asks_to_cut_when_body_is_over_max():
    """正文超上限时再叫它「扩写」，这次返修必然白费。"""
    stats = {"source_han": 10150, "body_han": 2600, "section_gaps": []}
    gap = skim_module._gap_message(stats, "正文汉字 2600 字越界")
    assert "超出 63 汉字" in gap
    assert "删减" in gap
    assert "总缺口" not in gap
    assert "只扩写" not in gap


def test_skim_gap_message_gives_per_section_targets():
    stats = {
        "source_han": 10150,
        "body_han": 1400,
        "section_gaps": [{"id": "S01", "body_han": 200, "units": 3, "source_han": 4000}],
    }
    gap = skim_module._gap_message(stats, "正文汉字 1400 字越界")
    assert "总缺口 123 汉字" in gap
    assert "- S01：当前正文 200 汉字（偏少）/ 目标 600–1000 汉字 / 3 个 unit / 源文 4000 汉字" in gap


def test_skim_min_ratio_change_updates_prompt_and_error(monkeypatch):
    monkeypatch.setattr(skim_module, "MIN_RATIO", 0.20)
    blocks, model, _payload = _long_skim_fixture()
    content = json.loads(
        skim_module._messages({"uid": "B0003"}, {"uid": "B0003", "n": 6}, model, blocks)[-1]["content"]
    )
    assert content["budget"]["source_han"] == 1203
    assert content["budget"]["body_min"] == math.ceil(1203 * 0.20) == 241
    assert "241–300" in content["reminder"]

    blocks, model, thin = _ratio_fixture(10150, 1522)
    with pytest.raises(SkimError, match="20%"):
        validate_skim(thin, model, blocks)


def test_generate_skim_repairs_once_with_previous_payload_and_gap():
    blocks, model, thin = _ratio_fixture(10150, 1522)
    _blocks, _model, good = _ratio_fixture(10150, 1523)
    client = FakeClient([thin, good])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
    )
    assert skim["supported"] is True
    assert len(client.calls) == 2
    repair = client.calls[1]
    assert repair[-2]["role"] == "assistant"
    assert json.loads(repair[-2]["content"])["sections"][0]["claims"][0]["text"] == "甲" * 1517
    gap = repair[-1]["content"]
    assert "正文汉字 1522 字（15.0%）越界（15%–25%）：下限 1523 字、上限 2537 字" in gap
    assert "目标 1523–2537 汉字" in gap
    assert "总缺口 1 汉字" in gap
    assert "- S01：当前正文 1522 汉字（偏少）/ 目标 1523–2537 汉字 / 4 个 unit / 源文 10150 汉字" in gap


def test_json_retry_and_quality_repair_use_separate_budgets():
    blocks, model, thin = _ratio_fixture(10150, 1522)
    _blocks, _model, good = _ratio_fixture(10150, 1523)
    client = FakeClient(["不是 JSON", thin, good])
    skim = generate_skim(
        client,
        {"uid": "B0003"},
        {"uid": "B0003", "n": 6},
        model,
        blocks,
        model="fake-model",
        effort="low",
    )
    assert skim["supported"] is True
    assert len(client.calls) == 3
    assert client.calls[1][-1]["role"] == "user"
    assert "不是合法 JSON" in client.calls[1][-1]["content"]
    assert client.calls[2][-2]["role"] == "assistant"
    assert "总缺口 1 汉字" in client.calls[2][-1]["content"]

    thin_client = FakeClient([thin, thin])
    with pytest.raises(SkimError, match="连续 2 次未通过速览校验"):
        generate_skim(
            thin_client,
            {"uid": "B0003"},
            {"uid": "B0003", "n": 6},
            model,
            blocks,
            model="fake-model",
            effort="low",
        )
    assert len(thin_client.calls) == 2

    json_client = FakeClient(["坏", "还是坏"])
    with pytest.raises(SkimError, match="连续 2 次没有得到合法 JSON"):
        generate_skim(
            json_client,
            {"uid": "B0003"},
            {"uid": "B0003", "n": 6},
            model,
            blocks,
            model="fake-model",
            effort="low",
        )
    assert len(json_client.calls) == 2


def test_generate_skim_omits_oversized_repair_payload_and_emits_event(tmp_path):
    blocks, model, thin = _ratio_fixture(10150, 1522)
    _blocks, _model, good = _ratio_fixture(10150, 1523)
    oversized = copy.deepcopy(thin)
    oversized["padding"] = "x" * 61000
    log = reading_events.EventLog(tmp_path, "repair-overlimit")
    token = reading_events.bind(log)
    try:
        client = FakeClient([oversized, good])
        skim = generate_skim(
            client,
            {"uid": "B0003"},
            {"uid": "B0003", "n": 6},
            model,
            blocks,
            model="fake-model",
            effort="low",
        )
    finally:
        reading_events.unbind(token)
        log.close()
    assert skim["supported"] is True
    assert len(client.calls) == 2
    assert client.calls[1][-1]["role"] == "user"
    assert "总缺口 1 汉字" in client.calls[1][-1]["content"]
    records = reading_events.read(tmp_path, "repair-overlimit")
    omitted = [record for record in records if record.get("kind") == "skim_repair_payload_omitted"]
    assert omitted
    assert omitted[0]["payload_chars"] > 60000


def test_skim_error_carries_quality_context_without_leaking_payload():
    exc = SkimError("失败", stats={"body_han": 1}, payload={"raw": "x"})
    assert exc.kind == "quality"
    assert exc.stats == {"body_han": 1}
    assert exc.payload == {"raw": "x"}
    assert str(exc) == "失败"
    assert exc.args == ("失败",)
    assert "x" not in str(exc)
    assert getattr(exc, "__notes__", None) is None


def test_existing_v2_skim_layer_still_validates(tmp_path):
    blocks, model, payload = _long_skim_fixture()
    path = tmp_path / "skim.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["prompt_version"] == "skim-compiler-v2"
    assert validate_skim(loaded, model, blocks)["prompt_version"] == "skim-compiler-v2"


def test_render_depth_chapter_keeps_all_source_blocks_in_order():
    blocks, model, payload = _long_skim_fixture()
    blocks = [
        *blocks,
        {"id": "B03", "kind": "quote", "text": "一句引文"},
        {"id": "B04", "kind": "list", "text": "列表内容"},
        {"id": "B05", "kind": "table", "text": "", "head": ["列"], "rows": [["值"]]},
    ]
    skim = validate_skim(payload, model, blocks)
    markup = render_depth_chapter(
        {"uid": "B0003", "title": "书名", "author": "作者"},
        {"n": 6, "title": "第6章"},
        blocks,
        model,
        {},
        skim,
    )
    assert markup.index('data-body="compiled"') < markup.index('data-body="source"')
    positions = [markup.index(f'data-block="{block["id"]}"') for block in blocks]
    assert positions == sorted(positions)
    assert 'href="#B02"' in markup
    assert 'data-skim-anchor="B02"' in markup
    assert "编译版 · 核心命题 1/1 · 细节 0 条" in markup
    for tab in ("速览", "标准", "深读"):
        assert tab in markup
    assert "深读注解将在后续生成" in markup
    assert "@media print" in markup


def test_render_depth_chapter_unsupported_has_no_claims_but_keeps_source():
    blocks, model, _payload = _long_skim_fixture()
    markup = render_depth_chapter(
        {"uid": "B0003", "title": "书名", "author": "作者"},
        {"n": 6, "title": "第6章"},
        blocks,
        model,
        {},
        unsupported_skim("这一章太短，适合读原文。"),
    )
    assert "本章更适合按原文阅读" in markup
    assert "这一章太短，适合读原文。" in markup
    assert "读标准档" in markup
    assert 'class="skim-claims"' not in markup
    for block in blocks:
        assert f'data-block="{block["id"]}"' in markup


def test_cli_generate_status_ready_with_skim_and_render(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
        ]
    )
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["skim_supported"] is False
    assert output["html"] is True
    store = Store(Layout(depth_root))
    assert store.depth_state("B0003", 6)["status"] == "ready"
    state = json.loads((Path(store.pack_dir("B0003", 6)) / "depth" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "ready"
    assert state["cache_key"] == output["cache_key"]
    assert state["skim_supported"] is False
    assert Path(store.depth_cache_dir("B0003", 6), f"{output['cache_key']}.json").exists()
    skim = store.depth_json("B0003", 6, "layers/skim.json")
    assert skim["supported"] is False
    assert skim["reason"]
    html = Path(store.depth_path("B0003", 6, "chapter.html")).read_text(encoding="utf-8")
    assert "本章更适合按原文阅读" in html
    assert 'data-depth-tiers="deep"' in html
    assert output["deep_notes"] == 2
    assert output["deep_chains"] == 1
    assert output["deep_audit"] == "pass"
    deep = store.depth_json("B0003", 6, "layers/deep.json")
    assert deep["prompt_version"] == DEEP_PROMPT_VERSION
    assert state["deep_sha"]
    assert len(state["deep_sha"]) == 64
    assert state["deep_prompt_version"] == DEEP_PROMPT_VERSION
    assert (
        depth_main(
            [
                "report",
                "--root",
                str(depth_root),
                "--uid",
                "B0003",
                "--n",
                "6",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["skim"]["supported"] is False
    assert report["skim_detail_count"] == 0
    assert report["skim"]["gates"]["G11"]["status"] == "pass"


def test_cli_generate_no_skim_keeps_model_state(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
            "--no-skim",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "model"
    store = Store(Layout(depth_root))
    assert store.depth_state("B0003", 6)["status"] == "model"
    assert store.depth_json("B0003", 6, "layers/skim.json") is None
    assert not Path(store.depth_path("B0003", 6, "chapter.html")).exists()


def test_cli_generate_skip_render_is_not_ready(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
            "--skip-render",
        ]
    )
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "model"
    assert output["html"] is False
    store = Store(Layout(depth_root))
    assert store.depth_state("B0003", 6)["status"] == "model"
    assert not Path(store.depth_path("B0003", 6, "chapter.html")).exists()


def test_depth_state_ready_without_html_is_model(tmp_path):
    store = Store(Layout(tmp_path))
    store.save_depth(
        "B0001",
        1,
        {
            "state.json": {"status": "ready", "source_sha256": "sha"},
        },
    )
    assert store.depth_state("B0001", 1, "sha")["status"] == "model"


def test_cli_state_ready_records_skim_layers_sha():
    blocks, model, payload = _long_skim_fixture()
    skim = validate_skim(payload, model, blocks)
    _, state_text = _state_text(
        model,
        {"source_sha256": "sha"},
        {},
        "B0003",
        6,
        status="ready",
        skim=skim,
    )
    state = json.loads(state_text)
    assert state["status"] == "ready"
    assert state["skim_prompt_version"] == SKIM_PROMPT_VERSION
    assert state["skim_supported"] is True
    assert len(state["layers_sha"]) == 64


def test_prompt_has_shape_guidance():
    for phrase in (
        "叙事章节按场景与转折分节",
        "setup→turn→consequence",
        "表格是一个 block",
        "每个有意义的行/列组",
        "不要丢行",
        "装饰图",
        "reason 写 decorative",
        "source_blocks 同时包含图片块和图注块",
        "不得声称图或图注里没有的数字",
        "宁可少而连贯",
    ):
        assert phrase in SYSTEM_PROMPT


def test_narrative_gold_validates_without_h2_sections():
    model, blocks, ledger, tiers = _load_gold_fixture("b0001_ch04_narrative", "B0001", 4)
    assert [section["title"] for section in model["sections"]] == ["舂米夜", "跪下", "挨打"]
    assert not [block for block in blocks if block["kind"] == "heading"]
    assert model["core_units"] == ["U03", "U06"]
    assert _tier_map(ledger) == tiers
    report = audit_conservation(model, ledger, blocks, expected_core=model["core_units"])
    assert report["status"] == "pass"
    assert report["core_coverage"] == 1.0


def test_table_gold_preserves_all_rows():
    model, blocks, ledger, tiers = _load_gold_fixture("b0004_ch04_table", "B0004", 4)
    assert [section["title"] for section in model["sections"]] == ["多数人影响下的决定", "培养勇气"]
    for unit_id in ("U02", "U07", "U08", "U13"):
        assert _unit(model, unit_id)["hard_info"] is True
    for unit_id in ("U03", "U04", "U05", "U06", "U09", "U10", "U11", "U12"):
        assert _unit(model, unit_id)["importance"] == "core"
        assert _unit(model, unit_id)["hard_info"] is False
        assert _tier_map(ledger)["skim"][unit_id] == "condensed"

    row_group_ids = {"U03", "U04", "U05", "U06", "U08", "U09", "U10", "U11", "U12"}
    assert row_group_ids <= {unit["id"] for unit in model["units"]}
    assert len(row_group_ids) == 9

    table_units = [unit for unit in model["units"] if set(unit["source_blocks"]) & {"B02", "B05"}]
    assert len(table_units) == 10
    cells = [
        "结果证明你是对的",
        "结果证明你是错的",
        "你的意见和多数人相左",
        "没有人会记得是你做出的决定，但公司受益。",
        "所有人都记得你做的这个决定，你会因此而被降级，被冷落，甚至被解聘。",
        "你的意见和多数人一致",
        "所有提出过建议的人都会记得这个决定，公司受益。",
        "你因此承受的指责会降到最低，但公司受损失。",
        "普通人的借口",
        "CEO的借口",
        "智者千虑，必有一失。",
        "这是生死攸关的决定。",
        "我所有的朋友都想这样做。",
        "大家都不赞同我的做法，我不能违背民意。",
        "那些厉害的人都是这样做的。",
        "业内的行规就是这样，我不知道它违法。",
        "它不够完美，所以我放弃竞争。",
        "我们从未生产出符合市场预期的产品，因此我就没指望它们能卖得出去。",
    ]
    for cell in cells:
        assert any(cell in quote["text"] for unit in table_units for quote in unit["quotes"]), cell
    assert model["non_semantic"] == [{"block": "B04", "reason": "小节标题"}]
    assert _tier_map(ledger) == tiers
    report = audit_conservation(model, ledger, blocks, expected_core=model["core_units"])
    assert report["status"] == "pass"


def test_image_gold_separates_semantic_and_decorative():
    model, blocks, ledger, tiers = _load_gold_fixture("b0004_ch01_image", "B0004", 1)
    assert [section["title"] for section in model["sections"]] == [
        "从0到1与从1到n",
        "两种进步",
        "科技与全球化",
    ]
    first = _unit(model, "U02")
    assert first["source_blocks"] == ["B03", "B04"]
    assert first["quotes"] == [{"block": "B04", "text": "图1–1两种进步"}]
    assert first["importance"] == "support"
    assert _unit(model, "U01")["importance"] == "core"
    second = _unit(model, "U03")
    assert second["source_blocks"] == ["B05", "B06"]
    assert second["quotes"] == [{"block": "B06", "text": "图1–2科技与全球化"}]

    figures = {figure["id"]: figure for figure in model["figures"]}
    assert figures["F01"]["origin"] == "source"
    assert figures["F01"]["units"] == ["U02"]
    assert figures["F01"]["caption"] == "图1–1两种进步"
    assert figures["F02"]["origin"] == "source"
    assert figures["F02"]["units"] == ["U03"]
    assert figures["F02"]["caption"] == "图1–2科技与全球化"
    assert model["non_semantic"] == [{"block": "B01", "reason": "decorative"}]
    assert not [unit for unit in model["units"] if "B01" in unit["source_blocks"]]
    assert _tier_map(ledger) == tiers
    report = audit_conservation(model, ledger, blocks, expected_core=model["core_units"])
    assert report["status"] == "pass"


@pytest.mark.parametrize(
    ("stem", "uid", "n"),
    [
        ("b0001_ch04_narrative", "B0001", 4),
        ("b0004_ch04_table", "B0004", 4),
        ("b0004_ch01_image", "B0004", 1),
    ],
)
def test_shaped_gold_payload_generates_through_model_path(stem, uid, n):
    raw = (FIXTURES / f"{stem}.md").read_bytes()
    blocks = parse_markdown(raw.decode("utf-8"), stem=stem)["blocks"]
    gold = json.loads((FIXTURES / f"{stem}_gold.json").read_text(encoding="utf-8"))
    payload = {key: value for key, value in gold.items() if key not in {"tiers", "core_units"}}
    client = FakeClient([payload])
    model, meta = generate_chapter_model(
        client,
        {"uid": uid},
        {"uid": uid, "n": n, "source_sha256": hashlib.sha256(raw).hexdigest()},
        blocks,
        model="fake-model",
        effort="low",
    )
    assert meta["chunks"] == 1

    section_global = {section["id"]: f"S{index + 1:02d}" for index, section in enumerate(gold["sections"])}
    counters = {}
    core_ids = []
    for unit in gold["units"]:
        section = unit["section"]
        counters[section] = counters.get(section, 0) + 1
        if unit["id"] in gold["core_units"]:
            core_ids.append(f"{section_global[section]}-U{counters[section]:02d}")
    ledger = plan_all_dispositions(model, blocks)
    report = audit_conservation(model, ledger, blocks, expected_core=core_ids)
    assert report["status"] == "pass", (stem, report["problems"])


def test_existing_argumentative_gold_still_passes():
    blocks, sha = _parsed()
    fixture = _gold_fixture(sha)
    tiers = fixture.pop("tiers")
    model = validate_chapter_model(fixture, blocks, uid="B0003", n=6, source_sha256=sha)
    ledger = plan_all_dispositions(model, blocks)
    assert len(model["sections"]) == 1
    assert model["core_units"] == ["U01"]
    assert _tier_map(ledger) == tiers
    report = audit_conservation(model, ledger, blocks, expected_core=model["core_units"])
    assert report["status"] == "pass"


def test_gold_core_coverage_uses_expected_core():
    cases = [
        ("b0003_ch06", "B0003", 6),
        ("b0001_ch04_narrative", "B0001", 4),
        ("b0004_ch04_table", "B0004", 4),
        ("b0004_ch01_image", "B0004", 1),
    ]
    for stem, uid, n in cases:
        model, blocks, ledger, _tiers = _load_gold_fixture(stem, uid, n)
        report = audit_conservation(model, ledger, blocks, expected_core=model["core_units"])
        assert report["status"] == "pass", (stem, report["problems"])
        assert report["core_coverage"] == 1.0, stem
        assert report["hard_info_coverage"] == 1.0, stem
        assert report["limitation"] == "", stem


def _standard_model():
    blocks = [
        {"id": "B01", "kind": "heading", "text": "第一节", "level": 2},
        {"id": "B02", "kind": "paragraph", "text": "复利是把利息计入本金。"},
        {"id": "B03", "kind": "heading", "text": "第二节", "level": 2},
        {"id": "B04", "kind": "list", "text": "清单", "items": [f"条目{i}" for i in range(1, 11)]},
    ]
    model = {
        "sections": [
            {"id": "S01", "title": "第一节", "lead": "先看定义。", "unit_ids": ["U01"]},
            {"id": "S02", "title": "第二节", "lead": "再看清单。", "unit_ids": ["U02"]},
        ],
        "units": [
            {"id": "U01", "source_blocks": ["B02"]},
            {"id": "U02", "source_blocks": ["B04"]},
        ],
        "terms": [],
        "figures": [],
        "folds": [],
    }
    return blocks, model


def test_standard_build_adds_chapter_map_and_auto_fold():
    blocks, model = _standard_model()
    model["standard_notes"] = [{"block": "B02", "text": "复利在原文中的背景。", "origin": "source"}]
    standard = build_standard(model, blocks)
    assert standard["notes"] == [
        {"block": "B02", "text": "复利在原文中的背景。", "origin": "source", "tag": "background"}
    ]
    assert standard["prompt_version"] == STANDARD_PROMPT_VERSION
    assert [section["lead"] for section in standard["sections"]] == ["先看定义。", "再看清单。"]
    assert len(standard["figures"]) == 1
    chapter = standard["figures"][0]
    assert chapter["id"] == "F00"
    assert chapter["family"] == "tree"
    assert chapter["origin"] == "derived"
    assert [node["id"] for node in chapter["data"]["nodes"]] == ["S01", "S02"]
    assert chapter["data"]["edges"] == [{"from": "S01", "to": "S02"}]
    assert len(standard["folds"]) == 1
    assert "10" in standard["folds"][0]["summary"]
    assert standard["audit"]["status"] == "pass"


def test_standard_terms_deduped_and_first_block():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "复利是利息滚入本金。"},
        {"id": "B02", "kind": "paragraph", "text": "复利之下，边际递减。"},
    ]
    model = {
        "sections": [{"id": "S01", "title": "第一节", "unit_ids": ["U01"]}],
        "units": [{"id": "U01", "source_blocks": ["B01", "B02"]}],
        "terms": [
            {"term": "复利", "definition": "利息滚入本金。", "source_block": "B02", "quote": "复利"},
            {"term": "复利", "definition": "重复计息。", "source_block": "B01", "quote": "复利"},
            {"term": "边际", "definition": "新增一单位带来的变化。", "source_block": "B02", "quote": "边际"},
        ],
        "figures": [],
        "folds": [],
    }
    standard = build_standard(model, blocks)
    assert [term["term"] for term in standard["terms"]] == ["复利", "边际"]
    assert standard["terms"][0]["source_block"] == "B01"


def test_standard_rejects_bad_lead_note_and_fold_kind():
    blocks, model = _standard_model()
    model["sections"][0]["lead"] = "长" * 81
    model["standard_notes"] = [
        {"block": "B02", "text": "注" * 121, "origin": "editor"},
        {"block": "B99", "text": "出处不存在", "origin": "source"},
    ]
    model["folds"] = [{"block": "B02", "summary": "段落不折", "reason": "测试"}]
    standard = build_standard(model, blocks)
    assert [section["id"] for section in standard["sections"]] == ["S01", "S02"]
    assert len(standard["sections"][0]["lead"]) == 80
    assert standard["sections"][1] == {"id": "S02", "lead": "再看清单。"}
    assert standard["notes"] == []
    assert standard["folds"] == [{"block": "B04", "summary": "完整清单 · 10 条", "reason": "长清单折叠"}]
    assert standard["audit"]["warnings"]
    assert standard["audit"]["status"] == "warn"


def test_render_standard_includes_lead_term_fold_figure():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "复利是把利息计入本金。"},
        {"id": "B02", "kind": "list", "text": "清单", "items": [f"条目{i}" for i in range(1, 11)]},
    ]
    model = {
        "sections": [{"id": "S01", "title": "第一节", "lead": "先看定义，再看清单。", "unit_ids": ["U01", "U02"]}],
        "units": [
            {"id": "U01", "source_blocks": ["B01"]},
            {"id": "U02", "source_blocks": ["B02"]},
        ],
        "terms": [{"term": "复利", "definition": "利息滚入本金。", "source_block": "B01", "quote": "复利"}],
        "figures": [],
        "folds": [],
    }
    standard = build_standard(model, blocks)
    markup = render_depth_chapter(
        {"uid": "B0003", "title": "书名", "author": "作者"},
        {"n": 6, "title": "第6章"},
        blocks,
        model,
        {},
        unsupported_skim("短章。"),
        standard=standard,
    )
    for block in blocks:
        assert f'data-block="{block["id"]}"' in markup
    assert '<details class="fold" data-block="B02" open>' in markup
    assert "完整清单 · 10 条" in markup
    assert markup.count('<aside data-origin="added" class="term-note" data-term="复利">') == 1
    assert "先看定义，再看清单。" in markup
    assert "本章结构" in markup
    assert 'data-origin="added"' in markup
    assert 'body.only-source [data-origin="added"]{display:none!important}' in markup
    assert "item.open = true" in markup


def test_audit_figures_rejects_number_not_in_source():
    blocks = [{"id": "B01", "kind": "paragraph", "text": "全年增长。"}]
    model = {"units": [{"id": "U01", "source_blocks": ["B01"]}]}
    figure = {
        "id": "F01",
        "family": "quantity",
        "question": "增长多少",
        "units": ["U01"],
        "origin": "derived",
        "caption": "增长 42%",
        "reading_note": "看条形。",
        "data": {"items": [{"label": "全年", "value": 42}]},
        "fallback": "",
    }
    report = audit_figures([figure], model, blocks)
    assert report["status"] == "fail"
    assert any("42" in error for error in report["errors"])
    blocks[0]["text"] = "全年增长 42%。"
    report = audit_figures([figure], model, blocks)
    assert report["status"] == "pass"
    figure["caption"] = "增长 43%"
    report = audit_figures([figure], model, blocks)
    assert report["status"] == "fail"


def test_standard_drops_external_figure():
    blocks = [{"id": "B01", "kind": "paragraph", "text": "原文。"}]
    model = {
        "sections": [{"id": "S01", "title": "第一节", "lead": "看原文。", "unit_ids": ["U01"]}],
        "units": [{"id": "U01", "source_blocks": ["B01"]}],
        "terms": [],
        "figures": [
            {
                "id": "F01",
                "family": "network",
                "question": "外部背景",
                "units": ["U01"],
                "origin": "external",
                "caption": "书外图",
                "reading_note": "看节点。",
                "data": {"nodes": [{"id": "N1", "label": "外部", "block": "B01"}]},
                "fallback": "",
            }
        ],
        "folds": [],
    }
    standard = build_standard(model, blocks)
    assert [figure["id"] for figure in standard["figures"]] == ["F00"]
    assert any("F01" in warning and "书外" in warning for warning in standard["audit"]["warnings"])


def test_standard_drops_figure_with_number_not_in_source():
    blocks = [
        {"id": "B01", "kind": "paragraph", "text": "全年增长。"},
        {"id": "B02", "kind": "paragraph", "text": "利润增加。"},
    ]
    model = {
        "sections": [
            {"id": "S01", "title": "第一节", "lead": "看增长。", "unit_ids": ["U01"]},
            {"id": "S02", "title": "第二节", "lead": "看利润。", "unit_ids": ["U02"]},
        ],
        "units": [
            {"id": "U01", "source_blocks": ["B01"]},
            {"id": "U02", "source_blocks": ["B02"]},
        ],
        "terms": [],
        "figures": [
            {
                "id": "F01",
                "family": "quantity",
                "question": "增长多少",
                "units": ["U01"],
                "origin": "derived",
                "caption": "增长 42%",
                "reading_note": "看条形。",
                "data": {"items": [{"label": "全年", "value": 42}]},
                "fallback": "",
            }
        ],
        "folds": [],
    }
    standard = build_standard(model, blocks)
    assert [figure["id"] for figure in standard["figures"]] == ["F00"]
    assert standard["audit"]["errors"] == []
    assert any("F01" in warning for warning in standard["audit"]["warnings"])


def test_cli_state_ready_includes_standard_layer(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
        ]
    )
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["standard_notes"] == 0
    assert output["figures"] >= 1
    assert output["g12"] in ("pass", "warn")
    store = Store(Layout(depth_root))
    layer = store.depth_json("B0003", 6, "layers/standard.json")
    assert layer["prompt_version"] == STANDARD_PROMPT_VERSION
    assert layer["figures"]
    state = json.loads((Path(store.pack_dir("B0003", 6)) / "depth" / "state.json").read_text(encoding="utf-8"))
    assert len(state["standard_sha"]) == 64
    assert state["standard_prompt_version"] == STANDARD_PROMPT_VERSION
    html = Path(store.depth_path("B0003", 6, "chapter.html")).read_text(encoding="utf-8")
    assert "本章结构" in html
    assert 'data-origin="added"' in html


def test_cli_skip_render_writes_standard_layer(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
            "--skip-render",
        ]
    )
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "model"
    store = Store(Layout(depth_root))
    assert store.depth_state("B0003", 6)["status"] == "model"
    assert store.depth_json("B0003", 6, "layers/standard.json")["prompt_version"] == STANDARD_PROMPT_VERSION
    assert not Path(store.depth_path("B0003", 6, "chapter.html")).exists()


def test_cli_no_standard_skips_layer_but_page_annotated(depth_root, monkeypatch, capsys):
    blocks, _ = _parsed()
    fake = FakeClient([_chunk_payload(blocks), _deep_payload(blocks)])
    monkeypatch.setattr(depth_cli, "ModelClient", lambda: fake)
    code = depth_main(
        [
            "generate",
            "--root",
            str(depth_root),
            "--uid",
            "B0003",
            "--n",
            "6",
            "--model",
            "fake-model",
            "--effort",
            "low",
            "--no-standard",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    store = Store(Layout(depth_root))
    assert store.depth_json("B0003", 6, "layers/standard.json") is None
    state = json.loads((Path(store.pack_dir("B0003", 6)) / "depth" / "state.json").read_text(encoding="utf-8"))
    assert state["standard_prompt_version"] == STANDARD_PROMPT_VERSION
    html = Path(store.depth_path("B0003", 6, "chapter.html")).read_text(encoding="utf-8")
    assert "本章结构" in html


def _deep_ready():
    model, blocks = _valid()
    units = [unit["id"] for unit in model["units"]][:3]
    return model, blocks, _deep_payload(blocks, units)


def _write_legacy_manifest(root, uid, n, source_sha):
    pack = Path(root) / "工作区" / "阅读" / uid / "ch" / f"{n:02d}"
    pack.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "uid": uid,
        "n": n,
        "tiers": {},
        "draft": True,
        "source_sha256": source_sha,
    }
    (pack / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pack / "chapter.html").write_text(
        "<!doctype html><html><head><style>body{font-size:16px;line-height:1.7}</style></head>"
        "<body><p>旧路径</p></body></html>",
        encoding="utf-8",
    )
    return pack


def test_chapter_payload_path_depth_legacy_none(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    raw = service.library.read_chapter("B0001", 1)
    _write_legacy_manifest(reading_root, "B0001", 1, raw["source_sha256"])

    legacy = service.chapter("B0001", 1)
    assert legacy["path"] == "legacy"
    assert legacy["fallback_reason"] == "旧包按旧路径打开"

    missing = service.chapter("B0001", 2)
    assert missing["path"] == "none"
    assert missing["fallback_reason"] == "尚未生成"

    raw2 = service.library.read_chapter("B0002", 1)
    service.store.save_depth(
        "B0002",
        1,
        {
            "chapter_model.json": {"schema": 1},
            "ledger.json": {"schema": 1},
            "chapter.html": "<!doctype html><html><body>深读</body></html>",
            "state.json": {"status": "ready", "source_sha256": raw2["source_sha256"], "path": "depth"},
        },
    )
    depth = service.chapter("B0002", 1)
    assert depth["path"] == "depth"
    assert depth["fallback_reason"] is None
    assert depth["chapter"]["path"] == "depth"


def test_audit_cli_passes_depth_fixture(depth_root, capsys):
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    files, blocks, ledger, model, _deep, _raw = _ready_depth(depth_root)
    markup = render_depth_chapter(
        service.library.book("B0003"),
        service.library.chapter("B0003", 6),
        blocks,
        model,
        ledger,
        files["layers/skim.json"],
    )
    files["chapter.html"] = markup
    files["state.json"]["path"] = "depth"
    service.store.save_depth("B0003", 6, files)
    for uid in ("B0001", "B0002"):
        shutil.rmtree(depth_root / "books" / uid)

    assert depth_audit.main(["--root", str(depth_root), "--limit", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sampled"] == 1
    assert report["counts"] == {"depth": 1, "legacy": 0, "missing": 0}
    assert report["pass"] is True
    item = report["chapters"][0]
    assert item["path"] == "depth"
    assert item["ready"] is True
    assert [gate["id"] for gate in item["gates"]] == ["G10", "G1", "G5", "G8"]
    assert all(gate["status"] == "pass" for gate in item["gates"])


def test_audit_cli_classifies_and_fails_missing(reading_root, capsys):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    raw = service.library.read_chapter("B0001", 1)
    _write_legacy_manifest(reading_root, "B0001", 1, raw["source_sha256"])

    assert depth_audit.main(["--root", str(reading_root), "--limit", "2"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["sampled"] == 2
    assert report["counts"] == {"depth": 0, "legacy": 1, "missing": 1}
    assert report["pass"] is False

    legacy, missing = report["chapters"]
    assert legacy["path"] == "legacy"
    assert legacy["fallback_reason"] == "旧包按旧路径打开"
    assert [gate["id"] for gate in legacy["gates"]] == ["G1", "G5", "G8"]
    assert missing["path"] == "missing"
    assert missing["ready"] is False
    assert missing["fallback_reason"] == "尚未生成"
    assert missing["gates"] == []


def test_merge_usage_sums_nested_provider_cost():
    from bookcorpus.reading.model import merge_usage

    merged = merge_usage(
        {"prompt_tokens": 1, "opencode": {"list_cost": 0.5, "billing_source": "go"}},
        {"prompt_tokens": 2, "opencode": {"list_cost": 0.25, "allowance": 1}},
    )
    assert merged == {
        "prompt_tokens": 3,
        "opencode": {"list_cost": 0.75, "billing_source": "go", "allowance": 1},
    }


def test_usage_usd_prefers_provider_list_cost():
    assert depth_cli._usage_usd({"opencode": {"list_cost": 0.125}}) == 0.125
    assert depth_cli._usage_usd({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) == 3.0


def test_model_client_max_tokens_env(monkeypatch):
    from bookcorpus.reading.model import ModelClient

    monkeypatch.setenv("READING_MODEL_MAX_TOKENS", "65536")
    assert ModelClient(key="k").max_tokens == 65536
    monkeypatch.delenv("READING_MODEL_MAX_TOKENS")
    assert ModelClient(key="k").max_tokens == 32768


def test_book_directory_reflects_depth_layers(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0001", 1)
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "ledger.json": {"label": "ledger"},
            "layers/skim.json": {},
            "layers/standard.json": {},
            "layers/deep.json": {},
            "chapter.html": "<html>depth</html>",
            "state.json": {"status": "ready", "source_sha256": raw["source_sha256"]},
        },
    )
    chapter = service.book("B0001")["chapters"][0]
    assert chapter["path"] == "depth"
    assert {key: chapter["tiers"][key]["exists"] for key in ("skim", "standard", "deep")} == {
        "skim": True,
        "standard": True,
        "deep": True,
    }
    assert chapter["pack"]["exists"] is True
    assert service.book("B0001")["book"]["packs"] == 1
    detail = service.chapter("B0001", 1)
    assert {tier["key"]: tier["exists"] for tier in detail["tiers"]} == {
        "skim": True,
        "standard": True,
        "deep": True,
    }


def test_depth_job_keeps_page_when_a_redo_fails(depth_root):
    """F1：快照要预载 chapter.html，重做失败不能把已发布的页面删掉。"""
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    # 让盘上的包看起来是用另一个模型建的：这样点名重做 deep 才会真跑。
    raw_state = files["state.json"]
    state = json.loads(raw_state) if isinstance(raw_state, str) else dict(raw_state)
    state["model"] = {"model": "old-model", "effort": "low"}
    files["state.json"] = state
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    html_path = Path(service.store.depth_path("B0003", 6, "chapter.html"))
    assert html_path.read_text(encoding="utf-8") == "<html>old depth</html>"
    service.client = _DropClient(
        ModelProviderError(
            "模型调用失败（对端在返回状态行前关闭了连接，阶段 read）：transport_closed",
            kind="transport_closed",
            retryable=True,
        )
    )

    with pytest.raises(DeepError):
        service._run_depth_job("r-depth-keep-html", dict(_all_layers_job(), layers=["deep"]))

    assert html_path.is_file()
    assert html_path.read_text(encoding="utf-8") == "<html>old depth</html>"
    assert service.store.depth_state("B0003", 6)["status"] == "ready"


def test_depth_job_reruns_layer_when_model_changes(depth_root):
    """F2：换模型点名重做，层参数对不上就必须真跑，不能当成就绪跳过。"""
    service, blocks, model = _partial_after_drop(depth_root)
    units = [unit["id"] for unit in model["units"]][:3]
    payload = _deep_payload(blocks, units)
    service.client = FakeClient([copy.deepcopy(payload)] * 3)
    first = service._run_depth_job("r-depth-model-a", dict(_all_layers_job(), layers=["deep"]))
    assert first["status"] == "ready"
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["layer_request"]["deep"]["model"] == "fake-model"

    service.client = FakeClient([copy.deepcopy(payload)] * 3)
    result = service._run_depth_job("r-depth-model-b", dict(_all_layers_job(), layers=["deep"], model="另一个模型"))

    assert len(service.client.calls) == 1
    assert result["status"] == "ready"
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["layer_request"]["deep"]["model"] == "另一个模型"


def test_depth_job_all_skipped_keeps_ready_pack(depth_root):
    """F3：全部命中续跑时不能把 ready 降级成 partial，页面也不该消失。"""
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)

    result = service._run_depth_job("r-depth-skip-all", dict(_all_layers_job(), layers=["model"]))

    assert result["skipped"] == ["model"]
    assert result["status"] == "ready"
    assert Path(service.store.depth_path("B0003", 6, "chapter.html")).is_file()


def test_failed_save_leaves_no_stage_dir(depth_root, monkeypatch):
    """F5：落盘失败要么保留旧包，要么什么都不留，不能留半截目录。"""
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    base = Path(service.store.pack_dir("B0003", 6))

    def boom(root, payload):
        raise OSError("磁盘已满")

    monkeypatch.setattr(Store, "_write_depth_files", staticmethod(boom))
    with pytest.raises(OSError):
        service.store.save_depth("B0003", 6, files)

    assert not [path for path in base.iterdir() if path.name.startswith(".depth-")]
    assert service.store.depth_state("B0003", 6)["status"] == "ready"


def test_chunk_cache_rejects_payload_from_another_plan(tmp_path):
    """F6：切分变了而整章键没变，也要靠块签名认出旧块不合用。"""
    from bookcorpus.reading.depth import model as depth_model

    key = "k" * 8
    path = depth_model.chunk_cache_path(tmp_path, key, 0)
    depth_model._write_chunk_cache(path, key, 0, {"units": []}, {}, "plan-a")

    assert depth_model._read_chunk_cache(path, key, 0, "plan-b") is None
    assert depth_model._read_chunk_cache(path, key, 0, "plan-a") is not None
    assert depth_model._read_chunk_cache(path, key, 1, "plan-a") is None


def test_chapter_payload_keeps_model_for_partial_pack(depth_root):
    """F8：partial 包也要带上结构模型与账，不然前端连"缺哪层"都没得看。"""
    service, _blocks, _model = _partial_after_drop(depth_root)

    data = service.chapter("B0003", 6)

    assert data["depth"]["state"] == "partial"
    assert data["depth"]["model"]["uid"] == "B0003"
    assert data["depth"]["ledger"]


class _PanelDownClient:
    """面板模型连续失败：服务端要退化成空面板，但账上必须留痕。"""

    def __init__(self):
        self.calls = 0
        self.model = "fake-model"
        self.effort = "low"

    def complete_json(self, messages, model=None, effort=None):
        del messages, model, effort
        self.calls += 1
        raise ModelProviderError(
            "模型调用失败（对端未回状态行就断开）：transport_closed",
            kind="transport_closed",
            retryable=True,
        )

    def prompt_sha256(self):
        return "fake-prompt"


def test_panel_degradation_is_recorded_in_state_and_events(depth_root):
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    files.pop("layers/panels.json")
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    service.client = _PanelDownClient()
    job = _all_layers_job()
    job["layers"] = ["panels"]
    token = reading_events.bind(reading_events.EventLog(service.queue.log_dir, "r-depth-degraded"))
    try:
        result = service._run_depth_job("r-depth-degraded", job)
    finally:
        reading_events.unbind(token)

    # 空面板落地了，但状态不许报 ready：退化必须看得见。
    assert result["status"] == "partial"
    state = json.loads(Path(service.store.depth_path("B0003", 6, "state.json")).read_text())
    assert state["layer_degraded"]["panels"]["kind"] == "transport_closed"
    log = Path(service.queue.log_dir) / "r-depth-degraded.jsonl"
    kinds = [json.loads(line)["kind"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "layer_degraded" in kinds
    assert service.client.calls >= 2


class _NonJsonClient(_PanelDownClient):
    """面板模型返回的不是 JSON：走 JSON 重试耗尽那条出口，同样必须留痕。"""

    def complete_json(self, messages, model=None, effort=None):
        del messages, model, effort
        self.calls += 1
        return "这不是 JSON 对象", {"total_tokens": 10}


def test_panel_non_json_exhaustion_is_also_recorded(depth_root):
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    files.pop("layers/panels.json")
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    service.client = _NonJsonClient()
    job = _all_layers_job()
    job["layers"] = ["panels"]
    token = reading_events.bind(reading_events.EventLog(service.queue.log_dir, "r-depth-nonjson"))
    try:
        result = service._run_depth_job("r-depth-nonjson", job)
    finally:
        reading_events.unbind(token)

    assert result["status"] == "partial"
    state = json.loads(Path(service.store.depth_path("B0003", 6, "state.json")).read_text())
    assert state["layer_degraded"]["panels"]["kind"] == "protocol"
    log = Path(service.queue.log_dir) / "r-depth-nonjson.jsonl"
    kinds = [json.loads(line)["kind"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "layer_degraded" in kinds


def test_degraded_layer_is_never_skipped_by_resume(depth_root):
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    files.pop("layers/panels.json")
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    service.client = _PanelDownClient()
    job = _all_layers_job()
    job["layers"] = ["panels"]
    service._run_depth_job("r-depth-heal-1", job)

    job2 = _all_layers_job()
    job2["layers"] = ["panels"]
    before = service.client.calls
    result = service._run_depth_job("r-depth-heal-2", job2)

    # 空面板看着“已生成”，但 fresh() 必须否掉它，否则永远补不上。
    assert result["skipped"] == []
    assert service.client.calls > before


def test_layer_timestamps_move_with_each_layer(depth_root, monkeypatch):
    """每层记自己的完成时间：五层共用任务开始时间的话，账本会说不出哪层刚跑完。"""
    _, blocks = _valid()
    ticks = iter([f"2026-11-12T00:00:{index:02d}" for index in range(40)])
    monkeypatch.setattr(service_module, "_now", lambda: next(ticks))
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.client = FakeClient([_chunk_payload(blocks)])
    service._run_depth_job(
        "r-depth-clock",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["model", "standard"],
            "model": "fake-model",
            "effort": "low",
        },
    )
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["layer_updated_at"]["model"] != state["layer_updated_at"]["standard"]
    assert (
        state["updated_at"] == state["layer_updated_at"]["standard"]
        or state["updated_at"] > state["layer_updated_at"]["standard"]
    )
    assert state["generated_at"] < state["updated_at"]


def test_status_moves_with_the_layer_being_written(depth_root):
    """两层落盘后是 partial，不能还报 model：状态按磁盘算会差一层。"""
    _, blocks = _valid()
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.client = FakeClient([_chunk_payload(blocks)])
    result = service._run_depth_job(
        "r-depth-status",
        {
            "uid": "B0003",
            "n": "06",
            "kind": "depth",
            "layers": ["model", "standard"],
            "model": "fake-model",
            "effort": "low",
        },
    )
    assert result["status"] == "partial"
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["status"] == "partial"
    assert service.store.depth_state("B0003", 6)["layers_ready"] == ["model", "standard"]


def test_skim_usage_reaches_the_ledger(depth_root, monkeypatch):
    """速览也是真调用：它的用量要进 layer_usage，否则算钱时少一层。"""
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    skim_payload = copy.deepcopy(files["layers/skim.json"])
    # 短章保护与「本章不适合编译」是另外两件事，这里只验用量记账。
    monkeypatch.setattr(skim_module, "MIN_SOURCE_HAN", 0)
    files["chapter_model.json"]["skim"] = {"supported": True}
    files.pop("layers/skim.json")
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    service.client = FakeClient([skim_payload])
    result = service._run_depth_job(
        "r-depth-skim-usage",
        {"uid": "B0003", "n": "06", "kind": "depth", "layers": ["skim"], "model": "另一个模型", "effort": "low"},
    )
    assert result["skipped"] == []
    assert len(service.client.calls) == 1
    state = service.store.depth_json("B0003", 6, "state.json")
    assert state["layer_usage"]["skim"] == {"total_tokens": 10}


def test_rendered_page_carries_its_own_generation_time(depth_root, monkeypatch):
    """页面上的「生成」时间不能是任务开始时间：九分钟的活，页头会说早了九分钟。"""
    files, _blocks, _ledger, _model, _deep, _raw = _ready_depth(depth_root)
    service = ReadingService(root=depth_root, annotate=False, depth=True)
    service.store.save_depth("B0003", 6, files)
    monkeypatch.setattr(service_module, "_now", lambda: "2026-11-12T09:09:09")
    service.client = FakeClient([])
    service._run_depth_job(
        "r-depth-page-clock",
        {"uid": "B0003", "n": "06", "kind": "depth", "layers": ["standard"], "model": "fake-model", "effort": "low"},
    )
    html = Path(service.store.depth_path("B0003", 6, "chapter.html")).read_text()
    assert "2026-11-12T09:09:09" in html
