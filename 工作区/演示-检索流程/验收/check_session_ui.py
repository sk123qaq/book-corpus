#!/usr/bin/env python3
"""02 会话页验收：真实会话恢复、落点开关、追问请求体、390 布局。

默认打 8765；自测时可 `--base http://127.0.0.1:8799`。
只读页面与会话接口，不调真实 /api/run 或 /api/answer；追问那一步用假 fetch 截请求体。
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shot import CDP, WS, eval_js, launch  # noqa: E402

SESSION = "7f0f8d8ba4c4"
fails = []
notes = []


def check(name, ok, detail=""):
    line = "[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else "")
    print(line)
    notes.append(line)
    if not ok:
        fails.append(name)


def wait_js(cdp, expr, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if eval_js(cdp, expr):
            return True
        time.sleep(0.15)
    return False


SNAP = r"""
(() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right};
  };
  return {
    rounds: qa('.round').length,
    current: q('.round.is-current') ? q('.round.is-current').dataset.turn : '',
    stats: qa('[data-stat]').map((e) => e.textContent.trim()),
    anchors: qa('.anchor').length,
    scopes: qa('.scope-row').length,
    scopeText: q('.scope-row') ? q('.scope-row').textContent.replace(/\s+/g, ' ').trim() : '',
    kicker: q('.kicker') ? q('.kicker').textContent.replace(/\s+/g, ' ').trim() : '',
    question: q('.question') ? q('.question').textContent.trim() : '',
    answerParas: qa('.answer p').length,
    answerText: q('.answer') ? q('.answer').textContent.replace(/\s+/g, ' ').trim().slice(0, 40) : '',
    meta: q('.answer-meta') ? q('.answer-meta').textContent.replace(/\s+/g, ' ').trim() : '',
    cites: qa('.ev').length,
    evOn: qa('.ev.is-on').length,
    pins: qa('.pin').map((e) => e.textContent.trim()),
    drawerCite: q('.dh-top .cite') ? q('.dh-top .cite').textContent.trim() : '',
    drawerBook: q('.dh-top .book') ? q('.dh-top .book').textContent.trim() : '',
    drawerCurrent: q('.current') ? q('.current').textContent.trim().slice(0, 30) : '',
    hint: q('.ask-hint') ? q('.ask-hint').textContent.replace(/\s+/g, ' ').trim() : '',
    placeholder: q('#q') ? q('#q').placeholder : '',
    send: q('#send') ? q('#send').textContent.trim() : '',
    newSession: q('#new-session') ? getComputedStyle(q('#new-session')).display : 'missing',
    drawerToggle: q('#drawer-toggle') ? getComputedStyle(q('#drawer-toggle')).display : 'missing',
    drawerOpen: q('#drawer') ? q('#drawer').classList.contains('is-open') : false,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    ask: rect(q('.ask')),
    roundsText: qa('.round').map((e) => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 90)),
  };
})()
"""

FAKE = r"""
(() => {
  window.__calls = [];
  window.__realFetch = window.fetch;
  const trace = {
    question: '带落点的问题',
    extract: {terms: ['测试'], groups: []},
    plan: {rounds: [], chapters: [], ranking: [], stop: 'cap'},
    stage_ms: {understand: 1, rounds: 1, rank: 1, cards: 1, result: 1},
    cards: {trigger: false, cards: [], reasons: []},
    result: {book: '人文社科/人类学与考古/乡土中国（2）',
             book_title: '乡土中国（2）', chapter: '无讼',
             path: '库/人文社科/人类学与考古/乡土中国（2）/md/12_无讼.md',
             line: 26, text: '模拟原文'},
    total_ms: 10, session_id: '7f0f8d8ba4c4', turn: 4,
    related: {related: true, reason: '模拟', against: [3], ms: 1,
              source: 'model', thinking: 'low'},
    session: {id: '7f0f8d8ba4c4', turns: []},
  };
  const answer = {
    ok: true, answer: '模拟回答', lead: '模拟回答', sections: [], dissent: '', gap: '',
    citations: [], confidence: 'high', not_found: false, error: '', ms: 10,
    thinking: 'high', cached: false, session_id: '7f0f8d8ba4c4', turn: 4,
    related: trace.related,
    history: {used: true, prev: 3, chars: 10, budget: 5000, chapters: 1, paragraphs: 1},
  };
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/run')) {
      window.__calls.push({kind: 'run', body: JSON.parse(opts.body)});
      return new Response(JSON.stringify(trace), {status: 200,
        headers: {'content-type': 'application/json'}});
    }
    if (u.includes('/api/answer')) {
      window.__calls.push({kind: 'answer', body: JSON.parse(opts.body)});
      return new Response(JSON.stringify(answer), {status: 200,
        headers: {'content-type': 'application/json'}});
    }
    return window.__realFetch(url, opts);
  };
  return true;
})()
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--port", type=int, default=9345)
    ap.add_argument("--out-dir", default=HERE)
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    url = base + "/session?session=" + SESSION
    proc, profile, ws_url = launch(1440, 900, "about:blank", a.port)
    try:
        cdp = CDP(WS(ws_url))
        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.call(method)
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": url})
        loaded = wait_js(cdp, "document.querySelectorAll('.round').length === 3"
                              " && !!document.querySelector('.answer p')", 25)
        check("恢复 3 轮会话", loaded)
        s0 = eval_js(cdp, SNAP)
        check("页头统计不是占位", all(v not in ("—", "") for v in s0["stats"]),
              ",".join(s0["stats"]))
        check("默认看第 3 轮", s0["current"] == "3" and s0["question"] != "",
              "current=%s q=%s" % (s0["current"], s0["question"][:20]))
        check("第 3 轮无落点框", s0["anchors"] == 0 and s0["scopes"] == 1
              and "独立问题" in s0["scopeText"],
              "anchors=%d scopes=%d %s" % (s0["anchors"], s0["scopes"], s0["scopeText"]))
        check("第 3 轮回答与依据", s0["answerParas"] > 0 and s0["cites"] > 0,
              "paras=%d cites=%d" % (s0["answerParas"], s0["cites"]))
        check("1440 无横向溢出", not s0["overflow"],
              "scrollWidth=%s innerWidth=%s" % (s0["scrollWidth"], s0["innerWidth"]))

        eval_js(cdp, "document.querySelector('.round[data-turn=\"2\"]').click(); true")
        ok2 = wait_js(cdp, "document.querySelector('.round.is-current')"
                          " && document.querySelector('.round.is-current').dataset.turn === '2'"
                          " && !!document.querySelector('.current')", 20)
        s1 = eval_js(cdp, SNAP)
        check("第 2 轮有落点框", ok2 and s1["anchors"] == 1 and s1["scopes"] == 0
              and "承接 01" in s1["kicker"],
              "anchors=%d scopes=%d kicker=%s" % (s1["anchors"], s1["scopes"], s1["kicker"]))
        check("右栏默认落在本轮落点", s1["drawerCite"] == "[1]"
              and "杨庆祥" in s1["drawerBook"] and "这一问的落点" in " ".join(s1["pins"]),
              "cite=%s book=%s pins=%s" % (s1["drawerCite"], s1["drawerBook"], s1["pins"]))
        check("五阶段可展开", eval_js(cdp, "(() => { const b=document.querySelector('.strip-bar');"
                                      " if(!b) return false; b.click();"
                                      " return document.querySelectorAll('.cells .cell').length===5; })()"))

        eval_js(cdp, "document.querySelector('.ev').click(); true")
        on = wait_js(cdp, "document.querySelectorAll('.ev.is-on').length === 1", 10)
        s2 = eval_js(cdp, SNAP)
        check("点依据设置下一问落点", on and "下一问挂在 [1]" in s2["hint"]
              and "下一问的落点" in " ".join(s2["pins"]),
              "evOn=%d hint=%s pins=%s" % (s2["evOn"], s2["hint"], s2["pins"]))
        check("占位跟着落点变", "就 [1] 这一段接着问" == s2["placeholder"], s2["placeholder"])
        eval_js(cdp, "document.querySelector('.ev').click(); true")
        off = wait_js(cdp, "document.querySelectorAll('.ev.is-on').length === 0", 10)
        s3 = eval_js(cdp, SNAP)
        check("再点一次取消落点", off and "不选依据" in s3["hint"]
              and "下一问的落点" not in " ".join(s3["pins"]),
              "evOn=%d hint=%s" % (s3["evOn"], s3["hint"]))

        eval_js(cdp, "document.querySelector('.ev').click(); true")
        wait_js(cdp, "document.querySelectorAll('.ev.is-on').length === 1", 10)
        wait_js(cdp, "!!document.querySelector('.current')", 10)
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path1440 = os.path.join(a.out_dir, "会话-1440x900.png")
        with open(path1440, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("1440 截图", os.path.getsize(path1440) > 20000,
              "%s %d B" % (path1440, os.path.getsize(path1440)))

        eval_js(cdp, FAKE)
        eval_js(cdp, "document.querySelector('#q').value='带落点的问题';"
                     "document.querySelector('#q').dispatchEvent(new Event('input', {bubbles:true}));"
                     "document.querySelector('#send').click(); true")
        done = wait_js(cdp, "document.querySelectorAll('.round').length === 4"
                            " && !!document.querySelector('.answer')"
                            " && document.querySelector('.answer').textContent.includes('模拟回答')", 15)
        s4 = eval_js(cdp, SNAP)
        calls = json.loads(eval_js(cdp, "JSON.stringify(window.__calls)"))
        run = next((c["body"] for c in calls if c["kind"] == "run"), {})
        ans = next((c["body"] for c in calls if c["kind"] == "answer"), {})
        check("追问追加第 4 轮", done and s4["rounds"] == 4 and s4["current"] == "4",
              "rounds=%d current=%s" % (s4["rounds"], s4["current"]))
        check("追问请求带会话、轮次和落点",
              run.get("session_id") == SESSION and run.get("turn") == 4
              and (run.get("anchor") or {}).get("line") == 972
              and "杨庆祥" in (run.get("anchor") or {}).get("path", "")
              and bool((run.get("anchor") or {}).get("quote")), json.dumps(run, ensure_ascii=False)[:180])
        check("回答请求带会话、轮次，且不加多书",
              ans.get("session_id") == SESSION and ans.get("turn") == 4
              and ans.get("sources") == [] and "杨庆祥" in ans.get("path", "")
              and ans.get("line") == 972,
              json.dumps(ans, ensure_ascii=False)[:160])
        check("上下文提示出现在回答里", "接上一问 · 上下文 10/5,000 字" in s4["meta"], s4["meta"])

        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        cdp.call("Page.navigate", {"url": url})
        wait_js(cdp, "document.querySelectorAll('.round').length === 3"
                     " && !!document.querySelector('.answer p')", 25)
        s5 = eval_js(cdp, SNAP)
        check("390 无横向溢出", not s5["overflow"] and s5["scrollWidth"] == 390,
              "scrollWidth=%s innerWidth=%s" % (s5["scrollWidth"], s5["innerWidth"]))
        check("390 composer 在视口内", s5["ask"] and s5["ask"]["top"] > 0
              and s5["ask"]["bottom"] <= 844,
              "ask=%s" % (s5["ask"],))
        check("390 有新会话入口", s5["newSession"] != "none", s5["newSession"])
        check("390 有原文开关", s5["drawerToggle"] != "none", s5["drawerToggle"])
        eval_js(cdp, "document.querySelector('.ev').click(); true")
        opened = wait_js(cdp, "document.querySelector('#drawer').classList.contains('is-open')", 10)
        s6 = eval_js(cdp, SNAP)
        check("390 点依据展开右栏", opened and s6["drawerOpen"], "open=%s" % s6["drawerOpen"])
        eval_js(cdp, "document.querySelector('#drawer-toggle').click(); true")
        closed = wait_js(cdp, "!document.querySelector('#drawer').classList.contains('is-open')", 10)
        check("390 右栏可收起", closed)
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path390 = os.path.join(a.out_dir, "会话-390x844.png")
        with open(path390, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("390 截图", os.path.getsize(path390) > 10000,
              "%s %d B" % (path390, os.path.getsize(path390)))

        cdp.wait_events(0.5)
        errs = cdp.errors()
        check("控制台 0 错误", not errs, "; ".join(errs)[:300])
        reqs = [((e.get("params") or {}).get("request") or {}).get("url")
                for e in cdp.events if e.get("method") == "Network.requestWillBeSent"]
        outside = [u for u in reqs if u and not u.startswith(base) and u != "about:blank"]
        check("无外部请求", not outside, ",".join(outside[:3]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
