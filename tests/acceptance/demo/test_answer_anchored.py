#!/usr/bin/env python3
"""点名书的两段式回答验收：前半只答点名的那本，后半只答其他书。

  BOOKS_ROOT=~/文档/book-corpus-data python -m pytest \
      tests/acceptance/demo/test_answer_anchored.py -q

离线跑，不调模型：call_pi 换成一个按提示词角色分流的假函数。假函数从提示词里的
证据窗口路径读一行真实原文当引文，所以回查、分书、排序都是真跑的，只有模型是假的。
"""
import json
import os
import re
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEMO = os.path.join(REPO, "apps", "demo")
INSTANCE = os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/文档/book-corpus-data")
os.environ.setdefault("BOOKS_ROOT", INSTANCE)
if DEMO not in sys.path:
    sys.path.insert(0, DEMO)

import answer  # noqa: E402

FOCUS = "B0019"
FOCUS_TITLE = "万历十五年"
FOCUS_PATH = "books/B0019/md/08_第三章世间已无张居正.md"
FOCUS_LINE = 18

OTHER = "B1114"
OTHER_TITLE = "张居正：全4册"
OTHER_PATH = "books/B1114/md/002_第一回病皇帝早朝生妄症美贵妃衔恨说娈童.md"
OTHER_LINE = 20

THIRD = "B0001"
THIRD_TITLE = "13.67"
THIRD_PATH = "books/B0001/md/02_黑与白之间的真实.md"
THIRD_LINE = 20

QUESTION = "张居正死后为什么会被清算"
TERMS = ["张居正", "万历", "清算"]

BLOCK_RE = re.compile(r"(books/B\d+/md/[^\s:]+\.md):(\d+)-(\d+)")
SKIP_RE = re.compile(r"^(\s*#|\s*!\[|\s*---|\s*[a-z_]+:)")


def _quote(path, start, end):
    """从提示词给出的窗口里挑一行真实原文当引文。"""
    try:
        with open(os.path.join(answer.ROOT, path), encoding="utf-8",
                  errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    for line in lines[max(0, start - 1):max(0, end)]:
        text = line.strip()
        if len(answer._norm(text)) < 8 or SKIP_RE.match(text):
            continue
        return text
    return ""


def _reply(prompt):
    """按角色拼回答：前半引点名书，后半引其他书，引文都来自窗口里的真实行。"""
    supplement = "第二部分" in prompt
    cites, seen = [], set()
    for path, start, end in BLOCK_RE.findall(prompt):
        book = answer.book_from_path(path)
        if path in seen:
            continue
        if supplement and book == FOCUS:
            continue
        if not supplement and book != FOCUS:
            continue
        quote = _quote(path, int(start), int(end))
        if not quote:
            continue
        seen.add(path)
        cites.append({"path": path, "line": int(start), "quote": quote})
        if len(cites) >= 2:
            break
    text = ("补充的判断\\n\\n## 其他书的说法\\n补充正文两句"
            if supplement else "前半的判断\\n\\n## 点名书的说法\\n前半正文两句")
    return json.dumps({"answer": text.replace("\\n", "\n"), "citations": cites,
                       "confidence": "high", "not_found": False},
                      ensure_ascii=False)


class Fake(object):
    """线程安全的假模型：按提示词里的角色说明决定返回哪一部分。"""

    def __init__(self, fail_supplement=False):
        self.fail_supplement = fail_supplement
        self.prompts = []
        self.lock = threading.Lock()

    def __call__(self, prompt, book, books=None, thinking=answer.DEFAULT_THINKING):
        with self.lock:
            self.prompts.append(prompt)
            fail = self.fail_supplement and "第二部分" in prompt
        if fail:
            return None, "回答超时（240 秒）"
        return _reply(prompt), ""

    def calls(self):
        with self.lock:
            return len(self.prompts)


def _sources():
    return [{"book": FOCUS, "title": FOCUS_TITLE, "path": FOCUS_PATH, "line": FOCUS_LINE},
            {"book": OTHER, "title": OTHER_TITLE, "path": OTHER_PATH, "line": OTHER_LINE}]


def _focus():
    return {"status": "exact", "book": FOCUS, "title": FOCUS_TITLE,
            "best_path": FOCUS_PATH, "best_line": FOCUS_LINE}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(answer, "CACHE_DIR", str(tmp_path / "answers"))
    yield


def _ask(fake, monkeypatch, sources, focus, path=FOCUS_PATH, line=FOCUS_LINE,
         use_cache=True):
    monkeypatch.setattr(answer, "call_pi", fake)
    return answer.answer_question(QUESTION, FOCUS, path, line, list(TERMS),
                                  sources=sources, focus=focus,
                                  use_cache=use_cache, thinking="high")


def test_two_parts_primary_first(monkeypatch):
    fake = Fake()
    out = _ask(fake, monkeypatch, _sources(), _focus())

    assert out["ok"] is True
    assert out["mode"] == "anchored"
    assert [p["kind"] for p in out["parts"]] == ["primary", "supplement"]
    assert out["parts"][0]["title"] == FOCUS_TITLE
    assert out["parts"][0]["citations"], "前半没有引文，分书断言就没意义"
    assert all(c["book"] == FOCUS for c in out["parts"][0]["citations"])
    assert all(c.get("book_title") == FOCUS_TITLE
               for c in out["parts"][0]["citations"]), "主书引文缺书名会显示《B0019》"
    assert all(c["book"] != FOCUS for c in out["parts"][1]["citations"])
    assert all(c.get("book_title") for c in out["parts"][1]["citations"])
    assert out["citations"][0]["book"] == FOCUS
    assert out["citations"][0].get("book_title") == FOCUS_TITLE
    assert "其他书的补充" in out["answer"]
    assert out["focus"] == {"book": FOCUS, "title": FOCUS_TITLE, "status": "exact"}
    assert [s["book"] for s in out["sources"]] == [FOCUS, OTHER]
    assert out["sources"][0]["title"] == FOCUS_TITLE
    assert out["supplement_error"] == ""
    # 两个角色各跑一次提示词，前半带 primary 说明、后半带 supplement 说明
    assert fake.calls() == 2
    assert any("第一部分" in p for p in fake.prompts)
    assert any(p.startswith(answer.ROLE_SUPPLEMENT.format(title=FOCUS_TITLE)[:20])
               for p in fake.prompts)


def test_only_focus_source_is_single(monkeypatch):
    fake = Fake()
    out = _ask(fake, monkeypatch, _sources()[:1], _focus())

    assert out["ok"] is True
    assert out["mode"] == "single"
    assert not out.get("parts")
    assert out["focus"] == {"book": FOCUS, "title": FOCUS_TITLE, "status": "exact"}
    # 单书模式的引文沿用旧形状（不带 book），落点用路径判
    assert out["citations"]
    assert all(c["path"].startswith("books/%s/" % FOCUS) for c in out["citations"])
    assert fake.calls() == 1
    assert "第一部分" in fake.prompts[0]


def test_missing_focus_source_does_not_fall_back(monkeypatch):
    fake = Fake()
    out = _ask(fake, monkeypatch, _sources(),
               {"status": "exact", "book": "B9999", "title": "不存在的书"},
               path="")

    assert out["ok"] is False
    error = out["error"] or ""
    assert "原文" in error and "locked" in error
    assert out["focus"]["book"] == "B9999"
    # 没拿检索第一名的书代答：一次模型都没调，也没有别的书进 sources
    assert fake.calls() == 0
    assert not out.get("sources")


def test_two_supplements_go_through_multi(monkeypatch):
    fake = Fake()
    sources = _sources() + [{"book": THIRD, "title": THIRD_TITLE,
                             "path": THIRD_PATH, "line": THIRD_LINE}]
    out = _ask(fake, monkeypatch, sources, _focus())

    assert out["mode"] == "anchored"
    assert [p["kind"] for p in out["parts"]] == ["primary", "supplement"]
    assert all(c["book"] != FOCUS for c in out["parts"][1]["citations"])
    assert [s["book"] for s in out["sources"]] == [FOCUS, OTHER, THIRD]
    # 两本及以上的补充走多书提示词，一次带上全部补充书，点名书不在里面
    supp = [p for p in fake.prompts if "第二部分" in p]
    assert len(supp) == 1
    assert OTHER in supp[0] and THIRD in supp[0] and FOCUS not in supp[0]


def test_supplement_failure_keeps_primary(monkeypatch):
    fake = Fake(fail_supplement=True)
    out = _ask(fake, monkeypatch, _sources(), _focus(), use_cache=False)

    assert out["ok"] is True
    assert out["mode"] == "anchored"
    assert [p["kind"] for p in out["parts"]] == ["primary"]
    assert out["supplement_error"]
    assert "回答超时" in out["error"]
    assert "其他书的补充" not in out["answer"]
    assert out["confidence"] == "medium"
    assert fake.calls() == 2

    # 整份结果不进缓存：再问一次（这次允许读缓存）仍然只有前半
    again = _ask(fake, monkeypatch, _sources(), _focus(), use_cache=True)
    assert again["ok"] is True
    assert again["supplement_error"]
    assert [p["kind"] for p in again["parts"]] == ["primary"]
    assert fake.calls() > 2


def test_followup_with_new_focus_does_not_pin_previous():
    """追问换了点名书：不钉回上一轮，也不把上一轮的点名抄过来。"""
    import server

    prev = {"n": 1, "question": "万历十五年怎么讲张居正",
            "trace": {"result": {"book": FOCUS, "book_title": FOCUS_TITLE,
                                 "path": FOCUS_PATH, "line": FOCUS_LINE,
                                 "title": "第三章世间已无张居正"},
                      "plan": {"ranking": [{"book": FOCUS, "title": FOCUS_TITLE}],
                               "chapters": []},
                      "focus": {"book": FOCUS, "title": FOCUS_TITLE,
                                "status": "exact"}},
            "answer": None}
    sess = {"turns": [prev]}
    rel = {"related": True, "reason": "追问"}
    data = {"focus": {"book": OTHER, "title": OTHER_TITLE, "status": "exact"},
            "result": {"book": OTHER, "book_title": OTHER_TITLE,
                       "path": OTHER_PATH, "line": OTHER_LINE, "title": "第一回"}}

    server._pin_followup(data, sess, 2, rel, None)
    assert "pin" not in data
    assert data["result"]["book"] == OTHER
    server._inherit_focus(data, sess, 2, rel)
    assert data["focus"] == {"book": OTHER, "title": OTHER_TITLE, "status": "exact"}

    # 没点名的一侧照旧：钉回上一轮的书，并把上一轮的点名抄成 inherited
    data2 = {"result": {"book": OTHER, "book_title": OTHER_TITLE,
                        "path": OTHER_PATH, "line": OTHER_LINE, "title": "第一回"}}
    server._pin_followup(data2, sess, 2, rel, None)
    assert data2["pin"]["book"] == FOCUS
    server._inherit_focus(data2, sess, 2, rel)
    assert data2["focus"]["book"] == FOCUS
    assert data2["focus"]["inherited"] is True


def test_followup_ambiguous_or_missing_does_not_pin_previous():
    """当前问点名了歧义/不存在的书：不钉回上一轮，也不继承上一轮的 exact。"""
    import server

    prev = {"n": 1, "trace": {"result": {"book": FOCUS, "book_title": FOCUS_TITLE,
                                         "path": FOCUS_PATH, "line": FOCUS_LINE},
                              "plan": {"ranking": [{"book": FOCUS, "title": FOCUS_TITLE}],
                                       "chapters": []},
                              "focus": {"book": FOCUS, "title": FOCUS_TITLE,
                                        "status": "exact", "query_title": FOCUS_TITLE}},
            "answer": None}
    sess = {"turns": [prev]}
    rel = {"related": True}
    for focus in ({"status": "ambiguous", "query_title": "同名书", "candidates": []},
                  {"status": "missing", "query_title": "不存在的书", "candidates": []}):
        data = {"focus": dict(focus),
                "result": {"book": OTHER, "book_title": OTHER_TITLE,
                           "path": OTHER_PATH, "line": OTHER_LINE, "title": "第一回"}}
        server._pin_followup(data, sess, 2, rel, None)
        server._inherit_focus(data, sess, 2, rel)
        assert "pin" not in data
        assert data["result"]["book"] == OTHER
        assert data["focus"]["status"] == focus["status"]
