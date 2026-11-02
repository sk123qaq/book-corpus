#!/usr/bin/env python3
"""给 原型-会话历史 出图并验收四版展开区。

  python3 原型-会话历史/shoot.py
  python3 原型-会话历史/shoot.py --full

横条与屏幕状态在 1440x900 / 390x844 各一张；V1–V4 各出「展开卡片」一张。
另跑「演示进度」，断言结束后 5 个圆点全静止、没有 active。只写 shots/。
"""
import argparse
import base64
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "shot", os.path.join(HERE, "..", "验收", "shot.py"))
shot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shot)

PAGE = "file://" + os.path.join(HERE, "index.html")
SCREENS = ["start", "running", "answering", "done", "open", "history"]
VARIANTS = ["v1", "v2", "v3", "v4"]
SIZES = [("1440", 1440, 900), ("390", 390, 844)]

MEASURE = """
(() => {
  const q = (s) => document.querySelector(s);
  const rows = [...document.querySelectorAll('.stage-row')];
  const h2 = q('.answer-body h2');
  const p = q('.answer-body p');
  return {
    iw: window.innerWidth,
    sw: document.documentElement.scrollWidth,
    sh: document.documentElement.scrollHeight,
    screen: document.body.dataset.screen,
    variant: document.body.dataset.variant,
    stageH: q('#stages') ? Math.round(q('#stages').getBoundingClientRect().height) : 0,
    rowH: rows.length ? Math.round(rows[0].getBoundingClientRect().height) : 0,
    openCount: document.querySelectorAll('.stage-row.is-open').length,
    active: document.querySelectorAll('.step.is-active').length,
    done: document.querySelectorAll('.step.is-done').length,
    stackOpen: q('#stages') ? q('#stages').classList.contains('is-open') : false,
    histRows: document.querySelectorAll('.hist-row').length,
    h2Size: h2 ? getComputedStyle(h2).fontSize : '',
    pSize: p ? getComputedStyle(p).fontSize : '',
  };
})()
"""

CLICK_SCREEN = "document.querySelector('[data-screen-btn=\"%s\"]').click()"
CLICK_VARIANT = "document.querySelector('[data-variant-btn=\"%s\"]').click()"
CLICK_HISTORY_TAB = "document.querySelector('#tab-history').click()"
CLICK_CURRENT_TAB = "document.querySelector('#tab-current').click()"
FILTER = "(() => { const i = document.querySelector('#hist-filter'); i.value = '南宫婉'; i.dispatchEvent(new Event('input', {bubbles:true})); return document.querySelectorAll('.hist-row').length; })()"
CLEAR_FILTER = "(() => { const i = document.querySelector('#hist-filter'); i.value = ''; i.dispatchEvent(new Event('input', {bubbles:true})); return document.querySelectorAll('.hist-row').length; })()"
TOGGLE_STAGE = "document.querySelectorAll('.stage-row-head')[2].click()"
TOGGLE_STRIP = "document.querySelector('#strip-toggle').click()"


def capture(cdp, width, height, out, full=False):
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(0.55)
    m = shot.eval_js(cdp, MEASURE)
    params = {"format": "png", "captureBeyondViewport": False}
    if full:
        params = {"format": "png", "captureBeyondViewport": True,
                  "clip": {"x": 0, "y": 0, "width": width,
                           "height": m["sh"], "scale": 1}}
    data = cdp.call("Page.captureScreenshot", params, timeout=60)["data"]
    with open(out, "wb") as f:
        f.write(base64.b64decode(data))
    return m, os.path.getsize(out)


def wait_done(cdp, timeout=16):
    end = time.time() + timeout
    while time.time() < end:
        m = shot.eval_js(cdp, MEASURE)
        if m["screen"] == "done" and m["active"] == 0 and m["done"] == 5:
            return m
        time.sleep(0.25)
    return shot.eval_js(cdp, MEASURE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    outdir = os.path.join(HERE, "shots")
    os.makedirs(outdir, exist_ok=True)
    bad = []

    proc, profile, ws_url = shot.launch(1440, 900, PAGE, 9520)
    try:
        cdp = shot.CDP(shot.WS(ws_url))
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": PAGE})
        time.sleep(1.0)

        for label, width, height in SIZES:
            for screen in SCREENS:
                shot.eval_js(cdp, CLICK_SCREEN % screen)
                time.sleep(0.35)
                m, size = capture(cdp, width, height, os.path.join(outdir, "%s-%s.png" % (screen, label)))
                overflow = m["sw"] - m["iw"]
                flag = " OVERFLOW +%d" % overflow if overflow > 1 else ""
                print("%-9s %s  stageH=%3d rowH=%2d active=%d done=%d stack=%s%s  %.0fKB"
                      % (screen, label, m["stageH"], m["rowH"], m["active"], m["done"],
                         "open" if m["stackOpen"] else "closed", flag, size / 1024))
                if overflow > 1:
                    bad.append("%s-%s overflow +%d" % (screen, label, overflow))
                if m["h2Size"] != m["pSize"]:
                    bad.append("answer h2 %s != body %s at %s" % (m["h2Size"], m["pSize"], label))

        # 四版展开区，1440 与 390
        for v in VARIANTS:
            shot.eval_js(cdp, CLICK_VARIANT % v)
            shot.eval_js(cdp, CLICK_SCREEN % "open")
            time.sleep(0.35)
            m, _ = capture(cdp, 1440, 900, os.path.join(outdir, "%s-open-1440.png" % v))
            print("%s-open   1440  stageH=%3d rowH=%2d open=%d h2=%s p=%s"
                  % (v, m["stageH"], m["rowH"], m["openCount"], m["h2Size"], m["pSize"]))
            if m["openCount"] != 1:
                bad.append("%s open count %d" % (v, m["openCount"]))
            if m["h2Size"] != m["pSize"]:
                bad.append("%s answer h2 %s != body %s" % (v, m["h2Size"], m["pSize"]))
            m, _ = capture(cdp, 390, 844, os.path.join(outdir, "%s-open-390.png" % v))
            if m["sw"] - m["iw"] > 1:
                bad.append("%s-open-390 overflow" % v)

        # 切版保留已展开的行
        shot.eval_js(cdp, CLICK_VARIANT % "v1")
        shot.eval_js(cdp, CLICK_SCREEN % "open")
        shot.eval_js(cdp, CLICK_VARIANT % "v3")
        time.sleep(0.2)
        keep = shot.eval_js(cdp, MEASURE)
        print("switch-v1-v3 open=%d" % keep["openCount"])
        if keep["openCount"] != 1:
            bad.append("variant switch lost open row")

        # 完成态展开 + 单行展开（V1）
        shot.eval_js(cdp, CLICK_VARIANT % "v1")
        shot.eval_js(cdp, CLICK_SCREEN % "done")
        time.sleep(0.3)
        shot.eval_js(cdp, TOGGLE_STRIP)
        time.sleep(0.3)
        m, _ = capture(cdp, 1440, 900, os.path.join(outdir, "done-expanded-1440.png"))
        print("done-expanded 1440  stageH=%3d stack=%s" % (m["stageH"], m["stackOpen"]))
        shot.eval_js(cdp, TOGGLE_STAGE)
        time.sleep(0.3)
        m, _ = capture(cdp, 1440, 900, os.path.join(outdir, "done-row-open-1440.png"))
        print("done-row-open 1440  stageH=%3d open=%d" % (m["stageH"], m["openCount"]))

        # 进度演示：结束后必须全部静止
        shot.eval_js(cdp, CLICK_SCREEN % "done")
        shot.eval_js(cdp, "document.querySelector('#play').click()")
        time.sleep(0.6)
        mid = shot.eval_js(cdp, MEASURE)
        print("playing   1440  screen=%s active=%d done=%d" % (mid["screen"], mid["active"], mid["done"]))
        if mid["screen"] != "playing" or mid["active"] != 1:
            bad.append("play did not start with exactly one active dot")
        end = wait_done(cdp)
        print("played    1440  screen=%s active=%d done=%d status=%s"
              % (end["screen"], end["active"], end["done"],
                 shot.eval_js(cdp, "document.querySelector('#proto-status').textContent")))
        if end["screen"] != "done" or end["active"] != 0 or end["done"] != 5:
            bad.append("after play not all static")

        # 历史切换 + 搜索
        shot.eval_js(cdp, CLICK_SCREEN % "done")
        shot.eval_js(cdp, CLICK_HISTORY_TAB)
        time.sleep(0.2)
        h = shot.eval_js(cdp, MEASURE)
        n = shot.eval_js(cdp, FILTER)
        print("history   1440  panel-history rows=%d filter(南宫婉)=%d" % (h["histRows"], n))
        if h["histRows"] < 8 or n != 1:
            bad.append("history rows/filter mismatch")
        shot.eval_js(cdp, CLEAR_FILTER)
        shot.eval_js(cdp, CLICK_SCREEN % "history")
        shot.eval_js(cdp, CLICK_CURRENT_TAB)
        time.sleep(0.2)
        c = shot.eval_js(cdp, "document.querySelector('#panel-current').hidden")
        if c:
            bad.append("tab switch back to current failed")

        # 初始页 · 本次会话空态
        shot.eval_js(cdp, CLICK_SCREEN % "start")
        shot.eval_js(cdp, CLICK_CURRENT_TAB)
        time.sleep(0.2)
        capture(cdp, 1440, 900, os.path.join(outdir, "start-current-1440.png"))
        print("start-cur 1440  sub=%s" % shot.eval_js(cdp, "document.querySelector('#sub-current').textContent"))
        m, _ = capture(cdp, 390, 844, os.path.join(outdir, "start-current-390.png"))
        if m["sw"] - m["iw"] > 1:
            bad.append("start-current-390 overflow")

        if args.full:
            shot.eval_js(cdp, CLICK_SCREEN % "done")
            time.sleep(0.3)
            m, size = capture(cdp, 390, 844, os.path.join(outdir, "done-390-full.png"), full=True)
            print("done-390-full  %dx%d  %.0fKB" % (390, m["sh"], size / 1024))

        errs = cdp.errors()
        if errs:
            bad.append("console: " + str(errs[:2]))
        print("CONSOLE ERROR %d" % len(errs))
    finally:
        proc.kill()
        time.sleep(0.2)

    if bad:
        print("FAIL")
        for item in bad:
            print("  - " + item)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
