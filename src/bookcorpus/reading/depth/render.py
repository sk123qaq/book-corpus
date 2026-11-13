"""深读页渲染：同一章两棵正文树，速览在上，原文在下。

速览是编译体，标准/深读两档共用逐字原文。没有 JavaScript 时两棵树
同时可见；有 JavaScript 时按三个页签切换。所有原文块都经过旧渲染器的
:func:`_block_element`，本模块不改动它，也不丢块。
"""

import html

from ..render import READER_CSS, _block_element
from .diagrams import render_figure
from .standard import build_standard

DEPTH_RENDER_VERSION = 1

DEEP_TYPE_LABELS = {
    "assumption": "前提",
    "boundary": "边界",
    "evidence": "证据",
    "counter": "反例",
    "intertext": "互文",
    "scene": "场景",
    "character": "人物",
    "motif": "母题",
    "turn": "转折",
    "setup_payoff": "埋伏与回收",
    "step": "步骤",
    "check": "检查",
    "failure": "失败",
    "decision": "决策",
    "example": "例证",
    "plate": "图版",
    "term": "术语",
    "version": "版本",
    "caption": "图注",
    "provenance": "来源",
}
DEPTH_CSS = """
.mode-chip{display:inline-block;border:1px solid #2c5a44;color:#2c5a44;font:10px/1 sans-serif;
  letter-spacing:.08em;padding:5px 7px;margin-left:14px;vertical-align:6px}
.depth-tabs{display:flex;align-items:stretch;gap:2px}
.depth-tab{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:12px 18px 10px;
border-bottom:2px solid transparent}
.depth-tab b{font-family:var(--sans);font-size:13px;letter-spacing:.06em}
.depth-tab small{font-family:var(--sans);font-size:10px;color:var(--ink-3)}
.depth-tab[aria-selected="true"]{border-bottom-color:var(--pine)}
.depth-tab[aria-selected="true"] b{color:var(--pine)}
.depth-panel{display:block}
.depth-panel + .depth-panel{margin-top:56px;padding-top:40px;border-top:1px solid var(--rule-strong)}
.depth-head{border-bottom:1px solid var(--rule);padding-bottom:22px;margin-bottom:28px}
.depth-head h2{font-size:26px;line-height:1.4;margin:0 0 10px;font-weight:600}
.depth-head p{margin:0;font-family:var(--sans);font-size:12px;line-height:1.8;color:var(--ink-2)}
.depth-head .depth-warn{color:var(--ochre)}
.skim-meta{display:inline-flex;flex-wrap:wrap;gap:8px;margin-top:12px;padding:7px 11px;border:1px solid var(--rule);
background:var(--paper-2);font-family:var(--sans);font-size:12px;color:var(--ink-2)}
.skim-section{margin:0 0 34px}
.skim-section h3{font-size:20px;line-height:1.5;margin:0 0 10px;padding-left:12px;border-left:4px solid var(--pine)}
.skim-lead{font-size:17px;line-height:1.85;margin:0 0 14px;color:var(--ink-2)}
.skim-claims{margin:0;padding:0;list-style:none;counter-reset:skim-claim}
.skim-claim{position:relative;margin:0 0 15px;padding-left:36px;font-size:17.5px;line-height:1.85}
.skim-claim:before{counter-increment:skim-claim;content:counter(skim-claim,decimal-leading-zero);position:absolute;left:0;top:.28em;
font-family:var(--sans);font-size:11px;letter-spacing:.06em;color:var(--cinnabar)}
.skim-anchor{display:inline-block;margin-left:8px;font-family:var(--sans);font-size:10.5px;letter-spacing:.05em;
color:var(--ink-3);text-decoration:none;border-bottom:1px dotted var(--rule-strong);vertical-align:.1em}
.skim-anchor:hover{color:var(--pine);border-bottom-color:var(--pine)}
.skim-turn{margin:0 0 16px;padding:11px 14px;border-left:3px solid var(--ochre);background:var(--paper-2);
font-size:16px;line-height:1.8;color:var(--ink-2)}
.skim-turn b{display:block;font-family:var(--sans);font-size:10px;letter-spacing:.16em;color:var(--ochre);
margin-bottom:3px}
.skim-actions{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 8px}
.skim-source-btn{font-family:var(--sans);font-size:12px;padding:8px 13px;
border:1px solid var(--rule-strong);color:var(--ink-2)}
.skim-source-btn:hover{border-color:var(--pine);color:var(--pine)}
.skim-index{margin-top:26px;border-top:1px solid var(--rule);padding-top:14px}
.skim-index summary{font-family:var(--sans);font-size:12px;letter-spacing:.05em;color:var(--ink-2);cursor:pointer}
.skim-index ul{margin:12px 0 0;padding:0;list-style:none}
.skim-index li{margin:0 0 9px;font-family:var(--sans);font-size:12px;line-height:1.75;color:var(--ink-2)}
.skim-index .skim-label{display:inline-block;min-width:72px;color:var(--ink-3)}
.skim-index q{quotes:"「" "」";color:var(--ink-3)}
.skim-unsupported{padding:20px 22px;border:1px solid var(--rule);background:var(--paper-2)}
.skim-unsupported p{margin:0 0 8px;font-size:17px;line-height:1.8}
.skim-unsupported .skim-reason{font-family:var(--sans);font-size:12px;color:var(--ink-3)}
.depth-source-note{margin:0 0 24px;padding:10px 14px;border-left:3px solid var(--ochre);background:var(--paper-2);
font-family:var(--sans);font-size:12px;line-height:1.8;color:var(--ink-2)}
.argument-note{margin:8px 0 20px;padding:11px 15px;border:1px solid var(--rule);border-left:3px solid var(--cinnabar);
background:var(--paper-2);font-family:var(--sans);font-size:13px;line-height:1.8;color:var(--ink-2)}
.argument-type{display:inline-block;margin-right:8px;padding:1px 6px;border:1px solid var(--rule-strong);
font-size:10px;letter-spacing:.12em;color:var(--cinnabar)}
.argument-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:11px;color:var(--ink-3)}
.argument-meta a,.deep-anchors a{color:var(--pine);border-bottom:1px dotted var(--pine)}
.deep-chain{margin:26px 0 30px}
.deep-chain-fallback{margin:10px 0 0;font-size:13px;line-height:1.8;color:var(--ochre)}
body.only-source [data-origin="added"]{display:none!important}
html.depth-js [data-depth-tiers]{display:none!important}
html.depth-js body[data-depth-tab="skim"] [data-depth-tiers~="skim"],
html.depth-js body[data-depth-tab="standard"] [data-depth-tiers~="standard"],
html.depth-js body[data-depth-tab="deep"] [data-depth-tiers~="deep"]{display:block!important}
html.depth-js section[data-body][hidden]{display:none}
.panels-block{margin:44px 0 0;border-top:1px solid var(--rule-strong);padding-top:26px}
.panels-block h2{font-size:22px;line-height:1.5;margin:0 0 6px;font-weight:600}
.panels-block .panels-lede{font-family:var(--sans);font-size:12px;color:var(--ink-3);margin:0 0 18px}
.panel-card{margin:0 0 26px;padding:16px 18px;border:1px solid var(--rule);background:var(--paper-2)}
.panel-card h3{font-size:18px;line-height:1.5;margin:0 0 1px}
.panel-card .panel-kind{font-family:var(--sans);font-size:10px;letter-spacing:.12em;color:var(--cinnabar)}
.panel-card p{font-size:15px;line-height:1.8;margin:8px 0}
.panel-items{list-style:none;margin:10px 0 0;padding:0}
.panel-items li{font-size:15px;line-height:1.8;margin:7px 0;padding-left:18px;position:relative}
.panel-items li:before{content:"·";position:absolute;left:4px;color:var(--cinnabar)}
.panel-items .panel-anchor{font-family:var(--sans);font-size:10px;color:var(--ink-3);margin-left:6px}
.scene-list{list-style:none;margin:0;padding:0;counter-reset:scene}
.scene-item{position:relative;margin:0 0 20px;padding-left:42px;font-size:17px;line-height:1.9}
.scene-item:before{counter-increment:scene;content:counter(scene,decimal-leading-zero);position:absolute;left:0;top:.25em;
font-family:var(--sans);font-size:11px;letter-spacing:.06em;color:var(--cinnabar)}
.scene-turn{display:block;margin-top:7px;font-family:var(--sans);font-size:12px;line-height:1.7;color:var(--ochre)}
.scene-anchor{font-family:var(--sans);font-size:10px;color:var(--ink-3);margin-left:7px;
text-decoration:none;border-bottom:1px dotted var(--rule-strong)}
.panels-empty{margin:16px 0;padding:14px 16px;border:1px solid var(--rule);background:var(--paper-2);
font-family:var(--sans);font-size:12px;color:var(--ink-3)}
.flow-row[id]{scroll-margin-top:84px}
.section-lead{margin:30px 0 18px;padding:2px 0 2px 14px;border-left:4px solid var(--pine);
font-size:17px;line-height:1.8;color:var(--ink-2)}
.term-note,.concept-note{margin:10px 0 22px;padding:11px 15px;border:1px solid var(--rule);
background:var(--paper-2);font-family:var(--sans);font-size:13px;line-height:1.8;color:var(--ink-2)}
.term-note b{display:inline-block;margin-right:8px;color:var(--cinnabar);letter-spacing:.04em}
.concept-note .note-tag{display:inline-block;margin-right:8px;padding:1px 6px;border:1px solid var(--rule-strong);
font-size:10px;letter-spacing:.12em;color:var(--ochre)}
.fold{margin:16px 0;border:1px solid var(--rule);background:var(--paper-2)}
.fold summary{padding:9px 14px;font-family:var(--sans);font-size:12px;letter-spacing:.04em;
color:var(--ink-2);cursor:pointer}
.fold[open] summary{border-bottom:1px solid var(--rule)}
.fold .flow-row{margin:0;padding:2px 14px 10px}
.depth-figure{margin:26px 0 30px;padding:16px 18px;border:1px solid var(--rule-strong);
background:var(--paper-2);break-inside:avoid;overflow-x:auto}
.depth-figure figcaption{margin:0 0 12px;padding-bottom:9px;border-bottom:1px solid var(--rule)}
.depth-figure figcaption b{display:block;font-size:16px;line-height:1.6;color:var(--ink)}
.depth-figure figcaption span{display:block;margin-top:3px;font-family:var(--sans);font-size:11px;color:var(--ink-3)}
.fig-nodes{margin:0;padding-left:22px;font-size:15px;line-height:2}
.fig-nodes a{color:var(--pine);border-bottom:1px dotted var(--pine)}
.fig-edges{margin:10px 0 0;font-family:var(--sans);font-size:11px;line-height:1.9;color:var(--ink-3)}
.fig-table{overflow-x:auto}
.fig-table table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.7}
.fig-table th,.fig-table td{padding:6px 9px;border:1px solid var(--rule);text-align:left;vertical-align:top}
.fig-table th{background:var(--paper);font-family:var(--sans);font-size:12px}
.fig-bars{margin:0}
.fig-bar{display:grid;grid-template-columns:minmax(5em,28%) 1fr auto;align-items:center;gap:9px;margin:0 0 8px;
font-family:var(--sans);font-size:12px}
.fig-bar-track{display:block;height:10px;background:var(--paper);border:1px solid var(--rule)}
.fig-bar-fill{display:block;height:100%;background:var(--pine)}
.fig-bar-value{min-width:3em;text-align:right;color:var(--ink-2)}
.fig-fallback{margin:0 0 10px;font-size:14px;line-height:1.8;color:var(--ochre)}
.fig-units{margin:0;padding-left:22px;font-size:14px;line-height:1.9}
.fig-units a{color:var(--pine);border-bottom:1px dotted var(--pine)}
@media (max-width:640px){.depth-figure{margin-left:-8px;margin-right:-8px;padding:12px}
.fig-bar{grid-template-columns:minmax(4em,34%) 1fr auto}}
@media print{.reader-top,.tierbar,.skim-actions{display:none!important}
section[data-body=source]{display:block!important}
.fold{break-inside:auto}.fold summary{display:none}
.fold > :not(summary){display:block!important}
.depth-figure{overflow:visible;break-inside:avoid}
.depth-panel + .depth-panel{margin-top:0;padding-top:0;border-top:0}
.skim-anchor{border:0}.skim-index{display:block}.skim-index summary{display:none}}
.flash{animation:depth-flash 900ms ease-out}
@keyframes depth-flash{0%{background:var(--note-bg)}100%{background:transparent}}
"""

DEPTH_JS = """
document.documentElement.classList.add('depth-js');
const DEPTH_TABS = ['skim', 'standard', 'deep'];
const depth$ = (s, r) => (r || document).querySelector(s);
const depth$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
function setDepthTab(tab) {
  if (!DEPTH_TABS.includes(tab)) tab = 'skim';
  const body = tab === 'skim' ? 'compiled' : 'source';
  depth$$('section[data-body]').forEach(el => {
    el.hidden = el.dataset.body !== body;
  });
  depth$$('[data-depth-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.depthTab === tab)));
  document.body.dataset.depthTab = tab;
}
function revealDepthAnchor(block) {
  setDepthTab('standard');
  const target = block ? document.getElementById(block) : null;
  if (!target) return;
  target.scrollIntoView({behavior: 'smooth', block: 'center'});
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), 900);
}
document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(location.href.split('?')[1] || '');
  setDepthTab(params.get('tier') || 'skim');
  depth$$('[data-depth-tab]').forEach(btn => btn.addEventListener('click', () => {
    setDepthTab(btn.dataset.depthTab);
    try {
      const url = new URL(location.href);
      url.searchParams.set('tier', btn.dataset.depthTab);
      history.replaceState(null, '', url);
    } catch (e) {}
  }));
  depth$$('[data-skim-anchor]').forEach(link => link.addEventListener('click', ev => {
    ev.preventDefault();
    revealDepthAnchor(link.dataset.skimAnchor || link.getAttribute('href').slice(1));
  }));
  depth$$('[data-skim-read-source]').forEach(btn => btn.addEventListener('click', () => {
    revealDepthAnchor(btn.dataset.skimAnchor || '');
  }));
  const only = depth$('#only-btn');
  if (only) only.addEventListener('click', () => {
    if (document.body.dataset.depthTab !== 'standard') setDepthTab('standard');
    const on = !document.body.classList.contains('only-source');
    document.body.classList.toggle('only-source', on);
    only.setAttribute('aria-pressed', String(on));
    if (on) depth$$('details').forEach(item => { item.open = true; });
  });
  const big = depth$('#big-btn');
  if (big) big.addEventListener('click', () => {
    const on = !document.body.classList.contains('big');
    document.body.classList.toggle('big', on);
    big.setAttribute('aria-pressed', String(on));
  });
  const night = depth$('#night-btn');
  if (night) night.addEventListener('click', () => {
    document.body.classList.toggle('night');
    night.textContent = document.body.classList.contains('night') ? '暖纸' : '夜读';
  });
  const print = depth$('#print-btn');
  if (print) print.addEventListener('click', () => window.print());
  document.addEventListener('keydown', ev => {
    if (ev.target.matches('input,textarea,select')) return;
    const index = Number(ev.key) - 1;
    if (index >= 0 && index < DEPTH_TABS.length) setDepthTab(DEPTH_TABS[index]);
  });
});
"""


def _skim_sections(skim):
    if not isinstance(skim, dict):
        return []
    return skim.get("sections") or []


def _unit_core_ids(model):
    return {unit.get("id") for unit in model.get("units") or [] if unit.get("importance") == "core"}


def _claim_unit_ids(skim):
    ids = set()
    for section in _skim_sections(skim):
        for claim in section.get("claims") or []:
            ids.update(claim.get("units") or [])
    return ids


def _section_titles(model):
    return {
        section.get("id"): section.get("title") or "本节"
        for section in model.get("sections") or []
        if section.get("id")
    }


def _unsupported_block(skim):
    reason = ""
    if isinstance(skim, dict):
        reason = skim.get("reason") or ""
    reason = reason or "本章暂未生成速览。"
    return (
        '<div class="skim-unsupported" data-skim-unsupported="true">'
        "<p>本章更适合按原文阅读</p>"
        f'<p class="skim-reason">{html.escape(reason)}</p>'
        '<div class="skim-actions"><button type="button" class="skim-source-btn" '
        "data-skim-read-source>读标准档</button></div>"
        "</div>"
    )


def _scenes_body(skim):
    scenes = skim.get("scenes") or []
    if not scenes:
        return _unsupported_block(skim)
    parts = [
        '<div class="skim-meta">场景速览 · ' + str(len(scenes)) + " 个节拍</div>",
        '<div class="skim-actions"><button type="button" class="skim-source-btn" '
        "data-skim-read-source>看原文</button></div>",
        '<ol class="scene-list">',
    ]
    for scene in scenes:
        anchor = scene.get("anchor") or ""
        link = (
            f'<a class="scene-anchor" href="#{html.escape(anchor, quote=True)}" '
            f'data-skim-anchor="{html.escape(anchor, quote=True)}">原文 {html.escape(anchor)}</a>'
            if anchor
            else ""
        )
        turn = scene.get("turn") or ""
        turn_html = f'<span class="scene-turn">{html.escape(turn)}</span>' if turn else ""
        parts.append(f'<li class="scene-item">{html.escape(scene.get("text") or "")}{link}{turn_html}</li>')
    parts.append("</ol>")
    return "".join(parts)


def _panel_items(items, limit=None):
    parts = ['<ul class="panel-items">']
    selected = items[:limit] if limit else items
    for item in selected or []:
        text = html.escape(item.get("text") or "")
        anchors = " ".join(
            f'<a class="panel-anchor" href="#{html.escape(block, quote=True)}">{html.escape(block)}</a>'
            for block in item.get("blocks") or []
        )
        quote = item.get("quote") or ""
        quote_html = f"<q>{html.escape(quote)}</q>" if quote else ""
        parts.append(f"<li>{text}{quote_html}{anchors}</li>")
    parts.append("</ul>")
    return "".join(parts)


def _panels_html(panels, tier):
    panels = panels if isinstance(panels, dict) else {}
    rows = panels.get("panels") or []
    if not rows:
        return (
            f'<div class="panels-block" data-depth-tiers="{tier}">'
            '<div class="panels-empty">本章没有足够的结构信息，面板留空。</div></div>'
        )
    parts = [f'<section class="panels-block" data-depth-tiers="{tier}" aria-label="本章面板">']
    parts.append("<h2>本章面板</h2>")
    parts.append('<p class="panels-lede">从正文抽取的结构，条目都能回到原文；不是第二份正文。</p>')
    for panel in rows:
        parts.append('<article class="panel-card">')
        parts.append(f'<div class="panel-kind">{html.escape(panel.get("kind") or "")}</div>')
        parts.append(f"<h3>{html.escape(panel.get('title') or '')}</h3>")
        if panel.get("summary"):
            parts.append(f"<p>{html.escape(panel['summary'])}</p>")
        if tier == "skim":
            parts.append('<p class="panel-more">条目在标准 / 深读档展开。</p>')
        else:
            parts.append(_panel_items(panel.get("items") or []))
        parts.append("</article>")
    parts.append("</section>")
    return "".join(parts)


def _compiled_body(skim, model):
    if not isinstance(skim, dict) or not skim.get("supported"):
        return _unsupported_block(skim)
    if skim.get("kind") == "scenes":
        return _scenes_body(skim)
    sections = _skim_sections(skim)
    if not sections:
        return _unsupported_block(skim)
    titles = _section_titles(model)
    core_ids = _unit_core_ids(model)
    covered = len(core_ids & _claim_unit_ids(skim))
    detail = len(skim.get("detail_index") or [])
    anchor = [claim.get("anchor") for item in sections for claim in item.get("claims") or [] if claim.get("anchor")]
    anchor_attr = f' data-skim-anchor="{html.escape(anchor[0], quote=True)}"' if anchor else ""
    parts = [
        f'<div class="skim-meta">编译版 · 核心命题 {covered}/{len(core_ids)} · 细节 {detail} 条</div>',
        '<div class="skim-actions"><button type="button" class="skim-source-btn" data-skim-read-source'
        f"{anchor_attr}>看原文</button></div>",
    ]
    for section in sections:
        title = titles.get(section.get("id")) or "本节"
        parts.append('<section class="skim-section">')
        parts.append(f"<h3>{html.escape(title)}</h3>")
        if section.get("lead"):
            parts.append(f'<p class="skim-lead">{html.escape(section["lead"])}</p>')
        parts.append('<ol class="skim-claims">')
        for claim in section.get("claims") or []:
            anchor = claim.get("anchor") or ""
            link = ""
            if anchor:
                link = (
                    f'<a class="skim-anchor" href="#{html.escape(anchor, quote=True)}" '
                    f'data-skim-anchor="{html.escape(anchor, quote=True)}">原文 {html.escape(anchor)}</a>'
                )
            parts.append(f'<li class="skim-claim">{html.escape(claim.get("text") or "")}{link}</li>')
        parts.append("</ol>")
        if section.get("turn"):
            parts.append(f'<p class="skim-turn"><b>转折</b>{html.escape(section["turn"])}</p>')
        parts.append("</section>")
    details = skim.get("detail_index") or []
    if details:
        parts.append(f'<details class="skim-index"><summary>细节索引 · {len(details)} 条</summary><ul>')
        for entry in details:
            anchor = entry.get("anchor") or ""
            link = (
                f'<a class="skim-anchor" href="#{html.escape(anchor, quote=True)}" '
                f'data-skim-anchor="{html.escape(anchor, quote=True)}">{html.escape(anchor)}</a>'
                if anchor
                else ""
            )
            parts.append(
                "<li>"
                f'<span class="skim-label">{html.escape(entry.get("label") or "细节")}</span>'
                f"{link} <q>{html.escape(entry.get('quote') or '')}</q></li>"
            )
        parts.append("</ul></details>")
    return "".join(parts)


def _source_row(block, resolver):
    element = _block_element(block, {"standard", "deep"}, resolver)
    block_id = block.get("id") or ""
    row_id = f' id="{html.escape(block_id, quote=True)}"' if block.get("kind") != "heading" else ""
    return f'<div class="flow-row" data-block="{html.escape(block_id, quote=True)}"{row_id}>{element}</div>'


def _section_units(model):
    """节 -> 单元；优先模型 section.unit_ids，缺时按 unit.section 反查。"""
    sections = model.get("sections") or []
    units = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    result = {}
    for section in sections:
        unit_ids = list(section.get("unit_ids") or [])
        if not unit_ids:
            section_id = section.get("id")
            unit_ids = [unit_id for unit_id, unit in units.items() if unit.get("section") == section_id]
        result[section.get("id")] = unit_ids
    return result


def _section_first_blocks(model, blocks):
    """每节最早出现的原文块，用于把节导语和节图插在节首。"""
    order = {block.get("id"): index for index, block in enumerate(blocks)}
    units = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    first = {}
    for section_id, unit_ids in _section_units(model).items():
        indexes = []
        for unit_id in unit_ids:
            for block_id in (units.get(unit_id) or {}).get("source_blocks") or []:
                if block_id in order:
                    indexes.append(order[block_id])
        if indexes:
            first[section_id] = blocks[min(indexes)].get("id")
    return first


def _figure_sections(figures, model):
    """图 -> 节；只收全部单元落在同一节的图。"""
    units = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    sections = {}
    for section_id, unit_ids in _section_units(model).items():
        for unit_id in unit_ids:
            sections.setdefault(unit_id, section_id)
    for unit_id, unit in units.items():
        if unit.get("section"):
            sections.setdefault(unit_id, unit.get("section"))
    result = []
    for figure in figures:
        found = {sections.get(unit_id) for unit_id in figure.get("units") or []}
        found.discard(None)
        result.append((figure, found.pop() if len(found) == 1 else None))
    return result


def _added(html_text, kind, **attrs):
    extra = "".join(f' {key.replace("_", "-")}="{html.escape(str(value), quote=True)}"' for key, value in attrs.items())
    return f'<{kind} data-origin="added"{extra}>{html_text}</{kind}>'


def _term_note(term):
    inner = f"<b>{html.escape(term.get('term') or '')}</b>{html.escape(term.get('definition') or '')}"
    return _added(inner, "aside", **{"class": "term-note", "data_term": term.get("term") or ""})


def _concept_note(note):
    inner = (
        f'<span class="note-tag">{html.escape(note.get("origin") or "editor")}</span>'
        f"{html.escape(note.get('text') or '')}"
    )
    return _added(inner, "aside", **{"class": "concept-note", "data_block": note.get("block") or ""})


def _unit_first_block(unit_id, units, block_map):
    unit = units.get(unit_id) or {}
    for block_id in unit.get("source_blocks") or []:
        if block_id in block_map:
            return block_id
    return ""


def _note_anchor_block(note, units, block_map, section_first):
    for block_id in note.get("blocks") or []:
        if block_id in block_map:
            return block_id
    for unit_id in note.get("related_units") or note.get("units") or []:
        block_id = _unit_first_block(unit_id, units, block_map)
        if block_id:
            return block_id
    return section_first.get(note.get("section")) or ""


def _anchor_links(units, block_ids, unit_map, block_map):
    links = []
    seen = set()
    for unit_id in units or []:
        block_id = _unit_first_block(unit_id, unit_map, block_map)
        if block_id and block_id not in seen:
            seen.add(block_id)
            links.append((block_id, unit_id))
    for block_id in block_ids or []:
        if block_id in block_map and block_id not in seen:
            seen.add(block_id)
            links.append((block_id, block_id))
    if not links:
        return ""
    escaped = "".join(
        f'<a href="#{html.escape(block_id, quote=True)}" data-skim-anchor="{html.escape(block_id, quote=True)}">'
        f"{html.escape(label)}</a>"
        for block_id, label in links
    )
    return f'<span class="deep-anchors">{escaped}</span>'


def _deep_note(note, units, block_map, section_first):
    anchor = _note_anchor_block(note, units, block_map, section_first)
    label = DEEP_TYPE_LABELS.get(note.get("type"), note.get("type") or "注")
    scope = html.escape(note.get("source_scope") or "本章")
    link = ""
    if anchor:
        escaped_anchor = html.escape(anchor, quote=True)
        link = f'<a href="#{escaped_anchor}" data-skim-anchor="{escaped_anchor}">原文 {html.escape(anchor)}</a>'
    return (
        f'<aside class="argument-note" data-origin="added" data-depth-tiers="deep" '
        f'data-anchor="{html.escape(anchor, quote=True)}">'
        f'<span class="argument-type">{html.escape(label)}</span>'
        f'<span class="argument-text">{html.escape(note.get("text") or "")}</span>'
        f'<span class="argument-meta"><span>{scope}</span>{link}</span></aside>'
    )


def _deep_chain(chain, model, blocks):
    inner = render_figure(chain, model, blocks)
    data = chain.get("data") if isinstance(chain.get("data"), dict) else {}
    if not data.get("edges"):
        fallback = str(chain.get("fallback") or "").strip()
        if fallback:
            inner += f'<p class="deep-chain-fallback">{html.escape(fallback)}</p>'
    return f'<div class="deep-chain" data-origin="added" data-depth-tiers="deep">{inner}</div>'


def _standard_rows(model, blocks, standard, resolver, deep=None):
    """逐块输出原文，并在固定位置叠加标准档与深读注解。"""
    figures = [figure for figure in standard.get("figures") or [] if isinstance(figure, dict)]
    chapter_figure = figures[0] if figures else None
    section_figures = _figure_sections(figures[1:], model)
    first_blocks = _section_first_blocks(model, blocks)
    leads = {row.get("id"): row.get("lead") for row in standard.get("sections") or []}
    terms = {}
    for term in standard.get("terms") or []:
        terms.setdefault(term.get("source_block"), []).append(term)
    notes = {}
    for note in standard.get("notes") or []:
        notes.setdefault(note.get("block"), []).append(note)
    folds = {row.get("block"): row for row in standard.get("folds") or []}
    at_section = {}
    for section_id, block_id in first_blocks.items():
        at_section.setdefault(block_id, []).append(section_id)

    units = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    block_map = {block.get("id"): block for block in blocks or [] if block.get("id")}
    deep = deep if isinstance(deep, dict) else {}
    deep_notes = {}
    for note in deep.get("notes") or []:
        anchor = _note_anchor_block(note, units, block_map, first_blocks)
        if anchor:
            deep_notes.setdefault(anchor, []).append(note)
    chains = {}
    for chain in deep.get("chains") or []:
        section_id = chain.get("section")
        chains.setdefault(section_id, []).append(chain)

    parts = []
    if chapter_figure is not None:
        parts.append(render_figure(chapter_figure, model, blocks))
    for block in blocks:
        block_id = block.get("id") or ""
        for section_id in at_section.get(block_id, []):
            lead = leads.get(section_id)
            if lead:
                parts.append(_added(html.escape(lead), "p", **{"class": "section-lead", "data_section": section_id}))
            for figure, figure_section in section_figures:
                if figure_section == section_id:
                    parts.append(render_figure(figure, model, blocks))
            for chain in chains.get(section_id) or []:
                parts.append(_deep_chain(chain, model, blocks))
        row = _source_row(block, resolver)
        fold = folds.get(block_id)
        if fold is not None:
            summary = html.escape(fold.get("summary") or "展开")
            parts.append(
                f'<details class="fold" data-block="{html.escape(block_id, quote=True)}" open>'
                f'<summary data-origin="added">{summary}</summary>{row}</details>'
            )
        else:
            parts.append(row)
        for term in terms.get(block_id, []):
            parts.append(_term_note(term))
        for note in notes.get(block_id, []):
            parts.append(_concept_note(note))
        for note in deep_notes.get(block_id, []):
            parts.append(_deep_note(note, units, block_map, first_blocks))
    return "".join(parts)


def _tab_buttons():
    specs = (
        ("skim", "速览", "编译 · 5–8 分钟"),
        ("standard", "标准", "逐字原文"),
        ("deep", "深读", "逐字 · 论证注"),
    )
    buttons = []
    for key, label, note in specs:
        selected = "true" if key == "skim" else "false"
        buttons.append(
            f'<button class="depth-tab" role="tab" data-depth-tab="{key}" aria-selected="{selected}">'
            f"<b>{label}</b><small>{note}</small></button>"
        )
    return "".join(buttons)


def render_depth_chapter(
    book,
    chapter,
    blocks,
    model,
    ledger,
    skim,
    *,
    resolver=None,
    generated_at=None,
    standard=None,
    deep=None,
    panels=None,
    reading=None,
):
    """渲染一页两树：编译速览 + 全部原文块；标准/深读各叠加自己的层。"""
    del ledger  # 处置账已落在 skim/核心计数里，这里只需要模型与速览。
    if standard is None:
        standard = build_standard(model, blocks, mode=(reading or {}).get("effective_mode"))
    title = html.escape(book.get("title_short") or book.get("title") or "")
    author = html.escape(book.get("author") or "作者待校")
    chapter_title = html.escape(chapter.get("title") or chapter.get("stem") or "")
    uid = html.escape(book.get("uid") or "")
    mode = (reading or {}).get("effective_mode") or ""
    mode_source = (reading or {}).get("effective_source") or ""
    mode_label = {"narrative": "叙事", "argument": "论证", "practical": "实用", "reference": "资料·图册"}.get(mode, "")
    mode_badge = (
        f'<span class="mode-chip">{html.escape(mode_label)}</span>'
        if mode_label and mode_source in ("book", "chapter")
        else ""
    )
    n = int(chapter.get("n") or 0)
    compiled = _compiled_body(skim, model)
    source_rows = _standard_rows(model, blocks, standard, resolver, deep=deep)
    skim_panels = _panels_html(panels, "skim")
    full_panels = _panels_html(panels, "standard deep")
    generated = html.escape(generated_at or chapter.get("generated_at") or "")
    prompt_version = html.escape((model or {}).get("prompt_version") or "")
    tabs = _tab_buttons()
    if deep is not None:
        layer_note = "标准 / 深读两档共用逐字正文；深读只叠加论证注解，不复制原文。"
    else:
        layer_note = "标准 / 深读两档共用同一份逐字原文；深读注解将在后续生成。"
    safe_js = DEPTH_JS.replace("</", "<\\/")
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title} · {chapter_title} · 深读</title>"
        f"<style>{READER_CSS}\n{DEPTH_CSS}</style></head>"
        '<body class="reader depth" data-depth-tab="skim" data-tier="standard">'
        f'<header class="reader-top"><a class="back" href="/book/{uid}">← 目录</a>'
        f'<div class="book"><span>{author}</span><b>{title}</b></div>'
        f'<div class="tool"><a href="/reader/{uid}/{n}">旧读本</a>'
        '<button id="only-btn" aria-pressed="false">只看原文</button>'
        '<button id="big-btn" aria-pressed="false">大字</button>'
        '<button id="night-btn">夜读</button>'
        '<button id="print-btn">打印</button></div></header>'
        f'<nav class="tierbar" aria-label="深读档位"><div class="tier-tabs depth-tabs" role="tablist">{tabs}</div>'
        '<div class="tier-note">同一章，速览为编译版 · 标准档逐字</div></nav>'
        f'<main class="read"><header class="read-head"><p class="eyebrow">{title} · 第 {n} 章 · 深读</p>'
        f'<h1>{chapter_title}</h1><div class="read-stats"><span>标准/深读两档共用逐字原文</span>'
        f"<span>速览与原文章节锚点互通</span>{mode_badge}</div></header>"
        '<div class="depth-bodies">'
        f'<section class="depth-panel" data-body="compiled" aria-label="编译版速览">{compiled}{skim_panels}</section>'
        '<section class="depth-panel" data-body="source" aria-label="逐字原文">'
        '<header class="depth-head"><p class="eyebrow">原文 · 标准 / 深读</p><h2>逐字正文</h2>'
        f'<p class="depth-warn">{layer_note}</p></header>'
        f'<div class="flow">{source_rows}</div>{full_panels}</section></div>'
        f'<footer class="read-foot"><a href="/reader/{uid}/{max(1, n - 1)}">← 上一章</a>'
        f'<a href="/reader/{uid}/{n + 1}">下一章 →</a></footer>'
        f'<div class="colophon">{author} · {prompt_version} · 生成 {generated} · '
        f"渲染 v{DEPTH_RENDER_VERSION}</div></main>"
        f"<script>{safe_js}</script></body></html>"
    )
