#!/usr/bin/env python3
"""对第一轮失败的无原词题做一次 agent 复核：看 top5 书，选书或改词，再出计划。

  refine_plans.py --workers 3
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import build_expand as be  # noqa: E402
import qsearch_plan as qp  # noqa: E402

GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
OUT = os.path.join(HERE, "plans", "refined")
EVAL = os.path.join(HERE, "eval-plan-full.tsv")

PROMPT = """你是中文书库的检索员。第一轮检索没找到目标，现在看候选书名和片段，决定下一步。

问题：{q}

第一轮 top5 书（书名 | 章名 | 片段）：
{books}

规则：
1. 先判断哪一本书最可能回答这个问题。只从上面 5 本里选。
2. 如果有一本明显相关，book_hint 原样照抄这本书的目录名，groups 只在这本书里搜。
3. 如果五本都不像，book_hint 留空，改写全局计划，加入更具体的机制词。
4. 只输出 JSON，不要解释：
{{
  "question": "{q}",
  "reason": "一句话为什么选这本书或为什么都不选",
  "literal": ["问题里的原词", "2到4个"],
  "concepts": ["概念表键", "0到2个"],
  "groups": [
    {{"name": "literal", "terms": ["..."], "weight": 1.0}}
  ],
  "book_hint": "候选书名或空字符串",
  "exclude": []
}}
5. 不要写 bridge 组。literal 里放这本书里可能出现的具体词，不要只复述问题。"""


def load_fails():
    rows = list(csv.DictReader(open(EVAL, encoding="utf-8-sig"), delimiter="\t"))
    return [r["qid"] for r in rows
            if r["variant"] == "lit1_c12" and r["category"] == "无原词"
            and r["rank"] == "99"]


def first_pass(qid, gold):
    plan = json.load(open(os.path.join(HERE, "plans", "full", qid + ".json"),
                          encoding="utf-8"))
    p = dict(plan)
    p["groups"] = [g for g in plan.get("groups") or [] if g.get("name") == "literal"]
    p["concepts"] = plan.get("concepts") or []
    p["concept_weight"] = 1.2
    hits = qp.run(p, limit=100)["hits"]
    books, seen = [], set()
    for h in hits:
        b = h["path"].split("/md/")[0]
        if b in seen:
            continue
        seen.add(b)
        books.append((b, os.path.basename(h["path"]), (h.get("text") or "")[:160]))
        if len(books) >= 5:
            break
    return books


def parse(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("回包没有 JSON")
    return json.loads(m.group(0))


def refine(qid, row, gold):
    path = os.path.join(OUT, qid + ".json")
    if os.path.isfile(path):
        return qid, "cached", 0
    books = first_pass(qid, gold)
    lines = "\n".join("%d. %s | %s | %s" % (i + 1, b[0], b[1], b[2])
                      for i, b in enumerate(books))
    txt, usage = be.call(PROMPT.format(q=row["question"], books=lines),
                         argparse.Namespace(provider="opencode", max_out=2500))
    d = parse(txt)
    d["question"] = row["question"]
    d.setdefault("groups", [])
    d.setdefault("concepts", [])
    d.setdefault("book_hint", "")
    d.setdefault("exclude", [])
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(path + ".tmp", path)
    return qid, "ok", int(usage.get("input_tokens", 0) or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    gold = {r["qid"]: r for r in csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t")}
    fails = load_fails()
    print("refine %d 题" % len(fails))
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        futs = [pool.submit(refine, q, gold[q], gold) for q in fails]
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                qid, status, tok = fut.result()
            except Exception as exc:  # noqa: BLE001
                print("FAIL", exc, file=sys.stderr)
                continue
            print("  %d/%d %s %s %d tok %.0fs" % (n, len(fails), qid, status, tok,
                                                  time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
