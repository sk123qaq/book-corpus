#!/usr/bin/env python3
"""book_expand 的用例与缓存回放。模型全部用脚本桩，不联网。

  test_book_expand.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import book_expand as be

QUERIES = ["庚丁 辛壬 癸甲", "子卯 寅巳 午未", "申酉 戌亥 甲乙"]
GOOD = {"need_more": False,
        "summary": "这一章讲的是礼治秩序，靠传统与教化维持，不靠法律强制。",
        "evidence_lines": [40], "queries": QUERIES}


class Fail(Exception):
    pass


def want(cond, why):
    if not cond:
        raise Fail(why)


def scripted(replies):
    state = {"i": 0, "calls": 0}

    def call(prompt, timeout):
        state["calls"] += 1
        r = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        if isinstance(r, Exception):
            raise r
        return (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)), \
            {"input_tokens": 10, "output_tokens": 20}
    call.state = state
    return call


def make_book(tmp, n_lines=100, chars=200):
    md = os.path.join(tmp, "库", "测试", "md")
    os.makedirs(md, exist_ok=True)
    lines = ["第%d行 %s" % (i, "礼" * (chars // n_lines)) for i in range(1, n_lines + 1)]
    path = os.path.join(md, "ch.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return "库/测试/md/ch.md", path


def packet_case(level, max_chars, expect_chars, best=50, n_lines=100):
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, n_lines)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        p = be.make_packet("测试", rel, "第四章", lines, best, level, max_chars, "ok")
        want(p.level == level, "层级不对")
        want(len(p.window_text) <= expect_chars, "窗口超过上限 %d" % expect_chars)
        want(p.window_start <= best <= p.window_end, "最佳命中行不在窗口里")
        if level == 0:
            want(len(p.window_text) <= 400, "L0 应只给约 400 字")
        if level >= 1 and level <= 2:
            want(p.window_end - p.window_start >= 2, "窗口太窄")
        return p


def case_l0():
    packet_case(0, 400, 400)


def case_l1():
    p = packet_case(1, 2000, 2000)
    want(p.window_start > 1 and p.window_end < 100, "L1 应是围绕命中行的局部窗口")


def case_l2():
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, 400)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        lines[10] = "## 小节标题甲"
        p = be.make_packet("测试", rel, "第四章", lines, 50, 2, 6000, "ok")
        want(p.window_start == 1 or p.window_text.startswith("## 小节标题甲"),
             "L2 截断时应带最近的标题：%r" % p.window_text[:20])


def case_l3():
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, 100)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        got = be.targeted(lines, {"type": "grep", "terms": ["第50行"], "context": 2}, 4000)
        want(got is not None, "grep 应该有命中")
        ranges, text = got
        want(any(a <= 50 <= b for a, b in ranges), "grep 窗口应覆盖命中行")
        want(len(text) <= 4000, "L3 超过 4000 字")
        got2 = be.targeted(lines, {"type": "lines", "start": 10, "end": 20}, 4000)
        want(got2 and got2[0] == [(10, 20)], "行范围不对")


def case_l3_gap():
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, 200)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        ranges, text = be.targeted(
            lines, {"type": "grep", "terms": ["第10行", "第150行"], "context": 2}, 4000)
        want(len(ranges) == 2, "两次相距很远的命中应是两段窗口：%s" % (ranges,))
        want(not any(a <= 80 <= b for a, b in ranges), "中间的未给行不能算窗口")
        ok, reason = be.validate_reply(dict(GOOD, evidence_lines=[80]), ranges, text, 3)
        want(not ok and "越界" in reason, "未给的行应判越界：%s" % (reason,))


def case_context_cap():
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, 200)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        ranges, _ = be.targeted(
            lines, {"type": "grep", "terms": ["第100行"], "context": 20}, 4000)
        want(ranges == [(95, 105)], "context 应封顶 5 行：%s" % (ranges,))
        req = be.validate_reply({"need_more": True,
                                 "request": {"type": "grep", "terms": ["甲"], "context": 20}},
                                [], "", 3)[1]["request"]
        want(req["context"] == 5, "validate_reply 也要把 context 收到 5")


def case_gap_round():
    ask = {"need_more": True, "reason": "要两处",
           "request": {"type": "grep", "terms": ["第10行", "第150行"], "context": 2}}
    res, _ = run_one([ask, dict(GOOD, evidence_lines=[80])], n_lines=200, best=40)
    want(res.status == "partial" and "越界" in res.reason,
         "夹在两次 grep 之间的行不能当证据：%s" % res.reason)


def case_window_budget():
    with tempfile.TemporaryDirectory() as tmp:
        rel, _ = make_book(tmp, 2000)
        be.ROOT = tmp
        lines = be.read_lines(rel)
        ranges, text = be.targeted(
            lines, {"type": "grep", "terms": ["第%d行" % i for i in range(1, 2001, 200)]},
            400)
        want(len(text) <= 400, "L3 文本要按字封顶：%d" % len(text))


def run_one(replies, mode="default", n_lines=100, best=40, use_cache=False, tmp=None,
            reuse=False):
    tmp = tmp or tempfile.mkdtemp()
    rel = "库/测试/md/ch.md"
    if not reuse:
        rel, _ = make_book(tmp, n_lines)
    old = (be.ROOT, be.CACHE)
    be.ROOT, be.CACHE = tmp, os.path.join(tmp, "cache")
    call = scripted(replies)
    res = be.run_chapter("测试问题", "测试书", rel, "第四章", best, mode, call,
                         deadline=__import__("time").monotonic() + 300, use_cache=use_cache)
    be.ROOT, be.CACHE = old
    return res, call


def case_l4_refuse():
    res, call = run_one([{"need_more": True, "reason": "要全文", "request": {"type": "full"}}],
                        mode="max", n_lines=20000, best=10)
    # 20000 行 × 20 字，远超 20000 字
    want(res.status == "partial", "长章请求全文应 partial，实得 %s" % res.status)
    want("20000" in res.reason, "应说明拒绝理由：%s" % res.reason)


def case_lines_out():
    res, _ = run_one([{"need_more": True, "reason": "x",
                       "request": {"type": "lines", "start": 0, "end": 10}}], mode="deep")
    want(res.status == "partial" and "越界" in res.reason, "行范围越界应拒绝：%s" % res.reason)


def case_grep_empty():
    res, _ = run_one([{"need_more": True, "reason": "x",
                       "request": {"type": "grep", "terms": []}}], mode="deep")
    want(res.status == "partial" and "grep" in res.reason, "空 grep 应拒绝：%s" % res.reason)


def case_evidence_out():
    tmp = tempfile.mkdtemp()
    bad = dict(GOOD, evidence_lines=[9999])
    res, call = run_one([bad], use_cache=True, tmp=tmp)
    want(res.status == "partial" and "越界" in res.reason, "证据行越界应丢弃：%s" % res.reason)
    cache = os.path.join(tmp, "cache")
    want(not os.path.isdir(cache) or not os.listdir(cache), "越界回复不该写缓存")


def case_rounds():
    ask = {"need_more": True, "reason": "不够",
           "request": {"type": "lines", "start": 30, "end": 60}}
    res, call = run_one([ask, ask], mode="deep")
    want(res.status == "partial" and "轮数" in res.reason, "轮数用完应 partial：%s" % res.reason)


def case_cache_hit():
    tmp = tempfile.mkdtemp()
    res1, call1 = run_one([GOOD], use_cache=True, tmp=tmp)
    res2, call2 = run_one([GOOD], use_cache=True, tmp=tmp)
    want(res1.status == "ok" and res2.status == "cached", "第二次应命中缓存：%s" % res2.status)
    want(call2.state["calls"] == 0, "缓存命中不该调用模型")


def case_mtime():
    tmp = tempfile.mkdtemp()
    res1, call1 = run_one([GOOD], use_cache=True, tmp=tmp)
    rel = "库/测试/md/ch.md"
    os.utime(os.path.join(tmp, rel), (1, 1))
    res2, call2 = run_one([GOOD], use_cache=True, tmp=tmp, reuse=True)
    want(res2.calls == 1 and not res2.cached, "mtime 变了应重新调用：calls=%d" % res2.calls)


def case_empty():
    res, _ = run_one(["", ""])
    want(res.status == "partial" and "JSON" in res.reason, "空输出应丢弃：%s" % res.reason)


def case_short():
    res, _ = run_one([{"need_more": False, "summary": "太短", "evidence_lines": [40],
                       "queries": QUERIES}])
    want(res.status == "partial" and "过短" in res.reason, "过短摘要应丢弃：%s" % res.reason)


def case_queries():
    res, _ = run_one([dict(GOOD, queries=["只有一条"])])
    want(res.status == "partial" and "检索式" in res.reason, "少于三条应丢弃：%s" % res.reason)


def case_replay():
    tmp = tempfile.mkdtemp()
    r1, _ = run_one([GOOD], use_cache=True, tmp=tmp)
    r2, call2 = run_one([GOOD], use_cache=True, tmp=tmp, reuse=True)
    keep = ("summary", "evidence_lines", "queries", "level", "window_start", "window_end")
    a = json.dumps({k: r1.__dict__[k] for k in keep}, sort_keys=True, ensure_ascii=False)
    b = json.dumps({k: r2.__dict__[k] for k in keep}, sort_keys=True, ensure_ascii=False)
    want(a == b, "缓存回放与第一次不一致")
    want(call2.state["calls"] == 0, "回放应零调用")


def case_neighbors():
    with tempfile.TemporaryDirectory() as tmp:
        book = "文学/邻章"
        md = os.path.join(tmp, "库", book, "md")
        os.makedirs(md, exist_ok=True)
        for name in ("01_第一章.md", "02_第二章.md", "03_第三章.md"):
            with open(os.path.join(md, name), "w", encoding="utf-8") as fh:
                fh.write("\n".join("第%d行 正文" % i for i in range(1, 41)))
        old = be.ROOT
        be.ROOT = tmp
        try:
            prev = ("库/%s/md/01_第一章.md" % book, "第一章")
            nxt = ("库/%s/md/03_第三章.md" % book, "第三章")
            both = be.neighbor_block({"prev": prev, "next": nxt}, 2000)
            want("前一章" in both and "后一章" in both, "两边都该给：%r" % both[:80])
            only_next = be.neighbor_block({"next": nxt}, 2000)
            want("后一章" in only_next and "前一章" not in only_next,
                 "只有后一章时不该出现前一章")
            want(be.neighbor_block({}, 2000) == "", "两边都没有时应为空")
            lines = be.read_lines(prev[0])
            a, b, text = be.boundary_window(lines, "prev", 2000)
            want(b == len(lines), "前一章应取尾部")
            a2, b2, _ = be.boundary_window(lines, "next", 2000)
            want(a2 == 1, "后一章应取头部")
        finally:
            be.ROOT = old



def case_modes():
    want(be.MODES["default"]["calls"] == 9, "default calls 应为 9")
    want(be.MODES["deep"]["calls"] == 18, "deep calls 应为 18")
    want(be.MODES["max"]["calls"] == 24, "max calls 应为 24")
    want(be.MODES["max"]["books"] == 8 and be.MODES["max"]["chapters"] == 1
         and be.MODES["max"]["rounds"] == 3, "max 应为 8×1×3=24")


CASES = [
    ("L0 元数据", case_l0),
    ("L1 默认窗口", case_l1),
    ("L2 深挖窗口", case_l2),
    ("L3 定向读取", case_l3),
    ("L3 两段窗口无夹层", case_l3_gap),
    ("L3 context 封顶", case_context_cap),
    ("L3 夹层不能当证据", case_gap_round),
    ("L3 文本按字封顶", case_window_budget),
    ("L4 拒绝长章全文", case_l4_refuse),
    ("行范围越界", case_lines_out),
    ("grep 空词", case_grep_empty),
    ("evidence_lines 越界", case_evidence_out),
    ("need_more 超轮数", case_rounds),
    ("缓存命中", case_cache_hit),
    ("mtime 失效", case_mtime),
    ("空输出", case_empty),
    ("摘要过短", case_short),
    ("检索式少于三条", case_queries),
    ("邻章窗口与缺边", case_neighbors),
    ("缓存回放", case_replay),
    ("模式调用上限", case_modes),
]


def main():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("PASS %s" % name)
        except Fail as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    if failed:
        return 1
    print("PASS %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
