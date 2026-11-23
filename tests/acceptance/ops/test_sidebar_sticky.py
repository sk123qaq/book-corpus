#!/usr/bin/env python3
"""书库操作台桌面端左侧导航固定（sticky）与窄屏回落回归验收。

  python3 tests/acceptance/ops/test_sidebar_sticky.py
  TMPDIR=/tmp uv run --frozen python tests/acceptance/ops/test_sidebar_sticky.py --screenshot-dir /tmp/ev

背景：`apps/ops/web/style.css` 的 `.sidebar` 虽声明了 `position: sticky; top: 0;`，但若
`html` 与 `body` 设为 `overflow-x: hidden`，会令 `overflow-y` 计算为 `auto`、`body` 成为
包裹整页高度的滚动容器，从而使 `.sidebar` 失去视口吸顶基准、随页面上下滚动而移出视口。
改为 `overflow-x: clip` 并显式补 `align-self: start` 后，桌面端固定吸顶且零横向溢出。

判定（没有 Chrome 的环境打印 DOM SKIP，仍完成静态 CSS 与服务响应校验）：
- `apps/ops/web/style.css` 与 `GET /style.css` 中 `html`/`body` 为 `overflow-x: clip`（非 `hidden`），
  `.sidebar` 为 `position: sticky` 与 `top: 0`；
- 桌面 `1440×700` 与 `1440×900` 滚到底部后，`.sidebar` 保持 `abs(top) <= 1`、`abs(bottom - innerHeight) <= 1`，
  `.brand` 与 `#theme-toggle` 留在视口内，`.topbar` 随右侧上滚，且在页底直接点击左侧导航可切换到 `#maintain`；
- 窄屏 `390×844` 下 `.sidebar` 保持 `position: static`，滚下后顶部导航正常滚走、不遮挡正文，全程无横向溢出与控制台错误。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STYLE_CSS = REPO / "apps" / "ops" / "web" / "style.css"

sys.path.insert(0, str(REPO / "tests" / "acceptance" / "ops"))
import check_ops


def find_chrome() -> str:
    import os

    env = os.environ.get("CHROME_BIN")
    if env:
        return env
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _extract_top_rule(css: str, selector: str) -> str:
    pattern = rf"(?:^|\n){re.escape(selector)}\s*\{{([^}}]*)\}}"
    match = re.search(pattern, css)
    assert match is not None, f"style.css 里找不到顶层规则 {selector}"
    return match.group(1)


def _norm_decl(block: str) -> str:
    return re.sub(r"\s+", " ", block).strip().lower()


def check_static_css(port: int) -> None:
    raw = STYLE_CSS.read_text(encoding="utf-8")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/style.css", timeout=10) as resp:
        assert resp.status == 200, f"GET /style.css 状态码异常：{resp.status}"
        served = resp.read().decode("utf-8")
    assert served == raw, "服务返回的 /style.css 与磁盘不一致"

    cleaned = _strip_css_comments(served)
    for selector in ("html", "body"):
        decl = _norm_decl(_extract_top_rule(cleaned, selector))
        assert "overflow-x: hidden" not in decl, f"{selector} 仍存在 overflow-x: hidden：{decl}"
        assert "overflow-x:hidden" not in decl, f"{selector} 仍存在 overflow-x:hidden：{decl}"
        assert "overflow-x: clip" in decl or "overflow-x:clip" in decl, f"{selector} 缺少 overflow-x: clip：{decl}"

    sidebar_decl = _norm_decl(_extract_top_rule(cleaned, ".sidebar"))
    assert "position: sticky" in sidebar_decl or "position:sticky" in sidebar_decl, (
        f".sidebar 缺少 position: sticky：{sidebar_decl}"
    )
    assert "top: 0" in sidebar_decl or "top:0" in sidebar_decl, f".sidebar 缺少 top: 0：{sidebar_decl}"
    print("PASS 静态与服务下发的 style.css 符合 overflow-x: clip 与 sticky 约束")


def _wait_ready(shot, cdp, selector: str, expected_text: str | None = None, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if expected_text is None:
            ok = shot.eval_js(
                cdp, f"!!document.querySelector({selector!r}) && !!document.querySelector('#stats .stat')"
            )
        else:
            ok = shot.eval_js(
                cdp,
                f"((el) => !!el && el.textContent.trim() === {expected_text!r})(document.querySelector({selector!r}))",
            )
        if ok:
            return
        time.sleep(0.1)
    raise AssertionError(f"等待页面就绪超时：{selector} ({expected_text})")


def _save_png(cdp, out_dir: Path | None, filename: str) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True, "captureBeyondViewport": False})
    (out_dir / filename).write_bytes(base64.b64decode(snap["data"]))


def _desktop_scroll_js(target_y: int | str) -> str:
    arg = json.dumps(target_y)
    return f"""
((targetY) => {{
  const se = document.scrollingElement || document.documentElement;
  const y = targetY === 'bottom' ? se.scrollHeight : Number(targetY);
  window.scrollTo(0, y);
  const html = getComputedStyle(document.documentElement);
  const body = getComputedStyle(document.body);
  const side = document.querySelector('.sidebar');
  const topbar = document.querySelector('.topbar');
  const brand = document.querySelector('.brand');
  const toggle = document.querySelector('#theme-toggle');
  const sc = getComputedStyle(side);
  const sr = side.getBoundingClientRect();
  const tr = topbar.getBoundingClientRect();
  const br = brand.getBoundingClientRect();
  const fr = toggle.getBoundingClientRect();
  const lastEl = document.querySelector('#view > :last-child');
  return {{
    htmlOx: html.overflowX,
    htmlOy: html.overflowY,
    bodyOx: body.overflowX,
    bodyOy: body.overflowY,
    sidePos: sc.position,
    sideTop: sc.top,
    scrollY: window.scrollY,
    scrollHeight: se.scrollHeight,
    vh: window.innerHeight,
    vw: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    srTop: +sr.top.toFixed(1),
    srBottom: +sr.bottom.toFixed(1),
    topbarTop: +tr.top.toFixed(1),
    brandTop: +br.top.toFixed(1),
    brandBottom: +br.bottom.toFixed(1),
    toggleTop: +fr.top.toFixed(1),
    toggleBottom: +fr.bottom.toFixed(1),
    lastCardBottom: lastEl ? +lastEl.getBoundingClientRect().bottom.toFixed(1) : -1,
  }};
}})({arg})
"""


def _mobile_scroll_js(y: int) -> str:
    return f"""
((y) => {{
  const mobBtn = document.querySelector('#theme-toggle-mobile');
  const mobCs = mobBtn ? getComputedStyle(mobBtn) : null;
  const mobRect = mobBtn ? mobBtn.getBoundingClientRect() : null;
  window.scrollTo(0, y);
  const side = document.querySelector('.sidebar');
  const sc = getComputedStyle(side);
  const sr = side.getBoundingClientRect();
  return {{
    sidePos: sc.position,
    scrollY: window.scrollY,
    srTop: +sr.top.toFixed(1),
    srBottom: +sr.bottom.toFixed(1),
    vw: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    mobDisplay: mobCs ? mobCs.display : '',
    mobWidth: mobRect ? +mobRect.width.toFixed(1) : 0,
    mobHeight: mobRect ? +mobRect.height.toFixed(1) : 0,
    mobRight: mobRect ? +mobRect.right.toFixed(1) : -1,
  }};
}})({y})
"""


WIDE_CENTER_PROBE = """
(() => {
  window.scrollTo(0, 0);
  const ws = document.querySelector('.workspace').getBoundingClientRect();
  const mn = document.querySelector('main').getBoundingClientRect();
  const ti = document.querySelector('.topbar-inner').getBoundingClientRect();
  const br = document.querySelector('.brand').getBoundingClientRect();
  const tb = document.querySelector('.topbar').getBoundingClientRect();
  const st = document.querySelector('#stats').getBoundingClientRect();
  return {
    hasSearchBtn: !!document.querySelector('#search-btn'),
    hasSearchStart: !!document.querySelector('#search-start'),
    mainLeft: +mn.left.toFixed(1),
    mainWidth: +mn.width.toFixed(1),
    topLeft: +ti.left.toFixed(1),
    mainLeftGap: +(mn.left - ws.left).toFixed(1),
    mainRightGap: +(ws.right - mn.right).toFixed(1),
    topLeftGap: +(ti.left - ws.left).toFixed(1),
    topRightGap: +(ws.right - ti.right).toFixed(1),
    brandBottom: +br.bottom.toFixed(1),
    topbarTop: +tb.top.toFixed(1),
    topbarBottom: +tb.bottom.toFixed(1),
    statsTop: +st.top.toFixed(1),
    statsBottom: +st.bottom.toFixed(1),
    vw: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  };
})()
"""


def _assert_desktop_sticky(probe: dict, vh: int, min_scroll: float, max_topbar_top: float, label: str) -> None:
    assert probe["htmlOx"] == "clip", f"{label} html overflowX 异常：{probe}"
    assert probe["htmlOy"] == "visible", f"{label} html overflowY 异常：{probe}"
    assert probe["bodyOx"] == "clip", f"{label} body overflowX 异常：{probe}"
    assert probe["bodyOy"] == "visible", f"{label} body overflowY 异常：{probe}"
    assert probe["sidePos"] == "sticky", f"{label} .sidebar 非 sticky：{probe}"
    assert probe["sideTop"] == "0px", f"{label} .sidebar top 非 0px：{probe}"
    assert probe["scrollY"] >= min_scroll, f"{label} 未滚动到预期距离：{probe}"
    assert probe["topbarTop"] <= max_topbar_top, f"{label} 右侧顶栏未随滚动上移：{probe}"
    assert abs(probe["srTop"]) <= 1.0, f"{label} 左侧栏 top 偏移：{probe}"
    assert abs(probe["srBottom"] - vh) <= 1.0, f"{label} 左侧栏未铺满视口高度：{probe}"
    assert probe["brandTop"] >= 0, f"{label} .brand 顶部超出视口：{probe}"
    assert probe["brandBottom"] <= vh, f"{label} .brand 底部超出视口：{probe}"
    assert probe["toggleTop"] >= 0, f"{label} #theme-toggle 顶部超出视口：{probe}"
    assert probe["toggleBottom"] <= vh, f"{label} #theme-toggle 底部超出视口：{probe}"
    assert probe["scrollWidth"] <= probe["vw"], f"{label} 发生横向溢出：{probe}"


def _dom_checks(cdp, shot, port: int, screenshot_dir: Path | None) -> None:
    cdp.call("Runtime.enable")
    cdp.call("Log.enable")
    cdp.call("Page.enable")
    base = f"http://127.0.0.1:{port}"

    # 1) 桌面 1440x700：初始、中段、页底与页底直接点左侧导航
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 700, "deviceScaleFactor": 1, "mobile": False},
    )
    cdp.call("Page.navigate", {"url": f"{base}/#overview"})
    _wait_ready(shot, cdp, "#view h1", "总览")

    at_top = shot.eval_js(cdp, _desktop_scroll_js(0))
    assert abs(at_top["srTop"]) <= 1.0, f"1440x700 顶部状态 top 异常：{at_top}"
    assert abs(at_top["srBottom"] - 700) <= 1.0, f"1440x700 顶部状态 bottom 异常：{at_top}"
    assert at_top["scrollHeight"] > 700, f"总览页高度不足以触发滚动：{at_top}"

    at_mid = shot.eval_js(cdp, _desktop_scroll_js(350))
    _assert_desktop_sticky(at_mid, 700, min_scroll=300, max_topbar_top=-200, label="1440x700 中段")

    at_bot = shot.eval_js(cdp, _desktop_scroll_js("bottom"))
    _assert_desktop_sticky(at_bot, 700, min_scroll=300, max_topbar_top=-200, label="1440x700 页底")
    assert abs(at_bot["lastCardBottom"] - 700) <= 2.0, f"1440x700 末张卡片下端未贴齐页面下端：{at_bot}"
    _save_png(cdp, screenshot_dir, "ops-sidebar-scrolled-1440x700.png")
    print(
        f"PASS 1440x700 滚到底部（scrollY={at_bot['scrollY']}, topbarTop={at_bot['topbarTop']}）"
        f"后左侧栏固定 top={at_bot['srTop']} bottom={at_bot['srBottom']}"
    )

    # 在页底不滚回顶部时，直接点击固定侧栏里的「同步与维护」
    clicked = shot.eval_js(
        cdp,
        """(() => {
          const link = document.querySelector('#nav a[href="#maintain"]');
          if (!link) return false;
          link.click();
          return true;
        })()""",
    )
    assert clicked is True, "找不到左侧导航 #maintain 链接"
    _wait_ready(shot, cdp, "#view h1", "同步与维护")
    maintain_bot = shot.eval_js(cdp, _desktop_scroll_js("bottom"))
    assert abs(maintain_bot["srTop"]) <= 1.0, f"1440x700 #maintain 侧栏 top 偏移：{maintain_bot}"
    assert abs(maintain_bot["srBottom"] - 700) <= 1.0, f"1440x700 #maintain 侧栏 bottom 偏移：{maintain_bot}"
    print(f"PASS 1440x700 页底直点 #maintain 切换成功，侧栏保持 top={maintain_bot['srTop']}")

    # 2) 桌面 1440x900：切回 #overview 并滚到底部
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
    )
    shot.eval_js(cdp, "document.querySelector('#nav a[href=\"#overview\"]').click(); window.scrollTo(0, 0); true")
    _wait_ready(shot, cdp, "#view h1", "总览")
    time.sleep(0.15)
    at_900 = shot.eval_js(cdp, _desktop_scroll_js("bottom"))
    _assert_desktop_sticky(at_900, 900, min_scroll=150, max_topbar_top=-150, label="1440x900 页底")
    assert abs(at_900["lastCardBottom"] - 900) <= 2.0, f"1440x900 末张卡片下端未贴齐页面下端：{at_900}"
    _save_png(cdp, screenshot_dir, "ops-sidebar-scrolled-1440x900.png")
    print(
        f"PASS 1440x900 滚到底部（scrollY={at_900['scrollY']}, topbarTop={at_900['topbarTop']}）"
        f"后左侧栏固定 top={at_900['srTop']} bottom={at_900['srBottom']}"
    )

    # 3) 宽屏 1920x1080：无顶栏搜索按钮，main 与 .topbar-inner 限宽且在 .workspace 内水平居中对齐
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False},
    )
    time.sleep(0.15)
    wide = shot.eval_js(cdp, WIDE_CENTER_PROBE)
    assert wide["hasSearchBtn"] is False, f"顶栏仍残留 #search-btn：{wide}"
    assert wide["hasSearchStart"] is False, f"顶栏仍残留 #search-start：{wide}"
    assert wide["mainWidth"] <= 1200.5, f"main 超过 1200px 上限：{wide}"
    assert abs(wide["mainLeftGap"] - wide["mainRightGap"]) <= 2.0, f"main 未在工作区水平居中：{wide}"
    assert abs(wide["topLeftGap"] - wide["topRightGap"]) <= 2.0, f".topbar-inner 未在工作区水平居中：{wide}"
    assert abs(wide["mainLeft"] - wide["topLeft"]) <= 1.0, f"main 与 .topbar-inner 左缘未对齐：{wide}"
    assert abs(wide["brandBottom"] - wide["topbarBottom"]) <= 1.0, f"左右两条顶部分隔线未对齐：{wide}"
    top_gap = wide["statsTop"] - wide["topbarTop"]
    bottom_gap = (wide["topbarBottom"] - 1.0) - wide["statsBottom"]
    assert abs(top_gap - bottom_gap) <= 2.0, f"右侧顶栏文字未垂直居中：{wide}"
    assert wide["scrollWidth"] <= wide["vw"], f"1920x1080 发生横向溢出：{wide}"
    _save_png(cdp, screenshot_dir, "ops-centered-1920x1080.png")
    print(
        f"PASS 1920x1080 无搜索按钮，main 宽={wide['mainWidth']}px "
        f"居中留白={wide['mainLeftGap']}/{wide['mainRightGap']}px"
    )

    # 3b) 宽屏 1920x1080 不满一屏页面（#manual）：自然排布在上方，不强行拉到底端
    shot.eval_js(cdp, "location.hash = '#manual'; true")
    _wait_ready(shot, cdp, "#view h1", "正文生产")
    time.sleep(0.15)
    short = shot.eval_js(cdp, _desktop_scroll_js(0))
    assert short["scrollHeight"] <= 1080, f"1920x1080 #manual 不应超出视口高度：{short}"
    assert short["lastCardBottom"] > 0, f"1920x1080 #manual 未渲染末张卡片：{short}"
    assert short["lastCardBottom"] < 900, f"1920x1080 不满一屏卡片被意外拉到底端：{short}"
    _save_png(cdp, screenshot_dir, "ops-short-page-1920x1080.png")
    print(f"PASS 1920x1080 不满一屏页 #manual 末卡自然停在 y={short['lastCardBottom']}")

    # 4) 窄屏 390x844：侧栏回落 static，随页面正常滚走且移动端主题按钮可见、零溢出
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
    )
    shot.eval_js(cdp, "location.hash = '#overview'; window.scrollTo(0, 0); true")
    _wait_ready(shot, cdp, "#view h1", "总览")
    time.sleep(0.2)
    mob = shot.eval_js(cdp, _mobile_scroll_js(300))
    assert mob["sidePos"] == "static", f"390x844 下 .sidebar 应回落 static：{mob}"
    assert mob["mobDisplay"] != "none", f"390x844 下 #theme-toggle-mobile 不可见：{mob}"
    assert mob["mobWidth"] > 0, f"390x844 下 #theme-toggle-mobile 宽度异常：{mob}"
    assert mob["mobHeight"] > 0, f"390x844 下 #theme-toggle-mobile 高度异常：{mob}"
    assert mob["mobRight"] <= mob["vw"], f"390x844 下 #theme-toggle-mobile 越出视口：{mob}"
    assert mob["scrollY"] >= 200, f"390x844 未滚下：{mob}"
    assert mob["srTop"] < -100, f"390x844 顶部导航未随页面滚走：{mob}"
    assert mob["scrollWidth"] <= mob["vw"], f"390x844 发生横向溢出：{mob}"
    _save_png(cdp, screenshot_dir, "ops-mobile-scrolled-390x844.png")
    print(f"PASS 390x844 侧栏回落 static（scrollY={mob['scrollY']}, sidebarTop={mob['srTop']}），零横向溢出")

    cdp.wait_events(0.3)
    errors = cdp.errors()
    assert not errors, f"控制台存在报错：{errors}"
    print("PASS 全程控制台 0 错误")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="操作台左侧栏固定验收")
    parser.add_argument("--screenshot-dir", default="", help="可选：保存桌面与手机滚动后截图到该目录")
    args = parser.parse_args(argv)
    screenshot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None

    root_str, fixture = check_ops.build_root()
    root = Path(root_str)
    srv, port = check_ops.start(root_str, fixture)
    try:
        check_static_css(port)
        chrome = find_chrome()
        if not chrome:
            print("DOM SKIP 没找到 Chrome（设 CHROME_BIN 或装 chromium 后重跑，才验证侧栏固定）")
        else:
            sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
            import shot

            cproc, profile, ws_url = shot.launch(1440, 700, "about:blank", 0)
            cdp = shot.connect_cdp(ws_url)
            try:
                _dom_checks(cdp, shot, port, screenshot_dir)
            finally:
                cdp.ws.close()
                cproc.kill()
                cproc.wait(timeout=10)
                shutil.rmtree(profile, ignore_errors=True)
    finally:
        check_ops.stop(srv)
        shutil.rmtree(root, ignore_errors=True)

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
