#!/usr/bin/env python3
"""检索重排与后续方案总览图。数据取自本目录的 eval-*.tsv 与 README §2.2，改数重跑即可。"""
import html
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "图-检索重排与路线-2026-10-21.svg"

FONT = "Noto Sans CJK SC, sans-serif"
INK = "#1a2330"
MUTED = "#5a6b7d"
LINE = "#c9d4de"
BG = "#ffffff"
CARD = "#f6f9fc"
DONE = "#2e7d32"
WIP = "#ef6c00"
HOLD = "#607d8b"
STOP = "#b23b3b"

W, H = 1800, 1320
parts = []


def add(s):
    parts.append(s)


def text(x, y, s, size=17, fill=INK, weight="normal", anchor="start", opacity=1):
    add(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}" '
        f'opacity="{opacity}">{html.escape(s)}</text>')


def rect(x, y, w, h, fill=CARD, stroke=LINE, rx=10, sw=1.5):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def chip(x, y, label, color):
    w = 12 * len(label) + 30
    add(f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="17" '
        f'fill="{color}" opacity="0.14"/>')
    add(f'<circle cx="{x + 17}" cy="{y + 17}" r="5" fill="{color}"/>')
    add(f'<text x="{x + 30}" y="{y + 23}" font-family="{FONT}" font-size="16" '
        f'fill="{color}" font-weight="bold">{html.escape(label)}</text>')
    return w


def arrow(x1, y1, x2, y2, color=MUTED):
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="2.5"/>')
    add(f'<polygon points="{x2},{y2} {x2 - 8},{y2 - 11} {x2 + 8},{y2 - 11}" '
        f'fill="{color}"/>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
    f'viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG}"/>')

text(40, 56, "检索重排与后续方案：管线 · 方法 · 进度", 34, INK, "bold")
text(40, 90, "口径：gold 60 题评测（eval-*.tsv）· index.db 2,359,595 段 / 扩展 87,664 章 · 2026-10-21 13:40", 17, MUTED)

# ---------- 左：管线 ----------
LX, LW = 40, 1000
text(LX, 140, "查询时管线与各步方法", 22, INK, "bold")

stages = [
    ("词法召回", "P1 · 已上线", DONE,
     ["CJK bigram(+unigram) 预切进 FTS5，BM25 打分后做组覆盖度重排",
      "概念表 126 键展开成 OR 组与排除正则，kana 段降权压日文噪声"],
     "判据：2 字词可查且有序，5 个译名墙案例全召回"),
    ("文档扩展补词", "P1.5 · 收尾中", WIP,
     ["章级主题前缀约 651 token 与伪查询 5 条每章，与原文分存，只进索引",
      "伪查询带少样本正例加「每条两个正文没出现的词」硬约束"],
     "进度：前缀 87,271 章铺完，伪查询 394 章，无偏实验在跑"),
    ("书级排序", "P2 · 待人眼判定", WIP,
     ["book_concept 概念剖面 16,027 行，tf-idf 书级聚合，qsearch --book",
      "问句路由到概念键后多路融合（M 档），检索词与概念双通道"],
     "判据：「哪本书讲自由」top20 人眼认可 ≥70%，判定材料已备好等人看"),
    ("候选重排", "P3 · 推迟", HOLD,
     ["启发式 H（定义句加分、对白与标题罚分）实测平盘，已停用",
      "cross-encoder 只排 top 30 至 50，门槛是无原词进前 20 先到 0.25"],
     "重排换的是池内顺序，池里没有的段它排不出来"),
    ("出口与流程", "P5 · 基本完成", DONE,
     ["多路结果 RRF 融合，视图/主题 125 键相对软链，序与书级排序一致",
      "引文一律回 库/ 原文逐字核对，扩展文本不当原文证据"],
     "红线：检索层只读 库/，verify_phase 箱 6 与 check_p1 把关"),
]
y = 160
for name, state, color, methods, gate in stages:
    rect(LX, y, LW, 148)
    text(LX + 24, y + 40, name, 22, INK, "bold")
    chip(LX + 200, y + 18, state, color)
    for i, m in enumerate(methods):
        add(f'<circle cx="{LX + 31}" cy="{y + 72 + i * 30}" r="3.5" fill="{MUTED}"/>')
        text(LX + 44, y + 78 + i * 30, m, 16, INK)
    text(LX + 44, y + 132, gate, 16, color, "bold")
    if y > 160:
        arrow(LX + LW // 2, y - 22, LX + LW // 2, y - 4)
    y += 170

rect(LX, y + 2, LW, 44, fill="#fdf2f2", stroke="#e3b6b6")
text(LX + 24, y + 31, "P4 向量层不做：操作人 2026-10-21 指示，sem_index.py 不建，bge-m3 方案归档。", 16, STOP)

# ---------- 右上：读数 ----------
RX, RW = 1080, 680
text(RX, 140, "无原词类进前 20 的读数（gold 60 题）", 22, INK, "bold")
bars = [
    ("A grep 原词", 0.00, "#b0bec5"),
    ("B rg 原词", 0.10, "#b0bec5"),
    ("C defpass 概念", 0.05, "#b0bec5"),
    ("D 近义词组", 0.00, "#b0bec5"),
    ("E bigram+覆盖度", 0.05, "#7e96a8"),
    ("G E+主题前缀", 0.05, "#7e96a8"),
    ("H E+启发式重排", 0.05, "#7e96a8"),
    ("M E+路由融合", 0.25, "#ef6c00"),
    ("J E+伪查询 上限", 0.25, "#ef6c00"),
    ("F 概念键直喂 上界", 0.50, "#9c27b0"),
]
CX, CY, CW = RX + 210, 185, 420
scale = CW / 0.55
for i, (label, v, color) in enumerate(bars):
    yy = CY + i * 42
    text(RX + 200, yy + 22, label, 15, INK, anchor="end")
    add(f'<rect x="{CX}" y="{yy + 4}" width="{max(v * scale, 2)}" height="24" '
        f'rx="5" fill="{color}"/>')
    text(CX + v * scale + 10, yy + 22, f"{v:.3f}", 15, color, "bold")
for v, lab in ((0.15, "0.15 有信号"), (0.25, "0.25 达标"), (0.50, "0.50 词法上界")):
    xx = CX + v * scale
    add(f'<line x1="{xx}" y1="{CY - 14}" x2="{xx}" y2="{CY + 430}" stroke="{MUTED}" '
        f'stroke-width="1" stroke-dasharray="4 4" opacity="0.5"/>')
    text(xx + 4, CY - 16, lab, 13, MUTED)
text(RX + 24, CY + 456, "E 的无原词 20 题里 17 题目标章不在前 100 候选，缺口是词不是序。", 15, MUTED)

# ---------- 右中：裁决 ----------
VY = 690
rect(RX, VY, RW, 318, fill="#fffaf2", stroke="#e6cfa8")
text(RX + 24, VY + 40, "重排为什么现在不做", 21, INK, "bold")
verdict = [
    "召回上限：把候选放大到 2000 段，17 题目标段一个都不在池内",
    "其中 7 题总命中不到 2000，目标段对检索词一次都没匹配上",
    "启发式 H 原词平盘（MRR 0.389 对 0.389），还把唯一噪声段顶到第 1",
    "池内只剩 3 题可救，就算全提进前 20，上限是 15%",
    "门槛重写：原词 r@10 与 MRR 不低于 E，噪声 ≤1 段，无原词 ≥0.25",
]
for i, s in enumerate(verdict):
    add(f'<circle cx="{RX + 32}" cy="{VY + 78 + i * 34}" r="3.5" fill="{WIP}"/>')
    text(RX + 46, VY + 84 + i * 34, s, 16, INK)
text(RX + 24, VY + 288, "结论：先补词（P1.5 伪查询），召回到 0.25 再上 cross-encoder。", 16, WIP, "bold")

# ---------- 下：四步 ----------
BY = 1118
text(LX, BY - 12, "接下来四步（按顺序，不并行）", 22, INK, "bold")
steps = [
    ("1", "伪查询无偏实验", WIP, "15 本书 1,069 章全扩后重测 J",
     "进行中 402/1,069 章，mimo 通道", "读数 ≥0.15 续，塌回则停"),
    ("2", "查询侧扩展改 RRF", MUTED, "查询词扩近义短语，按名次融合 k=60",
     "未开始，30 分钟级", "r@10 不低于 E 才留"),
    ("3", "稀疏检索试点", HOLD, "BGE-M3 稀疏模式或中文 SPLADE，章节级 A/B",
     "待操作人批准", "无原词 ≥0.25 才铺全库"),
    ("4", "cross-encoder 重排", HOLD, "bge-reranker-base 只排 top 30 至 50",
     "门槛未到", "MRR +0.02 才留，否则留作反例"),
]
sw = 415
for i, (num, title, color, how, state, gate) in enumerate(steps):
    x = LX + i * (sw + 22)
    rect(x, BY + 6, sw, 160)
    add(f'<circle cx="{x + 34}" cy="{BY + 44}" r="18" fill="{color}" opacity="0.16"/>')
    text(x + 34, BY + 51, num, 20, color, "bold", anchor="middle")
    text(x + 64, BY + 51, title, 19, INK, "bold")
    text(x + 24, BY + 88, how, 15, INK)
    text(x + 24, BY + 118, state, 15, color, "bold")
    text(x + 24, BY + 146, gate, 15, MUTED)
    if i:
        arrow(x - 18, BY + 90, x - 4, BY + 90)

add("</svg>")
OUT.write_text("\n".join(parts), encoding="utf-8")
print(OUT)
