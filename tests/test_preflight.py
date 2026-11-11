import json

import pytest

from bookcorpus import preflight


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把 pi 的凭据与共享配置指到空目录，结果只由本用例显式设置的东西决定。"""
    auth = tmp_path / "auth.json"
    monkeypatch.setattr(preflight, "PI_AUTH", auth)
    monkeypatch.setattr(preflight, "MIMO_KEY", tmp_path / "mimo")
    monkeypatch.setattr(preflight, "PADDLE_KEY", tmp_path / "paddle")
    monkeypatch.setattr(preflight, "MINERU_KEY", tmp_path / "mineru")
    monkeypatch.setenv("PI_WEB_SEARCH_CONFIG", str(tmp_path / "web-search.json"))
    for key in (
        "OPENCODE_GO_API_KEY",
        "MINERU_API_KEY",
        "MINERU_TOKEN",
        "READING_SEARCH_PROVIDER",
        "READING_SEARCH_ENDPOINT",
        "READING_SEARCH_API_KEY",
        "READING_FETCH_PROVIDER",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_reading_model_from_env_or_pi_auth(tmp_path):
    status = preflight.check("reading.model")
    assert status.missing
    assert "OPENCODE_GO_API_KEY" in status.detail

    (tmp_path / "auth.json").write_text(json.dumps({"opencode-go": {"key": "sk-live"}}), encoding="utf-8")
    assert preflight.check("reading.model").state == preflight.OK


def test_summary_lists_every_missing_capability():
    text = preflight.summary()
    assert "四读的标注/结构模型" in text
