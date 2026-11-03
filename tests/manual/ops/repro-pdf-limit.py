#!/usr/bin/env python3
"""复现：「最多转几本」跟着已选本数走。

  python3 tests/manual/ops/repro-pdf-limit.py

临时根 + stub 脚本 + headless Chrome，不碰真库也不调模型。勾一本看输入框是不是 1，
勾两本看是不是 2，手填 5 再切一个不改数量的筛选看还是不是 5，清空看是不是回到 0，
最后到 PDF 页看转写卡上那个框跟已选是否一致。

末行 REPRO PASS（都对）或 REPRO FAIL（列出不对的那几条）。
"""
import os
import shutil
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tests", "acceptance", "ops"))

import check_ops  # noqa: E402
import shot_ops  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    root, fixture = check_ops.build_root()
    server, port = check_ops.start(root, fixture)
    chrome = None
    fails = []
    try:
        code, body = check_ops.post(port, "/api/pdf/settle",
                                    {"items": [{"uid": "B0005", "route": "OCR"}]})
        check_ops.wait_job(port, body.get("job_id"),
                           ("succeeded", "failed", "awaiting_review"))
        chrome, _profile, ws_url = shot_ops.launch(free_port())
        cdp = shot_ops.CDP(shot_ops.WS(ws_url))
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/#pdf-list" % port})

        def js(expr, wait=False):
            return shot_ops.eval_js(cdp, expr, await_promise=wait)

        def limit():
            return js("(document.querySelector('#pick-bar #pdf-limit') || {}).value")

        def picked():
            return js("(document.querySelector('#pick-bar b') || {}).textContent")

        def press(i):
            return js("(() => { const b = document.querySelectorAll("
                      "'#pick-body input[type=checkbox]')[%d]; if (!b) return 'no-box';"
                      " b.click(); return 'ok'; })()" % i)

        def settle(name, want):
            js("new Promise((r) => setTimeout(r, 300))", True)
            got = limit()
            print("  %-22s %-12s 上限 %s" % (name, picked(), got))
            if str(got) != str(want):
                fails.append("%s：上限 %s，期望 %s" % (name, got, want))

        js("new Promise((r) => setTimeout(r, 900))", True)
        print("清单行数：%s" % js("document.querySelectorAll('#pick-body input[type=checkbox]').length"))
        settle("初始", 0)
        press(0)
        settle("勾一本", 1)
        press(1)
        settle("勾两本", 2)
        press(0)
        settle("取消一本", 1)
        js("(() => { const el = document.querySelector('#pick-bar #pdf-limit');"
           " el.value = '5'; el.dispatchEvent(new Event('change', {bubbles: true})); })()")
        settle("手填 5", 5)
        js("document.querySelector('input[data-act=pick-recheck]').click()")
        settle("切筛选不动数量", 5)
        js("document.querySelector('input[data-act=pick-recheck]').click()")
        js("document.querySelector('[data-act=pick-clear]').click()")
        settle("清空", 0)
        js("document.querySelector('[data-act=pick-all-convert]').click()")
        settle("全选可转的", 1)
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/#pdf" % port})
        js("new Promise((r) => setTimeout(r, 1500))", True)
        card = js("(() => { const e = document.querySelector('#view #pdf-limit');"
                  " return e ? e.value : 'no-input'; })()")
        print("  %-22s %-12s 上限 %s" % ("PDF 页转写卡", picked(), card))
        if str(card) != "1":
            fails.append("PDF 页转写卡：上限 %s，期望 1" % card)
    finally:
        if chrome is not None:
            chrome.kill()
        check_ops.stop(server)
        if os.environ.get("KEEP_ROOT"):
            print("留着临时根：%s" % root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    if fails:
        print("\nREPRO FAIL")
        for line in fails:
            print("  -", line)
        return 1
    print("\nREPRO PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
