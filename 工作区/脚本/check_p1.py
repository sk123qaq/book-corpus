#!/usr/bin/env python3
"""P1 验收：2 字词可查且有序、译名墙案例、原词类不低于裸 grep。

只读。任一不过就退出码 1。
"""
import csv, json, os, subprocess, sys, time

ROOT = "/home/shike/文档/books"
QSEARCH = os.path.join(ROOT, "工作区", "脚本", "qsearch.py")
RG = "/home/shike/.pi/agent/bin/rg"
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19")
fails = []


def run(args):
    out = subprocess.run([sys.executable, QSEARCH] + args + ["--json"], cwd=ROOT,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def corpus_files(term):
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(RG) + ":" + env.get("PATH", "")
    r = subprocess.run([RG, "-l", "-F", "-g", "*.md", "-e", term, "库"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    return [l for l in r.stdout.splitlines() if l]


def check(ok, name, detail):
    print(("PASS " if ok else "FAIL ") + name + "  " + detail)
    if not ok:
        fails.append(name)


def two_char_words():
    for q in ("自由", "内卷", "权力"):
        t0 = time.time()
        res = run([q, "-n", "20"])
        ms = res["ms"]
        hits = res["hits"]
        ok = bool(hits) and len(hits) >= 10 and q in hits[0]["text"] \
            and ms < 2000 and (time.time() - t0) < 5
        first = ("%s:%d" % (hits[0]["path"], hits[0]["line"])) if hits else "无命中"
        check(ok, "2字词 " + q, "%d 条 %.0f ms 首条 %s" % (len(hits), ms, first))


def wall(word, want_book, want_any=True):
    res = run([word, "--auto-concept", "-n", "20"])
    got = [h["path"] for h in res["hits"]]
    hit = [p for p in got if want_book in p]
    check(bool(hit) == want_any, "译名墙 " + word,
          "语料命中 %d 文件；%s 在 top20: %s" % (len(corpus_files(word)), want_book, bool(hit)))


def main():
    two_char_words()
    wall("过密化", "八次危机")
    wall("群己权界论", "金克木集")
    wall("消极自由", "刘擎西方现代思想讲义")
    for word in ("达克效应", "灰犀牛"):
        n = len(corpus_files(word))
        print("注  %s 语料命中 %d 个文件，不足以做译名墙验收（只记数，不计入通过/失败）" % (word, n))
    idx_db = os.path.join(ROOT, "目录", "检索", "index.db")
    for fn in ("eval-baseline.tsv", "eval-index.tsv"):
        p = os.path.join(BATCH, fn)
        if not os.path.isfile(p):
            check(False, "评测读数 " + fn, "缺文件 " + p)
            return 1
        if os.path.getmtime(p) < os.path.getmtime(idx_db):
            print("警告：%s 比 index.db 旧，原词类判据读的是旧索引的读数" % fn)
    base = {r["qid"] + r["system"]: r for r in csv.DictReader(
        open(os.path.join(BATCH, "eval-baseline.tsv"), encoding="utf-8"), delimiter="\t")}
    idx = {r["qid"] + r["system"]: r for r in csv.DictReader(
        open(os.path.join(BATCH, "eval-index.tsv"), encoding="utf-8"), delimiter="\t")}
    gold = {r["qid"]: r for r in csv.DictReader(
        open(os.path.join(BATCH, "gold.tsv"), encoding="utf-8"), delimiter="\t")}
    qids = [q for q, r in gold.items() if r["category"] == "原词"]
    for label, table, sysname in (("A 裸 grep", base, "A"), ("E FTS", idx, "E")):
        ranks = [int(table[q + sysname]["rank"]) for q in qids]
        print("     原词 %s recall@10 %.2f" % (label, sum(1 for x in ranks if x <= 10) / len(ranks)))
    a = [int(base[q + "A"]["rank"]) for q in qids]
    e = [int(idx[q + "E"]["rank"]) for q in qids]
    check(sum(1 for x in e if x <= 10) >= sum(1 for x in a if x <= 10),
          "原词类不低于裸 grep", "E %d/20 vs A %d/20 进前 10" % (
              sum(1 for x in e if x <= 10), sum(1 for x in a if x <= 10)))
    print("失败 %d 项" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
