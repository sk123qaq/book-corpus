#!/usr/bin/env python3
"""给 gold 60 题生成 agent 式检索计划，走 opencode。

  make_plans.py --mode full --limit 3
  make_plans.py --mode full --workers 4
  make_plans.py --mode noconcept --workers 4

计划落在 plans/<mode>/<qid>.json，已有文件跳过。
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

GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
CONCEPT_TSV = os.path.join(ROOT, "目录", "概念表.tsv")
PLANS = os.path.join(HERE, "plans")
USAGE = os.path.join(HERE, "plan-usage.jsonl")

SCHEMA = """{
  "question": "原问题",
  "mechanism": "一句话：这个问题背后的机制或学术概念",
  "literal": ["问题里的原词", "最多4个"],
  "bridge": ["作者可能用的具体词或上位词", "2到5个"],
  "concepts": ["概念表键", "0到3个，必须来自给定表"],
  "groups": [
    {"name": "literal", "terms": ["..."], "weight": 0.6},
    {"name": "bridge", "terms": ["..."], "weight": 1.2}
  ],
  "exclude": [],
  "book_hint": ""
}"""

RULES = """规则：
1. 先想清楚提问人真正在问哪个机制。literal 是问题里的口语词，只用来兜底，权重低。
2. bridge 是作者写书时真的会用的词，要具体，不要复述问题，不要写「为什么会…」这种问句。
3. concepts 只能从给定概念表里选。选不出就空着，不要硬凑。
4. 每个词不超过 12 字。literal 和 bridge 各 2 到 5 个。literal 不要放「人」「事」「为什么」这类泛词。
   book_hint 只在书名就是库里目录名时才填，否则留空字符串。
5. 只输出 JSON，不要解释，不要代码围栏。
6. 例：问「公司里的考核为什么让人只盯着指标」，mechanism 是工具理性把人的复杂情况简化成数字；
   literal 是考核、指标；bridge 是非个人化、KPI、简化、可计算；concepts 是工具理性、铁笼。"""


def concept_lines():
    out = []
    for r in csv.DictReader(open(CONCEPT_TSV, encoding="utf-8-sig"), delimiter="\t"):
        aliases = ",".join([a for a in r["aliases"].split(",") if a][:4])
        out.append("%s: %s" % (r["key"], aliases))
    return "\n".join(out)


def prompt(row, mode, table):
    q = row["question"]
    base = "你是中文书库的检索规划员。给一个问题，写出检索计划。\n\n问题：%s\n\n" % q
    if mode == "full":
        base += "可用概念表（键: 别名）：\n%s\n\n" % table
    else:
        base += "这次不给概念表。concepts 必须是空数组，把需要的学术词写进 bridge。\n\n"
    base += RULES + "\n\n输出结构：\n" + SCHEMA
    return base


def parse(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("回包没有 JSON")
    d = json.loads(m.group(0))
    for k in ("literal", "bridge", "concepts", "groups"):
        if k not in d:
            raise ValueError("缺字段 " + k)
    if not isinstance(d["groups"], list) or not d["groups"]:
        raise ValueError("groups 为空")
    return d


def make(row, mode, table):
    path = os.path.join(PLANS, mode, row["qid"] + ".json")
    if os.path.isfile(path):
        return row["qid"], "cached", 0
    txt, usage = be.call(prompt(row, mode, table),
                         argparse.Namespace(provider="opencode", max_out=3000))
    d = parse(txt)
    d["question"] = row["question"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    with open(USAGE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"qid": row["qid"], "mode": mode, "usage": usage},
                            ensure_ascii=False) + "\n")
    return row["qid"], "ok", int(usage.get("input_tokens", 0) or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=["full", "noconcept"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(os.path.join(PLANS, a.mode), exist_ok=True)
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    if a.limit:
        rows = rows[:a.limit]
    table = concept_lines() if a.mode == "full" else ""
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(a.workers) as pool:
        futs = [pool.submit(make, r, a.mode, table) for r in rows]
        for fut in as_completed(futs):
            try:
                qid, status, tok = fut.result()
            except Exception as exc:  # noqa: BLE001
                print("FAIL", exc, file=sys.stderr)
                continue
            done += 1
            print("  %d/%d %s %s %d tok  %.0fs" % (
                done, len(rows), qid, status, tok, time.time() - t0), file=sys.stderr)
    print("plans %d -> %s" % (done, os.path.join(PLANS, a.mode)))


if __name__ == "__main__":
    main()
