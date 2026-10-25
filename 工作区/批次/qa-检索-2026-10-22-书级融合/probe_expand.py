#!/usr/bin/env python3
"""B4 性能探针：首轮、默认、深挖、最大档、缓存命中。

  probe_expand.py            跑一遍并写 perf-expand.tsv
  probe_expand.py --summary  只按已有 TSV 打汇总

口径：每行一次（题，模式）的端到端时间与调用数。首轮 = mode=off（纯检索 +
README 选章，零模型调用）。缓存命中 = 同题同模式再跑一次。
"""
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import book_expand as be

GOLD = os.path.join(HERE, "book_gold.tsv")
OUT = os.path.join(HERE, "perf-expand.tsv")
COLS = ["qid", "mode", "phase", "ms", "calls", "rounds", "cached", "partial",
        "tokens_in", "tokens_out", "input_chars", "max_window_chars", "cache_hit_ms"]

CASES = [("off", 10), ("default", 10), ("deep", 3), ("max", 1)]


def question(row):
    return " ".join(t.strip() for t in row["terms"].split(",") if t.strip())


def cache_hit_ms(n=20):
    """缓存命中延迟：直接读缓存文件的时间。"""
    if not os.path.isdir(be.CACHE):
        return 0.0
    files = sorted(os.listdir(be.CACHE))[:n]
    if not files:
        return 0.0
    t0 = time.perf_counter()
    for f in files:
        with open(os.path.join(be.CACHE, f), encoding="utf-8") as fh:
            json.load(fh)
    return round((time.perf_counter() - t0) * 1000 / len(files), 3)


def pct(xs, p):
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))] if s else 0.0


def flush(out):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, OUT)


def run():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    out = []
    for mode, n in CASES:
        for row in rows[:n]:
            res = be.run(question(row), mode)
            out.append({"qid": row["qid"], "mode": mode,
                        "phase": "cold", "cache_hit_ms": 0.0,
                        "ms": res["ms"], "calls": res["calls"], "rounds": res["rounds"],
                        "cached": res["cached"], "partial": res["partial"],
                        "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                        "input_chars": res["input_chars"],
                        "max_window_chars": max([c["window_max"] for c in res["chapters"]]
                                                or [0])})
            flush(out)
    # 默认档重复跑同样的 3 题，看缓存命中
    for row in rows[:3]:
        res = be.run(question(row), "default")
        out.append({"qid": row["qid"], "mode": "default", "phase": "hot",
                    "ms": res["ms"], "calls": res["calls"], "rounds": res["rounds"],
                    "cached": res["cached"], "partial": res["partial"],
                    "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                    "input_chars": res["input_chars"],
                    "max_window_chars": max([c["window_max"] for c in res["chapters"]]
                                            or [0]),
                    "cache_hit_ms": cache_hit_ms()})
        flush(out)
    flush(out)
    return summarize(out)


def summarize(rows):
    groups = {}
    for r in rows:
        groups.setdefault((r["mode"], r["phase"]), []).append(r)
    print("%-18s %4s %6s %6s %6s %6s %6s %6s" % (
        "mode/phase", "n", "p50", "p95", "calls", "rounds", "cached", "partial"))
    for (mode, phase), rs in sorted(groups.items()):
        ms = [float(r["ms"]) for r in rs]
        print("%-18s %4d %6.1f %6.1f %6.1f %6.1f %6d %6d" % (
            mode + "/" + phase, len(rs), pct(ms, 50), pct(ms, 95),
            sum(int(r["calls"]) for r in rs) / len(rs),
            sum(int(r["rounds"]) for r in rs) / len(rs),
            sum(int(r["cached"]) for r in rs), sum(int(r["partial"]) for r in rs)))
    base = [float(r["ms"]) for r in rows if r["mode"] == "off"]
    print("首轮 p95 %.1f ms (零模型调用)" % pct(base, 95))
    for mode, cap_ms, cap_calls, cap_chars in (
            ("default", 60000, 3, 2000), ("deep", 80000, 6, 6000),
            ("max", 120000, 24, 6000)):
        rs = [r for r in rows if r["mode"] == mode and r["phase"] == "cold"]
        if not rs:
            continue
        p95 = pct([float(r["ms"]) for r in rs], 95)
        calls = max(int(r["calls"]) for r in rs)
        chars = max(int(r["max_window_chars"]) for r in rs)
        ok = p95 <= cap_ms and calls <= cap_calls and chars <= cap_chars
        print("%-8s p95 %7.1f ms (<=%d)  调用最多 %d (<=%d)  单章最多 %d 字 (<=%d)  %s"
              % (mode, p95, cap_ms, calls, cap_calls, chars, cap_chars,
                 "PASS" if ok else "FAIL"))
    hit_ms = [float(r["cache_hit_ms"]) for r in rows if float(r.get("cache_hit_ms") or 0) > 0]
    if hit_ms:
        print("缓存命中（读缓存文件）p95 %.3f ms (<=100)" % pct(hit_ms, 95))
    return 0


def main():
    if "--summary" in sys.argv:
        rows = list(csv.DictReader(open(OUT, encoding="utf-8"), delimiter="\t"))
        return summarize(rows)
    return run()


if __name__ == "__main__":
    sys.exit(main())
