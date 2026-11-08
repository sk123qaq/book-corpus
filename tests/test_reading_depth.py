"""Phase 1 深度包：结构模型、守恒账、存储、CLI 与服务接线。"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from bookcorpus.reading.depth import (
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
    validate_skim,
)
from bookcorpus.reading.depth import cli as depth_cli
from bookcorpus.reading.depth.cli import _state_text
from bookcorpus.reading.depth.cli import main as depth_main
from bookcorpus.reading.depth.diagrams import audit_figures
from bookcorpus.reading.depth.disposition import plan_all_dispositions
from bookcorpus.reading.depth.skim import unsupported_skim
from bookcorpus.reading.depth.standard import STANDARD_PROMPT_VERSION, build_standard
from bookcorpus.reading.mdparse import parse_markdown
from bookcorpus.reading.service import ReadingService
from bookcorpus.reading.store import Store
from bookcorpus.scripts.layout import Layout

FIXTURES = Path(__file__).parent / "fixtures" / "depth"
GOLD = FIXTURES / "b0003_ch06_gold.json"
CHAPTER = FIXTURES / "b0003_ch06.md"
PACK_KEYS = {
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
    for tier in ("standard", "deep", "research"):
        account = plan_dispositions(model, tier, blocks)
        assert all(entry["disposition"] == "verbatim" for entry in account["units"].values())
    assert set(plan_all_dispositions(model, blocks)["tiers"]) == {"skim", "standard", "deep", "research"}


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


def test_service_flag_off_has_exactly_the_old_keys(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=False)
    data = service.chapter("B0001", 1)
    assert set(data) == PACK_KEYS
    assert "depth" not in data


def test_service_depth_missing_and_model_pack(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    assert service.chapter("B0001", 1)["depth"] == {"state": "missing"}
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
    assert data["depth"] == {"state": "model", "model": {"label": "model"}, "ledger": {"label": "ledger"}}
    assert set(data) == PACK_KEYS | {"depth"}


def test_service_depth_ready_pack_exposes_skim_and_html(reading_root):
    service = ReadingService(root=reading_root, annotate=False, depth=True)
    raw = service.library.read_chapter("B0001", 1)
    skim = {"schema": 1, "prompt_version": SKIM_PROMPT_VERSION, "supported": False, "reason": "短章"}
    service.store.save_depth(
        "B0001",
        1,
        {
            "chapter_model.json": {"label": "model"},
            "ledger.json": {"label": "ledger"},
            "layers/skim.json": skim,
            "chapter.html": "<html>compiled</html>",
            "state.json": {"status": "ready", "source_sha256": raw["source_sha256"]},
        },
    )
    data = service.chapter("B0001", 1)
    assert data["depth"] == {
        "state": "ready",
        "model": {"label": "model"},
        "ledger": {"label": "ledger"},
        "skim": skim,
        "html": "/reader/B0001/1/depth",
    }


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
    assert service.chapter("B0001", 1)["depth"] == {"state": "stale"}


def test_service_default_depth_is_off(reading_root, monkeypatch):
    monkeypatch.delenv("READING_DEPTH", raising=False)
    service = ReadingService(root=reading_root, annotate=False)
    assert service.depth is False
    assert "depth" not in service.chapter("B0001", 1)


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
    for tier in ("skim", "standard", "deep", "research"):
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
    for tab in ("速览", "标准", "深读", "研究"):
        assert tab in markup
    assert "深读/研究层将在后续生成" in markup
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
    fake = FakeClient([_chunk_payload(blocks)])
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
    fake = FakeClient([_chunk_payload(blocks)])
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
    fake = FakeClient([_chunk_payload(blocks)])
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
    assert standard["sections"] == [{"id": "S02", "lead": "再看清单。"}]
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
    fake = FakeClient([_chunk_payload(blocks)])
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
    fake = FakeClient([_chunk_payload(blocks)])
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
    fake = FakeClient([_chunk_payload(blocks)])
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
