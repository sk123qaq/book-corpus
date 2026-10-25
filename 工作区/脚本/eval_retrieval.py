#!/usr/bin/env python3
"""四档基线评测：A grep 字面 / B rg 字面 / C defpass 概念组 / D grep 近义词组。

gold.tsv 一行一题，每个系统给目标章在候选列表里的名次（超过 99 记 99，没进候选也记 99）。
候选只认库下的 .md。索引本身只读，但 K/L/M 档会写缓存：K 写 目录/检索/expand_queries/，
L 与 M 写 目录/检索/route_cache/。

  eval_retrieval.py --gold gold.tsv --system A,B,C,D --k 10,20 --out eval-baseline.tsv
"""
import argparse, csv, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.expanduser("~/文档/books")
CONCEPTS = os.path.join(ROOT, "目录", "概念表.tsv")
DEFPASS = os.path.join(ROOT, "工作区", "脚本", "defpass.py")
RG = "/home/shike/.pi/agent/bin/rg"
QEXPAND = os.path.join(ROOT, "工作区", "脚本", "query_expand.py")
QROUTE = os.path.join(ROOT, "工作区", "脚本", "concept_route.py")
QSEARCH = os.path.join(ROOT, "工作区", "脚本", "qsearch.py")
CAP = 99


def concept_table():
    table = {}
    with open(CONCEPTS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            table[r["key"]] = (
                [a for a in r["aliases"].split(",") if a],
                [x for x in r["exclude"].split(",") if x and x != "无"],
            )
    return table


def env():
    e = dict(os.environ)
    e["PATH"] = os.path.dirname(RG) + ":" + e.get("PATH", "")
    return e


def grep_files(patterns, literal=True):
    cmd = ["grep", "-rl", "--include=*.md"]
    cmd += ["-F" if literal else "-E"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("库")
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l]


def rg_files(patterns):
    cmd = [RG, "-l", "-F", "--sort", "path", "-g", "*.md"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("库")
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env())
    return [l for l in out.stdout.splitlines() if l]


def grep_expanded(patterns, excludes):
    cmd = ["grep", "-rn", "--include=*.md", "-E"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("库")
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    ex = re.compile("|".join(excludes)) if excludes else None
    seen, order = set(), []
    for line in out.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, _, text = parts
        if ex and ex.search(text):
            continue
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def defpass_files(terms, aliases):
    cmd = [sys.executable, DEFPASS, "库", "-g", ",".join(terms), "-g", ",".join(aliases), "-n", "500"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env())
    seen, order = set(), []
    for line in out.stdout.splitlines():
        m = re.match(r"^\[[^\]]+\]\s+(.+?):(\d+)$", line)
        if not m:
            continue
        path = "库/" + m.group(1)
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def qsearch_files(args):
    out = subprocess.run([sys.executable, QSEARCH] + args + ["--json", "-n", "100"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("%s 退出码 %d：%s" % (os.path.basename(QSEARCH), out.returncode,
                                              (out.stderr or "").strip()[-300:]))
    try:
        hits = json.loads(out.stdout)["hits"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("%s 回包读不出 hits：%s" % (os.path.basename(QSEARCH), exc))
    seen, order = set(), []
    for h in hits:
        path = h["path"]
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def qexpand_files(args):
    out = subprocess.run([sys.executable, QEXPAND] + args + ["--json", "-n", "100"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("%s 退出码 %d：%s" % (os.path.basename(QEXPAND), out.returncode,
                                              (out.stderr or "").strip()[-300:]))
    try:
        hits = json.loads(out.stdout)["hits"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("%s 回包读不出 hits：%s" % (os.path.basename(QEXPAND), exc))
    seen, order = set(), []
    for h in hits:
        path = h["path"]
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def route_files(args):
    out = subprocess.run([sys.executable, QROUTE] + args + ["--json", "-n", "100"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("%s 退出码 %d：%s" % (os.path.basename(QROUTE), out.returncode,
                                              (out.stderr or "").strip()[-300:]))
    try:
        hits = json.loads(out.stdout)["hits"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("%s 回包读不出 hits：%s" % (os.path.basename(QROUTE), exc))
    seen, order = set(), []
    for h in hits:
        path = h["path"]
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def rank_of(path, candidates):
    try:
        pos = candidates.index(path) + 1
    except ValueError:
        return CAP, ""
    return min(pos, CAP), pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--system", default="A,B,C,D")
    ap.add_argument("--k", default="10,20")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题，调试用")
    ap.add_argument("--jobs", type=int, default=8, help="并发题数；1 为串行")
    a = ap.parse_args()
    systems = a.system.split(",")
    ks = [int(x) for x in a.k.split(",")]
    gold = list(csv.DictReader(open(a.gold, encoding="utf-8"), delimiter="\t"))
    if a.limit:
        gold = gold[: a.limit]
    table = concept_table()

    def one(sysname, r):
        terms = [t for t in r["terms"].split(",") if t]
        aliases, excludes = table.get(r["concept"], ([], []))
        if sysname == "A":
            cand = grep_files(terms)
        elif sysname == "B":
            cand = rg_files(terms)
        elif sysname == "C":
            cand = defpass_files(terms, aliases or terms)
        elif sysname == "D":
            pool = terms + aliases
            cand = grep_expanded([re.escape(p) for p in pool], excludes)
        elif sysname == "E":
            cand = qsearch_files([" ".join(terms)])
        elif sysname == "F":
            cand = qsearch_files(["--concept", r["concept"]])
        elif sysname == "G":
            cand = qsearch_files(["--expand", "--expand-kind", "prefix", " ".join(terms)])
        elif sysname == "N":
            cand = qsearch_files(["--route", "--route-mode", "append", " ".join(terms)])
        elif sysname == "M":
            cand = qsearch_files(["--route", " ".join(terms)])
        elif sysname == "L":
            cand = route_files([r["question"]])
        elif sysname == "K":
            cand = qexpand_files([" ".join(terms), "--question", r["question"]])
        elif sysname in ("K1", "K2"):
            cand = qexpand_files([" ".join(terms), "--question", r["question"],
                                  "--grain", "chapter",
                                  "--base-weight", "1" if sysname == "K1" else "2"])
        elif sysname == "J":
            cand = qsearch_files(["--expand", "--expand-kind", "queries", " ".join(terms)])
        elif sysname == "H":
            cand = qsearch_files(["--rerank", " ".join(terms)])
        elif sysname == "I":
            cand = qsearch_files(["--expand", "--rerank", " ".join(terms)])
        else:
            sys.exit("未知系统 " + sysname)
        rank, pos = rank_of(r["path"], cand)
        return {"qid": r["qid"], "system": sysname, "rank": rank, "pos": pos,
                "hit10": int(rank <= 10), "hit20": int(rank <= 20)}

    rows, summary = [], {}
    for sysname in systems:
        t0 = time.time()
        if a.jobs > 1:
            with ThreadPoolExecutor(a.jobs) as ex:
                recs = list(ex.map(lambda r: one(sysname, r), gold))
        else:
            recs = [one(sysname, r) for r in gold]
        for r, rec in zip(gold, recs):
            rows.append(rec)
            summary.setdefault(sysname, {"cat": {}})
            summary[sysname]["cat"].setdefault(r["category"], []).append(rec["rank"])
        summary[sysname]["secs"] = round(time.time() - t0, 1)
        print(sysname, "完成", summary[sysname]["secs"], "秒", file=sys.stderr)

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("qid\tsystem\trank\tpos\t" + "\t".join("hit%d" % k for k in ks) + "\n")
        for rec in rows:
            f.write("\t".join([rec["qid"], rec["system"], str(rec["rank"]), str(rec["pos"])] +
                              [str(rec["hit%d" % k]) for k in ks]) + "\n")
    os.replace(tmp, a.out)

    sp = a.out.rsplit(".", 1)[0] + "-summary.tsv"
    stmp = sp + ".tmp"
    with open(stmp, "w", encoding="utf-8") as f:
        f.write("system\trecall@10\trecall@20\tmrr\tno_term_hit20\tn\tsecs\n")
        for sysname in systems:
            allr = [x for v in summary[sysname]["cat"].values() for x in v]
            no = summary[sysname]["cat"].get("无原词", [])
            row = [sysname,
                   "%.3f" % (sum(1 for x in allr if x <= 10) / len(allr)),
                   "%.3f" % (sum(1 for x in allr if x <= 20) / len(allr)),
                   "%.3f" % (sum(1.0 / x for x in allr if x < CAP) / len(allr)),
                   "%.3f" % (sum(1 for x in no if x <= 20) / len(no)) if no else "nan",
                   str(len(allr)), str(summary[sysname]["secs"])]
            f.write("\t".join(row) + "\n")
            print("  ".join(row))
    os.replace(stmp, sp)


if __name__ == "__main__":
    main()
