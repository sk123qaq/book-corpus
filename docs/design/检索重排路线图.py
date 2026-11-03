#!/usr/bin/env python3
"""Emit docs/design/检索重排路线图.html as one self-contained page with inline SVG.

Run python3 docs/design/检索重排路线图.py to regenerate after editing a table below.
Every label and number comes from 工作区/批次/qa-检索-2026-10-19/make_检索重排路线图.py,
which rendered 图-检索重排与路线-2026-10-21.png on 2026-10-21.
"""

import html
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "检索重排路线图.html"

FONT = 'Noto Sans CJK SC, Source Han Sans SC, PingFang SC, "Microsoft YaHei", sans-serif'
W, H = 1800, 1320

PALETTE = {
    "ink": "#1a2330",
    "muted": "#5a6b7d",
    "line": "#c9d4de",
    "bg": "#ffffff",
    "card": "#f6f9fc",
    "done": "#2e7d32",
    "in_progress": "#ef6c00",
    "deferred": "#607d8b",
    "stopped": "#b23b3b",
    "not_started": "#5a6b7d",
    "bar_base": "#b0bec5",
    "bar_current": "#7e96a8",
    "bar_route": "#ef6c00",
    "bar_ceiling": "#9c27b0",
    "warn_bg": "#fdf2f2",
    "warn_line": "#e3b6b6",
    "note_bg": "#fffaf2",
    "note_line": "#e6cfa8",
}

HEADER = {
    "title": "检索重排与后续方案：管线 · 方法 · 进度",
    "subtitle": "口径：gold 60 题 · 2026-10-21 出图，2026-10-24 补 P3/P4 实测；右栏柱状读数仍为 10-21 口径",
}

LEFT = {
    "heading": "查询时管线与各步方法",
    "stages": [
        {
            "title": "词法召回",
            "status": "done",
            "chip": "P1 · 已上线",
            "methods": (
                "CJK bigram(+unigram) 预切进 FTS5，BM25 打分后做组覆盖度重排",
                "概念表键展开成 OR 组与排除正则，kana 段降权压日文噪声",
            ),
            "gate": "判据：2 字词可查且有序，5 个译名墙案例全召回",
        },
        {
            "title": "文档扩展补词",
            "status": "in_progress",
            "chip": "P1.5 · 收尾中",
            "methods": (
                "章级主题前缀约 651 token 与伪查询 5 条每章，与原文分存，只进索引",
                "伪查询带少样本正例加「每条两个正文没出现的词」硬约束",
            ),
            "gate": "进度：前缀 87,271 章铺完，伪查询 394 章，无偏实验在跑",
        },
        {
            "title": "书级排序",
            "status": "in_progress",
            "chip": "P2 · 待人眼判定",
            "methods": (
                "book_concept 概念剖面 16,027 行，tf-idf 书级聚合，qsearch --book",
                "问句路由到概念键后多路融合（M 档），检索词与概念双通道",
            ),
            "gate": "判据：「哪本书讲自由」top20 人眼认可 ≥70%，判定材料已备好等人看",
        },
        {
            "title": "候选重排",
            "status": "deferred",
            "chip": "P3 · 已实测",
            "methods": (
                "cross-encoder 实测：向量前 200 章候选，bge-reranker-v2-m3，单题 8 秒（K=2000 是 81 秒）",
                "近义 3/20 到 7/20，原词 16/20 到 17/20，无原词 5/20 到 4/20",
            ),
            "gate": "工作点 K=200；无原词暂停，验收线改挂近义（2026-10-24）",
        },
        {
            "title": "出口与流程",
            "status": "done",
            "chip": "P5 · 基本完成",
            "methods": (
                "多路结果 RRF 融合，视图/主题 125 键相对软链，序与书级排序一致",
                "引文一律回 库/ 原文逐字核对，扩展文本不当原文证据",
            ),
            "gate": "红线：检索层只读 库/，verify_phase 箱 6 与 check_p1 把关",
        },
    ],
    "p4": {
        "status": "stopped",
        "text": "P4 生产向量层不做（sem_index.py 不建）；实验台已用 bge-m3 全库 879,961 块实测，无原词 R@20 5/20，K=2000 池上限 70%。",
    },
}

RIGHT = {
    "heading": "无原词类进前 20 的读数（gold 60 题）",
    "bars": [
        {"label": "A grep 原词", "value": 0.00, "tone": "bar_base"},
        {"label": "B rg 原词", "value": 0.10, "tone": "bar_base"},
        {"label": "C defpass 概念", "value": 0.05, "tone": "bar_base"},
        {"label": "D 近义词组", "value": 0.00, "tone": "bar_base"},
        {"label": "E bigram+覆盖度", "value": 0.05, "tone": "bar_current"},
        {"label": "G E+主题前缀", "value": 0.05, "tone": "bar_current"},
        {"label": "H E+启发式重排", "value": 0.05, "tone": "bar_current"},
        {"label": "M E+路由融合", "value": 0.25, "tone": "bar_route"},
        {"label": "J E+伪查询 上限", "value": 0.25, "tone": "bar_route"},
        {"label": "F 概念键直喂 上界", "value": 0.50, "tone": "bar_ceiling"},
    ],
    "thresholds": [
        {"value": 0.15, "label": "0.15 有信号"},
        {"value": 0.25, "label": "0.25 达标"},
        {"value": 0.50, "label": "0.50 词法上界"},
    ],
    "caption": "E 的无原词 20 题里 17 题目标章不在前 100 候选，缺口是词不是序。",
    "verdict": {
        "title": "重排为什么现在不做（2026-10-24 复核）",
        "bullets": [
            "召回上限：向量前 2000 章含 14/20，工作点前 200 章只剩 6/20",
            "重排实测：近义 3/20→7/20，原词 16/20→17/20，无原词 5/20→4/20",
            "无原词不算产品场景，已暂停；70% 只作 K=2000 的池上限记录",
            "与 RRF 融合也试过：无原词 3/20、近义 5/20，都不如单路或重排",
            "代价：K=200 单题 8 秒，K=2000 是 81 秒，默认路径 0.7 秒",
        ],
        "conclusion": "结论：工作点 K=200，留实验台，不进默认路径（决策记录 2026-10-24）。",
    },
}

BOTTOM = {
    "heading": "接下来四步（按顺序，不并行）",
    "steps": [
        {
            "num": "1",
            "title": "伪查询无偏实验",
            "status": "in_progress",
            "how": "15 本书 1,069 章全扩后重测 J",
            "state": "进行中 402/1,069 章，mimo 通道",
            "gate": "读数 ≥0.15 续，塌回则停",
        },
        {
            "num": "2",
            "title": "查询侧扩展改 RRF",
            "status": "not_started",
            "how": "查询词扩近义短语，按名次融合 k=60",
            "state": "未开始，30 分钟级",
            "gate": "r@10 不低于 E 才留",
        },
        {
            "num": "3",
            "title": "稀疏检索试点",
            "status": "deferred",
            "how": "BGE-M3 稀疏模式或中文 SPLADE，章节级 A/B",
            "state": "待操作人批准",
            "gate": "无原词 ≥0.25 才铺全库",
        },
        {
            "num": "4",
            "title": "cross-encoder 重排",
            "status": "deferred",
            "how": "bge-reranker-v2-m3 排向量前 200 章，每章 8 块",
            "state": "已实测：近义 7/20、原词 17/20、无原词 4/20",
            "gate": "留实验台；K=2000 的 70% 线未达，无原词暂停",
        },
    ],
}

LX, LW = 40, 1000
RX, RW = 1080, 680
STAGE_Y0, STAGE_H, STAGE_STEP = 160, 148, 170
CX, CY, CW = RX + 210, 185, 420
BAR_ROW = 42
BAR_SCALE_MAX = 0.55
VY, VERDICT_H = 690, 318
BY, STEP_W, STEP_GAP = 1118, 415, 22


def esc(s):
    return html.escape(s, quote=True)


def text(out, x, y, s, size=17, tone="ink", weight="normal", anchor="start", opacity=1):
    out.append(
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{PALETTE[tone]}" '
        f'font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">{esc(s)}</text>'
    )


def rect(out, x, y, w, h, fill="card", stroke="line", rx=10, sw=1.5):
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{PALETTE[fill]}" stroke="{PALETTE[stroke]}" stroke-width="{sw}"/>'
    )


def chip(out, x, y, label, status):
    color = PALETTE[status]
    w = 12 * len(label) + 30
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="17" '
               f'fill="{color}" opacity="0.14"/>')
    out.append(f'<circle cx="{x + 17}" cy="{y + 17}" r="5" fill="{color}"/>')
    out.append(f'<text x="{x + 30}" y="{y + 23}" font-size="16" '
               f'fill="{color}" font-weight="bold">{esc(label)}</text>')
    return w


def arrow(out, x1, y1, x2, y2, tone="muted"):
    color = PALETTE[tone]
    out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
               f'stroke-width="2.5"/>')
    out.append(f'<polygon points="{x2},{y2} {x2 - 8},{y2 - 11} {x2 + 8},{y2 - 11}" '
               f'fill="{color}"/>')


def draw_header(out):
    text(out, 40, 56, HEADER["title"], 34, "ink", "bold")
    text(out, 40, 90, HEADER["subtitle"], 17, "muted")


def draw_stages(out):
    text(out, LX, 140, LEFT["heading"], 22, "ink", "bold")
    y = STAGE_Y0
    for stage in LEFT["stages"]:
        rect(out, LX, y, LW, STAGE_H)
        text(out, LX + 24, y + 40, stage["title"], 22, "ink", "bold")
        chip(out, LX + 200, y + 18, stage["chip"], stage["status"])
        for i, method in enumerate(stage["methods"]):
            out.append(f'<circle cx="{LX + 31}" cy="{y + 72 + i * 30}" r="3.5" '
                       f'fill="{PALETTE["muted"]}"/>')
            text(out, LX + 44, y + 78 + i * 30, method, 16, "ink")
        text(out, LX + 44, y + 132, stage["gate"], 16, stage["status"], "bold")
        if y > STAGE_Y0:
            arrow(out, LX + LW // 2, y - 22, LX + LW // 2, y - 4)
        y += STAGE_STEP

    p4 = LEFT["p4"]
    rect(out, LX, y + 2, LW, 44, fill="warn_bg", stroke="warn_line")
    text(out, LX + 24, y + 31, p4["text"], 16, p4["status"])


def draw_bars(out):
    text(out, RX, 140, RIGHT["heading"], 22, "ink", "bold")
    scale = CW / BAR_SCALE_MAX
    for i, row in enumerate(RIGHT["bars"]):
        yy = CY + i * BAR_ROW
        text(out, RX + 200, yy + 22, row["label"], 15, "ink", anchor="end")
        out.append(f'<rect x="{CX}" y="{yy + 4}" width="{max(row["value"] * scale, 2)}" '
                   f'height="24" rx="5" fill="{PALETTE[row["tone"]]}"/>')
        text(out, CX + row["value"] * scale + 10, yy + 22, f'{row["value"]:.3f}',
             15, row["tone"], "bold")
    for th in RIGHT["thresholds"]:
        xx = CX + th["value"] * scale
        out.append(f'<line x1="{xx}" y1="{CY - 14}" x2="{xx}" y2="{CY + 430}" '
                   f'stroke="{PALETTE["muted"]}" stroke-width="1" '
                   f'stroke-dasharray="4 4" opacity="0.5"/>')
        text(out, xx + 4, CY - 16, th["label"], 13, "muted")
    text(out, RX + 24, CY + 456, RIGHT["caption"], 15, "muted")


def draw_verdict(out):
    v = RIGHT["verdict"]
    rect(out, RX, VY, RW, VERDICT_H, fill="note_bg", stroke="note_line")
    text(out, RX + 24, VY + 40, v["title"], 21, "ink", "bold")
    for i, bullet in enumerate(v["bullets"]):
        out.append(f'<circle cx="{RX + 32}" cy="{VY + 78 + i * 34}" r="3.5" '
                   f'fill="{PALETTE["in_progress"]}"/>')
        text(out, RX + 46, VY + 84 + i * 34, bullet, 16, "ink")
    text(out, RX + 24, VY + 288, v["conclusion"], 16, "in_progress", "bold")


def draw_steps(out):
    text(out, LX, BY - 12, BOTTOM["heading"], 22, "ink", "bold")
    for i, step in enumerate(BOTTOM["steps"]):
        x = LX + i * (STEP_W + STEP_GAP)
        rect(out, x, BY + 6, STEP_W, 160)
        out.append(f'<circle cx="{x + 34}" cy="{BY + 44}" r="18" '
                   f'fill="{PALETTE[step["status"]]}" opacity="0.16"/>')
        text(out, x + 34, BY + 51, step["num"], 20, step["status"], "bold", anchor="middle")
        text(out, x + 64, BY + 51, step["title"], 19, "ink", "bold")
        text(out, x + 24, BY + 88, step["how"], 15, "ink")
        text(out, x + 24, BY + 118, step["state"], 15, step["status"], "bold")
        text(out, x + 24, BY + 146, step["gate"], 15, "muted")
        if i:
            arrow(out, x - 18, BY + 90, x - 4, BY + 90)


def render_svg():
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="{W}" height="{H}" font-family="{esc(FONT)}">',
           f'<rect width="{W}" height="{H}" fill="{PALETTE["bg"]}"/>']
    draw_header(out)
    draw_stages(out)
    draw_bars(out)
    draw_verdict(out)
    draw_steps(out)
    out.append("</svg>")
    return "\n".join(out)


def render_page():
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{esc(HEADER["title"])}</title>
<style>
  body {{ margin: 0; padding: 28px 24px 48px; background: #fff; color: #0f172a;
         font-family: "Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ overflow-x: auto; }}
  svg {{ display: block; width: 100%; height: auto; max-width: {W}px; margin: 0 auto; }}
</style>
</head>
<body>
<div class="wrap">
{render_svg()}
</div>
</body>
</html>
"""


def main():
    OUT.write_text(render_page(), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
