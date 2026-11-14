import math
from pathlib import Path

import pytest

from bookcorpus import preflight
from bookcorpus.reading.depth import preflight as depth_preflight
from bookcorpus.reading.depth.model import plan_chunks
from bookcorpus.reading.depth.preflight import SourceTooLarge
from bookcorpus.reading.service import ReadingService


class _Queue:
    """只记账、不真跑任务的队列，让预检成为唯一会拦人的东西。"""

    def __init__(self):
        self.jobs = []
        self.notes = []

    def conflicts(self, *args):
        return []

    def note(self, job_id, **fields):
        self.notes.append((job_id, fields))

    def submit(self, uid, n, tiers, **kwargs):
        job = {"id": "r-test", "uid": uid, "n": n, "tiers": list(tiers), **kwargs}
        self.jobs.append(job)
        return job


@pytest.fixture(autouse=True)
def _no_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "PI_AUTH", tmp_path / "auth.json")
    monkeypatch.setenv("PI_WEB_SEARCH_CONFIG", str(tmp_path / "web-search.json"))
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    for key in ("READING_SEARCH_PROVIDER", "TAVILY_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _service(root, annotate=False):
    service = ReadingService(root=root, annotate=annotate, depth=True)
    service.queue = _Queue()
    return service


def test_rerun_without_annotation_needs_no_model(reading_root):
    service = _service(reading_root)
    job = service.submit("B0001", 1, ["standard"], overwrite=True)
    assert job["tiers"] == ["standard"]


def test_force_annotation_requires_model(reading_root):
    service = _service(reading_root, annotate=True)
    with pytest.raises(preflight.MissingConfigError) as exc:
        service.submit("B0001", 1, ["standard"], force_annotation=True)
    assert exc.value.statuses[0].key == "reading.model"


def _blocks(count, text="字"):
    return [{"id": f"B{index:04d}", "kind": "paragraph", "level": 0, "text": text} for index in range(1, count + 1)]


def _gate(blocks, limits=None, *, chunk_chars=18000, max_blocks=100, plans=None):
    if plans is None:
        plans = plan_chunks(blocks, chunk_chars, max_blocks)
    return depth_preflight.evaluate(blocks, plans, limits or depth_preflight.Limits())


def test_small_chapter_passes_with_the_stats_contract():
    gate = _gate(_blocks(10))
    assert gate.ok is True
    assert gate.code is None
    assert gate.remediation is None
    assert gate.warnings == ()
    assert set(gate.stats) == {
        "blocks",
        "chars",
        "chunks",
        "max_chunk_blocks",
        "estimated_output_tokens",
        "estimated_seconds",
        "limits",
    }
    assert set(gate.stats["limits"]) == {
        "blocks",
        "chunks",
        "tokens",
        "seconds",
        "max_blocks_per_chunk",
        "max_tokens_per_chunk",
    }
    assert gate.stats["blocks"] == 10
    assert gate.stats["chars"] == 10
    assert gate.stats["chunks"] == 1
    assert gate.stats["max_chunk_blocks"] == 10
    assert gate.stats["estimated_output_tokens"] == math.ceil((1 * 2000 + 10 * 800) * 1.2)
    assert gate.stats["estimated_seconds"] == math.ceil(gate.stats["estimated_output_tokens"] / 109.0)


def test_large_chapter_warns_without_blocking():
    limits = depth_preflight.Limits(max_output_tokens_per_chapter=10**9, max_seconds_per_chapter=10**9)
    gate = _gate(_blocks(1001), limits)
    assert gate.ok is True
    assert any("1,000 块" in warning for warning in gate.warnings)


def test_many_chunks_warn_without_blocking():
    limits = depth_preflight.Limits(max_output_tokens_per_chapter=10**9, max_seconds_per_chapter=10**9)
    gate = _gate(_blocks(2100), limits)
    assert gate.ok is True
    assert any("21 个分块" in warning for warning in gate.warnings)


def test_blocks_gate_rejects_and_reports_the_exact_numbers():
    gate = _gate(_blocks(5001))
    assert gate.ok is False
    assert gate.code == "source_too_large"
    assert gate.stats["blocks"] == 5001
    assert gate.stats["chunks"] == 51
    with pytest.raises(SourceTooLarge) as caught:
        gate.raise_if_blocked()
    message = str(caught.value)
    assert "5,001 块" in message
    assert "5,000 块" in message
    assert "51 个分块" in message
    assert "4,923,360 输出 token" in message
    assert "12.5 小时" in message


def test_chunks_gate_rejects_before_tokens_and_seconds():
    blocks = _blocks(10)
    plans = [{"blocks": blocks[:5]}, {"blocks": blocks[5:]}]
    limits = depth_preflight.Limits(
        max_chunks_per_chapter=1,
        max_output_tokens_per_chapter=10**9,
        max_seconds_per_chapter=10**9,
    )
    gate = _gate(blocks, limits, plans=plans)
    assert gate.ok is False
    assert gate.stats["chunks"] == 2
    with pytest.raises(SourceTooLarge) as caught:
        gate.raise_if_blocked()
    assert "2 个分块" in str(caught.value)
    assert "1 个分块" in str(caught.value)


def test_tokens_gate_rejects():
    limits = depth_preflight.Limits(
        max_output_tokens_per_chapter=100,
        max_seconds_per_chapter=10**9,
    )
    gate = _gate(_blocks(10), limits)
    assert gate.ok is False
    assert gate.code == "source_too_large"
    with pytest.raises(SourceTooLarge) as caught:
        gate.raise_if_blocked()
    assert "超过单章上限 100" in str(caught.value)


def test_seconds_gate_rejects():
    limits = depth_preflight.Limits(
        max_output_tokens_per_chapter=10**9,
        max_seconds_per_chapter=1,
    )
    gate = _gate(_blocks(10), limits)
    assert gate.ok is False
    assert gate.code == "source_too_large"
    with pytest.raises(SourceTooLarge) as caught:
        gate.raise_if_blocked()
    assert "超过单章上限 1 秒" in str(caught.value)


def test_4999_blocks_passes_the_block_gate_with_isolated_limits():
    limits = depth_preflight.Limits(max_output_tokens_per_chapter=10**9, max_seconds_per_chapter=10**9)
    gate = _gate(_blocks(4999), limits)
    assert gate.ok is True
    assert gate.stats["blocks"] == 4999
    assert gate.stats["chunks"] == 50


def test_blank_text_blocks_still_pass():
    blocks = [{"id": "B0001", "kind": "paragraph", "level": 0, "text": ""} for _ in range(3)]
    gate = _gate(blocks)
    assert gate.ok is True
    assert gate.stats["chars"] == 0
    assert gate.stats["blocks"] == 3


def test_zero_block_chapter_passes():
    gate = _gate([])
    assert gate.ok is True
    assert gate.stats["blocks"] == 0
    assert gate.stats["chunks"] == 1
    assert gate.stats["max_chunk_blocks"] == 0


def test_split_chapter_passes_the_same_limits():
    assert _gate(_blocks(5001)).ok is False
    for _ in range(10):
        assert _gate(_blocks(500)).ok is True


def test_source_too_large_payload_has_the_fixed_shape():
    gate = _gate(_blocks(5001))
    with pytest.raises(SourceTooLarge) as caught:
        gate.raise_if_blocked()
    payload = caught.value.to_dict()
    assert set(payload) == {"error", "code", "stats", "remediation"}
    assert payload["code"] == "source_too_large"
    assert payload["stats"] == gate.stats
    assert payload["remediation"] == gate.remediation
    assert "每 100 块拆成多个 md" in payload["remediation"]
    assert "md/" in payload["remediation"]


def test_submit_depth_rejects_a_too_large_source_before_queueing(reading_root, monkeypatch):
    service = _service(reading_root)
    blocks = _blocks(5001)
    monkeypatch.setattr(
        service.library,
        "read_chapter",
        lambda uid, n: {"blocks": blocks, "source_sha256": "0" * 64},
    )
    # 凭据已由 autouse fixture 清空：仍然先报超限，证明预检在缺配置之前。
    with pytest.raises(SourceTooLarge) as caught:
        service.submit_depth("B0001", 1, ["model"], model="fake-model", effort="max")
    assert caught.value.stats["blocks"] == 5001
    assert service.queue.jobs == []


def test_submit_depth_gates_a_skim_only_job_when_a_model_already_exists(reading_root, monkeypatch):
    service = _service(reading_root)
    model_file = Path(service.store.depth_path("B0001", 1, "chapter_model.json"))
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("{}", encoding="utf-8")
    blocks = _blocks(5001)
    monkeypatch.setattr(
        service.library,
        "read_chapter",
        lambda uid, n: {"blocks": blocks, "source_sha256": "0" * 64},
    )
    with pytest.raises(SourceTooLarge):
        service.submit_depth("B0001", 1, ["skim"])
    assert service.queue.jobs == []


def test_submit_depth_records_preflight_stats_on_the_job(reading_root, monkeypatch):
    service = _service(reading_root)
    monkeypatch.setattr(preflight, "require", lambda *args, **kwargs: None)
    expected = len(service.library.read_chapter("B0001", 1)["blocks"])
    job = service.submit_depth("B0001", 1, ["skim"], model="fake-model", effort="max")
    assert job["layers"] == ["model", "skim"]
    assert service.queue.notes[0][0] == "r-test"
    assert service.queue.notes[0][1]["preflight"]["blocks"] == expected
    assert service.queue.notes[0][1]["preflight_warnings"] == []
    assert service.queue.notes[0][1]["chunks"] == service.queue.notes[0][1]["preflight"]["chunks"]
    assert service.queue.notes[0][1]["max_chunk_blocks"] == service.queue.notes[0][1]["preflight"]["max_chunk_blocks"]
    assert (
        service.queue.notes[0][1]["estimated_tokens"]
        == service.queue.notes[0][1]["preflight"]["estimated_output_tokens"]
    )
    assert service.queue.notes[0][1]["estimated_seconds"] == service.queue.notes[0][1]["preflight"]["estimated_seconds"]
