#!/usr/bin/env python3
"""P1 验收：2 字词可查且有序、原词类不低于裸 grep、查询边界回归。

边界回归段（edge_cases）覆盖：不可索引字符不崩、全角数字归一可查、
全角逗号当分隔符、ASCII 大小写覆盖口径、--pool all 不被吞。只读。

这是「外部题集」验收，不是每个库都能跑：需要 gold.tsv（qid/path/category）
和同目录的 eval-baseline.tsv、eval-index.tsv。缺任何一项就退出码 2
（前置缺失，没跑），不打印「全过」。gold 行指向的正文不在库里的，算失败：
题集和库对不上，验收就没有意义。

退出码：0 通过；1 有失败项；2 没跑（缺题集或读数）。
"""
import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

ROOT = L.ROOT
QSEARCH = os.path.join(HERE, "qsearch.py")
RG = L.RG
BATCH_ROOT = os.path.join(ROOT, "工作区", "批次")
fails = []
skips = []
passes = 0


def run(args):
    out = subprocess.run([sys.executable, QSEARCH] + args + ["--json"], cwd=ROOT,
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def corpus_files(term):
    env = dict(os.environ)
    rg_dir = os.path.dirname(RG)
    if rg_dir:
        env["PATH"] = rg_dir + ":" + env.get("PATH", "")
    r = subprocess.run([RG, "-l", "-F", "-g", "*.md", "-e", term, "books"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    return [l for l in r.stdout.splitlines() if l]


def check(ok, name, detail):
    global passes
    print(("PASS " if ok else "FAIL ") + name + "  " + detail)
    if ok:
        passes += 1
    else:
        fails.append(name)


def find_gold(explicit=None):
    """按 --gold -> BOOKS_P1_GOLD -> 最近一个 qa-检索-* 批次的顺序找 gold。

    返回 (路径, 来源)；找不到返回 (None, "")。
    """
    candidates = []
    if explicit:
        candidates.append((os.path.abspath(os.path.expanduser(explicit)), "--gold"))
    env = os.environ.get("BOOKS_P1_GOLD")
    if env:
        candidates.append((os.path.abspath(os.path.expanduser(env)), "BOOKS_P1_GOLD"))
    for path, why in candidates:
        if os.path.isfile(path):
            return path, why
    if os.path.isdir(BATCH_ROOT):
        batches = sorted((n for n in os.listdir(BATCH_ROOT) if n.startswith("qa-检索-")),
                         reverse=True)
        for name in batches:
            path = os.path.join(BATCH_ROOT, name, "gold.tsv")
            if os.path.isfile(path):
                return path, "最近的 qa-检索 批次 %s" % name
    return None, ""


def load_gold(path):
    """读 gold；指向的正文不在库里的算失败，逐条打印。"""
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    mapped = []
    for row in rows:
        qid = row.get("qid") or "?"
        new = (row.get("path") or "").strip().replace("\\", "/")
        while new.startswith("./"):
            new = new[2:]
        if not new or not os.path.isfile(os.path.join(ROOT, new)):
            check(False, "gold " + qid, "题集指的正文不在库里: %s" % (new or "（空路径）"))
            continue
        mapped.append((row, new))
    if rows and len(mapped) == len(rows):
        check(True, "gold 路径", "%d/%d 行都落在库里" % (len(mapped), len(rows)))
    return rows, mapped


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
        con = sqlite3.connect("file:%s?mode=ro" % L.PATHS.index_db, uri=True)
        expr = '"自由"'
        # limit 放大过池深，返回条数才反映 pool 的取数深度
        all_hits, _ = qsearch.search(con, expr, ["自由"], [], 10 ** 9, 40, False, 0)
        low_hits, _ = qsearch.search(con, expr, ["自由"], [], 10 ** 9, 40, False, 100)
        con.close()
        check(len(all_hits) > len(low_hits), "--pool all 不被吞",
              "all=%d 段 vs 100=%d 段" % (len(all_hits), len(low_hits)))
    except Exception as exc:  # noqa: BLE001
        check(False, "--pool all 不被吞", "异常 %s: %s" % (type(exc).__name__, exc))


def read_ranks(path):
    with open(path, encoding="utf-8") as fh:
        return {r["qid"] + r["system"]: r for r in csv.DictReader(fh, delimiter="\t")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="P1 检索验收")
    parser.add_argument("--gold", help="gold.tsv 路径；默认先找工作区批次")
    args = parser.parse_args(argv)

    gold_path, source = find_gold(args.gold)
    if not gold_path:
        print("没跑：库里没有题集（gold.tsv）。P1 验收要用自己的检索题集：")
        print("  --gold <path>/gold.tsv，或放 工作区/批次/qa-检索-<日期>/gold.tsv；")
        print("  列：qid  question  path  category（取值见 docs/检索/README.md）。")
        print("  不看题集也能查检索层：book-corpus verify（箱子 4）。")
        return 2
    print("P1 gold: %s（来源：%s）" % (gold_path, source))
    rows, mapped = load_gold(gold_path)
    if not mapped:
        print("没跑：题集里没有一行能落到库里的正文。")
        return 2

    two_char_words()
    edge_cases()

    batch_dir = os.path.dirname(gold_path)
    idx_db = L.PATHS.index_db
    readings = {}
    for fn in ("eval-baseline.tsv", "eval-index.tsv"):
        path = os.path.join(batch_dir, fn)
        if not os.path.isfile(path):
            check(False, "评测读数 " + fn, "缺文件 " + path)
            continue
        readings[fn] = path
        if os.path.isfile(idx_db) and os.path.getmtime(path) < os.path.getmtime(idx_db):
            print("警告：%s 比 index.db 旧，原词类判据读的是旧索引的读数" % path)
    if len(readings) == 2:
        base = read_ranks(readings["eval-baseline.tsv"])
        idx = read_ranks(readings["eval-index.tsv"])
        gold = {r["qid"]: r for r in rows}
        qids = [q for q, r in gold.items() if r.get("category") == "原词"]
        missing = [q for q in qids if q + "A" not in base or q + "E" not in idx]
        if not qids:
            check(False, "原词类不低于裸 grep",
                  "gold 没有 category=原词 的行，这项判据跑不了；题集要覆盖原词类")
        elif missing:
            check(False, "原词类不低于裸 grep", "读数缺 qid: %s" % " ".join(missing))
        else:
            for label, table, sysname in (("A 裸 grep", base, "A"), ("E FTS", idx, "E")):
                ranks = [int(table[q + sysname]["rank"]) for q in qids]
                print("     原词 %s recall@10 %.2f" % (
                    label, sum(1 for x in ranks if x <= 10) / len(ranks)))
            a = [int(base[q + "A"]["rank"]) for q in qids]
            e = [int(idx[q + "E"]["rank"]) for q in qids]
            check(sum(1 for x in e if x <= 10) >= sum(1 for x in a if x <= 10),
                  "原词类不低于裸 grep", "E %d/%d vs A %d/%d 进前 10" % (
                      sum(1 for x in e if x <= 10), len(qids),
                      sum(1 for x in a if x <= 10), len(qids)))

    print("汇总 PASS %d / FAIL %d" % (passes, len(fails)))
    if fails:
        print("失败 %d 项" % len(fails))
        return 1
    print("全过：P1 %d 项" % passes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
