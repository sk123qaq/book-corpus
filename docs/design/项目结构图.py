#!/usr/bin/env python3
"""Emit docs/design/项目结构图.html (one self-contained page, inline SVG flowchart).

2026-11-02 布局迁移：图内文字仍是 v1 读数，重新生成时按 build/、tables/、books/ 取数。

Regenerate after the layout changes: python3 docs/design/项目结构图.py
Every number in the diagram was read from build/library.db (lq.py), tables/分类表.csv,
tables/概念表.tsv and the filesystem on 2026-10-19.

输出默认落在本文件同目录的 项目结构图.html（即仓内那份），要落到别处用 --out。
"""

import argparse
import os

W, H = 1660, 1360
CW = 430
COLS = {1: 40, 2: 600, 3: 1160}
PAD = 18
BAR = dict(x=40, y=1200, w=1580, h=130)

TONES = {
    "amber": ("#fffbeb", "#b45309", "#92400e"),
    "blue": ("#eff6ff", "#1d4ed8", "#1e3a8a"),
    "teal": ("#ecfeff", "#0e7490", "#155e75"),
    "violet": ("#f5f3ff", "#6d28d9", "#4c1d95"),
    "slate": ("#f8fafc", "#475569", "#1e293b"),
    "red": ("#fef2f2", "#b91c1c", "#7f1d1d"),
}

NODES = {
    "L1": dict(
        col=1,
        y=60,
        tone="slate",
        title="新书源文件",
        lines=["epub、mobi、azw3、pdf、txt、djvu", "落盘之后由人账决定它去哪"],
    ),
    "L2": dict(
        col=1,
        y=200,
        tone="slate",
        title="库/_收件箱/",
        lines=["新书先落这里，再从人账开一条", "ingest.py 还没实现，入库靠手工加行"],
    ),
    "L5": dict(
        col=1,
        y=344,
        tone="slate",
        title="src/bookcorpus/scripts/",
        badge="46 个 py",
        lines=["每个动作都是可重跑脚本，不手改产物", "一个账本一个写者：人写人账，脚本写产物"],
    ),
    "L4": dict(
        col=1,
        y=556,
        tone="slate",
        title="../books.snapshot-2026-10-18",
        badge="47916 文件",
        lines=[
            "L0 硬链接快照，与库里的文件共享 inode",
            "所以库内文件走 tmp 加 os.replace",
            "截断式原地写会同时改掉快照",
        ],
    ),
    "L3": dict(
        col=1,
        y=760,
        tone="slate",
        title="工作区/批次/",
        badge="8 个批次",
        lines=[
            "8 个批次的目录约定不统一",
            "齐全的只有 旧md重拆 与 pdf-ocr：worklist、logs、out、",
            "overrides 四样都在",
            "普查只留 census.jsonl，检索批次只留 logs/",
        ],
    ),
    "S1": dict(
        col=2,
        y=100,
        tone="amber",
        title="目录/分类表.csv",
        badge="1694 行",
        lines=["人账。一行一本书：uid、一级、二级、作者层、", "保留、正式书名、备注", "分类、书名、保留策略只在这里改"],
    ),
    "S2": dict(
        col=2,
        y=262,
        tone="amber",
        title="build_library.py --emit-json",
        lines=["读人账，重建 library.db，同步每本 book.json", "conflicts=0 且 books 等于人账行数才算过"],
    ),
    "S3": dict(
        col=2,
        y=402,
        tone="blue",
        title="目录/library.db",
        badge="6 表 6 视图",
        lines=[
            "book、source、derived、job、award、book_award",
            "派生表记 chapters、bytes、method",
            "v_pending_md 视图：315 本还缺 md",
            "产物，可重建；查询用 lq.py 只读打开",
            "同目录：对照表.csv 路径映射、系列/ 卷序 21 份",
        ],
    ),
    "S4": dict(
        col=2,
        y=600,
        tone="blue",
        title="库/ 内容区",
        badge="1694 本",
        lines=[
            "唯一权威内容。路径按 一级/二级/作者层/系列 排",
            "多数是一级/二级/书名 1170 本，带作者层 230 本",
            "盘上层深：两层 149、三层 1193、四层 234、五层 118",
            "每本 book.json，329 本的源文件在合集目录里",
            "可选 md/ 与 html/，md/ 只认这个位置",
            "11 个一级分类，另加一个 _收件箱",
        ],
    ),
    "S5": dict(
        col=2,
        y=810,
        tone="teal",
        title="转换器（source/ 到 md/）",
        badge="1379 已拆 315 待拆",
        lines=[
            "产出章节 md、README 目录、images/",
            "1376 本有 README，938 本有 images/",
            "ebook-to-md 1140、paddle-ocr 174、pdf-text 55",
            "split_collection 7、mineru-ocr 3",
            "待拆的 pdf 里 193 本在排期，其余暂缓或不转",
        ],
    ),
    "R1": dict(
        col=3,
        y=402,
        tone="violet",
        title="目录/书目总表.csv 与 .xlsx",
        lines=["gen_book_catalog.py 从 DB 导出", "含 md 状态、主源文件、路径", "末行报未匹配条数，要求 0"],
    ),
    "R2": dict(
        col=3,
        y=536,
        tone="violet",
        title="视图/奖项/",
        badge="10 奖项 1010 链",
        lines=["奖项降为字段，目录树由软链现搭", "随时可删可重建"],
    ),
    "R3": dict(
        col=3,
        y=680,
        tone="violet",
        title="阅读版 html",
        badge="2 本",
        lines=["build_index.py 出每本 html/index.html", "加目录/阅读版索引.html"],
    ),
    "R4": dict(
        col=3,
        y=824,
        tone="violet",
        title="目录/检索/index.db",
        badge="约 5.5 GiB，在涨",
        lines=[
            "段落倒排 FTS5 bigram、概念剖面、章级扩展",
            "切分与查询共用 lib_fts_cjk.py",
            "概念表在这里参与检索",
            "改检索脚本前先看目录/检索/踩坑记录.md",
            "build_expand.py 从 13:19 跑到现在，体积在涨",
        ],
    ),
    "R5": dict(
        col=3,
        y=1030,
        tone="violet",
        title="视图/主题/",
        badge="124 概念 1025 链",
        lines=[
            "qsearch --book 的排序，build_topic_views.py 建链",
            "124 个概念目录，1025 条软链，外加一份 README",
            "概念表：达克效应、人工智能对齐 还没有书",
        ],
    ),
}

for _k, n in NODES.items():
    n["x"] = COLS[n["col"]]
    n["h"] = 16 + 24 + len(n["lines"]) * 20 + 14
    n["y2"] = n["y"] + n["h"]


def cx(n):
    return n["x"] + CW / 2


def estimate(s, size):
    return sum(size if ord(c) > 0x2E80 else size * 0.56 for c in s)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


E = NODES
EDGES = [
    ([(cx(E["L1"]), E["L1"]["y2"]), (cx(E["L1"]), E["L2"]["y"])], "收到", cx(E["L1"]) + 10, 190, False),
    ([(E["L2"]["x"] + CW, 250), (520, 250), (520, 180), (E["S1"]["x"], 180)], "人工分类", 528, 172, False),
    ([(cx(E["S1"]), E["S1"]["y2"]), (cx(E["S1"]), E["S2"]["y"])], "人账", cx(E["S1"]) + 10, 252, False),
    ([(cx(E["S2"]), E["S2"]["y2"]), (cx(E["S2"]), E["S3"]["y"])], "重建", cx(E["S2"]) + 10, 396, False),
    ([(cx(E["S3"]), E["S3"]["y2"]), (cx(E["S3"]), E["S4"]["y"])], "路径是分类表的投影", cx(E["S3"]) + 10, 584, False),
    ([(cx(E["S4"]), E["S4"]["y2"]), (cx(E["S4"]), E["S5"]["y"])], "source/ 里的文件", cx(E["S4"]) + 10, 798, False),
    ([(E["L5"]["x"] + CW, 362), (E["S2"]["x"], 362)], "重跑", 486, 354, True),
    ([(E["S4"]["x"], 650), (E["L4"]["x"] + CW, 650)], "搬错删错回滚", 482, 642, True),
    ([(E["S5"]["x"], 840), (E["L3"]["x"] + CW, 840)], "每批留账", 482, 832, True),
    ([(E["S3"]["x"] + CW, 462), (E["R1"]["x"], 462)], "导出", 1040, 454, False),
    ([(E["S3"]["x"] + CW, 500), (1080, 500), (1080, 583), (E["R2"]["x"], 583)], "奖项记录", 1088, 575, False),
    ([(E["S4"]["x"] + CW, 620), (1110, 620), (1110, 727), (E["R3"]["x"], 727)], "", 0, 0, False),
    ([(E["S4"]["x"] + CW, 690), (1080, 690), (1080, 891), (E["R4"]["x"], 891)], "", 0, 0, False),
    (
        [(cx(E["R4"]), E["R4"]["y2"]), (cx(E["R5"]), E["R5"]["y"])],
        "qsearch 与 build_topic_views.py",
        cx(E["R4"]) + 10,
        992,
        False,
    ),
    (
        [(cx(E["S5"]), E["S5"]["y2"]), (cx(E["S5"]), BAR["y"])],
        "每次改完之后，按改了什么挑一条跑",
        cx(E["S5"]) + 12,
        1188,
        True,
    ),
]

out = [
    f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
    'font-family="Noto Sans CJK SC, Source Han Sans SC, PingFang SC, Microsoft YaHei, sans-serif">',
    "<defs>"
    '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#334155"/></marker>'
    '<marker id="ahd" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker>'
    "</defs>",
    f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
]

for col, label in ((1, "源文件、机账、回滚副本"), (2, "人账、索引与内容"), (3, "派生产物")):
    out.append(f'<text x="{COLS[col]}" y="34" font-size="14" font-weight="700" fill="#94a3b8">{esc(label)}</text>')

for pts, label, lx, ly, dashed in EDGES:
    d = "M" + " L".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    dash = 'stroke-dasharray="6 4" ' if dashed else ""
    out.append(
        f'<path d="{d}" fill="none" stroke="{"#94a3b8" if dashed else "#334155"}" '
        f'stroke-width="1.6" {dash}marker-end="url(#{"ahd" if dashed else "ah"})"/>'
    )
    if label:
        assert estimate(label, 11.5) <= 210, label
        out.append(f'<text x="{lx:.0f}" y="{ly:.0f}" font-size="11.5" fill="#475569">{esc(label)}</text>')

for key, n in NODES.items():
    bg, stroke, ink = TONES[n["tone"]]
    x, y, h = n["x"], n["y"], n["h"]
    out.append(
        f'<rect x="{x}" y="{y}" width="{CW}" height="{h}" rx="8" fill="{bg}" stroke="{stroke}" stroke-width="1.8"/>'
    )
    out.append(
        f'<text x="{x + PAD}" y="{y + 30}" font-size="15" font-weight="700" fill="{ink}">{esc(n["title"])}</text>'
    )
    if n.get("badge"):
        out.append(
            f'<text x="{x + CW - PAD}" y="{y + 30}" font-size="12" fill="{stroke}" '
            f'text-anchor="end">{esc(n["badge"])}</text>'
        )
    ty = y + 52
    for line in n["lines"]:
        assert estimate(line, 11.5) <= CW - 2 * PAD, (key, line, estimate(line, 11.5))
        out.append(f'<text x="{x + PAD}" y="{ty}" font-size="11.5" fill="#334155">{esc(line)}</text>')
        ty += 20

bx, by, bw, bh = BAR["x"], BAR["y"], BAR["w"], BAR["h"]
out.append(
    f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" fill="#fef2f2" stroke="#b91c1c" stroke-width="1.8"/>'
)
out.append(
    f'<text x="{bx + PAD}" y="{by + 30}" font-size="15" font-weight="700" fill="#7f1d1d">改完跑什么（门槛）</text>'
)
checks = [
    "人账动过：build_library.py --emit-json，conflicts=0 且 books 等于人账行数",
    "结构与 html 动过：verify_phase.py，末行全过",
    "书目动过：gen_book_catalog.py，末行未匹配 0 条",
    "检索层动过：check_p1.py 失败 0 项，verify_phase.py --box 6 全过",
]
for i, line in enumerate(checks):
    out.append(
        f'<text x="{bx + PAD + (i % 2) * 780}" y="{by + 60 + (i // 2) * 26}" '
        f'font-size="11.5" fill="#334155">{esc(line)}</text>'
    )

out.append("</svg>")
svg = "\n".join(out)

html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>书库项目结构图</title>
<style>
  body {{ margin: 0; padding: 28px 24px 48px; background: #fff; color: #0f172a;
         font-family: "Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  h2 {{ font-size: 14px; margin: 28px 0 8px; color: #334155; }}
  p.sub {{ margin: 0 0 16px; font-size: 13px; color: #475569; }}
  .legend {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #334155; margin: 0 0 14px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 6px; }}
  .sw {{ width: 14px; height: 14px; border-radius: 3px; border: 1.8px solid; }}
  .wrap {{ overflow-x: auto; }}
  svg {{ max-width: none; }}
  ul {{ font-size: 12.5px; color: #334155; margin: 0; padding-left: 20px; line-height: 1.9; }}
  code {{ font-size: 12px; background: #f1f5f9; padding: 1px 5px; border-radius: 3px; }}
</style>
</head>
<body>
<div style="margin:12px 16px;padding:10px 14px;border-left:4px solid #b58900;background:#fff8e1;font:14px/1.6 system-ui,sans-serif;color:#333"><b>布局说明（2026-11-02）</b>：这是 v1 时代生成的图。图中的 <code>库/</code> 现在对应 <code>books/&lt;uid&gt;/</code>，<code>目录/library.db</code> 对应 <code>build/library.db</code>，<code>目录/检索/</code> 对应 <code>build/检索/</code>，<code>视图/</code> 对应 <code>build/views/</code>，<code>src/bookcorpus/scripts/</code> 对应 <code>src/bookcorpus/scripts/</code>。</div>

<h1>书库项目结构图</h1>
<p class="sub">数字取自 目录/library.db、目录/分类表.csv、目录/概念表.tsv 与盘上实测（2026-10-20，分类表那一行是 10-19）。按写入者分栏读，不按文件夹列。</p>
<div class="legend">
  <span class="chip"><span class="sw" style="background:#fffbeb;border-color:#b45309"></span>人账</span>
  <span class="chip"><span class="sw" style="background:#eff6ff;border-color:#1d4ed8"></span>内容区与索引（产物）</span>
  <span class="chip"><span class="sw" style="background:#ecfeff;border-color:#0e7490"></span>转换器</span>
  <span class="chip"><span class="sw" style="background:#f5f3ff;border-color:#6d28d9"></span>派生视图与导出</span>
  <span class="chip"><span class="sw" style="background:#f8fafc;border-color:#475569"></span>源文件、机账、快照</span>
  <span class="chip">实线是写入流向，虚线是留账、回滚或事后检查</span>
</div>
<div class="wrap">
{svg}
</div>

<h2>规则与方案住哪</h2>
<ul>
  <li><code>AGENTS.md</code> 现状数字、文件地图、脚本表、改完跑什么</li>
  <li><code>docs/design/设计方案-书目结构.md</code> L0 到 L5 的门槛与目标结构</li>
  <li><code>book-corpus-data/archive/决策记录-2026-10-18_2026-10-26.tsv</code> 每个决定的理由与证据</li>
  <li><code>目录/检索/README.md</code> 与 <code>踩坑记录.md</code>，改检索脚本前先查</li>
</ul>
<p class="sub" style="margin-top:22px">这张图由 <code>docs/design/项目结构图.py</code> 生成，改节点或坐标后重跑它即可。</p>
</body>
</html>
"""

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "项目结构图.html")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT, help="默认本文件同目录的 项目结构图.html")
    a = ap.parse_args()
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {a.out}, {len(NODES)} nodes, {len(EDGES)} edges, canvas {W}x{H}")
