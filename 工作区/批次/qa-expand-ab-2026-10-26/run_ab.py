#!/usr/bin/env python3
"""同一批问题、同一批查询词，跑两条检索道：段落档，与段落档加 expand 档。

  python3 run_ab.py

查询词由演示页的 trace.extract_terms 出（与书库检索同一个切词），A/B 完全相同。
A 路是 qsearch.py "<词>" -n 10，B 路是同一个命令加 --expand（RRF 并上 expand 档）。
gold 的 path/line 只用来量名次，不进证据。
产物：检索/<qid>-<lane>.json、证据/<qid>.md、对比.tsv。
"""
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
QSEARCH = os.path.join(ROOT, "工作区", "脚本", "qsearch.py")
TERMS = os.path.join(HERE, "terms.jsonl")
OUT_JSON = os.path.join(HERE, "检索")
OUT_EVID = os.path.join(HERE, "证据")
LANES = (("para", []), ("expand", ["--expand"]))
TOP_CHAPTERS = 3
SNIP = 700


def run(terms, flag):
    argv = [sys.executable, QSEARCH, terms, "-n", "100", "--json"] + flag
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        sys.exit("qsearch 失败 %s: %s" % (flag, proc.stderr[-300:]))
    return json.loads(proc.stdout)


def chapters(hits, book):
    order, rows = [], {}
    for r in hits:
        if r.get("book") != book or r["path"] in rows:
            continue
        rows[r["path"]] = r
        order.append(r["path"])
    return order, rows


def chapter_rank(order, gold):
    return order.index(gold) + 1 if gold in order else 0


def head_text(path, chars=450):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8", errors="ignore") as fh:
            text = fh.read(chars * 3)
    except OSError:
        return ""
    return " ".join(text.split())[:chars]


def evidence(question, lane, order, rows):
    lines = ["## %s" % question, "", "### %s 路前 %d 章" % (lane, TOP_CHAPTERS), ""]
    if not order:
        lines += ["本路 0 条命中", ""]
        return lines
    for i, path in enumerate(order[:TOP_CHAPTERS], 1):
        r = rows[path]
        body = r.get("text") or ""
        lines.append("%d. %s:%s  分 %.4f%s" % (
            i, path, r.get("line"), r.get("score"),
            "" if body else "（只有 expand 命中，取章首）"))
        if body:
            lines.append("   " + " ".join(body.split())[:SNIP])
        else:
            exp = (r.get("expand") or "").strip()
            if exp:
                lines.append("   expand 文本: " + " ".join(exp.split())[:260])
            lines.append("   章首: " + head_text(path))
        lines.append("")
    return lines


def main():
    os.makedirs(OUT_JSON, exist_ok=True)
    os.makedirs(OUT_EVID, exist_ok=True)
    questions = [json.loads(line) for line in open(TERMS, encoding="utf-8") if line.strip()]
    table = []
    for q in questions:
        qid, gold = q["qid"], q["gold"]
        book = gold.split("/md/")[0][2:]
        runs = {}
        for lane, flag in LANES:
            data = run(q["terms"], flag)
            with open(os.path.join(OUT_JSON, "%s-%s.json" % (qid, lane)), "w",
                      encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            order, rows = chapters(data["hits"], book)
            runs[lane] = (data, order, rows)
        a, b = runs["para"], runs["expand"]
        shared = len(set(a[1][:5]) & set(b[1][:5]))
        exp_only = sum(1 for p in b[1][:5] if not b[2][p].get("text"))
        table.append({
            "qid": qid, "category": q["category"], "question": q["question"],
            "terms": q["terms"],
            "rank_para": chapter_rank(a[1], gold), "rank_expand": chapter_rank(b[1], gold),
            "top1_para": a[1][0].split("/md/")[-1] if a[1] else "-",
            "top1_expand": b[1][0].split("/md/")[-1] if b[1] else "-",
            "top5_shared": shared, "expand_only_top5": exp_only,
        })
        with open(os.path.join(OUT_EVID, qid + ".md"), "w", encoding="utf-8") as fh:
            fh.write("# %s %s\n\n书 %s\n查询词 %s\ngold %s:%s\n\n" % (
                qid, q["question"], book, q["terms"], gold, q["gold_line"]))
            fh.write("\n".join(evidence(q["question"], "A 段落", a[1], a[2])))
            fh.write("\n" + "\n".join(evidence(q["question"], "B 段落+expand", b[1], b[2])))
    cols = list(table[0])
    with open(os.path.join(HERE, "对比.tsv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(table)
    print("%-5s %-5s %6s %6s %4s %4s" % ("qid", "类", "A名次", "B名次", "共5", "仅扩"))
    for r in table:
        print("%-5s %-5s %6d %6d %4d %4d" % (
            r["qid"], r["category"], r["rank_para"], r["rank_expand"],
            r["top5_shared"], r["expand_only_top5"]))
    for lane, name in (("rank_para", "A"), ("rank_expand", "B")):
        hit1 = sum(1 for r in table if r[lane] == 1)
        hit3 = sum(1 for r in table if 0 < r[lane] <= 3)
        hit5 = sum(1 for r in table if 0 < r[lane] <= 5)
        print("%s 路  gold 前1 %d/10  前3 %d/10  前5 %d/10" % (name, hit1, hit3, hit5))


if __name__ == "__main__":
    main()
