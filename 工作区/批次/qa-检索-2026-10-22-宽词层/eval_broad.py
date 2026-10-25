#!/usr/bin/env python3
"""Compare literal-only plans against plans that add the broad word group.

  eval_broad.py --rows 2 --variants no_broad,with_broad
  eval_broad.py
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import qsearch_plan as qp  # noqa: E402

CAP = 99
BROAD_TABLE = os.path.join(ROOT, "目录", "概念表-broad.tsv")

VARIANTS = {
    "no_broad": {"dir": "no_broad", "broad_weight": None},
    "with_broad": {"dir": "with_broad", "broad_weight": None},
    "with_broad06": {"dir": "with_broad", "broad_weight": 0.6},
    "with_broad12": {"dir": "with_broad", "broad_weight": 1.2},
}

COLUMNS = ["qid", "broad", "variant", "rank", "hit10", "hit20", "book_rank",
           "book_hit10", "key_free", "false10", "ms"]


def broad_terms(key):
    with open(BROAD_TABLE, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["key"] == key:
                return [key] + [a for a in (r["aliases"] or "").split(",") if a]
    return [key]


def load_rows(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_plan(plans, variant, row):
    spec = VARIANTS[variant]
    path = os.path.join(plans, spec["dir"], row["qid"] + ".json")
    with open(path, encoding="utf-8") as fh:
        plan = json.load(fh)
    if variant == "no_broad":
        if plan.get("broad"):
            raise SystemExit("计划不符 %s/%s：no_broad 计划带了宽词 %r"
                             % (variant, row["qid"], plan["broad"]))
    elif plan.get("broad") != [row["broad"]]:
        raise SystemExit("计划不符 %s/%s：宽词 %r，heldout 记的是 %r"
                         % (variant, row["qid"], plan.get("broad"), [row["broad"]]))
    if spec["broad_weight"] is not None:
        plan["broad_weight"] = spec["broad_weight"]
    return plan


def measure(row, hits, terms, book_prefix):
    rank, book_rank, false10 = CAP, CAP, 0
    for i, h in enumerate(hits, 1):
        path = h["path"]
        if rank == CAP and path == row["path"]:
            rank = min(i, CAP)
        if book_rank == CAP and path.startswith(book_prefix):
            book_rank = min(i, CAP)
        if i <= 10 and path != row["path"]:
            text = h.get("text") or ""
            if not any(t in text for t in terms):
                false10 += 1
    return rank, book_rank, false10


def evaluate(rows, plans, variants, limit):
    out = []
    for row in rows:
        terms = broad_terms(row["broad"])
        book_prefix = row["path"].split("/md/")[0] + "/md/"
        for v in variants:
            plan = load_plan(plans, v, row)
            try:
                res = qp.run(plan, limit=limit, pool=2000, group_limit=100)
            except SystemExit:
                hits, ms = [], 0
            else:
                hits, ms = res["hits"], res["ms"]
            rank, book_rank, false10 = measure(row, hits, terms, book_prefix)
            out.append({
                "qid": row["qid"], "broad": row["broad"], "variant": v,
                "rank": rank, "hit10": int(rank <= 10), "hit20": int(rank <= 20),
                "book_rank": book_rank, "book_hit10": int(book_rank <= 10),
                "key_free": int(row["chapter_has_key"] == "0"),
                "false10": false10, "ms": ms,
            })
    return out


def mean(rs, key):
    return sum(r[key] for r in rs) / len(rs) if rs else 0.0


def summarize(rows, variants):
    summary = []
    for v in variants:
        rs = [r for r in rows if r["variant"] == v]
        kf = [r for r in rs if r["key_free"]]
        summary.append({
            "variant": v, "n": len(rs),
            "hit10": mean(rs, "hit10"), "hit20": mean(rs, "hit20"),
            "mrr": sum(1 / r["rank"] if r["rank"] < CAP else 0 for r in rs) / len(rs),
            "book_hit10": mean(rs, "book_hit10"),
            "book_hit20": sum(1 for r in rs if r["book_rank"] <= 20) / len(rs),
            "book_mrr": sum(1 / r["book_rank"] if r["book_rank"] < CAP else 0
                            for r in rs) / len(rs),
            "key_free_n": len(kf),
            "key_free_hit10": mean(kf, "hit10"), "key_free_hit20": mean(kf, "hit20"),
            "false10_per_q": mean(rs, "false10"),
        })
    return summary


def summarize_by_broad(rows, variants):
    out = []
    for broad in sorted({r["broad"] for r in rows}):
        for v in variants:
            rs = [r for r in rows if r["variant"] == v and r["broad"] == broad]
            if rs:
                out.append({"broad": broad, "variant": v, "n": len(rs),
                            "hit10": mean(rs, "hit10"), "hit20": mean(rs, "hit20")})
    return out


def write_tsv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r[k] for k in COLUMNS])
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", default=os.path.join(HERE, "heldout.tsv"))
    ap.add_argument("--plans", default=os.path.join(HERE, "plans"))
    ap.add_argument("--variants", default="no_broad,with_broad,with_broad06,with_broad12")
    ap.add_argument("--rows", type=int, default=0, help="0 表示全部")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(HERE, "eval-broad.tsv"))
    a = ap.parse_args()

    variants = [v.strip() for v in a.variants.split(",") if v.strip()]
    for v in variants:
        if v not in VARIANTS:
            raise SystemExit("未知变体 " + v)
    rows = load_rows(a.heldout)
    if a.rows:
        rows = rows[:a.rows]
    result = evaluate(rows, a.plans, variants, a.limit)
    write_tsv(a.out, result)

    summary = summarize(result, variants)
    print("variant\tn\thit10\thit20\tmrr\tbook_hit10\tbook_hit20\tbook_mrr\t"
          "key_free_n\tkey_free_hit10\tkey_free_hit20\tfalse10_per_q")
    for s in summary:
        print("%s\t%d\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%d\t%.3f\t%.3f\t%.2f" % (
            s["variant"], s["n"], s["hit10"], s["hit20"], s["mrr"], s["book_hit10"],
            s["book_hit20"], s["book_mrr"], s["key_free_n"], s["key_free_hit10"],
            s["key_free_hit20"], s["false10_per_q"]))

    by_broad = summarize_by_broad(result, variants)
    print("")
    print("broad\tvariant\tn\thit10\thit20")
    for s in by_broad:
        print("%s\t%s\t%d\t%.2f\t%.2f" % (s["broad"], s["variant"], s["n"],
                                          s["hit10"], s["hit20"]))

    miss = [r for r in result if r["rank"] == CAP]
    print("")
    print("未进 top%d %d 条" % (a.limit, len(miss)))
    for r in miss:
        print("  %s %s %s" % (r["qid"], r["variant"], r["broad"]))

    jpath = os.path.splitext(a.out)[0] + ".json"
    tmp = jpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"heldout": a.heldout, "plans": a.plans, "limit": a.limit,
                   "variants": variants, "summary": summary, "by_broad": by_broad,
                   "not_in_top": [{"qid": r["qid"], "variant": r["variant"]}
                                  for r in miss],
                   "rows": result}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, jpath)
    print("->", a.out, jpath)


if __name__ == "__main__":
    main()
