# ruff: noqa: E501
"""编辑层的版式与交互。

所有内容都来自 ``editorial.json``，这里只负责把结构化数据变成 HTML。
新增一种图版时，先在这里加渲染器，再加 ``editorial.VISUAL_KINDS`` 的白名单；
不要用一段自由 HTML 绕过校验。
"""

from __future__ import annotations

import html
import itertools
import math

from .tiers import TIER_ORDER

VISUAL_CSS = r"""
:root{--plate:#F8F5EC;--plate-2:#EFE9DA;--rail:#C9C0AE}
body.night{--plate:#DDE4E4;--plate-2:#D2DADA;--rail:#AEBABA}
[hidden]{display:none!important}
body.has-editorial .flow-row .block > .src{margin-bottom:1.05em}
body.has-editorial .flow-row .block > .src:last-child{margin-bottom:0}
body.has-editorial .flow-row .block > blockquote.src{margin-top:1.3em;margin-bottom:1.5em}
body.only-source.has-editorial .flow-row .block > .src{margin-bottom:0}
body.only-source.has-editorial .flow-row .block > blockquote.src{margin-top:1.3em;margin-bottom:1.5em}
body.has-editorial .foreedge{display:none!important}

.spine{display:flex;flex-wrap:wrap;gap:4px 0;margin-top:17px;font-family:var(--sans);font-size:11px;line-height:1.7;color:var(--ink-3)}
.spine:not(:has(a:not([hidden]))){display:none}
.spine a{display:inline-flex;align-items:baseline;gap:5px;text-decoration:none;color:inherit;padding:2px 11px 2px 0;margin-right:11px;border-right:1px solid var(--rule)}
.spine a:last-child{border-right:0;margin-right:0;padding-right:0}
.spine a:hover{color:var(--pine)}
.spine b{font-weight:600;color:var(--cinnabar);letter-spacing:.04em}

.beat{max-width:680px;margin:3.2em 0 1.4em;padding-top:1.2em;border-top:1px solid var(--rule-strong);scroll-margin-top:132px}
.beat:first-child{margin-top:0;padding-top:0;border-top:0}
.beat-no{display:inline-block;font-family:var(--sans);font-size:10px;letter-spacing:.2em;color:var(--cinnabar);margin-bottom:2px}
.beat h3{font-size:23px;line-height:1.45;margin:.28em 0 .3em;font-weight:600;letter-spacing:.01em}
.beat-q{font-family:var(--sans);font-size:13px;line-height:1.75;color:var(--ink-2);margin:0}

.plate-row{max-width:948px;margin:3.4em 0 3.8em;padding:26px 30px 22px;background:var(--plate);border-top:2px solid var(--ink);border-bottom:1px solid var(--rule-strong);scroll-margin-top:120px}
.plate-head{display:flex;align-items:baseline;gap:14px;margin-bottom:20px}
.plate-no{font-family:var(--sans);font-size:10px;letter-spacing:.2em;color:var(--cinnabar);white-space:nowrap}
.plate-head h3{font-size:21px;line-height:1.4;margin:0;font-weight:600}
.plate-cap{font-family:var(--sans);font-size:12px;line-height:1.85;color:var(--ink-2);margin:18px 0 0;max-width:66em}
.plate-cap .cite,.method-note .cite{display:block;margin-top:3px;font-size:10px;letter-spacing:.04em;color:var(--ink-3)}

.two-lives{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.life{padding:20px 26px 16px;display:flex;flex-direction:column;align-items:center}
.life+.life{border-left:1px solid var(--rule)}
.life-mark{height:132px;display:flex;align-items:center;justify-content:center;margin-bottom:12px}
.one-dot{width:58px;height:58px;border-radius:50%;background:var(--cinnabar);box-shadow:0 0 0 12px color-mix(in srgb,var(--cinnabar) 10%,transparent),0 0 0 24px color-mix(in srgb,var(--cinnabar) 5%,transparent)}
.dots{display:grid;grid-template-columns:repeat(20,6px);gap:5px;justify-content:center;align-content:center}
.dots i{width:6px;height:6px;border-radius:50%;background:var(--pine);opacity:.55}
.life-label{font-family:var(--sans);font-size:10px;letter-spacing:.12em;color:var(--ink-3);text-align:center}
.life-label b{display:block;font-family:var(--serif);font-size:17px;letter-spacing:.01em;color:var(--ink);margin-bottom:3px}
.life p{font-size:13px;line-height:1.8;color:var(--ink-2);text-align:center;margin:9px 0 0;max-width:22em}
.life-facts{margin-top:10px;font-family:var(--sans);font-size:10px;letter-spacing:.04em;color:var(--cinnabar);border-top:1px solid var(--rule);padding-top:7px;text-align:center;width:100%}
.life.statistical .life-facts{color:var(--pine)}

.ratio{display:flex;flex-direction:column;gap:18px;padding:8px 0 2px}
.ratio-row{display:grid;grid-template-columns:200px 110px minmax(0,1fr);align-items:center;gap:18px}
.ratio-label b{display:block;font-size:16px;font-weight:600;line-height:1.4}
.ratio-label span{display:block;font-family:var(--sans);font-size:10px;color:var(--ink-3);margin-top:2px}
.ratio-value{font-family:var(--sans);font-size:25px;line-height:1;letter-spacing:-.02em;color:var(--ochre);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ratio-bar{height:24px;background:var(--plate-2);position:relative;border:1px solid var(--rule)}
.ratio-bar i{display:block;height:100%;background:var(--ochre);min-width:3px}
.ratio-row.wtp .ratio-bar i{background:var(--cinnabar)}
.ratio-row.wtp .ratio-value{color:var(--cinnabar)}
.ratio-gap{display:flex;align-items:baseline;gap:12px;margin-left:346px;padding-left:14px;border-left:1px solid var(--cinnabar)}
.ratio-gap b{font-family:var(--sans);font-size:30px;line-height:1;color:var(--cinnabar);letter-spacing:-.03em}
.ratio-gap span{font-family:var(--sans);font-size:11px;line-height:1.6;color:var(--ink-2)}
.scale-row.accent .ratio-bar i{background:var(--cinnabar)}
.scale-row.accent .ratio-value{color:var(--cinnabar)}
.scale-row.muted .ratio-bar i{background:var(--ink-3)}
.scale-row.muted .ratio-value{color:var(--ink-3)}
.scale-row.pine .ratio-bar i{background:var(--pine)}
.scale-row.pine .ratio-value{color:var(--pine)}
.scale-note{font-family:var(--sans);font-size:10px;line-height:1.7;color:var(--ink-3);margin:2px 0 0 346px;padding-left:14px}

.curve{max-width:680px;padding:4px 0 0}
.curve svg{display:block;width:100%;height:auto;overflow:visible}
.curve-axis{stroke:var(--rule-strong);stroke-width:1;fill:none}
.curve-grid{stroke:var(--rule);stroke-width:1;stroke-dasharray:2 4}
.curve-line{fill:none;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}
.curve-line.ochre{stroke:var(--ochre)}
.curve-line.cinnabar{stroke:var(--cinnabar)}
.curve-line.pine{stroke:var(--pine)}
.curve-line.indigo{stroke:var(--indigo)}
.curve-label{font-family:var(--sans);font-size:11px;fill:var(--ink-3)}
.curve-note{font-family:var(--sans);font-size:10px;line-height:1.7;color:var(--ink-3);margin:6px 0 0}
.curve-legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:6px;font-family:var(--sans);font-size:10px;color:var(--ink-2)}
.curve-legend i{display:inline-block;width:16px;height:2px;margin-right:5px;vertical-align:middle}
.curve-annot{font-family:var(--sans);font-size:10px;fill:var(--ink-2)}

.ledger-wrap{overflow-x:auto}
.ledger{width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12px;margin-top:2px;min-width:560px}
.ledger th,.ledger td{padding:11px 10px;border-bottom:1px solid var(--rule);vertical-align:baseline}
.ledger thead th{font-size:9px;letter-spacing:.12em;color:var(--ink-3);font-weight:500;text-align:right;border-bottom:1px solid var(--rule-strong)}
.ledger thead th:first-child{text-align:left}
.ledger tbody th{text-align:left;font-family:var(--serif);font-size:14px;font-weight:500;line-height:1.5}
.ledger td{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap}
.ledger td.yes{color:var(--pine);font-weight:600}
.ledger td.no{color:var(--cinnabar)}
.ledger tr.chosen{background:color-mix(in srgb,var(--ochre) 7%,transparent)}
.ledger .price-note{font-family:var(--sans);font-size:10px;color:var(--ink-3);letter-spacing:.03em;margin:0 0 10px}
.ledger-mobile{display:none}
.ledger-card{border:1px solid var(--rule);padding:10px 12px;background:var(--paper)}
.ledger-card.chosen{border-color:var(--ochre);background:color-mix(in srgb,var(--ochre) 7%,transparent)}
.ledger-card-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px solid var(--rule);padding-bottom:6px;margin-bottom:5px}
.ledger-card-head b{font-family:var(--serif);font-size:14px;font-weight:500;line-height:1.5}
.ledger-card-head span{font-family:var(--sans);font-size:10px;font-weight:600;white-space:nowrap}
.ledger-card-head .yes{color:var(--pine)}
.ledger-card-head .no{color:var(--cinnabar)}
.ledger-card-row{display:flex;justify-content:space-between;font-family:var(--sans);font-size:11px;color:var(--ink-2);line-height:1.75}
.ledger-card-row b{font-variant-numeric:tabular-nums;color:var(--ink)}

.method{max-width:680px;margin:2.8em 0 0;padding:18px 22px 16px;background:var(--paper-2);border-left:3px solid var(--ochre);scroll-margin-top:120px}
.method .tag,.predict .tag,.framing .tag{font-family:var(--sans);font-size:9px;letter-spacing:.16em}
.method .tag{color:var(--ochre)}
.steps{display:flex;align-items:stretch;gap:0;margin-top:12px}
.steps>div{flex:1;min-width:0;padding:0 12px;border-left:1px solid var(--rule)}
.steps>div:first-child{border-left:0;padding-left:0}
.steps b{display:block;font-family:var(--sans);font-size:13px;line-height:1.5;color:var(--ink)}
.steps span{display:block;font-family:var(--sans);font-size:10px;line-height:1.6;color:var(--ink-3);margin-top:3px}
.steps i{font-style:normal;align-self:center;color:var(--ochre);font-family:var(--sans);font-size:16px;padding:0 2px}
.steps .result b{font-size:23px;letter-spacing:-.02em;color:var(--ochre);font-variant-numeric:tabular-nums}
.method-note{font-family:var(--sans);font-size:11px;line-height:1.8;color:var(--ink-2);margin:12px 0 0;padding-top:10px;border-top:1px solid var(--rule)}

.predict{max-width:680px;margin:2.6em 0;padding:20px 22px 18px;border:1px solid var(--rule-strong);border-top:3px solid var(--cinnabar);scroll-margin-top:120px}
.predict .tag{color:var(--cinnabar)}
.predict p{font-size:15.5px;line-height:1.85;margin:9px 0 14px}
.choices{display:flex;flex-wrap:wrap;gap:8px}
.choices button{font-family:var(--sans);font-size:12px;padding:7px 14px;border:1px solid var(--rule-strong);background:transparent;transition:.15s}
.choices button:hover{border-color:var(--cinnabar);color:var(--cinnabar)}
.choices button.on{background:var(--cinnabar);border-color:var(--cinnabar);color:var(--paper)}
.predict-result{font-family:var(--sans);font-size:11px;line-height:1.7;color:var(--ink-2);margin:12px 0 0;min-height:1.4em}

.framing{max-width:680px;margin:2.6em 0 0;padding:20px 22px 18px;background:var(--plate);border:1px solid var(--rule);border-top:2px solid var(--pine);scroll-margin-top:120px}
.framing-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.framing .tag{color:var(--pine)}
.seg{display:inline-flex;border:1px solid var(--rule-strong)}
.seg button{font-family:var(--sans);font-size:11px;padding:5px 11px;color:var(--ink-2)}
.seg button.on{background:var(--pine);color:var(--paper)}
.framing-stage{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.framing-cell{padding:12px 14px;background:var(--paper);border:1px solid var(--rule);transition:.2s}
.framing-cell span{display:block;font-family:var(--sans);font-size:9px;letter-spacing:.08em;color:var(--ink-3)}
.framing-cell b{display:block;font-family:var(--sans);font-size:23px;line-height:1.3;font-variant-numeric:tabular-nums;color:var(--ochre);margin-top:2px}
.framing-cell.base b{color:var(--ink-2)}
.framing-cell .delta{display:block;font-family:var(--sans);font-size:10px;letter-spacing:.03em;color:var(--ink-3);margin-top:2px}
.framing-cell.hot{border-color:var(--cinnabar);box-shadow:inset 0 0 0 1px var(--cinnabar)}
.framing-cell.hot b{color:var(--cinnabar)}
.framing-cell.hot .delta{color:var(--cinnabar)}
.framing-copy{font-family:var(--sans);font-size:11px;line-height:1.8;color:var(--ink-2);margin:12px 0 0;padding-top:10px;border-top:1px solid var(--rule)}

.takeaways{max-width:948px;margin:3.6em 0 2.2em;padding:28px 32px 24px;background:var(--pine);color:var(--paper);scroll-margin-top:120px}
.takeaways .tag{font-family:var(--sans);font-size:9px;letter-spacing:.18em;color:color-mix(in srgb,var(--paper) 65%,transparent)}
.takeaways h3{font-size:22px;line-height:1.4;margin:6px 0 16px;color:var(--paper);font-weight:600}
.takeaways ol{margin:0;padding:0;list-style:none;counter-reset:take}
.takeaways li{counter-increment:take;position:relative;padding-left:38px;font-size:14px;line-height:1.85;margin-bottom:12px;color:color-mix(in srgb,var(--paper) 88%,transparent)}
.takeaways li:before{content:"0" counter(take);position:absolute;left:0;top:0;font-family:var(--sans);font-size:11px;letter-spacing:.08em;color:var(--ochre)}
.takeaways li:last-child{margin-bottom:0}
.takeaways .next{margin:18px 0 0;padding-top:14px;border-top:1px solid color-mix(in srgb,var(--paper) 18%,transparent);font-family:var(--sans);font-size:11px;line-height:1.8;color:color-mix(in srgb,var(--paper) 65%,transparent)}

@media(max-width:820px){
  .plate-row{padding:20px 18px;margin:2.8em 0}
  .two-lives{grid-template-columns:1fr}
  .life+.life{border-left:0;border-top:1px solid var(--rule)}
  .ratio-row{grid-template-columns:1fr;gap:6px}
  .ratio-value{text-align:left}
  .ratio-gap{margin-left:0}
  .scale-note{margin-left:0;padding-left:0}
  .curve{max-width:100%}
  .steps{flex-direction:column;gap:8px}
  .steps>div{border-left:0;padding:0}
  .steps i{display:none}
  .ledger-wrap{display:none}
  .ledger-mobile{display:flex;flex-direction:column;gap:8px}
  .framing-stage{grid-template-columns:1fr 1fr;gap:8px}
  .framing-cell{padding:10px 10px}
  .framing-cell.base{display:none}
  .framing-cell b{font-size:21px}
  .framing-cell span{font-size:9px}
  .framing-cell .delta{font-size:10px}
  .framing-head{gap:8px}
  .spine{font-size:10px}
  .beat{margin-top:2.6em}
  .beat h3{font-size:21px}
  .plate-head{flex-wrap:wrap;gap:4px 12px}
  .takeaways{padding:22px 20px 20px;margin:3em 0 1.8em}
  .takeaways h3{font-size:20px}
}
@media(prefers-reduced-motion:reduce){
  .framing-cell,.choices button{transition:none}
}
"""

VISUAL_JS = r"""
document.addEventListener('DOMContentLoaded', () => {
  // prediction: remember the guess, do not spoil the next paragraphs
  document.querySelectorAll('[data-visual="prediction"]').forEach(pr => {
    const result = pr.querySelector('[data-result]');
    const reveal = pr.dataset.reveal || '';
    pr.querySelectorAll('[data-guess]').forEach(btn => {
      btn.addEventListener('click', () => {
        pr.querySelectorAll('[data-guess]').forEach(b => {
          b.classList.remove('on');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('on');
        btn.setAttribute('aria-pressed', 'true');
        if (result) {
          result.textContent = reveal.split('{guess}').join(btn.dataset.guess || '');
        }
      });
    });
  });

  // framing toggle: same absolute prices, different reference point
  document.querySelectorAll('[data-visual="framing"]').forEach(fr => {
    const baseEl = fr.querySelector('[data-role="base"]');
    const creditEl = fr.querySelector('[data-role="credit"]');
    const cashEl = fr.querySelector('[data-role="cash"]');
    const copyEl = fr.querySelector('[data-copy]');
    const money = (n) => '$' + Number(n).toFixed(2);
    const cents = (n) => Math.round(Number(n) * 100);
    const base = Number(fr.dataset.base || 1);
    const credit = Number(fr.dataset.credit || 1.03);
    const cash = Number(fr.dataset.cash || 1);
    const delta = cents(credit - base);
    const set = (mode) => {
      fr.dataset.mode = mode;
      const surcharge = mode === 'surcharge';
      fr.querySelectorAll('.seg button').forEach(b => {
        const on = b.dataset.mode === mode;
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (baseEl) {
        baseEl.querySelector('b').textContent = money(surcharge ? base : credit);
        baseEl.querySelector('.delta').textContent = '标价';
      }
      if (creditEl) {
        creditEl.querySelector('b').textContent = money(credit);
        creditEl.querySelector('.delta').textContent = surcharge ? '+' + delta + '¢ 附加费' : '按标价';
        creditEl.classList.toggle('hot', surcharge);
      }
      if (cashEl) {
        cashEl.querySelector('b').textContent = money(cash);
        cashEl.querySelector('.delta').textContent = surcharge ? '按基准价' : '−' + delta + '¢ 折扣';
        cashEl.classList.toggle('hot', !surcharge);
      }
      if (copyEl) {
        copyEl.textContent = surcharge ? (fr.dataset.surchargeCopy || '') : (fr.dataset.discountCopy || '');
      }
    };
    fr.querySelectorAll('.seg button').forEach(b => {
      b.addEventListener('click', () => set(b.dataset.mode));
    });
    set(fr.dataset.mode || 'surcharge');
  });
});
"""

PLATE_KINDS = ("two_lives", "ratio", "scale", "curve", "ledger")


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _tiers_attr(tiers) -> str:
    wanted = set(tiers or ())
    return " ".join(tier for tier in TIER_ORDER if tier in wanted)


def _br(value) -> str:
    return _esc(value).replace("\n", "<br>")


def _cite(visual: dict) -> str:
    blocks = []
    for claim in visual.get("claims") or []:
        block = claim.get("block")
        if block and block not in blocks:
            blocks.append(block)
    return " · ".join(blocks)


def _caption(visual: dict, css: str) -> str:
    caption = (visual.get("caption") or "").strip()
    cite = _cite(visual)
    if not caption and not cite:
        return ""
    body = _esc(caption)
    if cite:
        body += f'<span class="cite">依据 {_esc(cite)}</span>'
    return f'<p class="{css}">{body}</p>'


def render_spine(beats: list[dict]) -> str:
    if not beats:
        return ""
    links = []
    for index, beat in enumerate(beats, 1):
        tiers = _tiers_attr(beat["tiers"])
        links.append(
            f'<a href="#beat-{_esc(beat["id"])}" data-tiers="{tiers}"><b>{index:02d}</b>{_esc(beat["title"])}</a>'
        )
    return '<nav class="spine" data-origin="added" aria-label="章节节拍">' + "".join(links) + "</nav>"


def render_beat(beat: dict, index: int, total: int) -> str:
    question = ""
    if beat.get("question"):
        question = f'<p class="beat-q">{_esc(beat["question"])}</p>'
    return (
        f'<div class="beat" id="beat-{_esc(beat["id"])}" data-origin="added" '
        f'data-tiers="{_tiers_attr(beat["tiers"])}">'
        f'<span class="beat-no">{index:02d} / {total:02d}</span>'
        f"<h3>{_esc(beat['title'])}</h3>{question}</div>"
    )


def _two_lives(data: dict) -> str:
    statistical = int(data.get("statistical", 120))
    identified = int(data.get("identified", 1))
    dots = "<i></i>" * statistical
    one = '<span class="one-dot"></span>'
    count = f'<span class="life-count">×{identified}</span>' if identified > 1 else ""
    return (
        '<div class="two-lives">'
        '<div class="life identified">'
        f'<div class="life-mark">{one}</div>'
        '<div class="life-label"><b>可识别的生命</b>identified life</div>'
        f"<p>{_esc(data.get('identified_text', '一个 6 岁的女孩。有面孔，有故事，有眼泪。'))}</p>"
        f'<div class="life-facts">{_esc(data.get("identified_fact", "捐款能塞满邮局"))}{count}</div>'
        "</div>"
        '<div class="life statistical">'
        f'<div class="life-mark"><div class="dots">{dots}</div></div>'
        '<div class="life-label"><b>统计意义上的生命</b>statistical life</div>'
        f"<p>{_esc(data.get('statistical_text', '医院预算里的一串数字。没有名字，只有概率。'))}</p>"
        f'<div class="life-facts">{_esc(data.get("statistical_fact", "很少有人捐款"))}</div>'
        "</div></div>"
    )


def _ratio(data: dict) -> str:
    left = data["left"]
    right = data["right"]
    peak = max(float(left["value"]), float(right["value"]))
    left_width = max(0.4, float(left["value"]) / peak * 100)
    right_width = max(0.4, float(right["value"]) / peak * 100)
    note = data.get("ratio_note", "同一份风险变化，答案相差 250 倍。\n经济学模型却预测：两个答案应该几乎一样。")
    sub_left = f"<span>{_esc(left['sub'])}</span>" if left.get("sub") else ""
    sub_right = f"<span>{_esc(right['sub'])}</span>" if right.get("sub") else ""
    return (
        '<div class="ratio">'
        '<div class="ratio-row wtp">'
        f'<div class="ratio-label"><b>{_esc(left["label"])}</b>{sub_left}</div>'
        f'<div class="ratio-value">{_esc(left["display"])}</div>'
        f'<div class="ratio-bar"><i style="width:{left_width:.2f}%"></i></div>'
        "</div>"
        '<div class="ratio-row wta">'
        f'<div class="ratio-label"><b>{_esc(right["label"])}</b>{sub_right}</div>'
        f'<div class="ratio-value">{_esc(right["display"])}</div>'
        f'<div class="ratio-bar"><i style="width:{right_width:.2f}%"></i></div>'
        "</div>"
        f'<div class="ratio-gap"><b>{_esc(data["ratio_label"])}</b><span>{_br(note)}</span></div>'
        "</div>"
    )


def _scale(data: dict) -> str:
    bars = data["bars"]
    peak = max(float(bar["value"]) for bar in bars)
    rows = []
    for bar in bars:
        width = max(0.4, float(bar["value"]) / peak * 100)
        tone = bar.get("tone") or ""
        sub = bar.get("sub") or ""
        sub_html = "<span>" + _esc(sub) + "</span>" if sub else ""
        rows.append(
            f'<div class="ratio-row scale-row {tone}">'
            f'<div class="ratio-label"><b>{_esc(bar["label"])}</b>{sub_html}</div>'
            f'<div class="ratio-value">{_esc(bar["display"])}</div>'
            f'<div class="ratio-bar"><i style="width:{width:.2f}%"></i></div>'
            "</div>"
        )
    factor = data.get("factor")
    factor_html = (
        f'<div class="ratio-gap"><b>{_esc(factor)}</b><span>{_br(data.get("factor_note", ""))}</span></div>'
        if factor
        else ""
    )
    note = data.get("note")
    note_html = f'<p class="scale-note">{_br(note)}</p>' if note else ""
    return '<div class="ratio scale">' + "".join(rows) + factor_html + note_html + "</div>"


def _curve_points(shape: str, count: int = 60) -> list[tuple[float, float]]:
    points = []
    for index in range(count + 1):
        x = index / count
        if shape == "concave":
            u = math.sqrt(x)
        elif shape == "convex":
            u = x * x
        elif shape == "s_curve":
            raw = 1.0 / (1.0 + math.exp(-8.0 * (x - 0.5)))
            low = 1.0 / (1.0 + math.exp(4.0))
            high = 1.0 / (1.0 + math.exp(-4.0))
            u = (raw - low) / (high - low)
        elif shape == "loss_aversion":
            d = x - 0.5
            u = 0.5 + (0.9 * d if d >= 0 else 1.8 * d)
        else:
            u = x
        points.append((x, u))
    return points


def _curve_value(points: list[tuple[float, float]], x: float) -> float:
    x = max(0.0, min(1.0, x))
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def _curve(data: dict) -> str:
    width, height = 680, 300
    left, right, top, bottom = 64, 646, 30, 250

    def sx(x: float) -> float:
        return left + x * (right - left)

    def sy(u: float) -> float:
        return bottom - u * (bottom - top)

    grid = "".join(
        f'<line class="curve-grid" x1="{left}" y1="{sy(u):.1f}" x2="{right}" y2="{sy(u):.1f}" />'
        for u in (0.25, 0.5, 0.75)
    )
    axes = (
        f'<line class="curve-axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" />'
        f'<line class="curve-axis" x1="{left}" y1="{bottom}" x2="{left}" y2="{top}" />'
        f'<text class="curve-label" x="{right}" y="{bottom + 22}" text-anchor="end">{_esc(data["x_label"])}</text>'
        f'<text class="curve-label" x="{left - 12}" y="{top - 8}" text-anchor="start">{_esc(data["y_label"])}</text>'
    )
    colors = ("ochre", "cinnabar")
    series_html = []
    legend = []
    for index, item in enumerate(data["series"]):
        pts = _curve_points(item["shape"])
        path = "M " + " L ".join(f"{sx(x):.1f} {sy(u):.1f}" for x, u in pts)
        color = colors[index % len(colors)]
        series_html.append(f'<path class="curve-line {color}" d="{path}" />')
        legend.append(f'<span><i style="background:var(--{color})"></i>{_esc(item["label"])}</span>')
        for note in item.get("annotations") or []:
            nx = sx(float(note["x"]))
            ny = sy(_curve_value(pts, float(note["x"])))
            series_html.append(
                f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="3" fill="var(--{color})" />'
                f'<text class="curve-annot" x="{nx + 7:.1f}" y="{ny - 7:.1f}">{_esc(note["text"])}</text>'
            )
    note = data.get("note")
    note_html = f'<p class="curve-note">{_br(note)}</p>' if note else ""
    return (
        f'<div class="curve"><svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_esc(data["y_label"])}随{_esc(data["x_label"])}变化">'
        f"{grid}{axes}{''.join(series_html)}</svg>"
        f'<div class="curve-legend">{"".join(legend)}</div>{note_html}</div>'
    )


def _ledger(data: dict) -> str:
    rows = data["rows"]
    note = data.get("price_note")
    head = '<p class="price-note">' + _esc(note) + "</p>" if note else ""
    table_rows = []
    cards = []
    for row in rows:
        chosen = bool(row.get("chosen"))
        choice_cls = "yes" if chosen else "no"
        table_rows.append(
            f'<tr class="{"chosen" if chosen else ""}">'
            f"<th>{_esc(row['label'])}</th>"
            f"<td>{_esc(row['cash'])}</td>"
            f"<td>{_esc(row['opportunity'])}</td>"
            f'<td class="{choice_cls}">{_esc(row["choice"])}</td>'
            "</tr>"
        )
        cards.append(
            f'<div class="ledger-card {"chosen" if chosen else ""}">'
            f'<div class="ledger-card-head"><b>{_esc(row["label"])}</b>'
            f'<span class="{choice_cls}">{_esc(row["choice"])}</span></div>'
            f'<div class="ledger-card-row"><span>现金账</span><b>{_esc(row["cash"])}</b></div>'
            f'<div class="ledger-card-row"><span>机会成本账</span><b>{_esc(row["opportunity"])}</b></div>'
            "</div>"
        )
    return (
        head + '<div class="ledger-wrap"><table class="ledger">'
        "<thead><tr><th>选择</th><th>现金账</th><th>机会成本账</th><th>罗塞特的选择</th></tr></thead>"
        "<tbody>" + "".join(table_rows) + "</tbody></table></div>"
        '<div class="ledger-mobile">' + "".join(cards) + "</div>"
    )


def _steps(data: dict) -> str:
    parts = []
    for index, step in enumerate(data["steps"]):
        if index:
            parts.append("<i>+</i>")
        parts.append(f"<div><b>{_esc(step['label'])}</b><span>{_esc(step.get('detail', ''))}</span></div>")
    parts.append("<i>→</i>")
    result = data["result"]
    parts.append(
        f'<div class="result"><b>{_esc(result["label"])}</b><span>{_esc(result.get("detail", ""))}</span></div>'
    )
    return '<div class="steps">' + "".join(parts) + "</div>"


def _prediction(data: dict) -> str:
    options = "".join(
        f'<button type="button" data-guess="{_esc(option)}" aria-pressed="false">{_esc(option)}</button>'
        for option in data["options"]
    )
    hint = data.get("hint", "先选一个。答案就在下面两段。")
    return (
        '<span class="tag">' + _esc(data.get("tag", "读前猜一猜")) + "</span>"
        f"<p>{_esc(data['question'])}</p>"
        f'<div class="choices">{options}</div>'
        f'<p class="predict-result" data-result aria-live="polite">{_esc(hint)}</p>'
    )


def _framing(data: dict) -> str:
    base = float(data["base"])
    credit = float(data["credit"])
    cash = float(data["cash"])
    labels = data.get("labels") or {}
    base_label = labels.get("base", "基准价")
    credit_label = labels.get("credit", "信用卡")
    cash_label = labels.get("cash", "现金")
    delta = round((credit - base) * 100)
    return (
        '<div class="framing-head"><span class="tag">'
        + _esc(data.get("tag", "微交互 · 换个说法"))
        + '</span><div class="seg" role="group" aria-label="价格说法">'
        '<button type="button" data-mode="surcharge" class="on" aria-pressed="true">附加费</button>'
        '<button type="button" data-mode="discount" aria-pressed="false">折扣</button>'
        "</div></div>"
        '<div class="framing-stage">'
        f'<div class="framing-cell base" data-role="base"><span>{_esc(base_label)}</span>'
        f'<b>${base:.2f}</b><span class="delta">标价</span></div>'
        f'<div class="framing-cell hot" data-role="credit"><span>{_esc(credit_label)}</span>'
        f'<b>${credit:.2f}</b><span class="delta">+{delta}¢ 附加费</span></div>'
        f'<div class="framing-cell" data-role="cash"><span>{_esc(cash_label)}</span>'
        f'<b>${cash:.2f}</b><span class="delta">按基准价</span></div>'
        "</div>"
        '<p class="framing-copy" data-copy>' + _esc(data.get("surcharge_copy", "")) + "</p>"
    )


def render_visual(visual: dict, *, plate_no: int | None = None) -> str:
    kind = visual["kind"]
    tiers = _tiers_attr(visual["tiers"])
    ident = _esc(visual["id"])
    data = visual["data"]
    title = _esc(visual.get("title") or "")

    if kind == "two_lives":
        body = _two_lives(data)
        return (
            f'<div class="plate-row" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><div class="plate-head"><span class="plate-no">图版 '
            f"{plate_no or 1:02d}</span><h3>{title}</h3></div>{body}{_caption(visual, 'plate-cap')}</div>"
        )
    if kind == "ratio":
        body = _ratio(data)
        return (
            f'<div class="plate-row" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><div class="plate-head"><span class="plate-no">图版 '
            f"{plate_no or 1:02d}</span><h3>{title}</h3></div>{body}{_caption(visual, 'plate-cap')}</div>"
        )
    if kind == "scale":
        body = _scale(data)
        return (
            f'<div class="plate-row" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><div class="plate-head"><span class="plate-no">图版 '
            f"{plate_no or 1:02d}</span><h3>{title}</h3></div>{body}{_caption(visual, 'plate-cap')}</div>"
        )
    if kind == "curve":
        body = _curve(data)
        return (
            f'<div class="plate-row" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><div class="plate-head"><span class="plate-no">图版 '
            f"{plate_no or 1:02d}</span><h3>{title}</h3></div>{body}{_caption(visual, 'plate-cap')}</div>"
        )
    if kind == "ledger":
        body = _ledger(data)
        return (
            f'<div class="plate-row" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><div class="plate-head"><span class="plate-no">图版 '
            f"{plate_no or 1:02d}</span><h3>{title}</h3></div>{body}{_caption(visual, 'plate-cap')}</div>"
        )
    if kind == "steps":
        body = _steps(data)
        return (
            f'<div class="method" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}"><span class="tag">{_esc(data.get("tag", "方法 · 三步"))}</span>'
            f"{body}{_caption(visual, 'method-note')}</div>"
        )
    if kind == "prediction":
        body = _prediction(data)
        return (
            f'<div class="predict" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}" data-reveal="{_esc(data["reveal"])}">{body}</div>'
        )
    if kind == "framing":
        body = _framing(data)
        return (
            f'<div class="framing" id="{ident}" data-origin="added" data-tiers="{tiers}" '
            f'data-visual="{kind}" data-mode="surcharge" '
            f'data-base="{float(data["base"]):.4f}" data-credit="{float(data["credit"]):.4f}" '
            f'data-cash="{float(data["cash"]):.4f}" '
            f'data-surcharge-copy="{_esc(data.get("surcharge_copy", ""))}" '
            f'data-discount-copy="{_esc(data.get("discount_copy", ""))}">{body}</div>'
        )
    raise ValueError(f"未知图版类型：{kind}")


def render_takeaways(takeaways: list[dict], *, title: str = "", note: str = "", tiers=("standard", "deep")) -> str:
    if not takeaways:
        return ""
    items = []
    for item in takeaways:
        sources = " ".join(item.get("sources") or [])
        attr = f' data-sources="{_esc(sources)}"' if sources else ""
        items.append(f"<li{attr}>{_esc(item['text'])}</li>")
    heading = _esc(title) if title else "这一章留下的三件事"
    note_html = f'<p class="next">{_esc(note)}</p>' if note else ""
    return (
        f'<div class="takeaways" id="takeaways" data-origin="added" data-tiers="{_tiers_attr(tiers)}">'
        '<span class="tag">带走三件事</span>'
        f"<h3>{heading}</h3><ol>{''.join(items)}</ol>{note_html}</div>"
    )
