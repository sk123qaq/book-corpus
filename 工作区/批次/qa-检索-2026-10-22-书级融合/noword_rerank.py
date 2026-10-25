#!/usr/bin/env python3
"""P3 交叉编码器重排：在向量的章级候选上比较「向量排序」与「reranker 排序」。

  noword_rerank.py --build     # 60 题的块分与章级候选
  noword_rerank.py --rerank    # 对候选章的前 8 块跑 bge-reranker-v2-m3
  noword_rerank.py --report    # 章级 R@k、MRR、分档对照

工作点 K=200（2026-10-24 定，见 P3-重排器-实测-2026-10-24.md「K 的选择」）。
历史读数里无原词与近义用的是 K=2000，要复现那批数得显式 --k-no 2000。
候选池来自 bge-m3 全库块嵌入（live/pilot-noword/shard-*.npy）。
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-22-书级融合")
GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
IDX = os.path.join(ROOT, "目录", "检索", "index.db")
OUT = os.path.join(BATCH, "live", "pilot-noword")
MODEL = os.path.join(ROOT, "工作区", "models", "bge-m3")
TASK = "为这个句子生成表示以用于检索相关文章："
K_NO = 200
K_WORD = 200
BLOCKS_PER_CHAP = 8


def load_gold():
    rows = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    return ([r for r in rows if r["category"] == "无原词"],
            [r for r in rows if r["category"] == "原词"],
            [r for r in rows if r["category"] == "近义表达"])


def block_scores(meta, gold, out_path):
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL, dtype=torch.float16).to("cuda").eval()
    enc = tok([TASK + g["question"] for g in gold], padding=True, truncation=True,
              max_length=128, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        h = model(**enc).last_hidden_state[:, 0]
        Q = torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy()
    nblock = sum(len(s) for s in meta["shards"])
    S = np.zeros((nblock, len(gold)), dtype=np.float16)
    off = 0
    for si in range(len(meta["shards"])):
        E = np.load(os.path.join(OUT, "shard-%03d.npy" % si))
        S[off:off + len(E)] = (E.astype(np.float32) @ Q.T).astype(np.float16)
        off += len(E)
    np.save(out_path, S)


def block_index(meta):
    rows = np.concatenate([np.asarray(s, dtype=np.int32) for s in meta["shards"]])
    pid, start, end = rows[:, 0], rows[:, 1], rows[:, 2]
    order = np.argsort(pid, kind="stable")
    by_path = {}
    for r in order:
        by_path.setdefault(int(pid[r]), []).append(int(r))
    for k in by_path:
        by_path[k] = np.asarray(by_path[k], dtype=np.int32)
    return pid, start, end, by_path


def chap_max(S, pid, nchap):
    out = np.full((S.shape[1], nchap), -2.0, dtype=np.float32)
    for qi in range(S.shape[1]):
        np.maximum.at(out[qi], pid, S[:, qi].astype(np.float32))
    return out


def fetch_paras(paths):
    con = sqlite3.connect(IDX)
    texts = {}
    paths = list(paths)
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        q = "SELECT path, line, text FROM para WHERE path IN (%s)" % ",".join("?" * len(chunk))
        for p, line, t in con.execute(q, chunk):
            texts[(p, line)] = t
    con.close()
    return texts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--k-no", type=int, default=K_NO)
    ap.add_argument("--k-word", type=int, default=K_WORD)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    no, word, near = load_gold()
    gold = no + word + near
    if args.limit:
        gold = gold[:args.limit]
        no = [g for g in gold if g["category"] == "无原词"]
        word = [g for g in gold if g["category"] == "原词"]
        near = [g for g in gold if g["category"] == "近义表达"]
    meta = json.load(open(os.path.join(OUT, "blocks.json")))
    paths = meta["paths"]
    pidx = {p: i for i, p in enumerate(paths)}
    cfile = os.path.join(OUT, "chap_score.npy")
    bfile = os.path.join(OUT, "block_score.npy")
    pid, start, end, by_path = block_index(meta)

    if args.build:
        t = time.time()
        block_scores(meta, gold, bfile)
        S = np.load(bfile)
        cm = chap_max(S, pid, len(paths))
        np.save(cfile, cm)
        print("blocks %d, questions %d, build %.0fs" % (S.shape[0], S.shape[1], time.time() - t))
        return

    cm = np.load(cfile)
    S = np.load(bfile)
    cand = {}
    for qi, g in enumerate(gold):
        k = args.k_word if len(no) <= qi < len(no) + len(word) else args.k_no
        top = np.argsort(-cm[qi])[:k]
        cand[g["qid"]] = {"qi": qi, "k": k, "pids": [int(p) for p in top],
                          "gold": pidx[g["path"]]}

    if args.rerank:
        sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
        from rerank import Reranker
        r = Reranker()
        sf = os.path.join(OUT, "rerank_scores.json")
        scores = json.load(open(sf)) if os.path.exists(sf) else {}
        todo = [g for g in gold if g["qid"] not in scores]
        print("rerank %d questions (%d cached)" % (len(todo), len(gold) - len(todo)), flush=True)
        all_paths = set()
        for g in todo:
            all_paths.update(paths[p] for p in cand[g["qid"]]["pids"])
        t = time.time()
        paras = fetch_paras(all_paths)
        print("paras %d for %d paths, %.0fs" % (len(paras), len(all_paths), time.time() - t))

        for qi, g in enumerate(gold):
            if g["qid"] in scores:
                continue
            c = cand[g["qid"]]
            rows, texts = [], []
            for p in c["pids"]:
                br = by_path.get(p)
                if br is None:
                    continue
                blocks = br[np.argsort(-S[br, qi].astype(np.float32))[:BLOCKS_PER_CHAP]]
                for row in blocks:
                    parts = [paras.get((paths[p], ln)) for ln in range(int(start[row]), int(end[row]) + 1)]
                    txt = "".join(x for x in parts if x)
                    if txt.strip():
                        rows.append((p, row))
                        texts.append(txt)
            t0 = time.time()
            sc = r.score(g["question"], texts)
            ms = (time.time() - t0) * 1000
            best = {}
            for (p, row), s in zip(rows, sc):
                best[p] = max(best.get(p, -1e9), s)
            scores[g["qid"]] = {"scores": best, "ms": ms, "pairs": len(texts)}
            print("%-5s %5d pairs %6.0f ms  %s" % (g["qid"], len(texts), ms, g["question"][:22]), flush=True)
        json.dump(scores, open(os.path.join(OUT, "rerank_scores.json"), "w"), ensure_ascii=False)

    if args.report:
        scores = json.load(open(os.path.join(OUT, "rerank_scores.json")))
        rows = []
        for i, g in enumerate(gold):
            c = cand[g["qid"]]
            gp = c["gold"]
            vr = 1 + int((cm[i] > cm[i][gp]).sum())
            d = scores[g["qid"]]
            pooled = {int(k): v for k, v in d["scores"].items()}
            pool_hit = gp in pooled
            if gp in pooled:
                rr = 1 + sum(1 for v in pooled.values() if v > pooled[gp])
            else:
                rr = None
            rows.append({"qid": g["qid"], "cat": g["category"], "question": g["question"],
                         "vec": vr, "pool": pool_hit, "rerank": rr,
                         "ms": d["ms"], "pairs": d["pairs"]})
            print("%-5s %-4s vec %-6d pool %-5s rerank %-6s %s" % (
                g["qid"], g["category"] == "无原词" and "无" or "原",
                vr, pool_hit, rr, g["question"][:22]))

        def stat(idx, key, k):
            v = [rows[i][key] for i in idx if rows[i][key] is not None]
            return sum(1 for x in v if x <= k), len(list(idx))

        print()
        print("%-10s %-8s %-8s %-8s" % ("group", "R@20", "R@100", "MRR20"))
        cuts = (("无原词", range(0, len(no))), ("原词", range(len(no), len(no) + len(word))),
                ("近义", range(len(no) + len(word), len(gold))))
        for name, idx in cuts:
            for key in ("vec", "rerank"):
                vals = [rows[i][key] for i in idx if rows[i][key] is not None]
                mrr = sum(1.0 / x for x in vals if x <= 20) / len(list(idx))
                print("%-10s %-8s %-8s %.3f" % (
                    name + ":" + key,
                    "%d/%d" % stat(idx, key, 20),
                    "%d/%d" % stat(idx, key, 100),
                    mrr))
        ms = [r["ms"] for r in rows]
        print("\nrerank %.0f ms/query median, %.0f ms total, %d pairs" % (
            float(np.median(ms)), sum(ms), sum(r["pairs"] for r in rows)))
        json.dump({"rows": rows}, open(os.path.join(OUT, "rerank_result.json"), "w"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
