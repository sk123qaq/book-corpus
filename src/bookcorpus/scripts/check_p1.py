#!/usr/bin/env python3
"""P1 验收：2 字词可查且有序、译名墙案例、原词类不低于裸 grep、查询边界回归。

边界回归段（edge_cases）覆盖：不可索引字符不崩、全角数字归一可查、
全角逗号当分隔符、ASCII 大小写覆盖口径、--pool all 不被吞。
只读。任一不过就退出码 1。

gold 发现顺序：工作区默认批次 -> --gold -> BOOKS_P1_GOLD -> SKIP 并打印原因。
旧布局 `库/.../md/...` 经 tables/对照表.csv 的「新路径 -> uid」映射成
`books/<uid>/md/...`；映射不到的行逐条 SKIP，不硬 FAIL。译名墙用
build/library.db 把书名解析成 uid 再断言；解析不到也 SKIP。
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
DEFAULT_GOLD = os.path.join(
    ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
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


def skip(name, detail):
    print("SKIP " + name + "  " + detail)
    skips.append(name)


def read_path_map(root=ROOT):
    """tables/对照表.csv 的 库/.../md -> uid 映射。同一条旧路径指向两个 uid 时丢弃。"""
    path = os.path.join(root, "tables", "对照表.csv")
    if not os.path.isfile(path):
        return {}
    table, dup = {}, set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            old = (row.get("新路径") or "").strip()
            if not old.startswith("库/"):
                alt = (row.get("旧路径") or "").strip()
                if alt.startswith("库/"):
                    old = alt
            old = old.rstrip("/")
            uid = (row.get("uid") or "").strip()
            if not old or not uid:
                continue
            if old in table and table[old] != uid:
                dup.add(old)
            table[old] = uid
    for old in dup:
        table.pop(old, None)
    return table


def map_gold_path(path, table):
    """库/.../md/x.md -> books/<uid>/md/x.md。

    返回 (新路径, "")；映射不到返回 (None, 原因)。
    """
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return None, "空路径"
    if p.startswith("books/"):
        return p, ""
    if "/md/" in p:
        pre, base = p.split("/md/", 1)
        pre = pre.rstrip("/")
        uid = table.get(pre + "/md") or table.get(pre)
        if uid:
            return "books/%s/md/%s" % (uid, base), ""
    for old in sorted(table, key=len, reverse=True):
        if p == old or p.startswith(old + "/"):
            return "books/%s%s" % (table[old], p[len(old):]), ""
    return None, "tables/对照表.csv 无匹配行"


def resolve_gold(explicit=None):
    """按 默认批次 -> --gold -> BOOKS_P1_GOLD 的顺序找 gold。

    返回 (路径, 来源, 说明列表)；找不到时路径为 None。
    """
    notes = []
    if os.path.isfile(DEFAULT_GOLD):
        return DEFAULT_GOLD, "工作区默认批次", notes
    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
        if os.path.isfile(path):
            return path, "--gold", notes
        notes.append("--gold 不存在: %s" % path)
    env = os.environ.get("BOOKS_P1_GOLD")
    if env:
        path = os.path.abspath(os.path.expanduser(env))
        if os.path.isfile(path):
            return path, "BOOKS_P1_GOLD", notes
        notes.append("BOOKS_P1_GOLD 不存在: %s" % path)
    notes.append("默认路径不存在: %s" % DEFAULT_GOLD)
    return None, "", notes


def load_gold(path):
    """读 gold 并把 库/ 路径映射到 books/<uid>/；不可映射的行逐条 SKIP。"""
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    table = read_path_map(ROOT)
    mapped = []
    for row in rows:
        new, reason = map_gold_path(row.get("path"), table)
        if not new:
            skip("gold %s" % (row.get("qid") or "?"),
                 "%s  %s" % (row.get("path") or "", reason))
            continue
        if not os.path.exists(os.path.join(ROOT, new)):
            skip("gold %s" % (row.get("qid") or "?"),
                 "映射后文件不存在: %s（原 %s）" % (new, row.get("path") or ""))
            continue
        mapped.append((row, new))
    if mapped and len(mapped) == len(rows):
        check(True, "gold 路径映射", "%d/%d 行可用" % (len(mapped), len(rows)))
    elif mapped:
        skip("gold 路径映射", "%d/%d 行可用；%d 条逐条 SKIP" % (
            len(mapped), len(rows), len(rows) - len(mapped)))
    else:
        skip("gold 路径映射", "0/%d 行可用；见上方逐条 SKIP" % len(rows))
    return rows, mapped


def title_uids(want):
    """build/library.db 里按书名找 uid：先精确，再唯一子串。"""
    path = os.path.join(ROOT, "build", "library.db")
    if not os.path.isfile(path):
        return [], "缺 build/library.db"
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        rows = con.execute("SELECT uid, title FROM book").fetchall()
    finally:
        con.close()
    exact = [uid for uid, title in rows if title == want]
    if exact:
        return exact, "精确匹配"
    sub = [uid for uid, title in rows if want in (title or "")]
    if len(sub) == 1:
        return sub, "唯一子串匹配"
    if not sub:
        return [], "build/library.db 无此书"
    return [], "书名匹配到 %d 本，无法唯一确定" % len(sub)


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
    uids, how = title_uids(want_book)
    if not uids:
        skip("译名墙 " + word, "书名解析不到 uid：%s（%s）" % (want_book, how))
        return
    res = run([word, "--auto-concept", "-n", "20"])
    got = [h["path"] for h in res["hits"]]
    hit = [p for p in got if any(p.startswith("books/%s/" % u) for u in uids)]
    check(bool(hit) == want_any, "译名墙 " + word,
          "书名->uid %s（%s）；语料命中 %d 文件；在 top20: %s" % (
              ",".join(uids), how, len(corpus_files(word)), bool(hit)))


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

    gold_path, source, notes = resolve_gold(args.gold)
    if not gold_path:
        skip("P1 gold", "；".join(notes))
        print("失败 0 项")
        return 0
    print("P1 gold: %s（来源：%s）" % (gold_path, source))
    for note in notes:
        print("注  " + note)
    rows, mapped = load_gold(gold_path)

    two_char_words()
    edge_cases()
    wall("过密化", "八次危机")
    wall("群己权界论", "金克木集")
    wall("消极自由", "刘擎西方现代思想讲义")
    for word in ("达克效应", "灰犀牛"):
        n = len(corpus_files(word))
        print("注  %s 语料命中 %d 个文件，不足以做译名墙验收（只记数，不计入通过/失败）"
              % (word, n))

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
            skip("原词类不低于裸 grep", "gold 没有 原词 行")
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

    print("汇总 PASS %d / FAIL %d / SKIP %d" % (passes, len(fails), len(skips)))
    print("失败 %d 项" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
