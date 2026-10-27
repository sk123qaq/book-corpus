(() => {
  'use strict';

  const STAGE_NAMES = ['理解问题', '词法循环', '打分与选章', '书卡', '结果'];
  const STAGE_KEYS = ['understand', 'rounds', 'rank', 'cards', 'result'];
  const STOP = { C: 'C 到线', 'A+B': 'A+B 到线', cap: '跑满 5 轮', exhausted: '词集用尽' };
  const THINKING = { off: '关', low: '低', medium: '中', high: '高' };
  const CONFIDENCE = { high: '高', medium: '中', low: '低' };
  const STATUS_TEXT = {
    retrieving: '检索中', answering: '回答中',
    'run-error': '检索失败', 'answer-error': '回答失败',
  };
  const THINKING_LEVEL = 'high';

  const state = {
    sessionId: '',
    turns: [],
    viewN: 0,
    selected: null,
    shown: null,
    drawer: { seq: 0, loading: false, data: null, error: '' },
    stats: null,
    busy: false,
    elapsed: 0,
    timer: null,
    error: '',
    epoch: 0,
    stripOpen: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const roundsEl = $('#rounds');
  const mainEl = $('#main');
  const panelEl = $('#panel');
  const hintEl = $('#hint');
  const turnCountEl = $('#turn-count');
  const field = $('#q');
  const sendBtn = $('#send');
  const drawerEl = $('#drawer');
  const toastEl = $('#toast');
  let railShown = 0;

  /* ---- 小工具 ---------------------------------------------------- */

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function plain(value) {
    return String(value == null ? '' : value)
      .replace(/^#{1,6}\s+/gm, '')
      .replace(/\*\*/g, '')
      .trim();
  }

  function fmtInt(value) {
    return String(value == null ? '' : value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function fmtSec(ms) {
    const n = Number(ms);
    if (!isFinite(n) || n <= 0) return '—';
    return (n / 1000).toFixed(1) + 's';
  }

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function cut(value, size) {
    const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    return text.length > size ? text.slice(0, size) + '…' : text;
  }

  function uniq(list) {
    const seen = new Set();
    return (list || []).filter((item) => {
      if (typeof item !== 'string' || !item.trim() || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
  }

  let toastTimer = null;

  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2200);
  }

  async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      data = null;
    }
    if (!res.ok || !data || data.ok === false) {
      throw new Error((data && data.error) || ('请求失败 ' + res.status));
    }
    return data;
  }

  function postJSON(url, body) {
    return fetchJSON(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  /* ---- 引文 ------------------------------------------------------ */

  function citesOf(turn) {
    const list = (turn && turn.answer && turn.answer.citations) || [];
    return list.filter((c) => c && typeof c.path === 'string' && c.path);
  }

  function bookOf(cite) {
    const named = cite.book_title || cite.book;
    if (named) return named;
    const path = String(cite.path || '');
    const head = path.startsWith('库/') ? path.slice(2) : path;
    return head.split('/md/')[0].split('/').pop() || '';
  }

  function bookKey(path) {
    const raw = String(path || '').replace(/^库\//, '');
    const at = raw.indexOf('/md/');
    return at >= 0 ? raw.slice(0, at) : '';
  }

  function chapterOf(cite) {
    if (cite.chapter) return cite.chapter;
    const name = String(cite.path || '').split('/').pop() || '';
    return name.replace(/^\d+[_、.\- ]*/, '').replace(/\.md$/, '');
  }

  function sameSpot(a, b) {
    if (!a || !b) return false;
    return a.path === b.path && (Number(a.line) || 0) === (Number(b.line) || 0);
  }

  function refOf(turn, cite) {
    const list = citesOf(turn);
    const index = list.findIndex((c) => sameSpot(c, cite));
    return index < 0 ? null : { citation: list[index], fromN: turn.n, cite: index + 1 };
  }

  function anchorRef(turn) {
    if (!turn || !turn.anchor) return null;
    for (const prev of state.turns) {
      if (prev.n >= turn.n) break;
      const ref = refOf(prev, turn.anchor);
      if (ref) return ref;
    }
    return { citation: turn.anchor, fromN: turn.n > 1 ? turn.n - 1 : turn.n, cite: 0 };
  }

  function viewTurn() {
    return state.turns.find((t) => t.n === state.viewN) || null;
  }

  function lastTurn() {
    return state.turns.length ? state.turns[state.turns.length - 1] : null;
  }

  function answering() {
    return state.turns.some((t) => t.status === 'answering');
  }

  function syncBusy() {
    state.busy = state.turns.some((t) => t.status === 'retrieving' || t.status === 'answering');
  }

  /* ---- 计时 ------------------------------------------------------ */

  function clockText() {
    return '已用 ' + state.elapsed + 's';
  }

  function tickClock() {
    document.querySelectorAll('[data-elapsed]').forEach((el) => {
      el.textContent = clockText();
    });
  }

  function startClock() {
    stopClock();
    state.elapsed = 0;
    state.timer = setInterval(() => {
      state.elapsed += 1;
      tickClock();
    }, 1000);
  }

  function stopClock() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  /* ---- 页头统计 -------------------------------------------------- */

  async function loadStats() {
    try {
      const data = await fetchJSON('/api/health');
      const meta = data.meta || {};
      state.stats = { books: meta.books, paras: meta.paras, chapters: meta.chapters };
    } catch (err) {
      state.stats = null;
    }
    renderStats();
  }

  function renderStats() {
    const stats = state.stats || {};
    document.querySelectorAll('[data-stat]').forEach((el) => {
      const value = stats[el.dataset.stat];
      el.textContent = typeof value === 'number' ? fmtInt(value) : '—';
    });
  }

  /* ---- 左栏 ------------------------------------------------------ */

  function usedLater(turn, k) {
    const cite = citesOf(turn)[k - 1];
    return state.turns.some((t) => t.n > turn.n && sameSpot(t.anchor, cite));
  }

  function railRel(turn) {
    if (turn.n === 1) return '';
    if (turn.related && turn.related.related === false) {
      return '<p class="rel none"><span class="from">独立问题 · 没有参考上一问</span></p>';
    }
    const ref = anchorRef(turn);
    if (ref) {
      return '<p class="rel"><span class="from">承接 ' + pad(ref.fromN) + '</span>'
        + (ref.cite ? '<span class="cite">[' + ref.cite + ']</span>' : '')
        + '<span class="qt">' + esc(cut(ref.citation.quote, 22)) + '</span></p>';
    }
    return '<p class="rel none"><span class="from">承接上一问 · 没有选落点</span></p>';
  }

  function roundHTML(turn) {
    const current = turn.n === state.viewN;
    const status = STATUS_TEXT[turn.status] || '';
    const caret = turn.status === 'retrieving' || turn.status === 'answering'
      ? '<i class="p-caret"></i>' : '';
    const result = (turn.trace && turn.trace.result) || {};
    const book = turn.anchor ? bookOf(turn.anchor) : (result.book_title || result.book || '');
    const ids = citesOf(turn).map((c, i) =>
      '<i class="' + (usedLater(turn, i + 1) ? 'on' : '') + '">[' + (i + 1) + ']</i>').join('');
    return '<li class="round' + (current ? ' is-current' : '') + '" data-turn="' + turn.n + '">'
      + '<span class="no">' + pad(turn.n) + (current ? ' · 当前' : '') + '</span>'
      + railRel(turn)
      + '<p class="q">' + esc(turn.question) + '</p>'
      + '<div class="meta">'
      + (book ? '<span class="chip">《' + esc(book) + '》</span>' : '')
      + (ids ? '<span class="ids">' + ids + '</span>' : '')
      + (status ? '<span class="round-status">' + status + caret + '</span>' : '')
      + '</div></li>';
  }

  /* ---- 中栏 ------------------------------------------------------ */

  function emptyHTML() {
    const error = state.error
      ? '<p class="answer-error">' + esc(state.error) + '</p>' : '';
    return '<section class="empty">'
      + '<h1>问一句，找到原文</h1>'
      + '<p>先问第一问，它会把命中的原文与依据递到你面前。之后点一条依据当落点，接着问。</p>'
      + '<div class="empty-samples">'
      + '<button class="chip" type="button" data-ask="乡土中国 差序格局">乡土中国 差序格局</button>'
      + '<button class="chip" type="button" data-ask="礼治社会靠什么维持秩序">礼治社会靠什么维持秩序</button>'
      + '<button class="chip" type="button" data-ask="三体 程心 执剑人">三体 程心 执剑人</button>'
      + '</div></section>' + error;
  }

  function relChip(turn) {
    if (turn.n === 1) return { text: '首问', scope: true };
    if (turn.related && turn.related.related === false) return { text: '独立问题', scope: true };
    const ref = anchorRef(turn);
    if (ref) {
      return {
        text: '承接 ' + pad(ref.fromN) + (ref.cite ? ' [' + ref.cite + ']' : ''),
        scope: false,
      };
    }
    return { text: '承接上一问 · 无落点', scope: true };
  }

  function kickerHTML(turn) {
    const chip = relChip(turn);
    const trace = turn.trace || {};
    const answer = turn.answer || {};
    const read = [
      trace.total_ms != null ? '检索 ' + fmtSec(trace.total_ms) : '',
      answer.ms != null ? '回答 ' + fmtSec(answer.ms) : '',
    ].filter(Boolean).join(' · ');
    return '<div class="kicker">'
      + '<span class="no">第 ' + turn.n + ' 问</span>'
      + '<span class="chip' + (chip.scope ? ' scope' : '') + '">' + esc(chip.text) + '</span>'
      + (read ? '<span class="read mono">' + esc(read) + '</span>' : '')
      + '</div>';
  }

  function stageMs(trace, index) {
    const stage = (trace && trace.stage_ms) || {};
    return fmtSec(stage[STAGE_KEYS[index]]);
  }

  function stageCells(turn) {
    const trace = turn.trace || {};
    const extract = trace.extract || {};
    const plan = trace.plan || {};
    const cards = trace.cards || {};
    const result = trace.result || {};
    const terms = extract.terms || [];
    const rounds = plan.rounds || [];
    const chapters = plan.chapters || [];
    const books = new Set((plan.ranking || []).map((b) => b && b.book).filter(Boolean));
    const cardList = cards.cards || [];
    return [
      { value: terms.length + ' 组词', note: terms.slice(0, 3).map(esc).join(' · ') },
      { value: rounds.length + ' 轮', note: esc(STOP[plan.stop] || '—') },
      { value: '评 ' + books.size + ' 本 · 选 ' + chapters.length + ' 章' },
      { value: cards.trigger ? cardList.length + ' 张' : '未触发' },
      { value: '《' + esc(turn.anchor ? bookOf(turn.anchor) : (result.book_title || result.book || '—'))
        + '》· ' + esc(turn.anchor ? (turn.anchor.title || '') : (result.chapter || '')) },
    ];
  }

  function stripHTML(turn) {
    const open = state.stripOpen;
    const steps = STAGE_NAMES.map((name, i) =>
      '<span class="step"><i class="dot"></i><span class="sn">' + pad(i + 1)
      + '</span><span class="sname">' + name + '</span></span>').join('');
    const cells = stageCells(turn).map((cell, i) =>
      '<div class="cell"><span class="cn">' + pad(i + 1) + '</span>'
      + '<span class="cname">' + STAGE_NAMES[i] + '</span>'
      + '<span class="cval">' + cell.value + '</span>'
      + (cell.note ? '<span class="cnote">' + cell.note + '</span>' : '')
      + '<span class="cms mono">' + stageMs(turn.trace, i) + '</span></div>').join('');
    return '<section class="strip' + (open ? ' is-open' : '') + '">'
      + '<button class="strip-bar" type="button" aria-expanded="' + open + '">'
      + '<span class="steps">' + steps + '</span>'
      + '<span class="strip-toggle mono"><span class="when-closed">展开</span>'
      + '<span class="when-open">收起</span><span aria-hidden="true">⌄</span></span>'
      + '</button><div class="cells">' + cells + '</div></section>';
  }

  function scopeHTML(turn) {
    const ref = anchorRef(turn);
    const independent = turn.related && turn.related.related === false;
    if (ref) {
      const cite = ref.citation;
      const on = state.selected && ref.cite > 0 && state.selected.fromN === ref.fromN
        && state.selected.cite === ref.cite;
      return '<div class="anchor"><button class="anchor-hit" type="button"'
        + ' data-cite="' + (ref.cite || '') + '" data-from="' + ref.fromN + '">'
        + '<span class="lbl">追问落点 · 来自 ' + pad(ref.fromN) + '</span>'
        + (ref.cite ? '<span class="cite' + (on ? ' is-on' : '') + '">[' + ref.cite + ']</span>' : '')
        + '<span class="quote">' + esc(cut(cite.quote, 28)) + '</span>'
        + '<span class="src">' + esc(bookOf(cite)) + ' · ' + esc(chapterOf(cite)) + ' →</span>'
        + '</button>'
        + (independent ? '<p class="anchor-note">判定为独立问题，没有参考上一轮上下文</p>' : '')
        + '</div>';
    }
    const text = independent ? '独立问题 · 没有参考上一问' : '没有选依据 · 这一问不带落点框';
    return '<div class="scope-row"><span class="lbl">范围</span>'
      + '<span class="quote">' + text + '</span></div>';
  }

  function answerBody(answer) {
    const parts = [];
    const lead = plain(answer.lead);
    if (lead) parts.push('<p class="answer-lead">' + esc(lead) + '</p>');
    const sections = Array.isArray(answer.sections) ? answer.sections : [];
    if (sections.length) {
      sections.forEach((section) => {
        if (!section) return;
        const head = plain(section.heading);
        const body = String(section.body || '').split(/\n+/).map(plain).filter(Boolean);
        parts.push('<section>' + (head ? '<h3>' + esc(head) + '</h3>' : '')
          + body.map((p) => '<p>' + esc(p) + '</p>').join('') + '</section>');
      });
    } else {
      String(answer.answer || '').split(/\n\s*\n/).map(plain).filter(Boolean)
        .forEach((p) => parts.push('<p>' + esc(p) + '</p>'));
    }
    if (!parts.length) parts.push('<p class="answer-loading">没有拿到回答正文</p>');
    return parts.join('');
  }

  function answerMeta(turn) {
    const data = turn.answer || {};
    const meta = [];
    if (data.confidence) meta.push('置信 ' + (CONFIDENCE[data.confidence] || data.confidence));
    const verified = citesOf(turn).filter((c) => c.verified).length;
    if (verified) meta.push(verified + ' 条引文已回查');
    const history = data.history || (turn.context
      ? { used: true, chars: turn.context.chars, budget: turn.context.budget } : null);
    if (history && history.used) {
      meta.push('接上一问 · 上下文 ' + fmtInt(history.chars) + '/' + fmtInt(history.budget) + ' 字');
    }
    if (data.cached) meta.push('缓存命中');
    if (data.ms != null) meta.push('回答 ' + fmtSec(data.ms));
    if (data.model) meta.push(data.model);
    if (data.thinking) meta.push('思考 ' + (THINKING[data.thinking] || data.thinking));
    if (data.error) meta.push(data.error);
    return meta.join(' · ');
  }

  function bodyHTML(turn) {
    if (turn.status === 'retrieving') {
      return '<p class="loading-card"><span class="dot-pulse"></span>正在检索，通常 20 到 60 秒 · '
        + '<span class="elapsed mono" data-elapsed>' + clockText() + '</span></p>';
    }
    if (turn.status === 'answering') {
      return '<p class="loading-card"><span class="dot-pulse"></span>正在组织回答，通常 40 到 90 秒 · '
        + '<span class="elapsed mono" data-elapsed>' + clockText() + '</span></p>';
    }
    if (turn.status === 'run-error') {
      return '<p class="answer-error">检索失败：' + esc(turn.runError || '未知错误') + '</p>'
        + '<button class="btn" type="button" data-retry-run="' + turn.n + '">重试</button>';
    }
    if (turn.status === 'answer-error') {
      return '<p class="answer-error">回答失败：' + esc(turn.answerError || '未知错误') + '</p>'
        + '<p class="answer-meta">检索结果已保留，重试只重发回答。</p>'
        + '<button class="btn" type="button" data-retry-answer="' + turn.n + '">重试回答</button>';
    }
    const answer = turn.answer || {};
    if (answer.ok === false) {
      return '<p class="answer-error">没有拿到回答：' + esc(answer.error || '未知错误') + '</p>';
    }
    const dissent = plain(answer.dissent);
    const gap = plain(answer.gap);
    return '<article class="answer">' + answerBody(answer) + '</article>'
      + (dissent ? '<p class="answer-small">分歧 · ' + esc(dissent) + '</p>' : '')
      + (gap ? '<p class="answer-small">没讲到的 · ' + esc(gap) + '</p>' : '')
      + '<p class="answer-meta">' + esc(answerMeta(turn)) + '</p>';
  }

  function evidenceHTML(turn) {
    const list = citesOf(turn);
    if (!list.length) return '<p class="empty">未找到可引用的原文</p>';
    const rows = list.map((cite, i) => {
      const k = i + 1;
      const on = state.selected && state.selected.fromN === turn.n && state.selected.cite === k;
      const pinned = sameSpot(turn.anchor, cite);
      return '<button class="ev' + (on ? ' is-on' : '') + (pinned ? ' is-anchor' : '')
        + '" type="button" data-cite="' + k + '" data-from="' + turn.n + '">'
        + '<span class="cite' + (on ? ' is-on' : '') + '">[' + k + ']</span>'
        + '<span><span class="ev-src">《' + esc(bookOf(cite)) + '》· ' + esc(chapterOf(cite))
        + (pinned ? '<span class="pin-tag">落点</span>' : '') + '</span>'
        + '<span class="ev-quote">' + esc(cite.quote || '') + '</span></span>'
        + '<span class="ev-go">' + (on ? '落点' : '原文') + '</span></button>';
    }).join('');
    return '<section class="evidences"><div class="ev-head">'
      + '<span class="t">本轮依据 · ' + list.length + ' 段</span>'
      + '<span class="h">点一段，右边的原文换过来，也设为下一问的落点</span></div>'
      + rows + '</section>';
  }

  function renderMain() {
    const turn = viewTurn();
    if (!turn) {
      mainEl.innerHTML = emptyHTML();
      return;
    }
    const parts = [kickerHTML(turn), '<h1 class="question">' + esc(turn.question) + '</h1>'];
    if (turn.trace) parts.push(stripHTML(turn));
    parts.push(scopeHTML(turn), bodyHTML(turn));
    if (turn.answer) parts.push(evidenceHTML(turn));
    mainEl.innerHTML = parts.join('');
  }

  /* ---- 右栏 ------------------------------------------------------ */

  function markQuote(line, quote) {
    const q = String(quote || '').trim();
    const at = q ? line.indexOf(q) : -1;
    if (at < 0) return esc(line);
    return esc(line.slice(0, at)) + '<mark>' + esc(q) + '</mark>' + esc(line.slice(at + q.length));
  }

  function chapterHTML(data, cite, fromN, k) {
    const text = String(data.text || '');
    const hitIndex = text.slice(0, Number(data.hit_start) || 0).split('\n').length - 1;
    const lines = text ? text.split('\n') : [];
    const body = lines.map((line, i) => {
      if (i === hitIndex) {
        return '<p class="current" data-cite="' + k + '" data-from="' + fromN + '">'
          + markQuote(line, cite.quote) + '</p>';
      }
      return '<p class="ctx">' + esc(line) + '</p>';
    }).join('');
    return '<p class="src-label">原文</p>' + (body || '<p class="ctx">没有读到这一段。</p>');
  }

  function renderPanel() {
    const ref = state.shown;
    if (!ref) {
      panelEl.innerHTML = '';
      return;
    }
    const cite = ref.citation;
    const owner = state.turns.find((t) => t.n === ref.fromN) || null;
    const view = viewTurn();
    const isSelected = state.selected && sameSpot(state.selected.citation, cite);
    const isAnchor = view && sameSpot(view.anchor, cite);
    const tags = (isSelected ? '<span class="pin">下一问的落点</span>' : '')
      + (isAnchor ? '<span class="pin">这一问的落点</span>' : '');
    const list = citesOf(owner);
    const pos = list.findIndex((c) => sameSpot(c, cite));
    let body = '';
    if (state.drawer.loading) body = '<p class="answer-loading">读取原文…</p>';
    else if (state.drawer.error) body = '<p class="answer-error">' + esc(state.drawer.error) + '</p>';
    else if (state.drawer.data) body = chapterHTML(state.drawer.data, cite, ref.fromN, ref.cite);
    const nav = list.length > 1
      ? '<div class="hop">'
        + '<button type="button" data-step="-1"' + (pos <= 0 ? ' disabled' : '') + '>‹ 上一条依据</button>'
        + '<button type="button" data-step="1"' + (pos >= list.length - 1 ? ' disabled' : '')
        + '>下一条依据 ›</button></div>'
      : '';
    panelEl.innerHTML = '<div class="drawer-head"><div class="dh-top">'
      + '<span class="cite">[' + (ref.cite || '·') + ']</span>'
      + '<span class="book">《' + esc(bookOf(cite)) + '》</span>' + tags + '</div>'
      + '<p class="dh-sub">' + esc(chapterOf(cite)) + '</p></div>'
      + '<div class="drawer-body">' + body + nav + '</div>'
      + '<div class="drawer-foot"><span class="path">' + esc(cite.path) + '</span>'
      + '<button class="btn" type="button" data-copy="' + esc(cite.path) + '">复制路径</button></div>';
  }

  function showCitation(ref) {
    state.shown = ref;
    const seq = state.drawer.seq + 1;
    if (!ref) {
      state.drawer = { seq, loading: false, data: null, error: '' };
      render();
      return;
    }
    state.drawer = { seq, loading: true, data: null, error: '' };
    render();
    const cite = ref.citation;
    const url = '/api/chapter?path=' + encodeURIComponent(cite.path || '')
      + '&line=' + (Number(cite.line) || 1) + '&chars=900&before=300';
    fetchJSON(url).then((data) => {
      if (state.drawer.seq !== seq) return;
      state.drawer = { seq, loading: false, data, error: '' };
      render();
    }).catch((err) => {
      if (state.drawer.seq !== seq) return;
      state.drawer = { seq, loading: false, data: null, error: err.message };
      render();
    });
  }

  /* ---- composer -------------------------------------------------- */

  function renderHint() {
    const empty = state.turns.length === 0;
    if (state.selected) {
      hintEl.innerHTML = '<span class="on">下一问挂在 <span class="cite">['
        + state.selected.cite + ']</span><span class="qt">'
        + esc(cut(state.selected.citation.quote, 20)) + '</span>· '
        + '<button class="x" type="button" data-clear="1">取消</button></span>';
      field.placeholder = '就 [' + state.selected.cite + '] 这一段接着问';
    } else if (empty) {
      hintEl.innerHTML = '';
      field.placeholder = '问一句，从书里找原文';
    } else {
      hintEl.innerHTML = '<span class="off">不选依据就是整轮追问 · ⏎ 发送</span>';
      field.placeholder = '接着问，不选依据也能问';
    }
    sendBtn.textContent = state.busy ? (answering() ? '回答中…' : '检索中…')
      : (state.turns.length ? '追问' : '提问');
    sendBtn.disabled = state.busy;
    document.querySelectorAll('[data-ask]').forEach((el) => { el.disabled = state.busy; });
  }

  function scrollRail() {
    if (state.viewN === railShown) return;
    railShown = state.viewN;
    if (window.innerWidth >= 1320) return;
    const cur = roundsEl.querySelector('.is-current');
    if (cur) roundsEl.scrollLeft = Math.max(0, cur.offsetLeft - 8);
  }

  window.addEventListener('resize', () => {
    railShown = 0;
    scrollRail();
  });

  function render() {
    syncBusy();
    roundsEl.innerHTML = state.turns.map(roundHTML).join('');
    turnCountEl.textContent = state.turns.length + ' 问';
    scrollRail();
    renderMain();
    renderPanel();
    renderHint();
  }

  /* ---- 会话动作 -------------------------------------------------- */

  function setUrl(sessionId) {
    const url = sessionId ? '/session?session=' + encodeURIComponent(sessionId) : '/session';
    try {
      history.replaceState(null, '', url);
    } catch (err) {
      // file:// 下 replaceState 会抛，页面照样能用
    }
  }

  function anchorPayload() {
    const ref = state.selected;
    if (!ref) return null;
    const cite = ref.citation || {};
    return {
      path: cite.path || '',
      line: Number(cite.line) || 1,
      title: cite.chapter || chapterOf(cite),
      quote: cite.quote || '',
    };
  }

  async function runTurn(turn) {
    const epoch = state.epoch;
    turn.status = 'retrieving';
    turn.runError = '';
    syncBusy();
    startClock();
    render();
    const body = { question: turn.question, thinking: THINKING_LEVEL };
    if (state.sessionId) {
      body.session_id = state.sessionId;
      body.turn = turn.n;
    }
    if (turn.anchor) body.anchor = turn.anchor;
    try {
      const data = await postJSON('/api/run', body);
      if (epoch !== state.epoch) return;
      turn.trace = data;
      turn.related = data.related || null;
      if (data.session_id && data.session_id !== state.sessionId) {
        state.sessionId = data.session_id;
        setUrl(state.sessionId);
      }
      await answerTurn(turn);
    } catch (err) {
      if (epoch !== state.epoch) return;
      turn.status = 'run-error';
      turn.runError = err.message;
      stopClock();
      syncBusy();
      render();
    }
  }

  async function answerTurn(turn, fresh) {
    const epoch = state.epoch;
    const trace = turn.trace || {};
    const result = trace.result || {};
    const extract = trace.extract || {};
    const plan = trace.plan || {};
    turn.status = 'answering';
    turn.answerError = '';
    syncBusy();
    startClock();
    render();
    const anchored = turn.anchor && turn.anchor.path;
    const answerPath = anchored ? turn.anchor.path : (result.path || '');
    const answerLine = Number(anchored ? turn.anchor.line : result.line) || 1;
    const body = {
      question: turn.question,
      book: anchored ? bookKey(answerPath) : (result.book || bookKey(answerPath)),
      path: answerPath,
      line: answerLine,
      terms: uniq([].concat(extract.terms || [], plan.nouns || [])).slice(0, 12),
      groups: Array.isArray(extract.groups) ? extract.groups : [],
      sources: [],
      session_id: state.sessionId,
      turn: turn.n,
      thinking: THINKING_LEVEL,
    };
    if (fresh) body.fresh = true;
    try {
      const data = await postJSON('/api/answer', body);
      if (epoch !== state.epoch) return;
      turn.answer = data;
      turn.status = 'ready';
      if (data.session_id && !state.sessionId) {
        state.sessionId = data.session_id;
        setUrl(state.sessionId);
      }
    } catch (err) {
      if (epoch !== state.epoch) return;
      turn.status = 'answer-error';
      turn.answerError = err.message;
    }
    stopClock();
    syncBusy();
    render();
    const first = defaultRef(turn);
    if (turn.status === 'ready' && first && state.viewN === turn.n) {
      showCitation(first);
    }
  }

  async function ask(question) {
    const text = String(question || '').trim();
    if (!text || state.busy) return;
    state.turns = state.turns.filter((t) => t.status !== 'run-error');
    const prev = lastTurn();
    const turn = {
      n: prev ? prev.n + 1 : 1,
      question: text,
      thinking: THINKING_LEVEL,
      trace: null,
      related: null,
      anchor: anchorPayload(),
      answer: null,
      context: null,
      status: 'retrieving',
      runError: '',
      answerError: '',
    };
    state.turns.push(turn);
    state.viewN = turn.n;
    state.error = '';
    field.value = '';
    field.closest('.ask-field').classList.remove('has-text');
    render();
    field.focus();
    await runTurn(turn);
  }

  function retryRun(n) {
    const turn = state.turns.find((t) => t.n === n);
    if (!turn || state.busy) return;
    turn.answer = null;
    turn.context = null;
    state.viewN = n;
    runTurn(turn);
  }

  function retryAnswer(n) {
    const turn = state.turns.find((t) => t.n === n);
    if (!turn || state.busy) return;
    state.viewN = n;
    answerTurn(turn, true);
  }

  function newSession() {
    state.epoch += 1;
    stopClock();
    state.sessionId = '';
    state.turns = [];
    state.viewN = 0;
    state.selected = null;
    state.shown = null;
    state.drawer = { seq: state.drawer.seq + 1, loading: false, data: null, error: '' };
    state.error = '';
    state.busy = false;
    state.elapsed = 0;
    state.stripOpen = false;
    field.value = '';
    field.closest('.ask-field').classList.remove('has-text');
    setUrl('');
    render();
    field.focus();
  }

  /* ---- 交互 ------------------------------------------------------ */

  function defaultRef(turn) {
    const anchor = anchorRef(turn);
    if (anchor) return anchor;
    const first = citesOf(turn)[0];
    return first ? { citation: first, fromN: turn.n, cite: 1 } : null;
  }

  function viewRound(n) {
    state.viewN = n;
    mainEl.scrollTop = 0;
    showCitation(defaultRef(viewTurn()));
  }

  function openDrawer() {
    if (window.matchMedia('(max-width: 1319px)').matches) drawerEl.classList.add('is-open');
  }

  function pick(fromN, k) {
    const turn = state.turns.find((t) => t.n === fromN);
    const citation = citesOf(turn)[Number(k) - 1];
    if (!citation) return;
    const same = state.selected && state.selected.fromN === turn.n
      && state.selected.cite === Number(k);
    if (same) {
      state.selected = null;
      render();
      return;
    }
    state.selected = { citation, fromN: turn.n, cite: Number(k) };
    openDrawer();
    showCitation(state.selected);
  }

  function stepCitation(delta) {
    const turn = state.shown
      ? (state.turns.find((t) => t.n === state.shown.fromN) || viewTurn())
      : viewTurn();
    const list = citesOf(turn);
    if (!list.length) return;
    const at = state.shown ? list.findIndex((c) => sameSpot(c, state.shown.citation)) : -1;
    const next = Math.min(list.length - 1, Math.max(0, (at < 0 ? 0 : at) + delta));
    openDrawer();
    showCitation({ citation: list[next], fromN: turn.n, cite: next + 1 });
  }

  async function copyPath(path) {
    try {
      await navigator.clipboard.writeText(path);
      toast('路径已复制');
    } catch (err) {
      const el = panelEl.querySelector('.drawer-foot .path');
      if (el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      toast('剪贴板不可用，请手动复制选中的路径');
    }
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('#drawer-toggle')) {
      drawerEl.classList.toggle('is-open');
      return;
    }
    const asker = e.target.closest('[data-ask]');
    if (asker) {
      ask(asker.dataset.ask);
      return;
    }
    if (e.target.closest('[data-clear]')) {
      state.selected = null;
      render();
      return;
    }
    const citer = e.target.closest('[data-cite]');
    if (citer && citer.dataset.cite) {
      pick(Number(citer.dataset.from), Number(citer.dataset.cite));
      return;
    }
    const runRetry = e.target.closest('[data-retry-run]');
    if (runRetry) {
      retryRun(Number(runRetry.dataset.retryRun));
      return;
    }
    const answerRetry = e.target.closest('[data-retry-answer]');
    if (answerRetry) {
      retryAnswer(Number(answerRetry.dataset.retryAnswer));
      return;
    }
    const stepper = e.target.closest('[data-step]');
    if (stepper) {
      stepCitation(Number(stepper.dataset.step));
      return;
    }
    const copier = e.target.closest('[data-copy]');
    if (copier) {
      copyPath(copier.dataset.copy);
      return;
    }
    const stripBar = e.target.closest('.strip-bar');
    if (stripBar) {
      state.stripOpen = !state.stripOpen;
      stripBar.closest('.strip').classList.toggle('is-open', state.stripOpen);
      stripBar.setAttribute('aria-expanded', String(state.stripOpen));
      return;
    }
    const round = e.target.closest('[data-turn]');
    if (round) {
      viewRound(Number(round.dataset.turn));
      return;
    }
    if (e.target.closest('#new-session')) {
      newSession();
      return;
    }
    if (e.target.closest('#send')) {
      ask(field.value);
    }
  });

  field.addEventListener('input', () => {
    field.closest('.ask-field').classList.toggle('has-text', field.value !== '');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    if (e.target && e.target.id === 'q') {
      e.preventDefault();
      ask(field.value);
    }
  });

  /* ---- 启动 ------------------------------------------------------ */

  function turnFromSaved(turn) {
    const answer = turn.answer && typeof turn.answer === 'object' ? turn.answer : null;
    const trace = turn.trace && typeof turn.trace === 'object' ? turn.trace : null;
    return {
      n: turn.n,
      question: turn.question || '',
      thinking: turn.thinking || '',
      trace,
      related: turn.related || null,
      anchor: turn.anchor || null,
      answer,
      context: turn.context || null,
      status: answer ? 'ready' : (trace ? 'answer-error' : 'run-error'),
      runError: '',
      answerError: '',
    };
  }

  async function openSession(sessionId) {
    try {
      const data = await fetchJSON('/api/session?id=' + encodeURIComponent(sessionId));
      const sess = data.session || {};
      state.sessionId = sess.id || sessionId;
      state.turns = (sess.turns || []).filter((t) => t && typeof t.n === 'number')
        .map(turnFromSaved);
      const last = lastTurn();
      const saved = last ? anchorRef(last) : null;
      state.selected = saved && saved.cite ? saved : null;
      state.viewN = last ? last.n : 0;
      state.error = '';
      render();
      if (last) showCitation(defaultRef(last));
    } catch (err) {
      state.error = '会话载入失败：' + err.message;
      state.sessionId = '';
      state.turns = [];
      state.viewN = 0;
      render();
    }
  }

  async function boot() {
    loadStats();
    const params = new URLSearchParams(location.search);
    const sessionId = params.get('session') || '';
    const question = params.get('q') || '';
    if (sessionId) {
      await openSession(sessionId);
      return;
    }
    render();
    if (question) {
      setUrl('');
      await ask(question);
    }
  }

  boot();
})();
