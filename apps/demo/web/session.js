(() => {
  'use strict';

  const STAGE_NAMES = ['理解问题', '词法循环', '打分与选章', '书卡', '结果'];
  const STAGE_KEYS = ['understand', 'rounds', 'rank', 'cards', 'result'];
  const STOP = { C: 'C 到线', 'A+B': 'A+B 到线', cap: '跑满 5 轮', exhausted: '词集用尽' };
  const KIND = { noun: '全词', full: '全词', 'drop-generic': '去泛词' };
  const THINKING = { off: '关', low: '低', medium: '中', high: '高' };
  const CONFIDENCE = { high: '高', medium: '中', low: '低' };
  const STATUS_TEXT = {
    retrieving: '检索中', answering: '回答中', paused: '已暂停',
    'run-error': '检索失败', 'answer-error': '回答失败',
    'answer-interrupted': '回答中断', 'answer-incomplete': '未回答',
  };
  const THINKING_LEVELS = ['off', 'low', 'medium', 'high'];
  const THINKING_DEFAULT = 'low';
  const THINKING_KEY = 'booksearch.thinking';
  const THEME_STORAGE_KEY = 'booksearch-color-scheme';
  const ALL_DONE = ['done', 'done', 'done', 'done', 'done'];
  const ALL_PENDING = ['pending', 'pending', 'pending', 'pending', 'pending'];
  const REPLAY_MS = 900;
  const HISTORY_PAGE = 10;
  const ANSWER_POLL_MS = 2000;
  const ANSWER_POLL_MAX = 5 * 60 * 1000;
  const PRESET_THRESHOLDS = [0.35, 0.45, 0.60, 0.75];
  const INT_FMT = new Intl.NumberFormat('zh-CN');

  const ICON_CHECK = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_PLUS = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>';
  const ICON_OUT = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7"/><path d="M7 7h10v10"/></svg>';

  const state = {
    sessionId: '',
    turns: [],
    viewN: 0,
    thinking: THINKING_DEFAULT,
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
    historySelected: new Set(),
    historyVisible: [],
    historyDeleting: false,
    historySelectMode: false,
    scopeBooks: [],
    scopeOrigin: '',
    excludeBooks: [],
    relatedMeta: null,
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
  const histBarEl = $('#hist-selbar');
  const railSubEl = $('#rail-sub');
  const railEmptyEl = $('#rail-empty');
  const field = $('#q');
  const sendBtn = $('#send');
  const thinkSelectEl = $('#think-select');
  const railEl = $('#rail');
  const drawerEl = $('#drawer');
  const scrimEl = $('#drawer-scrim');
  const composerEl = document.querySelector('.composer-dock');
  const toastEl = $('#toast');
  const confirmDialogEl = $('#confirm-dialog');

  function normalizeThinking(v) {
    const raw = String(v || '').trim().toLowerCase();
    return THINKING_LEVELS.includes(raw) ? raw : THINKING_DEFAULT;
  }

  function savedThinking() {
    try {
      return normalizeThinking(localStorage.getItem(THINKING_KEY));
    } catch (err) {
      return THINKING_DEFAULT;
    }
  }

  function setThinking(v, persist = false) {
    state.thinking = normalizeThinking(v);
    if (thinkSelectEl) thinkSelectEl.value = state.thinking;
    if (!persist) return;
    try {
      localStorage.setItem(THINKING_KEY, state.thinking);
    } catch (err) {
      // 同 setUrl：拿不到 storage 时只保留当前页状态
    }
  }
  let railShown = 0;

  /* ---- 主题切换 (Modern Web Guidance dark-mode-toggle) ------------ */

  const colorMeta = document.querySelector('meta[name="color-scheme"]');
  const themeMeta = document.getElementById('meta-theme-color');
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

  function currentRenderedTheme() {
    const content = colorMeta ? colorMeta.getAttribute('content') : 'light dark';
    if (content === 'dark') return 'dark';
    if (content === 'light') return 'light';
    return darkQuery.matches ? 'dark' : 'light';
  }

  function syncThemeUI() {
    const rendered = currentRenderedTheme();
    if (themeMeta) themeMeta.setAttribute('content', rendered === 'dark' ? '#161716' : '#F4F1E8');
    const btn = $('#theme-toggle');
    if (btn) {
      const nextLabel = rendered === 'dark' ? '切换宣纸日间主题' : '切换墨夜主题';
      btn.setAttribute('aria-label', nextLabel);
      btn.setAttribute('title', nextLabel);
    }
  }

  function applyStoredScheme(value) {
    if (!colorMeta) return;
    colorMeta.setAttribute('content', value === 'light' || value === 'dark' ? value : 'light dark');
    syncThemeUI();
  }

  const themeBtn = $('#theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const target = currentRenderedTheme() === 'dark' ? 'light' : 'dark';
      const osScheme = darkQuery.matches ? 'dark' : 'light';
      document.documentElement.classList.add('theme-switching');
      try {
        if (target !== osScheme) {
          localStorage.setItem(THEME_STORAGE_KEY, target);
        } else {
          localStorage.removeItem(THEME_STORAGE_KEY);
        }
      } catch (err) {
        // 隐私模式下忽略 storage 错误
      }
      applyStoredScheme(target !== osScheme ? target : 'light dark');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => document.documentElement.classList.remove('theme-switching'));
      });
      toast(target === 'dark' ? '已切换至墨夜主题' : '已切换至宣纸日间主题');
    });
  }

  darkQuery.addEventListener('change', syncThemeUI);
  window.addEventListener('storage', (e) => {
    if (e.key === THEME_STORAGE_KEY) applyStoredScheme(e.newValue);
  });
  syncThemeUI();

  /* ---- 原生确认弹窗 (Modern Web Guidance dialog) ------------------ */

  let confirmResolver = null;

  function openConfirmDialog({ title, desc, okLabel = '确认', danger = true }) {
    if (!confirmDialogEl || typeof confirmDialogEl.showModal !== 'function') {
      return Promise.resolve(window.confirm(title + (desc ? '\n' + desc : '')));
    }
    const titleEl = $('#confirm-dlg-title');
    const descEl = $('#confirm-dlg-desc');
    const okEl = $('#confirm-dlg-ok');
    if (titleEl) titleEl.textContent = title || '确认操作？';
    if (descEl) descEl.textContent = desc || '';
    if (okEl) {
      okEl.textContent = okLabel;
      okEl.className = 'btn ' + (danger ? 'danger' : 'primary');
    }
    if (confirmResolver) {
      const prev = confirmResolver;
      confirmResolver = null;
      prev(false);
    }
    return new Promise((resolve) => {
      confirmResolver = resolve;
      confirmDialogEl.showModal();
    });
  }

  if (confirmDialogEl) {
    confirmDialogEl.addEventListener('close', () => {
      const val = confirmDialogEl.returnValue;
      confirmDialogEl.returnValue = '';
      if (confirmResolver) {
        const resolve = confirmResolver;
        confirmResolver = null;
        resolve(val === 'confirm');
      }
    });
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e) => {
        const r = confirmDialogEl.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      confirmDialogEl.addEventListener('pointerdown', (e) => { pressedOutside = outside(e); });
      confirmDialogEl.addEventListener('pointerup', (e) => {
        if (pressedOutside && outside(e)) confirmDialogEl.close('cancel');
      });
    }
  }

  /* ---- 移动端抽屉与 inert 管理 ----------------------------------- */

  let lastDrawerTrigger = null;

  function setBackgroundInert(isInert) {
    [mainEl, composerEl].forEach((el) => {
      if (el) el.inert = isInert;
    });
  }

  function closeDrawers(restoreFocus = false) {
    const wasOpen = (railEl && railEl.classList.contains('is-open'))
      || (drawerEl && drawerEl.classList.contains('is-open'));
    if (railEl) railEl.classList.remove('is-open');
    if (drawerEl) drawerEl.classList.remove('is-open');
    document.body.classList.remove('drawer-is-open');
    if (scrimEl) {
      scrimEl.classList.remove('is-open');
      scrimEl.hidden = true;
    }
    setBackgroundInert(false);
    if (restoreFocus && wasOpen && lastDrawerTrigger && lastDrawerTrigger.isConnected) {
      lastDrawerTrigger.focus();
      lastDrawerTrigger = null;
    }
  }

  function openMobileDrawer(target, triggerEl) {
    if (!target) return;
    closeDrawers(false);
    lastDrawerTrigger = triggerEl || document.activeElement;
    if (scrimEl) {
      scrimEl.hidden = false;
      requestAnimationFrame(() => scrimEl.classList.add('is-open'));
    }
    target.classList.add('is-open');
    if (target === drawerEl) document.body.classList.add('drawer-is-open');
    setBackgroundInert(true);
    const firstFocusable = target.querySelector('button:not([disabled]), input, a[href]');
    if (firstFocusable) firstFocusable.focus({ preventScroll: true });
  }

  const openRailBtn = $('#btn-open-rail');
  if (openRailBtn) openRailBtn.addEventListener('click', (e) => openMobileDrawer(railEl, e.currentTarget));
  if (scrimEl) scrimEl.addEventListener('click', () => closeDrawers(true));

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
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value == null ? '' : value);
    return INT_FMT.format(n);
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

  function networkError(message) {
    return /failed to fetch|networkerror|load failed|network|连接/i.test(String(message || ''));
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

  function postJSON(url, body, fetchOpts) {
    return fetchJSON(url, Object.assign({
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }, fetchOpts || {}));
  }

  function parseSSEFrame(rawFrame, onEvent) {
    const lines = rawFrame.replace(/\r/g, '').split('\n');
    let eventName = '';
    const dataLines = [];
    for (let i = 0; i < lines.length; i++) {
      const ln = lines[i];
      if (!ln || ln[0] === ':') continue;
      if (ln.startsWith('event:')) eventName = ln.slice(6).trim();
      else if (ln.startsWith('data:')) dataLines.push(ln.slice(5).trimStart());
    }
    if (!dataLines.length) return null;
    let payload = null;
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch (err) {
      return null;
    }
    if (!payload || typeof payload !== 'object') return null;
    if (!payload.type && eventName) payload.type = eventName;
    if (payload.type === 'done') return { terminal: 'done', data: payload.data };
    if (payload.type === 'paused') return { terminal: 'paused', data: payload.data };
    if (payload.type === 'error') {
      const msg = (payload.data && payload.data.error) || payload.error || '回答失败';
      throw new Error(msg);
    }
    if (onEvent) onEvent(payload);
    return null;
  }

  async function postAnswerStream(url, body, onEvent, fetchOpts) {
    const res = await fetch(url, Object.assign({
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'text/event-stream, application/json',
      },
      body: JSON.stringify(Object.assign({}, body, { stream: true })),
    }, fetchOpts || {}));
    const ctype = String((res.headers && res.headers.get('content-type')) || '').toLowerCase();
    if (!ctype.includes('text/event-stream') || !res.body || !res.body.getReader) {
      let data = null;
      try { data = await res.json(); } catch (err) { data = null; }
      if (data && data.paused) return data;
      if (!res.ok || !data || data.ok === false) {
        throw new Error((data && data.error) || ('请求失败 ' + res.status));
      }
      return data;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let doneData = null;
    let pausedData = null;
    while (true) {
      const step = await reader.read();
      if (step.value) buffer += decoder.decode(step.value, { stream: !step.done });
      let sep = buffer.indexOf('\n\n');
      while (sep >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const resFrame = parseSSEFrame(frame, onEvent);
        if (resFrame && resFrame.terminal === 'done') doneData = resFrame.data;
        if (resFrame && resFrame.terminal === 'paused') pausedData = resFrame.data;
        sep = buffer.indexOf('\n\n');
      }
      if (step.done) break;
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      const resFrame = parseSSEFrame(buffer, onEvent);
      if (resFrame && resFrame.terminal === 'done') doneData = resFrame.data;
      if (resFrame && resFrame.terminal === 'paused') pausedData = resFrame.data;
    }
    if (pausedData) return pausedData;
    if (!doneData) throw new Error('Failed to fetch');
    if (doneData.ok === false) throw new Error(doneData.error || '回答失败');
    return doneData;
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

  function readingOpenHref(opts) {
    const o = opts || {};
    const p = new URLSearchParams();
    const uid = o.uid || o.book || (o.path ? bookKey(o.path) : '');
    if (uid) p.set('uid', uid);
    if (o.path) p.set('path', o.path);
    if (o.line) p.set('line', String(o.line));
    if (o.quote) p.set('q', String(o.quote).slice(0, 120));
    if (o.question) p.set('query', String(o.question).slice(0, 200));
    p.set('from', 'search');
    return '/go/reading?to=' + encodeURIComponent('/open?' + p.toString());
  }

  function readingBookHref(uid) {
    return '/go/reading?to=' + encodeURIComponent('/book/' + encodeURIComponent(uid));
  }

  async function fetchBookTitles(uids) {
    if (!uids || !uids.length) return new Map();
    try {
      const data = await fetchJSON('/api/books/meta?uids=' + encodeURIComponent(uids.join(',')));
      if (Array.isArray(data.books) && data.books.length) {
        return new Map(data.books.map((b) => [b.uid, b.title || b.uid]));
      }
    } catch (err) {
      // 查不到书名时保留 uid 展示即可
    }
    return new Map();
  }

  async function loadScopeBookMeta(uids) {
    const byUid = await fetchBookTitles(uids);
    if (!byUid.size) return;
    state.scopeBooks = state.scopeBooks.map((item) => ({
      uid: item.uid,
      title: byUid.get(item.uid) || item.title,
    }));
    renderHint();
  }

  async function loadExcludeBookMeta(uids) {
    const byUid = await fetchBookTitles(uids);
    if (!byUid.size) return;
    state.excludeBooks = state.excludeBooks.map((item) => ({
      uid: item.uid,
      title: byUid.get(item.uid) || item.title,
    }));
    state.turns.forEach((t) => {
      if (t.relatedMode && Array.isArray(t.excludeBookMeta)) {
        t.excludeBookMeta = t.excludeBookMeta.map((item) => ({
          uid: item.uid,
          title: byUid.get(item.uid) || item.title,
        }));
      }
    });
    render();
  }

  /* ---- 回答范围 & 相关性降序先排再筛 ----------------------------- */

  const MAX_SOURCES = 10;
  const MAX_SUPPLEMENTS = 3;
  const DEFAULT_RELATED_THRESHOLD = 0.45;

  function clampThreshold(v, fallback = DEFAULT_RELATED_THRESHOLD) {
    const n = Number(v);
    if (!Number.isFinite(n)) return fallback;
    return Math.round(Math.max(0, Math.min(1, n)) * 100) / 100;
  }

  function sortRelatedDescending(candidates) {
    return (candidates || []).slice().sort((a, b) => {
      if (b.relevance !== a.relevance) return b.relevance - a.relevance;
      if (b.score !== a.score) return b.score - a.score;
      return String(a.book || '').localeCompare(String(b.book || ''));
    });
  }

  function filterRelatedByThreshold(sortedList, threshold) {
    const thr = clampThreshold(threshold);
    const qualified = [];
    const below = [];
    for (const item of sortedList) {
      if (item.relevance >= thr) qualified.push(item);
      else below.push(item);
    }
    return { qualified, below, threshold: thr };
  }

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

  function focusOf(turn) {
    const focus = turn && turn.trace && turn.trace.focus;
    if (!focus || typeof focus !== 'object' || !focus.status) return null;
    if (focus.status === 'missing'
        && !(focus.title || focus.query_title || focus.book)) return null;
    return focus;
  }

  function focusExact(turn) {
    const focus = focusOf(turn);
    return focus && focus.status === 'exact' ? focus : null;
  }

  function focusName(focus) {
    return (focus && (focus.title || focus.query_title || focus.book)) || '';
  }

  function sourceFromFocus(turn, list) {
    const focus = focusOf(turn);
    if (!focus || focus.status !== 'exact') return null;
    const book = focus.book || '';
    const name = focusName(focus);
    const match = (source) => source && typeof source.path === 'string' && source.path
      && (!book || (source.book || bookKey(source.path)) === book);
    if (focus.best_path) {
      return {
        book,
        title: name,
        chapter: chapterOf({ path: focus.best_path }),
        path: focus.best_path,
        line: Number(focus.best_line) || 1,
      };
    }
    const top = sourceFromResult((turn.trace || {}).result);
    if (top && match(top)) return cloneSource(top);
    const found = (list || []).find(match);
    if (found) return cloneSource(found);
    return null;
  }

  function focusSupplements(turn, focus) {
    const trace = turn.trace || {};
    const focusBook = focus.book || bookKey(focus.best_path || '');
    const rows = [];
    const seen = new Set(focusBook ? [focusBook] : []);
    const add = (row) => {
      const source = sourceFromAnswer(row);
      if (!source) return;
      const book = source.book || bookKey(source.path);
      if (focusBook && book === focusBook) return;
      const key = book || sourceKey(source);
      if (!key || seen.has(key)) return;
      seen.add(key);
      rows.push(source);
    };
    ((trace.cards && Array.isArray(trace.cards.next)) ? trace.cards.next : []).forEach((row) => {
      const hit = (Array.isArray(row.selected) ? row.selected : [])[0];
      add({
        book: row.book, title: row.title,
        chapter: (hit && hit.chapter_title) || '',
        path: (hit && hit.chapter_path) || row.best_chapter || '',
        line: (hit && hit.line) || row.line || 1,
      });
    });
    (Array.isArray((trace.plan || {}).ranking) ? trace.plan.ranking : []).forEach((row) => {
      add({
        book: row.book, title: row.title,
        chapter: chapterOf({ path: row.best_path || '' }),
        path: row.best_path || '', line: row.best_line || 1,
      });
    });
    return rows.slice(0, MAX_SUPPLEMENTS);
  }

  function focusFirstSources(turn, list) {
    const out = [];
    const seen = new Set();
    (list || []).forEach((source) => {
      if (!source || typeof source.path !== 'string' || !source.path) return;
      const item = cloneSource(source);
      const key = sourceKey(item);
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push(item);
    });
    const focus = focusExact(turn);
    if (focus) {
      const primary = sourceFromFocus(turn, out);
      if (primary) {
        const focusBook = focus.book || bookKey(primary.path);
        const rest = out.filter((s) => (s.book || bookKey(s.path)) !== focusBook);
        return [primary].concat(rest).slice(0, MAX_SOURCES);
      }
    }
    return out.slice(0, MAX_SOURCES);
  }

  function relatedCandidates(turn) {
    const trace = (turn && turn.trace) || {};
    const plan = trace.plan || {};
    const ranking = Array.isArray(plan.ranking) ? plan.ranking : [];
    const chList = Array.isArray(plan.chapters) ? plan.chapters : [];
    const chByBook = new Map();
    chList.forEach((entry) => {
      if (entry && (entry.book || entry.uid)) chByBook.set(entry.book || entry.uid, entry);
    });
    const out = [];
    const seen = new Set();
    ranking.forEach((row) => {
      const book = (row && (row.uid || row.book)) || '';
      if (!book || seen.has(book)) return;
      const chEntry = chByBook.get(book);
      const sel = chEntry && Array.isArray(chEntry.selected) ? chEntry.selected[0] : null;
      const path = (sel && sel.chapter_path) || row.best_path || '';
      const line = Number((sel && sel.line) || row.best_line) || 1;
      if (!path) return;
      seen.add(book);
      const rel = Number.isFinite(Number(row.relevance)) ? Number(row.relevance) : 0;
      out.push({
        book,
        title: row.title || book,
        type: row.type || '',
        chapters: Number(row.chapters) || 0,
        relevance: Math.max(0, Math.min(1, rel)),
        score: Number(row.score) || 0,
        path,
        line,
        chapter: (sel && sel.chapter_title) || chapterOf({ path }),
        snippet: (sel && sel.snippet) || row.best_text || '',
      });
    });
    return sortRelatedDescending(out);
  }

  function relatedQualified(turn) {
    return filterRelatedByThreshold(relatedCandidates(turn), turn && turn.relatedThreshold).qualified;
  }

  function relatedDefaultSources(turn) {
    return relatedQualified(turn).slice(0, MAX_SOURCES).map((c) => ({
      book: c.book,
      title: c.title,
      chapter: c.chapter,
      path: c.path,
      line: c.line,
    }));
  }

  function defaultSources(turn) {
    if (turn && turn.relatedMode) {
      return relatedDefaultSources(turn);
    }
    const focus = focusExact(turn);
    if (focus) {
      const primary = sourceFromFocus(turn, turn.sources);
      if (!primary) return [];
      return [primary].concat(focusSupplements(turn, focus)).slice(0, MAX_SOURCES);
    }
    const top = sourceFromResult((turn.trace || {}).result);
    if (top) return [top];
    const anchor = sourceFromAnchor(turn.anchor);
    if (anchor && (!turn.anchor.fromN || Number(turn.anchor.fromN) === turn.n - 1)) return [anchor];
    const cards = cardSources(turn);
    return cards.length ? [cards[0]] : [];
  }

  function focusOnlySources(turn) {
    const primary = focusExact(turn) ? sourceFromFocus(turn, turn.sources) : null;
    return primary ? [primary] : defaultSources(turn);
  }

  function roleOf(source, anchor, top, focus) {
    const key = sourceKey(source);
    if (focus && sourceKey(focus) === key) return 'focus';
    if (anchor && sourceKey(anchor) === key) return 'anchor';
    if (top && sourceKey(top) === key) return 'top';
    return 'card';
  }

  function bookChoices(turn) {
    const anchor = sourceFromAnchor(turn.anchor);
    const top = sourceFromResult((turn.trace || {}).result);
    const focus = sourceFromFocus(turn, turn.sources);
    const seen = new Set();
    const rows = [];
    const push = (source, role) => {
      const key = sourceKey(source);
      if (!source || !key || seen.has(key)) return;
      seen.add(key);
      rows.push({ source, role, locked: role === 'focus' || (role === 'anchor' && !top) });
    };
    push(focus, 'focus');
    (turn.sources || []).forEach((source) => push(source, roleOf(source, anchor, top, focus)));
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
    if (answer.mode === 'anchored') {
      const used = (Array.isArray(turn.answerSources) && turn.answerSources.length)
        ? turn.answerSources : turn.sources;
      return focusFirstSources(turn, used || []);
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

  function startClock(initialElapsed = 0) {
    stopClock();
    state.elapsed = Number(initialElapsed) > 0 ? Math.floor(Number(initialElapsed)) : 0;
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

  /* ---- 回答轮询 -------------------------------------------------- */

  let answerPoll = null;
  let answerPollSeq = 0;

  function stopAnswerPoll() {
    answerPollSeq += 1;
    if (answerPoll && answerPoll.timer) clearTimeout(answerPoll.timer);
    answerPoll = null;
  }

  function savedStatusOf(saved) {
    const answer = saved.answer && typeof saved.answer === 'object' ? saved.answer : null;
    const name = String(saved.answer_state || '');
    if (name === 'running') return { status: 'answering', error: '' };
    if (name === 'paused') {
      return { status: 'paused', error: String(saved.answer_error || '') };
    }
    if (name === 'interrupted') {
      return { status: 'answer-interrupted', error: String(saved.answer_error || '') };
    }
    if (name === 'error') {
      return { status: 'answer-error', error: String(saved.answer_error || '') };
    }
    if (answer) {
      return answer.ok === false
        ? { status: 'answer-error', error: String(answer.error || '') }
        : { status: 'ready', error: '' };
    }
    if (name === 'pending' || name === 'incomplete') return { status: 'answer-incomplete', error: '' };
    if (name === 'ready') return { status: 'answer-incomplete', error: '' };
    const trace = saved.trace && typeof saved.trace === 'object' ? saved.trace : null;
    return { status: trace ? 'answer-incomplete' : 'run-error', error: '' };
  }

  function answerStateName(status) {
    if (status === 'answering') return 'running';
    if (status === 'paused') return 'paused';
    if (status === 'answer-interrupted') return 'interrupted';
    if (status === 'answer-error') return 'error';
    if (status === 'answer-incomplete') return 'incomplete';
    if (status === 'ready') return 'ready';
    return '';
  }

  function mergeSavedTurn(saved) {
    const row = turnFromSaved(saved);
    const at = state.turns.findIndex((t) => t.n === row.n);
    if (at < 0) return null;
    state.turns[at] = row;
    syncBusy();
    if (state.busy) {
      if (!state.timer) startClock();
    } else {
      stopClock();
    }
    render();
    loadSessions();
    return row;
  }

  function startAnswerPoll(sessionId, n, epoch) {
    stopAnswerPoll();
    if (!sessionId || !n) return;
    const seq = answerPollSeq;
    const token = epoch == null ? state.epoch : epoch;
    const started = Date.now();
    const alive = () => seq === answerPollSeq && token === state.epoch && state.sessionId === sessionId;
    const schedule = () => {
      if (!alive()) return;
      answerPoll = {
        timer: setTimeout(tick, ANSWER_POLL_MS),
        sessionId, n, epoch: token, started,
      };
    };
    const tick = async () => {
      answerPoll = null;
      if (!alive()) return;
      if (Date.now() - started > ANSWER_POLL_MAX) return;
      let data = null;
      try {
        data = await fetchJSON('/api/session?id=' + encodeURIComponent(sessionId));
      } catch (err) {
        schedule();
        return;
      }
      if (!alive()) return;
      const sess = data.session || {};
      const saved = (Array.isArray(sess.turns) ? sess.turns : [])
        .find((t) => t && t.n === n);
      const turn = state.turns.find((t) => t.n === n);
      if (!saved || !turn) return;
      const name = String(saved.answer_state || '');
      const next = savedStatusOf(saved);
      if (next.status === 'answering' || (next.status === 'answer-incomplete' && name === 'pending')) {
        schedule();
        return;
      }
      mergeSavedTurn(saved);
    };
    schedule();
  }

  /* ---- 页头统计 -------------------------------------------------- */

  async function loadStats() {
    try {
      const data = await fetchJSON('/api/health');
      state.stats = statsFromHealth(data);
    } catch (err) {
      state.stats = { values: { books: null, paras: null, chapters: null }, note: '', embedReady: false };
    }
    renderStats();
  }

  function statsFromHealth(data) {
    const meta = data.meta || {};
    const indexReady = meta.index === 'ok';
    const libraryReady = meta.library === 'ok';
    const count = (ready, value) => (ready && typeof value === 'number' ? value : null);
    let note = '';
    if (!indexReady) {
      note = meta.index === 'empty' ? '索引为空，先跑索引' : '索引未建，先跑索引';
    } else if (!libraryReady) {
      note = '书目库未建，先跑 book-corpus library';
    } else if (data.degraded) {
      note = String(data.message || '索引不可用，先跑索引');
    }
    return {
      values: {
        books: count(libraryReady, meta.books),
        books_total: count(libraryReady, meta.books_total),
        paras: count(indexReady, meta.paras),
        chapters: count(indexReady, meta.chapters),
      },
      builtAt: meta.built_at || '',
      note,
      embedReady: Boolean(indexReady && meta.embed && meta.embed.ready),
    };
  }

  function renderStats() {
    const stats = state.stats || { values: {}, note: '', embedReady: false };
    document.querySelectorAll('[data-stat]').forEach((el) => {
      const value = stats.values[el.dataset.stat];
      el.textContent = typeof value === 'number' ? fmtInt(value) : '—';
    });
    const builtEl = document.querySelector('[data-stat-built-at]');
    if (builtEl) {
      builtEl.textContent = stats.builtAt ? '统计于 ' + stats.builtAt : '';
      builtEl.title = stats.builtAt ? '与本页数字同一份快照；书库更新后下一分钟刷新' : '';
      builtEl.hidden = !stats.builtAt;
    }
    const fusionEl = $('#fusion-pill');
    if (fusionEl) fusionEl.hidden = !stats.embedReady;
    const box = $('#stats');
    let noteEl = box ? box.querySelector('[data-stat-note]') : null;
    if (!noteEl && box) {
      noteEl = document.createElement('span');
      noteEl.dataset.statNote = '';
      noteEl.className = 'stat-note';
      noteEl.setAttribute('role', 'status');
      box.appendChild(noteEl);
    }
    if (noteEl) {
      noteEl.textContent = stats.note || '';
      noteEl.hidden = !stats.note || window.matchMedia('(max-width: 639px)').matches;
    }
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
      + '<button class="round-btn" type="button">'
      + '<span class="no">' + pad(turn.n) + (current ? ' · 当前' : '') + '</span>'
      + railRel(turn)
      + '<span class="q">' + esc(turn.question) + '</span>'
      + '<span class="meta">'
      + (book ? '<span class="chip">' + esc(book) + '</span>' : '')
      + (ids ? '<span class="ids">' + ids + '</span>' : '')
      + (status ? '<span class="round-status">' + status + caret + '</span>' : '')
      + '</span></button></li>';
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
      + '<p class="start-lead pretty">先问第一问，它会把命中的原文与依据递到你面前。'
      + '之后点一条依据当落点，接着问。</p>'
      + card + '</section>' + error;
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
    const focus = focusOf(turn);
    const lockName = focusName(focus);
    const focusValue = focus
      ? (focus.status === 'exact' ? '已锁定'
        : focus.status === 'ambiguous' ? '有歧义' : '没找到')
      : '';
    const focusLead = focus
      ? (focus.status === 'exact' ? '已锁定《' + lockName + '》· 优先从这本书回答'
        : focus.status === 'ambiguous' ? '书名有歧义《' + lockName + '》· 按书卡候选继续'
        : '书库没有《' + lockName + '》· 按普通检索继续')
      : '';

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
        meta: (plan.vec_used
          ? '混合检索 BM25 + 向量（vec ' + Math.round(Number(plan.vec_ms || 0)) + ' ms） · '
          : '仅 BM25 · ') + '书级打分 ' + (plan.scheme || 'best_support'),
        field: {
          label: '排序',
          chips: ranking.slice(0, 3).map((row, i) => (i + 1) + '. '
            + (row.title || bookName(row.book)) + ' '
            + (isFinite(Number(row.score)) ? Number(row.score).toFixed(2) : '—')),
        },
        raw: {
          scheme: plan.scheme,
          fusion: plan.fusion || (plan.vec_used ? 'hybrid' : 'bm25_only'),
          vec_used: !!plan.vec_used,
          vec_ms: plan.vec_ms || 0,
          ranking: plan.ranking,
          chapters: plan.chapters,
        },
      },
      {
        value: focus ? focusValue : (cards.trigger ? cardList.length + ' 张' : '未触发'),
        lead: focus
          ? focusLead
          : (cards.trigger ? cardList.length + ' 张书卡 · 触发' : '未触发 · 直接取 top1'),
        meta: focus
          ? (focus.status === 'exact' ? '其他书只补它没有的角度' : '')
          : (cards.trigger ? '不确定时，把候选递给你' : '书级第一名够稳，不弹卡'),
        field: {
          label: '候选',
          chips: focus && focus.status === 'exact'
            ? ['主书《' + lockName + '》']
            : (focus && Array.isArray(focus.candidates) && focus.candidates.length
              ? focus.candidates.map((row) => '《' + (row.title || bookName(row.book)) + '》')
              : cardList.map((card) => '《' + (card.title || bookName(card.book)) + '》')),
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
      + '<svg class="scaret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
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
      + '" id="stages" aria-label="五阶段检索进度">'
      + '<div class="strip"><button class="strip-bar" type="button" aria-expanded="'
      + open + '">'
      + '<span class="steps">' + steps + '</span>'
      + '<span class="strip-toggle"><span class="t-open">展开链路</span>'
      + '<span class="t-close">收起链路</span>'
      + '<svg class="strip-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
      + '</span></button></div>'
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

  function buildRelatedDynamic(turn) {
    const last = lastTurn();
    const readonly = !last || last.n !== turn.n;
    const candidates = relatedCandidates(turn);
    const { qualified, below, threshold } = filterRelatedByThreshold(candidates, turn.relatedThreshold);
    const thrPct = Math.round(threshold * 100);
    const chosen = turn.sources || [];
    const picked = new Set(chosen.map(sourceKey));
    const maxPct = candidates.length
      ? Math.floor(candidates[0].relevance * 100 / 5) * 5
      : 0;

    let countMsg = '';
    if (!candidates.length) {
      countMsg = '全库其他书中没有命中相关内容';
    } else if (!qualified.length) {
      countMsg = '当前阈值 ≥ ' + thrPct + '% 下筛出 0 本达标（候选共 ' + candidates.length + ' 本，最高相关性 '
        + Math.round(candidates[0].relevance * 100) + '%）';
    } else if (qualified.length > MAX_SOURCES) {
      countMsg = '已按相关性从高到低降序排列，当前阈值 ≥ ' + thrPct + '% 筛出 ' + qualified.length
        + ' 本 · 已选前 ' + chosen.length + ' 本参与回答（上限 ' + MAX_SOURCES + ' 本）';
    } else if (qualified.length === 1) {
      countMsg = '已按相关性降序排列，当前阈值 ≥ ' + thrPct + '% 筛出 1 本 · 将从这 1 本书回答';
    } else {
      countMsg = '已按相关性从高到低降序排列，当前阈值 ≥ ' + thrPct + '% 筛出 ' + qualified.length
        + ' 本 · 已选 ' + chosen.length + ' 本参与回答';
    }

    const renderCard = (c, rankNum, isBelow) => {
      const on = !isBelow && picked.has(c.book);
      const pct = Math.round(c.relevance * 100);
      const readHref = readingOpenHref({
        uid: c.book,
        path: c.path,
        line: c.line,
        quote: c.snippet || '',
        question: turn.question,
      });
      return '<article class="related-card' + (on ? ' is-on' : '') + (isBelow ? ' is-below' : '') + '">'
        + (isBelow ? ''
          : '<button class="related-card-pick' + (on ? ' is-on' : '') + '" type="button"'
            + ' data-related-pick="' + esc(c.book) + '" data-from="' + turn.n + '"'
            + ' aria-pressed="' + on + '"'
            + (readonly ? ' disabled' : '') + '>'
            + (on ? ICON_CHECK : ICON_PLUS)
            + '<span>' + (on ? '已选' : '纳入') + '</span>'
            + '</button>')
        + '<div class="related-main">'
        + '<div class="related-book-line">'
        + '<span class="rank-badge" aria-label="相关性第 ' + rankNum + ' 名">#' + rankNum + '</span>'
        + '<span class="related-card-title">《' + esc(c.title) + '》</span>'
        + '<span class="related-card-sub">' + esc(c.chapter || '')
        + (c.chapter ? ' · ' : '') + '<span class="mono" translate="no">L' + esc(c.line) + '</span></span>'
        + '</div>'
        + (c.snippet ? '<p class="related-card-snip pretty">' + esc(cut(c.snippet, 120)) + '</p>' : '')
        + '</div>'
        + '<div class="related-right">'
        + '<div class="rel-score-wrap" aria-label="相关性 ' + pct + '%">'
        + '<div class="rel-bar-track" aria-hidden="true"><div class="rel-bar-fill" style="width:' + pct + '%"></div></div>'
        + '<span class="related-score mono">' + pct + '%</span>'
        + '</div>'
        + '<a class="ev-read" href="' + readHref + '" target="_blank" rel="noopener"><span>精读</span>' + ICON_OUT + '</a>'
        + '</div>'
        + '</article>';
    };

    const recovery = (!readonly && !qualified.length && candidates.length)
      ? '<div class="related-empty-recovery">'
        + '<span class="pretty" style="font-size:13px">当前阈值过高（≥ ' + thrPct + '%），所有候选书均在折叠区。</span>'
        + '<button class="btn sm primary related-lower-btn" type="button" data-related-threshold-set="'
        + (maxPct / 100) + '" data-from="' + turn.n + '">调低阈值到 ' + maxPct + '%</button>'
        + '</div>'
      : '';

    const act = !readonly && turn.answer && staleSources(turn) && chosen.length
      ? '<div class="books-act"><span class="books-note">当前回答按 '
        + scopeSources(turn).length + ' 本生成</span>'
        + '<button class="btn btn-primary" type="button" data-reanswer="' + turn.n + '"'
        + (state.busy ? ' disabled' : '') + '>用这 ' + chosen.length + ' 本重新回答</button></div>'
      : '';

    const html = '<p class="related-summary" aria-live="polite">' + esc(countMsg) + '</p>'
      + recovery
      + (qualified.length
        ? '<div class="related-list">' + qualified.map((c, i) => renderCard(c, i + 1, false)).join('') + '</div>'
        : '')
      + (below.length
        ? '<details class="related-below"' + (!qualified.length ? ' open' : '') + '>'
          + '<summary>低于当前阈值（' + below.length + ' 本不参与回答 · 仍保持降序排列）</summary>'
          + '<div class="related-list">' + below.map((c, i) => renderCard(c, qualified.length + i + 1, true)).join('') + '</div>'
          + '</details>'
        : '')
      + act;

    return { candidates, qualified, below, thrPct, readonly, html };
  }

  function updateRelatedInPlace(n) {
    const turn = state.turns.find((t) => t.n === n);
    if (!turn || !turn.relatedMode) return;
    const res = buildRelatedDynamic(turn);
    const panel = mainEl.querySelector('[data-related-panel="' + n + '"]');
    if (!panel) {
      render();
      return;
    }
    const lbl = panel.querySelector('[data-threshold-label="' + n + '"]');
    if (lbl) lbl.textContent = '≥ ' + res.thrPct + '%';
    const range = panel.querySelector('[data-related-threshold="' + n + '"]');
    if (range) {
      range.value = String(res.thrPct);
      range.style.setProperty('--progress', Math.round((res.thrPct / 95) * 100) + '%');
    }
    panel.querySelectorAll('.preset-chip[data-related-threshold-set]').forEach((btn) => {
      const p = Math.round(Number(btn.dataset.relatedThresholdSet) * 100);
      btn.setAttribute('aria-pressed', String(p === res.thrPct));
    });
    const dyn = panel.querySelector('[data-related-dynamic="' + n + '"]');
    if (dyn) dyn.innerHTML = res.html;
    const bodyWrap = mainEl.querySelector('[data-related-body="' + n + '"]');
    if (bodyWrap) bodyWrap.innerHTML = relatedPendingHTML(turn);
  }

  function relatedPanelHTML(turn) {
    if (!turn || !turn.trace) return '';
    const res = buildRelatedDynamic(turn);
    const exclList = (Array.isArray(turn.excludeBookMeta) && turn.excludeBookMeta.length
      ? turn.excludeBookMeta
      : (turn.excludeBooks || []).map((uid) => ({ uid, title: uid })));
    const exclText = exclList.length
      ? '已排除' + exclList.map((b) => '《' + esc(b.title || b.uid) + '》').join('、') + '及原段'
      : '已排除原段落';

    return '<section class="books related-panel" data-related-panel="' + turn.n + '" aria-label="相关性降序排列与阈值筛选">'
      + '<div class="related-head">'
      + '<div class="related-title-wrap">'
      + '<div class="related-title-row">'
      + '<span class="t">跨书找相关</span>'
      + '<span class="sort-badge">'
      + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 16 4 4 4-4"/><path d="M7 20V4"/><path d="M11 4h10"/><path d="M11 8h7"/><path d="M11 12h4"/></svg>'
      + '<span>先按相关性降序排列 · 再按阈值筛选</span>'
      + '</span>'
      + '</div>'
      + '<span class="h">' + esc(exclText) + '</span>'
      + '</div>'
      + '</div>'
      + '<div class="threshold-box">'
      + '<div class="related-threshold-row">'
      + '<label class="related-thr-lbl" for="rel-thr-' + turn.n + '">相关性阈值</label>'
      + '<input class="related-thr-range" id="rel-thr-' + turn.n + '" name="related_threshold" type="range"'
      + ' min="0" max="95" step="5" value="' + res.thrPct + '"'
      + ' style="--progress:' + Math.round((res.thrPct / 95) * 100) + '%"'
      + ' data-related-threshold="' + turn.n + '"' + (res.readonly ? ' disabled' : '') + '>'
      + '<span class="related-thr-val mono" data-threshold-label="' + turn.n + '">≥ ' + res.thrPct + '%</span>'
      + '</div>'
      + '<div class="preset-row">'
      + '<div class="preset-chips" role="group" aria-label="常用阈值预设">'
      + PRESET_THRESHOLDS.map((v) => {
        const p = Math.round(v * 100);
        return '<button type="button" class="preset-chip" data-related-threshold-set="' + v
          + '" data-from="' + turn.n + '" aria-pressed="' + (p === res.thrPct) + '"'
          + (res.readonly ? ' disabled' : '') + '>≥&nbsp;' + p + '%</button>';
      }).join('')
      + '</div>'
      + '</div>'
      + '</div>'
      + '<div data-related-dynamic="' + turn.n + '">' + res.html + '</div>'
      + '</section>';
  }

  function booksHTML(turn) {
    if (turn && turn.relatedMode) return relatedPanelHTML(turn);
    const choices = bookChoices(turn);
    const chosen = turn.sources || [];
    const focus = focusExact(turn);
    if (choices.length < 2 || !chosen.length) return '';
    const last = lastTurn();
    const readonly = !last || last.n !== turn.n;
    const picked = new Set(chosen.map(sourceKey));
    const selectable = choices.slice(0, MAX_SOURCES);
    const allOn = selectable.every((row) => picked.has(sourceKey(row.source)));
    const head = '<div class="books-head">'
      + '<span class="t">' + (focus ? '回答用书' : '候选书') + ' · ' + choices.length + ' 本</span>'
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
      const tag = row.role === 'focus' ? '指定'
        : (row.role === 'anchor' ? '落点' : (row.role === 'top' ? '首选' : ''));
      const tagText = full ? '最多 ' + MAX_SOURCES + ' 本' : tag;
      const cls = 'book-pick' + (on ? ' is-on' : '') + (row.locked ? ' is-locked' : '')
        + (full ? ' is-full' : '');
      const fullTitle = '《' + (row.source.title || row.source.book) + '》· '
        + (row.source.chapter || '');
      const uid = row.source.uid || row.source.book || bookKey(row.source.path);
      return '<span class="book-pick-wrap">'
        + '<button class="' + cls + '" type="button" data-book-pick="' + esc(key) + '"'
        + ' data-from="' + turn.n + '" title="' + esc(fullTitle) + '"'
        + (readonly || full ? ' disabled' : '') + '>'
        + (on ? ICON_CHECK : ICON_PLUS)
        + '<span class="bp-title">《' + esc(cut(row.source.title || row.source.book, 18)) + '》</span>'
        + (tagText ? '<span class="bp-tag">' + esc(tagText) + '</span>' : '')
        + '</button>'
        + (uid
          ? '<a class="bp-open" href="' + readingBookHref(uid) + '" target="_blank" rel="noopener"'
            + ' data-stop="1" title="在伴读查看书目与章节" aria-label="在伴读查看《' + esc(row.source.title || row.source.book) + '》">' + ICON_OUT + '</a>'
          : '')
        + '</span>';
    }).join('');
    const notes = [];
    if (focus) {
      notes.push('先答《' + focusName(focus) + '》，其他书只补它没有的角度。');
    }
    if (choices.length > MAX_SOURCES) {
      notes.push('候选超过 ' + MAX_SOURCES + ' 本，最多选 ' + MAX_SOURCES + ' 本。');
    }
    if (chosen.length > 1) notes.push('选中 2 本以上时，回答跨书，上下文预算 3,000 字。');
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

  function answerSectionHTML(section) {
    if (!section || (!section.heading && !section.body)) return '';
    const head = plain(section.heading);
    const body = String(section.body || '').split(/\n+/).map(plain).filter(Boolean);
    return '<section>' + (head ? '<h3>' + esc(head) + '</h3>' : '')
      + body.map((p) => '<p>' + esc(p) + '</p>').join('') + '</section>';
  }

  function answerPartHTML(part, index, answer) {
    const kind = part.kind || (index === 0 ? 'primary' : 'supplement');
    let title = plain(part.title);
    if (!title) {
      if (kind === 'supplement') title = '其他书的补充';
      else if (answer.focus) title = '《' + (answer.focus.title || answer.focus.book || '') + '》';
    }
    const blocks = [];
    const lead = plain(part.lead);
    if (lead) blocks.push('<p class="answer-lead">' + esc(lead) + '</p>');
    (Array.isArray(part.sections) ? part.sections : []).forEach((section) => {
      const html = answerSectionHTML(section);
      if (html) blocks.push(html);
    });
    if (!blocks.length && !part.error && !part.not_found) {
      blocks.push('<p class="answer-loading">没有拿到回答正文</p>');
    }
    if (part.not_found) blocks.push('<p class="answer-small">未找到原文依据</p>');
    if (part.error) blocks.push('<p class="answer-error">' + esc(part.error) + '</p>');
    if (part.dissent) {
      blocks.push('<p class="answer-small">分歧 · ' + esc(plain(part.dissent)) + '</p>');
    }
    if (part.gap) {
      blocks.push('<p class="answer-small">没讲到的 · ' + esc(plain(part.gap)) + '</p>');
    }
    return '<section class="answer-part' + (kind === 'supplement' ? ' is-supplement' : '') + '">'
      + (title ? '<h3>' + esc(title) + '</h3>' : '') + blocks.join('') + '</section>';
  }

  function answerBody(answer) {
    const partList = Array.isArray(answer.parts)
      ? answer.parts.filter((part) => part && typeof part === 'object') : [];
    if (partList.length) {
      return partList.map((part, i) => answerPartHTML(part, i, answer)).join('');
    }
    const parts = [];
    const sections = Array.isArray(answer.sections) ? answer.sections : [];
    if (sections.length) {
      const lead = plain(answer.lead);
      if (lead) parts.push('<p class="answer-lead">' + esc(lead) + '</p>');
      sections.forEach((section) => {
        const html = answerSectionHTML(section);
        if (html) parts.push(html);
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
    if (data.mode === 'anchored') {
      const partCount = Array.isArray(data.parts) ? data.parts.length : 0;
      const title = data.focus ? (data.focus.title || data.focus.book || '') : '';
      const books = new Set(citesOf(turn).map((c) => bookOf(c)).filter(Boolean));
      meta.push((partCount || 2) + ' 部分' + (title ? ' · 主书《' + title + '》' : ''));
      if (books.size) meta.push('引 ' + books.size + ' 本书');
    }
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

  function reanswerHTML(turn) {
    if (!turn.answer || turn.answer.ok === false) return '';
    return '<div class="turn-actions">'
      + '<button class="btn sm turn-reanswer" type="button" data-reanswer-turn="' + turn.n + '"'
      + ' title="清掉当前回答，重新检索并回答"'
      + (state.busy ? ' disabled' : '') + '>重新回答</button></div>';
  }

  function parseStreamMarkdown(text) {
    const raw = String(text || '').replace(/\r\n?/g, '\n');
    if (!raw.trim()) return null;
    const lines = raw.split('\n');
    if (lines.length && /^\s*#{1,4}\s*$/.test(lines[lines.length - 1])) lines.pop();
    const leadLines = [];
    const sections = [];
    let current = null;
    for (let i = 0; i < lines.length; i += 1) {
      const m = lines[i].match(/^\s*#{1,4}\s+(.+?)\s*$/);
      if (m) {
        const heading = plain(m[1]);
        if (heading) {
          current = { heading, lines: [] };
          sections.push(current);
          continue;
        }
      }
      if (current) current.lines.push(lines[i]);
      else leadLines.push(lines[i]);
    }
    return { leadLines, sections };
  }

  function streamPreviewHTML(text) {
    const parsed = parseStreamMarkdown(text);
    if (!parsed) return '';
    const leadParas = parsed.leadLines.join('\n').split(/\n+/).map(plain).filter(Boolean);
    if (!parsed.sections.length) {
      return leadParas.map((p) => '<p>' + esc(p) + '</p>').join('');
    }
    const parts = [];
    leadParas.forEach((p, idx) => {
      parts.push('<p' + (idx === 0 ? ' class="answer-lead"' : '') + '>' + esc(p) + '</p>');
    });
    parsed.sections.forEach((sec) => {
      const bodyParas = sec.lines.join('\n').split(/\n+/).map(plain).filter(Boolean);
      parts.push('<section><h3>' + esc(sec.heading) + '</h3>'
        + bodyParas.map((p) => '<p>' + esc(p) + '</p>').join('') + '</section>');
    });
    return parts.join('');
  }

  function updateStreamDOM(turn) {
    const statusEl = mainEl.querySelector('[data-stream-status]');
    if (statusEl && turn.streamStatus) statusEl.textContent = turn.streamStatus;
    const preview = streamPreviewHTML(turn.streamText);
    if (!preview) return;
    const bodyEl = mainEl.querySelector('[data-stream-body]');
    if (!bodyEl) {
      render();
      return;
    }
    bodyEl.innerHTML = preview;
  }

  function relatedPendingHTML(turn) {
    const last = lastTurn();
    const canRetry = last && last.n === turn.n;
    const count = (turn.sources || []).length;
    if (!count) {
      return '<p class="answer-pending-note">当前阈值下没有找到符合条件的其他书籍，无法生成回答。</p>'
        + '<p class="answer-meta">可调低上方相关性阈值查看较弱关联书目，或直接在下方输入新问题。</p>';
    }
    const label = count === 1 ? '由模型从这 1 本回答' : '由模型跨这 ' + count + ' 本回答';
    return '<p class="answer-pending-note">已按阈值筛选出 ' + count + ' 本跨书相关书目（上限 '
      + MAX_SOURCES + ' 本），可直接点选精读或由模型归纳回答。</p>'
      + '<div class="trace-pending-actions">'
      + (canRetry
        ? '<button class="btn btn-primary" type="button" data-retry-answer="' + turn.n + '">'
          + label + '</button>'
        : '')
      + '</div>';
  }

  function bodyHTML(turn) {
    if (turn.status === 'retrieving') {
      return '<div class="loading-card"><span class="loading-text"><span class="dot-pulse" aria-hidden="true"></span><span>正在检索，通常 20 到 60 秒 · '
        + '<span class="elapsed mono" data-elapsed>' + clockText() + '</span></span></span>'
        + '<button class="btn sm stream-pause-btn" type="button" data-toggle-pause="' + turn.n + '">暂停</button></div>';
    }
    if (turn.status === 'answering') {
      const focus = focusExact(turn);
      const fallback = focus
        ? '先取指定书的原文，再取其他书补充，最长约 2 分钟…'
        : '正在组织回答，通常 40 到 90 秒…';
      const line = turn.streamStatus || fallback;
      const head = '<div class="loading-card"><span class="loading-text"><span class="dot-pulse" aria-hidden="true"></span>'
        + '<span><span data-stream-status>' + esc(line) + '</span> · '
        + '<span class="elapsed mono" data-elapsed>' + clockText() + '</span></span></span>'
        + '<button class="btn sm stream-pause-btn" type="button" data-toggle-pause="' + turn.n + '">暂停</button></div>';
      const preview = streamPreviewHTML(turn.streamText);
      return head + (preview
        ? '<div class="answer stream-preview" data-stream-body>' + preview + '</div>'
        : '');
    }
    if (turn.status === 'paused') {
      const line = turn.streamStatus || (turn.pausedAt === 'retrieving'
        ? '已暂停检索 · 点击「继续问答」重新继续'
        : '已暂停 · 点击「继续问答」从当前进度继续');
      const elapsedTag = turn.pausedElapsed
        ? ' · <span class="elapsed mono">已用 ' + turn.pausedElapsed + 's</span>'
        : '';
      const head = '<div class="loading-card is-paused"><span class="loading-text"><span class="pause-dot" aria-hidden="true"></span>'
        + '<span><span data-stream-status>' + esc(line) + '</span>' + elapsedTag + '</span></span>'
        + '<button class="btn sm stream-pause-btn is-resume" type="button" data-toggle-pause="' + turn.n + '">继续问答</button></div>';
      const preview = streamPreviewHTML(turn.streamText);
      return head + (preview
        ? '<div class="answer stream-preview" data-stream-body>' + preview + '</div>'
        : '');
    }
    if (turn.status === 'run-error') {
      return '<p class="answer-error">检索失败：' + esc(turn.runError || '没有记录原因，可能服务重启或连接断开') + '</p>'
        + '<button class="btn" type="button" data-retry-run="' + turn.n + '">重试</button>';
    }
    if (turn.status === 'answer-error') {
      const last = lastTurn();
      const canRetry = last && last.n === turn.n;
      const detail = String(turn.answerError || '').trim();
      const text = !detail
        ? '回答没有完成，也没有记录原因。刷新或重试回答。'
        : (networkError(detail)
          ? '连接中断，回答可能仍在后台，刷新或稍后重试'
          : '回答失败：' + detail);
      return '<p class="answer-error">' + esc(text) + '</p>'
        + '<p class="answer-meta">' + (canRetry
          ? '检索结果已保留，重试只重发回答。'
          : '历史轮次，回答不再重试。') + '</p>'
        + (canRetry
          ? '<button class="btn" type="button" data-retry-answer="' + turn.n + '">重试回答</button>'
          : '');
    }
    if (turn.status === 'answer-interrupted' || turn.status === 'answer-incomplete') {
      const last = lastTurn();
      const canRetry = last && last.n === turn.n;
      const interrupted = turn.status === 'answer-interrupted';
      if (turn.relatedMode && !interrupted) {
        return '<div data-related-body="' + turn.n + '">' + relatedPendingHTML(turn) + '</div>';
      }
      const hit = turn.trace && turn.trace.result && turn.trace.result.path ? turn.trace.result : null;
      let text = hit ? '已完成检索定位，可直达原文精读或按需生成模型回答' : '上次只完成检索，还没有生成回答';
      if (interrupted) {
        text = networkError(turn.answerError)
          ? '连接中断，回答可能仍在后台，刷新或稍后重试'
          : '上次回答被中断（服务重启或连接断开）';
      }
      const hitCard = hit
        ? '<div class="trace-hit-card">'
          + '<div class="trace-hit-head"><b>《' + esc(hit.book_title || hit.book) + '》</b> · '
          + esc(hit.chapter || '') + ' <span class="mono">L' + esc(hit.line || 1) + '</span></div>'
          + (hit.hit_text ? '<p class="trace-hit-quote">' + esc(cut(hit.hit_text, 140)) + '</p>' : '')
          + '</div>'
        : '';
      const openLink = hit
        ? '<a class="btn" href="' + readingOpenHref({
            uid: hit.uid || hit.book,
            path: hit.path,
            line: hit.line,
            quote: hit.hit_text || '',
            question: turn.question,
          }) + '" target="_blank" rel="noopener">在伴读中打开 ↗</a>'
        : '';
      const actionLabel = interrupted ? '重试回答' : (hit ? '由模型归纳回答' : '重试回答');
      return '<p class="' + (interrupted ? 'answer-error' : 'answer-pending-note') + '">' + esc(text) + '</p>'
        + hitCard
        + '<p class="answer-meta">' + (interrupted && turn.answerError && !networkError(turn.answerError)
          ? '原因：' + esc(turn.answerError) + ' · ' : '')
        + (canRetry
          ? '检索结果已保留，点击即可补充模型归纳。'
          : '历史轮次，回答不再重试。') + '</p>'
        + '<div class="trace-pending-actions">'
        + (canRetry
          ? '<button class="btn btn-primary" type="button" data-retry-answer="' + turn.n + '">' + actionLabel + '</button>'
          : '')
        + openLink
        + '</div>';
    }
    const answer = turn.answer || {};
    if (answer.ok === false) {
      return '<p class="answer-error">没有拿到回答：'
        + esc(answer.error || '刷新或重试回答') + '</p>';
    }
    const dissent = (Array.isArray(answer.parts) && answer.parts.length)
      ? '' : plain(answer.dissent);
    const gap = (Array.isArray(answer.parts) && answer.parts.length)
      ? '' : plain(answer.gap);
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
      const readHref = readingOpenHref({
        uid: cite.uid || cite.book,
        path: cite.path,
        line: cite.line,
        quote: cite.quote || '',
        question: turn.question,
      });
      return '<div class="ev-card' + (on ? ' is-on' : '') + (pinned ? ' is-anchor' : '') + '">'
        + '<button class="ev' + (on ? ' is-on' : '') + (pinned ? ' is-anchor' : '')
        + '" type="button" data-cite="' + k + '" data-from="' + turn.n + '">'
        + '<span class="cite' + (on ? ' is-on' : '') + '">[' + k + ']</span>'
        + '<span class="ev-text-col"><span class="ev-src">《' + esc(bookOf(cite)) + '》· ' + esc(chapterOf(cite))
        + (pinned ? '<span class="pin-tag">落点</span>' : '') + '</span>'
        + '<span class="ev-quote pretty">' + esc(cite.quote || '') + '</span></span>'
        + '</button>'
        + '<div class="ev-ops">'
        + '<a class="ev-read" href="' + readHref + '" target="_blank" rel="noopener" data-stop="1" title="在伴读直达这段正文"><span>精读</span>' + ICON_OUT + '</a>'
        + '<span class="ev-go">' + (on ? '落点' : '原文') + '</span>'
        + '</div></div>';
    }).join('');
    return '<section class="evidences"><div class="ev-head">'
      + '<span class="t">本轮依据 · ' + list.length + ' 段</span>'
      + '<span class="h">点一段，右边的原文换过来，只用于下一问</span></div>'
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
    if (turn.answer) {
      parts.push(reanswerHTML(turn));
      parts.push(evidenceHTML(turn));
    }
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
        + '<button class="btn sm" type="button" data-step="-1"' + (pos <= 0 ? ' disabled' : '') + '>‹ 上一条依据</button>'
        + '<button class="btn sm" type="button" data-step="1"' + (pos >= list.length - 1 ? ' disabled' : '')
        + '>下一条依据 ›</button></div>'
      : '';
    const openHref = readingOpenHref({
      uid: cite.uid || cite.book,
      path: cite.path,
      line: cite.line,
      quote: cite.quote || '',
      question: owner ? owner.question : '',
    });
    panelEl.innerHTML = '<div class="drawer-head"><div class="dh-top">'
      + '<span class="cite">[' + (ref.cite || '·') + ']</span>'
      + '<span class="book">《' + esc(bookOf(cite)) + '》</span>' + tags
      + '<button type="button" class="icon-btn mobile-toggle drawer-close-btn" id="btn-close-drawer" aria-label="关闭原文面板">'
      + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
      + '</button></div>'
      + '<p class="dh-sub">' + esc(chapterOf(cite)) + '</p></div>'
      + '<div class="drawer-body">' + body + nav + '</div>'
      + '<div class="drawer-foot"><span class="path" title="' + esc(cite.path) + '">' + esc(cite.path) + '</span>'
      + '<div class="drawer-foot-actions">'
      + '<button class="btn sm has-icon-left" type="button" data-copy="' + esc(cite.path) + '">'
      + '<span class="icon-swap" id="copy-icon-swap" aria-hidden="true">'
      + '<svg class="icon-a" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>'
      + '<svg class="icon-b" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
      + '</span><span>复制路径</span></button>'
      + '<a class="btn sm primary btn-primary has-icon-right" href="' + openHref + '" target="_blank" rel="noopener"><span>在伴读中打开</span>' + ICON_OUT + '</a>'
      + '</div></div>';
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
    const scopeLabel = state.scopeBooks.length
      ? '<span class="on scope-pill">限定书目：'
        + state.scopeBooks.map((b) => '《' + esc(b.title || b.uid) + '》').join('、')
        + ' · <button class="x" type="button" data-clear-scope="1">搜全库</button></span>'
      : '';
    if (state.selected) {
      hintEl.innerHTML = scopeLabel
        + '<span class="on">下一问挂在 <span class="cite">['
        + state.selected.cite + ']</span><span class="qt">'
        + esc(cut(state.selected.citation.quote, 20)) + '</span>· '
        + '<button class="x" type="button" data-clear="1">取消</button></span>';
      field.placeholder = '就 [' + state.selected.cite + '] 这一段接着问';
    } else if (scopeLabel) {
      hintEl.innerHTML = scopeLabel;
      field.placeholder = state.scopeBooks.length === 1
        ? '在《' + (state.scopeBooks[0].title || state.scopeBooks[0].uid) + '》里检索或提问'
        : '在选定的 ' + state.scopeBooks.length + ' 本书里检索或提问';
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
    if (thinkSelectEl) thinkSelectEl.disabled = state.busy;
  }

  function scrollRail() {
    if (state.viewN === railShown) return;
    railShown = state.viewN;
    if (window.innerWidth >= 1280) return;
    const cur = roundsEl.querySelector('.is-current');
    if (cur) roundsEl.scrollLeft = Math.max(0, cur.offsetLeft - 8);
  }

  window.addEventListener('resize', () => {
    railShown = 0;
    scrollRail();
  });

  /* ---- 历史会话 & Native Popover Menu ---------------------------- */

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
        answer_state: answerStateName(last.status) || (found && found.answer_state) || '',
      };
    }
    return state.history.find((row) => row.id === state.sessionId) || null;
  }

  function historyStatusTag(row) {
    const name = String(row.answer_state || '');
    if (name === 'pending' || name === 'incomplete') return { text: '未回答', cls: 'is-incomplete' };
    if (name === 'interrupted') return { text: '已中断', cls: 'is-interrupted' };
    if (name === 'error') return { text: '失败', cls: 'is-error' };
    if (name === 'paused') return { text: '已暂停', cls: 'is-paused' };
    if (name === 'running') return { text: '回答中', cls: 'is-running' };
    return null;
  }

  function histRowHTML(row, current) {
    const books = (row.books || []).length ? '《' + row.books.join('》《') + '》' : '';
    const known = state.history.some((item) => item.id === row.id);
    const picked = known && state.historySelected.has(row.id);
    const selecting = state.historySelectMode;
    const tag = historyStatusTag(row);
    const safeId = String(row.id || '').replace(/[^A-Za-z0-9_-]/g, '');
    const menuId = 'hist-menu-' + safeId + (current ? '-cur' : '');
    const trigId = 'hist-trig-' + safeId + (current ? '-cur' : '');
    const check = !selecting ? ''
      : known
        ? '<label class="hist-check-label" aria-label="选择会话"><input class="hist-check" type="checkbox" data-session-check="' + esc(row.id) + '"'
          + (picked ? ' checked' : '') + '></label>'
        : '<span class="hist-check-gap" aria-hidden="true"></span>';
    return '<div class="hist-item' + (current ? ' is-current' : '')
      + (picked ? ' is-picked' : '') + '">' + check
      + '<button class="hist-row' + (current ? ' is-current' : '') + '" type="button"'
      + ' data-open-session="' + esc(row.id) + '">'
      + '<span class="top"><span>' + esc(whenText(row.updated_at)) + '</span>'
      + '<span>' + (Number(row.turns) || 0) + ' 问'
      + (current ? ' <span class="pin">当前</span>' : '') + '</span></span>'
      + '<span class="q">' + esc(row.question || row.last_question || '') + '</span>'
      + '<span class="meta"><span class="hist-books">' + esc(books) + '</span>'
      + (tag ? '<span class="hist-state ' + tag.cls + '">' + tag.text + '</span>' : '')
      + '</span></button>'
      + '<button class="hist-more-btn" id="' + trigId + '" type="button" data-hist-menu="' + esc(row.id) + '"'
      + ' aria-haspopup="menu" aria-expanded="false" aria-label="会话操作" commandfor="' + menuId + '" command="toggle-popover">'
      + '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
      + '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>'
      + '</svg></button>'
      + '<div id="' + menuId + '" class="menu-popover" popover="auto">'
      + '<div focusgroup="menu nomemory" aria-labelledby="' + trigId + '">'
      + '<button type="button" data-hist-format="md" data-id="' + esc(row.id) + '"><span>导出&nbsp;<span translate="no">Markdown</span></span><span class="hint" translate="no" aria-hidden="true">.md</span></button>'
      + '<button type="button" data-hist-format="docx" data-id="' + esc(row.id) + '"><span>导出&nbsp;<span translate="no">Word</span></span><span class="hint" translate="no" aria-hidden="true">.docx</span></button>'
      + '<button type="button" data-hist-act="multi" data-id="' + esc(row.id) + '"><span>多选管理</span></button>'
      + '<hr>'
      + '<button type="button" class="is-danger" data-hist-act="delete" data-id="' + esc(row.id) + '"><span>删除会话</span></button>'
      + '</div></div></div>';
  }

  function installMenuFallbackIfNeeded() {
    const supportsAnchor = CSS.supports && CSS.supports('position-area: block-end');
    const supportsInvoker = 'commandForElement' in HTMLButtonElement.prototype;
    const supportsFocusgroup = 'focusGroup' in HTMLElement.prototype;

    document.querySelectorAll('.menu-popover').forEach((popover) => {
      if (popover.dataset.wired) return;
      popover.dataset.wired = '1';
      const menuEl = popover.querySelector('[focusgroup~="menu"]');
      const trigger = popover.previousElementSibling;

      if (!supportsFocusgroup && menuEl) {
        menuEl.setAttribute('role', 'menu');
        menuEl.querySelectorAll('button').forEach((item) => item.setAttribute('role', 'menuitem'));
        menuEl.addEventListener('keydown', (e) => {
          if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
          const items = Array.from(menuEl.querySelectorAll('button:not([disabled])'));
          const idx = items.indexOf(document.activeElement);
          if (idx < 0) return;
          e.preventDefault();
          const next = e.key === 'Home' ? 0
            : e.key === 'End' ? items.length - 1
            : (idx + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
          items[next].focus({ preventScroll: true });
        });
      }

      if (!supportsInvoker && trigger) {
        trigger.addEventListener('click', (e) => {
          e.preventDefault();
          if (popover.matches(':popover-open')) popover.hidePopover();
          else popover.showPopover({ source: trigger });
        });
      }

      popover.addEventListener('toggle', (e) => {
        const isOpen = e.newState === 'open';
        if (trigger) trigger.setAttribute('aria-expanded', String(isOpen));
        const item = popover.closest('.hist-item');
        if (item) item.classList.toggle('is-menu', isOpen);
        if (!isOpen) return;

        if (!supportsAnchor && trigger) {
          const r = trigger.getBoundingClientRect();
          const w = popover.offsetWidth || 176;
          const h = popover.offsetHeight || 160;
          const left = Math.min(window.innerWidth - w - 8, Math.max(8, r.right - w));
          let top = r.bottom + 4;
          if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
          popover.style.position = 'fixed';
          popover.style.inset = 'auto';
          popover.style.margin = '0';
          popover.style.left = Math.round(left) + 'px';
          popover.style.top = Math.round(top) + 'px';
        }

        const first = menuEl && menuEl.querySelector('button:not([disabled])');
        if (first) first.focus({ preventScroll: true });
      });
    });
  }

  function histMatch(row, q) {
    if (!q) return true;
    const text = [row.question, row.last_question].concat(row.books || []).join(' ');
    return text.toLowerCase().includes(q);
  }

  function renderHistBar() {
    if (!histBarEl) return;
    if (!state.historySelectMode) {
      histBarEl.hidden = true;
      histBarEl.innerHTML = '';
      return;
    }
    const known = new Set(state.history.map((row) => row.id));
    Array.from(state.historySelected).forEach((id) => {
      if (!known.has(id)) state.historySelected.delete(id);
    });
    const ids = state.historyVisible.filter((id) => known.has(id));
    const picked = ids.filter((id) => state.historySelected.has(id));
    const all = ids.length > 0 && picked.length === ids.length;
    const busy = state.historyDeleting;
    histBarEl.hidden = false;
    histBarEl.innerHTML =
      '<span class="hist-sel-count">已选 ' + state.historySelected.size + ' 项</span>'
      + '<button class="btn sm ghost" type="button" data-hist-select-all'
      + (!ids.length || busy ? ' disabled' : '') + '>' + (all ? '取消全选' : '全选') + '</button>'
      + '<button class="btn sm danger" type="button" data-hist-select-delete'
      + (state.historySelected.size === 0 || busy ? ' disabled' : '') + '>'
      + (busy ? '删除中…' : '删除选中') + '</button>'
      + '<button class="icon-btn" type="button" data-hist-select-cancel'
      + (busy ? ' disabled' : '') + ' aria-label="退出多选">'
      + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
      + '</button>';
  }

  function enterHistSelect(initialId = '') {
    state.historySelectMode = true;
    state.historySelected.clear();
    if (initialId) state.historySelected.add(initialId);
    renderHistory();
  }

  function exitHistSelect() {
    if (state.historyDeleting) return;
    state.historySelectMode = false;
    state.historySelected.clear();
    renderHistory();
  }

  function toggleHistPick(id, on) {
    const next = typeof on === 'boolean' ? on : !state.historySelected.has(id);
    if (next) state.historySelected.add(id);
    else state.historySelected.delete(id);
    renderHistory();
  }

  function toggleHistSelectAll() {
    const known = new Set(state.history.map((row) => row.id));
    const ids = state.historyVisible.filter((id) => known.has(id));
    const all = ids.length > 0 && ids.every((id) => state.historySelected.has(id));
    ids.forEach((id) => {
      if (all) state.historySelected.delete(id);
      else state.historySelected.add(id);
    });
    renderHistory();
  }

  function downloadSession(id, format) {
    if (format !== 'md' && format !== 'docx') return;
    const a = document.createElement('a');
    a.href = '/api/export?id=' + encodeURIComponent(id) + '&format=' + format;
    a.download = '';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function renderHistory() {
    if (!histListEl) return;
    const q = state.historyQuery.trim().toLowerCase();
    const cur = currentSummary();
    const curId = cur ? cur.id : '';
    const rows = state.history.filter((row) => row.id !== curId);
    const known = new Set(state.history.map((row) => row.id));
    const visible = [];
    let html = '';
    let shown = 0;
    if (state.historyError) {
      html += '<p class="hist-empty">' + esc(state.historyError) + '</p>';
    }
    if (cur && histMatch(cur, q)) {
      html += '<p class="hist-group">当前打开</p>' + histRowHTML(cur, true);
      shown += 1;
      if (known.has(cur.id)) visible.push(cur.id);
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
      part.forEach((row) => visible.push(row.id));
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
    state.historyVisible = visible;
    histListEl.innerHTML = html;
    installMenuFallbackIfNeeded();
    renderHistBar();
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
    const known = new Set(state.history.map((row) => row.id));
    Array.from(state.historySelected).forEach((id) => {
      if (!known.has(id)) state.historySelected.delete(id);
    });
    renderRail();
    if (!state.turns.length) renderMain();
  }

  async function deleteSelectedSessions() {
    const ids = Array.from(state.historySelected);
    if (!ids.length || state.historyDeleting) return;
    const confirmed = await openConfirmDialog({
      title: '删除选中的 ' + ids.length + ' 个会话？',
      desc: '删除后将清除对应本地记录，且不可撤销。',
      okLabel: '确认删除',
      danger: true,
    });
    if (!confirmed) return;
    state.historyDeleting = true;
    renderHistory();
    try {
      const data = await postJSON('/api/sessions/delete', { ids });
      const gone = new Set(ids);
      const count = Number(data.count != null ? data.count : data.deleted);
      const label = Number.isFinite(count) ? count : ids.length;
      state.historyDeleting = false;
      state.historySelected.clear();
      state.historySelectMode = false;
      state.history = state.history.filter((row) => !gone.has(row.id));
      if (gone.has(state.sessionId)) {
        newSession();
        toast('已删除 ' + label + ' 个会话，已回到新会话');
      } else {
        render();
        toast('已删除 ' + label + ' 个会话');
      }
      await loadSessions();
    } catch (err) {
      state.historyDeleting = false;
      renderHistory();
      toast('删除失败：' + err.message);
    }
  }

  async function deleteSession(id) {
    if (!id) return;
    const row = state.history.find((item) => item.id === id);
    const q = String((row && (row.question || row.last_question)) || '').trim();
    const label = q ? q.slice(0, 20) : '这个会话';
    const confirmed = await openConfirmDialog({
      title: '删除「' + label + '」？',
      desc: '删除后将清除对应本地记录，且不可撤销。',
      okLabel: '确认删除',
      danger: true,
    });
    if (!confirmed) return;
    try {
      await postJSON('/api/sessions/delete', { ids: [id] });
      const current = id === state.sessionId;
      state.historySelected.delete(id);
      state.history = state.history.filter((item) => item.id !== id);
      if (current) {
        newSession();
        toast('已删除会话，已回到新会话');
      } else {
        render();
        toast('已删除会话');
      }
      await loadSessions();
    } catch (err) {
      toast('删除失败：' + err.message);
    }
  }

  /* ---- 左栏 Tabs 控制 (Modern Web Guidance tabs) ----------------- */

  const tabBtns = [tabCurrentEl, tabHistoryEl];
  const tabPanels = [panelCurrentEl, panelHistoryEl];

  function applyRailTabDOM(moveFocus = false) {
    const idx = state.rail === 'history' ? 1 : 0;
    tabBtns.forEach((btn, i) => {
      if (!btn) return;
      const selected = i === idx;
      btn.classList.toggle('is-on', selected);
      btn.setAttribute('aria-selected', String(selected));
      btn.toggleAttribute('focusgroupstart', selected);
      if (!('focusGroup' in HTMLElement.prototype)) {
        btn.setAttribute('tabindex', selected ? '0' : '-1');
      }
      if (selected && moveFocus) btn.focus();
    });
    tabPanels.forEach((panel, i) => {
      if (!panel) return;
      const selected = i === idx;
      if (selected) {
        panel.removeAttribute('hidden');
        panel.setAttribute('tabindex', '0');
      } else {
        panel.setAttribute('hidden', 'until-found');
        panel.removeAttribute('tabindex');
      }
    });
  }

  tabPanels.forEach((panel, i) => {
    if (!panel) return;
    panel.addEventListener('beforematch', () => {
      state.rail = i === 0 ? 'current' : 'history';
      applyRailTabDOM(false);
    });
  });

  if (!('focusGroup' in HTMLElement.prototype)) {
    const tablist = document.querySelector('.rail-tabs');
    if (tablist) {
      tablist.setAttribute('role', 'tablist');
      tabBtns.forEach((btn, i) => {
        if (!btn) return;
        btn.setAttribute('role', 'tab');
        btn.setAttribute('tabindex', (state.rail === 'history' ? i === 1 : i === 0) ? '0' : '-1');
      });
      tablist.addEventListener('keydown', (e) => {
        const cur = tabBtns.indexOf(document.activeElement);
        if (cur < 0) return;
        let next = cur;
        if (e.key === 'ArrowRight') next = (cur + 1) % tabBtns.length;
        else if (e.key === 'ArrowLeft') next = (cur - 1 + tabBtns.length) % tabBtns.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabBtns.length - 1;
        else return;
        e.preventDefault();
        state.rail = next === 0 ? 'current' : 'history';
        applyRailTabDOM(true);
      });
    }
  }

  function renderRail() {
    applyRailTabDOM(false);
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
      fromN: Number(ref.fromN) || 0,
    };
  }

  async function replayStages(turn) {
    const epoch = state.epoch;
    const active = () => epoch === state.epoch
      && (turn.status === 'answering' || turn.status === 'retrieving');
    for (let i = 0; i < 4; i += 1) {
      if (!active()) return false;
      turn.stageStates = [0, 1, 2, 3, 4].map((k) => {
        if (k < i) return 'done';
        return k === i ? 'active' : 'pending';
      });
      turn.stageOpen = new Set([i]);
      turn.stripOpen = true;
      render();
      await sleep(REPLAY_MS);
    }
    if (!active()) return false;
    turn.stageStates = ['done', 'done', 'done', 'done', 'active'];
    turn.stageOpen = new Set([4]);
    turn.stripOpen = true;
    render();
    return true;
  }

  async function runTurn(turn, opts) {
    const options = opts || {};
    const epoch = state.epoch;
    if (turn.controller) {
      try { turn.controller.abort(); } catch (err) { /* ignore */ }
    }
    const controller = new AbortController();
    turn.controller = controller;
    turn.pausing = false;
    turn.pausedAt = '';
    turn.lastFresh = !!options.fresh;
    turn.status = 'retrieving';
    turn.runError = '';
    turn.streamStatus = '';
    turn.streamText = '';
    turn.stageStates = ['active', 'pending', 'pending', 'pending', 'pending'];
    turn.stageOpen = new Set([0]);
    turn.stripOpen = true;
    syncBusy();
    startClock(options.resumeElapsed || 0);
    render();
    const body = { question: turn.question, thinking: state.thinking };
    if (state.sessionId) {
      body.session_id = state.sessionId;
      body.turn = turn.n;
    }
    if (turn.anchor) body.anchor = turn.anchor;
    if (state.scopeBooks.length) {
      body.scope = { books: state.scopeBooks.map((b) => b.uid) };
    }
    if (turn.relatedMode) {
      body.mode = 'related';
      body.threshold = clampThreshold(turn.relatedThreshold);
      if (Array.isArray(turn.excludeBooks) && turn.excludeBooks.length) {
        body.exclude_books = turn.excludeBooks.slice();
      }
      if (Array.isArray(turn.excludeSpots) && turn.excludeSpots.length) {
        body.exclude_spots = turn.excludeSpots.slice();
      }
      if (turn.sourceText) {
        body.source_text = turn.sourceText;
      }
    }
    if (options.fresh) body.fresh = true;
    try {
      const data = await postJSON('/api/run', body, { signal: controller.signal });
      if (epoch !== state.epoch) return;
      turn.controller = null;
      if (turn.pausing || turn.status === 'paused') return;
      turn.trace = data;
      turn.related = data.related || null;
      turn.sources = defaultSources(turn);
      turn.answerSources = [];
      if (data.session_id && data.session_id !== state.sessionId) {
        state.sessionId = data.session_id;
        setUrl(state.sessionId);
      }
      if (options.traceOnly) {
        turn.status = 'answer-incomplete';
        turn.stageStates = ['done', 'done', 'done', 'done', 'pending'];
        turn.stageOpen = new Set([2, 3]);
        turn.stripOpen = true;
        stopClock();
        syncBusy();
        render();
        loadSessions();
        const ref = defaultRef(turn);
        if (ref) showCitation(ref);
        return;
      }
      render();
      loadSessions();
      const ansPromise = answerTurn(turn, options.fresh ? { fresh: true } : {});
      const ok = await replayStages(turn);
      await ansPromise;
      if (!ok) return;
    } catch (err) {
      if (epoch !== state.epoch) return;
      turn.controller = null;
      if (err && err.name === 'AbortError' && turn.pausing) {
        turn.pausing = false;
        return;
      }
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
    const focus = focusOf(turn);
    const ordered = focus && focus.status === 'exact'
      ? focusFirstSources(turn, sources) : sources;
    if (!ordered.length) {
      const missing = focus && focus.status === 'exact' ? focusName(focus) : '';
      turn.status = 'answer-error';
      turn.answerError = missing
        ? '《' + missing + '》没有可读的原文路径，未拿其他书代答'
        : '这一轮没有可用的来源';
      turn.stageStates = ALL_DONE.slice();
      turn.stageOpen = new Set();
      turn.stripOpen = false;
      stopClock();
      syncBusy();
      render();
      return;
    }
    if (turn.controller) {
      try { turn.controller.abort(); } catch (err) { /* ignore */ }
    }
    const controller = new AbortController();
    turn.controller = controller;
    turn.pausing = false;
    turn.pausedAt = '';
    turn.sources = ordered;
    turn.answerSources = ordered.map(cloneSource);
    turn.answer = null;
    turn.context = null;
    turn.thinking = state.thinking;
    turn.status = 'answering';
    turn.answerError = '';
    turn.streamStatus = options.resume ? '正在从暂停处继续生成回答…' : '';
    const prePauseText = options.initialStreamText ? String(options.initialStreamText) : '';
    turn.streamText = prePauseText;
    let streamReplaced = !prePauseText;
    turn.stageStates = ['done', 'done', 'done', 'done', 'active'];
    turn.stageOpen = new Set([4]);
    turn.stripOpen = true;
    syncBusy();
    startClock(options.resumeElapsed || 0);
    render();
    const primary = ordered[0];
    const answerQuestion = turn.relatedMode
      ? ('请结合选中的这几本书原文，对比、补充或阐释与下面选段相关的论述：「' + turn.question + '」').slice(0, 300)
      : turn.question;
    const body = {
      question: answerQuestion,
      book: primary.book || bookKey(primary.path),
      path: primary.path,
      line: Number(primary.line) || 1,
      terms: uniq([].concat(extract.terms || [], plan.nouns || [])).slice(0, 12),
      groups: Array.isArray(extract.groups) ? extract.groups : [],
      sources: ordered.map((s) => ({
        book: s.book || '', title: s.title || '', path: s.path, line: Number(s.line) || 1,
      })),
      focus: focus || null,
      session_id: state.sessionId,
      turn: turn.n,
      thinking: state.thinking,
    };
    if (options.fresh || options.resume) body.fresh = true;
    if (options.resume) body.resume = true;
    try {
      const data = await postAnswerStream('/api/answer', body, (ev) => {
        if (epoch !== state.epoch || turn.pausing || turn.status === 'paused') return;
        if (ev.type === 'answer_delta') {
          if (typeof ev.text === 'string' && ev.text) {
            if (streamReplaced || ev.text.length >= 16 || ev.text.length >= prePauseText.length) {
              streamReplaced = true;
              turn.streamText = ev.text;
            }
          } else if (typeof ev.delta === 'string') {
            if (!streamReplaced) {
              streamReplaced = true;
              turn.streamText = '';
            }
            turn.streamText += ev.delta;
          }
          if (!turn.streamStatus || turn.streamStatus.indexOf('正在生成回答') < 0) {
            turn.streamStatus = '正在生成回答正文…';
          }
          if (state.viewN === turn.n) updateStreamDOM(turn);
          return;
        }
        if (ev.message && (ev.type === 'stage' || ev.type === 'tool_start' || ev.type === 'thinking')) {
          turn.streamStatus = String(ev.message);
          if (state.viewN === turn.n) updateStreamDOM(turn);
        }
      }, { signal: controller.signal });
      if (epoch !== state.epoch) return;
      turn.controller = null;
      if (data && data.paused) {
        turn.pausing = false;
        turn.pausedAt = 'answering';
        turn.status = 'paused';
        if (data.partial_text && !turn.streamText) turn.streamText = String(data.partial_text);
        turn.streamStatus = data.message || '已暂停 · 点击「继续问答」从当前进度继续';
        stopClock();
        syncBusy();
        render();
        loadSessions();
        return;
      }
      if (turn.pausing || turn.status === 'paused') return;
      stopAnswerPoll();
      turn.answer = data;
      turn.status = 'ready';
      if (data.session_id && !state.sessionId) {
        state.sessionId = data.session_id;
        setUrl(state.sessionId);
      }
    } catch (err) {
      if (epoch !== state.epoch) return;
      turn.controller = null;
      if (err && err.name === 'AbortError' && (turn.pausing || turn.status === 'paused')) {
        turn.pausing = false;
        return;
      }
      if (networkError(err.message)) {
        turn.status = 'answer-interrupted';
        turn.answerError = '连接中断，回答可能仍在后台，刷新或稍后重试';
        if (state.sessionId) startAnswerPoll(state.sessionId, turn.n, epoch);
      } else {
        turn.status = 'answer-error';
        turn.answerError = err.message;
      }
    }
    if (epoch !== state.epoch || turn.status === 'paused') return;
    turn.streamStatus = '';
    turn.streamText = '';
    turn.pausedElapsed = 0;
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

  async function togglePause(n) {
    const turn = state.turns.find((t) => t.n === n);
    if (!turn) return;
    if (turn.status === 'paused') {
      if (turn.pausedAt === 'retrieving' || !turn.trace) {
        await runTurn(turn, { fresh: !!turn.lastFresh, resumeElapsed: turn.pausedElapsed || 0 });
      } else {
        await answerTurn(turn, {
          resume: true,
          fresh: true,
          initialStreamText: turn.streamText || '',
          resumeElapsed: turn.pausedElapsed || 0,
        });
      }
      return;
    }
    if (turn.status !== 'retrieving' && turn.status !== 'answering') return;
    const phase = turn.status;
    turn.pausedAt = phase;
    turn.pausing = true;
    turn.pausedElapsed = state.elapsed || 0;
    turn.status = 'paused';
    turn.streamStatus = phase === 'retrieving'
      ? '已暂停检索 · 点击「继续问答」重新继续'
      : '已暂停 · 点击「继续问答」从当前进度继续';
    stopClock();
    syncBusy();
    render();
    if (phase === 'retrieving') {
      if (turn.controller) {
        try { turn.controller.abort(); } catch (err) { /* ignore */ }
      }
      loadSessions();
      return;
    }
    const ctl = turn.controller;
    if (state.sessionId) {
      try {
        await postJSON('/api/answer/pause', { session_id: state.sessionId, turn: turn.n });
      } catch (err) {
        // 即使暂停接口慢或失败，前端仍保持暂停态
      }
    }
    setTimeout(() => {
      if (turn.controller && turn.controller === ctl && turn.status === 'paused') {
        try { ctl.abort(); } catch (err) { /* ignore */ }
      }
    }, 1500);
    loadSessions();
  }

  async function ask(question, opts) {
    const options = opts || {};
    const text = String(question || '').trim();
    if (!text || state.busy) return;
    state.turns = state.turns.filter((t) => t.status !== 'run-error');
    const prev = lastTurn();
    const anchor = anchorPayload();
    const turn = {
      n: prev ? prev.n + 1 : 1,
      question: text,
      thinking: state.thinking,
      trace: null,
      related: null,
      anchor,
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
      relatedMode: Boolean(options.relatedMode),
      relatedThreshold: clampThreshold(options.threshold),
      excludeBooks: Array.isArray(options.excludeBooks) ? options.excludeBooks.slice() : [],
      excludeBookMeta: Array.isArray(options.excludeBookMeta) ? options.excludeBookMeta.slice() : [],
      excludeSpots: Array.isArray(options.excludeSpots) ? options.excludeSpots.slice() : [],
      sourceText: options.sourceText || '',
    };
    state.turns.push(turn);
    if (anchor) {
      state.selected = null;
      state.shown = null;
    }
    state.viewN = turn.n;
    state.rail = 'current';
    state.error = '';
    field.value = '';
    field.closest('.ask-field').classList.remove('has-text');
    render();
    field.focus();
    await runTurn(turn, options);
  }

  function retryRun(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    turn.answer = null;
    turn.context = null;
    turn.thinking = state.thinking;
    state.viewN = n;
    state.epoch += 1;
    runTurn(turn);
  }

  function retryAnswer(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    stopAnswerPoll();
    state.viewN = n;
    answerTurn(turn, { fresh: true });
  }

  function toggleRelatedBook(n, bookUid) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n) return;
    const cand = relatedQualified(turn).find((c) => c.book === bookUid);
    if (!cand) return;
    const chosen = turn.sources || [];
    const on = chosen.some((s) => sourceKey(s) === bookUid);
    if (on) {
      if (chosen.length <= 1) {
        toast('至少留一本');
        return;
      }
      turn.sources = chosen.filter((s) => sourceKey(s) !== bookUid);
    } else {
      if (chosen.length >= MAX_SOURCES) {
        toast('最多 ' + MAX_SOURCES + ' 本');
        return;
      }
      turn.sources = chosen.concat([{
        book: cand.book,
        title: cand.title,
        chapter: cand.chapter,
        path: cand.path,
        line: cand.line,
      }]);
    }
    updateRelatedInPlace(n);
  }

  function applyRelatedThreshold(n, val, inPlace = false) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || !turn.relatedMode) return;
    turn.relatedThreshold = clampThreshold(val);
    turn.sources = relatedDefaultSources(turn);
    if (inPlace) updateRelatedInPlace(n);
    else render();
  }

  function toggleBook(n, key) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n) return;
    const choice = bookChoices(turn).find((row) => sourceKey(row.source) === key);
    if (!choice) return;
    if (choice.locked) {
      toast(choice.role === 'focus' ? '指定书固定入选' : '落点只用于下一问；检索有结果时以结果为准');
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
      ? (focusExact(turn) ? focusOnlySources(turn) : defaultSources(turn))
      : selectable.map((row) => cloneSource(row.source));
    render();
  }

  function reanswer(n) {
    const turn = state.turns.find((t) => t.n === n);
    const last = lastTurn();
    if (!turn || !last || last.n !== n || state.busy) return;
    answerTurn(turn, { sources: turn.sources, fresh: false });
  }

  async function reanswerTurn(n) {
    const turn = state.turns.find((t) => t.n === n);
    if (!turn || !turn.answer || turn.answer.ok === false || state.busy) return;
    const later = state.turns.filter((t) => t.n > n);
    if (later.length) {
      const ok = await openConfirmDialog({
        title: '重新回答第 ' + n + ' 问？',
        desc: '重新回答会清掉它之后的 ' + later.length + ' 个问题，且不可恢复。',
        okLabel: '继续重答',
        danger: true,
      });
      if (!ok) return;
    }
    state.viewN = n;
    state.epoch += 1;
    state.turns = state.turns.filter((t) => t.n <= n);
    if (state.selected && state.selected.fromN >= n) state.selected = null;
    if (state.shown && state.shown.fromN >= n) state.shown = null;
    turn.answer = null;
    turn.context = null;
    turn.answerSources = [];
    runTurn(turn, { fresh: true });
  }

  function newSession() {
    state.epoch += 1;
    stopClock();
    stopAnswerPoll();
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
    state.historySelectMode = false;
    state.historySelected.clear();
    state.scopeBooks = [];
    state.scopeOrigin = '';
    if (histFilterEl) histFilterEl.value = '';
    field.value = '';
    field.closest('.ask-field').classList.remove('has-text');
    closeDrawers(false);
    setUrl('');
    render();
    field.focus();
  }

  /* ---- 交互 ------------------------------------------------------ */

  function defaultRef(turn) {
    const anchor = anchorRef(turn);
    if (anchor) return anchor;
    const first = citesOf(turn)[0];
    if (first) return { citation: first, fromN: turn.n, cite: 1 };
    const hit = turn && turn.trace && turn.trace.result;
    if (hit && hit.path) {
      return {
        citation: {
          path: hit.path,
          line: Number(hit.line) || 1,
          book: hit.book || bookKey(hit.path),
          uid: hit.uid || hit.book || bookKey(hit.path),
          book_title: hit.book_title || '',
          chapter: hit.chapter || '',
          quote: hit.hit_text || '',
        },
        fromN: turn.n,
        cite: 0,
      };
    }
    return null;
  }

  function viewRound(n) {
    state.viewN = n;
    mainEl.scrollTop = 0;
    closeDrawers(false);
    showCitation(defaultRef(viewTurn()));
  }

  function openDrawer() {
    if (window.matchMedia('(max-width: 1279px)').matches) {
      openMobileDrawer(drawerEl, $('#drawer-toggle'));
    }
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
      const swap = $('#copy-icon-swap');
      if (swap) {
        swap.classList.add('swapped');
        setTimeout(() => swap.classList.remove('swapped'), 1400);
      }
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
    if (e.target.closest('#brand-link')) {
      e.preventDefault();
      newSession();
      return;
    }
    if (e.target.closest('[data-stop]')) {
      return;
    }
    const menuFormat = e.target.closest('[data-hist-format]');
    if (menuFormat) {
      const popover = menuFormat.closest('[popover]');
      if (popover && popover.hidePopover) popover.hidePopover();
      downloadSession(menuFormat.dataset.id, menuFormat.dataset.histFormat);
      return;
    }
    const menuAct = e.target.closest('[data-hist-act]');
    if (menuAct) {
      const id = menuAct.dataset.id;
      const act = menuAct.dataset.histAct;
      const popover = menuAct.closest('[popover]');
      if (popover && popover.hidePopover) popover.hidePopover();
      if (act === 'multi') enterHistSelect(id);
      else if (act === 'delete') deleteSession(id);
      return;
    }
    if (e.target.closest('#btn-close-drawer')) {
      closeDrawers(true);
      return;
    }
    if (e.target.closest('#drawer-toggle')) {
      if (drawerEl.classList.contains('is-open')) closeDrawers(true);
      else openMobileDrawer(drawerEl, e.target.closest('#drawer-toggle'));
      return;
    }
    if (e.target.closest('[data-clear-scope]')) {
      state.scopeBooks = [];
      state.scopeOrigin = '';
      renderHint();
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
    const relPicker = e.target.closest('[data-related-pick]');
    if (relPicker) {
      toggleRelatedBook(Number(relPicker.dataset.from), relPicker.dataset.relatedPick);
      return;
    }
    const relThrSet = e.target.closest('[data-related-threshold-set]');
    if (relThrSet) {
      applyRelatedThreshold(
        Number(relThrSet.dataset.from),
        Number(relThrSet.dataset.relatedThresholdSet),
        true
      );
      return;
    }
    const allBooks = e.target.closest('[data-books-all]');
    if (allBooks) {
      toggleAllBooks(Number(allBooks.dataset.booksAll));
      return;
    }
    const reanswerTurnBtn = e.target.closest('[data-reanswer-turn]');
    if (reanswerTurnBtn) {
      reanswerTurn(Number(reanswerTurnBtn.dataset.reanswerTurn));
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
    const pauseBtn = e.target.closest('[data-toggle-pause]');
    if (pauseBtn) {
      togglePause(Number(pauseBtn.dataset.togglePause));
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
      if (state.historySelectMode) {
        toggleHistPick(opener.dataset.openSession);
        return;
      }
      const id = opener.dataset.openSession;
      const cur = currentSummary();
      if (cur && cur.id === id && state.turns.length) {
        const last = lastTurn();
        state.rail = 'current';
        state.viewN = last ? last.n : state.viewN;
        closeDrawers(false);
        render();
        return;
      }
      openSession(id);
      return;
    }
    if (e.target.closest('[data-hist-select-cancel]')) {
      exitHistSelect();
      return;
    }
    if (e.target.closest('[data-hist-select-delete]')) {
      deleteSelectedSessions();
      return;
    }
    if (e.target.closest('[data-hist-select-all]')) {
      toggleHistSelectAll();
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
      applyRailTabDOM(false);
      return;
    }
    if (e.target.closest('#tab-history')) {
      state.rail = 'history';
      applyRailTabDOM(false);
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

  mainEl.addEventListener('input', (e) => {
    const range = e.target.closest('[data-related-threshold]');
    if (!range) return;
    const n = Number(range.dataset.relatedThreshold);
    const val = Math.max(0, Math.min(95, Math.round(Number(range.value) || 0))) / 100;
    applyRelatedThreshold(n, val, true);
  });

  mainEl.addEventListener('change', (e) => {
    const range = e.target.closest('[data-related-threshold]');
    if (!range) return;
    const n = Number(range.dataset.relatedThreshold);
    const val = Math.max(0, Math.min(95, Math.round(Number(range.value) || 0))) / 100;
    applyRelatedThreshold(n, val, true);
  });

  if (histFilterEl) {
    histFilterEl.addEventListener('input', () => {
      state.historyQuery = histFilterEl.value;
      renderHistory();
    });
  }

  if (histListEl) {
    histListEl.addEventListener('change', (e) => {
      const box = e.target.closest('[data-session-check]');
      if (!box) return;
      toggleHistPick(box.dataset.sessionCheck, box.checked);
    });
  }

  window.addEventListener('beforeunload', stopAnswerPoll);
  window.addEventListener('pagehide', stopAnswerPoll);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeDrawers(true);
      return;
    }
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
    if (answer.mode === 'anchored') {
      const focus = focusExact(turn);
      const used = (Array.isArray(answer.sources) && answer.sources.length)
        ? answer.sources.map(sourceFromAnswer).filter(Boolean) : [];
      const primary = sourceFromFocus(turn, used);
      const list = (primary ? [primary] : []).concat(
        used, focus ? focusSupplements(turn, focus) : []);
      if (list.length) return focusFirstSources(turn, list);
    }
    const top = sourceFromResult((turn.trace || {}).result);
    if (top) return [top];
    const anchor = sourceFromAnchor(turn.anchor);
    if (anchor && (!turn.anchor.fromN || Number(turn.anchor.fromN) === turn.n - 1)) return [anchor];
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
    const relMeta = trace && trace.related_search && trace.related_search.active ? trace.related_search : null;
    const saved = savedStatusOf(turn);
    const exclBooks = relMeta && Array.isArray(relMeta.exclude_books) ? relMeta.exclude_books.slice() : [];
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
      status: saved.status,
      runError: '',
      answerError: saved.error,
      streamText: saved.status === 'paused' ? String(turn.answer_partial || '') : '',
      streamStatus: '',
      pausedAt: saved.status === 'paused' ? (trace ? 'answering' : 'retrieving') : '',
      pausedElapsed: 0,
      stageStates: ALL_DONE.slice(),
      stageOpen: new Set(),
      stripOpen: false,
      relatedMode: Boolean(relMeta),
      relatedThreshold: relMeta ? clampThreshold(relMeta.threshold) : DEFAULT_RELATED_THRESHOLD,
      excludeBooks: exclBooks,
      excludeBookMeta: exclBooks.map((uid) => ({ uid, title: uid })),
      excludeSpots: relMeta && Array.isArray(relMeta.exclude_spots) ? relMeta.exclude_spots.slice() : [],
      sourceText: turn.question || '',
    };
    if (row.relatedMode && !answer) {
      row.sources = relatedDefaultSources(row);
      return row;
    }
    const restored = savedSources(row);
    row.answerSources = restored.map(cloneSource);
    row.sources = restored.map(cloneSource);
    const anchor = sourceFromAnchor(row.anchor);
    if (anchor && !row.sources.some((s) => sourceKey(s) === sourceKey(anchor))) {
      row.sources = [anchor].concat(row.sources);
    }
    row.sources = focusFirstSources(row, row.sources);
    row.answerSources = focusFirstSources(row, row.answerSources);
    return row;
  }

  async function openSession(sessionId) {
    stopClock();
    stopAnswerPoll();
    const epoch = ++state.epoch;
    try {
      const data = await fetchJSON('/api/session?id=' + encodeURIComponent(sessionId));
      if (epoch !== state.epoch) return;
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
      closeDrawers(false);
      setUrl(state.sessionId);
      syncBusy();
      if (state.busy) startClock();
      render();
      const exclAll = uniq([].concat(...state.turns.map((t) => t.excludeBooks || [])));
      if (exclAll.length) loadExcludeBookMeta(exclAll);
      if (last) showCitation(defaultRef(last));
      if (last && last.status === 'answering') {
        startAnswerPoll(state.sessionId, last.n, epoch);
      }
    } catch (err) {
      if (epoch !== state.epoch) return;
      state.error = '会话载入失败：' + err.message;
      state.sessionId = '';
      state.turns = [];
      state.viewN = 0;
      state.rail = 'current';
      state.busy = false;
      setUrl('');
      render();
    }
  }

  async function boot() {
    loadStats();
    loadSessions();
    const params = new URLSearchParams(location.search);
    const paramThinking = params.get('thinking');
    setThinking(paramThinking || savedThinking(), Boolean(paramThinking));
    if (thinkSelectEl) {
      thinkSelectEl.addEventListener('change', () => setThinking(thinkSelectEl.value, true));
    }
    const sessionId = params.get('session') || '';
    const question = params.get('q') || '';
    const booksRaw = params.get('books') || params.get('book') || '';
    const excludeRaw = params.get('exclude') || params.get('exclude_books') || '';
    const srcPath = (params.get('src_path') || '').trim();
    const srcLine = Number(params.get('src_line') || 0) || 0;
    const thresholdParam = params.get('threshold');
    const mode = (params.get('mode') || '').trim().toLowerCase();
    state.scopeOrigin = (params.get('from') || '').trim();
    const uids = booksRaw.split(',').map((s) => s.trim()).filter(Boolean);
    if (uids.length) {
      state.scopeBooks = uids.map((uid) => ({ uid, title: uid }));
      loadScopeBookMeta(uids);
    }
    const exclUids = excludeRaw.split(',').map((s) => s.trim()).filter(Boolean);
    if (exclUids.length) {
      state.excludeBooks = exclUids.map((uid) => ({ uid, title: uid }));
      loadExcludeBookMeta(exclUids);
    }
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
      if (mode === 'prefill') {
        field.value = question;
        field.closest('.ask-field').classList.add('has-text');
        field.focus();
      } else if (mode === 'related') {
        setUrl('');
        await ask(question, {
          traceOnly: true,
          relatedMode: true,
          threshold: clampThreshold(thresholdParam),
          excludeBooks: exclUids,
          excludeBookMeta: state.excludeBooks,
          excludeSpots: srcPath ? [{ path: srcPath, line: srcLine }] : [],
          sourceText: question,
        });
      } else {
        setUrl('');
        await ask(question, { traceOnly: mode === 'trace' });
      }
    } else if (uids.length) {
      field.focus();
    }
  }

  boot();
})();
