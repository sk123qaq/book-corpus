"""深读页渲染：同一章两棵正文树，速览在上，原文在下。

速览是编译体，标准/深读/研究三档共用逐字原文。没有 JavaScript 时两棵树
同时可见；有 JavaScript 时按四个页签切换。所有原文块都经过旧渲染器的
:func:`_block_element`，本模块不改动它，也不丢块。
"""

import html

from ..render import READER_CSS, _block_element

DEPTH_RENDER_VERSION = 1

DEPTH_CSS = """
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
body.only-source [data-origin="added"]{display:none!important}
html.depth-js section[data-body][hidden]{display:none}
.flow-row[id]{scroll-margin-top:84px}
@media print{.reader-top,.tierbar,.skim-actions{display:none!important}
.depth-panel + .depth-panel{margin-top:0;padding-top:0;border-top:0}
.skim-anchor{border:0}.skim-index{display:block}.skim-index summary{display:none}}
.flash{animation:depth-flash 900ms ease-out}
@keyframes depth-flash{0%{background:var(--note-bg)}100%{background:transparent}}
"""

DEPTH_JS = """
document.documentElement.classList.add('depth-js');
const DEPTH_TABS = ['skim', 'standard', 'deep', 'research'];
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


def _compiled_body(skim, model):
    if not isinstance(skim, dict) or not skim.get("supported"):
        return _unsupported_block(skim)
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
    element = _block_element(block, {"standard", "deep", "research"}, resolver)
    block_id = block.get("id") or ""
    row_id = f' id="{html.escape(block_id, quote=True)}"' if block.get("kind") != "heading" else ""
    return f'<div class="flow-row" data-block="{html.escape(block_id, quote=True)}"{row_id}>{element}</div>'


def _tab_buttons():
    specs = (
        ("skim", "速览", "编译 · 5–8 分钟"),
        ("standard", "标准", "逐字原文"),
        ("deep", "深读", "逐字 · 标注待生成"),
        ("research", "研究", "逐字 · 研究待生成"),
    )
    buttons = []
    for key, label, note in specs:
        selected = "true" if key == "skim" else "false"
        buttons.append(
            f'<button class="depth-tab" role="tab" data-depth-tab="{key}" aria-selected="{selected}">'
            f"<b>{label}</b><small>{note}</small></button>"
        )
    return "".join(buttons)


def render_depth_chapter(book, chapter, blocks, model, ledger, skim, *, resolver=None, generated_at=None):
    """渲染一页两树：编译速览 + 全部原文块。"""
    del ledger  # 处置账已落在 skim/核心计数里，这里只需要模型与速览。
    title = html.escape(book.get("title_short") or book.get("title") or "")
    author = html.escape(book.get("author") or "作者待校")
    chapter_title = html.escape(chapter.get("title") or chapter.get("stem") or "")
    uid = html.escape(book.get("uid") or "")
    n = int(chapter.get("n") or 0)
    compiled = _compiled_body(skim, model)
    source_rows = "".join(_source_row(block, resolver) for block in blocks)
    generated = html.escape(generated_at or chapter.get("generated_at") or "")
    prompt_version = html.escape((model or {}).get("prompt_version") or "")
    tabs = _tab_buttons()
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
        f'<h1>{chapter_title}</h1><div class="read-stats"><span>标准/深读/研究三档共用逐字原文</span>'
        "<span>速览与原文章节锚点互通</span></div></header>"
        '<div class="depth-bodies">'
        f'<section class="depth-panel" data-body="compiled" aria-label="编译版速览">{compiled}</section>'
        '<section class="depth-panel" data-body="source" aria-label="逐字原文">'
        '<header class="depth-head"><p class="eyebrow">原文 · 标准 / 深读 / 研究</p><h2>逐字正文</h2>'
        '<p class="depth-warn">深读/研究层将在后续生成；当前三档共用同一份逐字原文。</p></header>'
        f'<div class="flow">{source_rows}</div></section></div>'
        f'<footer class="read-foot"><a href="/reader/{uid}/{max(1, n - 1)}">← 上一章</a>'
        f'<a href="/reader/{uid}/{n + 1}">下一章 →</a></footer>'
        f'<div class="colophon">{author} · {prompt_version} · 生成 {generated} · '
        f"渲染 v{DEPTH_RENDER_VERSION}</div></main>"
        f"<script>{safe_js}</script></body></html>"
    )
