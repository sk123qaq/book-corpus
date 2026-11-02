#!/usr/bin/env python3
"""在真实 390 宽度下出图。headless 的 --window-size 最小只到 500，所以用 CDP 覆盖设备尺寸。

  python3 原型-追问/shoot.py            # 五个变体，1440x900 与 390x844，视口图
  python3 原型-追问/shoot.py --full     # 1440 再加一张整页长图

复用 验收/shot.py 的 CDP 客户端。
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

VARIANTS = [("v1", "v1-chat.html"), ("v2", "v2-split.html"), ("v3", "v3-tree.html"),
            ("v4", "v4-ledger.html"), ("v5", "v5-turn.html")]
SIZES = [("1440", 1440, 900), ("390", 390, 844)]

MEASURE = "({sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight, ext: [...document.querySelectorAll('[src],[href]')].map(e => e.getAttribute('src') || e.getAttribute('href')).filter(u => u && /^(https?:)?\\/\\//.test(u))})"


def capture(cdp, width, height, out, full=False):
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(0.8)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    bad = 0
    for key, name in VARIANTS:
        path = os.path.join(HERE, name)
        proc, profile, ws_url = shot.launch(1440, 900, "about:blank", 9500)
        try:
            cdp = shot.CDP(shot.WS(ws_url))
            cdp.call("Runtime.enable")
            cdp.call("Log.enable")
            cdp.call("Page.enable")
            cdp.call("Page.navigate", {"url": "file://" + path})
            time.sleep(1.0)
            for label, w, h in SIZES:
                m, size = capture(cdp, w, h,
                                  os.path.join(HERE, "shots", "%s-%s.png" % (key, label)))
                flag = " OVERFLOW +%d" % (m["sw"] - w) if m["sw"] - w > 1 else ""
                if m["ext"]:
                    flag += " EXT %s" % m["ext"][:2]
                print("%s %s  iw=%d sw=%d sh=%d  %.0fKB%s" % (
                    key, label, w, m["sw"], m["sh"], size / 1024, flag))
                if flag:
                    bad += 1
            if a.full:
                m, size = capture(cdp, 1440, 900,
                                  os.path.join(HERE, "shots", "%s-full.png" % key), full=True)
                print("%s full  %dx%d  %.0fKB" % (key, 1440, m["sh"], size / 1024))
            errs = cdp.errors()
            if errs:
                bad += 1
                print("%s console: %s" % (key, errs[:2]))
        finally:
            proc.kill()
            time.sleep(0.2)
            import shutil
            shutil.rmtree(profile, ignore_errors=True)
    print("ALL PASS" if not bad else "FAIL %d" % bad)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
