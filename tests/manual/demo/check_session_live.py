#!/usr/bin/env python3
"""02 会话页真实端到端：新会话首问 → 选依据 → 追问 → 选第二本书重新回答。

会调真实 /api/run 与 /api/answer，通常 2 到 5 分钟。默认 8765。
"""

import argparse
import base64
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_session_ui import SNAP, wait_js
from shot import CDP, WS, eval_js, launch

Q1 = "乡土中国 差序格局"
Q2 = "那礼治秩序和差序格局是什么关系？"
fails = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--port", type=int, default=9346)
    ap.add_argument("--out-dir", default=HERE)
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    url = base + "/?q=" + urllib.parse.quote(Q1)
    proc, profile, ws_url = launch(1440, 900, "about:blank", a.port)
    try:
        cdp = CDP(WS(ws_url))
        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.call(method)
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        )
        cdp.call("Page.navigate", {"url": url})
        first = wait_js(
            cdp, "document.querySelectorAll('.round').length === 1 && !!document.querySelector('.answer')", 200
        )
        s1 = eval_js(cdp, SNAP)
        check(
            "首问出第 1 轮与回答",
            first and s1["answerParas"] > 0 and s1["cites"] > 0,
            "paras=%d cites=%d" % (s1["answerParas"], s1["cites"]),
        )
        check("首问问题正确", Q1 in s1["question"], s1["question"])
        check("首问无落点框", s1["anchors"] == 0, "anchors=%d" % s1["anchors"])
        check("首问后 URL 带会话", "session=" in eval_js(cdp, "location.search"), "")
        sid = eval_js(cdp, "(new URLSearchParams(location.search)).get('session') || ''")
        check("会话 id 是 12 位 hex", len(sid) == 12 and all(c in "0123456789abcdef" for c in sid), sid)

        eval_js(cdp, "document.querySelector('.ev').click(); true")
        picked = wait_js(
            cdp, "document.querySelectorAll('.ev.is-on').length === 1 && !!document.querySelector('#q')", 15
        )
        s2 = eval_js(cdp, SNAP)
        check("首问依据可设为下一问落点", picked and "下一问挂在 [1]" in s2["hint"], "hint=%s" % s2["hint"])

        eval_js(
            cdp,
            "document.querySelector('#q').value=" + json.dumps(Q2) + ";"
            "document.querySelector('#q').dispatchEvent(new Event('input', {bubbles:true}));"
            "document.querySelector('#send').click(); true",
        )
        second = wait_js(
            cdp,
            "document.querySelectorAll('.round').length === 2"
            " && !!document.querySelector('.answer')"
            " && document.querySelector('.kicker').textContent.includes('承接')",
            260,
        )
        s3 = eval_js(cdp, SNAP)
        check(
            "追问出第 2 轮",
            second and s3["rounds"] == 2 and s3["current"] == "2",
            "rounds=%d current=%s" % (s3["rounds"], s3["current"]),
        )
        check(
            "第 2 轮有落点框",
            s3["anchors"] == 1 and s3["scopes"] == 0,
            "anchors=%d scopes=%d kicker=%s" % (s3["anchors"], s3["scopes"], s3["kicker"]),
        )
        check("第 2 轮回答带上下文", "接上一问 · 上下文" in s3["meta"] and "/5,000 字" in s3["meta"], s3["meta"])
        check(
            "第 2 轮有回答与依据",
            s3["answerParas"] > 0 and s3["cites"] > 0,
            "paras=%d cites=%d" % (s3["answerParas"], s3["cites"]),
        )

        data = get_json(base + "/api/session?id=" + sid)
        turns = (data.get("session") or {}).get("turns") or []
        t2 = turns[1] if len(turns) > 1 else {}
        ctx = t2.get("context") or {}
        check(
            "服务端第 2 轮 related=true",
            (t2.get("related") or {}).get("related") is True,
            json.dumps(t2.get("related"), ensure_ascii=False)[:120],
        )
        check(
            "服务端上下文不超过 5000",
            0 < int(ctx.get("chars") or 0) <= 5000 and int(ctx.get("budget") or 0) == 5000,
            "chars=%s budget=%s" % (ctx.get("chars"), ctx.get("budget")),
        )
        check(
            "第 2 轮落点已落盘",
            bool(t2.get("anchor") and t2["anchor"].get("path")),
            json.dumps(t2.get("anchor"), ensure_ascii=False)[:120],
        )
        cards = ((t2.get("trace") or {}).get("cards") or {}).get("cards") or []
        check("第 2 轮 trace 有书卡", 5 <= len(cards) <= 8, "cards=%d" % len(cards))
        check(
            "1440 无横向溢出",
            not s3["overflow"],
            "scrollWidth=%s innerWidth=%s" % (s3["scrollWidth"], s3["innerWidth"]),
        )

        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(a.out_dir, "会话-live-1440x900.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("1440 真实截图", os.path.getsize(path) > 20000, "%s %d B" % (path, os.path.getsize(path)))

        picked = eval_js(
            cdp,
            """(() => {
          const pick = Array.from(document.querySelectorAll('.book-pick')).find(
            (el) => !el.classList.contains('is-on') && !el.classList.contains('is-locked'));
          if (!pick) return '';
          window.__picked = pick.dataset.bookPick;
          pick.click();
          return pick.dataset.bookPick;
        })()""",
        )
        bar = wait_js(cdp, "!!document.querySelector('[data-reanswer]')", 10)
        s5 = eval_js(cdp, SNAP)
        check(
            "选第二本书出现过期条",
            bar and bool(picked) and "用这 2 本重新回答" in s5["booksActBtn"],
            "picked=%s act=%s" % (picked, s5["booksAct"]),
        )

        eval_js(cdp, "document.querySelector('[data-reanswer]').click(); true")
        multi = wait_js(
            cdp,
            "!document.querySelector('.books-act')"
            " && !!document.querySelector('.answer-meta')"
            " && document.querySelector('.answer-meta').textContent.includes('本书')",
            300,
        )
        s6 = eval_js(cdp, SNAP)
        data = get_json(base + "/api/session?id=" + sid)
        t2b = ((data.get("session") or {}).get("turns") or [None, None])[1] or {}
        ans = t2b.get("answer") or {}
        ctx2 = t2b.get("context") or {}
        srcs = ans.get("sources") or []
        check(
            "服务端第 2 轮是 multi",
            ans.get("mode") == "multi" and len(srcs) >= 2,
            "mode=%s sources=%d" % (ans.get("mode"), len(srcs)),
        )
        check(
            "多书上下文守 10000",
            int(ctx2.get("budget") or 0) == 10000 and 0 < int(ctx2.get("chars") or 0) <= 10000,
            "chars=%s budget=%s" % (ctx2.get("chars"), ctx2.get("budget")),
        )
        check(
            "多书轮 related=true",
            (t2b.get("related") or {}).get("related") is True,
            json.dumps(t2b.get("related"), ensure_ascii=False)[:120],
        )
        wide = len(srcs)
        check(
            "页面按 N 本显示",
            multi
            and s6["railChips"][1] == "%d 本" % wide
            and ("%d 本书" % wide) in s6["meta"]
            and "/10,000 字" in s6["meta"]
            and s6["picksOn"] == wide,
            "chips=%s meta=%s on=%d" % (s6["railChips"], s6["meta"], s6["picksOn"]),
        )

        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(a.out_dir, "会话-live-multi-1440x900.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("multi 1440 真实截图", os.path.getsize(path) > 20000, "%s %d B" % (path, os.path.getsize(path)))

        cdp.call(
            "Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True}
        )
        time.sleep(0.4)
        s7 = eval_js(cdp, SNAP)
        check(
            "390 multi 无横向溢出",
            not s7["overflow"] and s7["scrollWidth"] == 390,
            "scrollWidth=%s innerWidth=%s" % (s7["scrollWidth"], s7["innerWidth"]),
        )
        check(
            "390 multi composer 在视口内",
            s7["ask"] and s7["ask"]["top"] > 0 and s7["ask"]["bottom"] <= 844,
            "ask=%s" % (s7["ask"],),
        )
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(a.out_dir, "会话-live-multi-390x844.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("multi 390 真实截图", os.path.getsize(path) > 10000, "%s %d B" % (path, os.path.getsize(path)))

        cdp.call(
            "Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True}
        )
        cdp.call("Page.navigate", {"url": base + "/?session=" + sid})
        wait_js(cdp, "document.querySelectorAll('.round').length === 2 && !!document.querySelector('.answer')", 25)
        s4 = eval_js(cdp, SNAP)
        check(
            "390 无横向溢出",
            not s4["overflow"] and s4["scrollWidth"] == 390,
            "scrollWidth=%s innerWidth=%s" % (s4["scrollWidth"], s4["innerWidth"]),
        )
        check(
            "390 composer 在视口内",
            s4["ask"] and s4["ask"]["top"] > 0 and s4["ask"]["bottom"] <= 844,
            "ask=%s" % (s4["ask"],),
        )
        check("390 恢复会话后落点仍在", "下一问挂在 [1]" in s4["hint"], s4["hint"])
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(a.out_dir, "会话-live-390x844.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        check("390 真实截图", os.path.getsize(path) > 10000, "%s %d B" % (path, os.path.getsize(path)))

        cdp.wait_events(0.5)
        errs = cdp.errors()
        check("控制台 0 错误", not errs, "; ".join(errs)[:300])
        print("会话：%s/?session=%s" % (base, sid))
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
