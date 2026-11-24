"""阅读工作台服务层测试：完整正文直出、划词伴读、旁批钉住、知识地图与操作日志。"""

import json

from bookcorpus.reading.service import ReadingService


class _FakeAssistClient:
    model = "fake-flash"
    effort = "medium"

    def __init__(self, *, text="这是[原文]的人话解释，[补充]举个工程例子。", figure_json=None, map_json=None):
        self.text = text
        self.figure_json = figure_json
        self.map_json = map_json
        self.calls = []

    def available(self):
        return True

    def complete(self, messages, *, model=None, effort=None, json_mode=False, max_tokens=None, on_delta=None):
        self.calls.append({"messages": messages, "model": model, "effort": effort, "json_mode": json_mode})
        if json_mode and self.figure_json is not None:
            content = json.dumps(self.figure_json, ensure_ascii=False)
            return content, {"prompt_tokens": 50, "completion_tokens": 80, "total_tokens": 130}
        if on_delta is not None:
            for ch in (self.text[:6], self.text[6:]):
                on_delta(ch)
        return self.text, {"prompt_tokens": 40, "completion_tokens": 60, "total_tokens": 100}

    def complete_json(self, messages, *, model=None, effort=None, max_tokens=None):
        self.calls.append({"messages": messages, "model": model, "effort": effort, "json_mode": True})
        assert self.map_json is not None
        return dict(self.map_json), {"prompt_tokens": 120, "completion_tokens": 180, "total_tokens": 300}


def test_shelf_book_and_chapter_return_full_blocks(reading_root):
    service = ReadingService(str(reading_root))
    shelf = service.shelf()
    assert shelf["stats"]["readable"] == 2
    assert shelf["stats"]["chapters"] == 3
    assert "jobs" not in shelf

    book = service.book("B0001")
    assert book["book"]["title"] == "甲书"
    assert "reading" not in book
    assert len(book["chapters"]) == 2
    assert book["chapters"][0]["n"] == "01"
    assert book["chapters"][0]["note_count"] == 0

    ch = service.chapter("B0001", 1)
    assert ch["chapter"]["n"] == "01"
    assert len(ch["blocks"]) >= 1
    for retired in (
        "tiers",
        "preview_blocks",
        "standard_blocks",
        "edition",
        "edition_fit",
        "depth",
        "gates",
        "reading",
    ):
        assert retired not in ch
    assert ch["preflight"]["chapter_map"]["supported"] is False


def test_assist_streams_renders_diagram_pins_and_logs(reading_root):
    fig_payload = {
        "caption": "核心因果流程",
        "family": "flow",
        "reading_note": "从现象到根因的三步推演",
        "summary": "[原文] 先定位样本范围，[补充] 再比较转换率变化。",
        "data": {
            "nodes": [{"id": "N1", "label": "识别问题"}, {"id": "N2", "label": "切分维度"}],
            "edges": [{"from": "N1", "to": "N2", "label": "触发"}],
        },
    }
    client = _FakeAssistClient(figure_json=fig_payload)
    service = ReadingService(str(reading_root), client=client)

    raw = service.library.read_chapter("B0001", 1)
    bid = raw["blocks"][0]["id"]
    text = raw["blocks"][0]["text"]

    chunks = []
    res = service.assist(
        "B0001",
        1,
        "explain",
        block_ids=[bid],
        selected_text=text,
        on_delta=chunks.append,
    )
    assert "".join(chunks) == client.text
    assert res["tokens"] == 100
    assert res["action"] == "explain"

    diag = service.assist("B0001", 1, "diagram", block_ids=[bid], selected_text=text)
    assert "<svg" in diag["diagram_html"]
    assert diag["figure"]["caption"] == "核心因果流程"

    saved = service.pin_assist_note(
        "B0001",
        1,
        {
            "action": "diagram",
            "title": "核心因果流程",
            "block_ids": [bid],
            "selected_text": text,
            "content": diag["content"],
            "diagram_html": diag["diagram_html"],
        },
    )
    ch = service.chapter("B0001", 1)
    assert len(ch["assist_notes"]) == 1
    assert ch["assist_notes"][0]["id"] == saved["id"]

    records = service.records()
    assert records["summary"]["total"] == 3
    assert [op["action"] for op in records["logs"]] == ["pin", "diagram", "explain"]
    assert len(records["chapters"]) == 1
    assert records["chapters"][0]["note_count"] == 1

    removed = service.unpin_assist_note("B0001", 1, saved["id"])
    assert removed == {"ok": True, "deleted": saved["id"]}
    assert service.chapter("B0001", 1)["assist_notes"] == []
    assert service.records()["summary"]["total"] == 4


def test_chapter_map_short_gate_and_force(reading_root):
    raw = ReadingService(str(reading_root)).library.read_chapter("B0001", 1)
    bid = raw["blocks"][0]["id"]
    text = raw["blocks"][0]["text"]
    quote = text[: max(1, min(6, len(text)))]
    map_doc = {
        "schema": 1,
        "uid": "B0001",
        "n": 1,
        "source_sha256": raw["source_sha256"],
        "prompt_version": "chapter-model-v1",
        "units": [
            {
                "id": "U01",
                "kind": "claim",
                "importance": "core",
                "hard_info": False,
                "summary": "开篇直陈主旨",
                "quotes": [{"block": bid, "text": quote}],
                "source_blocks": [bid],
                "relations": [],
                "section": "S01",
            }
        ],
        "sections": [{"id": "S01", "title": "起笔", "summary": "核心脉络", "unit_ids": ["U01"]}],
        "terms": [{"term": "样本", "definition": "文中提及的对象", "source_block": bid, "quote": quote}],
        "figures": [],
        "non_semantic": [],
        "folds": [],
    }
    client = _FakeAssistClient(map_json=map_doc)
    service = ReadingService(str(reading_root), client=client)

    gated = service.generate_chapter_map("B0001", 1, force=False)
    assert gated["supported"] is False
    assert gated["can_force"] is True
    assert client.calls == []

    forced = service.generate_chapter_map("B0001", 1, force=True)
    assert forced["supported"] is True
    assert forced["stats"]["sections"] == 1
    assert forced["terms"][0]["term"] == "样本"
    assert len(client.calls) == 1

    cached = service.generate_chapter_map("B0001", 1, force=False)
    assert cached["cached"] is True
    assert len(client.calls) == 1


def test_assist_prompts_have_no_reading_mode_injection(reading_root):
    fig_payload = {
        "caption": "关系简图",
        "family": "flow",
        "reading_note": "两步衔接",
        "summary": "[原文] 只讲选区本身。",
        "data": {
            "nodes": [{"id": "N1", "label": "甲"}, {"id": "N2", "label": "乙"}],
            "edges": [{"from": "N1", "to": "N2"}],
        },
    }
    client = _FakeAssistClient(figure_json=fig_payload)
    service = ReadingService(str(reading_root), client=client)
    raw = service.library.read_chapter("B0001", 1)
    bid = raw["blocks"][0]["id"]
    text = raw["blocks"][0]["text"]

    for action in ("translate", "explain", "critique", "diagram", "ask"):
        service.assist(
            "B0001",
            1,
            action,
            block_ids=[bid],
            selected_text=text,
            question="这句什么意思" if action == "ask" else "",
        )

    assert len(client.calls) == 5
    for idx, call in enumerate(client.calls):
        joined = "\n".join(msg["content"] for msg in call["messages"])
        for banned in ("阅读偏好参考", "书型参考", "论证式", "读法提示", "命题、论据"):
            assert banned not in joined, f"call[{idx}] unexpectedly contained {banned!r}"


def test_pin_and_unpin_assist_notes_serialize_concurrent_updates(reading_root, monkeypatch):
    import concurrent.futures
    import time

    store = ReadingService(str(reading_root), client=_FakeAssistClient()).store
    orig_read = store.assist_notes

    def slow_read(uid, n, source_sha=None):
        rows = orig_read(uid, n, source_sha=source_sha)
        time.sleep(0.01)
        return rows

    monkeypatch.setattr(store, "assist_notes", slow_read)

    def pin_one(idx):
        return store.pin_assist_note(
            "B0001",
            1,
            {"id": f"note-{idx:02d}", "block_ids": ["B01"], "content": f"批注 {idx}"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(pin_one, range(10)))

    saved = orig_read("B0001", 1)
    assert {item["id"] for item in saved} == {f"note-{i:02d}" for i in range(10)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(store.unpin_assist_note, "B0001", 1, f"note-{i:02d}") for i in range(5)]
        assert all(f.result() is not None for f in futs)

    remaining = orig_read("B0001", 1)
    assert {item["id"] for item in remaining} == {f"note-{i:02d}" for i in range(5, 10)}
