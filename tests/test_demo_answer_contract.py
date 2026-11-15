"""Answer-stage contract: one writer, one shape, focus book first.

The old two-part anchored reply (primary + supplement) is gone; these tests pin
the pieces that replaced it without calling a model or reading the real library.
"""

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "demo"))

import trace

import answer
import model_call


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
    sources = [{"path": f"books/B{i}/md/a.md", "line": 1} for i in range(1, 11)]
    report = {}
    kept = answer._normalize_sources(sources, report=report)
    assert len(kept) == answer.MAX_SOURCES
    assert report["over_limit"] == 2
    assert [row["book"] for row in report["dropped"]] == ["B9", "B10"]


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
