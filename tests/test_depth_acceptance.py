import importlib.util
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "depth_acceptance", Path(__file__).resolve().parents[1] / "tools" / "depth_acceptance.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _args(**kwargs):
    defaults = {"no_deep": False, "no_research": False, "no_citations": False}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _touch_required(depth):
    for name in runner.REQUIRED_ARTIFACTS:
        path = depth / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def test_missing_artifacts_reports_enabled_layers(tmp_path):
    _touch_required(tmp_path)
    assert runner._missing_artifacts(tmp_path, _args()) == [
        "layers/deep.json",
        "layers/research.json",
        "citations.json",
    ]
    assert runner._missing_artifacts(tmp_path, _args(no_deep=True, no_research=True, no_citations=True)) == []


def test_missing_artifacts_treats_empty_file_as_missing(tmp_path):
    _touch_required(tmp_path)
    (tmp_path / "citations.json").write_text("", encoding="utf-8")
    assert "citations.json" in runner._missing_artifacts(tmp_path, _args(no_deep=True, no_research=True))


def test_failed_layer_maps_cli_messages():
    assert runner._failed_layer("速览审计未通过：x") == "skim"
    assert runner._failed_layer("深读审计未通过：x") == "deep"
    assert runner._failed_layer("研究审计未通过：x") == "research"
    assert runner._failed_layer("引用审计未通过：x") == "citations"
    assert runner._failed_layer("模型输出被 max_tokens 截断") == "model"
    assert runner._failed_layer("unknown") is None


def test_atomic_write_replaces_without_leaving_temp_files(tmp_path):
    target = tmp_path / "report.json"
    runner._atomic_write(target, "first")
    runner._atomic_write(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert list(tmp_path.glob(".report.json.tmp-*")) == []


def test_run_chapter_sums_usage_and_elapsed_across_retries(tmp_path, monkeypatch):
    attempts = iter(
        [
            {
                "ok": False,
                "elapsed_s": 2.0,
                "error": "深读审计未通过：x",
                "failed_layer": "deep",
                "missing_artifacts": [],
                "usage": {"total_tokens": 10, "prompt_tokens": 4},
                "usd": 0.1,
            },
            {
                "ok": True,
                "elapsed_s": 2.0,
                "usage": {"total_tokens": 20, "prompt_tokens": 6},
                "usd": 0.2,
            },
        ]
    )
    monkeypatch.setattr(runner, "_run_once", lambda *args: next(attempts))
    chapter = runner._run_chapter(tmp_path, "B0001", 1, _args(retry=1))
    assert chapter["ok"] is True
    assert chapter["attempts"] == 2
    assert chapter["elapsed_s"] == 4.0
    assert chapter["usage"] == {"total_tokens": 30, "prompt_tokens": 10}
    assert chapter["usd"] == 0.3
    assert chapter["usage_missing_attempts"] == 0
    assert chapter["error"] is None
    assert chapter["failed_layer"] is None


def test_run_chapter_counts_failed_attempt_without_usage(tmp_path, monkeypatch):
    attempts = iter(
        [
            {
                "ok": False,
                "elapsed_s": 1.0,
                "error": "模型调用失败：timeout",
                "failed_layer": "model",
                "missing_artifacts": [],
            },
            {"ok": True, "elapsed_s": 1.0, "usage": {"total_tokens": 5}, "usd": 0.05},
        ]
    )
    monkeypatch.setattr(runner, "_run_once", lambda *args: next(attempts))
    chapter = runner._run_chapter(tmp_path, "B0001", 1, _args(retry=1))
    assert chapter["usage"] == {"total_tokens": 5}
    assert chapter["usage_missing_attempts"] == 1


def test_citations_unconfigured_detects_missing_or_none():
    assert runner._citations_unconfigured({"citations_provider": "none"}, _args())
    assert runner._citations_unconfigured({}, _args())
    assert not runner._citations_unconfigured({"citations_provider": "firecrawl"}, _args())
    assert not runner._citations_unconfigured({}, _args(no_citations=True))


def test_install_replaces_existing_depth_without_leftovers(tmp_path):
    src = tmp_path / "new"
    src.mkdir()
    (src / "state.json").write_text("new", encoding="utf-8")
    dest = tmp_path / "ch" / "01" / "depth"
    dest.mkdir(parents=True)
    (dest / "old.txt").write_text("old", encoding="utf-8")

    runner._install(src, dest)

    assert (dest / "state.json").read_text(encoding="utf-8") == "new"
    assert not (dest / "old.txt").exists()
    assert list(dest.parent.glob(".depth-stage-*")) == []
    assert list(dest.parent.glob(".depth-old-*")) == []


def test_write_report_records_citation_provider(tmp_path):
    report = {
        "root": "/srv/books",
        "model": "m",
        "effort": "max",
        "workers": 1,
        "generated_at": "2026-11-10T00:00:00+00:00",
        "elapsed_s": 1.0,
        "ok": 1,
        "total": 1,
        "failed": 0,
        "usage": {"total_tokens": 10},
        "usd": 0.1,
        "citations": {"total": 0, "verified": 0, "providers": ["firecrawl"]},
        "partial": False,
        "notes": [],
        "chapters": [
            {
                "chapter": "B0001/01",
                "ok": True,
                "attempts": 1,
                "elapsed_s": 1.0,
                "usage": {"total_tokens": 10},
                "usd": 0.1,
                "payload": {"citations_total": 0, "citations_verified": 0},
                "missing_artifacts": [],
            }
        ],
    }

    runner._write_report(tmp_path, report)

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "检索提供方：firecrawl" in markdown
    assert list(tmp_path.glob(".report.*.tmp-*")) == []


def test_citations_unconfigured_ignores_failed_or_skipped_payload():
    args = _args()
    assert runner._citations_unconfigured(None, args) is False
    assert runner._citations_unconfigured({"citations_skipped": True}, args) is False
    assert runner._citations_unconfigured({"citations_provider": "firecrawl"}, args) is False
    assert runner._citations_unconfigured({"citations_provider": "none"}, args) is True


def test_run_once_keeps_generation_error_when_payload_missing(tmp_path, monkeypatch):
    args = _args(
        model="fake",
        effort="max",
        model_timeout=1,
        max_tokens=1,
        chapter_timeout=1,
        keep_temp=True,
    )
    monkeypatch.setattr(runner, "_prepare_workspace", lambda *a: tmp_path)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="模型输出被 max_tokens 截断"),
    )
    result = runner._run_once(tmp_path, "B0001", 1, args)
    assert result["ok"] is False
    assert result["failed_layer"] == "model"
    assert "max_tokens" in result["error"]
    assert "检索提供方" not in result["error"]


def test_run_once_reports_missing_citation_provider(tmp_path, monkeypatch):
    args = _args(
        model="fake",
        effort="max",
        model_timeout=1,
        max_tokens=1,
        chapter_timeout=1,
        keep_temp=True,
    )
    payload = {"status": "ready", "citations_provider": "none", "usage": {}, "usd": 0}
    monkeypatch.setattr(runner, "_prepare_workspace", lambda *a: tmp_path)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    monkeypatch.setattr(runner, "_last_json", lambda text: payload)
    monkeypatch.setattr(runner, "_read_state", lambda depth: {})
    monkeypatch.setattr(runner, "_missing_artifacts", lambda depth, args: [])
    result = runner._run_once(tmp_path, "B0001", 1, args)
    assert result["ok"] is False
    assert result["failed_layer"] == "citations"
    assert "检索提供方" in result["error"]
