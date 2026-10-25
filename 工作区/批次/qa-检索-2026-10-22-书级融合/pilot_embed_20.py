#!/usr/bin/env python3
"""20 本书上的 embedding 对比试点。

  pilot_embed_20.py --model qwen|bge [--batch 32] [--max-len 1024]

块规则与方案一致：同一章内按段累积到 500 字就切一块。
指标：金标块在全部块里的名次（recall@20、MRR@20、top1），
以及书级名次（每本书取最高分块）。词法基线用问题自带的 terms 做组覆盖度。
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
GOLD = os.path.join(BATCH, "book_gold-heldout.tsv")
IDX = os.path.join(ROOT, "目录", "检索", "index.db")
OUT = os.path.join(BATCH, "live", "pilot-embed")
MODELS = {
    "qwen": ("工作区/models/Qwen3-Embedding-0.6B", "last"),
    "bge": ("工作区/models/bge-m3", "cls"),
}
TASK = "Given a web search query, retrieve relevant passages that answer the query"


def read_gold():
    with open(GOLD, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_blocks(books):
    con = sqlite3.connect(IDX)
    cur = con.execute(
        "SELECT book, path, line, chars, text FROM para ORDER BY book, path, line")
    blocks = []
    key = None
    acc, start, end = [], None, None
    for book, path, line, chars, text in cur:
        if book not in books:
            continue
        if (book, path) != key:
            if acc:
                blocks.append({"book": key[0], "path": key[1], "start": start,
                               "end": end, "text": "".join(acc)})
            key = (book, path)
            acc, start, end = [], line, line
        if not acc:
            start = line
        acc.append(text if text.endswith("\n") else text + "\n")
        end = line
        if sum(len(x) for x in acc) >= 500:
            blocks.append({"book": book, "path": path, "start": start, "end": end,
                           "text": "".join(acc)})
            acc, start = [], None
    if acc and key:
        blocks.append({"book": key[0], "path": key[1], "start": start,
                       "end": end, "text": "".join(acc)})
    return blocks


def load_model(tag):
    rel, pool = MODELS[tag]
    path = os.path.join(ROOT, rel)
    tok = AutoTokenizer.from_pretrained(path)
    if pool == "last":
        tok.padding_side = "left"
    model = AutoModel.from_pretrained(path, dtype=torch.float16)
    model.eval().to("cuda")
    return tok, model, pool


@torch.inference_mode()
def embed(texts, tok, model, pool, batch=32, max_len=512):
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    vecs = [None] * len(texts)
    for i in range(0, len(order), batch):
        idx = order[i:i + batch]
        enc = tok([texts[j] for j in idx], padding=True, truncation=True,
                  max_length=max_len, return_tensors="pt").to("cuda")
        h = model(**enc).last_hidden_state
        v = h[:, -1] if pool == "last" else h[:, 0]
        v = torch.nn.functional.normalize(v.float(), dim=-1).cpu().numpy()
        for k, j in enumerate(idx):
            vecs[j] = v[k]
    return np.vstack(vecs)


def gold_index(blocks, g):
    """先按 evidence 逐字找块，找不到再按行号找最近的块。"""
    ev = g.get("evidence") or ""
    if ev:
        for i, b in enumerate(blocks):
            if b["path"] == g["path"] and ev in b["text"]:
                return i
    line = int(g["line"])
    best, bestd = None, 10 ** 9
    for i, b in enumerate(blocks):
        if b["path"] != g["path"]:
            continue
        d = 0 if b["start"] <= line <= b["end"] else min(abs(line - b["start"]), abs(line - b["end"]))
        if d < bestd:
            best, bestd = i, d
    return best


def ranks(sims, gold_idx):
    order = np.argsort(-sims)
    pos = int(np.where(order == gold_idx)[0][0])
    return pos + 1, order


def lexical_scores(blocks, terms):
    out = []
    for b in blocks:
        t = b["text"]
        hit = [x for x in terms if x and x in t]
        cnt = sum(t.count(x) for x in hit)
        out.append((len(hit), cnt))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["qwen", "bge"], required=True)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--task", default=TASK)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    gold = read_gold()
    books = {g["target_book"] for g in gold}
    blocks = load_blocks(books)
    print("%s: %d blocks, %d books, %.1fM chars" % (
        args.model, len(blocks), len(books), sum(len(b["text"]) for b in blocks) / 1e6))

    tok, model, pool = load_model(args.model)
    cache = os.path.join(OUT, "%s-blocks.npy" % args.model)
    if os.path.exists(cache) and not args.refresh:
        E = np.load(cache)
        dt = float(np.load(cache.replace(".npy", ".sec.npy"))[0])
        print("cached embeddings", E.shape)
    else:
        t0 = time.time()
        E = embed([b["text"] for b in blocks], tok, model, pool, args.batch, args.max_len)
        dt = time.time() - t0
        np.save(cache, E)
        np.save(cache.replace(".npy", ".sec.npy"), np.array([dt]))
    print("embed %.1fs -> %.1f blocks/s" % (dt, len(blocks) / dt))

    qtexts = []
    for g in gold:
        q = g["question"]
        qtexts.append("Instruct: %s\nQuery:%s" % (args.task, q) if args.model == "qwen" else q)
    Q = embed(qtexts, tok, model, pool, batch=8, max_len=args.max_len)

    # gold block index per question
    rows = []
    for qi, g in enumerate(gold):
        gi = gold_index(blocks, g)
        sims = E @ Q[qi]
        if gi is None:
            rows.append({"qid": g["qid"], "rank": None})
            continue
        rank, order = ranks(sims, gi)
        # book rank: best block per book
        best = {}
        for idx in order:
            best.setdefault(blocks[idx]["book"], float(sims[idx]))
        book_order = sorted(best, key=lambda k: -best[k])
        brow = book_order.index(g["target_book"]) + 1
        rows.append({"qid": g["qid"], "rank": rank, "book_rank": brow,
                     "top1": int(order[0] == gi),
                     "top20": int(rank <= 20), "top50": int(rank <= 50),
                     "book_top1": int(brow == 1), "book_top5": int(brow <= 5),
                     "rr20": 1.0 / rank if rank <= 20 else 0.0})
    # lexical baseline
    lex = []
    for qi, g in enumerate(gold):
        terms = [t for t in g["terms"].split(",") if t]
        sc = lexical_scores(blocks, terms)
        gi = gold_index(blocks, g)
        if gi is None:
            continue
        order = sorted(range(len(blocks)), key=lambda i: (-sc[i][0], -sc[i][1]))
        pos = order.index(gi) + 1
        lex.append({"qid": g["qid"], "rank": pos, "top20": int(pos <= 20),
                    "rr20": 1.0 / pos if pos <= 20 else 0.0,
                    "top1": int(pos == 1)})

    def agg(rs, key):
        vals = [r[key] for r in rs if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    n = len([r for r in rows if r.get("rank")])
    summary = {
        "model": args.model, "blocks": len(blocks), "questions": n,
        "recall20": agg(rows, "top20"), "mrr20": agg(rows, "rr20"),
        "top1": agg(rows, "top1"), "book_top1": agg(rows, "book_top1"),
        "book_top5": agg(rows, "book_top5"),
        "lex_recall20": agg(lex, "top20"), "lex_mrr20": agg(lex, "rr20"),
        "lex_top1": agg(lex, "top1"),
        "blocks_per_s": len(blocks) / dt,
    }
    with open(os.path.join(OUT, "%s.tsv" % args.model), "w", encoding="utf-8") as fh:
        cols = ["qid", "rank", "book_rank", "top1", "top20", "rr20"]
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
    with open(os.path.join(OUT, "%s.json" % args.model), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": rows, "lex": lex}, fh,
                  ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
