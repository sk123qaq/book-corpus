#!/usr/bin/env python3
"""用 CDP 截图与查控制台：headless Chrome + 一个最小 WebSocket 客户端（只用标准库）。

  python3 验收/shot.py --width 1440 --height 900 --scroll stage4 --out 验收/回答-1440x900.png
  python3 验收/shot.py --width 390 --height 844 --scroll hit-center --out 验收/手机-命中块-390x844.png
  python3 验收/shot.py --width 1440 --height 900 --click-cite      # 只查交互与控制台，不出图
  python3 验收/shot.py --click-all --question "内卷和教育之间是什么关系"  # 点全选，查多书回答

做的事情：
  1. 起 headless Chrome（临时 profile，远程调试口 9222+）
  2. 打开页面，等回答卡不再是加载态
  3. --click-all 时再点第 4 阶段的全选按钮，等新的多书回答出来（上限 120 秒），
     报选中卡数、回答是不是多书模式、引文盖了几本书；没有全选按钮或回答不出来就是硬失败
  4. 按 --scroll 滚到结果阶段或命中块，截图
  5. 汇总 Runtime.exceptionThrown / console.error / Log.error，末行打印 CONSOLE ERROR n
  6. 顺带报横向溢出：document.documentElement.scrollWidth > innerWidth

不装任何包，不改演示页；只写 --out 指定的图。
"""

import argparse
import base64
import contextlib
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "google-chrome-stable"
DEFAULT_URL = "http://127.0.0.1:8765/classic"
QUESTION = "《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球"


class WS:
    """够用就行的 WebSocket 客户端：握手、发文本帧、收帧。"""

    def __init__(self, url, timeout=30):
        assert url.startswith("ws://")
        rest = url[len("ws://") :]
        hostport, path = rest.split("/", 1)
        path = "/" + path
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手断开")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("握手失败：%r" % buf.split(b"\r\n", 1)[0])
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < (1 << 16):
            head.append(0x80 | 126)
            head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        b1, b2 = self._recv(2)
        opcode = b1 & 0x0F
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._recv(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._recv(8))[0]
        payload = self._recv(n)
        if opcode == 0x9:  # ping
            self.send(payload.decode("utf-8", "replace"))
            return self.recv()
        if opcode == 0x8:
            raise RuntimeError("对端关闭")
        return payload.decode("utf-8", "replace")

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0
        self.events = []

    def call(self, method, params=None, timeout=40):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if "id" in msg:
                if msg["id"] != mid:
                    continue
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result") or {}
            self.events.append(msg)
        raise RuntimeError("%s 超时" % method)

    def wait_events(self, seconds):
        """在 seconds 内持续收事件（用于等延迟报错）。"""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.ws.sock], [], [], max(0.0, end - time.time()))
            if not ready:
                break
            try:
                msg = json.loads(self.ws.recv())
            except (TimeoutError, OSError, RuntimeError):
                break
            self.events.append(msg)

    def errors(self):
        out = []
        for ev in self.events:
            method = ev.get("method")
            params = ev.get("params") or {}
            if method == "Runtime.exceptionThrown":
                det = params.get("exceptionDetails") or {}
                text = (det.get("exception") or {}).get("description") or det.get("text") or ""
                out.append("exception: " + " ".join(str(text).split())[:300])
            elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
                args = params.get("args") or []
                text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
                out.append("console.error: " + " ".join(text.split())[:300])
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                if entry.get("level") == "error":
                    out.append("log: " + " ".join(str(entry.get("text", "")).split())[:300])
        return out


def page_ws(port, timeout=30):
    """拿页面 target 的 ws 地址（/json/version 给的是浏览器级，不能调 Runtime）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as r:
                for target in json.load(r):
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
        except (urllib.error.URLError, ValueError, OSError):
            pass
        time.sleep(0.3)
    return None


def launch(width, height, url, port):
    profile = tempfile.mkdtemp(prefix="shot-profile-")
    cmd = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=" + profile,
        "--window-size=%d,%d" % (width, height),
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = page_ws(port)
    if not ws_url:
        proc.kill()
        raise RuntimeError("连不上 Chrome 调试口")
    return proc, profile, ws_url


def eval_js(cdp, expr, await_promise=False, timeout=40):
    res = cdp.call(
        "Runtime.evaluate",
        {
            "expression": expr,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True,
        },
        timeout=timeout,
    )
    if res.get("exceptionDetails"):
        raise RuntimeError(str(res["exceptionDetails"])[:400])
    return (res.get("result") or {}).get("value")


WAIT_READY = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const card = document.querySelector('.answer-card');
    const cites = document.querySelectorAll('.cite').length;
    const done = card && !card.classList.contains('is-loading');
    if ((done && (cites > 0 || document.querySelector('.answer-card.is-empty') ||
                  document.querySelector('.answer-card.is-error'))) || Date.now() - t0 > 25000) {
      resolve({done: !!done, cites, ms: Date.now() - t0});
    } else {
      setTimeout(tick, 150);
    }
  };
  tick();
})
"""

WAIT_ALL = """
new Promise((resolve) => {
  const btn = document.querySelector('#select-all');
  if (!btn) { resolve({button: false}); return; }
  const cards = document.querySelectorAll('.book-card').length;
  btn.click();
  const t0 = Date.now();
  let loading = false;
  const tick = () => {
    const card = document.querySelector('.answer-card');
    if (card && card.classList.contains('is-loading')) loading = true;
    const done = card && !card.classList.contains('is-loading');
    const books = new Set(Array.from(document.querySelectorAll('.cite-book'))
      .map((el) => el.textContent.trim()).filter(Boolean));
    const meta = document.querySelector('.answer-card .answer-meta');
    const metaText = meta ? meta.textContent : '';
    if ((loading && done) || Date.now() - t0 > 120000) {
      resolve({
        button: true, cards, loading, done: !!done, ms: Date.now() - t0,
        selected: document.querySelectorAll('.book-card.selected').length,
        multi: /本书/.test(metaText), books: books.size,
      });
    } else {
      setTimeout(tick, 200);
    }
  };
  tick();
})
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--out", default="")
    ap.add_argument("--scroll", default="stage4", choices=("none", "stage3", "stage4", "hit", "hit-center"))
    ap.add_argument("--click-cite", action="store_true")
    ap.add_argument("--click-all", action="store_true")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--question", default=QUESTION)
    a = ap.parse_args(argv)

    url = a.url
    if not url:
        url = DEFAULT_URL + "?q=" + urllib.parse.quote(a.question) + "&instant=1&top=1"
    proc, _profile, ws_url = launch(a.width, a.height, url, a.port)
    errors, notes = [], []
    all_fail = False
    try:
        cdp = CDP(WS(ws_url))
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": a.width, "height": a.height, "deviceScaleFactor": 1, "mobile": a.width < 600},
        )
        cdp.call("Page.navigate", {"url": url})
        time.sleep(0.8)
        ready = eval_js(cdp, WAIT_READY, await_promise=True, timeout=45)
        notes.append("answer ready=%s cites=%s wait=%s ms" % (ready.get("done"), ready.get("cites"), ready.get("ms")))
        if a.click_all:
            multi = eval_js(cdp, WAIT_ALL, await_promise=True, timeout=140)
            all_fail = not (
                multi.get("button")
                and multi.get("loading")
                and multi.get("done")
                and multi.get("multi")
                and (multi.get("books") or 0) >= 2
                and (multi.get("selected") or 0) >= (multi.get("cards") or 0)
            )
            notes.append(
                "select-all: button=%s cards=%s selected=%s loading=%s done=%s "
                "multi=%s books=%s wait=%s ms"
                % (
                    multi.get("button"),
                    multi.get("cards"),
                    multi.get("selected"),
                    multi.get("loading"),
                    multi.get("done"),
                    multi.get("multi"),
                    multi.get("books"),
                    multi.get("ms"),
                )
            )
            notes.append("SELECT ALL " + ("FAIL" if all_fail else "PASS"))
        if a.scroll == "stage3":
            eval_js(cdp, "document.querySelector('[data-stage=\"3\"]').scrollIntoView({block:'start'});true")
        elif a.scroll == "stage4":
            eval_js(cdp, "document.querySelector('[data-stage=\"4\"]').scrollIntoView({block:'start'});true")
        elif a.scroll == "hit":
            eval_js(
                cdp, "const el=document.querySelector('.hit-para');if (el) el.scrollIntoView({block:'start'}); true"
            )
        elif a.scroll == "hit-center":
            eval_js(
                cdp, "const el=document.querySelector('.hit-para');if (el) el.scrollIntoView({block:'center'}); true"
            )
        time.sleep(0.4)
        notes.append(
            "overflow=%s (scrollWidth %s vs %s)"
            % (
                eval_js(cdp, "document.documentElement.scrollWidth > window.innerWidth"),
                eval_js(cdp, "document.documentElement.scrollWidth"),
                eval_js(cdp, "window.innerWidth"),
            )
        )
        if a.click_cite:
            before = eval_js(cdp, "document.querySelector('.result-foot code').textContent")
            eval_js(cdp, "document.querySelector('.cite').click(); true")
            time.sleep(1.2)
            after = eval_js(cdp, "document.querySelector('.result-foot code').textContent")
            active = eval_js(cdp, "!!document.querySelector('.cite.active')")
            answer_kept = eval_js(cdp, "!!document.querySelector('.answer-card .answer-text')")
            notes.append("cite click: %s -> %s active=%s answer_kept=%s" % (before, after, active, answer_kept))
        if a.out:
            shot = cdp.call(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
                timeout=60,
            )
            data = base64.b64decode(shot["data"])
            out = a.out if os.path.isabs(a.out) else os.path.join(os.path.dirname(HERE), a.out)
            out = os.path.abspath(out)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
            notes.append("saved %s (%d bytes)" % (out, len(data)))
        cdp.wait_events(0.6)
        errors = cdp.errors()
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for line in notes:
        print(line)
    for err in errors:
        print("ERROR " + err)
    print("CONSOLE ERROR %d" % len(errors))
    return 0 if not errors and not all_fail else 1


if __name__ == "__main__":
    sys.exit(main())
