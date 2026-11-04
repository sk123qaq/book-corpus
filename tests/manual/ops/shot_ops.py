#!/usr/bin/env python3
"""统一操作台页面自检：headless Chrome + CDP，两个断点，报控制台错误与横向溢出。

  python3 tests/manual/ops/shot_ops.py
  python3 tests/manual/ops/shot_ops.py --url http://127.0.0.1:8770

自己起一个临时根的服务（stub 脚本 + fixture 状态），把七个页签都点一遍收
console.error 与异常，最后出 1440x900 与 390x844 两张图。末行 SHOT PASS。
"""

import argparse
import base64
import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contextlib

import check_ops

CHROME = os.environ.get("CHROME_BIN") or "google-chrome-stable"
VIEWPORTS = ((1440, 900), (390, 844))
TABS = (
    ("overview", "总览"),
    ("import", "新书导入"),
    ("split", "电子书出正文"),
    ("pdf", "PDF 出正文"),
    ("maintain", "维护"),
    ("retire", "删书"),
    ("logs", "日志"),
)


class WS:
    def __init__(self, url, timeout=30):
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
        if opcode == 0x9:
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

    def drain(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.ws.sock], [], [], max(0.0, end - time.time()))
            if not ready:
                break
            try:
                self.events.append(json.loads(self.ws.recv()))
            except (TimeoutError, OSError, RuntimeError):
                break

    def errors(self):
        out = []
        for ev in self.events:
            method = ev.get("method")
            params = ev.get("params") or {}
            if method == "Runtime.exceptionThrown":
                det = params.get("exceptionDetails") or {}
                text = (det.get("exception") or {}).get("description") or det.get("text") or ""
                out.append("exception: " + " ".join(str(text).split())[:200])
            elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
                args = params.get("args") or []
                text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
                out.append("console.error: " + " ".join(text.split())[:200])
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                if entry.get("level") == "error":
                    out.append("log: " + " ".join(str(entry.get("text", "")).split())[:200])
        return out


def page_ws(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as resp:
                for target in json.load(resp):
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
        except (urllib.error.URLError, ValueError, OSError):
            pass
        time.sleep(0.3)
    return None


def launch(port):
    profile = tempfile.mkdtemp(prefix="shot-ops-")
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
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = page_ws(port)
    if not ws_url:
        proc.kill()
        raise RuntimeError("连不上 Chrome 调试口（%s 装了吗）" % CHROME)
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
        raise RuntimeError(str(res["exceptionDetails"])[:300])
    return (res.get("result") or {}).get("value")


WAIT_READY = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const h1 = document.querySelector('#view h1');
    const stats = document.querySelector('#stats .stat');
    if ((h1 && stats) || Date.now() - t0 > 12000) {
      resolve({h1: h1 ? h1.textContent : '', stats: document.querySelectorAll('#stats .stat').length,
               ms: Date.now() - t0});
    } else {
      setTimeout(tick, 120);
    }
  };
  tick();
})
"""

VISIT_TAB = """
new Promise((resolve) => {
  const link = document.querySelector('#nav a[href="#%s"]');
  if (!link) { resolve({tab: '%s', ok: false, title: ''}); return; }
  link.click();
  const t0 = Date.now();
  const tick = () => {
    const h1 = document.querySelector('#view h1');
    const title = h1 ? h1.textContent.trim() : '';
    if (title === '%s' || Date.now() - t0 > 8000) {
      resolve({tab: '%s', ok: title === '%s', title: title, ms: Date.now() - t0});
    } else {
      setTimeout(tick, 100);
    }
  };
  tick();
})
"""


IMPORT_PLAN = """
(() => {
  const head = Array.from(document.querySelectorAll('#view h2'))
    .map((h) => h.textContent).find((t) => t.indexOf('入库清单') >= 0) || '';
  const btn = document.querySelector('[data-act="import-scan"]');
  return {rows: document.querySelectorAll('[data-field="action"]').length,
          head: head, disabled: btn ? !!btn.disabled : null};
})()
"""

WAIT_PLAN_CLICK = """
new Promise((resolve) => {
  const before = %s;
  const t0 = Date.now();
  const tick = () => {
    const head = Array.from(document.querySelectorAll('#view h2'))
      .map((h) => h.textContent).find((t) => t.indexOf('入库清单') >= 0) || '';
    const btn = document.querySelector('[data-act="import-scan"]');
    const rows = document.querySelectorAll('[data-field="action"]').length;
    if ((rows > 0 && head !== before) || Date.now() - t0 > 10000) {
      resolve({rows: rows, head: head, ms: Date.now() - t0,
               disabled: btn ? !!btn.disabled : null});
    } else {
      setTimeout(tick, 150);
    }
  };
  tick();
})
"""

WAIT_PLAN_LOAD = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const head = Array.from(document.querySelectorAll('#view h2'))
      .map((h) => h.textContent).find((t) => t.indexOf('入库清单') >= 0) || '';
    const rows = document.querySelectorAll('[data-field="action"]').length;
    if (rows > 0 || Date.now() - t0 > 10000) {
      resolve({rows: rows, head: head, ms: Date.now() - t0});
    } else {
      setTimeout(tick, 150);
    }
  };
  tick();
})
"""

SPLIT_CHECKS = """
(() => {
  const q = (s) => document.querySelector(s);
  const rows = () => Array.from(document.querySelectorAll('[data-act="split-pick"]'));
  const all = () => q('[data-act="split-all"]');
  const rep = () => q('#split-replace');
  const work = () => q('#split-workers');
  const snap = () => ({count: rows().length,
                       uid: rows()[0] ? rows()[0].value : '',
                       checked: rows().filter((r) => r.checked).length,
                       first: rows()[0] ? rows()[0].checked : null,
                       all: all().checked, rep: rep().checked, workers: work().value});
  const out = {start: snap()};
  if (!rows().length) { out.empty = true; return out; }
  if (all().checked) all().click();
  out.off = snap();
  all().click();
  out.on = snap();
  rows()[0].click();
  out.partial = snap();
  rows()[0].click();
  out.reon = snap();
  rep().click();
  work().value = '8';
  work().dispatchEvent(new Event('change', {bubbles: true}));
  rows()[0].click();
  out.rowOff = snap();
  all().click();
  out.final = snap();
  return out;
})()
"""

SPLIT_OPS_CHECKS = """
(() => {
  const text = (document.querySelector('#view') || {}).textContent || '';
  return {
    one: !!document.querySelector('[data-act="split-one"]'),
    recheck: !!document.querySelector('[data-act="split-recheck"]'),
    inspect: !!document.querySelector('[data-act="split-inspect"]'),
    hint: text.indexOf('重新检查只复核暂存区里已有的正文') >= 0,
  };
})()
"""

IMPORT_EDIT = """
(() => {
  const q = (s) => document.querySelector(s);
  const t = q('[data-field="title"]');
  const s = q('[data-field="subjects"]');
  if (!t || !s) return {title: null, subjects: null};
  t.value = '改过的名';
  t.dispatchEvent(new Event('change', {bubbles: true}));
  s.value = '文学/类型小说';
  s.dispatchEvent(new Event('change', {bubbles: true}));
  render();
  const u = q('[data-field="uid"]');
  return {title: q('[data-field="title"]').value,
          subjects: q('[data-field="subjects"]').value,
          uid: u ? u.value : ''};
})()
"""

RETIRE_MODAL = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const q = (s) => document.querySelector(s);
  const uid = %s;
  q('#retire-q').value = uid;
  q('[data-act="retire-search"]').click();
  for (let i = 0; i < 40 && !q('[data-act="retire-preview"][data-uid="' + uid + '"]'); i += 1) {
    await sleep(150);
  }
  const hit = q('[data-act="retire-preview"][data-uid="' + uid + '"]');
  if (!hit) return {opened: false, afterInput: false, afterOk: false, checked: null, closed: false};
  hit.click();
  for (let i = 0; i < 120 && !q('#modal .modal'); i += 1) await sleep(150);
  const opened = !!q('#modal .modal');
  if (!opened) return {opened: false, afterInput: false, afterOk: false, checked: null, closed: false};
  q('#apply-uid').click();
  await sleep(80);
  const afterInput = !!q('#modal .modal');
  q('#apply-confirm').click();
  await sleep(80);
  const afterOk = !!q('#modal .modal');
  const checked = q('#apply-confirm') ? q('#apply-confirm').checked : null;
  if (q('#modal [data-act="close-modal"]')) q('#modal [data-act="close-modal"]').click();
  await sleep(80);
  return {opened: opened, afterInput: afterInput, afterOk: afterOk,
          checked: checked, closed: !q('#modal .modal')};
})()
"""

PDF_INPUTS = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const q = (s) => document.querySelector(s);
  const text = () => ((q('#view') || {}).textContent || '');
  if (!q('#pdf-limit') || !q('#pdf-workers')) return {before: null, after: null};
  const put = (sel, val) => {
    q(sel).value = val;
    q(sel).dispatchEvent(new Event('change', {bubbles: true}));
  };
  put('#pdf-limit', '7');
  put('#pdf-workers', '5');
  const snap = () => ({limit: q('#pdf-limit').value, workers: q('#pdf-workers').value,
                       legacy: !!q('#triage-uid'),
                       triage: !!q('[data-act="pdf-triage-batch"]'),
                       quota: text().indexOf('今天额度') >= 0,
                       archive: text().indexOf('已封存') >= 0,
                       open: !!q('[data-act="pdf-pick-open"]')});
  const before = snap();
  q('[data-act="pdf-refresh"]').click();
  await sleep(1500);
  return {before: before, after: snap()};
})()
"""

PDF_LIST = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const q = (s) => document.querySelector(s);
  const title = () => (q('#view h1') ? q('#view h1').textContent.trim() : '');
  const until = async (fn, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(120); }
    return false;
  };
  const out = {open: !!q('[data-act="pdf-pick-open"]'), legacy: !!q('#pdf-uids')};
  if (!out.open) return out;
  q('[data-act="pdf-pick-open"]').click();
  out.jumped = await until(() => location.hash === '#pdf-list' && title() === '待转 PDF 清单', 12000);
  out.title = title();
  out.filters = {q: !!q('#pick-q'), tags: document.querySelectorAll('[data-act="pick-tag"]').length,
                 subject: !!q('#pick-subject'), sort: !!q('#pick-sort'),
                 progress: document.querySelectorAll('[data-act="pick-progress"]').length,
                 recheck: !!q('[data-act="pick-recheck"]'),
                 actions: document.querySelectorAll('[data-act="pick-settle"], [data-act="pick-suggest"]').length,
                 bar: !!q('#pick-bar')};
  const box = q('#pick-q');
  if (!box) return out;
  box.focus();
  box.value = 'zzz-没有这本';
  box.dispatchEvent(new Event('input', {bubbles: true}));
  await sleep(150);
  out.empty = ((q('#pick-body') || {}).textContent || '').indexOf('没有符合条件') >= 0;
  out.focus = document.activeElement === box;
  box.value = 'B';
  box.dispatchEvent(new Event('input', {bubbles: true}));
  await sleep(150);
  const rows = Array.from(document.querySelectorAll('[data-act="pick-one"]'));
  out.rows = rows.length;
  if (rows.length) rows[0].click();
  await sleep(150);
  out.picked = rows.filter((r) => r.checked).length;
  out.summary = (q('#pick-bar') || {}).textContent || '';
  document.querySelector('#nav a[href="#pdf"]').click();
  await until(() => title() === 'PDF 出正文', 8000);
  out.back = title();
  location.hash = '#pdf-list';
  await until(() => title() === '待转 PDF 清单', 12000);
  await sleep(200);
  out.keptTitle = title();
  out.keptQ = q('#pick-q') ? q('#pick-q').value : null;
  out.keptPick = Array.from(document.querySelectorAll('[data-act="pick-one"]'))
    .filter((r) => r.checked).length;
  out.wide = document.documentElement.scrollWidth > window.innerWidth;
  if (q('[data-act="pick-clear"]')) q('[data-act="pick-clear"]').click();
  if (q('#pick-q')) {
    q('#pick-q').value = '';
    q('#pick-q').dispatchEvent(new Event('input', {bubbles: true}));
  }
  await sleep(150);
  out.cleared = Array.from(document.querySelectorAll('[data-act="pick-one"]'))
    .filter((r) => r.checked).length;
  const settle = q('[data-act="pick-settle"]');
  if (settle) {
    settle.click();
    await sleep(300);
    out.settle = {open: !!q('#modal .modal'),
                  routes: document.querySelectorAll('#settle-route option').length,
                  note: ((q('#modal .modal') || {}).textContent || '').indexOf('只写台账') >= 0};
    if (q('#modal [data-act="close-modal"]')) q('#modal [data-act="close-modal"]').click();
    await sleep(250);
    out.settle.closed = !q('#modal .modal');
  } else {
    out.settle = null;
  }
  return out;
})()
"""

# 分诊进度条看的是任务记录，不是这次点击。点完先看它出来，刷新页面再看它回来。
TRIAGE_START = """
new Promise((resolve) => {
  const box = () => document.querySelector('#triage-progress');
  const bar = () => (box() ? box().textContent.replace(/\\s+/g, ' ').trim() : '');
  const btn = () => document.querySelector('[data-act="pdf-triage-batch"]');
  const out = {label: '', empty: true, bar: '', disabled: null, ms: 0};
  const link = document.querySelector('#nav a[href="#pdf"]');
  if (link) link.click();
  const t0 = Date.now();
  const start = () => {
    if (!btn() && Date.now() - t0 < 10000) { setTimeout(start, 100); return; }
    out.label = btn() ? btn().textContent.replace(/\\s+/g, ' ').trim() : '';
    out.empty = !btn() || btn().disabled;
    if (out.empty) { resolve(out); return; }
    btn().click();
    const t1 = Date.now();
    const tick = () => {
      if (bar() || Date.now() - t1 > 10000) {
        out.bar = bar();
        out.disabled = btn() ? !!btn().disabled : null;
        out.ms = Date.now() - t1;
        resolve(out);
      } else {
        setTimeout(tick, 100);
      }
    };
    tick();
  };
  start();
})
"""

TRIAGE_BACK = """
new Promise((resolve) => {
  const link = document.querySelector('#nav a[href="#pdf"]');
  if (link) link.click();
  const box = () => document.querySelector('#triage-progress');
  const bar = () => (box() ? box().textContent.replace(/\\s+/g, ' ').trim() : '');
  const btn = () => document.querySelector('[data-act="pdf-triage-batch"]');
  const t0 = Date.now();
  const tick = () => {
    if (bar() || Date.now() - t0 > 12000) {
      resolve({bar: bar(), disabled: btn() ? !!btn().disabled : null,
               label: btn() ? btn().textContent.replace(/\\s+/g, ' ').trim() : '',
               ms: Date.now() - t0});
    } else {
      setTimeout(tick, 100);
    }
  };
  tick();
})
"""

TRIAGE_SETTLED = """
new Promise((resolve) => {
  const box = () => document.querySelector('#triage-progress');
  const bar = () => (box() ? box().textContent.replace(/\\s+/g, ' ').trim() : '');
  const btn = () => document.querySelector('[data-act="pdf-triage-batch"]');
  const made = () => {
    const text = Array.from(document.querySelectorAll('#view p'))
      .map((el) => el.textContent).join(' ');
    const hit = text.match(/其中 (\\d+) 本已经出了建议/);
    return hit ? Number(hit[1]) : 0;
  };
  const toast = () => (document.querySelector('#toast')
    ? document.querySelector('#toast').textContent.replace(/\\s+/g, ' ').trim() : '');
  const t0 = Date.now();
  const tick = () => {
    const off = !bar() && made() > 0;
    if (off || Date.now() - t0 > 15000) {
      resolve({bar: bar(), disabled: btn() ? !!btn().disabled : null,
               made: made(), toast: toast(), ms: Date.now() - t0});
    } else {
      setTimeout(tick, 150);
    }
  };
  tick();
})
"""

MAINTAIN_CHECKS = """
(() => {
  const q = (s) => document.querySelector(s);
  const picked = () => Array.from(document.querySelectorAll('[data-step]'))
    .filter((e) => e.checked).map((e) => e.dataset.step);
  if (!q('[data-step="index"]')) return {afterClick: null, afterRender: null};
  q('[data-step="index"]').click();
  const afterClick = picked();
  render();
  return {afterClick: afterClick, afterRender: picked()};
})()
"""

MAINTAIN_LAYOUT = """
(() => {
  const q = (s) => document.querySelector(s);
  const view = q('#view');
  const text = view ? view.textContent : '';
  const adv = q('#view details.adv');
  return {button: !!q('[data-act="maintain-run-default"]'),
          open: adv ? adv.open : null,
          steps: document.querySelectorAll('[data-step]').length,
          leaks: text.indexOf('.py') >= 0 || text.indexOf('--') >= 0};
})()
"""

MAINTAIN_PRODUCT = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const q = (s) => document.querySelector(s);
  const view = q('#view');
  const text = view ? view.textContent : '';
  const adv = q('#view details.adv');
  const btn = q('[data-act="maintain-run-default"]');
  if (!btn) return {button: false};
  const out = {button: true, open: adv ? adv.open : null,
               steps: document.querySelectorAll('[data-step]').length,
               leaks: text.indexOf('.py') >= 0 || text.indexOf('--') >= 0};
  btn.click();
  for (let i = 0; i < 200; i += 1) {
    await sleep(200);
    const b = q('[data-act="maintain-run-default"]');
    if (b && !b.disabled) break;
  }
  await sleep(500);
  const line = q('#view .jobline');
  out.last = line ? line.textContent.replace(/\\s+/g, ' ').trim() : '';
  return out;
})()
"""

STATUS_ISSUES = """
(() => {
  const box = document.querySelector('#status-issues');
  if (!box) return {box: false};
  const head = (el) => {
    const h = el && el.querySelector('h2');
    return h ? h.textContent.replace(/\\s+/g, ' ').trim() : '';
  };
  return {box: true, prev: head(box.previousElementSibling),
          next: head(box.nextElementSibling),
          count: box.querySelectorAll('.issue').length,
          text: box.textContent.replace(/\\s+/g, ' ').trim()};
})()
"""

LOG_CANCEL = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rows = Array.from(document.querySelectorAll('#view tbody tr')).map((r) => {
    const b = r.querySelector('[data-act="job-open"]');
    const p = r.querySelector('.pill');
    return {id: b ? b.dataset.id : '', state: p ? p.textContent : ''};
  });
  const row = rows.find((r) => r.state === '待确认');
  if (!row) return {found: false};
  document.querySelector('[data-act="job-open"][data-id="' + row.id + '"]').click();
  for (let i = 0; i < 50 && !document.querySelector('#job-detail h2'); i += 1) await sleep(150);
  return {found: true, state: row.state,
          cancel: !!document.querySelector('#job-detail [data-act="job-cancel"]'),
          rerun: !!document.querySelector('#job-detail [data-act="job-rerun"]')};
})()
"""

# 日志页是诊断面，命令行与门槛原文留在那里；其余页不得出现实现细节。
COPY_LEAKS = """
(() => {
  const bad = ['.py', '--', 'ops.db', 'library.db', 'book.json', 'index.db', 'ingest',
               'build_library', 'gen_book_catalog', 'ops_status', 'worklist', 'triage_final',
               'review.json', 'quota', 'conflicts', '未匹配', 'sha256', 'route', 'fixture',
               'plan_hash', '快照', 'replaced', 'pending_review',
               'stub', 'S1 ', 'S2 ', 'S3 ', 'S4 ', 'S5 ', 'S6 ', 'G3 ', 'G4 ', 'G5 ',
               'G10', 'G11'];
  const text = (document.querySelector('#view') || {}).textContent || '';
  return bad.filter((w) => text.indexOf(w) >= 0);
})()
"""


def dump_png(cdp, path):
    shot = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True, "captureBeyondViewport": False})
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(shot["data"]))


def seed(port, root=""):
    """给页面喂点数据：一条扫描计划、一个拆章批次、一次分诊。都用 stub，不碰真库。"""
    batch = ""
    for path, payload in (
        ("/api/import/scan", {}),
        ("/api/split/run", {"uids": ["B0001", "B0002"], "workers": 2}),
        ("/api/pdf/triage", {"uid": "B0001"}),
    ):
        try:
            _code, body = check_ops.post(port, path, payload)
            if path == "/api/split/run" and isinstance(body, dict):
                batch = body.get("batch") or ""
        except (OSError, ValueError):
            pass
    if root and batch:
        # 批次卡片每行才有「重新检查」「查看问题」，stub 驱动不写 logs，这里补一条
        logs = os.path.join(root, "工作区", "批次", batch, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "B0001.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "uid": "B0001",
                    "title": "书 B0001",
                    "status": "staged-review",
                    "verify_rc": 0,
                    "audit_rc": 1,
                    "audit_ratio": 0.81,
                    "chapters": 12,
                    "dest": "books/B0001/md",
                    "note": "verify rc=0, audit rc=1",
                },
                fh,
                ensure_ascii=False,
            )
    time.sleep(2.0)


def run_viewport(base, width, height, out_dir, port, live=False):
    proc, profile, ws_url = launch(port)
    errors, notes, failed_tabs = [], [], []
    widest = [None]
    overflow = None
    try:
        cdp = CDP(WS(ws_url))
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600},
        )
        cdp.call("Page.navigate", {"url": base + "/#overview"})
        time.sleep(0.6)
        ready = eval_js(cdp, WAIT_READY, await_promise=True, timeout=40)
        notes.append("首屏 h1=%r stats=%s 等 %s ms" % (ready.get("h1"), ready.get("stats"), ready.get("ms")))
        for tab, title in TABS:
            info = eval_js(cdp, VISIT_TAB % (tab, tab, title, tab, title), await_promise=True, timeout=30)
            tab_overflow = eval_js(cdp, "document.documentElement.scrollWidth > window.innerWidth")
            notes.append(
                "页签 %-9s %s %r %s ms%s"
                % (
                    tab,
                    "通" if info.get("ok") else "缺",
                    info.get("title"),
                    info.get("ms"),
                    " 溢出！" if tab_overflow else "",
                )
            )
            if tab_overflow:
                widest[0] = tab
            if tab != "logs":
                leaks = eval_js(cdp, COPY_LEAKS)
                if leaks:
                    notes.append("页签 %-9s 文案残留 %s" % (tab, "、".join(leaks)))
                    failed_tabs.append(tab + "-copy")
            cdp.drain(0.4)
            if not info.get("ok"):
                failed_tabs.append(tab)
        eval_js(cdp, "document.querySelector('#nav a[href=\"#import\"]').click(); true")
        cdp.drain(0.8)
        plan = eval_js(cdp, IMPORT_PLAN)
        notes.append("导入页计划 rows=%s head=%r 按钮禁用=%s" % (plan["rows"], plan["head"], plan["disabled"]))
        eval_js(cdp, "document.querySelector('[data-act=\"import-scan\"]').click(); true")
        plan2 = eval_js(
            cdp, WAIT_PLAN_CLICK % json.dumps(plan["head"], ensure_ascii=False), await_promise=True, timeout=20
        )
        notes.append("点扫描后计划 rows=%s 换计划 %s ms 按钮禁用=%s" % (plan2["rows"], plan2["ms"], plan2["disabled"]))
        if plan2["rows"] < 1 or plan2["head"] == plan["head"]:
            failed_tabs.append("import-scan-plan")
        cdp.call("Page.navigate", {"url": base + "/?r=%d#import" % int(time.time() * 1000)})
        time.sleep(1.2)
        plan3 = eval_js(cdp, WAIT_PLAN_LOAD, await_promise=True, timeout=20)
        notes.append("刷新后计划 rows=%s head=%r %s ms" % (plan3["rows"], plan3["head"], plan3["ms"]))
        if plan3["rows"] < 1:
            failed_tabs.append("import-plan-reload")
        eval_js(
            cdp, VISIT_TAB % ("split", "split", "电子书出正文", "split", "电子书出正文"), await_promise=True, timeout=30
        )
        split = eval_js(cdp, SPLIT_CHECKS)
        notes.append(
            "电子书勾选 全关=%s 全开=%s 单行关=%s 全选后=%s"
            % (split.get("off"), split.get("on"), split.get("partial"), split.get("final"))
        )
        if split.get("empty"):
            notes.append("没有待拆电子书，跳过勾选检查")
        else:
            for name, ok in (
                ("split-row-toggle", split["on"]["first"] is True and split["partial"]["first"] is False),
                ("split-all-on", split["on"]["all"] is True and split["on"]["checked"] == split["on"]["count"]),
                ("split-all-partial", split["partial"]["all"] is False),
                ("split-replace-kept", split["final"]["rep"] is True),
                ("split-workers-kept", split["final"]["workers"] == "8"),
            ):
                if not ok:
                    failed_tabs.append(name)
        ops = eval_js(cdp, SPLIT_OPS_CHECKS)
        notes.append(
            "电子书整行动作 重拆=%s 重新检查=%s 查看问题=%s 说明=%s"
            % (ops.get("one"), ops.get("recheck"), ops.get("inspect"), ops.get("hint"))
        )
        if not ops.get("one"):
            notes.append("批次卡片没有单本记录，跳过整行动作检查")
        else:
            for name, ok in (("split-recheck-button", ops.get("recheck")), ("split-ops-hint", ops.get("hint"))):
                if not ok:
                    failed_tabs.append(name)
        eval_js(cdp, VISIT_TAB % ("import", "import", "新书导入", "import", "新书导入"), await_promise=True, timeout=30)
        edited = eval_js(cdp, IMPORT_EDIT)
        notes.append("导入页改行后 title=%r subjects=%r" % (edited["title"], edited["subjects"]))
        if edited["title"] != "改过的名" or edited["subjects"] != "文学/类型小说":
            failed_tabs.append("import-edit-kept")
        retire_uid = edited.get("uid") or split.get("start", {}).get("uid") or ""
        eval_js(cdp, VISIT_TAB % ("retire", "retire", "删书", "retire", "删书"), await_promise=True, timeout=30)
        if not retire_uid:
            notes.append("没有可预览的 uid，跳过删书弹窗检查")
            modal = {"opened": True, "afterInput": True, "afterOk": True, "checked": True, "closed": True}
        else:
            modal = eval_js(cdp, RETIRE_MODAL % json.dumps(retire_uid), await_promise=True, timeout=40)
        notes.append("删书弹窗 %s" % (modal,))
        if not (modal["opened"] and modal["afterInput"] and modal["afterOk"] and modal["checked"] and modal["closed"]):
            failed_tabs.append("retire-modal")
        eval_js(cdp, VISIT_TAB % ("pdf", "pdf", "PDF 出正文", "pdf", "PDF 出正文"), await_promise=True, timeout=30)
        pdfin = eval_js(cdp, PDF_INPUTS, await_promise=True, timeout=40)
        notes.append("PDF 刷新前后 %s → %s" % (pdfin["before"], pdfin["after"]))
        if pdfin["before"] != pdfin["after"]:
            failed_tabs.append("pdf-inputs-kept")
        before_pdf = pdfin["before"] or {}
        if not before_pdf.get("open"):
            notes.append("PDF 页没有去清单的入口")
            failed_tabs.append("pdf-pick-entry")
        if before_pdf.get("legacy"):
            notes.append("PDF 页还留着一本书的编号输入框")
            failed_tabs.append("pdf-triage-input")
        if not (before_pdf.get("triage") and before_pdf.get("archive") and before_pdf.get("quota")):
            notes.append("PDF 页缺批量分诊按钮、旧批次摘要或今天额度")
            failed_tabs.append("pdf-cards")
        pdflist = eval_js(cdp, PDF_LIST, await_promise=True, timeout=60)
        notes.append("清单页 %s" % (pdflist,))
        filters = pdflist.get("filters") or {}
        settle = pdflist.get("settle") or {}
        for name, ok in (
            ("pdf-list-jump", pdflist.get("jumped") and pdflist.get("title") == "待转 PDF 清单"),
            (
                "pdf-list-filters",
                filters.get("q") and filters.get("subject") and filters.get("sort") and filters.get("tags") == 4,
            ),
            ("pdf-list-progress", filters.get("progress") == 3 and filters.get("recheck")),
            ("pdf-list-action", (filters.get("actions") or 0) >= 1),
            (
                "pdf-list-settle",
                not settle
                or (settle.get("open") and settle.get("routes") == 4 and settle.get("note") and settle.get("closed")),
            ),
            ("pdf-list-empty", pdflist.get("empty") and pdflist.get("focus")),
            (
                "pdf-list-kept",
                pdflist.get("keptQ") == "B"
                and pdflist.get("keptPick") == pdflist.get("picked")
                and pdflist.get("picked") == 1,
            ),
            ("pdf-list-width", not pdflist.get("wide")),
            ("pdf-list-clear", pdflist.get("cleared") == 0),
        ):
            if not ok:
                failed_tabs.append(name)
        if live:
            notes.append("live 模式不起分诊，跳过分诊进度条检查")
        else:
            triage = eval_js(cdp, TRIAGE_START, await_promise=True, timeout=30)
            notes.append("批量分诊 点按钮 %s" % (triage,))
            if triage.get("empty"):
                notes.append("没有未分诊的书，跳过分诊进度条检查")
            else:
                found = re.search(r"(\d+) 本", triage.get("label") or "")
                want = int(found.group(1)) if found else 0
                cdp.call("Page.reload", {"ignoreCache": True})
                time.sleep(1.2)
                back = None
                for _ in range(3):
                    try:
                        back = eval_js(cdp, TRIAGE_BACK, await_promise=True, timeout=40)
                        break
                    except RuntimeError:
                        time.sleep(1.0)
                back = back or {"bar": "", "disabled": None}
                notes.append("刷新后进度条 %s" % (back,))
                api_port = int(base.rsplit(":", 1)[1])
                job = None
                for row in (check_ops.get(api_port, "/api/jobs?limit=20")[1] or {}).get("jobs") or []:
                    if row.get("kind") == "pdf.triage" and (row.get("title") or "").startswith("批量分诊"):
                        job = row
                        break
                settled = (
                    check_ops.wait_job(
                        api_port,
                        job["id"],
                        ("awaiting_review", "succeeded", "failed", "cancelled", "orphaned"),
                        timeout=120,
                    )
                    if job
                    else None
                )
                after = eval_js(cdp, TRIAGE_SETTLED, await_promise=True, timeout=40)
                notes.append("分诊任务 %s，收尾后页面 %s" % (((settled or {}).get("job") or {}).get("state"), after))
                for name, ok in (
                    ("pdf-triage-bar", triage["disabled"] and ("/ %d 本" % want) in triage["bar"]),
                    ("pdf-triage-reload", ("/ %d 本" % want) in (back.get("bar") or "")),
                    ("pdf-triage-settle", not after["bar"] and after["made"] == want),
                ):
                    if not ok:
                        failed_tabs.append(name)
        eval_js(cdp, VISIT_TAB % ("maintain", "maintain", "维护", "maintain", "维护"), await_promise=True, timeout=30)
        maint = eval_js(cdp, MAINTAIN_CHECKS)
        notes.append("维护勾选 render 前后 %s → %s" % (maint["afterClick"], maint["afterRender"]))
        if maint["afterClick"] != maint["afterRender"]:
            failed_tabs.append("maintain-steps-kept")
        issues = eval_js(cdp, STATUS_ISSUES)
        notes.append("维护体检提醒 %s" % (issues,))
        issues_ok = issues.get("box") and issues.get("prev") == "库的状态" and issues.get("next") == "一键收尾"
        if not live:
            issue_text = issues.get("text") or ""
            issues_ok = (
                issues_ok
                and issues.get("count") == 2
                and "缺 README" in issue_text
                and "批次在跑" in issue_text
                and "体检于" in issue_text
            )
        if not issues_ok:
            failed_tabs.append("maintain-issues")
        maint2 = eval_js(cdp, MAINTAIN_PRODUCT if not live else MAINTAIN_LAYOUT, await_promise=True, timeout=120)
        notes.append("维护一键收尾 %s" % (maint2,))
        ok = (
            maint2.get("button")
            and maint2.get("open") is False
            and maint2.get("steps") == 9
            and not maint2.get("leaks")
        )
        if not live:
            ok = ok and "完成" in (maint2.get("last") or "") and "8 步全过" in (maint2.get("last") or "")
        if not ok:
            failed_tabs.append("maintain-product")
        eval_js(cdp, VISIT_TAB % ("logs", "logs", "日志", "logs", "日志"), await_promise=True, timeout=30)
        logchk = eval_js(cdp, LOG_CANCEL, await_promise=True, timeout=30)
        notes.append("日志待确认 %s" % (logchk,))
        if not (logchk.get("found") and logchk.get("rerun") and not logchk.get("cancel")):
            failed_tabs.append("log-cancel-hidden")
        eval_js(cdp, "document.querySelector('#nav a[href=\"#overview\"]').click(); true")
        cdp.drain(0.6)
        overflow = bool(widest[0]) or eval_js(cdp, "document.documentElement.scrollWidth > window.innerWidth")
        width_doc = eval_js(cdp, "document.documentElement.scrollWidth")
        inner = eval_js(cdp, "window.innerWidth")
        out = os.path.join(out_dir, "总览-%dx%d.png" % (width, height))
        dump_png(cdp, out)
        notes.append("图 → %s" % os.path.relpath(out, os.path.dirname(out_dir)))
        errors = cdp.errors()
        for note in notes:
            print("  " + note)
        print("%dx%d 图 %s" % (width, height, os.path.basename(out)))
        print("%dx%d CONSOLE ERROR %d" % (width, height, len(errors)))
        for err in errors:
            print("  ! " + err)
        print(
            "%dx%d overflow=%s (scrollWidth %s vs %s%s)"
            % (
                width,
                height,
                "true" if overflow else "false",
                width_doc,
                inner,
                "；溢出的页签 " + widest[0] if widest[0] else "",
            )
        )
        if failed_tabs:
            print("%dx%d 页签或页面检查没过：%s" % (width, height, "、".join(failed_tabs)))
        return len(errors) + len(failed_tabs), bool(overflow)
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        import shutil

        shutil.rmtree(profile, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="用已在跑的服务，不再自己起")
    ap.add_argument("--port", type=int, default=9411, help="Chrome 调试口")
    ap.add_argument("--root", default="", help="临时根，默认自己造")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    out_dir = HERE
    owns_root = not args.url
    root = args.root
    server = None
    base = args.url.rstrip("/")
    fixture = None
    if owns_root:
        if args.root:
            fixture = os.path.join(root, "status-fixture.json")
            if not os.path.exists(fixture):
                with open(fixture, "w", encoding="utf-8") as fh:
                    json.dump(check_ops.FIXTURE_STATUS, fh, ensure_ascii=False)
        else:
            root, fixture = check_ops.build_root()
        os.environ["STUB_TRIAGE_SLEEP"] = "4"
        server, port = check_ops.start(root, fixture)
        base = "http://127.0.0.1:%d" % port
        print("临时根：%s  服务：%s" % (root, base))
        seed(port, root)

    fails = 0
    try:
        for width, height in VIEWPORTS:
            print("[%dx%d]" % (width, height))
            errors, overflow = run_viewport(base, width, height, out_dir, args.port, live=bool(args.url))
            if errors or overflow:
                fails += 1
    finally:
        if server:
            check_ops.stop(server)
        if owns_root and root and not args.keep:
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        elif owns_root and args.keep:
            print("保留临时根：%s" % root)

    print("CONSOLE ERROR 0" if not fails else "CONSOLE ERROR 有")
    print("overflow=false" if not fails else "overflow 有")
    if fails:
        print("SHOT FAIL")
        return 1
    print("SHOT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
