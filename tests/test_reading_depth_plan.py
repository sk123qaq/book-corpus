"""分块规划：块数上限、H2 分组与旧行为。"""

from bookcorpus.reading.depth.model import (
    _chunks,
    _read_plan_hint,
    _write_plan_hint,
    chunk_cache_path,
    plan_chunks,
    plan_signature,
)


def _blocks(count, *, text="字", h2_at=()):
    blocks = []
    for index in range(count):
        heading = index in h2_at
        blocks.append(
            {
                "id": f"b{index:04d}",
                "kind": "heading" if heading else "paragraph",
                "level": 2 if heading else 0,
                "text": text,
            }
        )
    return blocks


def _sizes(plans):
    return [len(plan["blocks"]) for plan in plans]


def test_101_blocks_splits_at_the_block_cap():
    plans = plan_chunks(_blocks(101))
    assert _sizes(plans) == [100, 1]
    assert [plan["index"] for plan in plans] == [0, 1]
    assert [plan["total"] for plan in plans] == [2, 2]
    assert plans[0]["chunk_id"] == "0000-0100"
    assert plans[1]["chunk_id"] == "0100-0101"
    assert plans[0]["id_prefix"] == "C0000-0100-"
    assert plans[1]["id_prefix"] == "C0100-0101-"


def test_100_blocks_stays_one_call():
    plans = plan_chunks(_blocks(100))
    assert _sizes(plans) == [100]
    assert plans[0]["id_prefix"] == ""


def test_legacy_planner_ignores_the_block_cap():
    blocks = _blocks(101)
    plans = plan_chunks(blocks, 18000, None)
    assert _sizes(plans) == [101]
    assert _chunks(blocks, 18000) == [blocks]


def test_h2_groups_are_preserved():
    blocks = _blocks(120, h2_at=(0, 60))
    plans = plan_chunks(blocks)
    assert _sizes(plans) == [60, 60]
    assert [plan["blocks"][0]["id"] for plan in plans] == ["b0000", "b0060"]
    assert [plan["blocks"][0]["kind"] for plan in plans] == ["heading", "heading"]


def test_non_positive_max_blocks_is_disabled():
    blocks = _blocks(101)
    assert _sizes(plan_chunks(blocks, 18000, 0)) == [101]
    assert _sizes(plan_chunks(blocks, 18000, -1)) == [101]


def test_empty_blocks_yield_one_empty_plan():
    plans = plan_chunks([])
    assert len(plans) == 1
    assert plans[0]["blocks"] == []
    assert (plans[0]["start"], plans[0]["end"]) == (0, 0)


def test_character_limit_still_splits_without_a_block_cap():
    blocks = _blocks(2, text="字" * 10000)
    assert _sizes(plan_chunks(blocks, 18000, None)) == [1, 1]


def test_plan_signature_ignores_index_and_total():
    blocks = [{"id": f"B{index:02d}", "kind": "paragraph", "text": "字"} for index in range(1, 4)]
    first = {
        "chunk_id": "0000-0003",
        "index": 0,
        "total": 2,
        "blocks": blocks,
        "prev_title": None,
        "next_title": None,
    }
    second = {**first, "index": 1, "total": 9}
    assert plan_signature(first) == plan_signature(second)


def test_chunk_cache_path_uses_the_stable_chunk_id(tmp_path):
    path = chunk_cache_path(tmp_path, "key", 0, "0000-0100")
    assert path.name == "key-0000-0100.json"


def test_plan_hint_rejects_a_different_source(tmp_path):
    _write_plan_hint(
        tmp_path,
        source_sha256="a",
        model="m",
        effort="low",
        chunk_chars=9000,
        max_blocks=100,
        max_tokens=96000,
    )
    assert _read_plan_hint(tmp_path, source_sha256="b", model="m", effort="low") is None
    hint = _read_plan_hint(tmp_path, source_sha256="a", model="m", effort="low")
    assert hint["chunk_chars"] == 9000
