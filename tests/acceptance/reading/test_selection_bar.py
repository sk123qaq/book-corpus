#!/usr/bin/env python3
"""8771 章页划词工具栏可见性验收（真 headless Chrome + CDP）。

  python3 tests/acceptance/reading/test_selection_bar.py     # DOM 层需要 Chrome
  PYTHONPATH=src python3 -m pytest tests/acceptance/reading/test_selection_bar.py -q

背景：PR #26（f74e7bc）给 `chapter.html` 的 `#selection-bar` 加了静态 `hidden`，又引入
`[hidden] { display: none !important; }`（`workbench.css:2`、`pages.css:2`），压过
`.selection-bar.show { display: inline-flex; }`；`positionSelectionBar()` 只加 `show` 类、
`hideSelectionBar()` 只删 `show` 类，从不改 `hidden` 属性，于是工具栏永远 `display:none`：
框选后 `.show` 在、9 个按钮在 DOM 里，但 rect 恒为 0×0。只断言类名或节点存在抓不到这个回归。

判定（没有 Chrome 的环境跳过 DOM 层，仍完成 API 与静态资源验证）：
- `/api/chapter` 正常，`chapter.html` 里 `#selection-bar` 不再带静态 `hidden`；
- 跨段框选后：`bar.hidden === false`、`getComputedStyle(bar).display !== 'none'`、
  `getBoundingClientRect().width > 0`，按钮数 ≥ 5 且第一行为
  讲人话举例/翻译原意/拆解前提/自由追问/跨书找相关 ↗；
- Esc 与点正文空白处收起后 `hidden === true` 且 `display === 'none'`；
- 点段尾 `.blk-trigger` 也能唤出同一工具栏；
- 窄屏 390×844 下工具栏不出视口、不制造横向滚动；
- 点工具栏按钮后工具栏收起、伴读抽屉打开；末行 ALL PASS。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

FIRST_ROW = ["讲人话举例", "翻译原意", "拆解前提", "自由追问", "跨书找相关 ↗"]

SELECT_JS = r"""
(() => {
  const a = document.getElementById('blk-B02');
  const b = document.getElementById('blk-B03');
  if (!a || !b) return {error: 'blocks missing'};
  const first = document.createTreeWalker(a, NodeFilter.SHOW_TEXT).nextNode();
  const w2 = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
  let last = null, n;
  while ((n = w2.nextNode())) last = n;
  if (!first || !last) return {error: 'no text nodes'};
  const range = document.createRange();
  range.setStart(first, 0);
  range.setEnd(last, last.length);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.getElementById('reader-body').dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
  return {text: sel.toString().trim().slice(0, 20)};
})()
"""

PROBE_JS = r"""
(() => {
  const bar = document.getElementById('selection-bar');
  const cs = getComputedStyle(bar);
  const r = bar.getBoundingClientRect();
  const rows = Array.from(bar.querySelectorAll('.selection-bar-row')).map(
    (row) => Array.from(row.querySelectorAll('button')).map((b) => b.textContent.trim())
  );
  return {
    cls: bar.className, hidden: bar.hidden, display: cs.display,
    w: +r.width.toFixed(1), h: +r.height.toFixed(1),
    left: +r.left.toFixed(1), right: +r.right.toFixed(1),
    top: +r.top.toFixed(1), bottom: +r.bottom.toFixed(1),
    rows, buttons: rows.flat().length,
    vw: window.innerWidth, vh: window.innerHeight,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    drawerHidden: document.getElementById('assist-drawer').hidden,
  };
})()
"""


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_book(root, uid, title, chapters):
    book_dir = root / "books" / uid
    (book_dir / "md").mkdir(parents=True, exist_ok=True)
    for name, body in chapters:
        (book_dir / "md" / name).write_text(body, encoding="utf-8")
    payload = {
        "uid": uid,
        "title": title,
        "title_from_file": "",
        "author": "某人",
        "subjects": ["文学"],
        "retention": "keep",
        "sources": [
            {
                "format": "epub",
                "bytes": 100,
                "sha256": f"{int(uid[1:]):064x}",
                "primary_src": 1,
                "file": f"source/{title}.epub",
            }
        ],
        "awards": [],
        "notes": "",
    }
    (book_dir / "book.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _fixture_root(base):
    root = base / "selection-bar-instance"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    paras = [f"第 {i} 段：他把窗子推开一条缝，雨声就顺着缝挤了进来，桌角的灯芯跳了一下。" for i in range(1, 7)]
    _write_book(root, "B0001", "甲书", [("01_第一章.md", "# 第一章\n\n" + "\n\n".join(paras) + "\n")])
    return root


def _wait_shelf(port, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return _get_json(port, "/api/shelf")
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    raise AssertionError(f"8771 没起来：{last}")


def find_chrome():
    env = os.environ.get("CHROME_BIN")
    if env:
        return env
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return name
    return ""


def _start_reading(port, root):
    dead_model = _free_port()  # 没人监听：点击按钮只会立刻连接失败，不会打真模型
    env = dict(
        os.environ,
        READING_ANNOTATE="0",
        PYTHONDONTWRITEBYTECODE="1",
        HOME=str(root / "home"),
        OPENCODE_GO_API_KEY="offline-test-key",
        READING_MODEL_URL=f"http://127.0.0.1:{dead_model}",
        READING_MODEL_ATTEMPTS="1",
        READING_MODEL_FALLBACK="0",
        READING_MODEL_CONNECT_TIMEOUT="1",
        READING_MODEL_TIMEOUT="2",
    )
    (root / "home").mkdir(exist_ok=True)
    log = (root / "server.log").open("wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "apps" / "reading" / "server.py"),
            "--root",
            str(root),
            "--port",
            str(port),
            "--no-annotate",
        ],
        cwd=str(REPO),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def _dom_steps(cdp, shot, port):
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/chapter/B0001/01"})
    deadline = time.time() + 20
    while time.time() < deadline:
        if shot.eval_js(cdp, "!!document.querySelector('#blk-B03')"):
            break
        time.sleep(0.1)
    assert shot.eval_js(cdp, "!!document.querySelector('#blk-B03')"), "章页正文没渲染出来"
    assert shot.eval_js(cdp, "getComputedStyle(document.getElementById('selection-bar')).display") == "none"
    print("PASS 桌面前未框选时 #selection-bar 不可见")

    shot.eval_js(cdp, SELECT_JS)
    time.sleep(0.3)
    p = shot.eval_js(cdp, PROBE_JS)
    assert p["display"] != "none", f"框选后工具栏仍被隐藏：{p}"
    assert p["w"] > 0, p
    assert p["h"] > 0, p
    assert p["hidden"] is False, p
    assert p["buttons"] >= 5, p
    assert p["rows"][0] == FIRST_ROW, p["rows"]
    print(f"PASS 桌面框选后工具栏可见 {p['w']}×{p['h']}、{p['buttons']} 个按钮")

    shot.eval_js(cdp, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); true")
    time.sleep(0.2)
    esc = shot.eval_js(
        cdp,
        """(() => { const b = document.getElementById('selection-bar');
           return {hidden: b.hidden, display: getComputedStyle(b).display}; })()""",
    )
    assert esc["hidden"] is True, esc
    assert esc["display"] == "none", esc
    print("PASS Esc 后 hidden=true 且 display:none")

    shot.eval_js(cdp, SELECT_JS)
    time.sleep(0.3)
    shot.eval_js(
        cdp,
        """(() => { window.getSelection().removeAllRanges();
           document.getElementById('blk-B05').dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
           return true; })()""",
    )
    time.sleep(0.2)
    assert shot.eval_js(cdp, "document.getElementById('selection-bar').hidden") is True
    print("PASS 点正文空白处后工具栏收起")

    shot.eval_js(
        cdp,
        """(() => { const t = document.querySelector('#blk-B03 .blk-trigger');
           if (!t) return false; t.click(); return true; })()""",
    )
    time.sleep(0.3)
    trig = shot.eval_js(cdp, PROBE_JS)
    assert trig["display"] != "none", trig
    assert trig["w"] > 0, trig
    assert trig["buttons"] == p["buttons"], trig
    print("PASS 段尾 ✦ 触发按钮同样唤出工具栏")

    cdp.call(
        "Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}
    )
    time.sleep(0.4)
    shot.eval_js(cdp, "window.getSelection().removeAllRanges(); window.scrollTo(0, 0); true")
    shot.eval_js(cdp, SELECT_JS)
    time.sleep(0.4)
    narrow = shot.eval_js(cdp, PROBE_JS)
    assert narrow["display"] != "none", narrow
    assert narrow["w"] > 0, narrow
    assert narrow["left"] >= 0, narrow
    assert narrow["right"] <= narrow["vw"], narrow
    assert narrow["bottom"] <= narrow["vh"], narrow
    assert narrow["overflow"] is False, narrow
    print(f"PASS 窄屏 390×844 工具栏可见且不出视口（{narrow['w']}×{narrow['h']}）")

    shot.eval_js(
        cdp,
        """(() => { const b = Array.from(document.querySelectorAll('#selection-bar button'))
             .find((x) => x.textContent.trim() === '讲人话举例');
           b.click(); return true; })()""",
    )
    deadline = time.time() + 10
    after = None
    while time.time() < deadline:
        after = shot.eval_js(cdp, PROBE_JS)
        if after["drawerHidden"] is False:
            break
        time.sleep(0.2)
    assert after["display"] == "none", after
    assert after["hidden"] is True, after
    assert after["drawerHidden"] is False, after
    print("PASS 点「讲人话举例」后工具栏收起、伴读抽屉打开")


def main():
    base = Path(tempfile.mkdtemp(prefix="reading-selection-bar-"))
    try:
        root = _fixture_root(base)
        port = _free_port()
        proc, log = _start_reading(port, root)
        try:
            shelf = _wait_shelf(port)
            assert shelf["stats"]["readable"] == 1, shelf["stats"]

            html = (
                urllib.request.urlopen(f"http://127.0.0.1:{port}/chapter/B0001/01", timeout=10).read().decode("utf-8")
            )
            marker = 'id="selection-bar"'
            idx = html.find(marker)
            assert idx >= 0, "chapter.html 里找不到 #selection-bar"
            tag_end = html.find(">", idx)
            assert "hidden" not in html[html.rfind("<", 0, idx) : tag_end], "静态 hidden 又回来了"
            print("PASS chapter.html 的 #selection-bar 不再带静态 hidden")

            chrome = find_chrome()
            if not chrome:
                print("DOM SKIP 没找到 Chrome（设 CHROME_BIN 或装 chromium 后重跑，才验证工具栏可见性）")
            else:
                sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
                import shot

                chrome_proc, profile, ws_url = shot.launch(1440, 900, "about:blank", 0)
                cdp = shot.connect_cdp(ws_url)
                try:
                    _dom_steps(cdp, shot, port)
                finally:
                    cdp.ws.close()
                    chrome_proc.kill()
                    chrome_proc.wait(timeout=10)
                    shutil.rmtree(profile, ignore_errors=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("ALL PASS")


if __name__ == "__main__":
    main()
