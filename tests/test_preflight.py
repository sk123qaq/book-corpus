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


def test_reading_search_needs_provider_and_key(monkeypatch):
    monkeypatch.setenv("READING_SEARCH_PROVIDER", "auto")
    status = preflight.check("reading.search")
    assert status.missing
    assert "TAVILY" in status.needs

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert preflight.check("reading.search").state == preflight.OK


def test_reading_search_http_needs_endpoint(monkeypatch):
    monkeypatch.setenv("READING_SEARCH_PROVIDER", "http")
    assert preflight.check("reading.search").missing
    monkeypatch.setenv("READING_SEARCH_ENDPOINT", "http://127.0.0.1:9/search")
    assert preflight.check("reading.search").state == preflight.OK


def test_reading_fetch_firecrawl_requires_key(monkeypatch):
    assert preflight.check("reading.fetch").state == preflight.OK
    monkeypatch.setenv("READING_FETCH_PROVIDER", "firecrawl")
    assert preflight.check("reading.fetch").missing
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    assert preflight.check("reading.fetch").state == preflight.OK


def test_require_reports_where_to_put_the_key(monkeypatch):
    monkeypatch.setenv("READING_SEARCH_PROVIDER", "auto")
    with pytest.raises(preflight.MissingConfigError) as exc:
        preflight.require(["reading.search"], note="研究层会降级")
    err = exc.value
    assert err.code == "missing_config"
    assert err.degradable is True
    payload = err.as_payload()
    assert payload["degradable"] is True
    assert payload["missing"][0]["key"] == "reading.search"
    assert ".env" in payload["missing"][0]["fix"]
    assert "研究层会降级" in str(err)


def test_allow_unverified_only_covers_search(monkeypatch):
    monkeypatch.setenv("READING_SEARCH_PROVIDER", "auto")
    preflight.require(["reading.search"], allow_unverified=True)
    with pytest.raises(preflight.MissingConfigError):
        preflight.require(["reading.model"], allow_unverified=True)
    monkeypatch.setenv("READING_FETCH_PROVIDER", "firecrawl")
    with pytest.raises(preflight.MissingConfigError):
        preflight.require(["reading.search", "reading.fetch"], allow_unverified=True)


def test_summary_lists_every_missing_capability():
    text = preflight.summary()
    assert "四读·检索 provider" in text
    assert "四读的标注/结构模型" in text
