from pathlib import Path

import pytest

from bookcorpus import preflight
from bookcorpus.reading.service import DEPTH_FILES, ReadingService


class _Queue:
    """只记账、不真跑任务的队列，让预检成为唯一会拦人的东西。"""

    def __init__(self):
        self.jobs = []

    def conflicts(self, *args):
        return []

    def note(self, job_id, **fields):
        return None

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


def test_submit_depth_blocks_research_without_provider(reading_root, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    service = _service(reading_root)
    with pytest.raises(preflight.MissingConfigError) as exc:
        service.submit_depth("B0001", 1, ["research"], model="fake", effort="high")
    assert exc.value.degradable
    assert exc.value.statuses[0].key == "reading.search"

    job = service.submit_depth("B0001", 1, ["research"], model="fake", effort="high", allow_unverified=True)
    assert job["layers"] == ["model", "research"]
    assert job["allow_unverified"] is True


def test_allow_unverified_drops_citations(reading_root, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    service = _service(reading_root)
    job = service.submit_depth(
        "B0001", 1, ["research", "citations"], model="fake", effort="high", allow_unverified=True
    )
    assert "citations" not in job["layers"]
    assert "research" in job["layers"]


def test_allow_unverified_cannot_degrade_citations_only(reading_root, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    service = _service(reading_root)
    for layer in ("model", "research"):
        target = Path(service.store.depth_path("B0001", 1, DEPTH_FILES[layer]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "depth_status", lambda uid, n: {"layers": {"research": {"status": "ready"}}})
    with pytest.raises(ValueError, match="引用层需要真检索"):
        service.submit_depth("B0001", 1, ["citations"], model="fake", effort="high", allow_unverified=True)


def test_rerun_without_annotation_needs_no_model(reading_root):
    service = _service(reading_root)
    job = service.submit("B0001", 1, ["standard"], overwrite=True)
    assert job["tiers"] == ["standard"]


def test_force_annotation_requires_model(reading_root):
    service = _service(reading_root, annotate=True)
    with pytest.raises(preflight.MissingConfigError) as exc:
        service.submit("B0001", 1, ["standard"], force_annotation=True)
    assert exc.value.statuses[0].key == "reading.model"
