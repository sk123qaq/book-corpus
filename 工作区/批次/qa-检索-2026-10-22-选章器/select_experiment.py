#!/usr/bin/env python3
"""选章器对比：token 命中 vs Jev 1.14 判定「前缀有没有点到概念」。

  python3 select_experiment.py --build
  python3 select_experiment.py --run --workers 2
  python3 select_experiment.py --analyze

只读 库/ 与 目录/检索/index.db，产物写本目录。
"""
import argparse
import csv
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
IDX = os.path.join(ROOT, "目录", "检索", "index.db")
CONCEPT_TSV = os.path.join(ROOT, "目录", "概念表.tsv")
TARGETS = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "step1-targets.tsv")
ENV = os.path.expanduser("~/.agents/skills/typesafe-ai/.env")
API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"
SAMPLE = os.path.join(HERE, "sample.tsv")
RAW = os.path.join(HERE, "jev-raw.jsonl")
RESULTS = os.path.join(HERE, "results.tsv")
SUMMARY = os.path.join(HERE, "summary.json")

BROAD = [
    ("自由", "不受强制、可以自己选择的状态"),
    ("权力", "支配他人、决定资源与规则的能力"),
    ("服从", "接受命令、规则或权威的约束"),
    ("孤独", "与他人隔绝、缺少联结的状态"),
    ("正义", "分配、惩罚与制度是否公正"),
    ("秩序", "规则、稳定与可预期的社会安排"),
    ("爱", "亲密、依恋与利他的情感"),
    ("死亡", "生命的终结与必死性"),
    ("身份", "自我认同与社会位置"),
    ("家庭", "亲属关系与共同生活的单位"),
]


def api_key():
    for line in open(ENV, encoding="utf-8"):
        m = re.match(r"\s*(?:export\s+)?TYPESAFE_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
        if m:
            return m.group(1)
    raise SystemExit("找不到 TYPESAFE_API_KEY")


def load_concepts():
    out = {}
    for r in csv.DictReader(open(CONCEPT_TSV, encoding="utf-8-sig"), delimiter="\t"):
        out[r["key"]] = {"name": r["key"],
                         "aliases": [a for a in r["aliases"].split(",") if a],
                         "note": r["note"]}
    return out


def token_hits(text, concepts):
    low = text.lower()
    return [k for k, c in concepts.items()
            if any(t.lower() in low for t in [k] + c["aliases"])]


def prefix_rows():
    con = sqlite3.connect("file:%s?mode=ro" % IDX, uri=True)
    return con.execute("SELECT path, text FROM expand WHERE kind='prefix'").fetchall()


def book_top(con, limit=10):
    out = {}
    for book, key in con.execute(
            "SELECT book, key FROM book_concept ORDER BY book, score DESC"):
        out.setdefault(book, []).append(key)
    return {b: ks[:limit] for b, ks in out.items()}


def candidates_for(text, book, top_map, concepts):
    names = []
    for k in top_map.get(book, []):
        if k in concepts:
            names.append(k)
    names += token_hits(text, concepts)
    names += [b[0] for b in BROAD if b[0] in text]
    if not names:
        names = [b[0] for b in BROAD]
    seen, out = set(), []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        if n in concepts:
            out.append(concepts[n])
        else:
            note = dict(BROAD).get(n, "")
            out.append({"name": n, "aliases": [], "note": note})
    return out[:20]


def build():
    concepts = load_concepts()
    con = sqlite3.connect("file:%s?mode=ro" % IDX, uri=True)
    top_map = book_top(con)
    rows = []
    for path, text in prefix_rows():
        if not path.startswith("库/"):
            continue
        book = path[len("库/"):].split("/md/")[0]
        if not top_map.get(book):
            continue
        hits = token_hits(text, concepts)
        rows.append({"path": path, "book": book, "chapter": os.path.basename(path),
                     "prefix": text, "token_hits": hits})
    rnd = random.Random(20261022)
    hit = [r for r in rows if r["token_hits"]]
    miss = [r for r in rows if not r["token_hits"]]
    hit.sort(key=lambda r: len(r["token_hits"]))
    groups = []
    for n in (1, 2, 99):
        pool = [r for r in hit if (len(r["token_hits"]) == n if n < 99 else len(r["token_hits"]) >= 3)]
        groups += rnd.sample(pool, min(10, len(pool)))
    groups += rnd.sample(miss, min(30, len(miss)))
    picked = {r["path"]: r for r in groups}
    targets = list(csv.DictReader(open(TARGETS, encoding="utf-8"), delimiter="\t"))
    for t in targets:
        r = next((x for x in rows if x["path"] == t["path"]), None)
        if r:
            r["group"] = "target"
            r["qid"] = t["qid"]
            r["terms"] = t["terms"]
            picked[r["path"]] = r
    out = []
    for i, (path, r) in enumerate(sorted(picked.items(), key=lambda kv: kv[1].get("group", ""))):
        r.setdefault("group", "sample")
        cands = candidates_for(r["prefix"], r["book"], top_map, concepts)
        out.append({"i": i, "group": r["group"], "qid": r.get("qid", ""),
                    "terms": r.get("terms", ""), "path": path, "book": r["book"],
                    "chapter": r["chapter"], "prefix": r["prefix"],
                    "token_hits": ",".join(r["token_hits"]),
                    "candidates": json.dumps([c["name"] for c in cands], ensure_ascii=False),
                    "_cands": cands})
    with open(SAMPLE, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["i", "group", "qid", "terms", "path", "book", "chapter", "prefix",
                    "token_hits", "candidates"])
        for r in out:
            w.writerow([r[k] for k in ["i", "group", "qid", "terms", "path", "book",
                                       "chapter", "prefix", "token_hits", "candidates"]])
    print("sample %d 章 -> %s" % (len(out), SAMPLE))
    return out


def ask(state, questions, key, timeout=180):
    body = json.dumps({"model": MODEL, "state": state, "questions": questions},
                      ensure_ascii=False).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, e.read()[:200].decode(errors="replace"))
            if e.code in (429, 500, 502, 503, 504, 529):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
        except Exception as exc:  # noqa: BLE001
            last = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


def run(workers):
    key = api_key()
    with open(SAMPLE, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    for r in rows:
        r["_cands"] = json.loads(r["candidates"])
        # candidates 列只有名字，说明从概念表重建
    concepts = load_concepts()
    done = set()
    if os.path.isfile(RAW):
        for line in open(RAW, encoding="utf-8"):
            if line.strip():
                done.add(json.loads(line)["path"])
    todo = [r for r in rows if r["path"] not in done]
    print("待跑 %d / %d 章" % (len(todo), len(rows)))

    def one(chunk):
        state = [{"书名": r["book"], "章标题": r["chapter"], "前缀": r["prefix"],
                  "候选概念": [{"名称": c, "说明": concepts.get(c, {}).get("note", dict(BROAD).get(c, ""))}
                           for c in r["_cands"]]} for r in chunk]
        qs = {}
        for j, r in enumerate(chunk):
            for k, c in enumerate(r["_cands"]):
                meta = concepts.get(c, {"aliases": [], "note": dict(BROAD).get(c, "")})
                qs["q%d_%d" % (j, k)] = {
                    "type": "noul",
                    "instructions": {"问": "这条前缀是否在讨论这个概念？",
                                     "概念": c,
                                     "说明": meta.get("note", ""),
                                     "别名": meta.get("aliases", [])},
                    "criteria": {
                        "true": "前缀把该概念作为主题、论断或明确的解释对象，"
                                "读者用这个概念词或它的同义说法能从前缀找到对应表述",
                        "false": "前缀没有涉及该概念，或者只是把词当作专名、书名或无关字面出现",
                    },
                }
        resp = ask(state, qs, key)
        return chunk, resp

    out = open(RAW, "a", encoding="utf-8")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, todo[i:i + 10]) for i in range(0, len(todo), 10)]
        for n, fut in enumerate(as_completed(futs), 1):
            chunk, resp = fut.result()
            for j, r in enumerate(chunk):
                ans = {c: resp["answers"]["q%d_%d" % (j, k)]["noul"]
                       for k, c in enumerate(r["_cands"])}
                out.write(json.dumps({"path": r["path"], "group": r["group"],
                                      "qid": r["qid"], "terms": r["terms"],
                                      "answers": ans, "usage": resp.get("usage", {}),
                                      "model": resp.get("model")}, ensure_ascii=False) + "\n")
            out.flush()
            print("  %d/%d 批  %.0fs" % (n, len(futs), time.time() - t0), file=sys.stderr)
    out.close()


def analyze():
    concepts = load_concepts()
    sample = {}
    with open(SAMPLE, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            r["_cands"] = json.loads(r["candidates"]) if r["candidates"].startswith("[") else []
            sample[r["path"]] = r
    jev = {}
    tokens = 0
    for line in open(RAW, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        jev[rec["path"]] = rec
        tokens += int(rec.get("usage", {}).get("input_tokens", 0) or 0)
    rows = []
    for path, r in sample.items():
        rec = jev.get(path)
        if not rec:
            continue
        ans = rec["answers"]
        top = max(ans.items(), key=lambda kv: kv[1]) if ans else ("", 0.0)
        n50 = sum(1 for v in ans.values() if v >= 0.5)
        n70 = sum(1 for v in ans.values() if v >= 0.7)
        n90 = sum(1 for v in ans.values() if v >= 0.9)
        token_gap = 1 if not r["token_hits"] else 0
        rows.append({"path": path, "group": r["group"], "qid": r["qid"],
                     "terms": r["terms"], "token_gap": token_gap,
                     "token_hits": r["token_hits"],
                     "jev_max": round(top[1], 3), "jev_top": top[0],
                     "n50": n50, "n70": n70, "n90": n90, "ans": ans})
    with open(RESULTS, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["path", "group", "qid", "terms", "token_gap", "token_hits",
                    "jev_max", "jev_top", "n50", "n70", "n90"])
        for r in rows:
            w.writerow([r[k] for k in ["path", "group", "qid", "terms", "token_gap",
                                       "token_hits", "jev_max", "jev_top", "n50", "n70", "n90"]])

    def stats(threshold, use_n=False):
        # Jev gap = no concept >= threshold
        jg = [1 if (r["n50"] == 0 if threshold == 0.5 else
                    r["n70"] == 0 if threshold == 0.7 else r["n90"] == 0) else 0 for r in rows]
        tg = [r["token_gap"] for r in rows]
        same = sum(1 for a, b in zip(tg, jg) if a == b)
        both_gap = sum(1 for a, b in zip(tg, jg) if a == 1 and b == 1)
        token_only = sum(1 for a, b in zip(tg, jg) if a == 1 and b == 0)
        jev_only = sum(1 for a, b in zip(tg, jg) if a == 0 and b == 1)
        n = len(rows)
        p0 = (tg.count(1) / n) * (jg.count(1) / n) + (tg.count(0) / n) * (jg.count(0) / n)
        po = same / n
        kappa = (po - p0) / (1 - p0) if p0 != 1 else 1.0
        return {"threshold": threshold, "n": n, "token_gap": sum(tg), "jev_gap": sum(jg),
                "agree": same, "agree_pct": round(100 * po, 1), "kappa": round(kappa, 3),
                "both_gap": both_gap, "token_only_gap": token_only,
                "jev_only_gap": jev_only}

    summary = {"n": len(rows), "tokens": tokens,
               "cost_usd": round(tokens * 0.042 / 1_000_000, 4),
               "stats": [stats(t) for t in (0.5, 0.7, 0.9)]}
    targets = [r for r in rows if r["group"] == "target"]
    summary["targets"] = {
        "n": len(targets),
        "token_gap": sum(r["token_gap"] for r in targets),
        "jev_gap_0.5": sum(1 for r in targets if r["n50"] == 0),
        "jev_gap_0.7": sum(1 for r in targets if r["n70"] == 0),
        "jev_gap_0.9": sum(1 for r in targets if r["n90"] == 0),
        "detail": [{k: r[k] for k in ("qid", "terms", "path", "token_gap", "jev_max",
                                      "jev_top", "n50")} for r in targets],
    }
    with open(SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n分歧样例")
    tg = [r for r in rows if (r["token_gap"] == 1) != (r["n50"] == 0)]
    for r in tg[:12]:
        print("-", r["group"], r["path"])
        print("  prefix:", sample[r["path"]]["prefix"][:100])
        print("  token_hits:", r["token_hits"] or "(none)", "| jev_max:", r["jev_max"], r["jev_top"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if a.build:
        build()
    if a.run:
        run(a.workers)
    if a.analyze:
        analyze()
    if not (a.build or a.run or a.analyze):
        ap.error("要 --build / --run / --analyze")


if __name__ == "__main__":
    main()
