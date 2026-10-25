#!/usr/bin/env python3
"""无原词 20 题的全库向量检索实验（bge-m3）。

块规则与方案一致：同章内按段累积到 500 字切一块。
全库 1531 本，分片缓存，断了可以续跑。

  noword_embed.py --build     # 建块元数据并报告规模
  noword_embed.py --embed     # 分片嵌入（可续跑）
  noword_embed.py --eval      # 20 题排名，块级与章级
"""
import argparse
import csv
import json
import os
import sqlite3
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
SHARD_BOOKS = 40


def build_blocks():
    con = sqlite3.connect(IDX)
    cur = con.execute("SELECT book, path, line, text FROM para ORDER BY book, path, line")
    blocks = []
    key = None
    acc, start, end = [], None, None

    def flush():
        if acc:
            blocks.append({"book": key[0], "path": key[1], "start": start,
                           "end": end, "text": "".join(acc)})

    for book, path, line, text in cur:
        if (book, path) != key:
            flush()
            key = (book, path)
            acc, start, end = [], line, line
        if not acc:
            start = line
        acc.append(text if text.endswith("\n") else text + "\n")
        end = line
        if sum(len(x) for x in acc) >= 500:
            flush()
            acc, start = [], None
    flush()
    return blocks


def shards(blocks):
    out, cur, last, nbooks = [], [], None, 0
    for b in blocks:
        if b["book"] != last:
            if nbooks >= SHARD_BOOKS:
                out.append(cur)
                cur, nbooks = [], 0
            nbooks += 1
            last = b["book"]
        cur.append(b)
    if cur:
        out.append(cur)
    return out


def embed(texts, tok, model, batch=128, max_len=512):
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out = np.zeros((len(texts), 1024), dtype=np.float16)
    for i in range(0, len(order), batch):
        idx = order[i:i + batch]
        enc = tok([texts[j] for j in idx], padding=True, truncation=True,
                  max_length=max_len, return_tensors="pt").to("cuda")
        with torch.inference_mode():
            h = model(**enc).last_hidden_state[:, 0]
            v = torch.nn.functional.normalize(h.float(), dim=-1)
        out[idx] = v.cpu().numpy().astype(np.float16)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--task", default="")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    meta_path = os.path.join(OUT, "blocks.json")
    gold = [r for r in csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t")
            if r["category"] == "无原词"]
    print("questions", len(gold))

    t0 = time.time()
    blocks = build_blocks()
    print("blocks %d, %.1fM chars, build %.0fs" % (
        len(blocks), sum(len(b["text"]) for b in blocks) / 1e6, time.time() - t0))

    paths = sorted({b["path"] for b in blocks})
    pidx = {p: i for i, p in enumerate(paths)}
    for b in blocks:
        b["pid"] = pidx[b["path"]]

    # gold block per question: evidence 逐字找，找不到按行号最近
    gold_ids = []
    for g in gold:
        ev = g.get("evidence") or ""
        gi = None
        if ev:
            for i, b in enumerate(blocks):
                if b["pid"] == pidx.get(g["path"], -1) and ev in b["text"]:
                    gi = i
                    break
        if gi is None:
            line = int(g["line"])
            best, bd = None, 10 ** 9
            for i, b in enumerate(blocks):
                if b["pid"] != pidx.get(g["path"], -1):
                    continue
                d = 0 if b["start"] <= line <= b["end"] else min(
                    abs(line - b["start"]), abs(line - b["end"]))
                if d < bd:
                    best, bd = i, d
            gi = best
        gold_ids.append(gi)
        if gi is None:
            print("UNMAPPED", g["qid"])
    print("gold mapped", sum(1 for x in gold_ids if x is not None), "/", len(gold))

    sh = shards(blocks)
    print("shards", len(sh), [len(x) for x in sh][:5], "...")
    if args.build:
        json.dump({"paths": paths,
                   "shards": [[[b["pid"], b["start"], b["end"]] for b in part] for part in sh],
                   "gold_ids": gold_ids},
                  open(meta_path, "w"), ensure_ascii=False)
        print("meta written")
        return

    if args.embed:
        tok = AutoTokenizer.from_pretrained(MODEL)
        model = AutoModel.from_pretrained(MODEL, dtype=torch.float16).to("cuda").eval()
        done = 0
        for si, part in enumerate(sh):
            f = os.path.join(OUT, "shard-%03d.npy" % si)
            if os.path.exists(f):
                done += 1
                continue
            t = time.time()
            E = embed([b["text"] for b in part], tok, model)
            np.save(f, E)
            done += 1
            print("shard %d/%d %d blocks %.0fs (%.0f blocks/s) total %.0f%%" % (
                si + 1, len(sh), len(part), time.time() - t, len(part) / (time.time() - t),
                100.0 * done / len(sh)), flush=True)
        print("embed done")

    if args.eval:
        meta = json.load(open(meta_path))
        tok = AutoTokenizer.from_pretrained(MODEL)
        model = AutoModel.from_pretrained(MODEL, dtype=torch.float16).to("cuda").eval()
        qs = [((args.task + g["question"]) if args.task else g["question"]) for g in gold]
        enc = tok(qs, padding=True, truncation=True, max_length=128,
                  return_tensors="pt").to("cuda")
        with torch.inference_mode():
            h = model(**enc).last_hidden_state[:, 0]
            Q = torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy()
        paths = meta["paths"]
        nq, npath = len(gold), len(paths)
        gold_ids = meta["gold_ids"]
        books = sorted({p.split("/md/")[0] for p in paths})
        bidx = {b: i for i, b in enumerate(books)}
        path_book = np.array([bidx[p.split("/md/")[0]] for p in paths], dtype=np.int32)
        chap_max = np.full((nq, npath), -2.0, dtype=np.float32)
        gold_sim = np.zeros(nq, dtype=np.float32)
        gold_pid = np.full(nq, -1, dtype=np.int32)
        bases = np.cumsum([0] + [len(x) for x in meta["shards"]])
        for si in range(len(meta["shards"])):
            E = np.load(os.path.join(OUT, "shard-%03d.npy" % si)).astype(np.float32)
            S = E @ Q.T
            parts = np.array(meta["shards"][si], dtype=np.int32)
            pids = parts[:, 0]
            base = bases[si]
            for qi, gi in enumerate(gold_ids):
                if gi is None:
                    continue
                if base <= gi < base + len(parts):
                    gold_sim[qi] = S[gi - base, qi]
                    gold_pid[qi] = pids[gi - base]
                np.maximum.at(chap_max[qi], pids, S[:, qi])
        book_max = np.full((nq, len(books)), -2.0, dtype=np.float32)
        for qi in range(nq):
            np.maximum.at(book_max[qi], path_book, chap_max[qi])
        block_rank = np.ones(nq, dtype=np.int64)
        for si in range(len(meta["shards"])):
            E = np.load(os.path.join(OUT, "shard-%03d.npy" % si)).astype(np.float32)
            S = E @ Q.T
            for qi, gi in enumerate(gold_ids):
                if gi is not None:
                    block_rank[qi] += int((S[:, qi] > gold_sim[qi]).sum())
        rows = []
        for qi, g in enumerate(gold):
            if gold_pid[qi] < 0:
                rows.append({"qid": g["qid"], "question": g["question"],
                             "block_rank": None, "chap_rank": None, "book_rank": None})
                continue
            cg = chap_max[qi, gold_pid[qi]]
            bg = book_max[qi, path_book[gold_pid[qi]]]
            rows.append({
                "qid": g["qid"], "question": g["question"],
                "block_rank": int(block_rank[qi]),
                "chap_rank": int(1 + (chap_max[qi] > cg).sum()),
                "book_rank": int(1 + (book_max[qi] > bg).sum())})

        def rate(key, k):
            vals = [r[key] for r in rows if r[key] is not None]
            return sum(1 for v in vals if v <= k) / len(vals)

        def mrr(key):
            vals = [r[key] for r in rows if r[key] is not None]
            return sum(1 / v if v <= 20 else 0 for v in vals) / len(vals)

        summary = {"n": len(rows),
                   "block_r1": rate("block_rank", 1), "block_r5": rate("block_rank", 5),
                   "block_r20": rate("block_rank", 20), "block_mrr20": mrr("block_rank"),
                   "chap_r1": rate("chap_rank", 1), "chap_r5": rate("chap_rank", 5),
                   "chap_r20": rate("chap_rank", 20), "chap_mrr20": mrr("chap_rank"),
                   "book_r1": rate("book_rank", 1), "book_r5": rate("book_rank", 5)}
        json.dump({"summary": summary, "rows": rows},
                  open(os.path.join(OUT, "result.json"), "w"), ensure_ascii=False, indent=1)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        for r in rows:
            print("%-5s blk %-7s chap %-7s book %-4s %s" % (
                r["qid"], r["block_rank"], r["chap_rank"], r["book_rank"],
                r["question"][:28]))


if __name__ == "__main__":
    main()
