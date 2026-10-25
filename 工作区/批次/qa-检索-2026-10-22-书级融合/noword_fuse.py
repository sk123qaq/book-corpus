#!/usr/bin/env python3
"""无原词 20 题：向量候选 + 现有 qsearch 打分的融合实验。

  noword_fuse.py            # 重建向量章级分数缓存并跑融合
  noword_fuse.py --cached   # 复用 vec_chap_max.npy

比较四档的章级名次：
  lex   qsearch（题目 terms，-n 2000 --pool all）
  vec   bge-m3 全库块级相似度，按章取最大
  rrf   两路名次的 RRF（k=60）
  pool  只看向量前 2000 章，用 qsearch 名次重排
"""
import argparse
import csv
import json
import os
import subprocess
import sys

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-22-书级融合")
GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
OUT = os.path.join(BATCH, "live", "pilot-noword")
MODEL = os.path.join(ROOT, "工作区", "models", "bge-m3")
QSEARCH = os.path.join(ROOT, "工作区", "脚本", "qsearch.py")
PY = os.path.join(ROOT, "工作区", ".venv-pdf", "bin", "python")
TOP = 2000


def build_vec(meta, gold):
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL, dtype=torch.float16).to("cuda").eval()
    task = "为这个句子生成表示以用于检索相关文章："
    enc = tok([task + g["question"] for g in gold], padding=True, truncation=True,
              max_length=128, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        h = model(**enc).last_hidden_state[:, 0]
        Q = torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy()
    npath = len(meta["paths"])
    chap_max = np.full((len(gold), npath), -2.0, dtype=np.float32)
    for si in range(len(meta["shards"])):
        E = np.load(os.path.join(OUT, "shard-%03d.npy" % si)).astype(np.float32)
        S = E @ Q.T
        pids = np.array(meta["shards"][si], dtype=np.int32)[:, 0]
        for qi in range(len(gold)):
            np.maximum.at(chap_max[qi], pids, S[:, qi])
    np.save(os.path.join(OUT, "vec_chap_max.npy"), chap_max)
    return chap_max


def run_lex(gold):
    out = []
    for i, g in enumerate(gold):
        q = " ".join(x for x in g["terms"].split(",") if x)
        p = subprocess.run([PY, QSEARCH, q, "-n", str(TOP), "--pool", "all", "--json"],
                           capture_output=True, text=True, timeout=300)
        try:
            d = json.loads(p.stdout)
        except Exception:
            print("qsearch failed", g["qid"], p.stderr[-200:])
            d = {"hits": []}
        # 章（path）按首次出现的段落名次排序
        rank = {}
        for h in d.get("hits", []):
            rank.setdefault(h["path"], len(rank) + 1)
        out.append(rank)
        print("%s lex chapters %d" % (g["qid"], len(rank)), flush=True)
    json.dump(out, open(os.path.join(OUT, "lex_rank.json"), "w"), ensure_ascii=False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true")
    args = ap.parse_args()
    meta = json.load(open(os.path.join(OUT, "blocks.json")))
    paths = meta["paths"]
    gold = [r for r in csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t")
            if r["category"] == "无原词"]
    pidx = {p: i for i, p in enumerate(paths)}

    f = os.path.join(OUT, "vec_chap_max.npy")
    chap = np.load(f) if (args.cached and os.path.exists(f)) else build_vec(meta, gold)
    print("vec chapter matrix", chap.shape)

    lf = os.path.join(OUT, "lex_rank.json")
    if args.cached and os.path.exists(lf):
        lex = json.load(open(lf))
    else:
        lex = run_lex(gold)

    def rank_of(scores, gold_pid):
        return int(1 + (scores > scores[gold_pid]).sum())

    rows = []
    for qi, g in enumerate(gold):
        gp = pidx.get(g["path"])
        scores = chap[qi]
        vec_order = np.argsort(-scores)[:TOP]
        vec_rank = {int(p): r + 1 for r, p in enumerate(vec_order)}
        lr = {pidx[p]: r for p, r in lex[qi].items() if p in pidx}
        # 四档融合
        cand = set(vec_rank) | set(lr)
        rrf = {p: 1.0 / (60 + vec_rank.get(p, 10 ** 6)) + 1.0 / (60 + lr.get(p, 10 ** 6))
               for p in cand}
        pool = {p: (lr.get(p, 10 ** 6), vec_rank.get(p, 10 ** 6)) for p in vec_rank}
        def rk(d, key=None):
            order = sorted(d, key=key if key else (lambda p: d[p]))
            return {p: i + 1 for i, p in enumerate(order)}
        r_all = rk(rrf, key=lambda p: -rrf[p])
        r_pool = rk(pool)
        rows.append({
            "qid": g["qid"], "question": g["question"],
            "lex": rk(lr).get(gp),
            "vec": rank_of(scores, gp),
            "rrf": r_all.get(gp),
            "pool": r_pool.get(gp),
            "vec_top": vec_rank.get(gp),
            "lex_top": lr.get(gp),
        })
        print("%-5s lex %-7s vec %-7s rrf %-7s pool %-7s %s" % (
            g["qid"], rows[-1]["lex"], rows[-1]["vec"], rows[-1]["rrf"],
            rows[-1]["pool"], g["question"][:24]), flush=True)

    def stat(key, k):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(1 for v in vals if v <= k), len(rows)

    print()
    print("%-6s %8s %8s %8s" % ("", "R@20", "R@100", "R@2000"))
    for key in ("lex", "vec", "rrf", "pool"):
        print("%-6s %8s %8s %8s" % (
            key,
            "%d/%d" % stat(key, 20),
            "%d/%d" % stat(key, 100),
            "%d/%d" % stat(key, 2000)))
    json.dump({"rows": rows,
               "summary": {k: {"r20": stat(k, 20)[0], "r100": stat(k, 100)[0],
                               "r2000": stat(k, 2000)[0]} for k in ("lex", "vec", "rrf", "pool")}},
              open(os.path.join(OUT, "fuse.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
