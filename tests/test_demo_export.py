"""会话导出接口与引文书名补齐的验收。

HTTP 用例起一个真的 ThreadingHTTPServer，只把 session.SESS_DIR 指到临时目录，
不碰仓库里的会话缓存。
"""

import io
import json
import sqlite3
import sys
import threading
import zipfile
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "demo"))

import export_session
import server
import session

SID = "abcdef123456"


def sample_session(citations=None, sources=None, answer="开场判断。", trace_text="trace 文本"):
    turn = {
        "n": 1,
        "question": "万历十五年里万历为什么对张居正态度复杂",
        "answer": {
            "answer": answer,
            "citations": citations
            if citations is not None
            else [
                {
                    "path": "books/B0019/md/06_第一章.md",
                    "line": 221,
                    "quote": "原文引文",
                    "chapter": "第一章万历皇帝",
                    "verified": True,
                    "book": "B0019",
                    "book_title": "万历十五年",
                }
            ],
            "sources": sources if sources is not None else [{"book": "B0019", "title": "万历十五年"}],
        },
        "trace": {"plan": {"ranking": [{"book": "B0019", "title": "万历十五年"}]}, "result": {"text": trace_text}},
    }
    second = {
        "n": 2,
        "question": "接着问",
        "answer": {"answer": "", "citations": []},
        "trace": {"result": {"text": "只有 trace 文本"}},
    }
    return {"turns": [turn, second]}


def write_session(root, sess, sid=SID):
    sess = dict(sess, id=sid)
    (Path(root) / (sid + ".json")).write_text(json.dumps(sess, ensure_ascii=False), encoding="utf-8")


def get(addr, path):
    conn = HTTPConnection(addr[0], addr[1], timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, headers, body


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", httpd.server_address[1]), tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_markdown_contains_question_scope_answer_and_citations():
    doc = export_session.model(sample_session(), "2026-11-04 12:07")
    text = export_session.markdown(doc)

    assert text.startswith("# 万历十五年里万历为什么对张居正态度复杂")
    assert "导出时间：2026-11-04 12:07" in text
    assert "轮数：2" in text
    assert "**问题**：万历十五年里万历为什么对张居正态度复杂" in text
    assert "**范围**：《万历十五年》" in text
    assert "**回答**" in text
    assert "开场判断。" in text
    assert "**本轮依据**" in text
    assert "1. 《万历十五年》 · 第一章万历皇帝 · books/B0019/md/06_第一章.md:221" in text
    assert "> 原文引文" in text
    assert "只有 trace 文本" in text


def test_scope_reads_turn_sources_then_answer_sources():
    turn_level = sample_session(sources=[])
    turn_level["turns"][0]["sources"] = [{"book": "B0019", "title": "万历十五年"}]
    text = export_session.markdown(export_session.model(turn_level, "2026-11-04 12:07"))
    assert "**范围**：《万历十五年》" in text

    answer_level = sample_session(sources=[{"book": "B0019", "title": "万历十五年"}])
    text = export_session.markdown(export_session.model(answer_level, "2026-11-04 12:07"))
    assert "**范围**：《万历十五年》" in text


def test_scope_line_omitted_without_sources():
    # 页面在 sources 为空时写「没有选依据」，导出也要整行不写，不能拿 citation 书名补。
    sess = {
        "turns": [
            {
                "n": 1,
                "question": "没有范围的一问",
                "answer": {
                    "answer": "答",
                    "citations": [
                        {
                            "path": "books/B0019/md/06_第一章.md",
                            "line": 221,
                            "quote": "引文",
                            "chapter": "第一章万历皇帝",
                            "verified": True,
                            "book": "B0019",
                            "book_title": "万历十五年",
                        }
                    ],
                },
                "trace": {"result": {"text": ""}},
            }
        ]
    }
    text = export_session.markdown(export_session.model(sess, "2026-11-04 12:07"))

    assert "**范围**" not in text
    assert "**问题**：没有范围的一问" in text


def test_docx_unzips_and_carries_the_three_sections():
    quotes = [
        {
            "path": "books/B0019/md/06_第一章.md",
            "line": 221,
            "quote": "带控制符\x00的 <原文> & 引文",
            "chapter": "第一章万历皇帝",
            "verified": True,
            "book": "B0019",
            "book_title": "万历十五年",
        }
    ]
    data = export_session.docx(export_session.model(sample_session(citations=quotes), "2026-11-04 12:07"))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        document = zf.read("word/document.xml").decode("utf-8")
        styles = zf.read("word/styles.xml").decode("utf-8")

    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= names
    assert "问题" in document
    assert "回答" in document
    assert "本轮依据" in document
    assert "万历十五年" in document
    assert "&lt;原文&gt; &amp;" in document
    assert "\x00" not in document
    assert 'w:eastAsia="宋体"' in styles


def test_export_http_markdown_headers(http_server):
    addr, root = http_server
    write_session(root, sample_session())
    status, headers, body = get(addr, "/api/export?id=%s&format=md" % SID)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["content-type"].startswith("text/markdown")
    assert 'filename="session-%s.md"' % SID in headers["content-disposition"]
    assert "filename*=UTF-8''%E4%BC%9A%E8%AF%9D-" in headers["content-disposition"]
    assert "**问题**：" in text
    assert "**本轮依据**" in text


def test_export_http_docx_is_downloadable_zip(http_server):
    addr, root = http_server
    write_session(root, sample_session())
    status, headers, body = get(addr, "/api/export?id=%s&format=docx" % SID)

    assert status == 200
    assert headers["content-type"] == export_session.DOCX_CT
    assert 'filename="session-%s.docx"' % SID in headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        document = zf.read("word/document.xml").decode("utf-8")
    assert "问题" in document
    assert "回答" in document
    assert "本轮依据" in document


def test_export_rejects_bad_id_missing_session_and_bad_format(http_server):
    addr, root = http_server

    status, _headers, body = get(addr, "/api/export?id=bad&format=md")
    assert status == 400
    assert json.loads(body)["ok"] is False

    status, _headers, body = get(addr, "/api/export?format=md")
    assert status == 400
    assert json.loads(body)["ok"] is False

    status, _headers, body = get(addr, "/api/export?id=ffffffffffff&format=md")
    assert status == 404
    assert json.loads(body)["ok"] is False

    write_session(root, sample_session())
    status, _headers, body = get(addr, "/api/export?id=%s&format=pdf" % SID)
    assert status == 400
    assert "md" in json.loads(body)["error"]


def test_session_endpoint_fills_missing_book_title(http_server):
    addr, root = http_server
    path_only = {
        "path": "books/B0168/md/1002_第九百九十九章.md",
        "line": 91,
        "quote": "原文",
        "chapter": "第六卷",
        "verified": True,
    }
    sess = {
        "turns": [
            {
                "n": 1,
                "question": "凡人修仙传里南宫婉受伤",
                "answer": {"answer": "答", "citations": [path_only], "sources": []},
                "trace": {"plan": {"ranking": [{"book": "B0168", "title": "凡人修仙传"}]}, "result": {"text": ""}},
            }
        ]
    }
    write_session(root, sess)

    status, _headers, body = get(addr, "/api/session?id=%s" % SID)
    cite = json.loads(body)["session"]["turns"][0]["answer"]["citations"][0]

    assert status == 200
    assert cite["book"] == "B0168"
    assert cite["book_title"] == "凡人修仙传"
    assert cite["path"] == path_only["path"]
    assert cite["line"] == path_only["line"]
    assert cite["quote"] == path_only["quote"]
    assert cite["verified"] is True


def test_citation_title_falls_back_to_library_db(tmp_path, monkeypatch):
    db = tmp_path / "library.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE book (path TEXT, title TEXT)")
        con.execute("INSERT INTO book VALUES (?, ?)", ("B0168", "凡人修仙传（全本）"))
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(session, "LIBRARY_DB", str(db))

    turn = {
        "answer": {"citations": [{"path": "books/B0168/md/x.md", "line": 3, "quote": "q", "verified": True}]},
        "trace": {"result": {}},
    }
    session.fill_citations(turn)

    assert turn["answer"]["citations"][0]["book"] == "B0168"
    assert turn["answer"]["citations"][0]["book_title"] == "凡人修仙传（全本）"
    assert session._book_names(turn) == ["凡人修仙传（全本）"]
