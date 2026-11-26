#!/usr/bin/env python3
"""四档基线评测：A grep 字面 / B rg 字面 / C defpass 概念组 / D grep 近义词组。

gold.tsv 一行一题，每个系统给目标章在候选列表里的名次（超过 99 记 99，没进候选也记 99）。
候选只认 books/ 下的 .md。索引本身只读，但 K/L/M 档会写缓存：K 写 目录/检索/expand_queries/，
L 与 M 写 目录/检索/route_cache/。

  eval_retrieval.py --gold gold.tsv --system A,B,C,D --k 10,20 --out eval-baseline.tsv
"""

import argparse
import csv
import functools
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import book_query
import book_score
import lib_common as L
import lib_fts_cjk as cjk
import vec_search

ROOT = L.ROOT
DEFPASS = os.path.join(HERE, "defpass.py")
RG = L.RG
QEXPAND = os.path.join(HERE, "query_expand.py")
QSEARCH = os.path.join(HERE, "qsearch.py")
CAP = 99


def env(root=None):
    e = dict(os.environ)
    rg_dir = os.path.dirname(RG)
    if rg_dir:
        e["PATH"] = rg_dir + ":" + e.get("PATH", "")
    if root:
        e["BOOKS_ROOT"] = root
    return e


def grep_files(patterns, literal=True, *, root=None):
    cmd = ["grep", "-rl", "--include=*.md"]
    cmd += ["-F" if literal else "-E"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("books")
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line]


def rg_files(patterns, *, root=None):
    cmd = [RG, "-l", "-F", "--sort", "path", "-g", "*.md"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("books")
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True, env=env(root))
    return [line for line in out.stdout.splitlines() if line]


def grep_expanded(patterns, excludes, *, root=None):
    cmd = ["grep", "-rn", "--include=*.md", "-E"]
    for p in patterns:
        cmd += ["-e", p]
    cmd.append("books")
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True)
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


def defpass_files(terms, aliases, *, root=None):
    cmd = [sys.executable, DEFPASS, "books", "-g", ",".join(terms), "-g", ",".join(aliases), "-n", "500"]
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True, env=env(root))
    seen, order = set(), []
    for line in out.stdout.splitlines():
        m = re.match(r"^\[[^\]]+\]\s+(.+?):(\d+)$", line)
        if not m:
            continue
        path = "books/" + m.group(1)
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def qsearch_files(args, *, root=None, index_db=None):
    cmd = [sys.executable, QSEARCH, *args, "--json", "-n", "100"]
    if index_db:
        cmd.extend(["--db", index_db])
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True, env=env(root))
    if out.returncode != 0:
        raise RuntimeError(
            "%s 退出码 %d：%s" % (os.path.basename(QSEARCH), out.returncode, (out.stderr or "").strip()[-300:])
        )
    try:
        hits = json.loads(out.stdout)["hits"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("%s 回包读不出 hits：%s" % (os.path.basename(QSEARCH), exc)) from exc
    seen, order = set(), []
    for h in hits:
        path = h["path"]
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def qexpand_files(args, *, root=None, index_db=None):
    cmd = [sys.executable, QEXPAND, *args, "--json", "-n", "100"]
    if index_db:
        cmd.extend(["--db", index_db])
    out = subprocess.run(cmd, cwd=root or ROOT, capture_output=True, text=True, env=env(root))
    if out.returncode != 0:
        raise RuntimeError(
            "%s 退出码 %d：%s" % (os.path.basename(QEXPAND), out.returncode, (out.stderr or "").strip()[-300:])
        )
    try:
        hits = json.loads(out.stdout)["hits"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("%s 回包读不出 hits：%s" % (os.path.basename(QEXPAND), exc)) from exc
    seen, order = set(), []
    for h in hits:
        path = h["path"]
        if path not in seen:
            seen.add(path)
            order.append(path)
    return order


def vec_files(question, *, emb_dir=None, url=None, top_k=500):
    hits, _meta = vec_search.search_text(question, top_k=top_k, url=url, emb_dir=emb_dir)
    seen, order = set(), []
    for h in hits:
        path = h.get("path")
        if path and path not in seen:
            seen.add(path)
            order.append(path)
    return order


def book_query_files(
    terms,
    question,
    *,
    index_db=None,
    lib_db=None,
    root=None,
    emb_dir=None,
    url=None,
    hybrid=True,
    limit=50,
    max_chapters=10,
):
    db_path = index_db or (os.path.join(root, "build", "检索", "index.db") if root else book_score.DB)
    if lib_db and os.path.isfile(lib_db):
        meta = book_score.load_meta(lib_db)
    elif root:
        cand_lib = os.path.join(root, "build", "library.db")
        meta = book_score.load_meta(cand_lib) if os.path.isfile(cand_lib) else {}
    else:
        meta = None
    q_terms = terms or cjk.split_groups(question)
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        planner = book_query.Planner(
            con,
            meta=meta,
            limit=limit,
            root=root,
            emb_dir=emb_dir if hybrid else None,
            embed_url=url if hybrid else None,
            vec_fn=(lambda _q, _ex: ([], {"used": False, "ms": 0.0, "hits": 0, "qvec": None})) if not hybrid else None,
        )
        plan = planner.plan(q_terms, chapters=True, max_chapters=max_chapters, question=question)
    finally:
        con.close()
    seen, order = set(), []
    for bk in plan.chapters:
        for ch in bk.get("selected") or []:
            path = ch.get("chapter_path")
            if path and path not in seen:
                seen.add(path)
                order.append(path)
    return order


def rank_of(path, candidates):
    try:
        pos = candidates.index(path) + 1
    except ValueError:
        return CAP, ""
    return min(pos, CAP), pos


def parse_ks(raw):
    """解析逗号分隔的正整数 --k 列表，去重并保持升序。"""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("非法 --k 值：%r（应为逗号分隔的正整数）" % raw)
    parts = [x.strip() for x in text.split(",")]
    if any(not p for p in parts):
        raise ValueError("非法 --k 值：%r（应为逗号分隔的正整数）" % raw)
    ks = []
    for p in parts:
        try:
            val = int(p)
        except ValueError:
            raise ValueError("非法 --k 值：%r（%r 不是整数）" % (raw, p)) from None
        if val <= 0:
            raise ValueError("非法 --k 值：%r（%d 必须为正整数）" % (raw, val))
        ks.append(val)
    return sorted(set(ks))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--system", default="A,B,C,D")
    ap.add_argument("--k", default="10,20")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题，调试用")
    ap.add_argument("--jobs", type=int, default=8, help="并发题数；1 为串行")
    ap.add_argument("--root", help="隔离工作区根（不改生产 build/检索/）")
    ap.add_argument("--index-db", help="显式隔离 index.db 路径")
    ap.add_argument("--lib-db", help="显式隔离 library.db 路径")
    ap.add_argument("--emb-dir", help="显式隔离向量库目录（包含 emb.db 与 vectors.f16）")
    ap.add_argument("--server", help="embedding HTTP 服务地址（默认 BOOKS_EMBED_URL）")
    a = ap.parse_args(argv)
    work_root = os.path.abspath(a.root) if a.root else None
    index_db = a.index_db or (os.path.join(work_root, "build", "检索", "index.db") if work_root else None)
    lib_db = a.lib_db or (os.path.join(work_root, "build", "library.db") if work_root else None)
    emb_dir = a.emb_dir or (os.path.join(work_root, "build", "检索", "embeddings") if work_root else None)
    systems = [s.strip() for s in a.system.split(",") if s.strip()]
    if not systems:
        ap.error("至少指定一个 --system")
    try:
        ks = parse_ks(a.k)
    except ValueError as exc:
        ap.error(str(exc))
    with open(a.gold, encoding="utf-8") as fh:
        gold = list(csv.DictReader(fh, delimiter="\t"))
    if a.limit:
        gold = gold[: a.limit]

    q_kw = {}
    if work_root is not None:
        q_kw["root"] = work_root
    if index_db is not None:
        q_kw["index_db"] = index_db

    def one(sysname, r):
        terms = [t for t in r["terms"].split(",") if t]
        if sysname == "A":
            cand = grep_files(terms, root=work_root) if work_root else grep_files(terms)
        elif sysname == "B":
            cand = rg_files(terms, root=work_root) if work_root else rg_files(terms)
        elif sysname == "C":
            cand = defpass_files(terms, terms, root=work_root) if work_root else defpass_files(terms, terms)
        elif sysname == "D":
            pat = [re.escape(p) for p in terms]
            cand = grep_expanded(pat, [], root=work_root) if work_root else grep_expanded(pat, [])
        elif sysname == "E":
            cand = qsearch_files([" ".join(terms)], **q_kw)
        elif sysname == "G":
            cand = qsearch_files(["--expand", "--expand-kind", "prefix", " ".join(terms)], **q_kw)
        elif sysname == "K":
            cand = qexpand_files([" ".join(terms), "--question", r["question"]], **q_kw)
        elif sysname in ("K1", "K2"):
            cand = qexpand_files(
                [
                    " ".join(terms),
                    "--question",
                    r["question"],
                    "--grain",
                    "chapter",
                    "--base-weight",
                    "1" if sysname == "K1" else "2",
                ],
                **q_kw,
            )
        elif sysname == "J":
            cand = qsearch_files(["--expand", "--expand-kind", "queries", " ".join(terms)], **q_kw)
        elif sysname == "H":
            cand = qsearch_files(["--rerank", " ".join(terms)], **q_kw)
        elif sysname == "I":
            cand = qsearch_files(["--expand", "--rerank", " ".join(terms)], **q_kw)
        elif sysname == "V":
            cand = vec_files(r.get("question") or " ".join(terms), emb_dir=emb_dir, url=a.server)
        elif sysname == "BQ":
            cand = book_query_files(
                terms,
                r.get("question") or " ".join(terms),
                index_db=index_db,
                lib_db=lib_db,
                root=work_root,
                hybrid=False,
            )
        elif sysname == "HYB":
            cand = book_query_files(
                terms,
                r.get("question") or " ".join(terms),
                index_db=index_db,
                lib_db=lib_db,
                root=work_root,
                emb_dir=emb_dir,
                url=a.server,
                hybrid=True,
            )
        else:
            sys.exit("未知系统 " + sysname)
        rank, pos = rank_of(r["path"], cand)
        rec = {
            "qid": r["qid"],
            "system": sysname,
            "rank": rank,
            "pos": pos,
        }
        for k in ks:
            rec["hit%d" % k] = int(rank <= k)
        return rec

    rows, summary, skipped = [], {}, {}
    for sysname in systems:
        t0 = time.time()
        summary.setdefault(sysname, {"cat": {}, "secs": 0.0})
        if a.jobs > 1:
            with ThreadPoolExecutor(a.jobs) as ex:
                recs = list(ex.map(functools.partial(one, sysname), gold))
        else:
            recs = [one(sysname, r) for r in gold]
        for r, rec in zip(gold, recs, strict=False):
            if rec is None:
                skipped.setdefault(sysname, []).append(r["qid"])
                continue
            rows.append(rec)
            summary[sysname]["cat"].setdefault(r["category"], []).append(rec["rank"])
        summary[sysname]["secs"] = round(time.time() - t0, 1)
        print(sysname, "完成", summary[sysname]["secs"], "秒", file=sys.stderr)
        if skipped.get(sysname):
            print(
                "%s 跳过 %d 题：概念表里没有 %s"
                % (
                    sysname,
                    len(skipped[sysname]),
                    " ".join(sorted({r["concept"] for r in gold if r["qid"] in skipped[sysname]})),
                ),
                file=sys.stderr,
            )

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("qid\tsystem\trank\tpos\t" + "\t".join("hit%d" % k for k in ks) + "\n")
        for rec in rows:
            f.write(
                "\t".join(
                    [rec["qid"], rec["system"], str(rec["rank"]), str(rec["pos"])] + [str(rec["hit%d" % k]) for k in ks]
                )
                + "\n"
            )
    os.replace(tmp, a.out)

    sp = a.out.rsplit(".", 1)[0] + "-summary.tsv"
    stmp = sp + ".tmp"
    max_k = max(ks)
    with open(stmp, "w", encoding="utf-8") as f:
        header = ["system", *("recall@%d" % k for k in ks), "mrr", "no_term_hit%d" % max_k, "n", "secs"]
        f.write("\t".join(header) + "\n")
        for sysname in systems:
            allr = [x for v in summary[sysname]["cat"].values() for x in v]
            no = summary[sysname]["cat"].get("无原词", [])
            n = len(allr)
            recalls = ["%.3f" % (sum(1 for x in allr if x <= k) / n) if n else "nan" for k in ks]
            mrr = "%.3f" % (sum(1.0 / x for x in allr if x < CAP) / n) if n else "nan"
            no_hit = "%.3f" % (sum(1 for x in no if x <= max_k) / len(no)) if no else "nan"
            row = [
                sysname,
                *recalls,
                mrr,
                no_hit,
                str(n),
                str(summary[sysname]["secs"]),
            ]
            f.write("\t".join(row) + "\n")
            print("  ".join(row))
    os.replace(stmp, sp)


if __name__ == "__main__":
    main()
