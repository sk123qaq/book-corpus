"""生成编排：真实队列、原子产物、门禁、读本与 stale。"""

import json
import os
import time
from pathlib import Path

from bookcorpus.reading.render import render_chapter
from bookcorpus.reading.service import (
    ReadingService,
    _block_band_problems,
    _coverage_problems,
    audit_pack,
    source_texts,
)
from bookcorpus.reading.tiers import project

RUNNING = ("queued", "chunking", "annotating", "validating", "projecting", "rendering")


def _wait(service, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.queue.get(job_id)
        assert job is not None
        if job["state"] not in RUNNING:
            return job
        time.sleep(0.02)
    raise AssertionError("任务没有在 %.1fs 内结束" % timeout)


def _outside_work(root):
    work = Path(root) / "工作区"
    out = set()
    for path in Path(root).rglob("*"):
        if path.is_file() and work not in path.parents:
            out.add(str(path.relative_to(root)))
    return out


def test_shelf_book_and_chapter_are_real_data(reading_root):
    service = ReadingService(str(reading_root))
    shelf = service.shelf()
    assert shelf["stats"]["readable"] == 2
    assert shelf["stats"]["chapters"] == 3
    assert shelf["stats"]["packs"] == 0
    assert shelf["stats"]["chars"] > 0
    assert [book["uid"] for book in shelf["books"]] == ["B0001", "B0002"]
    assert shelf["recent"][0]["uid"] == "B0001"

    book = service.book("B0001")
    assert book["book"]["title"] == "甲书"
    assert len(book["chapters"]) == 2
    assert book["chapters"][0]["n"] == "01"
    assert book["chapters"][0]["tiers"]["standard"]["exists"] is False

    chapter = service.chapter("B0001", 1)
    assert chapter["chapter"]["n"] == "01"
    assert len(chapter["blocks"]) == 1
    assert len(chapter["tiers"]) == 4
    assert [tier["name"] for tier in chapter["tiers"]] == ["速览", "标准", "深读", "研究"]
    assert chapter["preview_blocks"] == ["B01"]


def test_draft_job_runs_through_local_queue_and_writes_only_work(reading_root):
    before = _outside_work(reading_root)
    service = ReadingService(str(reading_root))
    job = service.submit("B0001", 1, ["standard", "deep"])
    done = _wait(service, job["id"])
    assert done["state"] == "done"
    assert done["status"] == "draft"
    assert done["progress"] == 100
    assert done["pack_id"] == "B0001/ch/01"
    assert [step["status"] for step in done["steps"]] == ["done"] * 5

    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    for rel in (
        "manifest.json",
        "chapter.html",
        "audit.json",
        "export/standard.html",
        "export/deep.html",
    ):
        assert (pack / rel).is_file(), rel
    assert not (pack / "export" / "skim.html").exists()
    assert not (pack / "export" / "research.html").exists()
    assert not (pack / "annotation.json").exists()
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["draft"] is True
    assert manifest["status"] == "draft"
    assert manifest["model"] == "draft"
    assert manifest["source_sha256"] == service.library.read_chapter("B0001", 1)["source_sha256"]
    assert set(manifest["tiers"]) == {"standard", "deep"}
    assert manifest["requested_tiers"] == ["standard", "deep"]
    assert manifest["tiers"]["standard"]["stats"]["c_char"] == 1.0
    audit = json.loads((pack / "audit.json").read_text(encoding="utf-8"))
    gates = {gate["id"]: gate["status"] for gate in audit["gates"]}
    assert gates["G1"] == "pass"
    assert gates["G3"] == "warn"
    assert gates["G4"] == "pass"
    assert gates["G6"] == "pass"

    html = (pack / "chapter.html").read_text(encoding="utf-8")
    assert 'data-origin="source"' in html
    assert html.count('data-tier-tab="') == 4
    assert html.count('data-tier-tab="standard" aria-selected="true"') == 1
    assert "第一段写一个人走进屋子" in html
    assert 'data-origin="added"' in html  # 结构里保留了讲解层样式与容器
    assert source_texts(html)["B01"].startswith("第一段写一个人")

    assert _outside_work(reading_root) - before == set()

    cached = service.chapter("B0001", 1)
    assert cached["tiers"][1]["exists"] is True
    assert cached["tiers"][1]["stale"] is False
    assert cached["tiers"][1]["stats"]["c_char"] == 1.0
    assert service.shelf()["stats"]["packs"] == 1
    assert service.records()["packs"][0]["uid"] == "B0001"


def test_incremental_generation_keeps_unselected_tiers(reading_root):
    service = ReadingService(str(reading_root))
    first = _wait(service, service.submit("B0001", 1, ["standard"])["id"])
    assert first["state"] == "done"
    second = _wait(service, service.submit("B0001", 1, ["deep"])["id"])
    assert second["state"] == "done"

    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["tiers"]) == {"standard", "deep"}
    assert manifest["requested_tiers"] == ["deep"]
    assert manifest["preserved_tiers"] == ["standard"]
    assert (pack / "export" / "standard.html").is_file()
    assert (pack / "export" / "deep.html").is_file()
    assert not (pack / "export" / "skim.html").exists()
    assert not (pack / "export" / "research.html").exists()

    chapter = service.chapter("B0001", 1)
    assert chapter["tiers"][1]["exists"] is True
    assert chapter["tiers"][2]["exists"] is True
    assert chapter["tiers"][0]["exists"] is False
    assert chapter["tiers"][3]["exists"] is False


def test_overwrite_selected_tier_keeps_other_tiers(reading_root):
    service = ReadingService(str(reading_root))
    _wait(service, service.submit("B0001", 1, ["standard", "deep"])["id"])
    done = _wait(service, service.submit("B0001", 1, ["standard"], overwrite=True)["id"])
    assert done["state"] == "done"

    pack = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["tiers"]) == {"standard", "deep"}
    assert manifest["requested_tiers"] == ["standard"]
    assert manifest["preserved_tiers"] == ["deep"]
    assert (pack / "export" / "deep.html").is_file()


def test_source_change_marks_pack_stale(reading_root):
    service = ReadingService(str(reading_root))
    done = _wait(service, service.submit("B0001", 1, ["standard"])["id"])
    assert done["state"] == "done"
    path = Path(reading_root) / "books" / "B0001" / "md" / "001_第1节.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n\n后来补写的一段。\n", encoding="utf-8")
    service.library._chapters.clear()
    chapter = service.chapter("B0001", 1)
    assert chapter["tiers"][1]["stale"] is True
    assert chapter["chapter"]["tiers"]["standard"]["stale"] is True


def test_audit_rejects_changed_source_and_overlapping_card(reading_root):
    service = ReadingService(str(reading_root))
    raw = service.library.read_chapter("B0001", 1)
    blocks = raw["blocks"]
    draft = {tier: project(blocks, None, tier) for tier in ("skim", "standard", "deep", "research")}
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    html = render_chapter(book, chapter, blocks, draft, resolver=lambda rel: "")
    good = audit_pack(blocks, draft, html, None, True)
    assert good["status"] == "draft"

    changed = html.replace("第一段写一个人走进屋子", "第一段写两个人走进屋子")
    bad = audit_pack(blocks, draft, changed, None, True)
    assert bad["status"] == "failed"
    assert next(gate for gate in bad["gates"] if gate["id"] == "G1")["status"] == "fail"

    overlap_projection = dict(draft["standard"])
    overlap_projection["cards"] = [
        {
            "id": "C01",
            "trigger": "B01",
            "title": "",
            "text": "这一段故意照抄原文：" + blocks[0]["text"][:24],
            "citations": ["B01"],
            "quotes": [],
            "priority": 1,
        }
    ]
    projections = dict(draft)
    projections["standard"] = overlap_projection
    overlap = audit_pack(blocks, projections, html, None, True)
    assert next(gate for gate in overlap["gates"] if gate["id"] == "G4")["status"] == "fail"


def test_render_has_notes_tier_switch_and_source_only_controls(reading_root):
    service = ReadingService(str(reading_root))
    raw = service.library.read_chapter("B0001", 1)
    blocks = raw["blocks"]
    projections = {tier: project(blocks, None, tier) for tier in ("skim", "standard", "deep", "research")}
    projections["standard"]["cards"] = [
        {
            "id": "C01",
            "trigger": "B01",
            "title": "读法",
            "text": "讲解文字不进原文层。",
            "citations": ["B01"],
            "quotes": [{"block": "B01", "text": "第一段写一个人走进屋子"}],
            "priority": 1,
        }
    ]
    book = service._book_view(service.library.book("B0001"))
    chapter = {"n": "01", "title": raw["title"], "source_sha256": raw["source_sha256"], "tiers": {}}
    html = render_chapter(book, chapter, blocks, projections, resolver=lambda rel: "")
    assert 'data-origin="added"' in html
    assert 'id="only-btn"' in html
    assert 'id="cmp-btn"' in html
    assert "body.only-source" in html
    assert 'data-tier-tab="skim"' in html
    assert 'data-tier-tab="research"' in html
    assert "第一段写一个人走进屋子" in source_texts(html)["B01"]


def test_missing_chapter_does_not_create_a_job(reading_root):
    service = ReadingService(str(reading_root))
    try:
        service.submit("B0001", 99, ["standard"])
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("缺章应当拒绝")
    assert service.queue.list() == []
    assert not os.path.exists(Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "99")


def test_submit_rejects_existing_tier_without_overwrite(reading_root):
    import pytest

    from bookcorpus.reading.service import PackExistsError

    service = ReadingService(str(reading_root))
    done = _wait(service, service.submit("B0001", 1, ["standard"])["id"])
    assert done["state"] == "done"

    with pytest.raises(PackExistsError) as exc:
        service.submit("B0001", 1, ["standard"])
    assert exc.value.tiers == ["standard"]

    again = service.submit("B0001", 1, ["standard"], overwrite=True)
    assert again["overwrite"] is True
    assert _wait(service, again["id"])["state"] == "done"


def test_submit_rejects_overlapping_active_tier(reading_root, monkeypatch):
    import pytest

    from bookcorpus.reading.service import JobConflictError

    service = ReadingService(str(reading_root))
    monkeypatch.setattr(
        service.queue,
        "conflicts",
        lambda uid, n, tiers: [{"id": "r-active", "uid": uid, "n": "%02d" % n, "tiers": list(tiers)}],
    )
    with pytest.raises(JobConflictError) as exc:
        service.submit("B0001", 1, ["standard"])
    assert exc.value.conflicts[0]["id"] == "r-active"


def _manifest(root, uid="B0001", n="01"):
    path = Path(root) / "工作区" / "阅读" / uid / "ch" / n / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fake_annotation(blocks):
    return {
        "blocks": [{"id": blocks[0]["id"], "role": "claim", "priority": 1, "spine": True, "deps": []}],
        "supports": [],
        "cards": [],
    }


def test_source_change_requires_rebuild_and_rebuild_drops_unselected(reading_root):
    import pytest

    from bookcorpus.reading.service import RebuildRequiredError

    service = ReadingService(str(reading_root))
    assert _wait(service, service.submit("B0001", 1, ["standard"])["id"])["state"] == "done"
    path = Path(reading_root) / "books" / "B0001" / "md" / "001_第1节.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n\n后来补写的一段。\n", encoding="utf-8")

    with pytest.raises(RebuildRequiredError) as exc:
        service.submit("B0001", 1, ["deep"])
    assert exc.value.tiers == ["standard"]
    assert exc.value.reason == "原文已更改"

    done = _wait(service, service.submit("B0001", 1, ["deep"], rebuild=True)["id"])
    assert done["state"] == "done"
    manifest = _manifest(reading_root)
    assert set(manifest["tiers"]) == {"deep"}
    assert manifest["requested_tiers"] == ["deep"]
    assert manifest["preserved_tiers"] == []
    assert not (Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "export" / "standard.html").exists()


def test_mode_change_requires_rebuild(reading_root):
    import pytest

    from bookcorpus.reading.service import RebuildRequiredError

    service = ReadingService(str(reading_root))
    assert _wait(service, service.submit("B0001", 1, ["standard"])["id"])["state"] == "done"
    service.annotate = True

    with pytest.raises(RebuildRequiredError) as exc:
        service.submit("B0001", 1, ["deep"])
    assert exc.value.tiers == ["standard"]
    assert "生成模式不同" in exc.value.reason


def test_rebuild_replaces_pack_with_requested_tiers_only(reading_root):
    service = ReadingService(str(reading_root))
    assert _wait(service, service.submit("B0001", 1, ["standard", "deep"])["id"])["state"] == "done"

    done = _wait(service, service.submit("B0001", 1, ["standard"], rebuild=True)["id"])
    assert done["state"] == "done"
    manifest = _manifest(reading_root)
    assert set(manifest["tiers"]) == {"standard"}
    assert manifest["preserved_tiers"] == []
    assert not (Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "export" / "deep.html").exists()


def test_same_chapter_different_tier_conflicts_and_other_chapter_is_allowed(reading_root):
    import pytest

    from bookcorpus.reading.service import JobConflictError

    service = ReadingService(str(reading_root))
    release = __import__("threading").Event()
    started = __import__("threading").Event()

    def blocker(job_id, job):
        started.set()
        release.wait(5)
        return {"status": "done"}

    service.queue.runner = blocker
    first = service.submit("B0001", 1, ["deep"])
    try:
        assert started.wait(2)
        with pytest.raises(JobConflictError):
            service.submit("B0001", 1, ["standard"])
        other = service.submit("B0002", 1, ["standard"])
        assert other["state"] in ("queued", "rendering", "projecting", "validating", "annotating", "chunking")
    finally:
        release.set()
    assert _wait(service, first["id"])["state"] == "done"
    assert _wait(service, other["id"])["state"] == "done"


def test_concurrent_jobs_use_their_own_model_meta(reading_root, monkeypatch):
    import bookcorpus.reading.service as service_module

    class FakeClient:
        model = "fake-default"
        effort = "high"

        def annotate_with_meta(self, book, chapter, blocks, model=None, effort=None):
            uid = book["uid"]
            time.sleep(0.05 if uid == "B0001" else 0.01)
            return _fake_annotation(blocks), {
                "requested_model": model or self.model,
                "model": "m-" + uid,
                "effort": effort or self.effort,
                "usage": {
                    "prompt_tokens": 100 if uid == "B0001" else 200,
                    "completion_tokens": 10,
                },
                "fallback": uid == "B0001",
            }

    service = ReadingService(str(reading_root))
    service.annotate = True
    service.client = FakeClient()
    monkeypatch.setattr(service_module, "audit_pack", lambda *args, **kwargs: {"status": "ok", "gates": []})

    first = service.submit("B0001", 1, ["standard"], model="req-a", effort="low")
    second = service.submit("B0002", 1, ["standard"], model="req-b", effort="medium")
    assert _wait(service, first["id"])["state"] == "done"
    assert _wait(service, second["id"])["state"] == "done"

    a = _manifest(reading_root, "B0001", "01")
    b = _manifest(reading_root, "B0002", "01")
    assert (a["model"], a["model_requested"], a["reasoning_effort"], a["model_fallback"]) == (
        "m-B0001",
        "req-a",
        "low",
        True,
    )
    assert (b["model"], b["model_requested"], b["reasoning_effort"], b["model_fallback"]) == (
        "m-B0002",
        "req-b",
        "medium",
        False,
    )
    assert a["usage"]["prompt_tokens"] == 100
    assert b["usage"]["prompt_tokens"] == 200


def test_actual_usage_over_budget_fails_without_publishing(reading_root):
    class HugeUsageClient:
        model = "fake"
        effort = "high"

        def annotate_with_meta(self, book, chapter, blocks, model=None, effort=None):
            return _fake_annotation(blocks), {
                "requested_model": model or self.model,
                "model": self.model,
                "effort": effort or self.effort,
                "usage": {"prompt_tokens": 500_001, "completion_tokens": 1},
                "fallback": False,
            }

    service = ReadingService(str(reading_root))
    service.annotate = True
    service.client = HugeUsageClient()
    done = _wait(service, service.submit("B0001", 1, ["standard"])["id"])
    assert done["state"] == "failed"
    assert "模型用量超限" in done["error"]
    assert "usage" in done["error"]
    assert not (Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "manifest.json").exists()


def test_tier_states_use_each_tier_generated_at(reading_root):
    service = ReadingService(str(reading_root))
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "source_sha256": "sha",
        "status": "draft",
        "draft": True,
        "tiers": {
            "standard": {"generated_at": "2026-02-02T00:00:00Z", "stats": {}},
            "deep": {"stats": {}},
        },
    }
    exports = {"standard": "<html>standard</html>", "deep": "<html>deep</html>"}
    service.store.save("B0001", 1, manifest, None, None, "<html>chapter</html>", exports)

    states = service.store.tier_states("B0001", 1, "sha")["tiers"]
    assert states["standard"]["generated_at"] == "2026-02-02T00:00:00Z"
    assert states["deep"]["generated_at"] == "2026-01-01T00:00:00Z"


class _MetaClient:
    """按构造参数返回独立 model 的假客户端，用来演练增量包混模型。"""

    effort = "high"

    def __init__(self, model):
        self.model = model

    def annotate_with_meta(self, book, chapter, blocks, model=None, effort=None):
        return _fake_annotation(blocks), {
            "requested_model": model or self.model,
            "model": self.model,
            "effort": effort or self.effort,
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "fallback": False,
        }


def test_pack_footer_records_real_model_and_time(reading_root, monkeypatch):
    import bookcorpus.reading.service as service_module

    monkeypatch.setattr(service_module, "audit_pack", lambda *args, **kwargs: {"status": "ok", "gates": []})
    service = ReadingService(str(reading_root))
    service.annotate = True
    service.client = _MetaClient("m-old")

    assert _wait(service, service.submit("B0001", 1, ["standard"])["id"])["state"] == "done"
    first = _manifest(reading_root)
    old_time = first["generated_at"]
    assert first["model"] == "m-old"
    assert first["tiers"]["standard"]["model"] == "m-old"
    assert "error" not in first
    assert "budget_estimate" in first
    assert "budget" not in first
    assert "budget_estimate" in _manifest(reading_root)

    chapter_html = (Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "chapter.html").read_text(
        encoding="utf-8"
    )
    assert 'id="colophon-model"' in chapter_html
    assert "m-old" in chapter_html
    assert old_time in chapter_html
    footer = chapter_html.split('class="colophon"', 1)[1].split("</div>", 1)[0]
    assert '<span id="colophon-model">m-old</span>' in footer
    assert "生成器 phase1-1" in footer
    assert '<span id="colophon-time">' + old_time + "</span>" in footer

    export_dir = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "export"
    standard_export = (export_dir / "standard.html").read_text(encoding="utf-8")
    assert "m-old" in standard_export
    assert old_time in standard_export

    service.client = _MetaClient("m-new")
    assert _wait(service, service.submit("B0001", 1, ["deep"])["id"])["state"] == "done"
    second = _manifest(reading_root)
    assert second["tiers"]["deep"]["model"] == "m-new"
    assert second["tiers"]["standard"]["generated_at"] == first["tiers"]["standard"]["generated_at"]

    export_dir = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "export"
    standard_export = (export_dir / "standard.html").read_text(encoding="utf-8")
    deep_export = (export_dir / "deep.html").read_text(encoding="utf-8")
    assert "m-old" in standard_export
    assert "m-new" not in standard_export
    assert "m-new" in deep_export
    assert first["tiers"]["standard"]["generated_at"] in standard_export


def test_preserved_tier_without_old_model_falls_back_to_unknown(reading_root, monkeypatch):
    import bookcorpus.reading.service as service_module

    monkeypatch.setattr(service_module, "audit_pack", lambda *args, **kwargs: {"status": "ok", "gates": []})
    service = ReadingService(str(reading_root))
    service.annotate = True
    service.client = _MetaClient("m-old")
    assert _wait(service, service.submit("B0001", 1, ["standard"])["id"])["state"] == "done"

    manifest_path = Path(reading_root) / "工作区" / "阅读" / "B0001" / "ch" / "01" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("model")
    manifest["tiers"]["standard"].pop("model")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    service.client = _MetaClient("m-new")
    assert _wait(service, service.submit("B0001", 1, ["deep"])["id"])["state"] == "done"
    second = _manifest(reading_root)
    assert second["tiers"]["standard"]["model"] == "unknown"
    assert second["tiers"]["deep"]["model"] == "m-new"


def test_g3_tolerates_two_point_coverage_gap():
    projections = {"deep": {"stats": {"c_char": 0.78, "c_block": 0.80}}}
    assert _coverage_problems(projections) == []
    projections["deep"]["stats"]["c_char"] = 0.77
    assert _coverage_problems(projections) == ["deep C字=0.77"]


def test_block_band_gap_is_diagnostic_only():
    projections = {"skim": {"stats": {"c_char": 0.52, "c_block": 0.18}}}
    assert _coverage_problems(projections) == []
    assert _block_band_problems(projections) == ["skim C块=0.18"]
    projections["skim"]["stats"]["c_char"] = 0.73
    assert _coverage_problems(projections) == ["skim C字=0.73"]
