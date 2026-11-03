#!/usr/bin/env python3
"""回答路径的重问验收：无效 JSON 要换一句提醒重问一次，超时不重问。

  python3 验收/test_answer_retry.py

离线跑，不调模型：call_pi 换成一队 (raw, error) 应答，记下它收到的提示词。
单书一次无效 JSON 曾直接变成失败卡（模型在 citations 数组后多打了一个引号），
第二次提示词必须是原提示词加一句提醒，不能原样重放。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.dirname(HERE)
INSTANCE = os.environ.get("BOOKS_ROOT") or os.path.expanduser("~/文档/book-corpus-local")
os.environ.setdefault("BOOKS_ROOT", INSTANCE)
for _p in (DEMO,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import answer  # noqa: E402

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

BAD = ('{"answer":"判断\\n\\n## 节\\n正文","citations":[{"path":"books/x.md",'
       '"line":1,"quote":"引"}]","confidence":"high","not_found":false}')
GOOD = ('{"answer":"判断\\n\\n## 小标题\\n正文两句","citations":[],'
        '"confidence":"high","not_found":false}')
EMPTY = '{"answer":"","citations":[],"confidence":"high","not_found":false}'
TIMEOUT = "回答超时（240 秒）"


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def stub(replies):
    """把 call_pi 换成一队应答，返回它收到的提示词列表。"""
    prompts = []
    queue = list(replies)

    def fake(prompt, book, books=None, thinking=answer.DEFAULT_THINKING):
        prompts.append(prompt)
        return queue.pop(0) if queue else (GOOD, "")

    answer.call_pi = fake
    return prompts


def run_single(replies):
    prompts = stub(replies)
    out = answer.answer_question(QUESTION, BOOK, PATH, LINE, list(TERMS),
                                 use_cache=False, thinking="high")
    return out, prompts


def run_multi(replies):
    sources = [{"book": BOOK, "title": "黑洞与时间弯曲", "path": PATH, "line": LINE},
               {"book": HBOOK, "title": "时间简史", "path": HPATH, "line": HLINE}]
    prompts = stub(replies)
    out = answer.answer_question(QUESTION, BOOK, PATH, LINE, list(TERMS),
                                 sources=sources, use_cache=False, thinking="high")
    return out, prompts


# ---- A：一次坏 JSON 后重问，第二次带上 JSON 提醒 ----------------------
a, a_prompts = run_single([(BAD, ""), (GOOD, "")])
check("A 坏 JSON 后重问成功", a.get("ok") is True and bool(a.get("answer")),
      "ok=%s answer=%r" % (a.get("ok"), (a.get("answer") or "")[:30]))
check("A 共调两次", len(a_prompts) == 2, "calls=%d" % len(a_prompts))
tail = ""
if len(a_prompts) == 2 and a_prompts[1].startswith(a_prompts[0]):
    tail = a_prompts[1][len(a_prompts[0]):]
check("A 第二次提示词是原提示词加 JSON 提醒",
      bool(tail.strip()) and "JSON" in tail, "附加=%r" % tail[:60])

# ---- B：两次都坏，仍然失败 -------------------------------------------
b, b_prompts = run_single([(BAD, ""), (BAD, "")])
check("B 两次坏 JSON 报解析失败",
      b.get("ok") is False and "模型没有返回 JSON" in (b.get("error") or ""),
      "error=%r" % (b.get("error") or "")[:60])
check("B 也调了两次", len(b_prompts) == 2, "calls=%d" % len(b_prompts))

# ---- C：超时不重问 ---------------------------------------------------
c, c_prompts = run_single([(None, TIMEOUT)])
check("C 超时原样报错", c.get("ok") is False and c.get("error") == TIMEOUT,
      "error=%r" % (c.get("error") or "")[:60])
check("C 只调一次", len(c_prompts) == 1, "calls=%d" % len(c_prompts))

# ---- D：多书空正文后重问 ---------------------------------------------
d, d_prompts = run_multi([(EMPTY, ""), (GOOD, "")])
check("D 多书空正文后重问成功",
      d.get("mode") == "multi" and d.get("ok") is True and bool(d.get("answer")),
      "mode=%s ok=%s answer=%r" % (d.get("mode"), d.get("ok"),
                                   (d.get("answer") or "")[:30]))
check("D 共调两次", len(d_prompts) == 2, "calls=%d" % len(d_prompts))

print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
sys.exit(0 if not fails else 1)
