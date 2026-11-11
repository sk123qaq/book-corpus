import pytest

from bookcorpus import preflight
from bookcorpus.reading.service import ReadingService


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


def test_rerun_without_annotation_needs_no_model(reading_root):
    service = _service(reading_root)
    job = service.submit("B0001", 1, ["standard"], overwrite=True)
    assert job["tiers"] == ["standard"]


def test_force_annotation_requires_model(reading_root):
    service = _service(reading_root, annotate=True)
    with pytest.raises(preflight.MissingConfigError) as exc:
        service.submit("B0001", 1, ["standard"], force_annotation=True)
    assert exc.value.statuses[0].key == "reading.model"
