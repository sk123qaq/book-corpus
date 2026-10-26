#!/usr/bin/env python3
"""复现：批量分诊的进度条到点消失，任务还在跑。

  python3 工作区/运维/验收/repro-triage-progress.py
  python3 工作区/运维/验收/repro-triage-progress.py --books 7 --step 30

临时根 + stub 分诊脚本 + headless Chrome，不碰真库也不调模型。每本书的 stub
故意慢，让批次跑过前后端两处等待上限，看页面的进度条还在不在。

末行 REPRO PASS（进度条先走、任务还在跑，复现到了）或 REPRO CLEAN（进度条
一直留到任务收尾）。
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_ops  # noqa: E402
import shot_ops  # noqa: E402

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def slow_stub(step):
    return check_ops.STUB_TRIAGE.replace(
        'uid, out = opt("--uid"), opt("--out")',
        'uid, out = opt("--uid"), opt("--out")\n'
        'import time\ntime.sleep(%r)' % float(step))


def add_pdf_books(root, count):
    conn = sqlite3.connect(os.path.join(root, "目录", "library.db"))
    for i in range(count):
        uid = "B01%02d" % i
        conn.execute("INSERT OR REPLACE INTO book VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (uid, "书 " + uid, "某人", "文学", "小说",
                      "文学/小说/书 " + uid, "", "", "keep", "active"))
        conn.execute("INSERT INTO source VALUES (?,?,?,?,?,?)",
                     (uid, "库/文学/小说/书 %s/source/a.pdf" % uid, "pdf", 100,
                      uid.lower(), 1))
    conn.commit()
    conn.close()
    return ["B01%02d" % i for i in range(count)]


PAGE_STATE = """
(() => {
  const box = document.querySelector('#triage-progress');
  const btn = document.querySelector('[data-act="pdf-triage-batch"]');
  const toast = document.querySelector('#toast');
  return {
    bar: box ? box.textContent.replace(/\\s+/g, ' ').trim() : null,
    button: btn ? btn.textContent.replace(/\\s+/g, ' ').trim() : null,
    disabled: btn ? !!btn.disabled : null,
    toast: toast && toast.className.indexOf('on') >= 0
      ? toast.textContent.replace(/\\s+/g, ' ').trim() : '',
  };
})()
"""

WAIT_PDF = """
new Promise((resolve) => {
  const t0 = Date.now();
  const tick = () => {
    const h1 = document.querySelector('#view h1');
    const btn = document.querySelector('[data-act="pdf-triage-batch"]');
    if ((h1 && h1.textContent.trim() === 'PDF 出正文' && btn) || Date.now() - t0 > 15000) {
      resolve({title: h1 ? h1.textContent.trim() : '', button: btn ? btn.textContent : ''});
    } else {
      setTimeout(tick, 120);
    }
  };
  tick();
})
"""


def triage_job(port):
    code, body = check_ops.get(port, "/api/jobs?limit=20")
    if code != 200:
        return None
    for job in body.get("jobs") or []:
        if job.get("kind") == "pdf.triage" and (job.get("title") or "").startswith("批量分诊"):
            return job
    return None


def done_steps(job):
    steps = job.get("steps") or []
    done = [s for s in steps if s.get("state") in ("ok", "fail")]
    return len(done), len(steps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", type=int, default=7, help="临时根里加几本未分诊 PDF")
    ap.add_argument("--step", type=float, default=30.0, help="stub 每本睡几秒")
    ap.add_argument("--limit", type=float, default=300.0, help="最多看几秒")
    ap.add_argument("--after", type=float, default=8.0, help="任务收尾后再看几秒")
    ap.add_argument("--every", type=float, default=15.0, help="几秒打一行时间线")
    args = ap.parse_args()

    root, fixture = check_ops.build_root()
    with open(os.path.join(root, "工作区", "脚本", "ops_pdf_triage.py"), "w",
              encoding="utf-8") as fh:
        fh.write(slow_stub(args.step))
    uids = add_pdf_books(root, args.books)
    print("临时根 %s，新增未分诊 %d 本，stub 每本 %.0f 秒" % (root, len(uids), args.step))

    server, port = check_ops.start(root, fixture)
    code, books = check_ops.get(port, "/api/pdf/books")
    untriaged = [r["uid"] for r in books.get("books") or []
                 if r.get("disposition") == "untriaged"]
    print("服务 127.0.0.1:%d，未分诊 %d 本：%s" % (port, len(untriaged), ",".join(untriaged)))

    chrome, profile, ws_url = shot_ops.launch(free_port())
    cdp = shot_ops.CDP(shot_ops.WS(ws_url))
    try:
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:%d/#pdf" % port})
        page = shot_ops.eval_js(cdp, WAIT_PDF, await_promise=True, timeout=30)
        print("页面 %r 按钮 %r" % (page["title"], page["button"]))
        before = shot_ops.eval_js(cdp, PAGE_STATE)
        print("点之前 %s" % json.dumps(before, ensure_ascii=False))
        shot_ops.eval_js(cdp, "document.querySelector('[data-act=\"pdf-triage-batch\"]')"
                              ".click(); true")
        seen, t0, last_line, last_bar = None, time.time(), -1e9, False
        prints = []
        lost_running = False
        settled_at, last_toast = None, ""
        while time.time() - t0 < args.limit:
            job = triage_job(port)
            state = shot_ops.eval_js(cdp, PAGE_STATE)
            now = time.time() - t0
            if job:
                done, total = done_steps(job)
                if seen is None:
                    seen = job["id"]
                    prints.append("t=%.0fs 任务 %s 起跑，步数 %d" % (now, job["id"], total))
                settled = job["state"] not in ("queued", "running")
            else:
                done, total, settled = 0, 0, False
            bar_on = state["bar"] not in (None, "", "0%")
            if bar_on != last_bar:
                prints.append("t=%.0fs 进度条%s%s，任务 %s，步 %d/%d"
                              % (now, "出现" if bar_on else "消失",
                                 "（" + state["bar"] + "）" if bar_on else "",
                                 (job or {}).get("state"), done, total))
                if not bar_on and (job or {}).get("state") in ("queued", "running"):
                    lost_running = True
                last_bar = bar_on
            elif now - last_line >= args.every:
                prints.append("t=%.0fs 进度条%s，任务 %s，步 %d/%d，按钮禁用=%s"
                              % (now, "在" if bar_on else "没了",
                                 (job or {}).get("state"), done, total,
                                 state["disabled"]))
            if now - last_line >= args.every:
                last_line = now
            if state["toast"] and state["toast"] != last_toast:
                prints.append("t=%.0fs 提示 %r" % (now, state["toast"]))
                last_toast = state["toast"]
            if settled and settled_at is None:
                settled_at = now
            if settled_at is not None and now - settled_at >= args.after:
                break
            time.sleep(2.0)
        job = triage_job(port)
        done, total = done_steps(job or {})
        final = shot_ops.eval_js(cdp, PAGE_STATE)
        prints.append("收尾 t=%.0fs 任务 %s，步 %d/%d，进度条 %r，提示 %r"
                      % (time.time() - t0, (job or {}).get("state"), done, total,
                         final["bar"], final["toast"]))
        for line in prints:
            print(line)
        job = triage_job(port)
        running = job and job["state"] in ("queued", "running")
        if lost_running and running:
            print("REPRO PASS：进度条先消失，任务 %s 还在跑，步 %d/%d"
                  % (job["state"], done, total))
            return 0
        if lost_running:
            print("REPRO MIXED：进度条消失时任务已经收尾")
            return 0
        print("REPRO CLEAN：进度条一直留到任务收尾")
        return 0
    finally:
        try:
            cdp.ws.close()
        except Exception:  # noqa: BLE001
            pass
        chrome.kill()
        check_ops.stop(server)
        if os.environ.get("KEEP_ROOT"):
            print("留着临时根：%s" % root)
        else:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
