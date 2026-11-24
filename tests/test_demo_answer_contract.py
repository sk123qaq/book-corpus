"""Answer-stage contract: one writer, one shape, focus book first.

The old two-part anchored reply (primary + supplement) is gone; these tests pin
the pieces that replaced it without calling a model or reading the real library.
"""

import importlib.util
import sqlite3
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "apps" / "demo"
DEMO_SERVER_MODULE = "demo_server_answer_contract_test"
sys.path.insert(0, str(DEMO))

import trace

import answer
import model_call


def _load_demo_server():
    cached = sys.modules.get(DEMO_SERVER_MODULE)
    if cached is not None:
        return cached
    demo_dir = str(DEMO)
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    spec = importlib.util.spec_from_file_location(DEMO_SERVER_MODULE, str(DEMO / "server.py"))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[DEMO_SERVER_MODULE] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(DEMO_SERVER_MODULE, None)
        raise
    return mod


def test_split_answer_folds_dissent_into_body():
    lead, sections, dissent, gap = answer._split_answer(
        "判断一句。\n\n## 谁先动手\n正文一。\n\n## 分歧\n书里一处说甲，一处说乙。"
    )
    assert lead == "判断一句。"
    assert dissent == ""
    assert gap == ""
    assert len(sections) == 1
    assert sections[0]["heading"] == "谁先动手"
    assert "书里一处说甲" in sections[0]["body"]


def test_shape_answer_keeps_body_without_heading():
    _, sections, _, _, _ = answer._shape_answer({"lead": "判断。", "sections": [{"heading": "", "body": "正文。"}]})
    assert sections == [{"heading": "", "body": "正文。"}]


def test_split_answer_caps_sections():
    body = "判断。\n\n" + "\n\n".join(f"## 第{i}节\n正文{i}。" for i in range(1, 10))
    _, sections, _, _ = answer._split_answer(body)
    assert len(sections) == 6


def test_clip_answer_keeps_body_under_limit():
    text = "甲" * (answer.MAX_ANSWER_CHARS + 100)
    out = answer._clip_answer(text)
    assert len(out) <= answer.MAX_ANSWER_CHARS
    assert out.endswith("…")


def test_shape_answer_reshapes_after_clip():
    body = "\n\n".join(f"## 第{i}节\n" + "乙" * 900 for i in range(1, 9))
    lead, sections, _, _, text = answer._shape_answer({"answer": "判断。\n\n" + body})
    assert len(text) <= answer.MAX_ANSWER_CHARS
    assert len(lead) + sum(len(s["heading"]) + len(s["body"]) + 2 for s in sections) <= answer.MAX_ANSWER_CHARS


def test_recap_prefers_model_items_and_caps_three():
    reply = {"recap": ["一" * 80, "二", "三", "四"]}
    assert answer._recap(reply, "", []) == ["一" * 60, "二", "三"]


def test_recap_falls_back_to_lead_and_headings():
    sections = [{"heading": "第二件", "body": "x"}, {"heading": "第三件", "body": "y"}]
    out = answer._recap({}, "第一句。第二句。", sections)
    assert out == ["第一句。", "第二件", "第三件"]


def test_multi_prompt_focus_line_and_no_two_part_roles():
    plans = [
        {"source": {"book": "B1", "title": "甲书"}, "titles": {}, "status": {}, "chapters": [], "chosen": []},
        {"source": {"book": "B2", "title": "乙书"}, "titles": {}, "status": {}, "chapters": [], "chosen": []},
    ]
    prompt = answer.build_multi_prompt("张居正死后为什么被清算", plans, [], "库内覆盖：张居正 3 段", "", "甲书")
    assert "用户点名的是《甲书》" in prompt
    assert "第一部分" not in prompt
    assert "第二部分" not in prompt
    assert "B1" in prompt
    assert "B2" in prompt
    assert "不超过 5000 字" in prompt


def test_multi_prompt_names_missing_focus_book():
    plans = [
        {"source": {"book": "B2", "title": "乙书"}, "titles": {}, "status": {}, "chapters": [], "chosen": []},
    ]
    prompt = answer.build_multi_prompt("问题", plans, [], "", "", "甲书")
    assert "用户点名的是《甲书》，但这本书没有可用原文" in prompt


def test_fallback_terms_strip_question_scaffolding():
    terms = trace._fallback_terms("详细介绍下马斯克是如何在内部政变中被PayPal扫地出门的？")
    assert "马斯克" in terms
    assert len(terms) >= 2
    assert all(len(item) <= 24 for item in terms)
    assert not any(item.startswith("详细介绍") for item in terms)


def test_fallback_terms_keep_short_subject():
    terms = trace._fallback_terms("乡土中国讲什么？")
    assert "乡土中国" in terms


def test_degraded_extract_is_cached(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, prompt, timeout=None, cwd=None, env=None):
        calls.append((argv, prompt))
        return 0, "这不是 JSON", "", False, 1.0

    monkeypatch.setattr(model_call, "_run", fake_run)
    monkeypatch.setattr(trace, "CACHE_DIR", str(tmp_path / "terms"))

    first = trace.extract_terms("甲午海战为什么失败", thinking="high", use_cache=True)
    assert first["source"] == "degraded"
    assert len(calls) == 4, "第一次切词退化后应当再试一次；每次先 JSON 再纯文本"
    assert first["terms"]
    assert first["groups"]

    second = trace.extract_terms("甲午海战为什么失败", thinking="high", use_cache=True)
    assert second["source"] == "cache"
    assert len(calls) == 4, "退化结果也要写缓存，第二次不该再调模型"


def test_multi_windows_drops_books_without_support(monkeypatch):
    seen = []

    def fake_windows(chosen, titles, status, **kwargs):
        book = next(iter(titles))
        seen.append((book, kwargs["total_chars"]))
        return [{"book": book, "path": "x", "start": 1, "end": 1, "text": ""}]

    monkeypatch.setattr(answer, "build_windows", fake_windows)
    plans = [
        {
            "source": {"book": "B1", "title": "甲书"},
            "titles": {},
            "status": {},
            "chosen": [{"score": None}],
            "chapters": [],
        },
        {
            "source": {"book": "B2", "title": "乙书"},
            "titles": {},
            "status": {},
            "chosen": [{"score": 9.0}],
            "chapters": [],
        },
        {
            "source": {"book": "B3", "title": "丙书"},
            "titles": {},
            "status": {},
            "chosen": [{"score": 0.5}],
            "chapters": [],
        },
    ]
    out = answer.multi_windows(plans, focus_book="B1")
    books = [w["book"] for w in out]
    assert "B1" in books
    assert "B2" in books
    assert "B3" not in books
    assert dict(seen)["B1"] > dict(seen)["B2"]


def test_multi_cache_key_includes_focus_book():
    sources = [{"book": "B1", "path": "books/B1/md/a.md", "line": 1}]
    one = answer.multi_cache_key("问题", sources, ["词"], focus_title="甲书", focus_book="B1")
    two = answer.multi_cache_key("问题", sources, ["词"], focus_title="甲书", focus_book="B2")
    assert one != two


def test_recap_dedupes_after_truncation():
    head = "甲" * 60
    assert answer._recap({"recap": [head + "一", head + "二"]}, "", []) == [head]


def test_split_answer_keeps_long_heading_starting_with_meta_word():
    _, sections, _, _ = answer._split_answer("判断。\n\n## 不同版本的结局\n正文。")
    assert [s["heading"] for s in sections] == ["不同版本的结局"]


def test_multi_windows_falls_back_when_no_book_has_support(monkeypatch):
    def fake_windows(chosen, titles, status, **kwargs):
        book = next(iter(titles))
        return [{"book": book, "path": "x", "start": 1, "end": 1, "text": ""}]

    monkeypatch.setattr(answer, "build_windows", fake_windows)
    plans = [
        {
            "source": {"book": book, "title": book},
            "titles": {},
            "status": {},
            "chosen": [{"score": None}],
            "chapters": [],
        }
        for book in ("B1", "B2")
    ]
    out = answer.multi_windows(plans)
    assert sorted(w["book"] for w in out) == ["B1", "B2"]


def test_focus_without_source_answers_from_other_books(monkeypatch):
    got = {}

    def fake_multi(question, sources, *args, **kwargs):
        got["sources"] = sources
        got.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(answer, "_normalize_sources", lambda xs, report=None: list(xs))
    monkeypatch.setattr(answer, "_focus_source", lambda *a, **k: None)
    monkeypatch.setattr(answer, "_answer_multi", fake_multi)
    out = answer.answer_question(
        "问题是？",
        "",
        "",
        1,
        terms=["问题"],
        sources=[{"book": "B2", "path": "books/B2/md/a.md", "line": 1, "title": "乙书"}],
        focus={"book": "B9", "title": "甲书", "status": "exact"},
        use_cache=False,
    )
    assert out["ok"] is True
    assert got["focus_title"] == "甲书"
    assert got.get("focus_book") is None
    assert [s["book"] for s in got["sources"]] == ["B2"]


def test_normalize_sources_records_over_limit_names(monkeypatch):
    monkeypatch.setattr(answer, "_inside_library", lambda rel: True)
    monkeypatch.setattr(answer, "book_from_path", lambda rel: rel.split("/")[1])
    monkeypatch.setattr(answer.defpass, "first_content_line", lambda path: 1)
    sources = [{"path": f"books/B{i}/md/a.md", "line": 1} for i in range(1, 13)]
    report = {}
    kept = answer._normalize_sources(sources, report=report)
    assert len(kept) == answer.MAX_SOURCES
    assert report["over_limit"] == 2
    assert [row["book"] for row in report["dropped"]] == ["B11", "B12"]


def test_pi_cmd_uses_no_skills():
    cmd = answer.pi_cmd("low")
    assert "--no-skills" in cmd
    assert "--skill" not in cmd
    assert "--thinking" in cmd
    assert cmd[cmd.index("--thinking") + 1] == "low"


def test_numbered_window_text_and_prompt_injection():
    plain_text = "第一句原文。\n\n第二句原文。"
    heading_text = "# 第一章\n正文第十行。\n正文第十一行。"
    w_plain = {
        "book": "B1",
        "title": "书一",
        "chapter": "第一章",
        "path": "books/B1/md/01.md",
        "start": 10,
        "end": 12,
        "chars": len(plain_text),
        "text": plain_text,
    }
    w_heading = {
        "book": "B1",
        "title": "书一",
        "chapter": "第一章",
        "path": "books/B1/md/01.md",
        "start": 10,
        "end": 11,
        "chars": len(heading_text),
        "text": heading_text,
    }
    assert answer._numbered_window_text(w_plain) == "L10: 第一句原文。\n\nL12: 第二句原文。"
    assert answer._numbered_window_text(w_heading) == "# 第一章\nL10: 正文第十行。\nL11: 正文第十一行。"

    single_prompt = answer.build_prompt("问一句？", "B1", [w_plain], [])
    assert "L10: 第一句原文。" in single_prompt
    assert "L12: 第二句原文。" in single_prompt
    assert w_plain["text"] == plain_text

    plans = [
        {"source": {"book": "B1", "title": "书一"}, "titles": {}, "status": {}, "chapters": [], "chosen": []},
        {"source": {"book": "B2", "title": "书二"}, "titles": {}, "status": {}, "chapters": [], "chosen": []},
    ]
    multi_prompt = answer.build_multi_prompt("问一句？", plans, [w_heading], "", "", "书一")
    assert "# 第一章\nL10: 正文第十行。\nL11: 正文第十一行。" in multi_prompt
    assert w_heading["text"] == heading_text


def test_verify_citations_strips_line_number_prefix(tmp_path, monkeypatch):
    rel = "books/B1/md/01.md"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    lines = [f"第{i}行填充内容。" for i in range(1, 13)]
    quote_body = "这一段原文完整记录了事件的起因与经过，可供核对。"
    lines[9] = quote_body
    lines[10] = "下一段原文补充了后来的结局与人物去向。"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(answer, "ROOT", str(tmp_path))
    monkeypatch.setattr(answer, "LIBRARY", str(tmp_path / "books"))

    windows = [
        {
            "book": "B1",
            "title": "书一",
            "chapter": "第一章",
            "path": rel,
            "start": 10,
            "end": 11,
            "text": quote_body,
        }
    ]
    cites = [
        {"path": rel, "line": 10, "quote": f"L10: {quote_body}"},
        {"path": rel, "line": 10, "quote": f"L10：{quote_body}"},
    ]
    kept_single, dropped_single = answer.verify_citations(cites[:1], windows, hit_path=rel, hit_line=10)
    assert dropped_single == 0
    assert len(kept_single) == 1
    assert kept_single[0]["line"] == 10
    assert kept_single[0]["quote"] == quote_body

    sources = [{"book": "B1", "title": "书一", "path": rel, "line": 10}]
    kept_multi, dropped_multi = answer._verify_multi(cites[1:2], windows, sources)
    assert dropped_multi == 0
    assert len(kept_multi) == 1
    assert kept_multi[0]["line"] == 10
    assert kept_multi[0]["quote"] == quote_body


def test_search_book_pushes_down_book_filter_and_matches_qsearch():
    import lib_fts_cjk as cjk
    import qsearch

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE para(
          id INTEGER PRIMARY KEY, path TEXT NOT NULL, book TEXT NOT NULL,
          line INTEGER NOT NULL, chars INTEGER NOT NULL, tok TEXT NOT NULL, text TEXT NOT NULL);
        CREATE VIRTUAL TABLE para_fts USING fts5(
          tok, content='para', content_rowid='id', tokenize="unicode61 remove_diacritics 0");
        """
    )
    rows = [
        (
            "books/B1/md/01.md",
            "B1",
            5,
            "阿Q因为摸了小尼姑的头皮又被赵太爷打嘴巴，这里是一段足够超过四十个字的详细记叙文本。",
        ),
        (
            "books/B1/md/02.md",
            "B1",
            18,
            "赵太爷认为阿Q不配姓赵，便当众打了他一记耳光，这段文字同样超过了四十个字符以通过筛选。",
        ),
        (
            "books/B2/md/01.md",
            "B2",
            7,
            "赵太爷和阿Q在另一本书里被当作文学典型分析，阿Q挨打的情节在这里反复出现并超过四十字。",
        ),
    ]
    for path, book, line, text in rows:
        tok = cjk.index_text(text)
        cur = con.execute(
            "INSERT INTO para(path, book, line, chars, tok, text) VALUES (?, ?, ?, ?, ?, ?)",
            (path, book, line, len(text), tok, text),
        )
        con.execute("INSERT INTO para_fts(rowid, tok) VALUES (?, ?)", (cur.lastrowid, tok))
    con.commit()

    terms = ["阿Q", "赵太爷"]
    expr = f"({cjk.query_expr('阿Q')}) OR ({cjk.query_expr('赵太爷')})"
    got = answer._search_book(con, "B1", expr, terms, limit=10)
    assert got
    assert all(r["book"] == "B1" for r in got)

    expected_all, _ = qsearch.search(con, expr, terms, [], limit=50, min_chars=40, rerank=False, pool=2000)
    expected_b1 = [r for r in expected_all if r["book"] == "B1"]
    keys = ("score", "coverage", "bm25", "kana", "line", "path", "book", "text")
    assert [{k: r[k] for k in keys} for r in got] == [{k: r[k] for k in keys} for r in expected_b1]


def test_answer_tools_executor_grep_and_read(tmp_path):
    import answer_tools

    md = tmp_path / "books" / "B001" / "md" / "01.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 标题\n第一行赵太爷。\n第二行阿Q挨打。\n第三行收尾。\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("机密内容\n", encoding="utf-8")

    ex = answer_tools.ToolExecutor(str(tmp_path), ["books/B001/md"])
    read_ok, read_err, _ = ex.execute("read", {"path": "books/B001/md/01.md", "offset": 2, "limit": 2})
    assert read_err is False
    assert "L2: 第一行赵太爷。" in read_ok
    assert "L3: 第二行阿Q挨打。" in read_ok
    assert "第四行" not in read_ok

    blocked, blocked_err, _ = ex.execute("read", {"path": "../secret.txt"})
    assert blocked_err is True
    assert "outside" in blocked or "失败" in blocked
    blocked_root, blocked_root_err, _ = ex.execute("read", {"path": "secret.txt"})
    assert blocked_root_err is True
    assert "outside" in blocked_root or "失败" in blocked_root

    grepped, grep_err, _ = ex.execute("book_grep", {"pattern": "阿Q", "path": "books/B001/md"})
    assert grep_err is False
    assert "01.md:3:第二行阿Q挨打。" in grepped


def test_direct_llm_stream_extractor_and_tool_loop(tmp_path, monkeypatch):
    import direct_llm
    import runlog

    assert len(direct_llm.SYSTEM_PROMPT.encode("utf-8")) < 2048

    ext = direct_llm._AnswerStreamExtractor()
    chunks = [
        '{"citations":[],"ans',
        'wer":"判断\\n\\n## 缘由\\n他说\\"姓赵',
        '\\"，随后\\u6328打。","confiden',
        'ce":"high","not_found":false}',
    ]
    deltas = "".join(ext.feed(c) for c in chunks)
    assert deltas == '判断\n\n## 缘由\n他说"姓赵"，随后挨打。'
    assert ext.text == deltas

    md = tmp_path / "books" / "B001" / "md" / "01.md"
    md.parent.mkdir(parents=True)
    md.write_text("第1行\n第2行阿Q因姓赵挨打。\n", encoding="utf-8")

    final_reply = (
        '{"answer":"阿Q因自称姓赵而被打。",'
        '"citations":[{"path":"books/B001/md/01.md","line":2,"quote":"第2行阿Q因姓赵挨打。"}],'
        '"confidence":"high","not_found":false}'
    )
    turns = [
        {
            "content": "",
            "reasoning": "先搜章节",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "book_grep", "arguments": '{"pattern":"姓赵","path":"books/B001/md"}'},
                }
            ],
            "usage": {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0, "cost": 0.0},
            "stop_reason": "tool_calls",
        },
        {
            "content": final_reply,
            "reasoning": "",
            "tool_calls": [],
            "usage": {"input": 120, "output": 45, "cache_read": 50, "cache_write": 0, "cost": 0.0},
            "stop_reason": "stop",
        },
    ]

    def fake_stream_turn(cfg, payload, *, session_id="", deadline=0.0, on_delta=None):
        idx = 0 if len(payload["messages"]) == 2 else 1
        t = turns[idx]
        if t["reasoning"] and on_delta:
            on_delta("reasoning", t["reasoning"], "")
        if t["content"] and on_delta:
            on_delta("content", t["content"][:25], "")
            on_delta("content", t["content"][25:], "")
        return t, ""

    monkeypatch.setattr(direct_llm, "_http_stream_turn", fake_stream_turn)
    runlog.configure(str(tmp_path / "logs"))
    rec = runlog.start("answer", question="阿Q为什么挨打")
    cfg = {
        "provider": "magpie",
        "model_ref": "magpie/opencode-go/deepseek-v4.1-flash",
        "model": "opencode-go/deepseek-v4.1-flash",
        "url": "http://127.0.0.1:1/v1/chat/completions",
        "api_key": "test-key",
        "headers": {},
        "send_session_affinity": True,
        "reasoning_effort": "low",
        "thinking": "low",
    }
    seen = []
    res = direct_llm.invoke_answer(
        "问题：阿Q为什么挨打",
        ["books/B001/md"],
        root=str(tmp_path),
        cfg=cfg,
        rec=rec,
        on_event=seen.append,
    )
    assert res.exit_code == 0
    assert res.mode == "direct"
    assert '"阿Q因自称姓赵而被打。"' in res.text
    assert [e["type"] for e in seen if e["type"] in ("tool_start", "tool_end", "answer_delta")][:3] == [
        "tool_start",
        "tool_end",
        "answer_delta",
    ]


def test_repair_reply_json_deterministic_variants():
    bad_tail_quote = (
        '{"answer":"结论","citations":[{"path":"books/B1/md/01.md","line":3,"quote":"原文"}]",'
        '"confidence":"high","not_found":false}'
    )
    parsed = answer.repair_reply_json(bad_tail_quote)
    assert isinstance(parsed, dict)
    assert parsed["answer"] == "结论"
    assert len(parsed["citations"]) == 1

    bad_inner_quote = '{"answer":"他说"你好"然后走了。","citations":[],"confidence":"medium","not_found":false}'
    parsed2 = answer.repair_reply_json(bad_inner_quote)
    assert isinstance(parsed2, dict)
    assert '他说"你好"' in parsed2["answer"]

    truncated = '{"answer":"半截内容","citations":[{"path":"books/B1/md/01.md","line":1,"quote":"引文"}]'
    parsed3 = answer.repair_reply_json(truncated)
    assert isinstance(parsed3, dict)
    assert parsed3["answer"] == "半截内容"


def test_server_api_answer_sse_and_json_compat(monkeypatch):
    import json
    import urllib.request
    from http.server import ThreadingHTTPServer

    server = _load_demo_server()

    def fake_answer_question(question, book, path, line, terms, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            cb({"type": "stage", "stage": "windows", "message": "正在截取原文窗口…"})
            cb({"type": "tool_start", "id": "t1", "name": "book_grep", "args": {}, "message": "正在检索原文…"})
            cb({"type": "answer_delta", "delta": "判断一句。", "text": "判断一句。"})
        return {
            "ok": True,
            "mode": "single",
            "answer": "判断一句。",
            "lead": "判断一句。",
            "sections": [],
            "citations": [],
            "passages": [],
            "ms": 12.0,
        }

    monkeypatch.setattr(server.answerer, "answer_question", fake_answer_question)
    monkeypatch.setattr(server, "_fill_citations", lambda info: info)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    import threading

    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    th.start()
    try:
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}/api/answer"
        payload = {"question": "问", "book": "B1", "path": "books/B1/md/01.md", "line": 1, "terms": ["问"]}

        req_json = urllib.request.Request(
            base,
            data=json.dumps(dict(payload, stream=False)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_json, timeout=10) as resp:
            assert "application/json" in resp.headers.get("Content-Type", "")
            body = json.loads(resp.read().decode("utf-8"))
            assert body["ok"] is True
            assert body["answer"] == "判断一句。"

        req_sse = urllib.request.Request(
            base,
            data=json.dumps(dict(payload, stream=True)).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req_sse, timeout=10) as resp:
            assert "text/event-stream" in resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")
        assert "event: stage\n" in raw
        assert "event: tool_start\n" in raw
        assert "event: answer_delta\n" in raw
        assert "event: done\n" in raw
        assert '"answer": "判断一句。"' in raw
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_direct_llm_pause_checkpoint_and_resume(monkeypatch, tmp_path):
    import threading

    import direct_llm

    seen_payloads = []
    cancel_ev = threading.Event()
    events = []

    def fake_turn(cfg, payload, *, session_id, deadline, on_delta=None, cancel_event=None):
        seen_payloads.append(payload)
        if len(seen_payloads) == 1:
            assert cancel_event is cancel_ev
            if on_delta:
                on_delta("content", '{"answer":"前段结论', "")
            cancel_ev.set()
            return {"content": '{"answer":"前段结论', "reasoning": ""}, "PAUSED"
        final = '{"answer":"前段结论，接续补完。","citations":[]}'
        if on_delta:
            on_delta("content", final, "")
        return {"content": final, "reasoning": "", "tool_calls": [], "usage": {}, "raw_stream": final}, ""

    monkeypatch.setattr(direct_llm, "_http_stream_turn", fake_turn)

    cfg = {"provider": "opencode-go", "model": "deepseek-v4.1-flash", "url": "http://127.0.0.1:9/v1/chat/completions"}
    cp = {}
    res1 = direct_llm.invoke_answer(
        "测试问题",
        ["books/B1/md"],
        root=tmp_path,
        cfg=cfg,
        use_tools=False,
        on_event=events.append,
        cancel_event=cancel_ev,
        checkpoint_out=cp,
    )
    assert res1.paused is True
    assert res1.error == "PAUSED"
    assert res1.text == "前段结论"
    assert cp["partial_text"] == "前段结论"
    assert cp["partial_raw"] == '{"answer":"前段结论'
    assert any(ev.get("type") == "paused" and ev.get("partial_text") == "前段结论" for ev in events)

    res2 = direct_llm.invoke_answer(
        "测试问题",
        ["books/B1/md"],
        root=tmp_path,
        cfg=cfg,
        use_tools=False,
        resume_state=cp,
    )
    assert res2.paused is False
    assert res2.exit_code == 0
    assert "接续补完" in res2.text
    resumed_msgs = seen_payloads[1]["messages"]
    assert any(m["role"] == "assistant" and '{"answer":"前段结论' in (m.get("content") or "") for m in resumed_msgs)
    assert resumed_msgs[-1]["role"] == "user"
    assert "暂停" in resumed_msgs[-1]["content"]


def test_session_pause_answer_persists_partial(monkeypatch, tmp_path):
    import session

    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path))
    sess = session.create()
    sess["turns"].append({"n": 1, "question": "问", "run_token": "tok1", "answer_attempt": 0})
    session.save(sess)

    assert session.begin_answer(sess["id"], 1, 1, 999999, token="tok1")
    assert session.pause_answer(sess["id"], 1, 1, partial="已流式半段", token="tok1")
    loaded = session.load(sess["id"])
    turn = loaded["turns"][0]
    assert turn["answer_state"] == "paused"
    assert turn["answer_partial"] == "已流式半段"


def test_server_api_answer_pause_and_resume_sse(monkeypatch, tmp_path):
    import json
    import threading
    import time
    import urllib.request
    from http.server import ThreadingHTTPServer

    import session

    server = _load_demo_server()

    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path / "sessions"))
    sess = session.create()
    sess["turns"].append(
        {
            "n": 1,
            "question": "暂停续跑",
            "run_token": "tok_pause",
            "answer_attempt": 0,
            "trace": {"result": {"book": "B1", "path": "books/B1/md/01.md", "line": 1}},
        }
    )
    session.save(sess)

    calls = []

    def fake_answer_question(question, book, path, line, terms, **kwargs):
        calls.append(kwargs)
        cb = kwargs.get("on_event")
        cancel_ev = kwargs.get("cancel_event")
        cp_out = kwargs.get("checkpoint_out")
        resume_state = kwargs.get("resume_state")
        if resume_state is None:
            if cb:
                cb({"type": "answer_delta", "delta": "半截", "text": "半截"})
            assert cancel_ev is not None
            assert cancel_ev.wait(timeout=3.0)
            if cp_out is not None:
                cp_out.update({"messages": [{"role": "user", "content": question}], "partial_text": "半截"})
            return {"ok": False, "paused": True, "partial_text": "半截", "error": "PAUSED"}
        assert resume_state.get("partial_text") == "半截"
        if cb:
            cb({"type": "answer_delta", "delta": "完整答案", "text": "完整答案"})
        return {
            "ok": True,
            "mode": "single",
            "answer": "完整答案",
            "lead": "完整答案",
            "sections": [],
            "citations": [],
            "passages": [],
            "ms": 15.0,
        }

    monkeypatch.setattr(server.answerer, "answer_question", fake_answer_question)
    monkeypatch.setattr(server, "_fill_citations", lambda info: info)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    th.start()
    try:
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"
        common = {
            "question": "暂停续跑",
            "book": "B1",
            "path": "books/B1/md/01.md",
            "line": 1,
            "terms": ["暂停"],
            "session_id": sess["id"],
            "turn": 1,
            "stream": True,
        }

        sse_result = {}

        def run_first_sse():
            req = urllib.request.Request(
                base + "/api/answer",
                data=json.dumps(common).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                sse_result["raw"] = resp.read().decode("utf-8")

        t1 = threading.Thread(target=run_first_sse, daemon=True)
        t1.start()
        deadline = time.monotonic() + 2.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
        pause_req = urllib.request.Request(
            base + "/api/answer/pause",
            data=json.dumps({"session_id": sess["id"], "turn": 1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(pause_req, timeout=5) as presp:
            pbody = json.loads(presp.read().decode("utf-8"))
        assert pbody["ok"] is True
        assert pbody["paused"] is True
        t1.join(timeout=5)
        assert "event: paused\n" in sse_result["raw"]
        assert '"partial_text": "半截"' in sse_result["raw"]
        assert session.load(sess["id"])["turns"][0]["answer_state"] == "paused"

        req2 = urllib.request.Request(
            base + "/api/answer",
            data=json.dumps(dict(common, fresh=True, resume=True)).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            raw2 = resp2.read().decode("utf-8")
        assert "event: done\n" in raw2
        assert '"answer": "完整答案"' in raw2
        assert session.load(sess["id"])["turns"][0]["answer_state"] == "ready"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_stream_citations_stage_emitted_once_after_answer_closes():
    import direct_llm

    events: list[dict] = []
    extractor = direct_llm._AnswerStreamExtractor()
    handler = direct_llm._make_turn_delta_handler(events.append, None, extractor=extractor)

    assert extractor.done is False
    handler("content", '{"answer":"总体结论。\\n\\n## 缘由\\n细节正文', "")
    assert extractor.done is False
    assert [e for e in events if e.get("type") == "answer_delta"]
    assert not [e for e in events if e.get("stage") == "citations"]

    # Closing quote arrives without any new decoded characters
    handler("content", '","recap":"摘要', "")
    assert extractor.done is True
    cit_stages = [e for e in events if e.get("type") == "stage" and e.get("stage") == "citations"]
    assert len(cit_stages) == 1
    assert "引文" in cit_stages[0]["message"]

    # Subsequent chunks in recap/citations must not re-emit citations stage
    handler("content", '","citations":[]}', "")
    assert len([e for e in events if e.get("stage") == "citations"]) == 1


def test_parse_extract_handles_smart_quotes_and_drops_generic_stopwords():
    raw = (
        "前导噪声 {broken} 真正输出："
        "{“terms”:[“宋朝”,“介绍”,“金融”,“原因”,“交子”],"
        "“groups”:[{“name”:“宋代”,“variants”:[“宋朝”,“北宋”]}]} 尾注"
    )
    terms, groups = trace._parse_extract(raw)
    assert terms == ["宋朝", "金融", "交子"]
    assert groups == [{"name": "宋代", "variants": ["宋朝", "北宋"]}]


def test_fallback_terms_splits_long_phrases_and_strips_particles():
    assert trace._fallback_terms("介绍下宋朝的金融史") == ["宋朝", "金融史"]
    assert trace._fallback_terms("东晋初年的兵力情况") == ["东晋初年", "兵力"]
    assert trace._fallback_terms("阿Q为什么挨打") == ["阿Q", "挨打"]
    assert trace._fallback_terms("《中国是部金融史》里写过交子吗？") == ["中国是部金融史", "交子"]


def test_extract_terms_rescues_reasoning_json_without_retry(monkeypatch, tmp_path):
    import json

    calls = []
    stream = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": (
                            "分析完毕，输出："
                            '{"terms":["阿Q","未庄","赵太爷","精神胜利法"],'
                            '"groups":[{"name":"阿Q","variants":["阿Q","阿桂"]}]}'
                        ),
                    }
                ],
            },
        }
    )

    def fake_run(argv, prompt, timeout=None, cwd=None, env=None):
        calls.append((argv, prompt))
        return 0, stream, "", False, 1.0

    monkeypatch.setattr(model_call, "_run", fake_run)
    monkeypatch.setattr(trace, "CACHE_DIR", str(tmp_path / "terms"))
    monkeypatch.setattr(trace, "INDEX_DB", str(tmp_path / "missing-index.db"))

    res = trace.extract_terms("阿Q为什么挨打", thinking="high", use_cache=False)
    assert len(calls) == 1, "thinking 里有合法 JSON 时不该再触发 retry"
    assert res["source"] == "model"
    assert res["terms"] == ["阿Q", "未庄", "赵太爷", "精神胜利法"]
    assert "抢救" in res["note"]


def test_extract_terms_no_retry_when_terms_ready_without_groups(monkeypatch, tmp_path):
    import json

    calls = []
    stream = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": '{"terms":["宋朝","金融","货币"]}'}],
            },
        }
    )

    def fake_run(argv, prompt, timeout=None, cwd=None, env=None):
        calls.append((argv, prompt))
        return 0, stream, "", False, 1.0

    monkeypatch.setattr(model_call, "_run", fake_run)
    monkeypatch.setattr(trace, "CACHE_DIR", str(tmp_path / "terms"))
    monkeypatch.setattr(trace, "INDEX_DB", str(tmp_path / "missing-index.db"))

    res = trace.extract_terms("介绍下宋朝的金融史", thinking="low", use_cache=False)
    assert len(calls) == 1, "只缺 groups 但已有 >=2 个 terms 时不该重试"
    assert res["source"] == "model"
    assert res["terms"] == ["宋朝", "金融", "货币"]
    assert res["groups"] == [
        {"name": "宋朝", "variants": ["宋朝"]},
        {"name": "金融", "variants": ["金融"]},
        {"name": "货币", "variants": ["货币"]},
    ]


def test_preflight_terms_splits_zero_hit_compounds(tmp_path):
    from bookcorpus.scripts import lib_fts_cjk as cjk

    db_path = tmp_path / "index.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE VIRTUAL TABLE para_fts USING fts5(text, tokenize='unicode61')")
    passages = [
        "北宋与宋朝建立后，四川出现最早的纸币交子。",
        "金融史研究关注宋代与货币体系的演变。",
        "未庄，阿Q常与赵太爷发生口角，只能靠精神胜利法自慰。",
        "地保与闲人议论挨打的场面。",
    ]
    for idx, p in enumerate(passages, 1):
        con.execute("INSERT INTO para_fts(rowid, text) VALUES (?, ?)", (idx, cjk.index_text(p)))
    con.commit()
    con.close()

    after, dropped = trace._preflight_terms(
        ["宋朝金融史", "宋代金融", "交子纸币", "金融制度"],
        groups=[],
        question="介绍下宋朝的金融史",
        index_db=str(db_path),
    )
    assert dropped == ["宋朝金融史", "宋代金融", "交子纸币", "金融制度"]
    assert after == ["宋朝", "金融史", "宋代", "金融", "交子", "纸币"]

    q_after, q_dropped = trace._preflight_terms(
        ["阿Q挨打", "精神胜利法", "挨打原因"],
        groups=[],
        question="阿Q为什么挨打",
        index_db=str(db_path),
    )
    assert q_dropped == ["阿Q挨打", "挨打原因"]
    assert q_after == ["阿Q", "挨打", "精神胜利法"]


def test_low_confidence_answer_is_not_cached(monkeypatch, tmp_path):
    import json

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(answer, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(answer, "ROOT", str(tmp_path))
    monkeypatch.setattr(answer, "_inside_library", lambda _rel: True)
    monkeypatch.setattr(answer.defpass, "first_content_line", lambda _p: 1)
    monkeypatch.setattr(answer, "_chapter_titles", lambda _b: ({}, {}))
    monkeypatch.setattr(
        answer,
        "pick_passages",
        lambda *_a, **_k: (
            [{"book": "B0001", "path": "books/B0001/md/01.md", "start": 1, "end": 5, "line": 1, "chars": 20}],
            [{"path": "books/B0001/md/01.md", "line": 1, "score": 1.0, "coverage": 1.0}],
        ),
    )
    monkeypatch.setattr(
        answer,
        "build_windows",
        lambda *_a, **_k: [
            {
                "book": "B0001",
                "path": "books/B0001/md/01.md",
                "start": 1,
                "end": 5,
                "chars": 20,
                "title": "第一章",
                "text": "正文。",
            }
        ],
    )
    monkeypatch.setattr(answer, "build_prompt", lambda *_a, **_k: "prompt")

    class _DummyCon:
        def close(self):
            pass

    monkeypatch.setattr(answer.sqlite3, "connect", lambda *_a, **_k: _DummyCon())

    stage = {"n": 0}

    def fake_request_reply(*_a, **_k):
        stage["n"] += 1
        return {"answer": "回答正文。", "confidence": "high", "not_found": False, "citations": []}, "raw", ""

    def fake_verify_citations(*_a, **_k):
        if stage["n"] == 1:
            return [], 1
        return [
            {"book": "B0001", "path": "books/B0001/md/01.md", "line": 1, "quote": "甲"},
            {"book": "B0001", "path": "books/B0001/md/01.md", "line": 2, "quote": "乙"},
        ], 0

    monkeypatch.setattr(answer, "_request_reply", fake_request_reply)
    monkeypatch.setattr(answer, "verify_citations", fake_verify_citations)

    res1 = answer.answer_question("为什么？", "B0001", "books/B0001/md/01.md", 1, ["词"], use_cache=True)
    assert res1["ok"] is True
    assert res1["confidence"] == "low"
    assert not list(cache_dir.glob("*.json"))

    # 即使磁盘上残留旧版低置信缓存文件，读取侧也必须拒绝命中
    key = answer.cache_key("为什么？", "B0001", "books/B0001/md/01.md", 1, ["词"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    stale_file = cache_dir / f"{key}.json"
    stale_file.write_text(json.dumps(res1, ensure_ascii=False), encoding="utf-8")

    res2 = answer.answer_question("为什么？", "B0001", "books/B0001/md/01.md", 1, ["词"], use_cache=True)
    assert stage["n"] == 2
    assert res2["confidence"] == "high"
    assert res2["cached"] is False

    res3 = answer.answer_question("为什么？", "B0001", "books/B0001/md/01.md", 1, ["词"], use_cache=True)
    assert stage["n"] == 2
    assert res3["cached"] is True
    assert res3["confidence"] == "high"


def test_server_api_answer_completed_result_not_discarded_by_late_pause(monkeypatch, tmp_path):
    import json
    import urllib.request
    from http.server import ThreadingHTTPServer

    import session

    server = _load_demo_server()

    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path / "sessions"))
    sess = session.create()
    sess["turns"].append(
        {
            "n": 1,
            "question": "压哨暂停",
            "run_token": "tok_late_pause",
            "answer_attempt": 0,
            "trace": {"result": {"book": "B1", "path": "books/B1/md/01.md", "line": 1}},
        }
    )
    session.save(sess)

    def fake_answer_question(question, book, path, line, terms, **kwargs):
        cancel_ev = kwargs.get("cancel_event")
        assert cancel_ev is not None
        # 模拟生成刚完成的一瞬间，暂停请求刚好 set 了 cancel_event 与 job["paused"]
        server.pause_active_answer(sid=sess["id"], n=1)
        return {
            "ok": True,
            "mode": "single",
            "answer": "已完整生成的答案",
            "lead": "已完整生成的答案",
            "sections": [],
            "citations": [],
            "passages": [],
            "ms": 10.0,
        }

    monkeypatch.setattr(server.answerer, "answer_question", fake_answer_question)
    monkeypatch.setattr(server, "_fill_citations", lambda info: info)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    th.start()
    try:
        port = httpd.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/answer",
            data=json.dumps(
                {
                    "question": "压哨暂停",
                    "book": "B1",
                    "path": "books/B1/md/01.md",
                    "line": 1,
                    "terms": ["暂停"],
                    "session_id": sess["id"],
                    "turn": 1,
                    "stream": True,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        assert "event: done\n" in raw
        assert "event: paused\n" not in raw
        assert '"answer": "已完整生成的答案"' in raw
        loaded = session.load(sess["id"])
        assert loaded is not None
        assert loaded["turns"][0]["answer_state"] == "ready"
        key = server.answer_job_key(sess["id"], 1, "tok_late_pause")
        with server.ANSWER_JOBS_LOCK:
            job = server.ANSWER_JOBS[key]
        assert job["paused"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_api_answer_deleted_session_midflight_returns_404(monkeypatch, tmp_path):
    import json
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import session

    server = _load_demo_server()

    monkeypatch.setattr(session, "SESS_DIR", str(tmp_path / "sessions"))
    sess = session.create()
    sess["turns"].append(
        {
            "n": 1,
            "question": "中途删会话",
            "run_token": "tok_del",
            "answer_attempt": 0,
            "trace": {"result": {"book": "B1", "path": "books/B1/md/01.md", "line": 1}},
        }
    )
    session.save(sess)

    def fake_answer_question(question, book, path, line, terms, **kwargs):
        assert session.delete(sess["id"]) == "deleted"
        return {
            "ok": True,
            "mode": "single",
            "answer": "会话已删后完成的回答",
            "lead": "会话已删后完成的回答",
            "sections": [],
            "citations": [],
            "passages": [],
            "ms": 10.0,
        }

    monkeypatch.setattr(server.answerer, "answer_question", fake_answer_question)
    monkeypatch.setattr(server, "_fill_citations", lambda info: info)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    th.start()
    try:
        port = httpd.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/answer",
            data=json.dumps(
                {
                    "question": "中途删会话",
                    "book": "B1",
                    "path": "books/B1/md/01.md",
                    "line": 1,
                    "terms": ["删"],
                    "session_id": sess["id"],
                    "turn": 1,
                    "stream": False,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        import pytest

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=10)
        assert exc_info.value.code == 404
        body = json.loads(exc_info.value.read().decode("utf-8"))
        assert body["error"] == "会话不存在"
    finally:
        httpd.shutdown()
        httpd.server_close()
