#!/usr/bin/env python3
"""Drive the ops console's six modules and compat subtabs in headless Chromium.

Checks every nav entry renders a heading, records console errors, horizontal
overflow at 1440x900 and 390x844, and writes one screenshot.

Usage:
  python3 .agents/skills/verify-book-corpus/scripts/drive_ops_tabs.py \
    --base http://127.0.0.1:18770 --out "$EV"

Exit 0 only when every tab renders with no console error and no overflow.
CHROME_BIN picks the browser; the default is /usr/bin/chromium.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

TABS = ["overview", "import", "ledger", "split", "maintain", "logs"]
SUBTABS = ["retire", "cold", "docs", "pdf", "pdf-list", "manual"]
CHROME = os.environ.get("CHROME_BIN") or "/usr/bin/chromium"


class WS:
    def __init__(self, url: str, timeout: float = 30.0):
        rest = url[len("ws://") :]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("handshake closed")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("handshake failed: %r" % buf.split(b"\r\n", 1)[0])
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0

    def _recv(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise RuntimeError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        ln = len(data)
        if ln < 126:
            header = bytearray([0x81, 0x80 | ln])
        elif ln < 65536:
            header = bytearray([0x81, 0x80 | 126]) + struct.pack(">H", ln)
        else:
            header = bytearray([0x81, 0x80 | 127]) + struct.pack(">Q", ln)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header + mask) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        b2 = self._recv(2)[1]
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv(8))[0]
        return self._recv(ln).decode("utf-8", "replace")

    def call(self, method: str, params: dict | None = None) -> dict:
        self.mid += 1
        self.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.recv())
            if msg.get("id") == self.mid:
                return msg


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18770")
    ap.add_argument("--out", default=".")
    ap.add_argument("--chrome", default=CHROME)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    port = free_port()
    profile = tempfile.mkdtemp(prefix="ops-tabs-")
    proc = subprocess.Popen(
        [
            args.chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--remote-debugging-port=%d" % port,
            "--user-data-dir=" + profile,
            "--window-size=%d,%d" % (args.width, args.height),
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    errors: list[str] = []
    try:
        deadlines = time.time() + 25
        targets = None
        while time.time() < deadlines:
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port))
                if targets:
                    break
            except Exception:
                time.sleep(0.3)
        if not targets:
            print("SHOT FAIL: chromium did not expose a debugging target")
            return 1
        page = [t for t in targets if t.get("type") == "page"][0]
        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Log.enable")
        ws.call("Page.navigate", {"url": args.base + "/#overview"})
        time.sleep(2.5)

        def drain() -> None:
            ws.sock.settimeout(0.05)
            try:
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get("method") == "Runtime.exceptionThrown":
                        detail = msg["params"]["exceptionDetails"]
                        desc = (detail.get("exception") or {}).get("description", "")
                        errors.append("EXC " + str(detail.get("text") or "") + " " + str(desc)[:200])
                    elif msg.get("method") == "Log.entryAdded":
                        entry = msg["params"]["entry"]
                        if entry.get("level") == "error":
                            errors.append("LOG " + entry.get("text", "")[:200])
            except Exception:
                pass
            finally:
                ws.sock.settimeout(30.0)

        def ev(expr: str):
            out = ws.call(
                "Runtime.evaluate",
                {"expression": expr, "returnByValue": True, "awaitPromise": True},
            )
            return out["result"]["result"].get("value")

        report = []
        for tab in TABS + SUBTABS:
            ev("location.hash = '#%s'" % tab)
            time.sleep(1.0)
            drain()
            info = ev(
                "(()=>{const v=document.getElementById('view');"
                "const h=v.querySelector('h1,h2');"
                "return {h:(h&&h.textContent||'').trim().slice(0,40),"
                "len:(v.textContent||'').length,"
                "overflow: document.documentElement.scrollWidth > innerWidth+1,"
                "nav: document.querySelectorAll('#nav a').length};})()"
            )
            info["tab"] = tab
            report.append(info)

        ev("location.hash='#overview'")
        time.sleep(1.2)
        shot = ws.call("Page.captureScreenshot", {"format": "png"})
        shot_path = os.path.join(args.out, "ops-overview-%dx%d.png" % (args.width, args.height))
        with open(shot_path, "wb") as fh:
            fh.write(base64.b64decode(shot["result"]["data"]))

        ws.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        time.sleep(0.8)
        drain()
        mobile = ev("({overflow: document.documentElement.scrollWidth > innerWidth+1, sw: document.documentElement.scrollWidth})")

        bad = [r for r in report if not r.get("h") or r.get("overflow")]
        summary = {
            "base": args.base,
            "nav_links": report[0].get("nav") if report else None,
            "tabs": report,
            "console_errors": errors,
            "mobile": mobile,
            "screenshot": shot_path,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        if bad or errors or mobile.get("overflow"):
            print("SHOT FAIL: bad=%d errors=%d mobile_overflow=%s" % (len(bad), len(errors), mobile.get("overflow")))
            return 1
        print("CONSOLE ERROR 0")
        print("overflow=false")
        print("SHOT PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
