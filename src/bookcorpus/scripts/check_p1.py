#!/usr/bin/env python3
"""P1 验收：2 字词可查且有序、译名墙案例、原词类不低于裸 grep、查询边界回归。

边界回归段（edge_cases）覆盖：不可索引字符不崩、全角数字归一可查、
全角逗号当分隔符、ASCII 大小写覆盖口径、--pool all 不被吞。
只读。任一不过就退出码 1。
"""
import csv, json, os, sqlite3, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
ROOT = L.ROOT
QSEARCH = os.path.join(HERE, "qsearch.py")
RG = L.RG
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19")
fails = []


def run(args):
    out = subprocess.run([sys.executable, QSEARCH] + args + ["--json"], cwd=ROOT,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def corpus_files(term):
    env = dict(os.environ)
    rg_dir = os.path.dirname(RG)
    if rg_dir:
        env["PATH"] = rg_dir + ":" + env.get("PATH", "")
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


def safe_run(args):
    """边界用例专用：坏输入不应让验收本身崩掉，崩了记 FAIL 并带回错误。"""
    r = subprocess.run([sys.executable, QSEARCH] + args + ["--json"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stderr or "").strip().splitlines()[-1:]
    try:
        return json.loads(r.stdout), None
    except ValueError:
        return None, ["stdout 不是 JSON"]


def edge_cases():
    # 不可索引字符（标点/假名单独成查询）不该崩，返回空结果即可
    res, err = safe_run(["！！！", "-n", "3"])
    check(res is not None and res["hits"] == [], "纯标点查询不崩",
          "崩：%s" % err[0] if err else "返回 %d 条" % len(res["hits"]))
    # 全角数字折半角后应能查到（语料有 1984）
    res, err = safe_run(["１９８４", "-n", "5"])
    check(bool(res and res["hits"]), "全角数字归一可查",
          "崩：%s" % err[0] if err else "返回 %d 条" % len(res["hits"]))
    # 全角逗号当分隔符：与空格分隔同口径
    comma, err1 = safe_run(["自由，平等", "-n", "5"])
    space, err2 = safe_run(["自由 平等", "-n", "5"])
    same = (comma and space and comma["hits"] and space["hits"]
            and [h["path"] for h in comma["hits"]] == [h["path"] for h in space["hits"]])
    check(bool(same), "全角逗号当分隔符",
          "逗号版 %s / 空格版 %s" % (
              "崩" if err1 else "%d 条" % len(comma["hits"]),
              "崩" if err2 else "%d 条" % len(space["hits"])))
    # ASCII 大小写：FTS 折叠了大小写，覆盖判定也得跟上
    res, err = safe_run(["PANOPTICON", "-n", "5"])
    cov = max((h.get("coverage", 0) for h in res["hits"]), default=0) if res else 0
    check(bool(res and res["hits"]) and cov > 0, "大写词覆盖判定",
          "崩：%s" % err[0] if err else "%d 条，最大 coverage %d" % (len(res["hits"]), cov))
    # --pool all（0）是不设上限，不能被 or 吞成默认值
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import qsearch
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(
            ROOT, "目录", "检索", "index.db"), uri=True)
        expr = '"自由"'
        # limit 放大过池深，返回条数才反映 pool 的取数深度
        all_hits, _ = qsearch.search(con, expr, ["自由"], [], 10 ** 9, 40, False, 0)
        low_hits, _ = qsearch.search(con, expr, ["自由"], [], 10 ** 9, 40, False, 100)
        con.close()
        check(len(all_hits) > len(low_hits), "--pool all 不被吞",
              "all=%d 段 vs 100=%d 段" % (len(all_hits), len(low_hits)))
    except Exception as exc:  # noqa: BLE001
        check(False, "--pool all 不被吞", "异常 %s: %s" % (type(exc).__name__, exc))


def fixtures_available():
    gold = os.path.join(BATCH, "gold.tsv")
    if not os.path.isfile(gold):
        return False
    with open(gold, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    paths = [r.get("path") for r in rows if r.get("path")][:10]
    return bool(paths) and all(os.path.exists(os.path.join(ROOT, p)) for p in paths)


def main():
    if not fixtures_available():
        print("SKIP 当前库不含 P1 评测语料（gold.tsv 的书），跳过。")
        return 0
    two_char_words()
    edge_cases()
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
