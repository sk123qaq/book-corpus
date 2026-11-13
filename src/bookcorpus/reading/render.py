# ruff: noqa: E501
# CSS/HTML/JS 模板按浏览器里可读的写法保留长行，不按 Python 行宽折行。
"""自包含读本渲染。源文块逐块落地，三档用 ``data-tiers`` 切换。

读本不依赖外部 CSS/JS；测试用 HTMLParser 从 ``data-origin="source"`` 抽文本，
与源块逐块比对（G1）。导出页只含一档，图片按需内嵌。
"""

import html
import json
from urllib.parse import quote

from .tiers import TIER_ORDER, TIER_SPECS
from .visuals import (
    PLATE_KINDS,
    VISUAL_CSS,
    VISUAL_JS,
    render_beat,
    render_spine,
    render_takeaways,
    render_visual,
)

RENDER_VERSION = 2

TIER_META = {
    "skim": ("速览", "40–55% · E≤10%"),
    "standard": ("标准", "60–75% · E≤25%"),
    "deep": ("深读", "80–90% · E≤40%"),
}

READER_CSS = """
:root{--paper:#F3F0E7;--paper-2:#EAE5D8;--paper-3:#DED7C7;--ink:#181817;--ink-2:#55544E;--ink-3:#69665C;
--rule:#D2CBBB;--rule-strong:#B9B09C;--pine:#1E3A36;--oxblood:#6B2B2B;--ochre:#7C5A2E;--indigo:#2C4056;
--moss:#4A5839;--cinnabar:#A8442F;--note-bg:#F0F2F0;--note-rule:#C9D2CC;
--serif:"Noto Serif CJK SC","Songti SC","Source Han Serif SC",serif;
--sans:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--serif);font-size:16px}
a{color:inherit}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
.reader-top{position:sticky;top:0;z-index:30;height:56px;display:flex;align-items:center;gap:20px;padding:0 22px;
background:color-mix(in srgb,var(--paper) 94%,transparent);border-bottom:1px solid var(--rule);backdrop-filter:blur(8px)}
.reader-top .back{font-family:var(--sans);font-size:12px;letter-spacing:.08em;text-decoration:none;color:var(--ink-2)}
.reader-top .book{display:flex;align-items:baseline;gap:10px;min-width:0}
.reader-top .book span{font-family:var(--sans);font-size:12px;color:var(--ink-3);white-space:nowrap}
.reader-top .book b{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reader-top .tool{margin-left:auto;display:flex;align-items:center;gap:6px}
.reader-top .tool button,.reader-top .tool a{font-family:var(--sans);font-size:12px;padding:6px 9px;border:1px solid transparent;text-decoration:none;color:var(--ink-2)}
.reader-top .tool button:hover,.reader-top .tool a:hover{border-color:var(--rule-strong);color:var(--ink)}
.reader-top .tool button[aria-pressed="true"]{border-color:var(--pine);color:var(--pine)}
.progress{position:sticky;top:56px;height:2px;background:transparent;z-index:31}
.progress i{display:block;height:2px;width:0;background:var(--cinnabar)}
.tierbar{position:sticky;top:58px;z-index:29;display:flex;align-items:stretch;justify-content:center;gap:26px;
padding:0 22px;background:color-mix(in srgb,var(--paper) 96%,transparent);border-bottom:1px solid var(--rule);backdrop-filter:blur(8px)}
.tier-tabs{display:flex;align-items:stretch}
.tier-tab{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:12px 18px 10px;border-bottom:2px solid transparent}
.tier-tab b{font-family:var(--sans);font-size:13px;letter-spacing:.06em}
.tier-tab small{font-family:var(--sans);font-size:10px;color:var(--ink-3)}
.tier-tab[aria-selected="true"]{border-bottom-color:var(--pine)}
.tier-tab[aria-selected="true"] b{color:var(--pine)}
.tier-tab[disabled]{opacity:.35;cursor:not-allowed}
.tier-note{display:flex;align-items:center;font-family:var(--sans);font-size:11px;color:var(--ink-3);letter-spacing:.04em}
.read{max-width:1000px;margin:0 auto;padding:44px 26px 72px}
.read-head{border-bottom:1px solid var(--rule);padding-bottom:28px;margin-bottom:34px}
.eyebrow{font-family:var(--sans);font-size:11px;letter-spacing:.16em;color:var(--ink-3);margin:0 0 12px}
.read-head h1{font-size:34px;line-height:1.35;margin:0 0 18px;font-weight:600;letter-spacing:.02em}
.read-stats{display:flex;flex-wrap:wrap;gap:18px;font-family:var(--sans);font-size:12px;color:var(--ink-2)}
.read-stats b{font-weight:600;color:var(--ink)}
.draft-banner{display:flex;align-items:baseline;gap:8px;margin:0 0 28px;padding:10px 14px;border-left:3px solid var(--ochre);
background:var(--paper-2);font-family:var(--sans);font-size:12px;line-height:1.7;color:var(--ink-2)}
.draft-banner b{font-size:10px;letter-spacing:.16em;color:var(--ochre);white-space:nowrap}
.flow{display:flex;flex-direction:column}
.flow-row{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:40px;align-items:start}
.flow-row .block{min-width:0}
.flow-row .margin{min-width:0}
.src{font-size:17.5px;line-height:1.9;text-indent:2em;margin:0;letter-spacing:.01em}
.src.lst{text-indent:0;padding-left:1.4em;border-left:2px solid var(--rule)}
blockquote.src{text-indent:2em;margin:1.4em 0 1.4em;padding:2px 0 2px 20px;border-left:3px solid var(--rule-strong);color:var(--ink-2)}
.sec{font-size:21px;line-height:1.6;margin:2.6em 0 1.1em;padding-left:14px;border-left:4px solid var(--pine);font-weight:600}
.fig{margin:1.8em 0;text-align:center}
.fig img{max-width:100%;height:auto;border:1px solid var(--rule);background:var(--paper-2)}
.fig figcaption{font-family:var(--sans);font-size:13px;line-height:1.6;color:var(--ink-3);margin:7px auto 0;max-width:40em;text-indent:0}
.fig .fig-kicker{display:block;font-size:11px;letter-spacing:.16em;color:var(--cinnabar);margin-bottom:4px}
.fig .fig-cap{display:block}
body:not(.only-source) .fig-title{display:none}
body.only-source figcaption{display:none}
.fn{font-family:var(--sans);font-size:12px;color:var(--ink-3);text-indent:0;margin:-.6em 0 1.2em}
.note{background:var(--note-bg);border-top:1px solid var(--note-rule);padding:12px 13px 13px;margin:0 0 1.6em;
font-size:13px;line-height:1.75;text-indent:0;box-shadow:0 1px 0 var(--rule)}
.note .tag{display:block;font-family:var(--sans);font-size:9.5px;letter-spacing:.14em;color:var(--pine);margin-bottom:6px}
.note h4{font-size:13.5px;margin:0 0 6px;line-height:1.5}
.note p{margin:0 0 .6em;text-indent:0}
.note q{display:block;quotes:"「" "」";color:var(--ink-2);font-size:12.5px;border-left:2px solid var(--rule-strong);padding-left:8px;margin:.5em 0}
.note .cite{font-family:var(--sans);font-size:9.5px;color:var(--ink-3);letter-spacing:.06em}
.read-foot{display:flex;justify-content:space-between;gap:20px;border-top:1px solid var(--rule);margin-top:52px;padding-top:22px}
.read-foot a,.read-foot span{font-family:var(--sans);font-size:12px;color:var(--ink-2);text-decoration:none;max-width:46%}
.read-foot a:hover{color:var(--pine)}
.read-foot .dim{color:var(--ink-3)}
.colophon{margin-top:38px;padding-top:16px;border-top:1px solid var(--rule);font-family:var(--sans);font-size:11px;
line-height:1.9;color:var(--ink-3);letter-spacing:.03em}
.foreedge{position:fixed;left:0;top:50%;transform:translateY(-50%);display:flex;flex-direction:column;gap:3px;padding:6px 4px;z-index:20}
.foreedge i{display:block;width:9px;height:2px;background:var(--rule);cursor:pointer;transition:width .15s}
.foreedge i.kept{background:var(--pine)}
.foreedge i:hover{width:18px}
.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);background:var(--ink);color:var(--paper);
font-family:var(--sans);font-size:12px;padding:8px 14px;opacity:0;pointer-events:none;transition:.2s;z-index:60}
.toast.on{opacity:1;transform:translate(-50%,0)}
.cmp-overlay{position:fixed;inset:0;background:rgba(24,24,23,.45);z-index:50;display:flex;align-items:center;justify-content:center;padding:24px}
.cmp-overlay[hidden]{display:none}
.cmp{background:var(--paper);border:1px solid var(--rule-strong);max-width:660px;width:100%;padding:26px 28px;box-shadow:0 20px 60px rgba(0,0,0,.25)}
.cmp h3{margin:0 0 4px;font-size:18px}
.cmp p{font-family:var(--sans);font-size:11px;color:var(--ink-3);margin:0 0 16px}
.cmp table{width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px}
.cmp th,.cmp td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--rule)}
.cmp th{font-weight:600;color:var(--ink-2);font-size:10px;letter-spacing:.08em}
.cmp tr.current td{background:var(--paper-2);color:var(--pine);font-weight:600}
.cmp-close{margin-top:16px;font-family:var(--sans);font-size:11px;border:1px solid var(--rule-strong);padding:7px 12px}
body.only-source [data-origin="added"]{display:none!important}
body.only-source .flow-row{grid-template-columns:minmax(0,680px)}
body.only-source .stat-notes{display:none}
body.big .src{font-size:20px;line-height:1.8}
body.big .read-head h1{font-size:38px}
body.night{--paper:#E2E6E6;--paper-2:#D3DADA;--paper-3:#C3CCCC;--ink:#131A1F;--ink-2:#3C4A52;--ink-3:#4F5C63;
--rule:#B7C1C1;--rule-strong:#93A1A1;--pine:#18344F;--oxblood:#4E2A33;--ochre:#6B4E28;--indigo:#2B5C84;
--moss:#3C4A36;--cinnabar:#2B5C84;--note-bg:#DCE3E3;--note-rule:#AEBABA}
body.night .foreedge i.kept{background:var(--indigo)}
body.night .tier-tab[aria-selected="true"]{border-bottom-color:var(--indigo)}
body.night .tier-tab[aria-selected="true"] b{color:var(--indigo)}
@media(max-width:1100px){.foreedge{display:none}}
@media(max-width:980px){.flow-row{grid-template-columns:minmax(0,1fr)}.flow-row .margin{padding-left:0}
.note{margin-top:-.6em;margin-bottom:1.6em}.tierbar{justify-content:flex-start;overflow-x:auto;gap:8px}
.tier-tab{padding:10px 12px 8px;white-space:nowrap}.tier-note{display:none}}
@media(max-width:720px){.read{padding:30px 16px 56px}.read-head h1{font-size:26px}.src{font-size:16.5px;line-height:1.85}
.reader-top{padding:0 10px;gap:6px}.reader-top .book{display:none}
.reader-top .tool{margin-left:auto;gap:0;min-width:0;overflow-x:auto;scrollbar-width:none}
.reader-top .tool button,.reader-top .tool a{padding:6px 5px;font-size:11px;white-space:nowrap}
.tierbar{padding:6px 10px;justify-content:stretch;overflow:visible;display:block}
.tier-tabs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px;width:100%}
.tier-tab{padding:8px 8px 7px;border:1px solid var(--rule);border-bottom-width:2px}
.tier-tab b{font-size:12px}.tier-tab small{font-size:9.5px}
.tier-note{display:none}.read-stats{gap:10px}
.read-foot{flex-direction:column}.read-foot a,.read-foot span{max-width:100%}
.draft-banner{margin:0 0 22px;font-size:11px;padding:10px 12px}}
@media print{.reader-top,.tierbar,.foreedge,.progress,.toast,.cmp-overlay,.read-foot{display:none!important}
.read{padding:0}.note{break-inside:avoid}.src{font-size:12pt;line-height:1.7}}
body.export .reader-top,body.export .tierbar,body.export .foreedge,body.export .progress{display:none}
body.export .read{padding-top:30px}
"""

READER_JS = """
const TIERS = __TIERS__;
const STATS = __STATS__;
const TICK_COUNT = __TICKS__;
const state = {tier: __INITIAL__, only: false, big: false};
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
function toast(msg) { const el = $('#toast'); if (!el) return; el.textContent = msg; el.classList.add('on');
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('on'), 1600); }
function fmt(n) { return Number(n || 0).toLocaleString('zh-CN'); }
function setTier(tier, push) {
  if (!TIERS.some(t => t.key === tier)) tier = 'standard';
  state.tier = tier;
  document.body.dataset.tier = tier;
  $$('[data-tiers]').forEach(el => { el.hidden = !el.dataset.tiers.split(' ').includes(tier); });
  $$('[data-tier-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tierTab === tier)));
  const s = STATS[tier]; if (s) {
    const line = $('#live-stats');
    if (line) line.innerHTML = '原文 <b>' + Math.round(s.c_block * 100) + '%</b> · 约 <b>' + fmt(s.chars) +
      '</b> 字<span class="stat-notes"> · 讲解 <b>' + s.notes + '</b> 条</span> · <b>' + s.minutes + '</b> 分钟';
    const pct = Math.max(0, Math.min(100, Math.round(s.c_block * 100)));
    const bar = $('#scope-bar'); if (bar) bar.style.width = pct + '%';
    // 页脚出处按档位切：增量包可能混多个模型/时间。
    const at = $('#colophon-time'); if (at) at.textContent = s.generated_at || '';
    const md = $('#colophon-model'); if (md) md.textContent = s.model || '';
  }
  updateTicks(); updateCompare();
  if (push) {
    const url = new URL(location.href); url.searchParams.set('tier', tier); history.replaceState(null, '', url);
  }
}
function updateTicks() {
  $$('.foreedge i').forEach(tick => {
    const row = document.querySelector('[data-block="' + tick.dataset.block + '"] [data-origin="source"]');
    tick.classList.toggle('kept', !!row && !row.hidden);
  });
}
function updateCompare() {
  $$('.cmp tr[data-tier-row]').forEach(tr => tr.classList.toggle('current', tr.dataset.tierRow === state.tier));
}
function setOnly(on) { state.only = on; document.body.classList.toggle('only-source', on);
  const b = $('#only-btn'); if (b) b.setAttribute('aria-pressed', String(on)); }
function setBig(on) { state.big = on; document.body.classList.toggle('big', on);
  const b = $('#big-btn'); if (b) b.setAttribute('aria-pressed', String(on)); }
function setNight(on) { document.body.classList.toggle('night', on);
  try { localStorage.setItem('reading-theme', on ? 'cold' : 'warm'); } catch (e) {}
  const b = $('#night-btn'); if (b) b.textContent = on ? '暖纸' : '夜读'; }
function themeLinks() {
  let theme = 'warm'; try { theme = localStorage.getItem('reading-theme') || 'warm'; } catch (e) {}
  if (theme === 'warm') return;
  $$('a[href]').forEach(a => { const href = a.getAttribute('href');
    if (href && !href.startsWith('#') && !href.includes('theme=')) a.setAttribute('href', href + (href.includes('?') ? '&' : '?') + 'theme=' + theme); });
}
document.addEventListener('DOMContentLoaded', () => {
  const url = new URL(location.href);
  let theme = 'warm'; try { theme = localStorage.getItem('reading-theme') || 'warm'; } catch (e) {}
  setNight(theme === 'cold');
  setTier(url.searchParams.get('tier') || __INITIAL__, false);
  themeLinks();
  $$('[data-tier-tab]').forEach(btn => btn.addEventListener('click', () => {
    if (btn.disabled) return; setTier(btn.dataset.tierTab, true);
  }));
  const only = $('#only-btn'); if (only) only.addEventListener('click', () => setOnly(!state.only));
  const big = $('#big-btn'); if (big) big.addEventListener('click', () => setBig(!state.big));
  const night = $('#night-btn'); if (night) night.addEventListener('click', () => setNight(!document.body.classList.contains('night')));
  const cmp = $('#cmp-overlay'); const open = $('#cmp-btn');
  if (open && cmp) {
    open.addEventListener('click', () => { cmp.hidden = false; updateCompare(); });
    $$('[data-cmp-close]', cmp).forEach(el => el.addEventListener('click', () => { cmp.hidden = true; }));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') cmp.hidden = true; });
  }
  document.addEventListener('keydown', e => {
    if (e.target.matches('input,textarea')) return;
    if (e.key >= '1' && e.key <= String(TIERS.length)) { const t = TIERS[Number(e.key) - 1]; if (t) setTier(t.key, true); }
  });
  $$('.foreedge i').forEach(tick => tick.addEventListener('click', () => {
    const row = document.querySelector('[data-block="' + tick.dataset.block + '"]');
    if (!row) return;
    const src = row.querySelector('[data-origin="source"]');
    if (src && src.hidden) { toast('本档未收这一段，按 3 切到深读可见'); return; }
    row.scrollIntoView({behavior: 'smooth', block: 'center'}); row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 900);
  }));
  const bar = $('#progress i');
  if (bar) {
    const update = () => { const h = document.documentElement; const max = h.scrollHeight - h.clientHeight;
      bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + '%'; };
    document.addEventListener('scroll', update, {passive: true}); update();
  }
});
"""


def _tiers_attr(tier_list):
    return " ".join(tier for tier in TIER_ORDER if tier in tier_list)


def _block_element(block, tiers, resolver, captions=None, figure_titles=None, extra_class=""):
    text = html.escape(block["text"])
    kind = block["kind"]
    attrs = 'data-origin="source" data-src="%s" data-block="%s" data-kind="%s" data-tiers="%s"' % (
        block["id"],
        block["id"],
        kind,
        _tiers_attr(tiers),
    )
    if kind == "heading":
        level = min(6, max(2, int(block.get("level", 2))))
        klass = "sec" + (" " + extra_class if extra_class else "")
        return '<h%d class="%s" id="%s" %s>%s</h%d>' % (level, klass, block["id"], attrs, text, level)
    if kind == "image":
        src = resolver(block.get("rel", "")) if resolver else ""
        caption = (captions or {}).get(block["id"])
        alt = html.escape(block.get("text") or "")
        source = "model" if caption else "alt"
        size = ""
        width, height = block.get("width"), block.get("height")
        if width and height:
            size = ' width="%d" height="%d"' % (int(width), int(height))
        img = '<img src="%s" alt="%s"%s loading="lazy">' % (html.escape(src, quote=True), alt, size)
        title = (figure_titles or {}).get(block["id"], "")
        if caption and title:
            label = '<span class="fig-kicker">%s</span><span class="fig-cap">%s</span>' % (
                html.escape(title),
                html.escape(caption),
            )
        elif caption:
            label = '<span class="fig-cap">%s</span>' % html.escape(caption)
        else:
            label = html.escape(block.get("text", ""))
        return '<figure class="fig" %s data-caption-src="%s">%s<figcaption>%s</figcaption></figure>' % (
            attrs,
            source,
            img,
            label,
        )
    if kind == "footnote":
        if not block["text"]:
            return '<span class="fn" %s></span>' % attrs
        return '<p class="fn" %s>%s</p>' % (attrs, text)
    if kind == "quote":
        return '<blockquote class="src" %s>%s</blockquote>' % (attrs, text)
    if kind == "list":
        return '<p class="src lst" %s>%s</p>' % (attrs, text)
    if kind == "table":
        head = block.get("head") or []
        rows = block.get("rows") or []
        if head:
            cells = "".join("<th>%s</th>" % html.escape(cell) for cell in head)
            thead = "<thead><tr>%s</tr></thead>" % cells
        else:
            thead = ""
            rows = (rows or []) + [block.get("head") or []]
        body_rows = "".join(
            "<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(cell) for cell in row) for row in rows if row
        )
        return '<table class="src tbl" %s>%s<tbody>%s</tbody></table>' % (attrs, thead, body_rows)
    if kind == "code":
        lang_attr = ' data-lang="%s"' % html.escape(str(block.get("lang") or ""), quote=True)
        return '<pre class="src code" %s%s><code>%s</code></pre>' % (attrs, lang_attr, text)
    return '<p class="src" %s>%s</p>' % (attrs, text)


def _card_element(card, tiers, tag=None):
    quotes = "".join(
        '<q data-src="%s">%s</q>' % (html.escape(quote_item["block"]), html.escape(quote_item["text"]))
        for quote_item in card.get("quotes", [])
    )
    cites = " · ".join(card.get("citations", []))
    title = "<h4>%s</h4>" % html.escape(card["title"]) if card.get("title") else ""
    return (
        '<aside class="note" data-origin="added" data-trigger="%s" data-tiers="%s">'
        '<span class="tag">%s</span>%s<p>%s</p>%s<span class="cite">依据 %s</span></aside>'
        % (
            html.escape(card["trigger"]),
            _tiers_attr(tiers),
            html.escape(tag or "讲解 · 模型补充"),
            title,
            html.escape(card["text"]),
            quotes,
            cites,
        )
    )


def _cards_by_trigger(projections):
    table = {}
    for tier, projection in projections.items():
        for card in projection["cards"]:
            entry = table.setdefault(card["trigger"], {})
            record = entry.setdefault(card["id"], {"card": card, "tiers": set()})
            record["tiers"].add(tier)
    return table


def _stats_json(projections, chapter=None):
    out = {}
    tier_meta = (chapter or {}).get("tiers") or {}
    for tier in TIER_ORDER:
        projection = projections.get(tier)
        if not projection:
            continue
        stats = dict(projection["stats"])
        stats["label"] = TIER_SPECS[tier]["label"]
        info = tier_meta.get(tier) or {}
        stats["model"] = info.get("model")
        stats["generated_at"] = info.get("generated_at")
        out[tier] = stats
    return out


def _compare_rows(projections):
    rows = []
    for tier in TIER_ORDER:
        projection = projections.get(tier)
        if not projection:
            continue
        s = projection["stats"]
        rows.append(
            '<tr data-tier-row="%s"><td>%s</td><td>%d%%</td><td>%d%%</td><td>%d</td><td>%s 字</td><td>%d 分</td></tr>'
            % (
                tier,
                TIER_META[tier][0],
                round(s["c_block"] * 100),
                round(s["c_char"] * 100),
                s["notes"],
                format(s["chars"], ","),
                s["minutes"],
            )
        )
    return "".join(rows)


def _tabs(projections, initial):
    buttons = []
    for tier in TIER_ORDER:
        label, band = TIER_META[tier]
        available = tier in projections
        buttons.append(
            '<button class="tier-tab" data-tier-tab="%s" aria-selected="%s"%s><b>%s</b><small>%s</small></button>'
            % (tier, "true" if tier == initial else "false", "" if available else " disabled", label, band)
        )
    return "".join(buttons)


def _reader_body(book, chapter, blocks, projections, cards, resolver, standalone=False, editorial=None):
    chapter = {**chapter, "n": int(chapter.get("n") or 0)}
    editorial = editorial or None
    draft = bool(chapter.get("draft"))
    draft_banner = (
        '<div class="draft-banner" role="status"><b>草稿</b>'
        " 未接模型：所选档位暂时同文，讲解为空。接入模型后重新生成，才会出现取舍。</div>"
        if draft
        else ""
    )
    initial = "standard" if "standard" in projections else next(iter(projections))
    captions = {}
    for projection in projections.values():
        for block_id, caption in (projection.get("captions") or {}).items():
            captions.setdefault(block_id, caption)

    active_tier = next(iter(projections)) if standalone else None
    figure_titles = {}
    figure_title_ids = set()
    for index, block in enumerate(blocks):
        if block["kind"] != "image" or index + 1 >= len(blocks):
            continue
        following = blocks[index + 1]
        if following["kind"] == "heading" and following["text"].strip().startswith(("图", "表")):
            figure_titles[block["id"]] = following["text"].strip()
            figure_title_ids.add(following["id"])
    beats = list((editorial or {}).get("beats") or [])
    visuals = list((editorial or {}).get("visuals") or [])
    takeaways = list((editorial or {}).get("takeaways") or [])
    takeaways_tiers = list((editorial or {}).get("takeaways_tiers") or ())
    if active_tier:
        beats = [beat for beat in beats if active_tier in beat["tiers"]]
        visuals = [visual for visual in visuals if active_tier in visual["tiers"]]
        if active_tier not in takeaways_tiers:
            takeaways = []

    beats_by_block = {}
    for index, beat in enumerate(beats, 1):
        beats_by_block.setdefault(beat["before_block"], []).append((index, beat))
    visuals_by_block = {}
    plate_no = 0
    for visual in visuals:
        if visual["kind"] in PLATE_KINDS:
            plate_no += 1
        visuals_by_block.setdefault(visual["after_block"], []).append(
            (visual, plate_no if visual["kind"] in PLATE_KINDS else None)
        )
    note_tags = (editorial or {}).get("note_tags") or {}

    rows = []
    for block in blocks:
        tiers = {tier for tier, projection in projections.items() if block in projection["blocks"]}
        if standalone:
            tiers = set(projections) if tiers else set()
        if not tiers:
            continue
        element = _block_element(
            block,
            tiers,
            resolver,
            captions,
            figure_titles,
            "fig-title" if block["id"] in figure_title_ids else "",
        )
        inside = "".join(
            render_visual(visual, plate_no=plate)
            for visual, plate in visuals_by_block.get(block["id"], [])
            if visual["placement"] == "inside"
        )
        before = "".join(
            render_visual(visual, plate_no=plate)
            for visual, plate in visuals_by_block.get(block["id"], [])
            if visual["placement"] == "before"
        )
        after = "".join(
            render_visual(visual, plate_no=plate)
            for visual, plate in visuals_by_block.get(block["id"], [])
            if visual["placement"] == "after"
        )
        notes = []
        for record in cards.get(block["id"], {}).values():
            if standalone and not record["tiers"].intersection(projections):
                continue
            notes.append(_card_element(record["card"], record["tiers"], tag=note_tags.get(block["id"])))
        beat_html = "".join(render_beat(beat, index, len(beats)) for index, beat in beats_by_block.get(block["id"], []))
        rows.append(
            beat_html
            + before
            + '<div class="flow-row" data-block="%s"><div class="block">%s</div><div class="margin">%s</div></div>'
            % (block["id"], element + inside, "".join(notes))
            + after
        )

    if standalone:
        tier = next(iter(projections))
        s = projections[tier]["stats"]
        head_stats = '原文 %d%% · %s 字<span class="stat-notes"> · 讲解 %d 条</span> · %d 分钟' % (
            round(s["c_block"] * 100),
            format(s["chars"], ","),
            s["notes"],
            s["minutes"],
        )
    else:
        head_stats = '<span id="live-stats"></span>'

    if standalone:
        chrome_top = ""
    else:
        chrome_top = (
            '<header class="reader-top"><a class="back" href="/chapter/%s/%s">← 工单</a>'
            '<div class="book"><span>%s</span><b>%s</b></div>'
            '<div class="tool"><a href="/book/%s">目录</a>'
            "<!--EDITION_LINK-->"
            '<button id="only-btn" aria-pressed="false">只看原文</button>'
            '<button id="big-btn" aria-pressed="false">大字</button>'
            '<button id="night-btn">夜读</button>'
            '<button id="cmp-btn">对照</button></div></header>'
            '<div class="progress" id="progress"><i></i></div>'
            '<nav class="tierbar"><div class="tier-tabs">%s</div>'
            '<div class="tier-note">同一章，已生成档位 · 数字键切换</div></nav>'
            % (
                book["uid"],
                chapter["n"],
                html.escape(book["title_short"]),
                html.escape(chapter["title"]),
                book["uid"],
                _tabs(projections, initial),
            )
        )

    if standalone:
        foreedge = ""
        modal = ""
        toast = ""
    else:
        ticks = []
        for block in blocks:
            if block["kind"] not in ("paragraph", "quote", "list", "image"):
                continue
            kept = block in projections[initial]["blocks"]
            ticks.append(
                '<i data-block="%s" class="%s" title="%s"></i>' % (block["id"], "kept" if kept else "", block["id"])
            )
        foreedge = '<div class="foreedge" aria-hidden="true">%s</div>' % "".join(ticks)
        modal = (
            '<div class="cmp-overlay" id="cmp-overlay" hidden><div class="cmp" role="dialog" aria-modal="true" aria-label="档位对照">'
            "<h3>档位对照</h3><p>同一章源文，已生成档位对照。浏览 600 字/分 · 标准 500 · 深读 350。</p>"
            "<table><thead><tr><th>档</th><th>块覆盖</th><th>字数覆盖</th><th>讲解</th><th>正文字数</th><th>用时</th></tr></thead>"
            "<tbody>%s</tbody></table>"
            '<button class="cmp-close" data-cmp-close>关闭</button></div></div>' % _compare_rows(projections)
        )
        toast = '<div class="toast" id="toast"></div>'

    prev_link = (
        '<a href="/reader/%s/%s">← %s</a>' % (book["uid"], chapter["n"] - 1, "上一章")
        if chapter["n"] > 1
        else '<span class="dim">← 上一章</span>'
    )
    next_link = '<a href="/reader/%s/%s">下一章 →</a>' % (book["uid"], chapter["n"] + 1)

    spine = render_spine(beats) if beats else ""
    takeaways_html = (
        render_takeaways(
            takeaways,
            title=(editorial or {}).get("takeaways_title", ""),
            note=(editorial or {}).get("takeaways_note", ""),
            tiers=takeaways_tiers,
        )
        if takeaways
        else ""
    )
    style_css = READER_CSS + ("\n" + VISUAL_CSS if editorial else "")

    stats_payload = _stats_json(projections, chapter)
    script_body = ""
    if not standalone:
        script_body = (
            READER_JS.replace(
                "__TIERS__", json.dumps([{"key": t, "label": TIER_META[t][0]} for t in TIER_ORDER], ensure_ascii=False)
            )
            .replace("__STATS__", json.dumps(stats_payload, ensure_ascii=False))
            .replace("__TICKS__", str(len(blocks)))
            .replace("__INITIAL__", json.dumps(initial))
        )
    if editorial:
        script_body = (script_body + "\n" + VISUAL_JS).strip()
    script = "<script>%s</script>" % script_body.replace("</", "<\\/") if script_body else ""

    tier_meta = chapter.get("tiers") or {}
    shown_tier = next(iter(projections)) if standalone else initial
    shown_info = tier_meta.get(shown_tier) or {}
    shown_time = shown_info.get("generated_at") or chapter.get("generated_at", "")
    shown_model = shown_info.get("model") or chapter.get("model") or "none"
    colophon = (
        '<div class="colophon">源文件 %s · SHA-256 %s…<br>生成 <span id="colophon-time">%s</span>'
        ' · 生成器 %s · 模型 <span id="colophon-model">%s</span> · 渲染 v%d'
        " · 原文逐字保留，块 %d</div>"
        % (
            html.escape(chapter.get("file") or ""),
            html.escape(chapter.get("source_sha256", "")[:16]),
            html.escape(shown_time),
            html.escape(chapter.get("generator_version", "0.1.0")),
            html.escape(shown_model),
            RENDER_VERSION,
            len(blocks),
        )
    )

    body_class = ("reader export" if standalone else "reader") + (" has-editorial" if editorial else "")
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>%s · %s · 分层读</title><style>%s</style></head>"
        '<body class="%s" data-tier="%s">%s<main class="read"><header class="read-head">'
        '<p class="eyebrow">%s · %s</p><h1>%s</h1><div class="read-stats">%s</div>%s</header>%s'
        '<div class="flow">%s</div>'
        '%s<footer class="read-foot">%s%s</footer>%s</main>%s%s%s%s</body></html>'
        % (
            html.escape(book["title_short"]),
            html.escape(chapter["title"]),
            style_css,
            body_class,
            initial,
            chrome_top,
            html.escape(book["title_short"]),
            html.escape(book["author"] or "作者待校"),
            html.escape(chapter["title"]),
            head_stats,
            spine,
            draft_banner,
            "".join(rows),
            takeaways_html,
            prev_link,
            next_link,
            colophon,
            foreedge,
            modal,
            toast,
            script,
        )
    )


def render_chapter(book, chapter, blocks, projections, *, resolver=None, editorial=None):
    """读本单页：三档 tab、只看原文、对照、页边刻度与记录。"""
    cards = _cards_by_trigger(projections)
    return _reader_body(book, chapter, blocks, projections, cards, resolver, standalone=False, editorial=editorial)


def render_export(book, chapter, blocks, projection, *, resolver=None, editorial=None):
    """导出页：一档、无工具条、图片已内嵌。"""
    tier = projection["tier"]
    cards = _cards_by_trigger({tier: projection})
    return _reader_body(
        book, chapter, blocks, {tier: projection}, cards, resolver, standalone=True, editorial=editorial
    )


def image_data_uri(path):
    import base64
    import mimetypes

    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        payload = base64.b64encode(fh.read()).decode("ascii")
    return "data:%s;base64,%s" % (mime, payload)


def asset_url(uid, rel):
    return "/api/asset?uid=%s&rel=%s" % (quote(uid), quote(rel))
