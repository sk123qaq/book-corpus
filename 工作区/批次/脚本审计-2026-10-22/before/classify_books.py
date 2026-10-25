#!/usr/bin/env python3
"""用 TypeSafe Jev 给书做学科分类（一级 + 二级），两阶段，批量提问，带缓存。

用法:
  python3 classify_books.py --stage 1 --limit 40 --batch 10     # 小样试跑
  python3 classify_books.py --stage 1 --concurrency 4           # 全量一级
  python3 classify_books.py --stage 2 --concurrency 4           # 全量二级
  python3 classify_books.py --merge                             # 合并到 分类预填.csv 与 分类表.csv
  python3 classify_books.py --report                            # 只看缓存统计
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
CACHE = os.path.join(L.CAT, "分类缓存.jsonl")
ENV = os.path.expanduser("~/.agents/skills/typesafe-ai/.env")
UNCERTAIN = "拿不准"
NO_SUB = "不细分"

TAXONOMY = {
    "文学": ("严肃文学小说", "类型小说", "网络文学", "诗歌", "散文随笔", "戏剧", "儿童与青少年文学"),
    "人文社科": ("历史", "思想与哲学", "宗教与神话", "政治与法律", "社会与经济",
             "人类学与考古", "心理与认知", "语言与文字"),
    "商业与管理": ("战略", "管理与组织", "投资与金融", "创新与创业", "营销与品牌"),
    "科学技术": ("数学与统计", "物理与天文", "生命与医学", "计算机与技术", "工程与制造", "科普"),
    "建筑与古迹": ("中国古建筑", "宗教建筑", "城市与聚落", "建筑史与理论", "地方文物"),
    "艺术与图像": ("美术史", "佛教美术", "绘画与概念设计", "摄影", "设计", "书法篆刻"),
    "游戏": ("游戏设计", "开发与工程", "设定集与美术集", "攻略与资料", "游戏研究"),
    "影视动画漫画": ("漫画与画集", "动画与动画电影", "电影与影史", "编剧与制作"),
    "写作与编剧": ("编剧理论与对白", "写作方法", "叙事与故事结构"),
    "生活与实用": ("饮食", "健康与身体", "两性与情色", "求职与职业", "手工与家居"),
    "其他": ("待归类", "图册与写真", "杂志与工具书"),
}
TOP_RUBRIC = {
    "文学": "小说、诗、散文、戏剧等文学作品，含推理科幻奇幻网络小说、诺奖等文学奖获奖作品",
    "人文社科": "历史、哲学、宗教、政治、法律、社会、经济、人类学、考古、心理、语言",
    "商业与管理": "企业战略、管理、投资理财、金融、创业创新、营销品牌，不含公告等单页文件",
    "科学技术": "数学、物理、天文、生物、医学、计算机、工程、科普读物",
    "建筑与古迹": "建筑史、古建筑、寺塔祠庙、城市与聚落规划、文物保护",
    "艺术与图像": "美术史、绘画与概念设计、佛教美术、书法、设计；不含写真集与杂志",
    "游戏": "游戏设计、开发、引擎、设定集、美术集、攻略、游戏研究",
    "影视动画漫画": "电影、动画、漫画、影史、制作；不含真人写真集",
    "写作与编剧": "编剧理论、对白、写作方法、叙事结构",
    "生活与实用": "饮食、健康、身体、两性与情色、职业、家居手工",
    "其他": "写真集与成人向图册、摄影写真、杂志、财报公告与培训资料等非书文档、来源不明或不属于以上任何一类",
}


def api_key():
    with open(ENV, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"\s*(?:export\s+)?TYPESAFE_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                return m.group(1)
    raise SystemExit("找不到 TYPESAFE_API_KEY")


def ask(state, questions, key, timeout=90):
    body = json.dumps({"state": state, "model": MODEL, "questions": questions},
                      ensure_ascii=False).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, e.read()[:200].decode(errors="replace"))
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def book_state(rows):
    return [{"书名": r["正式书名"], "作者": r["作者"], "系列": r["系列"], "卷": r["卷序"],
             "来源目录": r["src_path"].split("/")[0],
             "奖项": (r["备注"] or "").replace("奖项:", ""),
             "原始文件名": os.path.basename(r["src_path"]),
             "已有md章节数": r["md_chapters"] or ""} for r in rows]


def run_stage1(batch_rows, key):
    questions = {}
    for i, _ in enumerate(batch_rows):
        questions["q%d" % i] = {"type": "choice",
                                "instructions": "第 %d 本书属于哪一个一级学科。依据书名、作者、系列、来源目录与奖项判断，忽略文件名里的宣传语。" % (i + 1),
                                "criteria": {**TOP_RUBRIC, UNCERTAIN: "信息不足或横跨多类，无法判断"}}
    return ask(book_state(batch_rows), questions, key)


def run_stage2(batch_rows, key, level1):
    questions = {}
    for i, _ in enumerate(batch_rows):
        subs = TAXONOMY[level1[i]]
        crit = {s: None for s in subs}
        crit[NO_SUB] = "只分到一级即可，二级里没有合适的"
        crit[UNCERTAIN] = "信息不足，无法判断二级"
        questions["q%d" % i] = {"type": "choice",
                                "instructions": "第 %d 本书在「%s」下面属于哪个二级分类。" % (i + 1, level1[i]),
                                "criteria": crit}
    return ask(book_state(batch_rows), questions, key)


def load_cache(path=CACHE):
    got = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            got[(rec["uid"], rec["stage"])] = rec
    return got


def append_cache(records, path=CACHE):
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=(1, 2))
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--uids", default="", help="只处理这些 uid，逗号分隔")
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.45)
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重问")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--include-other", action="store_true", help="一级为其他也问二级")
    args = ap.parse_args()

    rows = L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))
    cache = load_cache(args.cache)

    if args.report or args.merge:
        pass
    if args.stage:
        todo = [r for r in rows if (r["uid"], args.stage) not in cache]
        if args.uids and args.refresh:
            want = set(args.uids.split(","))
            todo = [r for r in rows if r["uid"] in want]
            todo = todo if args.stage == 1 else [r for r in todo if r["uid"] in want]
        if args.uids:
            want = set(args.uids.split(","))
            todo = [r for r in todo if r["uid"] in want]
        if args.stage == 2 and not args.include_other:
            todo = [r for r in todo if
                    (cache.get((r["uid"], 1), {}).get("choice", r["一级"])) not in (UNCERTAIN, "其他")]
        if args.limit:
            random.Random(args.seed).shuffle(todo)
            todo = todo[:args.limit]
        print("stage %d 待处理 %d 条" % (args.stage, len(todo)))
        key = api_key()
        batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
        done = 0
        for b in batches:
            if args.stage == 1:
                resp = run_stage1(b, key)
                level1 = [None] * len(b)
            else:
                level1 = [cache.get((r["uid"], 1), {}).get("choice") or r["一级"] for r in b]
                resp = run_stage2(b, key, level1)
            out = []
            for i, r in enumerate(b):
                a = resp["answers"].get("q%d" % i) or {}
                out.append({"uid": r["uid"], "stage": args.stage, "choice": a.get("choice", ""),
                            "confidence": a.get("confidence", 0.0),
                            "probabilities": a.get("probabilities", {}),
                            "level1": level1[i] if args.stage == 2 else "",
                            "title": r["正式书名"]})
            append_cache(out, args.cache)
            for o in out:
                cache[(o["uid"], o["stage"])] = o
            done += len(b)
            print("  已处理 %d/%d（本批 %s，用量 in %s out %s）"
                  % (done, len(todo), resp.get("model"),
                     resp.get("usage", {}).get("input_tokens"), resp.get("usage", {}).get("output_tokens")),
                  flush=True)

    if args.merge or args.report:
        cache = load_cache(args.cache)
        per = defaultdict(dict)
        for (uid, stage), rec in cache.items():
            per[uid][stage] = rec
        level1 = Counter()
        low = []
        series_hit = 0
        for r in rows:
            rec = per.get(r["uid"], {}).get(1)
            # 优先级: 来源目录与奖项 > 系列表 > TypeSafe(达阈值) > 关键词预填
            basis = r["预填依据"]
            explicit = basis in ("奖项", "来源目录")
            series = basis.startswith("系列:")
            hit = None
            if rec and rec["choice"] != UNCERTAIN and rec["confidence"] >= args.threshold:
                hit = rec["choice"], rec["confidence"]
            conf = round(rec["confidence"], 3) if rec else ""
            if explicit:
                l1, src = r["一级"], "来源目录或奖项"
            elif series:
                l1, src = r["一级"], basis
                series_hit += 1
            elif hit:
                l1, src = hit[0], "TypeSafe(%.2f)" % hit[1]
            else:
                l1, src = r["一级"], basis
            l2 = ""
            r2 = per.get(r["uid"], {}).get(2)
            if r2 and r2["choice"] not in (UNCERTAIN, NO_SUB) and \
                    r2.get("confidence", 0) >= args.threshold:
                l2 = r2["choice"]
            r["一级TypeSafe"], r["二级TypeSafe"] = l1, l2
            r["依据最终"] = src
            r["一级置信"], r["二级置信"] = conf, round(r2.get("confidence", 0), 3) if r2 else ""
            level1[l1] += 1
            if l1 == "其他" or (r2 and not l2 and l1 != "其他"):
                low.append(r["uid"])
        print("一级分布:", dict(level1.most_common()))
        print("其中系列表直接定的 %d 条，TypeSafe 达到阈值的记录 %d 条"
              % (series_hit, len([1 for r in rows if (r.get("依据最终") or "").startswith("TypeSafe")])))
        print("进入 其他 或二级缺失的记录 %d 条" % len(low))
        by_conf = Counter()
        for r in rows:
            rec = per.get(r["uid"], {}).get(1)
            if rec:
                by_conf[round(rec["confidence"], 1)] += 1
        print("一级置信度分布:", dict(sorted(by_conf.items())))
        if args.merge:
            L.write_csv(os.path.join(L.CAT, "分类预填.csv"), rows,
                        L.LEDGER_HEADER + ["预填依据", "依据最终", "一级TypeSafe", "二级TypeSafe",
                                           "一级置信", "二级置信"])
            ledger = [{"uid": r["uid"],
                       "一级": r.get("一级TypeSafe") or r["一级"],
                       "二级": r.get("二级TypeSafe") or "",
                       "作者层": r["作者层"], "保留": "keep", "正式书名": r["正式书名"],
                       "备注": "；".join(x for x in (r["备注"], "依据:" + (r.get("依据最终") or ""),
                                                   ("置信:" + str(r.get("一级置信"))) if r.get("一级置信") != "" else ""
                                                   ) if x)}
                      for r in rows]
            L.write_csv(os.path.join(L.CAT, "分类表.csv"), ledger, L.LEDGER_HEADER)
            print("已写 分类表.csv 与 分类预填.csv")


if __name__ == "__main__":
    main()
