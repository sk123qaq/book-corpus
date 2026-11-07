# ruff: noqa: E501
"""视觉读本的确定性渲染器（xerox 模板）。

正文块逐字来自 mdparse；编辑层只提供导语、分节、图注、图表、旁批和
读后要点。渲染器不做任何改写，也不接受外部资源。
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from bookcorpus.reading.edition.content import ContentError

_HERE = Path(__file__).with_name("styles")
BASE_CSS = (_HERE / "base.css").read_text(encoding="utf-8")
XEROX_CSS = (_HERE / "xerox.css").read_text(encoding="utf-8")

EDITION_CSS = r"""
.edition-toc { display:block; margin:0 0 34px;
  border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:10px 0; }
.edition-toc .toc-title { display:block; margin-bottom:9px; font:12px/1.4 var(--mono); letter-spacing:.14em; color:var(--muted); }
.edition-toc ol { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px 30px; list-style:none; margin:0; padding:0; }
.edition-toc a { color:var(--ink); text-decoration:none; font-size:13px; }
.edition-toc a:hover { color:var(--accent); }
.source-table { width:100%; border-collapse:collapse; margin:10px 0 18px; font-size:14px; }
.source-table th, .source-table td { border-bottom:1px solid var(--rule); padding:7px 10px; text-align:left; vertical-align:top; }
.source-table th { background:var(--table-head); font-weight:600; }
.source-code { overflow:auto; background:var(--plate); border:1px solid var(--rule); padding:12px 14px; font:13px/1.6 var(--mono); }
.callout { margin:22px 0; border-left:3px solid var(--accent-2); background:var(--hot-bg); padding:14px 18px; }
.callout-kicker { display:block; font:11px/1.4 var(--mono); letter-spacing:.12em; color:var(--accent-2); margin-bottom:5px; }
.callout h3 { margin:0 0 5px; font-size:16px; }
.callout p { margin:0; font-size:14px; line-height:1.75; }
.chart-card { margin:24px 0; }
.chart-card h3 { margin:0 0 4px; font-size:16px; }
.chart-caption { margin:4px 0 10px; color:var(--muted); font-size:12px; }
.chart-note { margin:6px 0 0; color:var(--muted); font-size:11px; }
.bars { display:grid; gap:8px; }
.bars-row { display:grid; grid-template-columns:minmax(90px,150px) 1fr 52px; gap:10px; align-items:center; }
.bars-label { font-size:13px; text-align:right; color:var(--ink); }
.bar-track { height:16px; background:var(--bar-bg); position:relative; }
.bar-fill { height:100%; background:var(--accent); }
.bars-row.hot .bar-fill { background:var(--accent-2); }
.bars-value { font:12px/1 var(--mono); color:var(--muted); }
.ratio-block { margin:24px 0; border-top:2px solid var(--ink); border-bottom:1px solid var(--rule); padding:14px 0; }
.ratio-title { margin:0 0 4px; font-size:16px; }
.ratio-bars { display:grid; gap:7px; margin-top:10px; }
.ratio-row { display:grid; grid-template-columns:minmax(90px,150px) 1fr 44px; gap:10px; align-items:center; }
.ratio-row span:first-child { font-size:13px; text-align:right; }
.ratio-row .bar-track { height:14px; }
.ratio-row .bar-fill { background:var(--accent); }
.ratio-row.loss .bar-fill { background:var(--accent-2); }
.takeaways { margin:36px 0; }
.takeaways-list { display:grid; grid-template-columns:1fr 1fr; gap:12px 22px; list-style:none; padding:0; }
.takeaways-list li { display:flex; gap:10px; }
.takeaways-list strong { font-size:14px; }
.takeaways-list p { margin:3px 0 0; font-size:13px; line-height:1.7; color:var(--muted); }
@media (max-width: 720px) {
  .callout, .chart-card, .ratio-block { margin:18px 0; }
  .bars-row { grid-template-columns:96px 1fr 44px; }
  .takeaways-list { grid-template-columns:1fr; }
  .edition-toc { display:block; }
  .edition-toc ol { display:grid; grid-template-columns:1fr; gap:5px; margin-top:8px; }
  .source-table { display:block; overflow-x:auto; }
}
@media print {
  .edition-toc { break-after:avoid; }
  .chart-card, .plate, .callout, .takeaways { break-inside:avoid; }
}
"""


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _asset(uid: str, rel: str | None, resolver: Callable[[str], str] | None) -> str:
    if not rel:
        return ""
    if resolver is not None:
        return resolver(rel)
    return "/api/asset?uid=%s&rel=%s" % (quote(uid), quote(rel))


PSEUDO_HEAD_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.、]\s*\S")


def _paragraph(block: dict) -> str:
    text = block.get("text", "")
    tag = "h3" if len(text) <= 40 and PSEUDO_HEAD_RE.match(text) else "p"
    css = "source-h3" if tag == "h3" else "source"
    return '<%s class="%s" id="%s" data-block="%s">%s</%s>' % (
        tag,
        css,
        _escape(block["id"]),
        _escape(block["id"]),
        _escape(text),
        tag,
    )


def _heading(block: dict) -> str:
    level = str(block.get("level") or 2)
    text = block.get("text", "")
    return '<h%s class="source-h2" id="%s" data-block="%s">%s</h%s>' % (
        level,
        _escape(block["id"]),
        _escape(block["id"]),
        _escape(text),
        level,
    )


def _quote(block: dict) -> str:
    return '<blockquote class="source-quote" id="%s" data-block="%s"><p>%s</p></blockquote>' % (
        _escape(block["id"]),
        _escape(block["id"]),
        _escape(block.get("text")),
    )


def _list(block: dict) -> str:
    items = block.get("items") or [block.get("text", "")]
    body = "".join("<li>%s</li>" % _escape(item) for item in items)
    return '<ul class="source-list" id="%s" data-block="%s">%s</ul>' % (
        _escape(block["id"]),
        _escape(block["id"]),
        body,
    )


def _table(block: dict) -> str:
    rows = block.get("rows") or []
    head = block.get("head")
    parts = [
        '<div class="table-wrap"><table class="source-table" id="%s" data-block="%s">'
        % (_escape(block["id"]), _escape(block["id"]))
    ]
    if head:
        parts.append("<thead><tr>%s</tr></thead>" % "".join("<th>%s</th>" % _escape(cell) for cell in head))
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _escape(cell) for cell in row))
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _code(block: dict) -> str:
    lang = block.get("lang") or ""
    return '<pre class="source-code" id="%s" data-block="%s" data-lang="%s"><code>%s</code></pre>' % (
        _escape(block["id"]),
        _escape(block["id"]),
        _escape(lang),
        _escape(block.get("text")),
    )


def _plate(block: dict, spec: dict | None, uid: str, resolver: Callable[[str], str] | None, number: int) -> str:
    spec = spec or {}
    src = _asset(uid, block.get("rel"), resolver)
    title = spec.get("title") or block.get("fig_title") or ""
    caption = spec.get("caption") or block.get("fig_caption") or ""
    note = spec.get("note") or ""
    label = "图 %02d" % number
    head = '<div class="plate-head"><span class="plate-no">%s</span><span class="plate-kicker">原文图</span>' % label
    if title:
        head += '<h3 class="plate-title">%s</h3>' % _escape(title)
    head += "</div>"
    if src:
        body = '<div class="plate-body"><img src="%s" alt="%s" loading="lazy"></div>' % (
            _escape(src),
            _escape(caption or title or label),
        )
    else:
        body = '<div class="plate-body missing">图片缺失</div>'
    tail = ""
    if caption:
        tail += '<figcaption class="plate-caption">%s</figcaption>' % _escape(caption)
    if note:
        tail += '<p class="plate-note">%s</p>' % _escape(note)
    return '<figure class="plate" id="%s" data-block="%s">%s%s%s</figure>' % (
        _escape(block["id"]),
        _escape(block["id"]),
        head,
        body,
        tail,
    )


def _block_html(
    block: dict,
    *,
    uid: str,
    figures: dict[str, dict],
    resolver: Callable[[str], str] | None,
    plate_numbers: dict[str, int],
) -> str:
    kind = block.get("kind")
    if kind == "paragraph":
        return _paragraph(block)
    if kind == "heading":
        return _heading(block)
    if kind == "quote":
        return _quote(block)
    if kind == "list":
        return _list(block)
    if kind == "table":
        return _table(block)
    if kind == "code":
        return _code(block)
    if kind == "image":
        return _plate(block, figures.get(block["id"]), uid, resolver, plate_numbers.get(block["id"], 1))
    return '<p class="source missing">%s</p>' % _escape(block.get("text"))


def _bars(chart: dict) -> str:
    data = chart["data"]
    values = data["values"]
    top = max(values) or 1
    rows = []
    for index, (label, value) in enumerate(zip(data["labels"], values, strict=False)):
        pct = max(2.0, value / top * 100.0)
        hot = " hot" if index == data.get("highlight") else ""
        rows.append(
            '<div class="bars-row%s"><span class="bars-label">%s</span>'
            '<div class="bar-track"><div class="bar-fill" style="width:%.1f%%"></div></div>'
            '<span class="bars-value">%s%s</span></div>'
            % (hot, _escape(label), pct, _escape(_number(value)), _escape(data.get("unit", "")))
        )
    return '<figure class="chart-card"><h3>%s</h3>%s<div class="bars">%s</div>%s%s</figure>' % (
        _escape(chart["title"]),
        _caption(chart),
        "".join(rows),
        _note(chart),
        "",
    )


def _ratio(chart: dict) -> str:
    data = chart["data"]
    top = max(data["left_value"], data["right_value"]) or 1
    left = data["left_value"] / top * 100.0
    right = data["right_value"] / top * 100.0
    return (
        '<figure class="chart-card"><h3>%s</h3>%s'
        '<div class="ratio-block"><div class="ratio-bars">'
        '<div class="ratio-row"><span>%s</span>'
        '<div class="bar-track"><div class="bar-fill" style="width:%.1f%%"></div></div>'
        '<span class="bars-value">%s</span></div>'
        '<div class="ratio-row loss"><span>%s</span>'
        '<div class="bar-track"><div class="bar-fill" style="width:%.1f%%"></div></div>'
        '<span class="bars-value">%s</span></div>'
        "</div>%s</div>%s</figure>"
        % (
            _escape(chart["title"]),
            _caption(chart),
            _escape(data["left_label"]),
            left,
            _escape(_number(data["left_value"])),
            _escape(data["right_label"]),
            right,
            _escape(_number(data["right_value"])),
            '<p class="chart-note">%s</p>' % _escape(data["note"]) if data.get("note") else "",
            _note(chart),
        )
    )


def _compare(chart: dict) -> str:
    data = chart["data"]
    head = "".join("<th>%s</th>" % _escape(cell) for cell in data["headers"])
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _escape(cell) for cell in row) for row in data["rows"])
    return (
        '<figure class="chart-card"><h3>%s</h3>%s'
        '<div class="compare-wrap"><table class="compare"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
        "%s</figure>" % (_escape(chart["title"]), _caption(chart), head, body, _note(chart))
    )


def _steps(chart: dict) -> str:
    items = []
    for index, step in enumerate(chart["data"]["steps"], 1):
        items.append(
            '<li><span class="flow-no">%02d</span><strong>%s</strong><p>%s</p></li>'
            % (index, _escape(step["title"]), _escape(step["text"]))
        )
    return '<figure class="chart-card"><h3>%s</h3>%s<ol class="flow">%s</ol>%s</figure>' % (
        _escape(chart["title"]),
        _caption(chart),
        "".join(items),
        _note(chart),
    )


def _caption(chart: dict) -> str:
    caption = chart.get("caption") or ""
    return '<p class="chart-caption">%s</p>' % _escape(caption) if caption else ""


def _note(chart: dict) -> str:
    note = chart.get("note") or ""
    return '<p class="chart-note">%s</p>' % _escape(note) if note else ""


def _number(value: float) -> str:
    return ("%.2f" % value).rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _chart_html(chart: dict) -> str:
    if chart["kind"] == "bars":
        return _bars(chart)
    if chart["kind"] == "ratio":
        return _ratio(chart)
    if chart["kind"] == "compare":
        return _compare(chart)
    return _steps(chart)


def _callout_html(callout: dict) -> str:
    kicker = callout.get("kicker") or "旁批"
    return '<aside class="callout"><span class="callout-kicker">%s</span><h3>%s</h3><p>%s</p></aside>' % (
        _escape(kicker),
        _escape(callout["title"]),
        _escape(callout["text"]),
    )


def _takeaways_html(takeaways: dict | None) -> str:
    if not takeaways:
        return ""
    items = "".join(
        '<li><span class="take-num">%02d</span><div><strong>%s</strong><p>%s</p></div></li>'
        % (index, _escape(item["title"]), _escape(item["text"]))
        for index, item in enumerate(takeaways["items"], 1)
    )
    return (
        '<section class="takeaways"><div class="takeaways-head"><span class="takeaways-kicker">读后</span>'
        '<h2>%s</h2></div><ol class="takeaways-list">%s</ol></section>' % (_escape(takeaways["title"]), items)
    )


def _footnotes_html(footnotes: list[dict]) -> str:
    if not footnotes:
        return ""
    items = "".join(
        '<li id="fn-%s" data-block="%s"><span class="fn-num">%s</span><p>%s</p></li>'
        % (
            _escape(block.get("marker") or index),
            _escape(block["id"]),
            _escape(block.get("marker") or index),
            _escape(block.get("text")),
        )
        for index, block in enumerate(footnotes, 1)
    )
    return '<section class="footnotes"><h2>注释</h2><ol>%s</ol></section>' % items


def _overview_html(overview: list[dict]) -> str:
    if not overview:
        return ""
    cards = "".join(
        '<article><span class="ov-no">%s</span><h3>%s</h3><p>%s</p></article>'
        % (_escape(item["no"]), _escape(item["title"]), _escape(item["text"]))
        for item in overview
    )
    cols = max(1, min(4, len(overview)))
    return '<section class="overview" style="grid-template-columns:repeat(%d,minmax(0,1fr))">%s</section>' % (
        cols,
        cards,
    )


def _toc_html(sections: list[dict]) -> str:
    if len(sections) < 2:
        return ""
    items = "".join(
        '<li><a href="#%s"><span>%s</span></a></li>' % (_escape(section["id"]), _escape(section["title"]))
        for section in sections
    )
    return '<nav class="edition-toc"><span class="toc-title">目录</span><ol>%s</ol></nav>' % items


def render_edition(
    book: dict,
    chapter: dict,
    blocks: list[dict],
    content: dict,
    *,
    resolver: Callable[[str], str] | None = None,
    generated_at: str | None = None,
) -> str:
    """把一个章、一份编辑层渲染成单文件 HTML。"""
    if content.get("schema") != 1:
        raise ContentError("不支持的编辑层 schema")
    template = content.get("template", "xerox")
    if template != "xerox":
        raise ContentError("未知模板 %s" % template)
    uid = book.get("uid", "")
    title = chapter.get("title") or chapter.get("h1") or "未命名"
    hero = content["hero"]
    title_lines = hero.get("title_lines") or [title]
    title_len = max((len(line) for line in title_lines), default=0)
    title_class = "hero-title-long" if title_len > 8 else ""
    title_class += " hero-title-xlong" if title_len > 11 else ""
    hero_title = "<br>".join(_escape(line) for line in title_lines)
    kicker = hero.get("kicker") or "视觉读本"
    lead = hero.get("lead") or ""
    author = book.get("author") or ""
    han = chapter.get("body_han") or sum(block.get("chars", 0) for block in blocks)
    read_min = max(1, round(han / 400))
    plates = sum(1 for block in blocks if block.get("kind") == "image")
    image_blocks = [block for block in blocks if block.get("kind") == "image"]
    plate_numbers = {block["id"]: index for index, block in enumerate(image_blocks, 1)}

    figure_map = content.get("figures") or {}
    before_map: dict[str, list[str]] = {}
    after_map: dict[str, list[str]] = {}
    for chart in content.get("charts") or []:
        target = after_map if chart.get("after", True) else before_map
        target.setdefault(chart["anchor"], []).append(_chart_html(chart))
    for callout in content.get("callouts") or []:
        target = after_map if callout.get("after", True) else before_map
        target.setdefault(callout["anchor"], []).append(_callout_html(callout))
    section_map = {section["anchor"]: section for section in content.get("sections") or []}

    chunks: list[str] = []
    footnotes: list[dict] = []
    open_section = False
    for block in blocks:
        if block.get("kind") == "footnote":
            footnotes.append(block)
            continue
        if block["id"] in before_map:
            chunks.extend(before_map[block["id"]])
        section = section_map.get(block["id"])
        skip_source = False
        if section is not None:
            if open_section:
                chunks.append("</section>")
            skip_source = block.get("kind") == "heading" and block.get("text", "").strip() == section["title"].strip()
            anchor_attr = ' data-block="%s"' % _escape(block["id"]) if skip_source else ""
            chunks.append(
                '<section class="chapter-section" id="%s"><div class="section-head"><span class="section-num">%s</span>'
                "<div><h2%s>%s</h2>%s</div></div>"
                % (
                    _escape(section["id"]),
                    _escape(section["id"][1:]),
                    anchor_attr,
                    _escape(section["title"]),
                    '<p class="section-intro">%s</p>' % _escape(section["intro"]) if section.get("intro") else "",
                )
            )
            open_section = True
        elif not open_section:
            chunks.append('<section class="chapter-section">')
            open_section = True
        if not skip_source:
            chunks.append(
                _block_html(
                    block,
                    uid=uid,
                    figures=figure_map,
                    resolver=resolver,
                    plate_numbers=plate_numbers,
                )
            )
        if block["id"] in after_map:
            chunks.extend(after_map[block["id"]])
    if open_section:
        chunks.append("</section>")
    if content.get("takeaways"):
        chunks.append(_takeaways_html(content["takeaways"]))
    chunks.append(_footnotes_html(footnotes))
    if generated_at:
        chunks.append('<p class="colophon">%s · 生成于 %s</p>' % (_escape(template), _escape(generated_at)))
    else:
        chunks.append('<p class="colophon">%s · 原文逐字插入</p>' % _escape(template))

    foot_left = hero.get("foot_left") or "原文逐字 · 编辑层加注"
    body = "".join(chunks)
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>%s · %s</title><style>%s\n%s\n%s</style></head><body>"
        '<main class="page"><header class="hero"><div class="hero-meta"><span class="kicker">%s</span>'
        '<span class="hero-dot">◆</span><span>%s</span></div><div class="hero-grid"><div class="hero-copy">'
        '%s<p class="dek">%s</p><p class="byline">%s</p></div><figure class="hero-figure">%s</figure></div>'
        '<div class="hero-foot"><span>%s</span><span>%s 字 · 约 %d 分钟 · %d 张原文图</span></div></header>'
        '%s%s<div class="edition-body">%s</div></main></body></html>'
        % (
            _escape(title),
            _escape(book.get("title") or ""),
            BASE_CSS,
            XEROX_CSS,
            EDITION_CSS,
            _escape(kicker),
            _escape(author),
            '<h1 class="%s">%s</h1>' % (title_class.strip(), hero_title),
            _escape(lead),
            _escape(book.get("title") or ""),
            _mini_curve(),
            _escape(foot_left),
            _escape(str(han)),
            read_min,
            plates,
            _toc_html(content.get("sections") or []),
            _overview_html(content.get("overview") or []),
            body,
        )
    )


def _mini_curve() -> str:
    return (
        '<svg class="chart-svg" viewBox="0 0 560 300" role="img" aria-label="损失与获益的价值函数示意">'
        '<path class="svg-axis" d="M70 40 V260 H510"/>'
        '<path class="svg-guide" d="M70 175 H510"/>'
        '<path class="svg-curve gain" d="M70 175 C150 174 250 150 510 92"/>'
        '<path class="svg-curve loss" d="M70 175 C120 176 165 205 220 255"/>'
        '<circle class="svg-dot" cx="70" cy="175" r="4"/>'
        '<text class="svg-label" x="80" y="196">参照点</text>'
        '<text class="svg-small" x="420" y="80">获益</text>'
        '<text class="svg-small" x="120" y="250">损失</text></svg>'
    )
