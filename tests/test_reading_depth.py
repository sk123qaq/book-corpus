"""Phase 1 深度包：结构模型、守恒账、存储、CLI 与服务接线。"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from bookcorpus.reading.depth import (
    SYSTEM_PROMPT,
    ChapterModelError,
    DepthModelError,
    audit_conservation,
    generate_chapter_model,
    plan_dispositions,
    validate_chapter_model,
)
from bookcorpus.reading.depth import cli as depth_cli
from bookcorpus.reading.depth.cli import _state_text
from bookcorpus.reading.depth.cli import main as depth_main
from bookcorpus.reading.depth.disposition import plan_all_dispositions
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


def test_cli_generate_status_model_and_cache(depth_root, monkeypatch, capsys):
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
    assert output["status"] == "model"
    store = Store(Layout(depth_root))
    assert store.depth_state("B0003", 6)["status"] == "model"
    state = json.loads((Path(store.pack_dir("B0003", 6)) / "depth" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "model"
    assert state["cache_key"] == output["cache_key"]
    assert Path(store.depth_cache_dir("B0003", 6), f"{output['cache_key']}.json").exists()


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
