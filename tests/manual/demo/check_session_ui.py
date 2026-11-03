#!/usr/bin/env python3
"""02 会话页验收：真实会话恢复、落点开关、候选书多选、追问请求体、390 布局。

默认打 8765；自测时可 `--base http://127.0.0.1:8799`。
只读页面与会话接口，不调真实 /api/run 或 /api/answer；追问与重新回答那两步用假 fetch 截请求体。
会话 id 默认从 /api/sessions 动态选：一个 3 轮会话、一个最后一轮带多书 sources 的会话。
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shot import CDP, WS, eval_js, launch  # noqa: E402

SESSION = "7f0f8d8ba4c4"
SESSION_MULTI = "e4120ff4977d"
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
    answerBox: rect(q('.answer')),
    evBox: rect(q('.evidences')),
    reBtn: rect(q('.turn-reanswer')),
    reBtnText: q('.turn-reanswer') ? q('.turn-reanswer').textContent.trim() : '',
    reBtns: qa('.turn-reanswer').length,
    turnActions: qa('.turn-actions').length,
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
    railChips: qa('.round').map((e) => {
      const chip = e.querySelector('.meta .chip');
      return chip ? chip.textContent.trim() : '';
    }),
    books: qa('.books').length,
    picks: qa('.book-pick').length,
    picksOn: qa('.book-pick.is-on').length,
    picksLocked: qa('.book-pick.is-locked').length,
    picksOff: qa('.book-pick:disabled').length,
    booksHead: q('.books-head') ? q('.books-head').textContent.replace(/\s+/g, ' ').trim() : '',
    booksAll: q('.books-head [data-books-all]')
      ? q('.books-head [data-books-all]').textContent.trim() : '',
    booksNote: qa('.books > .books-note').map((e) => e.textContent.trim()),
    booksAct: q('.books-act') ? q('.books-act').textContent.replace(/\s+/g, ' ').trim() : '',
    booksActBtn: q('[data-reanswer]') ? q('[data-reanswer]').textContent.trim() : '',
    booksActOff: q('[data-reanswer]') ? q('[data-reanswer]').disabled : null,
    picksWrap: q('.books-picks') ? getComputedStyle(q('.books-picks')).flexWrap : '',
    picksScroll: q('.books-picks') ? getComputedStyle(q('.books-picks')).overflowX : '',
    stageRows: qa('.stage-row').length,
    stageValues: qa('.stage-row .v3-val').map((e) => e.textContent.trim()),
    stageTexts: qa('.stage-row').map((e) => e.textContent.replace(/\s+/g, ' ').trim()),
    stageOpen: qa('.stage-row.is-open').length,
    stageActive: qa('.strip .step.is-active').length,
    stageDone: qa('.strip .step.is-done').length,
    stripOpen: q('.stages') ? q('.stages').classList.contains('is-open') : false,
    historyRows: qa('.hist-row').length,
    historyCount: q('#history-count') ? q('#history-count').textContent.trim() : '',
    tabHistoryOn: q('#tab-history') ? q('#tab-history').classList.contains('is-on') : false,
    tabCurrentOn: q('#tab-current') ? q('#tab-current').classList.contains('is-on') : false,
    h2Size: q('.answer h2, .answer h3')
      ? getComputedStyle(q('.answer h2, .answer h3')).fontSize : '',
    pSize: q('.answer p') ? getComputedStyle(q('.answer p')).fontSize : '',
    starts: qa('.start').length,
    lastCards: qa('.last-card').length,
    histScroll: q('#hist-list') ? getComputedStyle(q('#hist-list')).overflowX : '',
    histWrap: q('#hist-list') ? getComputedStyle(q('#hist-list')).flexWrap : '',
    histFirstGroup: q('.hist-group') ? q('.hist-group').textContent.trim() : '',
    histFirstCurrent: qa('.hist-row').length
      ? qa('.hist-row')[0].classList.contains('is-current') : false,
    panelHistoryHidden: q('#panel-history') ? q('#panel-history').hidden : null,
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
    result: {book: 'B0606',
             book_title: '乡土中国（2）', chapter: '无讼',
             path: 'books/B0606/md/12_无讼.md',
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


REANSWER = r"""
(() => {
  window.__calls = [];
  window.__realFetch = window.fetch;
  window.__release = null;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/answer')) {
      const body = JSON.parse(opts.body);
      window.__calls.push({kind: 'answer', body});
      await new Promise((resolve) => { window.__release = resolve; });
      const sources = body.sources || [];
      const citations = sources.map((s, i) => ({
        path: s.path, line: s.line || 1, quote: '重答引文' + (i + 1),
        chapter: '重答章', verified: true, book: s.book, book_title: s.title,
      }));
      const answer = {
        ok: true, answer: '重新回答的正文', lead: '重新回答的正文', sections: [],
        dissent: '', gap: '', citations, confidence: 'high', not_found: false,
        error: '', ms: 10, thinking: 'high', cached: false,
        session_id: body.session_id, turn: body.turn,
        related: {related: true, reason: '模拟重答', against: [body.turn - 1], ms: 1},
        history: {used: true, prev: body.turn - 1, chars: 100, budget: 5000,
                  chapters: 1, paragraphs: 1},
      };
      return new Response(JSON.stringify(answer), {status: 200,
        headers: {'content-type': 'application/json'}});
    }
    return window.__realFetch(url, opts);
  };
  return true;
})()
"""


MULTI = r"""
(() => {
  window.__calls = [];
  window.__picked = '';
  window.__realFetch = window.fetch;
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/answer')) {
      const body = JSON.parse(opts.body);
      window.__calls.push({kind: 'answer', body});
      const sources = body.sources || [];
      const answer = {
        ok: true, mode: 'multi', answer: '模拟多书回答', lead: '模拟多书回答',
        sections: [], dissent: '', gap: '',
        citations: sources.map((s, i) => ({
          path: s.path, line: s.line,
          quote: i ? '其最著名的，即是提出了差序格局' : '差序格局是一圈圈推出去的波纹',
          chapter: i ? '杨庆祥 新时代文学写作景观' : '差序格局', verified: true,
          book: s.book, book_title: s.title,
        })),
        sources: sources, confidence: 'high', not_found: false, error: '', ms: 10,
        thinking: 'high', cached: false, session_id: body.session_id, turn: body.turn,
        related: {related: true, reason: '模拟', against: [1], ms: 1},
        history: {used: true, prev: 1, chars: 3625, budget: 10000, chapters: 2, paragraphs: 2},
      };
      return new Response(JSON.stringify(answer), {status: 200,
        headers: {'content-type': 'application/json'}});
    }
    return window.__realFetch(url, opts);
  };
  return true;
})()
"""

PICK_FREE = r"""
(() => {
  const pick = Array.from(document.querySelectorAll('.book-pick')).find(
    (el) => !el.classList.contains('is-on') && !el.classList.contains('is-locked'));
  if (!pick) return '';
  window.__picked = pick.dataset.bookPick;
  pick.click();
  return pick.dataset.bookPick;
})()
"""


def get_html(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return r.status, r.read().decode("utf-8", "replace")


def get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def pick_sessions(base):
    """从 /api/sessions 选一个 3 轮会话和一个多书会话，不再依赖固定 id。

    3 轮会话优先选「第 2 轮有落点、第 3 轮无落点」的，UI 的落点开关才可验证；
    多书会话优先选最后一轮确实带 2 个以上 sources 的。
    """
    status, data = get_json(base, "/api/sessions")
    if status != 200 or data.get("ok") is not True:
        raise SystemExit("会话列表不可用：HTTP %s" % status)
    rows = data.get("sessions") or []
    three = ""
    for row in rows:
        if row.get("turns") != 3:
            continue
        sid = row.get("id") or ""
        if not sid:
            continue
        st, sess = get_json(base, "/api/session?id=" + sid)
        turns = (sess.get("session") or {}).get("turns") or []
        if st != 200 or len(turns) != 3:
            continue
        anchors = [t.get("anchor") or {} for t in turns]
        if anchors[1].get("path") and not anchors[2].get("path"):
            three = sid
            break
        if not three:
            three = sid
    multi = ""
    for row in rows:
        if (row.get("turns") or 0) < 2:
            continue
        sid = row.get("id") or ""
        if not sid:
            continue
        st, sess = get_json(base, "/api/session?id=" + sid)
        turns = (sess.get("session") or {}).get("turns") or []
        if st != 200 or len(turns) < 2:
            continue
        ans = turns[-1].get("answer") or {}
        if ans.get("mode") == "multi" and len(ans.get("sources") or []) >= 2:
            multi = sid
            break
    if not three:
        raise SystemExit("没有找到可用的 3 轮会话；先用 check_session.py 生成一轮")
    if not multi:
        raise SystemExit("没有找到带 2 本以上 sources 的多书会话；先用 check_session_live.py 生成")
    return three, multi


def main(argv=None):
    global SESSION, SESSION_MULTI
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--port", type=int, default=9345)
    ap.add_argument("--out-dir", default=HERE)
    ap.add_argument("--session", default="", help="3 轮会话 id；默认从 /api/sessions 选")
    ap.add_argument("--multi-session", default="", help="多书会话 id；默认从 /api/sessions 选")
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    auto_three, auto_multi = pick_sessions(base)
    SESSION = a.session or auto_three
    SESSION_MULTI = a.multi_session or auto_multi
    _, three_payload = get_json(base, "/api/session?id=" + SESSION)
    three_turns = (three_payload.get("session") or {}).get("turns") or []
    anchor2 = (three_turns[1].get("anchor") or {}) if len(three_turns) > 1 else {}
    turn2_mode = ((three_turns[1].get("answer") or {}).get("mode")
                  if len(three_turns) > 1 else "") or "single"
    _, multi_payload = get_json(base, "/api/session?id=" + SESSION_MULTI)
    multi_turns = (multi_payload.get("session") or {}).get("turns") or []
    init_sources = (((multi_turns[-1].get("answer") or {}).get("sources") or [])
                    if multi_turns else [])
    n0 = len(init_sources)
    if len(three_turns) != 3:
        raise SystemExit("3 轮会话形状不对：%s 轮" % len(three_turns))
    if n0 < 1:
        raise SystemExit("多书会话最后一轮没有 sources")
    print("[会话] 3 轮 %s，第 2 轮 %s；多书 %s，初始 %d 本" %
          (SESSION, turn2_mode, SESSION_MULTI, n0))
    url = base + "/?session=" + SESSION
    status, classic = get_html(base, "/classic")
    check("旧页在 /classic", status == 200 and "ask-form" in classic,
          "status=%s" % status)
    status, home = get_html(base, "/")
    check("/ 是 02 页", status == 200 and "session.js" in home and "id=\"main\"" in home,
          "status=%s" % status)
    proc, profile, ws_url = launch(1440, 900, "about:blank", a.port)
    try:
        cdp = CDP(WS(ws_url))
        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.call(method)
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/session?session=" + SESSION})
        loaded = wait_js(cdp, "document.querySelectorAll('.round').length === 3"
                              " && !!document.querySelector('.answer p')", 25)
        check("恢复 3 轮会话", loaded)
        check("/session 别名可用且 URL 归一",
              eval_js(cdp, "location.pathname === '/' && location.search.includes("
                          + json.dumps("session=" + SESSION) + ")"),
              eval_js(cdp, "location.pathname + location.search"))
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
        check("每轮有重新回答按钮", s0["reBtns"] == 1 and s0["turnActions"] == 1
              and s0["reBtnText"] == "重新回答",
              "btns=%d actions=%d text=%s" % (s0["reBtns"], s0["turnActions"],
                                              s0["reBtnText"]))
        rb, ab, eb = s0["reBtn"], s0["answerBox"], s0["evBox"]
        check("重新回答在答案与依据之间靠右",
              bool(rb and ab and eb) and rb["top"] >= ab["bottom"]
              and rb["bottom"] <= eb["top"] and abs(rb["right"] - eb["right"]) <= 1,
              "answer=%s btn=%s ev=%s" % (ab, rb, eb))
        eval_js(cdp, "document.querySelector('.turn-reanswer').scrollIntoView({block:'center'}); true")
        time.sleep(0.25)
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        re_shot = os.path.join(a.out_dir, "会话-重新回答-1440x900.png")
        with open(re_shot, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("重新回答 1440 截图", os.path.getsize(re_shot) > 20000,
              "%s %d B" % (re_shot, os.path.getsize(re_shot)))
        eval_js(cdp, "document.querySelector('#main').scrollTop = 0; true")

        old_answer = s0["answerText"]
        eval_js(cdp, REANSWER)
        eval_js(cdp, "document.querySelector('.turn-reanswer').click(); true")
        cleared = wait_js(cdp, "!document.querySelector('.answer')"
                                " && !document.querySelector('.turn-actions')", 8)
        held = eval_js(cdp, "typeof window.__release === 'function'")
        s_pending = eval_js(cdp, SNAP)
        check("重答先清旧答案与依据", cleared and held and s_pending["answerParas"] == 0
              and s_pending["cites"] == 0 and s_pending["reBtns"] == 0
              and s_pending["answerText"] == "",
              "cleared=%s held=%s paras=%d cites=%d btns=%d" % (
                  cleared, held, s_pending["answerParas"], s_pending["cites"],
                  s_pending["reBtns"]))
        eval_js(cdp, "window.__release(); true")
        redone = wait_js(cdp, "!!document.querySelector('.answer')"
                              " && document.querySelector('.answer').textContent.includes('重新回答的正文')"
                              " && !!document.querySelector('.turn-reanswer')", 10)
        s_re = eval_js(cdp, SNAP)
        calls = json.loads(eval_js(cdp, "JSON.stringify(window.__calls)"))
        ans = next((c["body"] for c in calls if c["kind"] == "answer"), {})
        ans_srcs = ans.get("sources") or []
        check("重答只发一次回答请求", redone and len(calls) == 1
              and calls[0]["kind"] == "answer",
              "redone=%s calls=%s" % (redone, [c["kind"] for c in calls]))
        check("重答带 fresh 且沿用原来源", ans.get("fresh") is True
              and ans.get("session_id") == SESSION and ans.get("turn") == 3
              and len(ans_srcs) >= 1 and ans.get("path") == ans_srcs[0].get("path"),
              json.dumps(ans, ensure_ascii=False)[:220])
        check("重答后新答案与按钮回来", s_re["answerText"].startswith("重新回答的正文")
              and s_re["reBtnText"] == "重新回答" and s_re["cites"] >= 1,
              "answer=%s btn=%s cites=%d" % (s_re["answerText"], s_re["reBtnText"],
                                             s_re["cites"]))
        check("重答替换而不是追加", old_answer != s_re["answerText"]
              and s_re["rounds"] == 3, "old=%s new=%s rounds=%d" % (
                  old_answer, s_re["answerText"], s_re["rounds"]))
        check("历史页签有列表", s0["historyRows"] > 0 and s0["historyCount"] != "",
              "rows=%d count=%s" % (s0["historyRows"], s0["historyCount"]))
        check("回答标题与正文同字号", s0["h2Size"] != "" and s0["h2Size"] == s0["pSize"],
              "h2=%s p=%s" % (s0["h2Size"], s0["pSize"]))

        eval_js(cdp, "document.querySelector('.round[data-turn=\"2\"]').click(); true")
        ok2 = wait_js(cdp, "document.querySelector('.round.is-current')"
                          " && document.querySelector('.round.is-current').dataset.turn === '2'"
                          " && !!document.querySelector('.current')", 20)
        s1 = eval_js(cdp, SNAP)
        check("第 2 轮有落点框", ok2 and s1["anchors"] == 1 and s1["scopes"] == 0
              and "承接 01" in s1["kicker"],
              "anchors=%d scopes=%d kicker=%s" % (s1["anchors"], s1["scopes"], s1["kicker"]))
        check("历史轮也有重新回答", s1["reBtns"] == 1 and s1["reBtnText"] == "重新回答",
              "btns=%d text=%s" % (s1["reBtns"], s1["reBtnText"]))
        anchor_title = anchor2.get("title") or ""
        anchor_book = (anchor2.get("path") or "").split("/")[1] if anchor2.get("path") else ""
        check("右栏默认落在本轮落点", s1["drawerCite"] == "[1]"
              and (not anchor_title or anchor_title in s1["drawerBook"]
                   or (anchor_book and anchor_book in s1["drawerBook"]))
              and "这一问的落点" in " ".join(s1["pins"]),
              "cite=%s book=%s expected=%s/%s pins=%s" % (
                  s1["drawerCite"], s1["drawerBook"], anchor_title, anchor_book, s1["pins"]))
        check("五阶段可展开", eval_js(cdp, "(() => { const b=document.querySelector('.strip-bar');"
                                      " if(!b) return false; b.click();"
                                      " return document.querySelectorAll('.stage-row').length===5; })()"))
        s_cells = eval_js(cdp, SNAP)
        check("V3 阶段面板五行展开", s_cells["stageRows"] == 5 and s_cells["stripOpen"]
              and len(s_cells["stageValues"]) == 5
              and all(v and v != "—" for v in s_cells["stageValues"]),
              "rows=%d open=%s vals=%s" % (s_cells["stageRows"], s_cells["stripOpen"],
                                           s_cells["stageValues"]))
        check("第 2 轮候选书有选中与锁定",
              s_cells["books"] >= 1 and s_cells["picksOn"] >= 1
              and s_cells["picksLocked"] >= 1 and "候选书" in s_cells["booksHead"],
              "books=%d on=%d locked=%d head=%s" % (s_cells["books"], s_cells["picksOn"],
                                                    s_cells["picksLocked"], s_cells["booksHead"]))
        if turn2_mode == "multi":
            check("左栏范围与回答元信息按多书",
                  s_cells["railChips"] and s_cells["railChips"][-1].endswith("本")
                  and "本书" in s_cells["meta"] and "10,000" in s_cells["meta"],
                  "chips=%s meta=%s" % (s_cells["railChips"], s_cells["meta"]))
            check("五阶段第 5 格是跨书",
                  len(s_cells["stageTexts"]) == 5 and "跨书" in s_cells["stageTexts"][4],
                  s_cells["stageTexts"][4][:120] if len(s_cells["stageTexts"]) > 4
                  else "rows=%d" % len(s_cells["stageTexts"]))
        else:
            print("[跳过] 第 2 轮是单书，跳过跨书左栏断言")
        check("历史轮次书单只读",
              s_cells["booksAll"] == "" and s_cells["picksOff"] == s_cells["picks"]
              and "历史轮次 · 书单只读" in s_cells["booksHead"] and s_cells["booksAct"] == "",
              "all=%s off=%d/%d head=%s act=%s" % (s_cells["booksAll"], s_cells["picksOff"],
                                                    s_cells["picks"], s_cells["booksHead"],
                                                    s_cells["booksAct"]))

        eval_js(cdp, "document.querySelectorAll('.stage-row .stage-row-head')[2].click(); true")
        s_open = eval_js(cdp, SNAP)
        check("阶段行可点开", s_open["stageOpen"] == 1 and s_open["stripOpen"],
              "open=%d strip=%s" % (s_open["stageOpen"], s_open["stripOpen"]))
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        v3shot = os.path.join(a.out_dir, "会话-V3-阶段-1440x900.png")
        with open(v3shot, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("V3 阶段 1440 截图", os.path.getsize(v3shot) > 20000,
              "%s %d B" % (v3shot, os.path.getsize(v3shot)))

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

        eval_js(cdp, FAKE.replace("7f0f8d8ba4c4", SESSION))
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
        ranchor = run.get("anchor") or {}
        check("追问请求带会话、轮次和落点",
              run.get("session_id") == SESSION and run.get("turn") == 4
              and (ranchor.get("path") or "").startswith("books/")
              and int(ranchor.get("line") or 0) > 0
              and bool(ranchor.get("quote")), json.dumps(run, ensure_ascii=False)[:180])
        ans_srcs = ans.get("sources") or []
        check("回答请求带会话、轮次，且只带一本来源",
              ans.get("session_id") == SESSION and ans.get("turn") == 4
              and len(ans_srcs) == 1 and ans_srcs[0].get("path") == ans.get("path")
              and ans.get("path") == ranchor.get("path") and ans.get("line") == ranchor.get("line")
              and ans.get("book") == ans_srcs[0].get("book"),
              json.dumps(ans, ensure_ascii=False)[:200])
        check("上下文提示出现在回答里", "接上一问 · 上下文 10/5,000 字" in s4["meta"], s4["meta"])

        still = wait_js(cdp, "document.querySelectorAll('.strip .step.is-active').length === 0"
                            " && document.querySelectorAll('.strip .step.is-done').length === 5"
                            " && !!document.querySelector('.stages')"
                            " && !document.querySelector('.stages').classList.contains('is-open')"
                            " && document.querySelectorAll('.stage-row.is-open').length === 0", 20)
        s5a = eval_js(cdp, SNAP)
        check("阶段结束后静止", still and s5a["stageActive"] == 0 and s5a["stageDone"] == 5
              and not s5a["stripOpen"] and s5a["stageOpen"] == 0,
              "active=%d done=%d strip=%s open=%d" % (s5a["stageActive"], s5a["stageDone"],
                                                     s5a["stripOpen"], s5a["stageOpen"]))

        cdp.call("Page.navigate", {"url": base + "/"})
        home = wait_js(cdp, "document.querySelectorAll('.hist-row').length > 0"
                            " && !!document.querySelector('.last-card')", 25)
        s7 = eval_js(cdp, SNAP)
        check("初始页默认历史", home and s7["tabHistoryOn"] and not s7["tabCurrentOn"]
              and s7["starts"] == 1 and s7["lastCards"] == 1 and s7["historyRows"] > 0,
              "hist=%d start=%d last=%d tabH=%s tabC=%s" % (s7["historyRows"], s7["starts"],
                                                            s7["lastCards"], s7["tabHistoryOn"],
                                                            s7["tabCurrentOn"]))
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        hist1440 = os.path.join(a.out_dir, "会话-历史-1440x900.png")
        with open(hist1440, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("历史 1440 截图", os.path.getsize(hist1440) > 20000,
              "%s %d B" % (hist1440, os.path.getsize(hist1440)))
        eval_js(cdp, "document.querySelector('.last-card').click(); true")
        back = wait_js(cdp, "document.querySelectorAll('.round').length > 0"
                            " && document.querySelector('#tab-current')"
                            " .classList.contains('is-on')", 25)
        s8 = eval_js(cdp, SNAP)
        check("接着上次能打开会话", back and s8["rounds"] > 0 and s8["tabCurrentOn"],
              "rounds=%d tabC=%s" % (s8["rounds"], s8["tabCurrentOn"]))
        eval_js(cdp, "document.querySelector('#tab-history').click(); true")
        pinned = wait_js(cdp, "document.querySelector('#panel-history')"
                             " && !document.querySelector('#panel-history').hidden"
                             " && document.querySelectorAll('.hist-row.is-current').length === 1", 10)
        s8b = eval_js(cdp, SNAP)
        check("历史里当前会话置顶",
              pinned and s8b["histFirstGroup"] == "当前打开" and s8b["histFirstCurrent"]
              and s8b["panelHistoryHidden"] is False,
              "group=%s first=%s hidden=%s" % (s8b["histFirstGroup"], s8b["histFirstCurrent"],
                                              s8b["panelHistoryHidden"]))

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
        check("390 回答标题与正文同字号", s5["h2Size"] != "" and s5["h2Size"] == s5["pSize"],
              "h2=%s p=%s" % (s5["h2Size"], s5["pSize"]))
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

        cdp.call("Page.navigate", {"url": base + "/"})
        h390 = wait_js(cdp, "document.querySelectorAll('.hist-row').length > 0"
                            " && !!document.querySelector('.last-card')", 25)
        s9 = eval_js(cdp, SNAP)
        check("390 初始页历史可滚", h390 and not s9["overflow"] and s9["scrollWidth"] == 390
              and s9["histScroll"] in ("auto", "scroll") and s9["histWrap"] == "nowrap",
              "overflow=%s sw=%s scroll=%s wrap=%s" % (s9["overflow"], s9["scrollWidth"],
                                                      s9["histScroll"], s9["histWrap"]))
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        hist390 = os.path.join(a.out_dir, "会话-历史-390x844.png")
        with open(hist390, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("历史 390 截图", os.path.getsize(hist390) > 10000,
              "%s %d B" % (hist390, os.path.getsize(hist390)))

        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/?session=" + SESSION_MULTI})
        loaded2 = wait_js(cdp, "document.querySelectorAll('.round').length === 2"
                               " && !!document.querySelector('.answer p')", 25)
        m0 = eval_js(cdp, SNAP)
        check("最后一轮出多书选择器",
              loaded2 and m0["books"] >= 1 and m0["picksOn"] == n0
              and m0["picksLocked"] >= 1 and m0["booksAll"] == "全选",
              "books=%d on=%d locked=%d n0=%d all=%s head=%s" % (
                  m0["books"], m0["picksOn"], m0["picksLocked"], n0,
                  m0["booksAll"], m0["booksHead"]))
        check("换书前没有过期条", m0["booksAct"] == "", m0["booksAct"])

        eval_js(cdp, MULTI)
        picked = eval_js(cdp, PICK_FREE)
        bar = wait_js(cdp, "!!document.querySelector('[data-reanswer]')", 10)
        m1 = eval_js(cdp, SNAP)
        pre = json.loads(eval_js(cdp, "JSON.stringify(window.__calls || [])"))
        check("点书卡出现过期条且不发请求",
              bar and bool(picked)
              and m1["booksActBtn"] == "用这 %d 本重新回答" % (n0 + 1) and not pre,
              "picked=%s btn=%s calls=%d act=%s" % (picked, m1["booksActBtn"], len(pre),
                                                   m1["booksAct"]))

        eval_js(cdp, "document.querySelector('[data-reanswer]').click(); true")
        redone = wait_js(cdp, "!document.querySelector('.books-act')"
                              " && !!document.querySelector('.answer-meta')"
                              " && document.querySelector('.answer-meta').textContent"
                                 ".includes('3,625/10,000')", 20)
        m2 = eval_js(cdp, SNAP)
        calls = json.loads(eval_js(cdp, "JSON.stringify(window.__calls || [])"))
        ans2 = next((c["body"] for c in calls if c["kind"] == "answer"), {})
        srcs = ans2.get("sources") or []
        check("重新回答的请求带 n0+1 本来源",
              ans2.get("session_id") == SESSION_MULTI and ans2.get("turn") == 2
              and len(srcs) == n0 + 1 and srcs[-1].get("book") == picked
              and srcs[0].get("path") == (init_sources[0].get("path") if init_sources else ""),
              json.dumps(ans2, ensure_ascii=False)[:220])
        check("多书回答回到页面",
              redone and m2["railChips"][-1] == "%d 本" % (n0 + 1)
              and ("%d 本书" % (n0 + 1)) in m2["meta"]
              and "3,625/10,000 字" in m2["meta"] and m2["picksOn"] == n0 + 1,
              "chips=%s meta=%s on=%d" % (m2["railChips"], m2["meta"], m2["picksOn"]))

        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        multi1440 = os.path.join(a.out_dir, "会话-multi-1440x900.png")
        with open(multi1440, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("multi 1440 截图", os.path.getsize(multi1440) > 20000,
              "%s %d B" % (multi1440, os.path.getsize(multi1440)))

        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        time.sleep(0.4)
        m3 = eval_js(cdp, SNAP)
        check("390 multi 无横向溢出", not m3["overflow"] and m3["scrollWidth"] == 390,
              "scrollWidth=%s innerWidth=%s" % (m3["scrollWidth"], m3["innerWidth"]))
        check("390 multi composer 在视口内",
              m3["ask"] and m3["ask"]["top"] > 0 and m3["ask"]["bottom"] <= 844,
              "ask=%s" % (m3["ask"],))
        check("390 书卡横向滚动",
              m3["picksWrap"] == "nowrap" and m3["picksScroll"] in ("auto", "scroll"),
              "wrap=%s scroll=%s" % (m3["picksWrap"], m3["picksScroll"]))
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        multi390 = os.path.join(a.out_dir, "会话-multi-390x844.png")
        with open(multi390, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("multi 390 截图", os.path.getsize(multi390) > 10000,
              "%s %d B" % (multi390, os.path.getsize(multi390)))

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
