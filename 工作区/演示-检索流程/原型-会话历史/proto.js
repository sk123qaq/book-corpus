(() => {
  'use strict';

  const DATA = window.PROTO_DATA;
  const TURN = DATA.turn;
  const STAGE_NAMES = TURN.stages.map((s) => s.name);
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const VARIANT_LABELS = {
    v1: 'V1 · 居中索引',
    v2: 'V2 · 时间线',
    v3: 'V3 · 单卡手风琴',
    v4: 'V4 · 台账',
  };
  const ALL_PENDING = ['pending', 'pending', 'pending', 'pending', 'pending'];
  const ALL_DONE = ['done', 'done', 'done', 'done', 'done'];
  const FIELD_LABELS = { rounds: '轮次', rank: '排序', cards: '候选', result: '命中' };

  const state = {
    screen: 'start',
    variant: 'v1',
    stageStates: ALL_DONE.slice(),
    openRows: new Set(),
    playing: false,
    timer: null,
  };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function fmtMs(ms) {
    const n = Number(ms);
    if (!isFinite(n) || n < 0) return '—';
    if (n < 10) return n.toFixed(1) + ' ms';
    if (n < 1000) return Math.round(n) + ' ms';
    return (n / 1000).toFixed(1) + ' s';
  }

  let toastTimer = null;
  function toast(text) {
    const el = $('#toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
  }

  function status(text) {
    $('#proto-status').textContent = text;
  }

  /* ---- 横条（四种展开样式共用） ---------------------------------- */

  function stripHTML() {
    const steps = TURN.stages.map((stage, i) =>
      '<span class="step is-pending" data-i="' + i + '">'
      + '<i class="dot" aria-hidden="true"></i>'
      + '<span class="sn">' + pad(i + 1) + '</span>'
      + '<span class="sname">' + esc(stage.name) + '</span>'
      + '</span>').join('');
    return '<div class="strip"><button class="strip-bar" type="button" id="strip-toggle"'
      + ' aria-expanded="false" aria-controls="stage-stack">'
      + '<span class="steps">' + steps + '</span>'
      + '<span class="strip-toggle"><span class="t-open">展开</span>'
      + '<span class="t-close">收起</span><span class="strip-caret" aria-hidden="true">⌄</span></span>'
      + '</button></div>';
  }

  /* ---- 阶段内容：四版共用的一套数据 ------------------------------ */

  function chipsHTML(stage) {
    return stage.chips.map((item) => '<span class="stage-chip">' + esc(item) + '</span>').join('');
  }

  function quoteHTML(stage) {
    return stage.quote ? '<p class="stage-quote">「' + esc(stage.quote) + '」</p>' : '';
  }

  function loadingHTML(stage) {
    return '<p class="stage-loading"><i class="blink-dot"></i><span>' + esc(stage.loading) + '</span></p>';
  }

  function rawHTML(stage) {
    return '<details class="raw"><summary>原始数据</summary><pre>'
      + esc(JSON.stringify(stage.raw, null, 1))
      + '</pre></details>';
  }

  function v1Body(stage) {
    const chips = stage.key === 'understand'
      ? '' : '<div class="stage-chips">' + chipsHTML(stage) + '</div>';
    return '<p class="v1-lead">' + esc(stage.summary) + '</p>'
      + '<p class="v1-meta">' + esc(stage.meta) + '</p>'
      + chips + quoteHTML(stage);
  }

  function v2Body(stage) {
    return '<p class="stage-sub">' + esc(stage.meta) + '</p>'
      + '<div class="stage-chips">' + chipsHTML(stage) + '</div>'
      + quoteHTML(stage);
  }

  function v3Body(stage) {
    const field = stage.key === 'understand' ? ''
      : '<div class="v3-field"><span class="v3-k">' + FIELD_LABELS[stage.key] + '</span>'
        + '<span class="v3-v">'
        + (stage.key === 'result'
          ? quoteHTML(stage)
          : '<div class="stage-chips">' + chipsHTML(stage) + '</div>')
        + '</span></div>';
    return '<p class="v3-lead">' + esc(stage.summary) + '</p>'
      + '<p class="v3-meta">' + esc(stage.meta) + '</p>'
      + field;
  }

  function v4Body(stage) {
    const detail = stage.key === 'understand' ? ''
      : '<div class="v4-field"><span class="v4-k">细节</span><span class="v4-v">'
        + (stage.key === 'result'
          ? quoteHTML(stage)
          : '<div class="stage-chips">' + chipsHTML(stage) + '</div>')
        + '</span></div>';
    return '<div class="v4-detail">'
      + '<div class="v4-field"><span class="v4-k">摘要</span><span class="v4-v">'
      + esc(stage.summary) + '</span></div>'
      + detail
      + '<div class="v4-field"><span class="v4-k">线索</span><span class="v4-v">'
      + esc(stage.meta) + '</span></div>'
      + '</div>';
  }

  function bodyDoneHTML(variant, stage) {
    if (variant === 'v2') return v2Body(stage);
    if (variant === 'v3') return v3Body(stage);
    if (variant === 'v4') return v4Body(stage);
    return v1Body(stage);
  }

  /* ---- 阶段头：四版各自的结构 ------------------------------------ */

  function caretHTML() {
    return '<span class="scaret" aria-hidden="true">⌄</span>';
  }

  function headHTML(variant, stage, i) {
    const num = '<span class="' + variant + '-num mono">' + pad(i + 1) + '</span>';
    const name = '<span class="' + variant + '-name stage-name">' + esc(stage.name) + '</span>';
    const ms = '<span class="' + variant + '-ms mono">' + fmtMs(stage.ms) + '</span>';
    const attrs = ' class="stage-row-head ' + variant + '-head" type="button"'
      + ' aria-expanded="false" aria-controls="stage-body-' + i + '"';

    if (variant === 'v1') {
      return '<button' + attrs + '>'
        + '<span class="v1-center">' + num + name + '</span>'
        + '<span class="v1-right">' + ms + caretHTML() + '</span>'
        + '</button>';
    }
    if (variant === 'v2') {
      return '<button' + attrs + '>'
        + '<span class="v2-rail"><i class="v2-dot" aria-hidden="true"></i>' + num + '</span>'
        + '<span class="v2-main">' + name
        + '<span class="v2-sum">' + esc(stage.summary) + '</span></span>'
        + ms + caretHTML()
        + '</button>';
    }
    if (variant === 'v3') {
      return '<button' + attrs + '>'
        + num + name
        + '<span class="v3-val">' + esc(stage.value) + '</span>'
        + ms + caretHTML()
        + '</button>';
    }
    return '<button' + attrs + '>'
      + '<span class="v4-name">' + num
      + '<span class="stage-name">' + esc(stage.name) + '</span></span>'
      + '<span class="v4-sum">' + esc(stage.value) + '</span>'
      + ms + caretHTML()
      + '</button>';
  }

  function rowHTML(variant, stage, i) {
    return '<article class="stage-row ' + variant + '-row" data-i="' + i + '">'
      + headHTML(variant, stage, i)
      + '<div class="stage-row-body ' + variant + '-body" id="stage-body-' + i + '">'
      + '<div class="stage-body-done">' + bodyDoneHTML(variant, stage) + '</div>'
      + loadingHTML(stage) + rawHTML(stage)
      + '</div></article>';
  }

  function renderStack() {
    const variant = state.variant;
    const el = $('#stage-stack');
    const rows = TURN.stages.map((stage, i) => rowHTML(variant, stage, i)).join('');
    el.className = 'stage-stack ' + variant;
    el.innerHTML = (variant === 'v4'
      ? '<div class="v4-headrow" aria-hidden="true"><span>阶段</span><span>摘要</span>'
        + '<span>耗时</span><span></span></div>'
      : '') + rows;
  }

  /* ---- 状态绘制 -------------------------------------------------- */

  function setStackOpen(open) {
    $('#stages').classList.toggle('is-open', open);
    const btn = $('#strip-toggle');
    if (btn) btn.setAttribute('aria-expanded', String(open));
  }

  function paintStages() {
    const states = state.stageStates;
    $$('#stages .step').forEach((el, i) => {
      el.classList.toggle('is-done', states[i] === 'done');
      el.classList.toggle('is-active', states[i] === 'active');
      el.classList.toggle('is-pending', states[i] === 'pending');
    });
    $$('#stages .stage-row').forEach((row, i) => {
      const st = states[i];
      row.classList.toggle('is-done', st === 'done');
      row.classList.toggle('is-active', st === 'active');
      row.classList.toggle('is-pending', st === 'pending');
      row.classList.toggle('is-loading', st === 'active'
        && (state.screen === 'running' || state.screen === 'answering' || state.screen === 'playing'));
      const open = state.openRows.has(i);
      row.classList.toggle('is-open', open);
      const head = row.querySelector('.stage-row-head');
      head.disabled = st === 'pending';
      head.setAttribute('aria-expanded', String(open));
      const caret = row.querySelector('.scaret');
      if (caret) {
        caret.textContent = state.variant === 'v4'
          ? (open ? '−' : '+')
          : (open ? '⌃' : '⌄');
      }
    });
  }

  /* ---- 中栏其他块 ------------------------------------------------ */

  function renderBooks() {
    const rows = TURN.books.map((book) =>
      '<span class="book' + (book.on ? ' is-on' : '') + '">《' + esc(book.title) + '》'
      + (book.role ? '<span class="role">' + esc(book.role) + '</span>' : '')
      + '</span>').join('');
    $('#books').innerHTML = '<div class="books-head">'
      + '<span class="t">候选书</span><span class="h">6 本 · 已选 2/6 本</span>'
      + '<button class="books-all" type="button">全选</button></div>'
      + '<div class="book-row">' + rows + '</div>'
      + '<p class="books-note">选中 2 本以上时，回答跨书，上下文预算 10,000 字。</p>';
  }

  function renderAnchor() {
    const a = TURN.anchor;
    $('#anchor').innerHTML = '<span class="lbl">追问落点 · 来自 ' + pad(a.from) + '</span>'
      + '<span class="cite">[' + a.cite + ']</span>'
      + '<span class="quote">' + esc(a.quote) + '</span>'
      + '<span class="src">《' + esc(a.book) + '》 · ' + esc(a.chapter)
      + ' <span class="go">→</span></span>';
  }

  function renderAnswer() {
    const a = TURN.answer;
    $('#answer').innerHTML = '<p class="answer-lead">' + esc(a.lead) + '</p>'
      + a.sections.map((section) =>
        '<h2>' + esc(section.h) + '</h2><p>' + esc(section.p) + '</p>').join('')
      + '<p class="answer-gap"><span class="mono">留白</span>' + esc(a.gap) + '</p>';
  }

  function renderEvidences() {
    const rows = TURN.cites.map((cite) =>
      '<button class="ev' + (cite.on ? ' is-on' : '') + '" type="button" data-n="' + cite.n + '">'
      + '<span class="n mono">[' + cite.n + ']</span>'
      + '<span><span class="src">《乡土中国（2）》· ' + esc(cite.chapter)
      + ' · 第 ' + cite.line + ' 行</span>'
      + '<span class="quote">' + esc(cite.quote) + '</span></span>'
      + '<span class="go">' + (cite.on ? '原文' : '→') + '</span>'
      + '</button>').join('');
    $('#evidences').innerHTML = '<div class="ev-head">'
      + '<span class="t">本轮依据 · 6 段</span>'
      + '<span class="h">点一段，右边的原文换过来，也设为下一问的落点</span></div>'
      + rows;
  }

  function renderDrawer() {
    const d = TURN.drawer;
    $('#drawer-full').innerHTML = '<div class="drawer-head"><div class="dh-top">'
      + '<span class="cite">[1]</span><span class="book">《' + esc(d.book) + '》</span>'
      + '<span class="pin">这一问的落点</span></div>'
      + '<p class="dh-sub">' + esc(d.chapter) + '</p></div>'
      + '<div class="drawer-body"><p class="src-label">原文</p>'
      + '<p class="current"><mark>' + esc(d.current) + '</mark></p>'
      + d.ctx.map((p) => '<p class="ctx">' + esc(p) + '</p>').join('')
      + '</div>'
      + '<div class="drawer-foot"><span class="path">' + esc(d.path) + '</span>'
      + '<button type="button" id="copy-path">复制路径</button></div>';
  }

  /* ---- 历史会话 -------------------------------------------------- */

  function histRow(row, current) {
    const books = row.books.length ? '《' + row.books.join('》《') + '》' : '';
    return '<button class="hist-row' + (current ? ' is-current' : '') + '" type="button"'
      + ' data-hist="' + row.id + '">'
      + '<span class="top"><span>' + esc(row.when) + '</span>'
      + '<span>' + row.turns + ' 问' + (current ? ' <span class="pin">当前</span>' : '') + '</span></span>'
      + '<span class="q">' + esc(row.q) + '</span>'
      + '<span class="meta">' + esc(books) + '</span>'
      + '</button>';
  }

  function renderHistory(filter) {
    const q = String(filter || '').trim().toLowerCase();
    const match = (row) => !q
      || (row.q + ' ' + row.last + ' ' + row.books.join(' ')).toLowerCase().includes(q);
    let html = '';
    if (match(DATA.current)) {
      html += '<p class="hist-group">当前打开</p>' + histRow(DATA.current, true);
    }
    ['今天', '昨天'].forEach((group) => {
      const rows = DATA.history.filter((row) => row.group === group && match(row));
      if (!rows.length) return;
      html += '<p class="hist-group">' + group + '</p>'
        + rows.map((row) => histRow(row, false)).join('');
    });
    if (!html) html = '<p class="hist-empty">没有匹配的会话。</p>';
    if (!q) html += '<button class="hist-more" type="button" id="hist-more">更早的 47 个 ↓</button>';
    $('#hist-list').innerHTML = html;
  }

  /* ---- 页面状态 -------------------------------------------------- */

  function setRail(mode) {
    const current = mode === 'current';
    $('#panel-current').hidden = !current;
    $('#panel-history').hidden = current;
    $('#tab-current').classList.toggle('is-on', current);
    $('#tab-history').classList.toggle('is-on', !current);
    $('#tab-current').setAttribute('aria-selected', String(current));
    $('#tab-history').setAttribute('aria-selected', String(!current));
    $('#count-current').textContent = state.screen === 'start' ? '0' : '3';
    $('#sub-current').textContent = state.screen === 'start'
      ? '还没有开始'
      : '3 问 · 正在看第 2 问';
  }

  function updateReadout(name) {
    const text = {
      running: '检索中 12.4s',
      answering: '检索 0.3s · 回答中 12.4s',
      done: '检索 0.3s · 回答 39.2s',
      open: '检索 0.3s · 回答 39.2s',
      history: '检索 0.3s · 回答 39.2s',
      playing: '检索中 …',
      start: '',
    };
    $('#readout').textContent = text[name] || '';
  }

  function updateTurnStatus(name) {
    const el = $('#turn-status');
    const text = (name === 'running' || name === 'playing') ? '检索中'
      : (name === 'answering' ? '回答中' : '');
    if (!text) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    el.innerHTML = text + '<i class="p-caret"></i>';
  }

  function updateAsk(name) {
    const start = name === 'start';
    $('#hint').innerHTML = start ? ''
      : '<span class="on">下一问挂在 <span class="cite">[1]</span>'
        + '<span class="qt">其最著名的，即是…</span> · '
        + '<button class="x" type="button">取消</button></span>';
    $('#q').placeholder = start ? '问一句，从书里找原文' : '就 [1] 这一段接着问';
  }

  function syncButtons() {
    $$('[data-screen-btn]').forEach((btn) => {
      btn.classList.toggle('is-on', btn.dataset.screenBtn === state.screen);
    });
    $$('[data-variant-btn]').forEach((btn) => {
      btn.classList.toggle('is-on', btn.dataset.variantBtn === state.variant);
    });
  }

  function clearTimer() {
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
  }

  function resetPlayButton() {
    const btn = $('#play');
    btn.disabled = false;
    btn.textContent = '▶ 演示进度';
  }

  function setScreen(name) {
    clearTimer();
    if (state.playing) {
      state.playing = false;
      resetPlayButton();
    }
    state.screen = name;
    document.body.dataset.screen = name;
    setRail(name === 'start' || name === 'history' ? 'history' : 'current');

    state.stageStates = name === 'running'
      ? ['done', 'active', 'pending', 'pending', 'pending']
      : name === 'answering'
        ? ['done', 'done', 'done', 'done', 'active']
        : ALL_DONE.slice();
    state.openRows = new Set();
    if (name === 'running') state.openRows.add(1);
    if (name === 'answering') state.openRows.add(4);
    if (name === 'open') state.openRows.add(0);

    setStackOpen(name === 'running' || name === 'answering' || name === 'open');
    paintStages();
    updateReadout(name);
    updateTurnStatus(name);
    updateAsk(name);
    syncButtons();
    status(VARIANT_LABELS[state.variant]);
  }

  function setVariant(variant) {
    state.variant = variant;
    document.body.dataset.variant = variant;
    renderStack();
    paintStages();
    syncButtons();
    status(VARIANT_LABELS[variant]);
  }

  /* ---- 进度演示 -------------------------------------------------- */

  function play() {
    if (state.playing) return;
    clearTimer();
    state.playing = true;
    state.screen = 'playing';
    document.body.dataset.screen = 'playing';
    setRail('current');
    setStackOpen(true);
    updateAsk('playing');
    updateReadout('playing');
    updateTurnStatus('playing');
    const btn = $('#play');
    btn.disabled = true;
    btn.textContent = '演示中…';
    let i = 0;

    const tick = () => {
      const states = ALL_PENDING.slice();
      for (let k = 0; k < i; k += 1) states[k] = 'done';
      if (i < 5) states[i] = 'active';
      state.stageStates = states;
      state.openRows = new Set(i < 5 ? [i] : []);
      paintStages();
      if (i >= 5) {
        state.playing = false;
        resetPlayButton();
        state.screen = 'done';
        document.body.dataset.screen = 'done';
        state.stageStates = ALL_DONE.slice();
        state.openRows = new Set();
        setStackOpen(false);
        paintStages();
        updateReadout('done');
        updateTurnStatus('done');
        updateAsk('done');
        syncButtons();
        status('5 个阶段全部完成 · 圆点静止');
        return;
      }
      status(pad(i + 1) + ' ' + STAGE_NAMES[i] + ' · 进行中');
      i += 1;
      state.timer = setTimeout(tick, i === 5 ? 1600 : 1100);
    };
    tick();
  }

  /* ---- 事件 ------------------------------------------------------ */

  $('#tab-current').addEventListener('click', () => setRail('current'));
  $('#tab-history').addEventListener('click', () => setRail('history'));
  $('#hist-filter').addEventListener('input', (ev) => renderHistory(ev.target.value));

  $('#hist-list').addEventListener('click', (ev) => {
    if (ev.target.closest('#hist-more')) {
      toast('原型：载入更早的 47 个会话');
      return;
    }
    const row = ev.target.closest('[data-hist]');
    if (!row) return;
    if (row.dataset.hist === DATA.current.id) {
      setScreen('done');
      toast('原型：打开当前会话');
      return;
    }
    toast('原型：打开会话 ' + row.dataset.hist);
  });

  $('#stages').addEventListener('click', (ev) => {
    const strip = ev.target.closest('#strip-toggle');
    if (strip) {
      setStackOpen(!$('#stages').classList.contains('is-open'));
      return;
    }
    const head = ev.target.closest('.stage-row-head');
    if (!head) return;
    const row = head.closest('.stage-row');
    if (row.classList.contains('is-pending')) return;
    const i = Number(row.dataset.i);
    if (state.openRows.has(i)) state.openRows.delete(i);
    else state.openRows.add(i);
    paintStages();
  });

  $('#evidences').addEventListener('click', (ev) => {
    const cite = ev.target.closest('.ev');
    if (!cite) return;
    if (Number(cite.dataset.n) === 1) toast('右栏已经是 [1]');
    else toast('原型：右栏只放了 [1] 的原文');
  });

  $$('[data-screen-btn]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const name = btn.dataset.screenBtn;
      if (name === 'done' && state.playing) {
        clearTimer();
        state.playing = false;
        resetPlayButton();
      }
      setScreen(name);
    });
  });

  $$('[data-variant-btn]').forEach((btn) => {
    btn.addEventListener('click', () => setVariant(btn.dataset.variantBtn));
  });

  $('#play').addEventListener('click', play);
  $('#new-session').addEventListener('click', () => setScreen('start'));
  $('#last-session').addEventListener('click', () => setScreen('done'));
  $('#brand').addEventListener('click', (ev) => {
    ev.preventDefault();
    setScreen('start');
  });

  document.addEventListener('click', (ev) => {
    if (ev.target.closest('#copy-path')) toast('原型：路径已复制（未接剪贴板）');
    if (ev.target.closest('.ask-hint .x')) toast('原型：取消落点');
    const sample = ev.target.closest('[data-ask]');
    if (sample) toast('原型：' + sample.dataset.ask);
  });

  /* ---- 启动 ------------------------------------------------------ */

  $('#stages').innerHTML = stripHTML() + '<div class="stage-stack" id="stage-stack"></div>';
  renderStack();
  renderBooks();
  renderAnchor();
  renderAnswer();
  renderEvidences();
  renderDrawer();
  renderHistory('');
  setScreen('start');
})();
