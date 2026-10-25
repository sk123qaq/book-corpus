#!/usr/bin/env python3
"""章级候选池的累计召回随 K 变化（K = 向量前 K 章）。

  k_curve.py --build    # 把 heldout 20 题并入 80 列分数矩阵
  k_curve.py --plot     # 出图与 CSV
"""
import argparse
import csv
import json
import os
import sqlite3

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-22-书级融合")
GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
HELD = os.path.join(BATCH, "book_gold-heldout.tsv")
OUT = os.path.join(BATCH, "live", "pilot-noword")
MODEL = os.path.join(ROOT, "工作区", "models", "bge-m3")
TASK = "为这个句子生成表示以用于检索相关文章："
KS = [100, 150, 200, 300, 400, 500, 700, 1000, 1400, 2000]
PAIRS_PER_CHAP = 6.5
MS_PER_PAIR = 6.2


def load_all():
    gold = list(csv.DictReader(open(GOLD, encoding="utf-8"), delimiter="\t"))
    tiers = ([("无原词", r) for r in gold if r["category"] == "无原词"]
             + [("原词", r) for r in gold if r["category"] == "原词"]
             + [("近义", r) for r in gold if r["category"] == "近义表达"])
    held = list(csv.DictReader(open(HELD, encoding="utf-8"), delimiter="\t"))
    return tiers + [("普通书", r) for r in held]


def build():
    meta = json.load(open(os.path.join(OUT, "blocks.json")))
    held = list(csv.DictReader(open(HELD, encoding="utf-8"), delimiter="\t"))
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL, dtype=torch.float16).to("cuda").eval()
    enc = tok([TASK + r["question"] for r in held], padding=True, truncation=True,
              max_length=128, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        h = model(**enc).last_hidden_state[:, 0]
        Q = torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy()
    n = sum(len(s) for s in meta["shards"])
    S = np.zeros((n, len(held)), dtype=np.float16)
    off = 0
    for si in range(len(meta["shards"])):
        E = np.load(os.path.join(OUT, "shard-%03d.npy" % si))
        S[off:off + len(E)] = (E.astype(np.float32) @ Q.T).astype(np.float16)
        off += len(E)
    b60 = np.load(os.path.join(OUT, "block_score.npy"))
    c60 = np.load(os.path.join(OUT, "chap_score.npy"))
    b80 = np.concatenate([b60, S], axis=1)
    rows = np.concatenate([np.asarray(s, dtype=np.int32) for s in meta["shards"]])
    pid = rows[:, 0]
    c80 = np.concatenate([c60, np.full((len(held), c60.shape[1]), -2.0, dtype=np.float32)])
    for j in range(len(held)):
        np.maximum.at(c80[60 + j], pid, b80[:, 60 + j].astype(np.float32))
    np.save(os.path.join(OUT, "block_score80.npy"), b80)
    np.save(os.path.join(OUT, "chap_score80.npy"), c80)
    print("block", b80.shape, "chap", c80.shape)


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for f in ("Noto Sans CJK SC", "Droid Sans Fallback"):
        if any(f in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            break
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False,
                         "figure.dpi": 200, "savefig.dpi": 200})

    meta = json.load(open(os.path.join(OUT, "blocks.json")))
    pidx = {p: i for i, p in enumerate(meta["paths"])}
    cm = np.load(os.path.join(OUT, "chap_score80.npy"))
    qs = load_all()
    ranks = {}
    for i, (tier, r) in enumerate(qs):
        gp = pidx[r["path"]]
        ranks.setdefault(tier, []).append(int(1 + (cm[i] > cm[i][gp]).sum()))

    tiers = ["原词", "近义", "无原词", "普通书"]
    colors = {"原词": "#1f77b4", "近义": "#d95f02", "无原词": "#2ca02c", "普通书": "#7f7f7f"}
    rng = np.random.default_rng(0)
    curves = {}
    for t in tiers:
        r = np.asarray(ranks[t])
        rec = [float((r <= k).mean() * 100) for k in KS]
        boots = np.empty((2000, len(KS)))
        for b in range(2000):
            s = r[rng.integers(0, len(r), len(r))]
            boots[b] = [(s <= k).mean() * 100 for k in KS]
        lo, hi = np.percentile(boots, [2.5, 97.5], axis=0)
        curves[t] = (rec, lo, hi)

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(12.4, 4.8),
                                 gridspec_kw={"width_ratios": [1.55, 1]})
    for t in tiers:
        rec, lo, hi = curves[t]
        ax.plot(KS, rec, color=colors[t], lw=2, ls="-" if t != "普通书" else "--")
        if t in ("近义", "无原词"):
            ax.fill_between(KS, lo, hi, color=colors[t], alpha=0.10, lw=0)
    ax.set_xscale("log")
    ax.set_xlim(95, 3200)
    ax.set_ylim(0, 108)
    ax.set_xticks([100, 200, 500, 1000, 2000])
    ax.set_xticklabels(["100", "200", "500", "1000", "2000"])
    ax.set_xlabel("候选池大小 K（向量前 K 章）")
    ax.set_ylabel("目标章落在池内的比例（%）")
    ax.grid(axis="y", color="#dddddd", lw=0.7)
    ax.set_axisbelow(True)
    ax.set_title("候选池上限：K 越大，目标章越可能进池", loc="left", fontsize=11)

    ends = {"普通书": (100, 0), "原词": (90, 2.5), "近义": (85, -2.5), "无原词": (70, 0)}
    for t, (y, dy) in ends.items():
        ax.text(2130, y + dy, t, color=colors[t], va="center", fontsize=9.5,
                fontweight="bold" if t != "普通书" else "normal")

    pts = [("原词", 200, 85.0, (7, -12), "left"), ("近义", 200, 35.0, (7, -12), "left"),
           ("近义", 2000, 50.0, (-9, 4), "right"), ("无原词", 200, 20.0, (7, -13), "left"),
           ("无原词", 2000, 20.0, (-6, 8), "right")]
    for t, k, v, off, ha in pts:
        ax.plot([k], [v], "o", ms=6, mfc="white", mec=colors[t], mew=1.8, zorder=5)
        ax.annotate("实排 %.0f%%" % v, (k, v), textcoords="offset points", xytext=off,
                    fontsize=8.5, color=colors[t], ha=ha)
    ax.text(0.01, 0.02, "每组 20 题；浅色带为 95% 自助区间；空心点是实际重排的 R@20。无原词在 K=200 与 K=2000 都是 20%",
            transform=ax.transAxes, fontsize=8, color="#555555")

    cost = [k * PAIRS_PER_CHAP * MS_PER_PAIR / 1000 for k in KS]
    bx.plot(KS, cost, color="#444444", lw=2)
    for k in (200, 500, 1000, 2000):
        c = k * PAIRS_PER_CHAP * MS_PER_PAIR / 1000
        bx.plot([k], [c], "o", color="#444444", ms=4)
        bx.annotate("%.0f s" % c, (k, c), textcoords="offset points", xytext=(5, 5), fontsize=9)
    bx.set_xscale("log")
    bx.set_xticks([100, 200, 500, 1000, 2000])
    bx.set_xticklabels(["100", "200", "500", "1000", "2000"])
    bx.set_xlabel("候选池大小 K")
    bx.set_ylabel("单题重排耗时（秒）")
    bx.grid(axis="y", color="#dddddd", lw=0.7)
    bx.set_axisbelow(True)
    bx.set_title("代价与 K 成正比（每章 6.5 块，每对 6.2 ms）", loc="left", fontsize=11)

    fig.tight_layout()
    png = os.path.join(OUT, "k-curve.png")
    fig.savefig(png)
    print("wrote", png)

    with open(os.path.join(OUT, "k-curve.csv"), "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["K"] + ["%s_%%" % t for t in tiers] + ["cost_s"])
        for j, k in enumerate(KS):
            w.writerow([k] + ["%.1f" % curves[t][0][j] for t in tiers] + ["%.1f" % cost[j]])
    for t in tiers:
        print("%-4s %s" % (t, " ".join("%d:%.0f%%" % (k, v) for k, v in zip(KS, curves[t][0]))))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()
    if a.build:
        build()
    if a.plot:
        plot()
