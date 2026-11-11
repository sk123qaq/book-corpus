import importlib.util
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "depth_acceptance", Path(__file__).resolve().parents[1] / "tools" / "depth_acceptance.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _args(**kwargs):
    defaults = {"no_deep": False}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _touch_required(depth):
    for name in runner.REQUIRED_ARTIFACTS:
        path = depth / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def test_missing_artifacts_reports_enabled_layers(tmp_path):
    _touch_required(tmp_path)
    assert runner._missing_artifacts(tmp_path, _args()) == ["layers/deep.json"]
    assert runner._missing_artifacts(tmp_path, _args(no_deep=True)) == []


def test_missing_artifacts_treats_empty_file_as_missing(tmp_path):
    _touch_required(tmp_path)
    (tmp_path / "layers" / "deep.json").write_text("", encoding="utf-8")
    assert runner._missing_artifacts(tmp_path, _args()) == ["layers/deep.json"]


def test_failed_layer_maps_cli_messages():
    assert runner._failed_layer("速览审计未通过：x") == "skim"
    assert runner._failed_layer("深读审计未通过：x") == "deep"
    assert runner._failed_layer("模型输出被 max_tokens 截断") == "model"
    assert runner._failed_layer("unknown") is None


def test_atomic_write_replaces_without_leaving_temp_files(tmp_path):
    target = tmp_path / "report.json"
    runner._atomic_write(target, "first")
    runner._atomic_write(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert list(tmp_path.glob(".report.json.tmp-*")) == []
