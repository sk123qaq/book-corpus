#!/usr/bin/env python3
"""给书做学科分类（一级 + 二级），批量提问，带缓存。

用法:
  python3 classify_books.py --limit 40 --batch 10       # 小样试跑，默认 model 后端
  python3 classify_books.py --batch 12                  # 全量，一次调用回两级
  python3 classify_books.py --backend typesafe --stage 1   # 旧 TypeSafe 两阶段
  python3 classify_books.py --merge                     # 写 分类预填.csv，人账不动
  python3 classify_books.py --merge --add-new           # 再追加人账里没有的 uid
  python3 classify_books.py --report                    # 只看缓存统计

后端 model 走 classify_model.classify，一次调用给一级、二级、置信与理由，不读
TypeSafe。后端 typesafe 保留原来的 /systemone 两阶段接口，--stage 只对它有效。
缓存 目录/分类缓存.jsonl 每行带 backend、model、prompt_version；旧的 TypeSafe 条目
没有 backend 字段，按 typesafe 读，--backend model 不读它们。

--merge 以人账为准：已有行一个字段都不动，只有 --add-new 才在末尾追加新 uid。
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor  # noqa: F401 子模块从这里再导出，本文件不直接用

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_model as CM
import lib_common as L

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TS_PROMPT_VERSION = "typesafe-2026-10-18.1"
ENV = os.environ.get("TYPESAFE_ENV") or os.path.join(L.skill_dir("typesafe-ai"), ".env")
UNCERTAIN = CM.UNCERTAIN
NO_SUB = CM.NO_SUB
TAXONOMY = CM.TAXONOMY
TOP_RUBRIC = CM.TOP_RUBRIC
PREFILL_EXTRA = [
    "预填依据",
    "依据最终",
    "一级TypeSafe",
    "二级TypeSafe",
    "一级置信",
    "二级置信",
    "一级模型",
    "二级模型",
    "模型置信",
    "模型依据",
]


def api_key():
    try:
        with open(ENV, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*(?:export\s+)?TYPESAFE_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
                if m:
                    return m.group(1)
    except FileNotFoundError:
        raise SystemExit("找不到 %s；旧 TypeSafe 后端需要 TYPESAFE_API_KEY 或 TYPESAFE_ENV" % ENV) from None
    raise SystemExit("找不到 TYPESAFE_API_KEY")


def ask(state, questions, key, timeout=90):
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    )
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 端点来自模块内 https 常量，urlopen 不接外部输入
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, e.read()[:200].decode(errors="replace"))
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            raise RuntimeError(last) from e
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2**attempt)
    raise RuntimeError(last)


def book_state(rows):
    return [
        {
            "书名": r["正式书名"],
            "作者": r["作者"],
            "系列": r["系列"],
            "卷": r["卷序"],
            "来源目录": r["src_path"].split("/")[0],
            "奖项": (r["备注"] or "").replace("奖项:", ""),
            "原始文件名": os.path.basename(r["src_path"]),
            "已有md章节数": r["md_chapters"] or "",
        }
        for r in rows
    ]


def run_stage1(batch_rows, key):
    questions = {}
    for i, _ in enumerate(batch_rows):
        questions["q%d" % i] = {
            "type": "choice",
            "instructions": "第 %d 本书属于哪一个一级学科。"
            "依据书名、作者、系列、来源目录与奖项判断，忽略文件名里的宣传语。" % (i + 1),
            "criteria": {**TOP_RUBRIC, UNCERTAIN: "信息不足或横跨多类，无法判断"},
        }
    return ask(book_state(batch_rows), questions, key)


def run_stage2(batch_rows, key, level1):
    questions = {}
    for i, _ in enumerate(batch_rows):
        subs = TAXONOMY.get(level1[i]) or []
        crit = dict.fromkeys(subs)
        crit[NO_SUB] = "只分到一级即可，二级里没有合适的"
        crit[UNCERTAIN] = "信息不足，无法判断二级"
        questions["q%d" % i] = {
            "type": "choice",
            "instructions": "第 %d 本书在「%s」下面属于哪个二级分类。" % (i + 1, level1[i]),
            "criteria": crit,
        }
    return ask(book_state(batch_rows), questions, key)


def load_cache(path):
    got = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                got[(rec["uid"], rec["stage"])] = rec
    return got


def append_cache(records, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def backend_of(rec):
    return rec.get("backend") or "typesafe"


def per_uid(cache, backend):
    """{(uid, stage): rec} → {uid: {stage: rec}}，只留这个后端的条目。"""
    out = {}
    for (uid, stage), rec in cache.items():
        if backend_of(rec) != backend:
            continue
        out.setdefault(uid, {})[stage] = rec
    return out


def verdict(recs, backend):
    """缓存里一本的建议，没有返回 None。一级未过阈值也照样给出，阈值由调用方判。"""
    if backend == "model":
        m1 = recs.get("model")
        if not m1:
            return None
        conf = float(m1.get("confidence") or 0)
        return {
            "level1": m1.get("level1") or "",
            "level2": m1.get("level2") or "",
            "conf1": conf,
            "conf2": conf,
            "reason": m1.get("reason") or "",
        }
    r1, r2 = recs.get(1), recs.get(2)
    if not r1:
        return None
    l2 = (r2.get("choice") or "") if r2 else ""
    return {
        "level1": r1.get("choice") or "",
        "level2": "" if l2 in (UNCERTAIN, NO_SUB) else l2,
        "conf1": float(r1.get("confidence") or 0),
        "conf2": float(r2.get("confidence") or 0) if r2 else 0.0,
        "reason": "",
    }


def annotate(rows, cache, backend, threshold):
    """给清单每行写模型建议四列与最终建议列，返回统计。

    模型四列永远取 model 后端的条目；一级TypeSafe/依据最终等列取 backend 的选择。
    """
    model_per, dec_per = per_uid(cache, "model"), per_uid(cache, backend)
    stats = {"level1": Counter(), "series": 0, "model": 0, "low": []}
    for r in rows:
        mv = verdict(model_per.get(r["uid"], {}), "model")
        r["一级模型"] = mv["level1"] if mv else ""
        r["二级模型"] = mv["level2"] if mv else ""
        r["模型置信"] = mv["conf1"] if mv else ""
        r["模型依据"] = mv["reason"] if mv else ""
        v = verdict(dec_per.get(r["uid"], {}), backend)
        basis = r.get("预填依据") or ""
        explicit = basis in ("奖项", "来源目录")
        hit = bool(v) and v["level1"] not in (UNCERTAIN, "") and v["conf1"] >= threshold
        if explicit:
            l1, src = r.get("一级") or "", "来源目录或奖项"
        elif basis.startswith("系列:"):
            l1, src = r.get("一级") or "", basis
            stats["series"] += 1
        elif hit:
            l1, src = v["level1"], "%s(%.2f)" % (backend, v["conf1"])
            stats["model"] += 1
        else:
            l1, src = r.get("一级") or "", basis
        l2 = v["level2"] if (v and hit and l1 == v["level1"]) else ""
        r["一级TypeSafe"], r["二级TypeSafe"] = l1, l2
        r["依据最终"] = src
        r["一级置信"] = v["conf1"] if v else ""
        r["二级置信"] = v["conf2"] if (v and l2) else ""
        stats["level1"][l1] += 1
        if l1 == "其他" or (v and not l2 and l1 != "其他"):
            stats["low"].append(r["uid"])
    return stats


def merge_ledger(rows, ledger_old, add_new=False):
    """人账为准：已有行原样保留，--add-new 只在末尾追加人账没有的 uid。

    add_new=True 时一级二级取模型结果（一级模型/二级模型，退到旧列），备注写
    auto:classify；待复核，留给人工复核。返回 (人账行, 未入账 uid, 新增 uid)。
    """
    have = {x["uid"] for x in ledger_old}
    ledger = [{k: x.get(k, "") for k in L.LEDGER_HEADER} for x in ledger_old]
    ignored, added = [], []
    for r in rows:
        uid = r.get("uid") or ""
        if not uid or uid in have:
            continue
        l1 = r.get("一级模型") or r.get("一级TypeSafe") or r.get("一级") or ""
        l2 = r.get("二级模型") or r.get("二级TypeSafe") or ""
        if not add_new or l1 in ("", UNCERTAIN):
            ignored.append(uid)
            continue
        ledger.append(
            {
                "uid": uid,
                "一级": l1,
                "二级": l2,
                "作者层": "0",
                "保留": "keep",
                "正式书名": r.get("正式书名") or "",
                "备注": "auto:classify；待复核",
            }
        )
        added.append(uid)
    return ledger, ignored, added


def run_typesafe(rows, args, cache, cache_path):
    todo = [r for r in rows if args.refresh or (r["uid"], args.stage) not in cache]
    if args.uids:
        want = set(args.uids.split(","))
        todo = [r for r in todo if r["uid"] in want]
    if args.stage == 2 and not args.include_other:
        todo = [r for r in todo if (cache.get((r["uid"], 1), {}).get("choice", r["一级"])) not in (UNCERTAIN, "其他")]
    if args.limit:
        random.Random(args.seed).shuffle(todo)
        todo = todo[: args.limit]
    print("typesafe stage %d 待处理 %d 条" % (args.stage, len(todo)))
    key = api_key()
    batches = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
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
            out.append(
                {
                    "uid": r["uid"],
                    "stage": args.stage,
                    "backend": "typesafe",
                    "model": MODEL,
                    "prompt_version": TS_PROMPT_VERSION,
                    "choice": a.get("choice", ""),
                    "confidence": a.get("confidence", 0.0),
                    "probabilities": a.get("probabilities", {}),
                    "level1": level1[i] if args.stage == 2 else "",
                    "title": r["正式书名"],
                }
            )
        append_cache(out, cache_path)
        for o in out:
            cache[(o["uid"], o["stage"])] = o
        done += len(b)
        print(
            "  已处理 %d/%d（本批 %s，用量 in %s out %s）"
            % (
                done,
                len(todo),
                resp.get("model"),
                resp.get("usage", {}).get("input_tokens"),
                resp.get("usage", {}).get("output_tokens"),
            ),
            flush=True,
        )
    return cache


def run_model(rows, args, cache, cache_path):
    todo = [r for r in rows if args.refresh or (r["uid"], "model") not in cache]
    if args.uids:
        want = set(args.uids.split(","))
        todo = [r for r in todo if r["uid"] in want]
    if args.limit:
        random.Random(args.seed).shuffle(todo)
        todo = todo[: args.limit]
    print("model 待处理 %d 条（模型 %s，一次给两级）" % (len(todo), args.model))
    size = max(1, args.batch)
    failed = 0
    for i in range(0, len(todo), size):
        b = todo[i : i + size]
        try:
            got = CM.classify([r["正式书名"] for r in b], [r.get("作者", "") for r in b], args.model, args.timeout)
        except CM.ClassifyError as exc:
            failed += 1
            print("  分类失败（%s）：%s" % (",".join(r["uid"] for r in b), exc), file=sys.stderr)
            continue
        out = [
            {
                "uid": r["uid"],
                "stage": "model",
                "backend": "model",
                "model": args.model,
                "prompt_version": CM.PROMPT_VERSION,
                "level1": g["level1"],
                "level2": g["level2"],
                "confidence": g["confidence"],
                "reason": g["reason"],
                "title": r["正式书名"],
            }
            for r, g in zip(b, got, strict=False)
        ]
        append_cache(out, cache_path)
        for o in out:
            cache[(o["uid"], "model")] = o
        print("  已处理 %d/%d" % (min(i + size, len(todo)), len(todo)), flush=True)
    if failed:
        print("有 %d 批分类失败，重跑会跳过已缓存的" % failed, file=sys.stderr)
        return cache, 1
    return cache, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=("model", "typesafe"), default="model")
    ap.add_argument("--root", default=L.ROOT, help="库根，测试指到临时目录")
    ap.add_argument("--stage", type=int, choices=(1, 2), help="只对 typesafe 后端有效")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--uids", default="", help="只处理这些 uid，逗号分隔")
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--timeout", type=int, default=CM.TIMEOUT, help="model 后端单次调用上限秒")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--add-new", action="store_true", help="把清单里人账没有的 uid 追加进人账（默认跳过）")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.45)
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重问")
    ap.add_argument("--cache", help="缓存文件，默认 tables/分类缓存.jsonl")
    ap.add_argument("--include-other", action="store_true", help="一级为其他也问二级")
    args = ap.parse_args()
    args.model = os.environ.get("CLASSIFY_BOOKS_MODEL") or CM.configured()[0]
    if args.stage and args.backend == "model":
        print("--stage 只对 --backend typesafe 有效；model 后端一次就回两级", file=sys.stderr)
        return 2

    cat = os.path.join(os.path.abspath(args.root), "tables")
    cache_path = args.cache or os.path.join(cat, "分类缓存.jsonl")
    rows = L.read_csv(os.path.join(cat, "书目录清单.csv"))
    cache = load_cache(cache_path)
    rc = 0

    if args.stage:
        cache = run_typesafe(rows, args, cache, cache_path)
    elif not (args.merge or args.report):
        cache, rc = run_model(rows, args, cache, cache_path)

    if args.merge or args.report:
        cache = load_cache(cache_path)
        counts = Counter(backend_of(rec) for rec in cache.values())
        print("缓存条目按后端：%s" % dict(counts))
        for be in ("model", "typesafe"):
            if not counts.get(be):
                continue
            per = per_uid(cache, be)
            hit = 0
            for r in rows:
                v = verdict(per.get(r["uid"], {}), be)
                if v and v["level1"] not in (UNCERTAIN, "") and v["conf1"] >= args.threshold:
                    hit += 1
            print("[%s] 有缓存 %d 条，过阈值 %d 条" % (be, len([r for r in rows if r["uid"] in per]), hit))
        stats = annotate(rows, cache, args.backend, args.threshold)
        print("[%s] 一级分布: %s" % (args.backend, dict(stats["level1"].most_common())))
        print("其中系列表直接定的 %d 条，%s 后端达阈值的 %d 条" % (stats["series"], args.backend, stats["model"]))
        print("进入 其他 或二级缺失的记录 %d 条" % len(stats["low"]))
        by_conf = Counter()
        for uid in {r["uid"] for r in rows}:
            v = verdict(per_uid(cache, args.backend).get(uid, {}), args.backend)
            if v:
                by_conf[round(v["conf1"], 1)] += 1
        print("[%s] 一级置信度分布: %s" % (args.backend, dict(sorted(by_conf.items()))))
        if args.merge:
            L.write_csv(os.path.join(cat, "分类预填.csv"), rows, L.LEDGER_HEADER + PREFILL_EXTRA)
            if not L._ensure_ledger(root=args.root, create=True):
                raise SystemExit("tables/ledger.db 建不起来，merge 停")
            ledger_old = L.categories(root=args.root)
            ledger, ignored, added = merge_ledger(rows, ledger_old, args.add_new)
            if added:
                L.category_put_many([r for r in ledger if r.get("uid") in set(added)], "classify", root=args.root)
            print(
                "已写 分类预填.csv；人账 %d 行%s"
                % (
                    len(ledger_old) + len(added),
                    "，新增 %d 本" % len(added) if added else "，一个字段没动（加 --add-new 才追加）",
                )
            )
            if added:
                print("  新增 uid: " + " ".join(added[:10]))
            if ignored:
                print("  未入账 uid: " + " ".join(ignored[:10]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
