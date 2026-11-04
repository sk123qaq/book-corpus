#!/usr/bin/env python3
"""扫描 books/**/html/，给每本有阅读版的书重建 html/index.html，并重建 build/阅读版索引.html。

可重复运行：每次按当前 html/ 目录内容重建。书名与分类从同目录的 book.json 读，
书籍清单不再写在代码里；只有两本老书保留手写的副标题与介绍。

用法: book-corpus render
"""

import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L

TOP_INDEX = L.PATHS.reading_index
DATE = datetime.date.today().isoformat()

# 现有两本书的手写文案；其余书由 book.json 生成通用文案
BOOK_COPY = {
    "竞争优势": {
        "subtitle": "迈克尔·波特《竞争优势》",
        "blurb": "1985 年原书，中信出版社中译本，15 章",
    },
    "竞争战略": {
        "subtitle": "迈克尔·波特《竞争战略》",
        "blurb": "1980 年原书，中信出版社中译本，18 章（含 2 篇附录）",
    },
}
KNOWN_ORDER = ["竞争优势", "竞争战略"]

THEME_NAME = {
    "0e1a2b": "T1 战略档案",
    "16181d": "T2 现代研究",
    "14342b": "T3 知识手册",
    "1b1a19": "T4 杂志专题",
}


def iter_html_books():
    """books/**/html 目录。返回 [(uid, book.json 文档, html 目录绝对路径)]。"""
    out = []
    for dirpath, dirnames, _files in os.walk(L.BOOKS):
        if "html" not in dirnames:
            continue
        rel = os.path.relpath(dirpath, L.BOOKS)
        if rel.split(os.sep)[0].startswith("_"):
            continue
        html_dir = os.path.join(dirpath, "html")
        if not os.path.isdir(html_dir):
            continue
        bj = os.path.join(dirpath, "book.json")
        doc = {}
        if os.path.isfile(bj):
            with open(bj, encoding="utf-8") as fh:
                doc = json.load(fh)
        out.append((rel, doc, html_dir))
    out.sort(key=lambda x: x[0])
    return out


def probe(path):
    """返回 (主题名, 图表数, 汉字数)。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    theme = "—"
    for key, name in THEME_NAME.items():
        if key in raw.lower():
            theme = name
            break
    figs = sum(len(re.findall(r"<" + t, raw, re.I)) for t in ("figure", "svg", "table", "details"))
    txt = re.sub(r"(?is)<style.*?</style>", "", raw)
    txt = re.sub(r"(?is)<script.*?</script>", "", txt)
    txt = html.unescape(re.sub(r"(?s)<[^>]+>", " ", txt))
    cn = len(re.findall(r"[\u4e00-\u9fff]", txt))
    return theme, figs, cn


def chapter_rows(html_dir):
    out = []
    for f in sorted(os.listdir(html_dir)):
        if not f.endswith(".html") or f == "index.html":
            continue
        p = os.path.join(html_dir, f)
        if not os.path.isfile(p):
            continue
        stem = f[:-5]
        m = re.match(r"^(\d+)[_．.\s]*(.*)$", stem)
        num, title = (m.group(1), m.group(2)) if m else ("", stem)
        theme, figs, cn = probe(p)
        out.append(
            {
                "file": f,
                "num": num,
                "title": title,
                "kb": os.path.getsize(p) / 1024.0,
                "theme": theme,
                "figs": figs,
                "cn": cn,
            }
        )
    return out


CSS = """
:root{--ink:#0e1a2b;--ink-2:#23344a;--paper:#faf6ed;--card:#fff;--accent:#8a4a12;
--accent-deco:#b06f2d;--muted:#5b6472;--line:#d9cfbe;}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
main{max-width:1100px;margin:0 auto;padding:0 22px 64px}
header.hero{background:var(--ink);color:#f3ece0;padding:44px 22px 38px;margin-bottom:30px}
header.hero .wrap{max-width:1100px;margin:0 auto}
header.hero .kicker{letter-spacing:.22em;font-size:12px;color:var(--accent-deco);margin:0 0 12px}
header.hero h1{margin:0 0 10px;font-size:30px;letter-spacing:.02em}
header.hero p{margin:0;color:#cdd3dd;font-size:15px}
h2.sec{font-size:20px;margin:38px 0 6px;padding-bottom:8px;border-bottom:2px solid var(--accent-deco)}
h2.sec .cnt{font-size:13px;color:var(--muted);font-weight:400;margin-left:8px}
p.lede{color:var(--muted);font-size:14px;margin:8px 0 18px}
ul.chaps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
ul.chaps li{min-width:0}
a.chap{display:block;background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent-deco);
border-radius:6px;padding:13px 15px;text-decoration:none;color:var(--ink);height:100%}
a.chap:hover{border-left-color:var(--accent);background:#fffdf8}
a.chap .n{font-size:11px;color:var(--accent);letter-spacing:.14em}
a.chap .t{display:block;font-weight:600;font-size:15.5px;margin:3px 0 7px;line-height:1.45;word-break:break-word}
a.chap .meta{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
a.chap .meta span+span:before{content:"·";margin:0 6px;color:var(--line)}
.bookcard{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px 22px;margin:22px 0}
.bookcard h3{margin:0 0 6px;font-size:19px}
.bookcard p{margin:0 0 12px;color:var(--muted);font-size:14px}
.bookcard a.go{display:inline-block;background:var(--ink);color:#f3ece0;text-decoration:none;
padding:9px 18px;border-radius:4px;font-size:14px}
.note{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:6px;padding:16px 18px;margin:30px 0 0;font-size:13.5px;color:var(--ink-2)}
.note h3{margin:0 0 8px;font-size:15px}
.note ul{margin:0;padding-left:20px}
.note li{margin:5px 0}
footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media(max-width:520px){header.hero h1{font-size:23px}main{padding:0 15px 48px}}
"""


def page(title, hero_kicker, hero_h1, hero_p, body):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="hero"><div class="wrap">
<p class="kicker">{html.escape(hero_kicker)}</p>
<h1>{html.escape(hero_h1)}</h1>
<p>{hero_p}</p>
</div></header>
<main>
{body}
</main>
</body>
</html>
"""


def generic_blurb(doc, rel):
    parts = []
    author = (doc.get("author") or "").split(" (")[0].strip()
    if author:
        parts.append(author)
    subs = " / ".join(x for x in (doc.get("subjects") or []) if x)
    if subs:
        parts.append(subs)
    if doc.get("series"):
        vol = (" 第 %s 卷" % doc["volume"]) if doc.get("volume") else ""
        parts.append(doc["series"] + vol)
    if not parts:
        parts.append(rel)
    return " · ".join(parts)


def book_page(title, subtitle, blurb, rows, date):
    lis = []
    for r in rows:
        num = ('<span class="n">第 %s 章</span>' % html.escape(r["num"])) if r["num"] else ""
        lis.append(
            '<li><a class="chap" href="{f}">'
            "{num}"
            '<span class="t">{title}</span>'
            '<span class="meta"><span>{kb:.0f} KB</span><span>{figs} 个图表</span>'
            "<span>{theme}</span><span>{cn:,} 字</span></span>"
            "</a></li>".format(
                f=html.escape(r["file"]),
                num=num,
                title=html.escape(r["title"]),
                kb=r["kb"],
                figs=r["figs"],
                theme=html.escape(r["theme"]),
                cn=r["cn"],
            )
        )
    body = (
        '<h2 class="sec">章节<span class="cnt">共 {} 章</span></h2>'
        '<p class="lede">每章一页，图表为主，10 分钟读完；正文约为源文的 30–45%。</p>'
        '<ul class="chaps">{}</ul>'
        '<div class="note"><h3>使用说明</h3><ul>'
        "<li>点任意章节卡片直接打开该章的可视化阅读版（单文件 HTML，可离线看）。</li>"
        "<li>每章包含 7 个区块：封面 / 目录 / 知识地图 / 正文分区 / 今天还成立吗 / 回看 / 页脚。</li>"
        "<li>原书插图已按扫描件重建，扫描件原件收在对应图下方的折叠块里备查。</li>"
        "<li>「今天还成立吗」区分「已核验」与「基于逻辑推断」两类；详见总索引的核验说明。</li>"
        "</ul></div>"
        "<footer>来源：{book}（{sub}）｜生成日期：{date}</footer>"
    ).format(len(rows), "".join(lis), book=html.escape(title), sub=html.escape(subtitle), date=date)
    return page(f"{title} · 可视化阅读版", "CHAPTERS · 可视化阅读版", title, html.escape(blurb), body)


def top_page(books, date):
    """books: [(title, blurb, href, n)]。"""
    known = {t for t, _b, _h, _n in books}
    cards = []
    for title, blurb, href, n in books:
        cards.append(
            f'<div class="bookcard"><h3>{html.escape(title)}</h3><p>{html.escape(blurb)}</p>'
            f'<a class="go" href="{html.escape(href)}">打开 {n} 章索引 →</a></div>'
        )
    total = sum(n for _t, _b, _h, n in books)
    notes = []
    if {"竞争优势", "竞争战略"} & known:
        notes.append(
            '<div class="note"><h3>关于「今天还成立吗」的核验说明</h3><ul>'
            "<li>原书分别出版于 1980 年（《竞争战略》）与 1985 年（《竞争优势》），"
            "各章末尾设「今天还成立吗」区块，逐条标注该判断在今天是否仍然成立。</li>"
            "<li>标注分两类：<b>已核验</b>（生成时依据当时可用的联网检索结果）与"
            "<b>基于逻辑推断</b>（无公开证据，仅按机制推演）。</li>"
            "<li><b>已知限制</b>：本次批量的后半程检索插件不可用（HTTP 402 余额不足），"
            "因此《竞争战略》第 14–17 章的全部条目、以及部分章节的若干条目只能标为"
            "「基于逻辑推断」；已核验条目也未能逐条二次复核。引用具体数字前请自行核对原始来源。</li>"
            "<li>页脚日期为生成日期，不代表内容的事实核验日期。</li>"
            "</ul></div>"
        )
    if "竞争战略" in known:
        notes.append(
            '<div class="note"><h3>一处已知的体例例外</h3><ul>'
            "<li>《竞争战略》附录 1「业务组合竞争分析方法」源文仅 2,821 字，"
            "而页面必备区块（封面／目录／知识地图／回看等）本身就要占掉同等量级的文字，"
            "因此该页正文比高于其他章节的 30–45% 目标。</li>"
            "<li>这是刻意保留：如硬压到 45% 就必须删掉必备区块或原书结论，得不偿失。"
            "该页的 BCG 四象限与 GE 九宫格均已按原书扫描件重建。</li>"
            "</ul></div>"
        )
    body = (
        f'<h2 class="sec">有阅读版的书<span class="cnt">共 {len(books)} 本 · {total} 章</span></h2>'
        + "".join(cards)
        + "".join(notes)
        + f"<footer>生成日期：{date}｜本页与各书索引由 book-corpus render 重建</footer>"
    )
    return page(
        "书库 · 章节可视化阅读版",
        "LIBRARY · 章节可视化阅读版",
        "书库 · 章节可视化阅读版",
        "把库里有可视化阅读版的书逐章做成一页图表为主、10 分钟读完的 HTML。",
        body,
    )


def main():
    books = iter_html_books()
    made, chapters = [], 0
    for rel, doc, html_dir in books:
        title = doc.get("title") or os.path.basename(rel)
        rows = chapter_rows(html_dir)
        copy = BOOK_COPY.get(title)
        subtitle = copy["subtitle"] if copy else (doc.get("author") or title)
        blurb = copy["blurb"] if copy else generic_blurb(doc, rel)
        out = os.path.join(html_dir, "index.html")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(book_page(title, subtitle, blurb, rows, DATE))
        os.replace(tmp, out)
        href = "../books/" + rel.replace(os.sep, "/") + "/html/index.html"
        made.append((title, blurb, href, len(rows)))
        chapters += len(rows)
        print("%s  (%d 章)" % (out, len(rows)))
    # 两本老书排在前面，其余按书名
    made.sort(key=lambda x: (KNOWN_ORDER.index(x[0]) if x[0] in KNOWN_ORDER else 99, x[0]))
    tmp = TOP_INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(top_page(made, DATE))
    os.replace(tmp, TOP_INDEX)
    print("%s  (总索引，%d 本 %d 章)" % (TOP_INDEX, len(made), chapters))


if __name__ == "__main__":
    main()
