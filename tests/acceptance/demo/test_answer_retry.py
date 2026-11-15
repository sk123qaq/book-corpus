#!/usr/bin/env python3
"""回答路径的重问验收：无效 JSON 要换一句提醒重问一次，超时不重问。

  python3 tests/acceptance/demo/test_answer_retry.py

离线跑，不调模型：call_pi 换成一队 (raw, error) 应答，记下它收到的提示词。
单书一次无效 JSON 曾直接变成失败卡（模型在 citations 数组后多打了一个引号），
第二次提示词必须是原提示词加一句提醒，不能原样重放。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEMO = os.path.join(REPO, "apps", "demo")
INSTANCE = os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/书库")
os.environ.setdefault("BOOKS_ROOT", INSTANCE)
for _p in (DEMO,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import answer

answer.CACHE_DIR = tempfile.mkdtemp(prefix="answer-retry-")
fails = []

QUESTION = "黑洞的潮汐力是怎么来的"
BOOK = "B0210"
PATH = "books/B0210/md/05_序幕黑洞之旅.md"
LINE = 119
TERMS = ["黑洞", "潮汐力"]

HBOOK = "B0206"
HPATH = "books/B0206/md/08_第6章黑洞.md"
HLINE = 45

BAD = (
    '{"answer":"判断\\n\\n## 节\\n正文","citations":[{"path":"books/x.md",'
    '"line":1,"quote":"引"}]","confidence":"high","not_found":false}'
)
UNREPAIRABLE = "抱歉，我先直接说明，这不是 JSON"
GOOD = '{"answer":"判断\\n\\n## 小标题\\n正文两句","citations":[],"confidence":"high","not_found":false}'
EMPTY = '{"answer":"","citations":[],"confidence":"high","not_found":false}'
TIMEOUT = "回答超时（240 秒）"


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def stub(replies):
    """把 call_pi 换成一队应答，返回它收到的调用记录列表。"""
    calls = []
    queue = list(replies)

    def fake(
        prompt,
        book,
        books=None,
        thinking=answer.DEFAULT_THINKING,
        rec=None,
        kind="answer",
        use_tools=True,
        on_event=None,
    ):
        calls.append({"prompt": prompt, "thinking": thinking, "use_tools": use_tools})
        return queue.pop(0) if queue else (GOOD, "")

    answer.call_pi = fake
    return calls


def run_single(replies):
    calls = stub(replies)
    out = answer.answer_question(QUESTION, BOOK, PATH, LINE, list(TERMS), use_cache=False, thinking="high")
    return out, calls


def run_multi(replies):
    sources = [
        {"book": BOOK, "title": "黑洞与时间弯曲", "path": PATH, "line": LINE},
        {"book": HBOOK, "title": "时间简史", "path": HPATH, "line": HLINE},
    ]
    calls = stub(replies)
    out = answer.answer_question(
        QUESTION, BOOK, PATH, LINE, list(TERMS), sources=sources, use_cache=False, thinking="high"
    )
    return out, calls


# ---- A：常见坏 JSON（] 后多引号）本地直接修复，无需重调模型 ----------
a, a_calls = run_single([(BAD, "")])
check(
    "A1 常见坏 JSON 本地确定性修复成功",
    a.get("ok") is True and bool(a.get("answer")),
    "ok=%s answer=%r" % (a.get("ok"), (a.get("answer") or "")[:30]),
)
check("A1 本地修复只调一次模型", len(a_calls) == 1, "calls=%d" % len(a_calls))

# ---- A2：不可本地修复的输出只走轻量 JSON 修复，不重跑整段检索 ----------
a2, a2_calls = run_single([(UNREPAIRABLE, ""), (GOOD, "")])
check(
    "A2 不可本地修复时轻量修复成功",
    a2.get("ok") is True and bool(a2.get("answer")),
    "ok=%s answer=%r" % (a2.get("ok"), (a2.get("answer") or "")[:30]),
)
check("A2 共调两次", len(a2_calls) == 2, "calls=%d" % len(a2_calls))
check(
    "A2 第二次只传原输出并用 thinking=off + 无工具修复",
    len(a2_calls) == 2
    and UNREPAIRABLE in a2_calls[1]["prompt"]
    and len(a2_calls[1]["prompt"]) < len(a2_calls[0]["prompt"])
    and a2_calls[1]["thinking"] == "off"
    and a2_calls[1]["use_tools"] is False,
    "calls=%r" % [(c["thinking"], c["use_tools"], len(c["prompt"])) for c in a2_calls],
)

# ---- B：两次都不可修复，仍然失败 -------------------------------------
b, b_calls = run_single([(UNREPAIRABLE, ""), (UNREPAIRABLE, "")])
check(
    "B 两次都非 JSON 报解析失败",
    b.get("ok") is False and "模型没有返回 JSON" in (b.get("error") or ""),
    "error=%r" % (b.get("error") or "")[:60],
)
check("B 也调了两次", len(b_calls) == 2, "calls=%d" % len(b_calls))

# ---- C：超时不重问 ---------------------------------------------------
c, c_calls = run_single([(None, TIMEOUT)])
check("C 超时原样报错", c.get("ok") is False and c.get("error") == TIMEOUT, "error=%r" % (c.get("error") or "")[:60])
check("C 只调一次", len(c_calls) == 1, "calls=%d" % len(c_calls))

# ---- D：多书空正文后重问（合法 JSON 但正文为空，才带全提示词重问） -----
d, d_calls = run_multi([(EMPTY, ""), (GOOD, "")])
check(
    "D 多书空正文后重问成功",
    d.get("mode") == "multi" and d.get("ok") is True and bool(d.get("answer")),
    "mode=%s ok=%s answer=%r" % (d.get("mode"), d.get("ok"), (d.get("answer") or "")[:30]),
)
check("D 共调两次", len(d_calls) == 2, "calls=%d" % len(d_calls))
check(
    "D 第二次保留全提示词并追加提醒",
    len(d_calls) == 2 and d_calls[1]["prompt"].startswith(d_calls[0]["prompt"]),
    "len0=%d len1=%d" % (len(d_calls[0]["prompt"]), len(d_calls[1]["prompt"]) if len(d_calls) > 1 else 0),
)

print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
if __name__ == "__main__":
    sys.exit(0 if not fails else 1)
