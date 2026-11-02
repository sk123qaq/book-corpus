(() => {
  'use strict';

  const STAGE_NAMES = ['理解问题', '词法循环', '打分与选章', '书卡', '结果'];
  const STAGE_KEYS = ['understand', 'rounds', 'rank', 'cards', 'result'];
  const STOP = { C: 'C 到线', 'A+B': 'A+B 到线', cap: '跑满 5 轮', exhausted: '词集用尽' };
  const KIND = { noun: '名词', 'noun+concept': '名词+概念', 'drop-generic': '去泛词' };
  const THINKING = { off: '关', low: '低', medium: '中', high: '高' };
  const CONFIDENCE = { high: '高', medium: '中', low: '低' };
  const STATUS_TEXT = {
    retrieving: '检索中', answering: '回答中',
    'run-error': '检索失败', 'answer-error': '回答失败',
  };
  const THINKING_LEVEL = 'high';
  const ALL_DONE = ['done', 'done', 'done', 'done', 'done'];
  const ALL_PENDING = ['pending', 'pending', 'pending', 'pending', 'pending'];
  const REPLAY_MS = 900;
  const HISTORY_PAGE = 10;

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
    rail: 'history',
    history: [],
    historyQuery: '',
    historyAll: false,
    historyError: '',
  };

  const $ = (sel) => document.querySelector(sel);
  const roundsEl = $('#rounds');
  const mainEl = $('#main');
  const panelEl = $('#panel');
  const hintEl = $('#hint');
  const turnCountEl = $('#turn-count');
  const historyCountEl = $('#history-count');
  const tabCurrentEl = $('#tab-current');
  const tabHistoryEl = $('#tab-history');
  const panelCurrentEl = $('#panel-current');
  const panelHistoryEl = $('#panel-history');
  const histListEl = $('#hist-list');
  const histFilterEl = $('#hist-filter');
  const railSubEl = $('#rail-sub');
  const railEmptyEl = $('#rail-empty');
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

  function fmtMs(ms) {
    const n = Number(ms);
    if (!isFinite(n) || n < 0) return '—';
    if (n < 10) return n.toFixed(1) + ' ms';
    if (n < 1000) return Math.round(n) + ' ms';
    return (n / 1000).toFixed(1) + ' s';
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function bookName(value) {
    const raw = String(value == null ? '' : value).trim();
    return raw ? raw.split('/').pop() : '';
  }

  function kindLabel(kind) {
    return KIND[kind] || String(kind || '—');
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
    const head = path.startsWith('books/') ? path.slice(6) : path;
    return head.split('/md/')[0].split('/').pop() || '';
  }

  function bookKey(path) {
    const raw = String(path || '').replace(/^books\//, '');
    const at = raw.indexOf('/md/');
    return at >= 0 ? raw.slice(0, at) : '';
  }

  function chapterOf(cite) {
    if (cite.chapter) return cite.chapter;
    const name = String(cite.path || '').split('/').pop() || '';
    return name.replace(/^\d+[_、.\- ]*/, '').replace(/\.md$/, '');
  }

  /* ---- 回答范围：一本书或几本书 ---------------------------------- */

  const MAX_SOURCES = 8;

  function sourceFromAnchor(anchor) {
    if (!anchor || typeof anchor.path !== 'string' || !anchor.path) return null;
    return {
      book: bookKey(anchor.path),
      title: bookOf(anchor),
      chapter: anchor.title || chapterOf(anchor),
      path: anchor.path,
      line: Number(anchor.line) || 1,
    };
  }

  function sourceFromResult(result) {
    if (!result || typeof result.path !== 'string' || !result.path) return null;
    return {
      book: result.book || bookKey(result.path),
      title: result.book_title || result.title || bookOf(result),
      chapter: result.chapter || result.title || chapterOf(result),
      path: result.path,
      line: Number(result.line) || 1,
    };
  }

  function sourceFromCard(card, result) {
    if (!card || typeof card.best_chapter !== 'string' || !card.best_chapter) return null;
    if (result && result.book && card.book === result.book) return sourceFromResult(result);
    const spot = { path: card.best_chapter, chapter: card.best_chapter_title };
    return {
      book: card.book || bookKey(card.best_chapter),
      title: card.title || bookOf(spot),
      chapter: card.best_chapter_title || chapterOf(spot),
      path: card.best_chapter,
      line: Number(card.line) || 1,
    };
  }

  function sourceFromAnswer(item) {
    if (!item || typeof item.path !== 'string' || !item.path) return null;
    return {
      book: item.book || bookKey(item.path),
      title: item.title || bookOf(item),
      chapter: item.chapter || chapterOf(item),
      path: item.path,
      line: Number(item.line) || 1,
    };
  }

  function sourceKey(source) {
    if (!source) return '';
    return source.book || bookKey(source.path) || source.path || '';
  }

  function cloneSource(source) {
    return {
      book: source.book || '',
      title: source.title || '',
      chapter: source.chapter || '',
      path: source.path || '',
      line: Number(source.line) || 1,
    };
  }

  function sameSources(left, right) {
    const a = left || [];
    const b = right || [];
    if (a.length !== b.length) return false;
    return a.every((s, i) => s.path === b[i].path
      && (Number(s.line) || 1) === (Number(b[i].line) || 1));
  }

  function cardSources(turn) {
    const trace = turn.trace || {};
    const cards = trace.cards || {};
    if (!cards.trigger) return [];
    const result = trace.result || {};
    return (Array.isArray(cards.cards) ? cards.cards : [])
      .map((card) => sourceFromCard(card, result)).filter(Boolean);
  }

  function defaultSources(turn) {
    const anchor = sourceFromAnchor(turn.anchor);
    if (anchor) return [anchor];
    const top = sourceFromResult((turn.trace || {}).result);
    if (top) return [top];
    const cards = cardSources(turn);
    return cards.length ? [cards[0]] : [];
  }

  function roleOf(source, anchor, top) {
    const key = sourceKey(source);
    if (anchor && sourceKey(anchor) === key) return 'anchor';
    if (top && sourceKey(top) === key) return 'top';
    return 'card';
  }

  function bookChoices(turn) {
    const anchor = sourceFromAnchor(turn.anchor);
    const top = sourceFromResult((turn.trace || {}).result);
    const seen = new Set();
    const rows = [];
    const push = (source, role) => {
      const key = sourceKey(source);
      if (!source || !key || seen.has(key)) return;
      seen.add(key);
      rows.push({ source, role, locked: role === 'anchor' });
    };
    (turn.sources || []).forEach((source) => push(source, roleOf(source, anchor, top)));
    push(anchor, 'anchor');
    push(top, 'top');
    cardSources(turn).forEach((source) => push(source, 'card'));
    return rows;
  }

  function scopeSources(turn) {
    const answer = turn.answer || {};
    if (answer.mode === 'multi' && Array.isArray(answer.sources) && answer.sources.length) {
      return answer.sources.map(sourceFromAnswer).filter(Boolean);
    }
    if (Array.isArray(turn.answerSources) && turn.answerSources.length) return turn.answerSources;
    return turn.sources || [];
  }

  function staleSources(turn) {
    const used = Array.isArray(turn.answerSources) ? turn.answerSources : [];
    return used.length > 0 && !sameSources(turn.sources, used);
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
    const scope = scopeSources(turn);
    const book = scope.length > 1
      ? scope.length + ' 本'
      : (scope[0] ? '《' + (scope[0].title || scope[0].book) + '》' : '');
    const ids = citesOf(turn).map((c, i) =>
      '<i class="' + (usedLater(turn, i + 1) ? 'on' : '') + '">[' + (i + 1) + ']</i>').join('');
    return '<li class="round' + (current ? ' is-current' : '') + '" data-turn="' + turn.n + '">'
      + '<span class="no">' + pad(turn.n) + (current ? ' · 当前' : '') + '</span>'
      + railRel(turn)
      + '<p class="q">' + esc(turn.question) + '</p>'
      + '<div class="meta">'
      + (book ? '<span class="chip">' + esc(book) + '</span>' : '')
      + (ids ? '<span class="ids">' + ids + '</span>' : '')
      + (status ? '<span class="round-status">' + status + caret + '</span>' : '')
      + '</div></li>';
  }

  /* ---- 中栏 ------------------------------------------------------ */

  function emptyHTML() {
    const error = state.error
      ? '<p class="answer-error">' + esc(state.error) + '</p>' : '';
    const last = state.history[0];
    const card = last ? ('<div class="start-last">'
      + '<span class="lbl mono">接着上次</span>'
      + '<button class="last-card" type="button" data-open-session="' + esc(last.id) + '">'
      + '<span class="last-when mono">' + esc(whenText(last.updated_at)) + '</span>'
      + '<span class="last-q">' + esc(last.question || last.last_question || '') + '</span>'
      + '<span class="last-meta">' + (Number(last.turns) || 0) + ' 问'
      + ((last.books || []).length ? ' · 《' + esc(last.books.join('》《')) + '》' : '')
      + '</span>'
      + '<span class="last-go" aria-hidden="true">→</span>'
      + '</button></div>') : '';
    return '<section class="start">'
      + '<p class="start-kicker mono">会话追问 · 书库检索</p>'
      + '<h1>问一句，<br>找到原文</h1>'
      + '<p class="start-lead">先问第一问，它会把命中的原文与依据递到你面前。'
      + '之后点一条依据当落点，接着问。</p>'
      + '<div class="start-samples">'
      + '<button class="chip" type="button" data-ask="乡土中国 差序格局">乡土中国 差序格局</button>'
      + '<button class="chip" type="button" data-ask="礼治社会靠什么维持秩序">礼治社会靠什么维持秩序</button>'
      + '<button class="chip" type="button" data-ask="三体 程心 执剑人">三体 程心 执剑人</button>'
      + '</div>' + card + '</section>' + error;
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

  function stageMsOf(trace, index) {
    const stage = (trace && trace.stage_ms) || {};
    return fmtMs(stage[STAGE_KEYS[index]]);
  }

  function stageDetails(turn) {
    const blank = { value: '—', lead: '', meta: '', field: null, raw: null };
    const trace = turn.trace || null;
    if (!trace) return [blank, blank, blank, blank, blank];
    const extract = trace.extract || {};
    const plan = trace.plan || {};
    const cards = trace.cards || {};
    const terms = Array.isArray(extract.terms) ? extract.terms : [];
    const rounds = Array.isArray(plan.rounds) ? plan.rounds : [];
    const ranking = Array.isArray(plan.ranking) ? plan.ranking : [];
    const chapters = Array.isArray(plan.chapters) ? plan.chapters : [];
    const cardList = Array.isArray(cards.cards) ? cards.cards : [];
    const scope = scopeSources(turn);
    const one = scope[0] || {};
    const top = ranking[0] || {};
    const topScore = Number(top.score);
    const topName = top.title || bookName(top.book) || '—';
    const bookCount = new Set(ranking.map((row) => row && row.book).filter(Boolean)).size;
    const cites = citesOf(turn);
    const result = trace.result || {};
    const answer = turn.answer || null;
    const resultBook = one.title || bookName(one.book) || result.book_title
      || bookName(result.book) || '—';
    const resultChapter = one.chapter || result.chapter || result.title || '—';
    const scoreText = isFinite(topScore) ? topScore.toFixed(2) : '—';

    return [
      {
        value: terms.length + ' 组词',
        lead: terms.length + ' 组词'
          + (terms.length ? ' · ' + terms.slice(0, 3).join(' · ') : ''),
        meta: terms.length + ' 个检索词 · '
          + (extract.source === 'model' ? '模型切词'
             : extract.source === 'cache' ? '命中切词缓存' : '切词'),
        field: null,
        raw: trace.extract,
      },
      {
        value: rounds.length + ' 轮',
        lead: rounds.length + ' 轮 · ' + (STOP[plan.stop] || '—'),
        meta: plan.best_round ? '取第 ' + plan.best_round + ' 轮' : '',
        field: {
          label: '轮次',
          chips: rounds.map((row) => kindLabel(row.kind) + ' · ' + fmtInt(row.hits) + ' 段'
            + (row.n === plan.best_round ? ' · 采用' : '')),
        },
        raw: {
          terms: plan.terms, nouns: plan.nouns, rounds: plan.rounds,
          stop: plan.stop, best_round: plan.best_round, ms: plan.ms,
        },
      },
      {
        value: '评 ' + bookCount + ' 本 · 选 ' + chapters.length + ' 章',
        lead: ranking.length
          ? 'top1 ' + topName + ' · ' + scoreText + ' 分 · 取 ' + chapters.length + ' 本书的章'
          : '没有命中',
        meta: '书级打分 ' + (plan.scheme || 'best_support') + ' · 分数相对第一名',
        field: {
          label: '排序',
          chips: ranking.slice(0, 3).map((row, i) => (i + 1) + '. '
            + (row.title || bookName(row.book)) + ' '
            + (isFinite(Number(row.score)) ? Number(row.score).toFixed(2) : '—')),
        },
        raw: { scheme: plan.scheme, ranking: plan.ranking, chapters: plan.chapters },
      },
      {
        value: cards.trigger ? cardList.length + ' 张' : '未触发',
        lead: cards.trigger ? cardList.length + ' 张书卡 · 触发' : '未触发 · 直接取 top1',
        meta: cards.trigger ? '不确定时，把候选递给你' : '书级第一名够稳，不弹卡',
        field: {
          label: '候选',
          chips: cardList.map((card) => '《' + (card.title || bookName(card.book)) + '》'),
        },
        raw: trace.cards,
      },
      {
        value: scope.length > 1
          ? scope.length + ' 本 · 跨书'
          : '《' + resultBook + '》· ' + resultChapter,
        lead: '《' + resultBook + '》· ' + resultChapter + ' · ' + cites.length + ' 条引文',
        meta: '读窗口 ' + fmtMs((trace.stage_ms || {}).result)
          + (answer && answer.ms != null ? ' · 回答 ' + fmtMs(answer.ms) : ' · 回答中'),
        field: {
          label: '命中',
          quote: cut(result.text || (cites[0] && cites[0].quote) || '', 80),
        },
        raw: { result: trace.result || null, answer },
      },
    ];
  }

  function rawHTML(raw) {
    if (raw == null) return '';
    let text = '';
    try {
      text = JSON.stringify(raw, null, 1) || '';
    } catch (err) {
      text = '';
    }
    if (text.length > 4096) text = text.slice(0, 4096) + '…';
    if (!text) return '';
    return '<details class="raw"><summary>原始数据</summary><pre>' + esc(text) + '</pre></details>';
  }

  function fieldHTML(field) {
    if (!field) return '';
    const body = field.quote != null
      ? '<p class="stage-quote">「' + esc(field.quote) + '」</p>'
      : '<div class="stage-chips">' + (field.chips || []).map((chip) =>
        '<span class="stage-chip">' + esc(chip) + '</span>').join('') + '</div>';
    return '<div class="v3-field"><span class="v3-k">' + esc(field.label) + '</span>'
      + '<span class="v3-v">' + body + '</span></div>';
  }

  function stageRowHTML(turn, detail, i, stateName) {
    const open = (turn.stageOpen || new Set()).has(i);
    const loading = !turn.trace && stateName === 'active' && i === 0;
    const waiting = turn.status === 'answering' && stateName === 'active' && i === 4;
    const cls = ['stage-row', 'v3-row', 'is-' + stateName];
    if (open) cls.push('is-open');
    if (loading) cls.push('is-loading');
    const done = (detail.lead ? '<p class="v3-lead">' + esc(detail.lead) + '</p>' : '')
      + (detail.meta ? '<p class="v3-meta">' + esc(detail.meta) + '</p>' : '')
      + fieldHTML(detail.field)
      + (waiting
        ? '<p class="v3-waiting"><i class="blink-dot"></i>正在读原文、组织回答…</p>' : '');
    return '<article class="' + cls.join(' ') + '" data-i="' + i + '">'
      + '<button class="stage-row-head v3-head" type="button" aria-expanded="' + open + '"'
      + (stateName === 'pending' ? ' disabled' : '') + '>'
      + '<span class="v3-num mono">' + pad(i + 1) + '</span>'
      + '<span class="v3-name stage-name">' + STAGE_NAMES[i] + '</span>'
      + '<span class="v3-val">' + esc(detail.value) + '</span>'
      + '<span class="v3-ms mono">' + stageMsOf(turn.trace, i) + '</span>'
      + '<span class="scaret" aria-hidden="true">' + (open ? '⌃' : '⌄') + '</span>'
      + '</button>'
      + '<div class="stage-row-body v3-body">'
      + '<div class="stage-body-done">' + done + '</div>'
      + '<p class="stage-loading"><i class="blink-dot"></i>正在理解这句话…</p>'
      + rawHTML(detail.raw)
      + '</div></article>';
  }

  function stagesHTML(turn) {
    const states = Array.isArray(turn.stageStates) ? turn.stageStates : ALL_DONE;
    const details = stageDetails(turn);
    const open = !!turn.stripOpen;
    const steps = STAGE_NAMES.map((name, i) =>
      '<span class="step is-' + (states[i] || 'pending') + '">'
      + '<i class="dot" aria-hidden="true"></i>'
      + '<span class="sn">' + pad(i + 1) + '</span>'
      + '<span class="sname">' + name + '</span></span>').join('');
    const rows = STAGE_NAMES.map((name, i) =>
      stageRowHTML(turn, details[i], i, states[i] || 'pending')).join('');
    return '<section class="stages' + (open ? ' is-open' : '')
      + '" id="stages" aria-label="检索进度">'
      + '<div class="strip"><button class="strip-bar" type="button" aria-expanded="'
      + open + '">'
      + '<span class="steps">' + steps + '</span>'
      + '<span class="strip-toggle"><span class="t-open">展开</span>'
      + '<span class="t-close">收起</span>'
      + '<span class="strip-caret" aria-hidden="true">⌄</span></span>'
      + '</button></div>'
      + '<div class="stage-stack v3">' + rows + '</div></section>';
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

  function booksHTML(turn) {
    const choices = bookChoices(turn);
    const chosen = turn.sources || [];
    if (choices.length < 2 || !chosen.length) return '';
    const last = lastTurn();
    const readonly = !last || last.n !== turn.n;
    const picked = new Set(chosen.map(sourceKey));
    const selectable = choices.slice(0, MAX_SOURCES);
    const allOn = selectable.every((row) => picked.has(sourceKey(row.source)));
    const head = '<div class="books-head">'
      + '<span class="t">候选书 · ' + choices.length + ' 本</span>'
      + '<span class="h">' + (readonly
        ? '历史轮次 · 书单只读'
        : '已选 ' + chosen.length + '/' + choices.length + ' 本') + '</span>'
      + (readonly ? ''
        : '<button class="books-all" type="button" data-books-all="' + turn.n + '">'
          + (allOn ? '清空' : '全选') + '</button>')
      + '</div>';
    const picks = choices.map((row, i) => {
      const key = sourceKey(row.source);
      const on = picked.has(key);
      const full = i >= MAX_SOURCES;
      const tag = row.role === 'anchor' ? '落点' : (row.role === 'top' ? '首选' : '');
      const tagText = full ? '最多 ' + MAX_SOURCES + ' 本' : tag;
      const cls = 'book-pick' + (on ? ' is-on' : '') + (row.locked ? ' is-locked' : '')
        + (full ? ' is-full' : '');
      const fullTitle = '《' + (row.source.title || row.source.book) + '》· '
        + (row.source.chapter || '');
      return '<button class="' + cls + '" type="button" data-book-pick="' + esc(key) + '"'
        + ' data-from="' + turn.n + '" title="' + esc(fullTitle) + '"'
        + (readonly || full ? ' disabled' : '') + '>'
        + '<span class="bp-title">《' + esc(cut(row.source.title || row.source.book, 18)) + '》</span>'
        + (tagText ? '<span class="bp-tag">' + esc(tagText) + '</span>' : '')
        + '</button>';
    }).join('');
    const notes = [];
    if (choices.length > MAX_SOURCES) {
      notes.push('候选超过 ' + MAX_SOURCES + ' 本，最多选 ' + MAX_SOURCES + ' 本。');
    }
    if (chosen.length > 1) notes.push('选中 2 本以上时，回答跨书，上下文预算 10,000 字。');
    const act = !readonly && turn.answer && staleSources(turn)
      ? '<div class="books-act"><span class="books-note">当前回答按 '
        + scopeSources(turn).length + ' 本生成</span>'
        + '<button class="btn btn-primary" type="button" data-reanswer="' + turn.n + '"'
        + (state.busy ? ' disabled' : '') + '>用这 ' + chosen.length + ' 本重新回答</button></div>'
      : '';
    return '<section class="books">' + head
      + '<div class="books-picks">' + picks + '</div>'
      + (notes.length ? '<div class="books-note">' + esc(notes.join(' ')) + '</div>' : '')
      + act + '</section>';
  }

  function answerBody(answer) {
    const parts = [];
    const sections = Array.isArray(answer.sections) ? answer.sections : [];
    if (sections.length) {
      const lead = plain(answer.lead);
      if (lead) parts.push('<p class="answer-lead">' + esc(lead) + '</p>');
      sections.forEach((section) => {
        if (!section) return;
        const head = plain(section.heading);
        const body = String(section.body || '').split(/\n+/).map(plain).filter(Boolean);
        parts.push('<section>' + (head ? '<h3>' + esc(head) + '</h3>' : '')
          + body.map((p) => '<p>' + esc(p) + '</p>').join('') + '</section>');
      });
    } else {
      String(answer.answer || answer.lead || '').split(/\n\s*\n/).map(plain).filter(Boolean)
        .forEach((p) => parts.push('<p>' + esc(p) + '</p>'));
    }
    if (!parts.length) parts.push('<p class="answer-loading">没有拿到回答正文</p>');
    return parts.join('');
  }

  function answerMeta(turn) {
    const data = turn.answer || {};
    const meta = [];
    if (data.confidence) meta.push('置信 ' + (CONFIDENCE[data.confidence] || data.confidence));
    const scope = scopeSources(turn);
    if (data.mode === 'multi' && scope.length > 1) meta.push(scope.length + ' 本书');
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
      const last = lastTurn();
      const canRetry = last && last.n === turn.n;
      return '<p class="answer-error">回答失败：' + esc(turn.answerError || '未知错误') + '</p>'
        + '<p class="answer-meta">' + (canRetry
          ? '检索结果已保留，重试只重发回答。'
          : '历史轮次，回答不再重试。') + '</p>'
        + (canRetry
          ? '<button class="btn" type="button" data-retry-answer="' + turn.n + '">重试回答</button>'
          : '');
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
    const top = mainEl.scrollTop;
    const turn = viewTurn();
    if (!turn) {
      mainEl.innerHTML = emptyHTML();
      mainEl.scrollTop = top;
      return;
    }
    const parts = [kickerHTML(turn), '<h1 class="question">' + esc(turn.question) + '</h1>',
      stagesHTML(turn), booksHTML(turn), scopeHTML(turn), bodyHTML(turn)];
    if (turn.answer) parts.push(evidenceHTML(turn));
    mainEl.innerHTML = parts.join('');
    mainEl.scrollTop = top;
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
      drawerEl.classList.add('is-empty');
      panelEl.innerHTML = '<div class="drawer-empty"><span class="lbl mono">原文</span>'
        + '<p>点一条依据，这里给出命中段与上下文。</p></div>';
      return;
    }
    drawerEl.classList.remove('is-empty');
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
    const sugs = document.querySelector('.ask-sugs');
    if (sugs) sugs.hidden = state.turns.length === 0;
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

  /* ---- 历史会话 -------------------------------------------------- */

  function parseStamp(stamp) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(String(stamp || ''));
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      Number(m[4]), Number(m[5]), Number(m[6]));
  }

  function dayGroup(stamp) {
    const at = parseStamp(stamp);
    if (!at) return '更早';
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const t = at.getTime();
    if (t >= today) return '今天';
    if (t >= today - 86400000) return '昨天';
    return '更早';
  }

  function whenText(stamp) {
    const at = parseStamp(stamp);
    if (!at) return '';
    const hm = pad(at.getHours()) + ':' + pad(at.getMinutes());
    const group = dayGroup(stamp);
    if (group === '今天') return hm;
    if (group === '昨天') return '昨天 ' + hm;
    return pad(at.getMonth() + 1) + '-' + pad(at.getDate()) + ' ' + hm;
  }

  function historyCount() {
    if (!state.sessionId) return state.history.length;
    const listed = state.history.some((row) => row.id === state.sessionId);
    return state.history.length + (listed ? 0 : 1);
  }

  function currentSummary() {
    if (state.turns.length) {
      const first = state.turns[0];
      const last = lastTurn() || first;
      const found = state.history.find((row) => row.id === state.sessionId);
      const books = [];
      state.turns.forEach((turn) => {
        scopeSources(turn).forEach((source) => {
          const name = source.title || bookName(source.book);
          if (name) books.push(name);
        });
      });
      return {
        id: state.sessionId,
        updated_at: last.at || (found && found.updated_at) || '',
        turns: state.turns.length,
        question: first.question || '',
        last_question: last.question || '',
        books: uniq(books).slice(0, 3),
      };
    }
    return state.history.find((row) => row.id === state.sessionId) || null;
  }

  function histRowHTML(row, current) {
    const books = (row.books || []).length ? '《' + row.books.join('》《') + '》' : '';
    return '<button class="hist-row' + (current ? ' is-current' : '') + '" type="button"'
      + ' data-open-session="' + esc(row.id) + '">'
      + '<span class="top"><span>' + esc(whenText(row.updated_at)) + '</span>'
      + '<span>' + (Number(row.turns) || 0) + ' 问'
      + (current ? ' <span class="pin">当前</span>' : '') + '</span></span>'
      + '<span class="q">' + esc(row.question || row.last_question || '') + '</span>'
      + '<span class="meta">' + esc(books) + '</span></button>';
  }

  function histMatch(row, q) {
    if (!q) return true;
    const text = [row.question, row.last_question].concat(row.books || []).join(' ');
    return text.toLowerCase().includes(q);
  }

  function renderHistory() {
    if (!histListEl) return;
    const q = state.historyQuery.trim().toLowerCase();
    const cur = currentSummary();
    const curId = cur ? cur.id : '';
    const rows = state.history.filter((row) => row.id !== curId);
    let html = '';
    let shown = 0;
    if (state.historyError) {
      html += '<p class="hist-empty">' + esc(state.historyError) + '</p>';
    }
    if (cur && histMatch(cur, q)) {
      html += '<p class="hist-group">当前打开</p>' + histRowHTML(cur, true);
      shown += 1;
    }
    const matched = rows.filter((row) => histMatch(row, q));
    const list = (q || state.historyAll)
      ? matched
      : matched.slice(0, Math.max(0, HISTORY_PAGE - shown));
    ['今天', '昨天', '更早'].forEach((group) => {
      const part = list.filter((row) => dayGroup(row.updated_at) === group);
      if (!part.length) return;
      html += '<p class="hist-group">' + group + '</p>'
        + part.map((row) => histRowHTML(row, false)).join('');
    });
    if (!q) {
      const rest = matched.length - list.length;
      if (rest > 0) {
        html += '<button class="hist-more" type="button" data-hist-more="1">更早的 '
          + rest + ' 个 ↓</button>';
      } else if (state.historyAll && matched.length > HISTORY_PAGE) {
        html += '<button class="hist-more" type="button" data-hist-collapse="1">收起</button>';
      }
    }
    if (!html) html = '<p class="hist-empty">没有匹配的会话。</p>';
    histListEl.innerHTML = html;
  }

  async function loadSessions() {
    try {
      const data = await fetchJSON('/api/sessions');
      state.history = Array.isArray(data.sessions) ? data.sessions : [];
      state.historyError = '';
    } catch (err) {
      state.history = [];
      state.historyError = '历史会话加载失败：' + err.message;
    }
    renderRail();
    if (!state.turns.length) renderMain();
  }

  /* ---- 左栏渲染 -------------------------------------------------- */

  function renderRail() {
    const histOn = state.rail === 'history';
    tabCurrentEl.classList.toggle('is-on', !histOn);
    tabHistoryEl.classList.toggle('is-on', histOn);
    tabCurrentEl.setAttribute('aria-selected', String(!histOn));
    tabHistoryEl.setAttribute('aria-selected', String(histOn));
    panelCurrentEl.hidden = histOn;
    panelHistoryEl.hidden = !histOn;
    turnCountEl.textContent = String(state.turns.length);
    if (historyCountEl) historyCountEl.textContent = String(historyCount());
    railSubEl.textContent = state.turns.length
      ? state.turns.length + ' 问 · 正在看第 ' + state.viewN + ' 问'
      : '还没有开始';
    railEmptyEl.hidden = state.turns.length > 0;
    roundsEl.innerHTML = state.turns.map(roundHTML).join('');
    scrollRail();
    renderHistory();
  }

  function render() {
    syncBusy();
    renderRail();
    renderMain();
    renderPanel();
    renderHint();
  }

  /* ---- 会话动作 -------------------------------------------------- */

  function setUrl(sessionId) {
    const url = sessionId ? pagePath() + '?session=' + encodeURIComponent(sessionId) : pagePath();
    try {
      history.replaceState(null, '', url);
    } catch (err) {
      // file:// 下 replaceState 会抛，页面照样能用
    }
  }

  function pagePath() {
    const path = location.pathname;
    if (!path || path === '/session' || path === '/session.html') return '/';
    return path;
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

  async function replayStages(turn) {
    const epoch = state.epoch;
    for (let i = 0; i < 4; i += 1) {
      if (epoch !== state.epoch) return false;
      turn.stageStates = [0, 1, 2, 3, 4].map((k) => {
        if (k < i) return 'done';
        return k === i ? 'active' : 'pending';
      });
      turn.stageOpen = new Set([i]);
      turn.stripOpen = true;
      render();
      await sleep(REPLAY_MS);
    }
    if (epoch !== state.epoch) return false;
    turn.stageStates = ['done', 'done', 'done', 'done', 'active'];
    turn.stageOpen = new Set([4]);
    turn.stripOpen = true;
    render();
    return true;
  }

  async function runTurn(turn) {
    const epoch = state.epoch;
    turn.status = 'retrieving';
    turn.runError = '';
    turn.stageStates = ['active', 'pending', 'pending', 'pending', 'pending'];
    turn.stageOpen = new Set([0]);
    turn.stripOpen = true;
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
      turn.sources = defaultSources(turn);
      turn.answerSources = [];
      if (data.session_id && data.session_id !== state.sessionId) {
        state.sessionId = data.session_id;
        setUrl(state.sessionId);
      }
      render();
      loadSessions();
      if (!(await replayStages(turn))) return;
      await answerTurn(turn);
    } catch (err) {
      if (epoch !== state.epoch) return;
      turn.status = 'run-error';
      turn.runError = err.message;
      turn.stageStates = ALL_PENDING.slice();
      turn.stageOpen = new Set();
      turn.stripOpen = false;
      stopClock();
      syncBusy();
      render();
    }
  }

  async function answerTurn(turn, opts) {
    const options = opts || {};
    const epoch = state.epoch;
    const trace = turn.trace || {};
    const extract = trace.extract || {};
    const plan = trace.plan || {};
    const sources = (options.sources || turn.sources || defaultSources(turn))
      .filter((s) => s && typeof s.path === 'string' && s.path)
      .slice(0, MAX_SOURCES)
      .map(cloneSource);
    if (!sources.length) {
      turn.status = 'answer-error';
      turn.answerError = '这一轮没有可用的来源';
      turn.stageStates = ALL_DONE.slice();
      turn.stageOpen = new Set();
      turn.stripOpen = false;
      stopClock();
      render();
      return;
    }
    turn.sources = sources;
    turn.answerSources = sources.map(cloneSource);
    turn.answer = null;
    turn.context = null;
    turn.status = 'answering';
    turn.answerError = '';
    turn.stageStates = ['done', 'done', 'done', 'done', 'active'];
    turn.stageOpen = new Set([4]);
    turn.stripOpen = true;
    syncBusy();
    startClock();
    render();
    const primary = sources[0];
    const body = {
      question: turn.question,
      book: primary.book || bookKey(primary.path),
      path: primary.path,
      line: Number(primary.line) || 1,
      terms: uniq([].concat(extract.terms || [], plan.nouns || [])).slice(0, 12),
      groups: Array.isArray(extract.groups) ? extract.groups : [],
      sources: sources.map((s) => ({
        book: s.book || '', title: s.title || '', path: s.path, line: Number(s.line) || 1,
      })),
      session_id: state.sessionId,
      turn: turn.n,
      thinking: THINKING_LEVEL,
    };
    if (options.fresh) body.fresh = true;
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
    if (epoch !== state.epoch) return;
    turn.stageStates = ALL_DONE.slice();
    turn.stageOpen = new Set();
    turn.stripOpen = false;
    stopClock();
    syncBusy();
    render();
    loadSessions();
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
      sources: [],
      answerSources: [],
      status: 'retrieving',
      runError: '',
      answerError: '',
      stageStates: ['active', 'pending', 'pending', 'pending', 'pending'],
      stageOpen: new Set([0]),
      stripOpen: true,
    };
    state.turns.push(turn);
    state.viewN = turn.n;
    state.rail = 'current';
    state.error = '';
    field.value = '';
    field.closest('.ask-field').classList.remove('has-text');
    render();
    field.focus();
    await runTurn(turn);
  }

  function retryRun(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    turn.answer = null;
    turn.context = null;
    state.viewN = n;
    state.epoch += 1;
    runTurn(turn);
  }

  function retryAnswer(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    state.viewN = n;
    answerTurn(turn, { fresh: true });
  }

  function toggleBook(n, key) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n) return;
    const choice = bookChoices(turn).find((row) => sourceKey(row.source) === key);
    if (!choice) return;
    if (choice.locked) {
      toast('落点那本固定入选，先取消落点再换');
      return;
    }
    const chosen = turn.sources || [];
    const on = chosen.some((s) => sourceKey(s) === key);
    if (on) {
      if (chosen.length <= 1) {
        toast('至少留一本');
        return;
      }
      turn.sources = chosen.filter((s) => sourceKey(s) !== key);
    } else {
      if (chosen.length >= MAX_SOURCES) {
        toast('最多 ' + MAX_SOURCES + ' 本');
        return;
      }
      turn.sources = chosen.concat([cloneSource(choice.source)]);
    }
    render();
  }

  function toggleAllBooks(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n) return;
    const selectable = bookChoices(turn).slice(0, MAX_SOURCES);
    const picked = new Set((turn.sources || []).map(sourceKey));
    const allOn = selectable.every((row) => picked.has(sourceKey(row.source)));
    turn.sources = allOn
      ? defaultSources(turn)
      : selectable.map((row) => cloneSource(row.source));
    render();
  }

  function reanswer(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    answerTurn(turn, { sources: turn.sources, fresh: false });
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
    state.rail = 'history';
    state.historyAll = false;
    state.historyQuery = '';
    if (histFilterEl) histFilterEl.value = '';
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
    const picker = e.target.closest('[data-book-pick]');
    if (picker) {
      toggleBook(Number(picker.dataset.from), picker.dataset.bookPick);
      return;
    }
    const allBooks = e.target.closest('[data-books-all]');
    if (allBooks) {
      toggleAllBooks(Number(allBooks.dataset.booksAll));
      return;
    }
    const reanswerBtn = e.target.closest('[data-reanswer]');
    if (reanswerBtn) {
      reanswer(Number(reanswerBtn.dataset.reanswer));
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
      const turn = viewTurn();
      if (turn) {
        turn.stripOpen = !turn.stripOpen;
        render();
      }
      return;
    }
    const stageHead = e.target.closest('.stage-row-head');
    if (stageHead) {
      const turn = viewTurn();
      const row = stageHead.closest('.stage-row');
      if (turn && row) {
        const i = Number(row.dataset.i);
        if (Number.isInteger(i)) {
          if (turn.stageOpen.has(i)) turn.stageOpen.delete(i);
          else turn.stageOpen.add(i);
          render();
        }
      }
      return;
    }
    const opener = e.target.closest('[data-open-session]');
    if (opener) {
      const id = opener.dataset.openSession;
      const cur = currentSummary();
      if (cur && cur.id === id && state.turns.length) {
        const last = lastTurn();
        state.rail = 'current';
        state.viewN = last ? last.n : state.viewN;
        render();
        return;
      }
      if (state.busy) {
        toast('这一问还在跑，落地后再切');
        return;
      }
      openSession(id);
      return;
    }
    if (e.target.closest('[data-hist-more]')) {
      state.historyAll = true;
      renderHistory();
      return;
    }
    if (e.target.closest('[data-hist-collapse]')) {
      state.historyAll = false;
      renderHistory();
      return;
    }
    if (e.target.closest('#tab-current')) {
      state.rail = 'current';
      render();
      return;
    }
    if (e.target.closest('#tab-history')) {
      state.rail = 'history';
      render();
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

  if (histFilterEl) {
    histFilterEl.addEventListener('input', () => {
      state.historyQuery = histFilterEl.value;
      renderHistory();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    if (e.target && e.target.id === 'q') {
      e.preventDefault();
      ask(field.value);
    }
  });

  /* ---- 启动 ------------------------------------------------------ */

  function savedSources(turn) {
    const answer = turn.answer || {};
    if (answer.mode === 'multi' && Array.isArray(answer.sources) && answer.sources.length) {
      return answer.sources.map(sourceFromAnswer).filter(Boolean);
    }
    const anchor = sourceFromAnchor(turn.anchor);
    if (anchor) return [anchor];
    const top = sourceFromResult((turn.trace || {}).result);
    if (top) return [top];
    const cite = citesOf(turn).find((c) => c.verified) || citesOf(turn)[0];
    if (cite && cite.path) {
      return [{
        book: bookKey(cite.path),
        title: bookOf(cite),
        chapter: chapterOf(cite),
        path: cite.path,
        line: Number(cite.line) || 1,
      }];
    }
    return [];
  }

  function turnFromSaved(turn) {
    const answer = turn.answer && typeof turn.answer === 'object' ? turn.answer : null;
    const trace = turn.trace && typeof turn.trace === 'object' ? turn.trace : null;
    const row = {
      n: turn.n,
      question: turn.question || '',
      thinking: turn.thinking || '',
      trace,
      related: turn.related || null,
      anchor: turn.anchor || null,
      answer,
      context: turn.context || null,
      sources: [],
      answerSources: [],
      status: answer ? 'ready' : (trace ? 'answer-error' : 'run-error'),
      runError: '',
      answerError: '',
      stageStates: ALL_DONE.slice(),
      stageOpen: new Set(),
      stripOpen: false,
    };
    const restored = savedSources(row);
    row.answerSources = restored.map(cloneSource);
    row.sources = restored.map(cloneSource);
    const anchor = sourceFromAnchor(row.anchor);
    if (anchor && !row.sources.some((s) => sourceKey(s) === sourceKey(anchor))) {
      row.sources = [anchor].concat(row.sources);
    }
    return row;
  }

  async function openSession(sessionId) {
    state.epoch += 1;
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
      state.rail = 'current';
      state.historyAll = false;
      setUrl(state.sessionId);
      render();
      if (last) showCitation(defaultRef(last));
    } catch (err) {
      state.error = '会话载入失败：' + err.message;
      state.sessionId = '';
      state.turns = [];
      state.viewN = 0;
      state.rail = 'current';
      setUrl('');
      render();
    }
  }

  async function boot() {
    loadStats();
    loadSessions();
    const params = new URLSearchParams(location.search);
    const sessionId = params.get('session') || '';
    const question = params.get('q') || '';
    if (location.pathname !== pagePath()) {
      try {
        history.replaceState(null, '', pagePath() + (location.search || ''));
      } catch (err) {
        // 同 setUrl：拿不到 history 时只影响地址栏
      }
    }
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
