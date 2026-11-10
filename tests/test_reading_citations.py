"""Issue 6：检索、抓页与引用核验。测试不访问真实网络。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from email.message import Message

import pytest

from bookcorpus.reading.depth import citations as citations_module
from bookcorpus.reading.depth.citations import (
    CITATIONS_PROMPT_VERSION,
    CITATIONS_SCHEMA_VERSION,
    MAX_HITS_PER_QUERY,
    QUOTE_MISMATCH,
    STALE_WARNING,
    VERIFY_SYSTEM_PROMPT,
    BraveSearchProvider,
    FetchedPage,
    FetchError,
    FirecrawlPageFetcher,
    FirecrawlSearchProvider,
    HttpPageFetcher,
    HttpSearchProvider,
    NullSearchProvider,
    SearchError,
    SearchHit,
    SerperSearchProvider,
    StaticSearchProvider,
    TavilySearchProvider,
    audit_citations,
    build_citations,
    dotenv_path,
    fetcher_from_env,
    load_dotenv,
    plan_queries,
    provider_from_env,
    verify_candidates,
)
from bookcorpus.reading.depth.cli import _parser, _state_text
from bookcorpus.reading.depth.render import render_depth_chapter
from bookcorpus.reading.depth.skim import unsupported_skim


class FakeClient:
    def __init__(self, payloads=None):
        self.payloads = list(payloads or [])
        self.calls = []

    def complete_json(self, messages, *, model=None, effort=None):
        self.calls.append({"messages": messages, "model": model, "effort": effort})
        if not self.payloads:
            raise AssertionError("没有预置的模型响应")
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {}


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


class FakeResponse:
    def __init__(self, body=b"", *, status=200, content_type=None, charset=None):
        self.body = body
        self.status = status
        self.headers = Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        if charset:
            self.headers.set_param("charset", charset)
        self.closed = False

    def read(self, size=-1):
        if size is None or size < 0:
            return self.body
        return self.body[:size]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _page(url, text, *, title="页面", fetched_at="2026-01-01T00:00:00+00:00"):
    return FetchedPage(
        url=url,
        final_url=url,
        title=title,
        text=text,
        content_type="text/html; charset=utf-8",
        fetched_at=fetched_at,
    )


def _row(row_id, claim="判断", verdict="still", needed="", units=()):
    return {
        "id": row_id,
        "claim": claim,
        "verdict": verdict,
        "change": "",
        "needed": needed,
        "units": list(units),
        "blocks": [],
    }


def _research(rows, questions=()):
    return {"searched": False, "matrix": rows, "questions": list(questions), "notes": []}


def _citation(
    citation_id,
    *,
    url="https://a.example/1",
    quote="引文",
    accessed_at="2026-01-01T00:00:00+00:00",
    source_domain="a.example",
):
    return {
        "id": citation_id,
        "url": url,
        "title": "来源",
        "accessed_at": accessed_at,
        "quote": quote,
        "source_domain": source_domain,
        "page_sha256": "0" * 64,
        "query_id": "Q1",
    }


def _payload(claims, citations=(), **overrides):
    payload = {
        "schema": CITATIONS_SCHEMA_VERSION,
        "prompt_version": CITATIONS_PROMPT_VERSION,
        "searched": True,
        "provider": "static",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "queries": [],
        "citations": list(citations),
        "claims": list(claims),
    }
    payload.update(overrides)
    return payload


def _claim(claim_id, status, citation_ids=(), *, required=1):
    return {
        "claim_id": claim_id,
        "status": status,
        "required_sources": required,
        "found_sources": len(citation_ids),
        "citation_ids": list(citation_ids),
        "reason": status,
    }


def test_plan_queries_links_units_dedupes_clips_and_caps():
    rows = [
        _row("R01", "判断一", "changed", units=["U1"]),
        _row("R02", "判断二", "verify", units=["U2"]),
        _row("R03", "判断三", "caution", units=["U1"]),
        _row("R04", "判断四", "still", "需要数据", units=["U3"]),
        _row("R05", "判断五", "still", "", units=["U4"]),
        _row("R06", "判" * 200, "changed", units=["U5"]),
        _row("R07", "判断七", "verify", units=["U6"]),
        _row("R08", "判断八", "changed", units=["U7"]),
    ]
    questions = [
        {"id": "Q1", "query": "共同 查询", "why": "共同理由", "units": ["U1"]},
        {"id": "Q2", "query": "第二 查询", "why": "第二理由", "units": ["U2"]},
        {"id": "Q3", "query": "第三 查询", "why": "第三理由", "units": ["U3"]},
    ]
    targets = plan_queries(_research(rows, questions))

    assert [item["claim_id"] for item in targets] == ["R01", "R02", "R04", "R06", "R07"]
    assert [item["query_id"] for item in targets] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert targets[0] == {
        "query_id": "Q1",
        "claim_id": "R01",
        "query": "共同 查询",
        "reason": "共同理由",
    }
    assert targets[2]["query"] == "第三 查询"
    assert targets[2]["reason"] == "第三理由"
    assert len(targets[3]["query"]) == 160
    assert targets[3]["reason"] == "核对：" + "判" * 200
    assert targets[4]["query"] == "判断七"
    assert targets[4]["reason"] == "核对：判断七"
    assert len(plan_queries(_research(rows, questions), limit=2)) == 2


def test_plan_queries_dedupes_normalized_text_and_falls_back_to_still_rows():
    duplicate = _research(
        [
            _row("R01", "A  B", "changed"),
            _row("R02", "A B", "changed"),
        ]
    )
    assert [item["claim_id"] for item in plan_queries(duplicate)] == ["R01"]

    fallback = _research(
        [
            _row("R01", "判断一", "still", "补读"),
            _row("R02", "判断二", "still", "回到原文"),
            _row("R03", "判断三", "still", ""),
        ]
    )
    assert [item["claim_id"] for item in plan_queries(fallback)] == ["R01", "R02"]
    assert plan_queries(_research([_row("R01", "判断一", "still", "")])) == []


def test_null_provider_marks_all_not_searched_and_never_calls_model():
    research = _research(
        [
            _row("R01", "判断一", "still", "补读"),
            _row("R02", "判断二", "changed"),
        ]
    )
    client = FakeClient()
    payload = build_citations(
        client,
        research,
        provider=NullSearchProvider(),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert client.calls == []
    assert payload["schema"] == CITATIONS_SCHEMA_VERSION
    assert payload["prompt_version"] == CITATIONS_PROMPT_VERSION
    assert payload["searched"] is False
    assert payload["provider"] == "none"
    assert payload["generated_at"] == "2026-01-01T00:00:00+00:00"
    assert payload["queries"] == []
    assert payload["citations"] == []
    assert [claim["claim_id"] for claim in payload["claims"]] == ["R01", "R02"]
    assert {claim["status"] for claim in payload["claims"]} == {"not_searched"}
    assert [claim["required_sources"] for claim in payload["claims"]] == [1, 2]
    assert [claim["citation_ids"] for claim in payload["claims"]] == [[], []]
    assert {claim["reason"] for claim in payload["claims"]} == {"未连接检索提供方"}


def test_static_provider_caps_hits_and_build_dedupes_fetches_globally():
    provider = StaticSearchProvider(
        "static",
        {
            "q1": [SearchHit("https://a.example/1"), SearchHit("https://a.example/2")],
            "q2": [SearchHit("https://a.example/1"), SearchHit("https://b.example/3")],
        },
    )
    assert len(provider.search("q1", limit=2)) == 2
    research = _research(
        [
            _row("R01", "判断一", "changed", units=["U1"]),
            _row("R02", "判断二", "changed", units=["U2"]),
        ],
        [
            {"id": "Q1", "query": "q1", "why": "理由一", "units": ["U1"]},
            {"id": "Q2", "query": "q2", "why": "理由二", "units": ["U2"]},
        ],
    )
    fetcher = FakeFetcher(
        {
            "https://a.example/1": _page("https://a.example/1", "第一页有引文"),
            "https://a.example/2": _page("https://a.example/2", "第二页有引文"),
            "https://b.example/3": _page("https://b.example/3", "第三页有引文"),
        }
    )
    client = FakeClient(
        [
            {
                "results": [
                    {"citation_id": "C1", "supported": True, "quote": "引文"},
                    {"citation_id": "C2", "supported": True, "quote": "引文"},
                ]
            },
            {"results": [{"citation_id": "C1", "supported": True, "quote": "引文"}]},
        ]
    )

    payload = build_citations(client, research, provider=provider, fetcher=fetcher)

    assert fetcher.urls == ["https://a.example/1", "https://a.example/2", "https://b.example/3"]
    assert [query["fetched_count"] for query in payload["queries"]] == [2, 1]
    assert [claim["status"] for claim in payload["claims"]] == ["partial", "partial"]
    assert [citation["id"] for citation in payload["citations"]] == ["C1", "C2", "C3"]
    assert [citation["source_domain"] for citation in payload["citations"]] == [
        "a.example",
        "a.example",
        "b.example",
    ]


def test_build_fetches_at_most_ten_pages_and_records_empty_queries():
    rows = [_row(f"R{index:02d}", f"目标{index}", "changed") for index in range(1, 6)]
    provider = StaticSearchProvider(
        "static",
        {
            f"目标{index}": [
                SearchHit(f"https://s{index}.example/1"),
                SearchHit(f"https://s{index}.example/2"),
            ]
            for index in range(1, 6)
        },
    )
    fetcher = FakeFetcher(
        {
            f"https://s{index}.example/{page}": _page(f"https://s{index}.example/{page}", "页面正文")
            for index in range(1, 6)
            for page in (1, 2)
        }
    )
    client = FakeClient(
        [{"results": [{"citation_id": "C1", "supported": False}, {"citation_id": "C2", "supported": False}]}] * 5
    )

    payload = build_citations(client, _research(rows), provider=provider, fetcher=fetcher)

    assert len(fetcher.urls) == 10
    assert len(set(fetcher.urls)) == 10
    assert len(client.calls) == 5
    assert all(query["fetched_count"] == 2 for query in payload["queries"])


def test_build_reuses_fetched_page_for_later_queries():
    research = _research(
        [
            _row("R01", "判断一", "still", "查", units=["U1"]),
            _row("R02", "判断二", "still", "查", units=["U2"]),
        ],
        [
            {"id": "Q1", "query": "q1", "why": "理由一", "units": ["U1"]},
            {"id": "Q2", "query": "q2", "why": "理由二", "units": ["U2"]},
        ],
    )
    url = "https://a.example/shared"
    provider = StaticSearchProvider("static", {"q1": [SearchHit(url)], "q2": [SearchHit(url)]})
    fetcher = FakeFetcher({url: _page(url, "同一页支持两个判断")})
    client = FakeClient([{"results": [{"citation_id": "C1", "supported": True, "quote": "同一页支持两个判断"}]}] * 2)

    payload = build_citations(client, research, provider=provider, fetcher=fetcher)

    assert fetcher.urls == [url]
    assert [query["fetched_count"] for query in payload["queries"]] == [1, 0]
    assert [query["reused_count"] for query in payload["queries"]] == [0, 1]
    assert [claim["status"] for claim in payload["claims"]] == ["verified", "verified"]
    assert [claim["citation_ids"] for claim in payload["claims"]] == [["C1"], ["C1"]]
    assert len(payload["citations"]) == 1


def test_verify_accepts_only_verbatim_quote_and_degrades_fabrication():
    research = _research([_row("R01", "判断一", "changed", units=["U1"])])
    targets = plan_queries(research)
    pages = {"Q1": [_page("https://a.example/1", "页面里写着逐字引文，别的内容。")]}

    accepted = FakeClient(
        [{"results": [{"citation_id": "C1", "supported": True, "quote": "逐字引文", "reason": "匹配"}]}]
    )
    [record] = verify_candidates(accepted, research, targets, pages)
    assert record["supported"] is True
    assert record["quote"] == "逐字引文"
    assert record["reason"] == "匹配"

    fabricated = FakeClient([{"results": [{"citation_id": "C1", "supported": True, "quote": "页面没有的句子"}]}])
    [record] = verify_candidates(fabricated, research, targets, pages)
    assert record["supported"] is False
    assert record["reason"] == QUOTE_MISMATCH

    missing = FakeClient([{"results": []}])
    [record] = verify_candidates(missing, research, targets, pages)
    assert record["supported"] is False
    assert record["reason"] == "模型未返回该引文"


def test_verify_prompt_demands_json_boolean_schema():
    assert "JSON" in VERIFY_SYSTEM_PROMPT
    assert "布尔值" in VERIFY_SYSTEM_PROMPT


def test_verify_excerpt_includes_claim_terms_beyond_page_head():
    research = _research([_row("R01", "模型总参数为 552B", "changed", "核验 552B", units=["U1"])])
    targets = plan_queries(research)
    long_text = "导航" * 1200 + "该模型是 552B 参数的 MoE 模型。" + "页脚" * 1200
    pages = {"Q1": [_page("https://a.example/1", long_text)]}
    client = FakeClient(
        [{"results": [{"citation_id": "C1", "supported": True, "quote": "该模型是 552B 参数的 MoE 模型。"}]}]
    )
    [record] = verify_candidates(client, research, targets, pages)
    assert record["supported"] is True
    sent = client.calls[0]["messages"][-1]["content"]
    assert "552B" in sent
    assert len(sent) < 5000


def test_verify_retries_once_and_degrades_single_query_error():
    research = _research([_row("R01", "判断一", "changed", units=["U1"])])
    targets = plan_queries(research)
    pages = {"Q1": [_page("https://a.example/1", "逐字引文")]}
    retry = FakeClient(
        [
            ValueError("bad json"),
            {"results": [{"citation_id": "C1", "supported": True, "quote": "逐字引文"}]},
        ]
    )
    [record] = verify_candidates(retry, research, targets, pages)
    assert len(retry.calls) == 2
    assert record["supported"] is True

    broken = FakeClient([RuntimeError("模型不可用")])
    [record] = verify_candidates(broken, research, targets, pages)
    assert record["supported"] is False
    assert record["error"] == "模型不可用"

    no_pages = verify_candidates(FakeClient(), research, targets, {"Q1": []})
    assert no_pages == []


def test_build_verified_requires_independent_domains():
    research = _research(
        [
            _row("R01", "判断一", "still", "补查", units=["U1"]),
            _row("R02", "判断二", "changed", units=["U2"]),
            _row("R03", "判断三", "changed", units=["U3"]),
            _row("R04", "判断四", "still", "", units=["U4"]),
        ],
        [
            {"id": "Q1", "query": "q1", "why": "理由一", "units": ["U1"]},
            {"id": "Q2", "query": "q2", "why": "理由二", "units": ["U2"]},
            {"id": "Q3", "query": "q3", "why": "理由三", "units": ["U3"]},
        ],
    )
    provider = StaticSearchProvider(
        "static",
        {
            "q1": [SearchHit("https://a.example/1")],
            "q2": [SearchHit("https://b.example/2"), SearchHit("https://c.example/3")],
            "q3": [SearchHit("https://d.example/4")],
        },
    )
    fetcher = FakeFetcher(
        {
            "https://a.example/1": _page("https://a.example/1", "支持内容"),
            "https://b.example/2": _page("https://b.example/2", "支持内容"),
            "https://c.example/3": _page("https://c.example/3", "支持内容"),
            "https://d.example/4": _page("https://d.example/4", "支持内容"),
        }
    )
    client = FakeClient(
        [
            {"results": [{"citation_id": "C1", "supported": True, "quote": "支持内容"}]},
            {
                "results": [
                    {"citation_id": "C1", "supported": True, "quote": "支持内容"},
                    {"citation_id": "C2", "supported": True, "quote": "支持内容"},
                ]
            },
            {"results": [{"citation_id": "C1", "supported": True, "quote": "支持内容"}]},
        ]
    )

    payload = build_citations(client, research, provider=provider, fetcher=fetcher)
    claims = {claim["claim_id"]: claim for claim in payload["claims"]}

    assert claims["R01"]["status"] == "verified"
    assert claims["R01"]["found_sources"] == 1
    assert claims["R01"]["citation_ids"] == ["C1"]
    assert claims["R02"]["status"] == "verified"
    assert claims["R02"]["found_sources"] == 2
    assert claims["R02"]["citation_ids"] == ["C2", "C3"]
    assert claims["R03"]["status"] == "partial"
    assert claims["R03"]["found_sources"] == 1
    assert claims["R03"]["citation_ids"] == ["C4"]
    assert claims["R04"]["status"] == "not_searched"


def test_audit_rejects_missing_evidence_and_warns_stale():
    research = _research(
        [
            _row("R01", "判断一", "still"),
            _row("R02", "判断二", "changed"),
        ]
    )
    missing_quote = _payload(
        [_claim("R01", "verified", ["C1"])],
        [_citation("C1", quote="")],
    )
    result = audit_citations(missing_quote, research)
    assert result["status"] == "fail"
    assert any("缺少带 url/quote/accessed_at" in error for error in result["errors"])

    missing_url = _payload(
        [_claim("R01", "verified", ["C1"])],
        [_citation("C1", url="")],
    )
    result = audit_citations(missing_url, research)
    assert result["status"] == "fail"
    assert any("缺少带 url/quote/accessed_at" in error for error in result["errors"])

    missing_accessed = _payload(
        [_claim("R01", "verified", ["C1"])],
        [_citation("C1", accessed_at="")],
    )
    result = audit_citations(missing_accessed, research)
    assert result["status"] == "fail"
    assert any("缺少带 url/quote/accessed_at" in error for error in result["errors"])

    same_domain = _payload(
        [_claim("R02", "verified", ["C1", "C2"], required=2)],
        [
            _citation("C1", source_domain="a.example"),
            _citation("C2", source_domain="a.example"),
        ],
    )
    result = audit_citations(same_domain, research)
    assert result["status"] == "fail"
    assert any("只有 1 个独立来源，需要 2 个" in error for error in result["errors"])
    assert any("来源域名重复" in warning for warning in result["warnings"])

    stale = _payload(
        [
            _claim("R01", "verified", ["C1"], required=1),
            _claim("R02", "not_found", []),
        ],
        [_citation("C1", accessed_at="2020-01-01T00:00:00+00:00")],
    )
    result = audit_citations(stale, research, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert result["status"] == "warn"
    assert STALE_WARNING in result["warnings"]
    assert result["stats"]["verified"] == 1


def test_audit_requires_every_matrix_claim():
    research = _research([_row("R01", "判断一", "still"), _row("R02", "判断二", "still")])
    payload = _payload([_claim("R01", "not_searched", [])], searched=False, provider="none")
    result = audit_citations(payload, research)
    assert result["status"] == "fail"
    assert "引用账缺少判断：R02" in result["errors"]


def test_render_uses_citation_search_state_for_badge():
    blocks = [{"id": "B01", "kind": "paragraph", "text": "原文内容。"}]
    model = {
        "schema": 1,
        "uid": "B0003",
        "n": 6,
        "sections": [{"id": "S01", "title": "节", "unit_ids": ["U01"]}],
        "units": [{"id": "U01", "importance": "core", "source_blocks": ["B01"]}],
    }
    research = {
        "searched": False,
        "matrix": [_row("R01", "判断", "changed")],
        "questions": [],
        "notes": [],
    }
    citations = _payload([_claim("R01", "not_found", [])], searched=True, provider="static")
    markup = render_depth_chapter(
        {"uid": "B0003", "title": "书名", "author": "作者"},
        {"n": 6, "title": "第6章"},
        blocks,
        model,
        {},
        unsupported_skim("短章。"),
        research=research,
        citations=citations,
    )
    assert "研究议程 · 已检索" in markup
    assert "已连接检索提供方" in markup


def test_audit_checks_searched_flag_and_unknown_claims():
    research = _research([_row("R01", "判断一", "still")])
    unsearched = _payload(
        [_claim("R01", "verified", ["C1"])],
        [_citation("C1")],
        searched=False,
        provider="static",
    )
    result = audit_citations(unsearched, research)
    assert result["status"] == "fail"
    assert "未检索时不能有引用" in result["errors"]
    assert any("不能是 verified" in error for error in result["errors"])

    wrong_provider = _payload(
        [_claim("R01", "not_searched", [])],
        searched=True,
        provider="none",
    )
    result = audit_citations(wrong_provider, research)
    assert result["status"] == "fail"
    assert "已检索时 provider 不能是 none" in result["errors"]

    unknown = _payload([_claim("R99", "not_searched", [])], searched=False, provider="static")
    result = audit_citations(unknown, research)
    assert result["status"] == "fail"
    assert any("未知 claim id" in error for error in result["errors"])


def test_render_citation_refs_list_and_status_badges():
    blocks = [{"id": "B01", "kind": "paragraph", "text": "原文内容。"}]
    model = {
        "schema": 1,
        "uid": "B0003",
        "n": 6,
        "sections": [{"id": "S01", "title": "节", "unit_ids": ["U01"]}],
        "units": [{"id": "U01", "importance": "core", "source_blocks": ["B01"]}],
        "core_units": ["U01"],
    }
    research = {
        "searched": True,
        "matrix": [
            _row("R01", "已核验判断", "still"),
            _row("R02", "部分核验判断", "changed"),
            _row("R03", "未检索判断", "still"),
        ],
        "questions": [{"id": "Q1", "text": "开放问题？", "why": "理由", "query": "建议 查询", "units": []}],
        "notes": [
            {
                "id": "N01",
                "type": "open",
                "text": "议程笔记",
                "certainty": "unverified",
                "citations": [{"url": "https://note.example/1", "quote": "笔记来源"}],
            }
        ],
    }
    citation = _citation(
        "C1",
        url="https://a.example/1?x=1&y=2",
        quote="引文 <x>",
        accessed_at="2020-01-01T00:00:00+00:00",
    )
    citation["title"] = "来源 <b>"
    citations = {
        "citations": [citation],
        "claims": [
            _claim("R01", "verified", ["C1"]),
            _claim("R02", "partial", ["C1"], required=2),
            _claim("R03", "not_searched", []),
        ],
    }

    markup = render_depth_chapter(
        {"uid": "B0003", "title": "书名", "author": "作者"},
        {"n": 6, "title": "第6章"},
        blocks,
        model,
        {},
        unsupported_skim("短章。"),
        research=research,
        citations=citations,
    )

    assert '<sup class="cite-ref"><a href="#cite-C1" id="cite-ref-R01-1">[1]</a></sup>' in markup
    assert '<span class="cite-status" data-status="partial">部分核验</span>' in markup
    assert 'data-status="not_searched"' not in markup
    assert '<section class="citation-list"><h4>引用来源</h4><ol>' in markup
    assert '<li id="cite-C1">' in markup
    assert 'rel="noreferrer"' in markup
    assert 'target="_blank"' not in markup
    assert "来源 &lt;b&gt;" in markup
    assert "https://a.example/1?x=1&amp;y=2" in markup
    assert "引文 &lt;x&gt;" in markup
    assert markup.count("引文 &lt;x&gt;") == 1
    assert '<span class="cite-stale">超过 180 天</span>' in markup
    assert "研究议程" in markup
    assert "开放问题" in markup
    assert "建议查询" in markup
    assert "原文内容。" in markup
    for css_class in (".cite-ref", ".cite-status", ".citation-list", ".cite-meta", ".cite-stale"):
        assert css_class in markup


def test_cli_no_citations_flag_and_state_text_hash():
    args = _parser().parse_args(["generate", "--root", "books", "--uid", "B1", "--n", "1", "--no-citations"])
    assert args.no_citations is True
    model = {"prompt_version": "chapter-v1"}
    raw = {"source_sha256": "abc"}
    _, state_text = _state_text(model, raw, {}, "B1", 1)
    state = json.loads(state_text)
    assert "citations_sha" not in state
    assert "citations_prompt_version" not in state

    citations = {"prompt_version": CITATIONS_PROMPT_VERSION, "citations": []}
    _, state_text = _state_text(model, raw, {}, "B1", 1, citations=citations)
    state = json.loads(state_text)
    assert state["citations_prompt_version"] == CITATIONS_PROMPT_VERSION
    assert len(state["citations_sha"]) == 64


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "ftp://example.com/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://foo.local/",
    ],
)
def test_http_fetcher_rejects_local_and_unsafe_urls(url):
    with pytest.raises(FetchError):
        HttpPageFetcher().fetch(url)


def test_http_fetcher_rejects_redirect_to_private_address(monkeypatch):
    real_validate = citations_module._validated_url

    def validate(url):
        if url == "https://public.example/":
            return urllib.parse.urlsplit(url)
        return real_validate(url)

    calls = []
    response = FakeResponse(status=302, content_type="text/html")
    response.headers["Location"] = "http://127.0.0.1/"

    def redirect_open(url):
        calls.append(url)
        return response

    fetcher = HttpPageFetcher()
    fetcher._open = redirect_open  # type: ignore[method-assign]
    monkeypatch.setattr(citations_module, "_validated_url", validate)
    with pytest.raises(FetchError, match="不允许抓取内网"):
        fetcher.fetch("https://public.example/")
    assert calls == ["https://public.example/"]


def test_http_fetcher_rejects_oversize_and_non_html(monkeypatch):
    monkeypatch.setattr(citations_module, "_validated_url", lambda url: None)
    fetcher = HttpPageFetcher(max_bytes=5)
    fetcher._open = lambda url: FakeResponse(  # type: ignore[method-assign]
        b"123456", content_type="text/plain; charset=utf-8"
    )
    with pytest.raises(FetchError, match="页面超过大小上限"):
        fetcher.fetch("https://public.example/")

    fetcher = HttpPageFetcher()
    fetcher._open = lambda url: FakeResponse(  # type: ignore[method-assign]
        b"pdf", content_type="application/pdf"
    )
    with pytest.raises(FetchError, match="不是可读网页"):
        fetcher.fetch("https://public.example/")


def test_http_fetcher_extracts_html_text_and_title(monkeypatch):
    monkeypatch.setattr(citations_module, "_validated_url", lambda url: None)
    body = (
        "<html><head><title>标题</title><script>不要</script></head>"
        "<body><nav>菜单</nav><p>正文  内容</p><footer>页脚</footer></body></html>"
    ).encode()
    fetcher = HttpPageFetcher()
    fetcher._open = lambda url: FakeResponse(  # type: ignore[method-assign]
        body, content_type="text/html; charset=utf-8"
    )
    page = fetcher.fetch("https://public.example/")
    assert page.url == "https://public.example/"
    assert page.final_url == "https://public.example/"
    assert page.title == "标题"
    assert page.text == "正文 内容"
    assert "不要" not in page.text
    assert "菜单" not in page.text
    assert "页脚" not in page.text
    assert page.content_type.startswith("text/html")
    assert page.fetched_at.endswith("+00:00")


def test_http_search_provider_parses_fake_responses_without_network(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps({"results": [{"url": "https://a.example/1", "title": "标题", "snippet": "摘要"}]}).encode(),
            content_type="application/json",
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    provider = HttpSearchProvider("https://search.example/api", api_key="secret")
    hits = provider.search("查询", limit=2)

    assert hits == [SearchHit("https://a.example/1", "标题", "摘要")]
    request, timeout = requests[0]
    assert request.full_url == "https://search.example/api"
    assert json.loads(request.data.decode()) == {"query": "查询", "limit": 2}
    assert request.get_header("Authorization") == "Bearer secret"
    assert timeout == 10

    monkeypatch.setattr(
        citations_module.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps([{"url": "https://b.example/2"}]).encode(),
            content_type="application/json",
        ),
    )
    assert provider.search("查询", limit=MAX_HITS_PER_QUERY) == [SearchHit("https://b.example/2")]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "不是合法 JSON"),
        (b"{}", "缺少 results"),
        (b'{"results":"x"}', "缺少 results"),
        (b'{"results":[1]}', "不是对象"),
        (b'{"results":[{}]}', "缺少 url"),
    ],
)
def test_http_search_provider_rejects_malformed_payload(monkeypatch, body, message):
    monkeypatch.setattr(
        citations_module.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(body, content_type="application/json"),
    )
    with pytest.raises(SearchError, match=message):
        HttpSearchProvider("https://search.example/api").search("查询")


def test_http_search_provider_rejects_http_errors(monkeypatch):
    def raise_http(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "server error", Message(), None)

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", raise_http)
    with pytest.raises(SearchError, match="HTTP 500"):
        HttpSearchProvider("https://search.example/api").search("查询")


def test_load_dotenv_parses_quotes_and_does_not_override(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nexport FIRECRAWL_API_KEY='fc-test'\nREADING_SEARCH_PROVIDER=\"firecrawl\"\nKEEP_ME=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("READING_SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("KEEP_ME", "from-env")

    assert load_dotenv(path) == path
    assert os.environ["FIRECRAWL_API_KEY"] == "fc-test"
    assert os.environ["READING_SEARCH_PROVIDER"] == "firecrawl"
    assert os.environ["KEEP_ME"] == "from-env"
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("READING_SEARCH_PROVIDER", raising=False)


def test_explicit_missing_dotenv_disables_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv(citations_module.DOTENV_ENV, str(tmp_path / "missing.env"))
    assert dotenv_path() is None


def test_provider_from_env_defaults_and_errors(tmp_path, monkeypatch):
    monkeypatch.delenv(citations_module.SEARCH_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(citations_module.SEARCH_ENDPOINT_ENV, raising=False)
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(tmp_path / "missing.json"))
    assert isinstance(provider_from_env(), NullSearchProvider)

    monkeypatch.setenv(citations_module.SEARCH_PROVIDER_ENV, "http")
    monkeypatch.delenv(citations_module.SEARCH_ENDPOINT_ENV, raising=False)
    assert isinstance(provider_from_env(), NullSearchProvider)

    monkeypatch.setenv(citations_module.SEARCH_ENDPOINT_ENV, "https://search.example/api")
    assert isinstance(provider_from_env(), HttpSearchProvider)

    monkeypatch.setenv(citations_module.SEARCH_PROVIDER_ENV, "bogus")
    with pytest.raises(SearchError, match="未知检索提供方"):
        provider_from_env()


def test_verify_system_prompt_is_exact():
    assert VERIFY_SYSTEM_PROMPT == (
        "你是引用核验员。只有页面中出现能直接支持判断的逐字引文时才判 supported。"
        "不要补写页面没有的内容。只输出 JSON 对象，格式为 "
        '{"results":[{"citation_id":"C1","supported":true,"quote":"逐字引文","reason":"理由"}]}；'
        "supported 必须是布尔值 true 或 false。"
    )


def test_tavily_provider_posts_query_and_parses_results(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps({"results": [{"url": "https://a.example/1", "title": "标题", "content": "摘要"}]}).encode()
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    provider = TavilySearchProvider("https://api.tavily.com/search", "tvly-secret")
    hits = provider.search("查询", limit=2)

    assert hits == [SearchHit("https://a.example/1", "标题", "摘要")]
    request, timeout = requests[0]
    assert request.full_url == "https://api.tavily.com/search"
    assert json.loads(request.data.decode()) == {"query": "查询", "max_results": 2, "search_depth": "basic"}
    assert request.get_header("Authorization") == "Bearer tvly-secret"
    assert timeout == citations_module.SEARCH_TIMEOUT


def test_brave_provider_uses_header_and_web_results(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps(
                {"web": {"results": [{"url": "https://b.example/2", "title": "<b>标题</b>", "description": "摘要"}]}}
            ).encode()
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    provider = BraveSearchProvider("https://api.search.brave.com/res/v1/web/search", "brave-secret")
    hits = provider.search("查询", limit=2)

    assert hits == [SearchHit("https://b.example/2", "标题", "摘要")]
    request, _ = requests[0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    assert query == {"q": ["查询"], "count": ["2"]}
    assert request.get_header("X-subscription-token") == "brave-secret"


def test_serper_provider_posts_query_and_parses_organic(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps({"organic": [{"link": "https://c.example/3", "title": "标题", "snippet": "摘要"}]}).encode()
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    provider = SerperSearchProvider("https://google.serper.dev/search", "serper-secret")
    hits = provider.search("查询", limit=2)

    assert hits == [SearchHit("https://c.example/3", "标题", "摘要")]
    request, _ = requests[0]
    assert json.loads(request.data.decode()) == {"q": "查询", "num": 2}
    assert request.get_header("X-api-key") == "serper-secret"


def test_firecrawl_provider_parses_search_and_requires_success(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps(
                {
                    "success": True,
                    "data": {"web": [{"url": "https://d.example/4", "title": "标题", "description": "摘要"}]},
                }
            ).encode()
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    provider = FirecrawlSearchProvider("https://api.firecrawl.dev/v2/search", "fc-secret")
    hits = provider.search("查询", limit=2)

    assert hits == [SearchHit("https://d.example/4", "标题", "摘要")]
    request, _ = requests[0]
    assert request.full_url == "https://api.firecrawl.dev/v2/search"
    assert json.loads(request.data.decode()) == {"query": "查询", "limit": 2, "sources": ["web"]}
    assert request.get_header("Authorization") == "Bearer fc-secret"

    monkeypatch.setattr(
        citations_module.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(json.dumps({"success": False}).encode()),
    )
    with pytest.raises(SearchError, match="Firecrawl 返回失败响应"):
        provider.search("查询", limit=2)


def test_firecrawl_page_fetcher_returns_markdown(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "markdown": "# 正文",
                        "metadata": {"title": "标题", "sourceURL": "https://e.example/final"},
                    },
                }
            ).encode()
        )

    monkeypatch.setattr(citations_module.urllib.request, "urlopen", fake_urlopen)
    fetcher = FirecrawlPageFetcher("https://api.firecrawl.dev/v2/scrape", "fc-secret")
    page = fetcher.fetch("https://e.example/start")

    assert page.url == "https://e.example/start"
    assert page.final_url == "https://e.example/final"
    assert page.title == "标题"
    assert page.text == "# 正文"
    assert page.content_type.startswith("text/markdown")
    request, _ = requests[0]
    assert json.loads(request.data.decode()) == {
        "url": "https://e.example/start",
        "formats": ["markdown"],
        "onlyMainContent": True,
    }


def _clear_provider_env(monkeypatch):
    for name in (
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
        "FIRECRAWL_API_KEY",
        citations_module.SEARCH_API_KEY_ENV,
        citations_module.SEARCH_ENDPOINT_ENV,
        citations_module.PI_WEB_SEARCH_CONFIG_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_provider_from_env_reads_pi_web_search_config(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    path = tmp_path / "web-search.json"
    path.write_text(json.dumps({"firecrawlApiKey": "fc-config"}), encoding="utf-8")
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(path))
    monkeypatch.setenv(citations_module.SEARCH_PROVIDER_ENV, "firecrawl")

    provider = provider_from_env()

    assert isinstance(provider, FirecrawlSearchProvider)
    assert provider.endpoint == "https://api.firecrawl.dev/v2/search"
    assert provider.api_key == "fc-config"


def test_provider_from_env_auto_selects_first_configured(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    path = tmp_path / "web-search.json"
    path.write_text(json.dumps({"serperApiKey": "serper-config"}), encoding="utf-8")
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(path))
    monkeypatch.setenv(citations_module.SEARCH_PROVIDER_ENV, "auto")

    assert isinstance(provider_from_env(), SerperSearchProvider)


def test_provider_from_env_rejects_missing_key(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    path = tmp_path / "missing.json"
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(path))
    monkeypatch.setenv(citations_module.SEARCH_PROVIDER_ENV, "tavily")

    with pytest.raises(SearchError, match="tavily 缺少 API key"):
        provider_from_env()


def test_provider_and_fetcher_from_config_provider_fields(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.delenv(citations_module.SEARCH_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(citations_module.FETCH_PROVIDER_ENV, raising=False)
    path = tmp_path / "web-search.json"
    path.write_text(
        json.dumps(
            {
                "firecrawlApiKey": "fc-config",
                "searchProvider": "firecrawl",
                "fetchProvider": "firecrawl",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(path))

    provider = provider_from_env()
    fetcher = fetcher_from_env()

    assert isinstance(provider, FirecrawlSearchProvider)
    assert provider.api_key == "fc-config"
    assert isinstance(fetcher, FirecrawlPageFetcher)
    assert fetcher.api_key == "fc-config"


def test_fetcher_from_env_defaults_http_and_selects_firecrawl(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.delenv(citations_module.FETCH_PROVIDER_ENV, raising=False)
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(tmp_path / "missing.json"))
    assert isinstance(fetcher_from_env(), HttpPageFetcher)

    path = tmp_path / "web-search.json"
    path.write_text(json.dumps({"firecrawlApiKey": "fc-config"}), encoding="utf-8")
    monkeypatch.setenv(citations_module.PI_WEB_SEARCH_CONFIG_ENV, str(path))
    monkeypatch.setenv(citations_module.FETCH_PROVIDER_ENV, "firecrawl")

    fetcher = fetcher_from_env()
    assert isinstance(fetcher, FirecrawlPageFetcher)
    assert fetcher.endpoint == "https://api.firecrawl.dev/v2/scrape"
