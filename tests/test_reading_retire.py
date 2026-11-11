import json
import tarfile
from pathlib import Path

from bookcorpus.reading import retire


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _fixture(root: Path) -> Path:
    pack = root / "工作区" / "阅读" / "B0001" / "ch" / "01"
    depth = pack / "depth"
    _write(
        depth / "layers" / "research.json",
        {
            "schema": 1,
            "questions": [
                {"text": "问题一", "why": "原因一", "blocks": ["S01-U01"]},
                {"text": "问题二", "why": "原因二"},
                {"text": "问题三"},
                {"text": "问题四"},
            ],
        },
    )
    _write(
        depth / "layers" / "deep.json",
        {"schema": 1, "prompt_version": "deep-annotations-v1", "notes": [], "chains": []},
    )
    _write(depth / "citations.json", {"schema": 1, "searched": True})
    _write(
        depth / "state.json",
        {
            "status": "ready",
            "research_sha": "r",
            "citations_sha": "c",
            "research_prompt_version": "p",
            "citations_prompt_version": "p",
            "layer_input_sha": {"citations": "r", "skim": "m"},
            "layer_model_sha": {"skim": "m", "research": "m"},
            "layer_usage": {"skim": {}, "citations": {}},
            "layer_updated_at": {"skim": "t", "research": "t"},
        },
    )
    for name in ("skim", "standard", "deep"):
        _write(depth / "layers" / f"{name}.json", {"schema": 1, "name": name})
    _write(pack / "chapter.html", "<html>legacy</html>")
    _write(pack / "manifest.json", {"uid": "B0001"})
    for name in ("skim", "standard", "deep", "research"):
        _write(pack / "export" / f"{name}.html", f"<html>{name}</html>")
    return pack


def test_discover_only_returns_retired_files(tmp_path):
    _fixture(tmp_path)
    found = {path.relative_to(tmp_path).as_posix() for path in retire.discover(tmp_path)}
    assert found == {
        "工作区/阅读/B0001/ch/01/depth/layers/research.json",
        "工作区/阅读/B0001/ch/01/depth/citations.json",
        "工作区/阅读/B0001/ch/01/export/research.html",
        "工作区/阅读/B0001/ch/01/manifest.json",
    }


def test_apply_archives_deletes_and_keeps_working_layers(tmp_path):
    pack = _fixture(tmp_path)
    result = retire.apply(tmp_path, rerender=False)
    assert result["removed"] == 4
    assert result["questions"] == {"packs": 1, "questions": 3, "skipped": []}
    assert result["state_changed"] == 1
    assert not (pack / "depth" / "layers" / "research.json").exists()
    assert not (pack / "depth" / "citations.json").exists()
    assert not (pack / "export" / "research.html").exists()
    assert (pack / "export" / "skim.html").is_file()
    assert (pack / "export" / "standard.html").is_file()
    assert (pack / "export" / "deep.html").is_file()
    deep = json.loads((pack / "depth" / "layers" / "deep.json").read_text())
    assert [row["text"] for row in deep["open_questions"]] == ["问题一", "问题二", "问题三"]
    assert deep["open_questions"][0]["anchor"] == "S01-U01"
    state = json.loads((pack / "depth" / "state.json").read_text())
    assert "research_sha" not in state
    assert "citations_sha" not in state
    assert state["layer_input_sha"] == {"skim": "m"}
    assert state["layer_model_sha"] == {"skim": "m"}
    assert state["layer_usage"] == {"skim": {}}
    assert state["layer_updated_at"] == {"skim": "t"}
    archive = Path(result["manifest"]["archive"])
    assert archive.is_file()
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert names == {
        "工作区/阅读/B0001/ch/01/depth/layers/research.json",
        "工作区/阅读/B0001/ch/01/depth/citations.json",
        "工作区/阅读/B0001/ch/01/export/research.html",
        "工作区/阅读/B0001/ch/01/manifest.json",
    }
    batch = Path(result["manifest"]["batch"])
    assert (batch / "manifest.json").is_file()
    assert (batch / "说明.md").is_file()


def test_dry_run_writes_nothing(tmp_path):
    _fixture(tmp_path)
    result = retire.apply(tmp_path, rerender=False, dry_run=True)
    assert result["dry_run"] is True
    assert not Path(result["manifest"]["archive"]).exists()
    assert (tmp_path / "工作区/阅读/B0001/ch/01/depth/layers/research.json").is_file()


def test_strip_legacy_research():
    html = (
        '<button class="tier-tab" data-tier-tab="research">研究</button>'
        '<tr data-tier-row="research"><td>研究</td></tr>'
        '<div data-tiers="skim deep research"></div>'
        '<div data-tiers="research"></div>'
        '<script>const STATS = {"skim":{"label":"速览"},"research":{"label":"研究"}};</script>'
        '<script>const TIERS = [{"key":"skim"},{"key":"research"}];</script>'
        "if (e.key >= '1' && e.key <= '4') {}"
    )
    out = retire._strip_legacy_research(html)
    assert "research" not in out
    assert '"skim"' in out
    assert 'data-tiers="skim deep"' in out
    assert "<tr" not in out
    assert "e.key <= '3'" in out
