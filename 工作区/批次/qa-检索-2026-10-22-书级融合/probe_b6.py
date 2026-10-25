#!/usr/bin/env python3
"""B6 实跑：默认查询、旧 mix、文档命令、60 题性能。

  python3 probe_b6.py --lanes
  python3 probe_b6.py --gate
  python3 probe_b6.py --perf

写 live/B6/。只读 index.db 与 library.db；不调模型。
"""
import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_cards as BC  # noqa: E402
import book_query as BQ  # noqa: E402
import book_score  # noqa: E402
import lib_fts_cjk as cjk  # noqa: E402
import qsearch  # noqa: E402

OUT = os.path.join(HERE, "live", "B6")
PY = sys.executable
Q = ["礼治", "秩序", "法律"]
QUESTION = "礼治社会靠什么维持秩序，为什么不需要法律？"


def cli(args, timeout=300):
    r = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def lane1():
    os.makedirs(OUT, exist_ok=True)
    rc1, out1, err1 = cli(["工作区/脚本/book_query.py"] + Q + ["--question", QUESTION,
                                                               "--json"])
    rc2, out2, err2 = cli(["工作区/脚本/book_query.py"] + Q + ["--question", QUESTION,
                                                               "--no-cards", "--json"])
    rc3, out3, err3 = cli(["工作区/脚本/book_score.py", " ".join(Q), "--json"])
    d1, d2, d3 = json.loads(out1), json.loads(out2), json.loads(out3)
    top1 = d1["chapters"][0]
    top2 = d2["chapters"][0]
    top3 = d3[0]
    best1 = top1["selected"][0]["chapter_path"] if top1["selected"] else ""
    best2 = top2["selected"][0]["chapter_path"] if top2["selected"] else ""
    match = {"top_book": top1["book"] == top2["book"] == top3["book"],
             "best_chapter": best1 == best2,
             "cards": len(d1.get("cards", {}).get("cards", [])),
             "trigger": d1.get("cards", {}).get("trigger")}
    def slim(b):
        return {"book": b["book"], "title": b["title"], "selected": b["selected"]}

    payload = {"query": Q, "question": QUESTION,
               "default": {"top": slim(top1), "cards": d1.get("cards"),
                           "stop": d1.get("stop"), "rounds": len(d1.get("rounds", [])),
                           "ms": d1.get("ms")},
               "no_cards": {"top": slim(top2)},
               "book_score": [{"book": x["book"], "title": x["title"],
                               "score": x["score"]} for x in d3[:3]],
               "match": match, "rc": [rc1, rc2, rc3],
               "stderr": [err1, err2, err3]}
    with open(os.path.join(OUT, "lane-1.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    lines = ["B6 lane-1 默认查询（book_query，卡片默认开）", "",
             "top1（默认）: %s" % top1["book"],
             "top1（--no-cards）: %s" % top2["book"],
             "top1（book_score best_support）: %s" % top3["book"],
             "最佳章（默认）: %s" % best1,
             "最佳章（--no-cards）: %s" % best2,
             "卡片: %d 张，触发 %s" % (match["cards"], match["trigger"]),
             "对齐: top_book=%s best_chapter=%s" % (match["top_book"],
                                                     match["best_chapter"]),
             "rc: %s" % [rc1, rc2, rc3]]
    open(os.path.join(OUT, "lane-1.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return match["top_book"] and match["best_chapter"]


def lane2():
    args = ["工作区/脚本/qsearch.py", " ".join(Q), "--book", "--book-score", "mix",
            "-n", "5"]
    rc1, out1, err1 = cli(args)
    rc2, out2, err2 = cli(args)
    same = out1 == out2 and rc1 == rc2 == 0
    lines = ["B6 lane-2 旧 mix 口径（qsearch --book）", "",
             "rc: %s / %s" % (rc1, rc2),
             "两遍逐字节相同: %s" % same, "", out1]
    open(os.path.join(OUT, "lane-2.txt"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[:5]))
    return same


def lane3():
    cmds = [
        ["工作区/脚本/book_query.py"] + Q + ["--question", QUESTION, "-n", "3"],
        ["工作区/脚本/book_query.py"] + Q + ["--question", QUESTION, "--no-cards", "-n", "3"],
        ["工作区/脚本/book_query.py"] + Q + ["--book-score", "mix", "--no-cards", "-n", "3"],
        ["工作区/脚本/qsearch.py", " ".join(Q), "--book", "--book-score", "mix",
         "-n", "3"],
        ["工作区/脚本/book_cards.py"] + Q + ["--question", QUESTION, "--json",
                                             "--feedback", ""],
        ["工作区/脚本/book_query.py", "--help"],
    ]
    lines = ["B6 lane-3 文档命令（README 里的命令逐条跑）", ""]
    ok = True
    for cmd in cmds:
        rc, out, err = cli(cmd)
        ok = ok and rc == 0
        first = (out or err).strip().splitlines()[0] if (out or err).strip() else ""
        lines.append("rc=%d  %s" % (rc, " ".join(cmd)))
        lines.append("   " + first[:160])
    lines.append("")
    lines.append("全部 rc=0: %s" % ok)
    open(os.path.join(OUT, "lane-3.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return ok


def gate():
    """60 题等宽 gate：未触发走循环，卡片用默认前三本，主判据 K=3。"""
    res = BC.gate(out=os.path.join(OUT, "gate.tsv"))
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    return len(res["rows"]) == 60


def perf(passes=2):
    con = BC.sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    meta = book_score.load_meta()
    expand = book_score.load_expand(con)
    rows = BC.gold_rows()
    out = []

    def run_qsearch(row):
        t0 = time.time()
        expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in row["terms"]
                           if cjk.query_expr(t))
        hits, _ = qsearch.search(con, expr, row["terms"], [], 2000, 40, False, 2000)
        qsearch.rollup_by_book(hits, con, 5, "mix")
        return (time.time() - t0) * 1000

    def run_mix(row):
        t0 = time.time()
        p = BQ.Planner(con, meta, expand, scheme="mix", limit=5)
        res = p.plan(row["terms"], chapters=True)
        cardset, hits, nouns, ranking = BC.cards_for(
            p, row["terms"], row["question"], 6)
        if cardset.trigger:
            keep = {c.book for c in cardset.cards}
            res.chapters = [b for b in res.chapters if b["book"] in keep]
        return (time.time() - t0) * 1000

    def run_default(row):
        t0 = time.time()
        p = BQ.Planner(con, meta, expand, scheme="best_support", limit=5)
        res = p.plan(row["terms"], chapters=True)
        cardset, hits, nouns, ranking = BC.cards_for(
            p, row["terms"], row["question"], 6)
        if cardset.trigger:
            keep = {c.book for c in cardset.cards}
            res.chapters = [b for b in res.chapters if b["book"] in keep]
            have = {b["book"] for b in res.chapters}
            missing = [c for c in cardset.cards if c.book in keep and c.book not in have]
            if missing:
                res.chapters += BC.next_books(missing, hits, row["terms"], 3)
        return (time.time() - t0) * 1000

    for pnum in range(1, passes + 1):
        for i, row in enumerate(rows):
            modes = [("qsearch_mix", run_qsearch, row),
                     ("book_query_mix", run_mix, row),
                     ("default", run_default, row)]
            shift = (pnum + i) % 3
            modes = modes[shift:] + modes[:shift]
            vals = {}
            for name, fn, arg in modes:
                vals[name] = fn(arg)
            out.append({"pass": pnum, "qid": row["qid"],
                        **{k: round(vals[k], 1) for k in
                           ("qsearch_mix", "book_query_mix", "default")}})
    con.close()
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "perf.tsv")
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]), delimiter="\t",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    os.replace(path + ".tmp", path)
    lines = ["B6 性能：60 题，qsearch 旧路径 / book_query mix / 默认，三路交错", ""]
    p95s = {}
    for p in range(1, passes + 1):
        for mode in ("qsearch_mix", "book_query_mix", "default"):
            xs = sorted(r[mode] for r in out if r["pass"] == p)
            p95 = xs[max(0, min(len(xs) - 1, int(round(0.95 * (len(xs) - 1)))))]
            p95s[(p, mode)] = p95
            lines.append("pass=%d %-14s n=%d p50 %.0f ms p95 %.0f ms max %.0f ms"
                         % (p, mode, len(xs), statistics.median(xs), p95, xs[-1]))
        d = p95s[(p, "default")]
        q = p95s[(p, "qsearch_mix")]
        m = p95s[(p, "book_query_mix")]
        lines.append("  default/qsearch p95 %.2f ｜ default/book_query_mix p95 %.2f"
                     % (d / max(1.0, q), d / max(1.0, m)))
    worst = max(p95s[(p, "default")] for p in range(1, passes + 1))
    ratio = max(p95s[(p, "default")] / max(1.0, p95s[(p, "qsearch_mix")])
                for p in range(1, passes + 1))
    lines += ["", "默认 p95 最大值 %.0f ms（线 2000）: %s" % (
        worst, "过" if worst <= 2000 else "不过"),
        "默认/旧 qsearch p95 比值最大 %.2f（线 2.0）: %s" % (
            ratio, "过" if ratio <= 2.0 else "不过"),
        "注意：旧路径是 qsearch --book --book-score mix；book_query mix 只作同管线诊断。"]
    open(os.path.join(OUT, "perf.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return worst <= 2000 and ratio <= 2.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lanes", action="store_true")
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--passes", type=int, default=2)
    a = ap.parse_args(argv)
    if not (a.lanes or a.perf or a.gate):
        ap.error("要 --lanes / --perf / --gate")
    os.makedirs(OUT, exist_ok=True)
    ok = True
    if a.lanes:
        ok = lane1() and ok
        ok = lane2() and ok
        ok = lane3() and ok
    if a.gate:
        ok = gate() and ok
    if a.perf:
        ok = perf(a.passes) and ok
    print("ALL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
