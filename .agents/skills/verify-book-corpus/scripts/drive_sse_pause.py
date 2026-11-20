#!/usr/bin/env python3
"""Drive one demo answer over SSE, pause after the first delta, resume, report.

  python3 .agents/skills/verify-book-corpus/scripts/drive_sse_pause.py \
    --base http://127.0.0.1:18765 --out "$EV/sse-pause-resume.json"

Needs a model-backed demo instance. Exit 0 only when a paused checkpoint was
observed and the resumed stream ended with done.ok=true.
"""
import argparse
import http.client
import json
import sys
import time
import urllib.parse


def post(base, path, payload, timeout=330):
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    body = json.dumps(payload, ensure_ascii=False).encode()
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    return conn, conn.getresponse()


def read_sse(resp, on_event):
    event = None
    while True:
        raw = resp.readline()
        if not raw:
            return
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            on_event(event, json.loads(line[6:]))
        elif line == "":
            event = None


def payload_data(event):
    data = event.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else event


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18765")
    ap.add_argument("--question", default="阿Q为什么挨打")
    ap.add_argument("--book", default="B0001")
    ap.add_argument("--path", default="books/B0001/md/06_第六章从中兴到末路.md")
    ap.add_argument("--line", type=int, default=75)
    ap.add_argument("--terms", default="阿Q,精神胜利法,未庄,赵太爷,闲人")
    ap.add_argument("--thinking", default="low")
    ap.add_argument("--stream-id", default="verify-sse-pause")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    base = {
        "question": args.question,
        "book": args.book,
        "path": args.path,
        "line": args.line,
        "terms": [t.strip() for t in args.terms.split(",") if t.strip()],
        "groups": [],
        "thinking": args.thinking,
        "stream": True,
        "fresh": True,
        "stream_id": args.stream_id,
    }

    events = []
    started = time.time()
    paused_reply = None

    def first(etype, data):
        nonlocal paused_reply
        events.append({"t": round(time.time() - started, 2), "event": etype, "data": data})
        if paused_reply is None and etype == "answer_delta":
            pc, pr = post(args.base, "/api/answer/pause", {"stream_id": args.stream_id}, timeout=30)
            paused_reply = json.loads(pr.read().decode())
            pc.close()

    conn, resp = post(args.base, "/api/answer", base)
    headers = {k.lower(): v for k, v in resp.getheaders()}
    try:
        read_sse(resp, first)
    finally:
        conn.close()

    paused_ev = next((e for e in events if e["event"] == "paused"), None)
    if not paused_ev:
        print("PAUSED none events=%d elapsed_s=%.1f" % (len(events), time.time() - started))
        if args.out:
            with open(args.out, "w") as fh:
                json.dump({"events": events, "pause_reply": paused_reply}, fh, ensure_ascii=False, indent=2)
        return 2
    partial = payload_data(paused_ev).get("partial_text") or ""

    time.sleep(1.0)
    resumed = []
    rstarted = time.time()
    rconn, rresp = post(args.base, "/api/answer", dict(base, resume=True))
    try:
        read_sse(rresp, lambda etype, data: resumed.append({"t": round(time.time() - rstarted, 2), "event": etype, "data": data}))
    finally:
        rconn.close()

    done_ev = next((e for e in resumed if e["event"] == "done"), None)
    done = payload_data(done_ev) if done_ev else {}
    ok = bool(done.get("ok"))
    print("STREAM content_type=%s cache_control=%s" % (headers.get("content-type"), headers.get("cache-control")))
    print("PAUSED partial_chars=%d pause_ok=%s" % (len(partial), bool((paused_reply or {}).get("paused"))))
    print(
        "RESUMED status=%s events=%d done_ok=%s final_chars=%d elapsed_s=%.1f"
        % (rresp.status, len(resumed), ok, len(done.get("answer") or ""), time.time() - rstarted)
    )
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "headers": headers,
                    "first_events": events,
                    "pause_reply": paused_reply,
                    "partial_text": partial,
                    "resumed_events": resumed,
                    "done": done,
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
