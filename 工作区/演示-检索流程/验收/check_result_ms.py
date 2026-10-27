#!/usr/bin/env python3
"""核对第 5 阶段「结果」头部耗时：它必须把回答那一步算进去。

  python3 验收/check_result_ms.py                       # 默认 127.0.0.1:8765
  python3 验收/check_result_ms.py --base http://127.0.0.1:8799

做法：headless Chrome 打开页面，点「重新检索」强制一次不走缓存的回答，等回答卡落地后读三处数字：
  1. 第 5 阶段头部的 .stage-ms
  2. 回答卡 .answer-meta 里那个 fmtMs(d.ms)
  3. 左侧流程轨第 5 项的 .rail-ms
判据：
  - 回答本身至少 1 秒（否则这次没跑到模型，检查不成立）
  - 头部耗时 >= 回答耗时（回答是第 5 阶段的一部分）
  - 头部耗时 - 回答耗时 <= 2000 ms（剩下的只能是读原文那点事）
  - 流程轨第 5 项与头部一致
"""
import argparse
import base64
import json
import os
import re
import shutil
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shot  # noqa: E402

QUESTION = "礼治社会靠什么维持秩序"
MS_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s)(?=[ ·]|$)")

READ_HEAD = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const el = document.querySelectorAll('.stage-ms')[4];
    const txt = el ? el.textContent.trim() : '';
    if (txt && txt !== '—') { resolve({ok: true, text: txt, wait: Date.now() - t0}); return; }
    if (Date.now() - t0 > 30000) { resolve({ok: false, text: txt, wait: Date.now() - t0}); return; }
    setTimeout(tick, 100);
  };
  tick();
})
"""

WAIT_ANSWER = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const card = document.querySelector('.answer-card');
    const meta = document.querySelector('.answer-meta');
    const done = card && !card.classList.contains('is-loading');
    const err = card && card.classList.contains('is-error');
    if ((done && meta) || err || Date.now() - t0 > 180000) {
      resolve({done: !!done, error: !!err, meta: meta ? meta.textContent.trim() : '',
               wait: Date.now() - t0});
      return;
    }
    setTimeout(tick, 150);
  };
  tick();
})
"""

DUMP = """JSON.stringify({
  stageMs: Array.from(document.querySelectorAll('.stage-ms')).map(e => e.textContent.trim()),
  railMs: Array.from(document.querySelectorAll('.rail-ms')).map(e => e.textContent.trim()),
  question: (document.querySelector('.question-text') || {}).textContent || '',
})"""


def parse_ms(text):
    """'32.6 s' -> 32600.0；'4.2 ms' -> 4.2；没有就 None。"""
    found = MS_TOKEN.findall(text or "")
    if not found:
        return None
    value, unit = found[-1]
    return float(value) * (1000.0 if unit == "s" else 1.0)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--question", default=QUESTION)
    ap.add_argument("--port", type=int, default=9402)
    ap.add_argument("--shot", default="")
    args = ap.parse_args(argv)

    url = args.base + "/?q=" + urllib.parse.quote(args.question) + "&instant=1"
    fails = []
    proc, profile, ws_url = shot.launch(1440, 900, url, args.port)
    ws = None
    try:
        ws = shot.WS(ws_url, timeout=260)
        ws.sock.settimeout(260)
        cdp = shot.CDP(ws)
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": url})
        time.sleep(0.8)

        first = shot.eval_js(cdp, READ_HEAD, await_promise=True, timeout=60)
        print("[首屏] 第 5 阶段耗时 %s（等了 %.0f ms）" % (first.get("text"), first.get("wait") or 0))

        shot.eval_js(cdp, "document.querySelector('#fresh-btn').click(); true", timeout=20)
        answer = shot.eval_js(cdp, WAIT_ANSWER, await_promise=True, timeout=200)
        dump = json.loads(shot.eval_js(cdp, DUMP))
        if args.shot:
            png = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            with open(args.shot, "wb") as fh:
                fh.write(base64.b64decode(png["data"]))
            print("[截图] " + args.shot)

        head_text = dump["stageMs"][4]
        rail_text = dump["railMs"][4]
        head_ms = parse_ms(head_text)
        rail_ms = parse_ms(rail_text)
        answer_ms = parse_ms(answer.get("meta"))
        print("[回答] meta=%r -> %.1f ms（等了 %.1f s，done=%s error=%s）"
              % (answer.get("meta"), answer_ms or 0, (answer.get("wait") or 0) / 1000.0,
                 answer.get("done"), answer.get("error")))
        print("[阶段] 头部 %s -> %s ms；流程轨 %s -> %s ms"
              % (head_text, head_ms, rail_text, rail_ms))

        def check(name, ok, detail=""):
            print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
            if not ok:
                fails.append(name)

        check("回答卡落地", bool(answer.get("done")) and not answer.get("error"))
        check("这次跑到模型（回答 >= 1 秒）", (answer_ms or 0) >= 1000,
              "%.1f ms" % (answer_ms or 0))
        check("头部含回答耗时", head_ms is not None and answer_ms is not None
              and head_ms >= answer_ms,
              "头部 %s ms vs 回答 %s ms" % (head_ms, answer_ms))
        check("头部只比回答多读原文那点", head_ms is not None and answer_ms is not None
              and 0 <= head_ms - answer_ms <= 2000,
              "差 %.1f ms" % ((head_ms or 0) - (answer_ms or 0)))
        check("流程轨与头部一致", rail_ms is not None and head_ms is not None
              and abs(rail_ms - head_ms) < 0.51,
              "%s vs %s" % (rail_text, head_text))
        errs = cdp.errors()
        check("控制台无报错", not errs, "; ".join(errs)[:300])
    finally:
        try:
            if ws:
                ws.close()
        except Exception:  # noqa: BLE001
            pass
        proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print("失败 %d 项" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
