(() => {
  'use strict';

  const STAGE_NAMES = ['理解问题', '词法循环', '打分与选章', '书卡', '结果'];
  const KIND = {
    noun: '名词集',
    full: '全词集',
    'noun+concept': '名词 + 同义组',
    'full+broad': '全词 + 宽词组',
    'drop-generic': '去掉最泛的词',
  };
  const WHY_NEXT = {
    noun: '换名词集',
    full: '补回动作词',
    'noun+concept': '换同义组',
    'full+broad': '加宽词组',
    'drop-generic': '去掉最泛的词',
  };
  const SOURCE = { model: '模型切词', cache: '缓存', degraded: '降级' };
  const SOURCE_CLASS = { model: 'model', cache: 'cache', degraded: 'degraded' };
  const THINKING = { off: '关', low: '低', medium: '中', high: '高' };
  const THINKING_LEVELS = ['off', 'low', 'medium', 'high'];
  const THINKING_DEFAULT = 'high';
  const THINKING_KEY = 'booksearch.thinking';

  const state = {
    trace: null,
    health: null,
    thinking: THINKING_DEFAULT,
    answerFresh: false,
    instant: false,
    expandAll: false,
    selectAll: false,
    selected: 0,
    result: null,
    locator: null,
    answer: null,
    answerSeq: 0,
    timers: [],
    stage: 0,
    playing: false,
    busy: false,
    runSeq: 0,
    noScroll: false,
    scrollHit: false,
    open: new Set(),
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const stages = $$('.stage');
  const railItems = $$('.rail li');

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtInt(n) {
    return String(n == null ? '' : n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function fmtMs(ms) {
    const n = Number(ms);
    if (!isFinite(n) || n < 0) return '—';
    if (n < 10) return n.toFixed(1) + ' ms';
    if (n < 1000) return Math.round(n) + ' ms';
    return (n / 1000).toFixed(1) + ' s';
  }

  function pct(x) {
    const n = Number(x);
    if (!isFinite(n)) return '—';
    return (n * 100).toFixed(1) + '%';
  }

  function fixed(x, digits = 2) {
    const n = Number(x);
    return isFinite(n) ? n.toFixed(digits) : '—';
  }

  function normalizeThinking(value) {
    const v = String(value || '').trim().toLowerCase();
    return THINKING_LEVELS.indexOf(v) >= 0 ? v : THINKING_DEFAULT;
  }

  function thinkingLabel(value) {
    const level = normalizeThinking(value);
    return THINKING[level] || level;
  }

  function savedThinking() {
    try {
      const v = localStorage.getItem(THINKING_KEY);
      if (v) return normalizeThinking(v);
    } catch (err) { /* 隐私模式下 localStorage 可能不可用 */ }
    return THINKING_DEFAULT;
  }

  function renderThinking() {
    $$('.think-opt').forEach((el) => {
      const on = el.dataset.level === state.thinking;
      el.classList.toggle('on', on);
      el.setAttribute('aria-pressed', String(on));
    });
    const sel = $('#think-select');
    if (sel && sel.value !== state.thinking) sel.value = state.thinking;
  }

  function setThinking(value) {
    state.thinking = normalizeThinking(value);
    try { localStorage.setItem(THINKING_KEY, state.thinking); } catch (err) { /* ignore */ }
    renderThinking();
  }

  function stageMs(trace) {
    const s = (trace && trace.stage_ms) || {};
    return [s.understand, s.rounds, s.rank, s.cards, s.result];
  }

  /* 第 5 阶段 = 读原文窗口 + 回答。读窗口那半跟着屏幕上这段原文走，回答那半等它落地才加上。 */
  function resultStageMs() {
    if (!state.result) return null;
    const read = Number(state.result.ms) || 0;
    const a = state.answer;
    const ans = (a && !a.loading && a.data) ? (Number(a.data.ms) || 0) : 0;
    return read + ans;
  }

  function renderResultMs() {
    const value = resultStageMs();
    const text = value == null ? '—' : fmtMs(value);
    const msEl = $('.stage-ms', stages[4]);
    if (msEl) msEl.textContent = text;
    const li = railItems[4];
    if (li) $('.rail-ms', li).textContent = text;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (!res.ok || !data || data.ok === false) {
      const msg = (data && data.error) || ('HTTP ' + res.status);
      throw new Error(msg);
    }
    return data;
  }

  function termChip(term, dropped) {
    if (dropped) {
      return `<span class="term dropped"><span class="t">${esc(term)}</span>` +
        `<span class="tag">动作词</span></span>`;
    }
    return `<span class="term">${esc(term)}</span>`;
  }

  function kindLabel(kind) {
    return KIND[kind] || kind || '换词';
  }

  function stopLabel(trace) {
    const p = trace.plan || {};
    const best = p.best_round || 0;
    const map = {
      C: 'C 到线，停在第 ' + best + ' 轮',
      'A+B': 'A+B 到线，停在第 ' + best + ' 轮',
      cap: '跑满 5 轮，取第 ' + best + ' 轮',
      exhausted: '词集用尽，取第 ' + best + ' 轮',
    };
    return map[p.stop] || ('取第 ' + (best || 1) + ' 轮');
  }

  function roundWhy(round) {
    const n = (round.groups || []).length;
    const cover = round.cover_paras;
    const parts = [];
    if (round.C) {
      parts.push(`C 成立：top1 书里有 ${cover} 段同时覆盖 ${n} 个组`);
    } else {
      if (round.A) {
        parts.push(`A 成立：top1 书覆盖全部 ${n} 组，且至少两段各覆盖 ${Math.min(2, n)} 组`);
      } else {
        parts.push(`A 不成立：top1 书命中段没有覆盖全部 ${n} 组`);
      }
      parts.push(`B ${round.B ? '成立' : '不成立'}：分差 ${pct(round.margin)}（门槛 10%）`);
    }
    return parts.join('；');
  }

  function highlight(text, terms) {
    let html = esc(text || '');
    const seen = new Set();
    let count = 0;
    for (const raw of terms) {
      const term = String(raw || '');
      if (term.length < 2 || seen.has(term)) continue;
      seen.add(term);
      if (count >= 20) break;
      const needle = esc(term);
      if (!needle) continue;
      const parts = html.split(needle);
      if (parts.length === 1) continue;
      const take = Math.min(parts.length - 1, 20 - count);
      let out = parts[0];
      for (let i = 1; i < parts.length; i++) {
        if (i <= take) {
          out += '<mark>' + needle + '</mark>' + parts[i];
          count++;
        } else {
          out += needle + parts[i];
        }
      }
      html = out;
    }
    return { html, count };
  }

  function highlightTerms(trace) {
    const ex = (trace.extract && trace.extract.terms) || [];
    const plan = trace.plan || {};
    return [...new Set([...(ex || []), ...(plan.nouns || []), ...(plan.terms || [])])];
  }

  function setStat(key, value) {
    const el = $(`[data-stat="${key}"]`);
    if (el) el.textContent = value;
  }

  async function loadHealth() {
    try {
      const data = await fetchJSON('/api/health');
      state.health = data.meta || {};
      setStat('books', fmtInt(state.health.books));
      setStat('paras', fmtInt(state.health.paras));
      setStat('chapters', fmtInt(state.health.chapters));
    } catch (err) {
      setStat('books', '—');
      setStat('paras', '—');
      setStat('chapters', '—');
    }
  }

  /* ---- render stages -------------------------------------------- */

  function renderHeadBadge(article, trace) {
    const head = $('.stage-head', article);
    const old = $('.badge', head);
    if (old) old.remove();
    if (article.dataset.stage !== '0') return;
    const ex = trace.extract || {};
    const src = ex.source || 'degraded';
    const badge = document.createElement('span');
    badge.className = 'badge ' + (SOURCE_CLASS[src] || 'degraded');
    badge.textContent = SOURCE[src] || src;
    if (ex.note) badge.title = ex.note;
    head.appendChild(badge);
  }

  function renderStage0(trace) {
    const ex = trace.extract || {};
    const plan = trace.plan || {};
    const dropped = new Set(plan.action_dropped || []);
    const terms = ex.terms || [];
    const notes = [];
    notes.push(terms.length + ' 个检索词');
    if (ex.source === 'cache') notes.push('命中切词缓存');
    if (ex.source === 'degraded') notes.push('模型不可用，按概念表包含匹配');
    if (dropped.size) notes.push(dropped.size + ' 个动作词已排除');
    if (ex.note) notes.push(ex.note);
    const chips = terms.length
      ? terms.map((t) => termChip(t, dropped.has(t))).join('')
      : '<span class="summary-sub">没有检索词</span>';
    $('.stage-body', stages[0]).innerHTML =
      `<p class="stage-note">${esc(notes.join(' · '))}</p><div class="term-row">${chips}</div>`;
    $('.stage-summary', stages[0]).innerHTML =
      `<p class="summary-line">${terms.length} 个检索词 · 思考 ${thinkingLabel(trace.thinking)} · ${SOURCE[ex.source] || '降级'} · ${fmtMs(ex.ms)}</p>` +
      `<div class="summary-evidence">${terms.map((t) => `<span class="mini-term">${esc(t)}</span>`).join('')}</div>`;
  }

  function renderRound(round, adopted) {
    const flags = ['A', 'B', 'C'].map((k) =>
      `<span class="flag ${round[k] ? 'on' : ''}" title="${k} 判据">${k}</span>`).join('');
    const terms = (round.terms || []).map((t) => `<span class="mini-term">${esc(t)}</span>`).join('');
    return `<div class="round ${adopted ? 'adopted' : ''}">
      ${adopted ? '<span class="round-tag">' + (round.stop ? '采用' : '取这轮') + '</span>' : ''}
      <div class="round-head">
        <span class="round-n mono">${String(round.n).padStart(2, '0')}</span>
        <span class="round-kind">${esc(kindLabel(round.kind))}</span>
        <span class="round-ms mono">${fmtMs(round.ms)}</span>
      </div>
      <div class="round-terms">${terms}</div>
      <div class="round-stats">
        <div><span>命中段</span><b>${fmtInt(round.hits)}</b></div>
        <div><span>覆盖段</span><b>${fmtInt(round.cover_paras)}</b></div>
        <div><span>top1</span><b>${fixed(round.top1_score)}</b></div>
        <div><span>top2</span><b>${fixed(round.top2_score)}</b></div>
        <div><span>分差</span><b>${pct(round.margin)}</b></div>
        <div><span>停止</span><b>${round.stop ? '是' : '否'}</b></div>
      </div>
      <p class="round-top1" title="${esc(round.top1)}">top1 ${esc(round.top1 || '未命中')}</p>
      <div class="round-flags">${flags}</div>
      <p class="round-why">${esc(roundWhy(round))}</p>
    </div>`;
  }

  function renderStage1(trace) {
    const plan = trace.plan || {};
    const rounds = plan.rounds || [];
    if (!rounds.length) {
      $('.stage-body', stages[1]).innerHTML = '<p class="empty">这一轮没有检索词，未产生词法循环。</p>';
      $('.stage-summary', stages[1]).innerHTML = '<p class="summary-line">没有轮次</p>';
      return;
    }
    const pieces = [];
    rounds.forEach((r, i) => {
      pieces.push(renderRound(r, r.n === plan.best_round));
      if (i < rounds.length - 1) {
        const next = rounds[i + 1];
        pieces.push(`<div class="round-arrow"><span class="why">${esc(WHY_NEXT[next.kind] || '换词')}</span><span class="arrow">→</span></div>`);
      }
    });
    $('.stage-body', stages[1]).innerHTML =
      `<p class="stage-note">${esc(stopLabel(trace))} · 判据：A 覆盖全部组且至少两段合格；B 前两名分差 ≥10%；C 至少 3 段覆盖全部组。</p>` +
      `<div class="round-row">${pieces.join('')}</div>`;
    $('.stage-summary', stages[1]).innerHTML =
      `<p class="summary-line">${rounds.length} 轮 · ${esc(stopLabel(trace))}</p>` +
      `<div class="summary-evidence">${rounds.map((r) =>
        `<span class="mini-term">${esc(kindLabel(r.kind))} · ${fmtInt(r.hits)} 段${r.n === plan.best_round ? ' · 采用' : ''}</span>`).join('')}</div>`;
  }

  function renderStage2(trace) {
    const plan = trace.plan || {};
    const ranking = plan.ranking || [];
    const chapters = plan.chapters || [];
    const nGroups = (plan.nouns || []).length || 1;
    if (!ranking.length) {
      $('.stage-body', stages[2]).innerHTML =
        '<p class="empty">没找到：这个问题在书库里没有命中。换一种说法，或去掉太泛的词。</p>';
      $('.stage-summary', stages[2]).innerHTML = '<p class="summary-line">没有命中</p>';
      return;
    }
    const top = Number(ranking[0].score) || 0;
    const rows = ranking.map((b, i) => {
      const cov = Math.round((Number(b.coverage) || 0) * nGroups);
      const width = top > 0 ? Math.max(2, Math.round((Number(b.score) / top) * 100)) : 0;
      return `<div class="rank-row">
        <div class="rank-top">
          <span class="rank-n mono">${String(i + 1).padStart(2, '0')}</span>
          <button type="button" class="rank-name" title="${esc(b.title)}">${esc(b.title)}</button>
          <span class="rank-score mono">${fixed(b.score)}</span>
        </div>
        <div class="rank-meta"><span>${esc(b.type)}</span><span class="mono">${fmtInt(b.chapters)} 章</span><span class="cov">${cov}/${nGroups} 组</span></div>
        <div class="bar"><i data-w="${width}%"></i></div>
        <p class="rank-text">${esc(b.best_text || '')}</p>
      </div>`;
    }).join('');
    const chap = chapters.map((c) => {
      const sel = (c.selected || []).map((s) =>
        `<div class="chapter-row"><span class="name">${esc(s.chapter_title)}</span><span class="score">${fixed(s.score)} · L${s.line}</span></div>`).join('');
      return `<div class="chapter-book"><h3>${esc(c.title)}</h3>${sel || '<p class="summary-sub">没有选到章</p>'}</div>`;
    }).join('');
    $('.stage-body', stages[2]).innerHTML =
      `<p class="stage-note">书级打分 ${esc(plan.scheme || 'best_support')} · 分数相对第一名 · 覆盖组按名词集计</p>` +
      `<div class="rank-grid"><div><p class="panel-title">书级榜</p>${rows}</div>` +
      `<div><p class="panel-title">选章</p>${chap || '<p class="empty">没有选到章</p>'}</div></div>`;
    $('.stage-summary', stages[2]).innerHTML =
      `<p class="summary-line">top1 ${esc(ranking[0].title)} · ${fixed(ranking[0].score)} 分 · 取 ${chapters.length} 本书的章</p>` +
      `<div class="summary-evidence">${ranking.slice(0, 3).map((b, i) =>
        `<span class="mini-term">${i + 1}. ${esc(b.title)}</span>`).join('')}</div>`;
  }

  function reasonText(reason, u) {
    const hints = (state.trace && state.trace.cards && state.trace.cards.hints) || {};
    if (reason === 'margin') return `前两名分差 ${pct(u.margin)}，小于 10%`;
    if (reason === 'types') return `前十名跨 ${u.types || 0} 个类型`;
    if (reason === 'long') return `前十名里有 ${u.long10 || 0} 本 50 章以上的长书`;
    if (reason === 'concept') {
      return hints.concept ? '概念型开放问题' : '问句型开放问题';
    }
    return reason;
  }

  function renderStage3(trace) {
    const cardsData = trace.cards || {};
    const cards = cardsData.cards || [];
    const reasons = cardsData.reasons || [];
    const u = cardsData.uncertainty || {};
    const hints = cardsData.hints || {};
    const all = cardsSelected();
    const signals = reasons.map((r) =>
      `<span class="signal">${esc(reasonText(r, u))}</span>`).join('');
    let body = '';
    if (cardsData.trigger && cards.length) {
      const toggle = cards.length > 1
        ? `<button type="button" class="select-all ${all ? 'on' : ''}" id="select-all" aria-pressed="${all}">` +
          `${all ? '已全选 ' + cards.length + ' 本 · 取消' : '全选 ' + cards.length + ' 本'}</button>`
        : '';
      body += `<p class="trigger-line"><span>触发书卡</span>${signals}${toggle}</p>`;
      body += '<div class="card-grid">' + cards.map((c, i) => {
        const on = all || i === state.selected;
        const tags = (c.matched_groups || []).map((g) => `<span class="tag-mini">${esc(g)}</span>`).join('');
        return `<button type="button" class="book-card ${on ? 'selected' : ''}" data-card="${i}" aria-pressed="${on}">
          <span class="check">${on ? (all ? '✓' : '✓ 已选') : ''}</span>
          <h3>${esc(c.title)}</h3>
          <div class="card-meta"><span>${esc(c.type)}</span><span class="mono">${fmtInt(c.chapters)} 章</span><span class="mono">#${c.rank}</span></div>
          <p class="card-chapter">${esc(c.best_chapter_title || '')}</p>
          <p class="card-snippet">${esc(c.snippet || '')}</p>
          <div class="card-tags">${tags}</div>
        </button>`;
      }).join('') + '</div>';
      if (all) {
        body += `<p class="select-all-note">第 5 阶段会用这 ${cards.length} 本书一起回答这个问题</p>`;
      }
    } else {
      let why = '没有命中，无法选书';
      if ((trace.plan && trace.plan.ranking || []).length) {
        if (hints.title) why = '书名精确命中，直接给章节';
        else if (hints.author) why = '作者精确命中，直接给章节';
        else why = '结果足够明确，直接给章节';
      }
      body += `<p class="trigger-line">未触发书卡 · ${esc(why)}</p>` +
        '<p class="no-cards">跳过选择，第 5 阶段直接给出最佳章节。</p>';
    }
    $('.stage-body', stages[3]).innerHTML = body;
    $('.stage-summary', stages[3]).innerHTML =
      `<p class="summary-line">${cardsData.trigger ? cards.length + ' 张书卡' : '未触发书卡'} · ${fmtMs(cardsData.ms)}</p>` +
      `<div class="summary-evidence">${(cards.length ? cards.map((c) =>
        `<span class="mini-term">${esc(c.title)}</span>`) : ['直接给章节']).join('')}</div>`;
  }

  function resultHTML(result) {
    const terms = highlightTerms(state.trace || {});
    const text = result.text || '';
    const hs = Number(result.hit_start);
    const he = Number(result.hit_end);
    let marked;
    let body;
    if (isFinite(hs) && isFinite(he) && he > hs && he <= text.length) {
      let preText = text.slice(0, hs);
      let postText = text.slice(he);
      if (preText.endsWith('\n')) preText = preText.slice(0, -1);
      if (postText.startsWith('\n')) postText = postText.slice(1);
      const before = highlight(preText, terms);
      const mid = highlight(text.slice(hs, he), terms);
      const after = highlight(postText, terms);
      marked = { count: before.count + mid.count + after.count };
      body = before.html +
        '<span class="hit-para">' + (mid.html || esc(text.slice(hs, he))) + '</span>' +
        after.html;
    } else {
      marked = highlight(text, terms);
      body = marked.html;
    }
    const books = ((state.trace || {}).cards || {}).next || [];
    let select = '';
    if (books.length > 1) {
      const opts = books.map((b, i) =>
        `<option value="${i}" ${b.book === result.book ? 'selected' : ''}>${esc(b.title)}</option>`).join('');
      select = `<label class="result-actions">换一本 <select id="book-switch">${opts}</select></label>`;
    }
    const total = Number(result.total_lines) || 0;
    const windowChars = Number(result.window_chars) || text.length;
    const hitLine = Number(result.hit_line) || Number(result.line) || 1;
    const windowNote = total
      ? `本章 ${fmtInt(total)} 行 · 载入 ${fmtInt(windowChars)} 字 · 命中第 ${hitLine} 行`
      : `载入 ${fmtInt(windowChars)} 字 · 命中第 ${hitLine} 行`;
    return `<div class="result-head">
        <div>
          <p class="result-book">${esc(result.book_title || result.book || '')}</p>
          <p class="result-chapter">${esc(result.chapter || result.title || '')}</p>
        </div>
        ${select}
      </div>
      <p class="result-text">${body}</p>
      <div class="result-foot">
        <code>${esc(result.path)}:${hitLine}</code>
        <span class="mono">${marked.count ? marked.count + ' 处高亮 · ' : ''}${windowNote}</span>
      </div>`;
  }

  function chapterNameOf(path) {
    const name = String(path || '').split('/').pop() || '';
    return name.replace(/^\d+[_、.\- ]*/, '').replace(/\.md$/, '');
  }

  function confidenceText(value) {
    return { high: '高', medium: '中', low: '低' }[value] || value || '';
  }

  function citesHTML(data) {
    const list = (data && data.citations) || [];
    if (!list.length) return '';
    const books = new Set(list.map((c) => c.book || c.book_title).filter(Boolean));
    const rows = list.map((c, i) => {
      const line = Number(c.line) || 1;
      const chapter = c.chapter || chapterNameOf(c.path);
      const bookLabel = c.book_title
        ? `<span class="cite-book">${esc(c.book_title)}</span>`
        : '';
      const bookAttr = c.book ? ` data-book="${esc(c.book)}"` : '';
      const titleAttr = c.book_title ? ` data-book-title="${esc(c.book_title)}"` : '';
      return `<li>
        <button type="button" class="cite" data-path="${esc(c.path)}" data-line="${line}"
                data-chapter="${esc(chapter)}" data-i="${i}"${bookAttr}${titleAttr}>
          ${bookLabel}
          <span class="cite-head">
            <span class="cite-chapter">${esc(chapter)}</span>
            <span class="cite-line mono">第 ${line} 行</span>
          </span>
          <span class="cite-quote">${esc(c.quote || '')}</span>
        </button>
      </li>`;
    }).join('');
    const covered = (data && data.mode === 'multi') ? `，覆盖 ${books.size} 本书` : '';
    return `<section class="cites">
      <p class="cites-note">依据 ${list.length} 条${covered} · 点一条载入那段原文</p>
      <ol class="cite-list">${rows}</ol>
    </section>`;
  }

  function answerHTML() {
    const a = state.answer;
    if (!a) return '';
    if (a.loading) {
      const n = ((a.locator && a.locator.sources) || []).length;
      const note = n > 1 ? '先取 ' + n + ' 本书的多段原文，再逐书归纳' : '先取同书多段原文，再归纳';
      const line = n > 1
        ? `正在检索 ${n} 本书的原文并组织回答，最长约 2 分钟…`
        : '正在检索原文并组织回答，最长约 90 秒…';
      return `<section class="answer-card is-loading">
        <div class="answer-head">
          <span class="answer-label">回答</span>
          <span class="answer-note">${esc(note)}</span>
        </div>
        <p class="answer-loading"><span class="dot-pulse"></span>${esc(line)}</p>
      </section>`;
    }
    if (a.error) {
      return `<section class="answer-card is-error">
        <div class="answer-head"><span class="answer-label">回答</span></div>
        <p class="answer-error">没有拿到回答：${esc(a.error)}</p>
        <button type="button" class="link-btn answer-retry">重试</button>
      </section>`;
    }
    const d = a.data || {};
    const multi = d.mode === 'multi';
    const scope = multi ? (d.sources || []) : [];
    const covered = new Set(((d.citations || []).map((c) => c.book || c.book_title)).filter(Boolean)).size;
    // 正文按换行拆段：空行与单换行各成一段。
    const paragraphs = (text) => String(text || '').split(/\n+/).map((s) => s.trim()).filter(Boolean);
    let body;
    if (Array.isArray(d.sections) && d.sections.length) {
      body = [
        d.lead ? `<p class="answer-lead">${esc(d.lead)}</p>` : '',
        d.sections
          .filter((s) => s && (s.heading || s.body))
          .map((s) => {
            const head = s.heading ? `<h3 class="answer-h">${esc(s.heading)}</h3>` : '';
            const ps = paragraphs(s.body).map((p) => `<p>${esc(p)}</p>`).join('');
            return `<section class="answer-section">${head}${ps}</section>`;
          }).join(''),
        d.dissent ? `<p class="answer-dissent">${esc(d.dissent)}</p>` : '',
        d.gap ? `<p class="answer-gap">${esc(d.gap)}</p>` : '',
      ].join('');
    } else {
      const paras = paragraphs(d.answer);
      body = paras.length
        ? paras.map((p) => `<p>${esc(p)}</p>`).join('')
        : '';
    }
    if (!body) body = '<p class="answer-empty">未找到原文依据</p>';
    const meta = [];
    if (d.confidence) meta.push('置信 ' + confidenceText(d.confidence));
    if (multi) meta.push(scope.length + ' 本书');
    if (d.not_found) meta.push('未找到原文依据');
    if (d.error) meta.push(d.error);
    const verified = ((d.citations || []).filter((c) => c.verified)).length;
    if (d.passages && d.passages.length) meta.push(d.passages.length + ' 段原文');
    if (verified) meta.push(verified + ' 条引文已回查');
    if (multi && covered) meta.push('覆盖 ' + covered + ' 本');
    const note = d.not_found ? '未找到可引用的原文'
      : (verified ? '由模型根据原文归纳，引文已回查' : '引文回查不足，仅供参考');
    if (d.cached) meta.push('缓存命中');
    if (d.ms != null) meta.push(fmtMs(d.ms));
    if (d.model) meta.push(d.model);
    if (d.thinking) meta.push('思考 ' + thinkingLabel(d.thinking));
    return `<section class="answer-card ${d.not_found ? 'is-empty' : ''}">
      <div class="answer-head">
        <span class="answer-label">回答</span>
        <span class="answer-note">${note}</span>
        <button type="button" class="fresh-btn answer-fresh"
                title="跳过缓存，重新检索并生成新回答">重新检索</button>
      </div>
      <div class="answer-text">${body}</div>
      <p class="answer-meta mono">${esc(meta.join(' · '))}</p>
    </section>` + citesHTML(d);
  }

  function stage4HTML() {
    const result = state.result;
    if (result && result.error) {
      return '<p class="empty">读取原文失败：' + esc(result.error) + '</p>';
    }
    if (!result || !result.path) {
      return '<p class="empty">没找到：这句话没有命中任何书。可以换个更具体的词再试。</p>';
    }
    return answerHTML() + resultHTML(result);
  }

  function renderStage4(trace) {
    const result = state.result || trace.result;
    state.result = result;
    $('.stage-body', stages[4]).innerHTML = stage4HTML();
    renderResultMs();
  }

  /* ---- answer ---------------------------------------------------- */

  function locatorFromResult(result) {
    if (!result || !result.path) return null;
    return {
      book: result.book || '',
      book_title: result.book_title || result.title || '',
      chapter: result.chapter || result.title || '',
      path: result.path,
      line: result.hit_line || result.line || 1,
      card: result.card || 0,
    };
  }

  function cardsSelected() {
    const cards = ((state.trace || {}).cards || {}).cards || [];
    return state.selectAll && cards.length > 1;
  }

  function allSources() {
    const cards = ((state.trace || {}).cards || {}).cards || [];
    if (!cardsSelected()) return [];
    const seed = (state.locator && state.locator.book) || '';
    const list = cards.map((c) => ({
      book: c.book || '',
      title: c.title || '',
      path: c.best_chapter || '',
      line: Number(c.line) || 1,
    })).filter((s) => s.path);
    const at = list.findIndex((s) => s.book === seed);
    if (at > 0) list.unshift(list.splice(at, 1)[0]);
    return list.slice(0, 8);
  }

  function answerLocator() {
    const trace = state.trace;
    const loc = state.locator;
    if (!trace || !loc || !loc.path) return null;
    const ex = (trace.extract && trace.extract.terms) || [];
    const plan = trace.plan || {};
    const terms = [...new Set([...(ex || []), ...(plan.nouns || [])])].slice(0, 12);
    const out = {
      question: trace.question || '',
      book: loc.book || '',
      path: loc.path,
      line: loc.line || 1,
      terms,
      groups: (trace.extract && trace.extract.groups) || [],
    };
    const sources = allSources();
    if (sources.length > 1) out.sources = sources;
    return out;
  }

  function locatorKey(loc) {
    if (!loc) return '';
    const sig = (loc.sources || []).map((s) => [s.book, s.path, s.line].join(',')).join(';');
    return [loc.question, loc.book, loc.path, loc.line, sig, state.thinking].join('|');
  }

  function cancelAnswer() {
    state.answerSeq++;
    if (state.answer && state.answer.controller) {
      try { state.answer.controller.abort(); } catch (err) { /* ignore */ }
    }
    state.answer = null;
  }

  async function startAnswer(loc, opts = {}) {
    if (!loc || !loc.path || !loc.question) return;
    let fresh = opts.fresh;
    if (fresh == null) fresh = !!state.answerFresh;
    state.answerFresh = false;
    const key = locatorKey(loc);
    const seq = ++state.answerSeq;
    if (state.answer && state.answer.controller) {
      try { state.answer.controller.abort(); } catch (err) { /* ignore */ }
    }
    const controller = new AbortController();
    state.answer = { key, locator: loc, loading: true, data: null, error: '', controller };
    renderStage4(state.trace);
    try {
      const data = await fetchJSON('/api/answer', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          question: loc.question, book: loc.book, path: loc.path,
          line: loc.line, terms: loc.terms, groups: loc.groups || [],
          sources: loc.sources || [],
          thinking: state.thinking,
          fresh: !!fresh,
        }),
        signal: controller.signal,
      });
      if (seq !== state.answerSeq) return;
      state.answer = { key, locator: loc, loading: false, data, error: '', controller: null };
      renderStage4(state.trace);
      renderRaw(4, state.trace);
      const first = (data.citations || []).find((c) => c.verified && c.path);
      if (first) {
        const base = state.result || {};
        await loadResult({
          book: first.book || base.book || '',
          book_title: first.book_title || base.book_title || '',
          chapter: first.chapter || chapterNameOf(first.path),
          path: first.path,
          line: Number(first.line) || 1,
          card: base.card || 0,
        }, { relocate: false });
        $$('.cite').forEach((el) => {
          const on = el.dataset.path === first.path &&
            Number(el.dataset.line) === Number(first.line);
          el.classList.toggle('active', on);
        });
      }
      if (state.scrollHit) scrollToHit();
    } catch (err) {
      if (seq !== state.answerSeq) return;
      if (err && err.name === 'AbortError') return;
      state.answer = { key, locator: loc, loading: false, data: null,
        error: (err && err.message) || String(err), controller: null };
      renderStage4(state.trace);
      renderRaw(4, state.trace);
    }
  }

  function maybeAnswer() {
    const loc = answerLocator();
    if (!loc) return;
    const key = locatorKey(loc);
    if (state.answer && state.answer.key === key &&
        (state.answer.loading || state.answer.data || state.answer.error)) return;
    startAnswer(loc);
  }

  async function openCitation(btn) {
    const path = btn.dataset.path;
    if (!path) return;
    const base = state.result || (state.trace && state.trace.result) || {};
    $$('.cite').forEach((el) => el.classList.toggle('active', el === btn));
    await loadResult({
      book: btn.dataset.book || base.book || '',
      book_title: btn.dataset.bookTitle || base.book_title || base.title || '',
      chapter: btn.dataset.chapter || '',
      path,
      line: Number(btn.dataset.line) || 1,
      card: base.card || 0,
    }, { relocate: false });
    const line = btn.dataset.line;
    const i = btn.dataset.i;
    $$('.cite').forEach((el) => {
      const on = el.dataset.i === i && el.dataset.line === line && el.dataset.path === path;
      el.classList.toggle('active', on);
    });
  }

  function rawFor(i, trace) {
    if (i === 0) return trace.extract;
    if (i === 1) {
      const p = trace.plan || {};
      return { terms: p.terms, nouns: p.nouns, action_dropped: p.action_dropped, rounds: p.rounds, stop: p.stop, best_round: p.best_round, ms: p.ms };
    }
    if (i === 2) {
      const p = trace.plan || {};
      return { scheme: p.scheme, ranking: p.ranking, chapters: p.chapters };
    }
    if (i === 3) return trace.cards;
    return { result: state.result || trace.result,
             answer: (state.answer && state.answer.data) || null };
  }

  function renderRaw(i, trace) {
    const pre = $('.raw pre', stages[i]);
    if (!pre) return;
    let text = '';
    try { text = JSON.stringify(rawFor(i, trace), null, 1); } catch (err) { text = ''; }
    pre.textContent = text.slice(0, 4096);
  }

  function renderAll() {
    const trace = state.trace;
    if (!trace) return;
    cancelAnswer();
    $('.question-text').textContent = trace.question || '';
    document.title = (trace.question || '书库检索') + ' · 书库检索';
    state.result = trace.result || null;
    state.locator = locatorFromResult(trace.result);
    const ms = stageMs(trace);
    stages.forEach((article, i) => {
      renderHeadBadge(article, trace);
      const msEl = $('.stage-ms', article);
      if (msEl && i < 4) msEl.textContent = fmtMs(ms[i]);
      article.classList.remove('is-open');
      article.dataset.state = 'pending';
    });
    state.open.clear();
    renderStage0(trace);
    renderStage1(trace);
    renderStage2(trace);
    renderStage3(trace);
    renderStage4(trace);
    [0, 1, 2, 3, 4].forEach((i) => renderRaw(i, trace));
    $$('.stage-summary').forEach((el) => { el.hidden = false; });
    $$('.raw').forEach((el) => { el.style.display = ''; });
    railItems.forEach((li) => {
      li.classList.remove('done', 'active');
      const step = Number(li.dataset.step);
      if (step < 4) $('.rail-ms', li).textContent = fmtMs(ms[step]);
    });
    const segs = $('#segments');
    segs.innerHTML = STAGE_NAMES.map(() => '<span class="segment"></span>').join('');
    $('#mobile-label').textContent = '';
    requestAnimationFrame(() => {
      $$('.bar i').forEach((bar) => { bar.style.width = bar.dataset.w || '0%'; });
    });
  }

  /* ---- playback -------------------------------------------------- */

  function clearTimers() {
    state.timers.forEach((t) => clearTimeout(t));
    state.timers = [];
  }

  function stageState(i, value) {
    const article = stages[i];
    if (!article) return;
    article.dataset.state = value;
    if (value === 'active') {
      article.classList.remove('enter');
      void article.offsetWidth;
      article.classList.add('enter');
      article.classList.remove('is-open');
    }
    const li = railItems[i];
    if (li) {
      li.classList.toggle('active', value === 'active');
      li.classList.toggle('done', value === 'done');
    }
    const seg = $('#segments').children[i];
    if (seg) {
      seg.className = 'segment' + (value === 'active' ? ' active' : value === 'done' ? ' done' : '');
    }
    if (value === 'active') {
      const ms = i === 4 ? resultStageMs() : stageMs(state.trace)[i];
      $('#mobile-label').textContent = String(i + 1).padStart(2, '0') + ' ' + STAGE_NAMES[i] + ' · ' + (ms == null ? '—' : fmtMs(ms));
      if (i === stages.length - 1) maybeAnswer();
    }
  }

  function play() {
    clearTimers();
    state.playing = true;
    state.instant = false;
    document.body.classList.remove('instant');
    state.stage = 0;
    stages.forEach((s) => {
      s.dataset.state = 'pending';
      s.classList.remove('is-open');
    });
    state.open.clear();
    railItems.forEach((li) => li.classList.remove('done', 'active'));
    $('#skip-btn').hidden = false;
    $('#replay-btn').hidden = true;
    const ms = stageMs(state.trace);
    let cursor = 0;
    for (let i = 0; i < stages.length; i++) {
      const at = cursor;
      state.timers.push(setTimeout(() => {
        state.stage = i;
        stageState(i, 'active');
      }, at));
      const dwell = Math.min(1800, Math.max(1100, Number(ms[i]) || 0));
      cursor += dwell;
      if (i < stages.length - 1) {
        state.timers.push(setTimeout(() => stageState(i, 'done'), cursor));
      }
    }
    state.timers.push(setTimeout(finishAll, cursor + 30));
  }

  function finishAll() {
    clearTimers();
    state.playing = false;
    const last = stages.length - 1;
    for (let i = 0; i < stages.length; i++) {
      stageState(i, i === last ? 'active' : 'done');
    }
    state.stage = last;
    state.open = new Set([0, 1, 2, 3]);
    [0, 1, 2, 3].forEach((i) => stages[i].classList.add('is-open'));
    $('#skip-btn').hidden = true;
    $('#replay-btn').hidden = false;
    document.body.classList.toggle('instant', state.instant);
    bindSwitch();
    if (state.scrollHit) {
      scrollToHit();
    } else if (!state.noScroll) {
      requestAnimationFrame(() => {
        stages[last].scrollIntoView({ block: 'start' });
      });
    }
  }

  function scrollToHit() {
    const el = $('.hit-para');
    if (el) el.scrollIntoView({ block: 'start' });
  }

  function enterLoading(q) {
    clearTimers();
    state.playing = false;
    $('#hero').hidden = true;
    $('#workspace').hidden = false;
    $('.question-text').textContent = q;
    document.title = q + ' · 书库检索';
    stages.forEach((s) => {
      s.dataset.state = 'pending';
      s.classList.remove('is-open', 'enter');
      $('.stage-body', s).innerHTML = '';
      $('.stage-summary', s).hidden = true;
      $('.stage-ms', s).textContent = '';
      s.querySelectorAll('.raw').forEach((r) => { r.style.display = 'none'; });
    });
    railItems.forEach((li) => li.classList.remove('done', 'active'));
    stages[0].dataset.state = 'active';
    railItems[0].classList.add('active');
    $('.stage-body', stages[0]).innerHTML =
      '<p class="stage-loading"><span class="dot-pulse"></span>正在理解这句话，切成检索词。新问题要调一次模型，约 3 到 5 秒。</p>';
    const segs = $('#segments');
    if (!segs.children.length) {
      segs.innerHTML = STAGE_NAMES.map(() => '<span class="segment"></span>').join('');
    }
    $$('#segments .segment').forEach((seg, i) => {
      seg.className = 'segment' + (i === 0 ? ' active' : '');
    });
    $('#mobile-label').textContent = '01 ' + STAGE_NAMES[0];
  }

  /* ---- selection + chapter --------------------------------------- */

  function markSelected(idx) {
    const all = cardsSelected();
    $$('.book-card').forEach((el, i) => {
      const on = all || i === idx;
      el.classList.toggle('selected', on);
      el.setAttribute('aria-pressed', String(on));
      const check = $('.check', el);
      if (check) check.textContent = on ? (all ? '✓' : '✓ 已选') : '';
    });
  }

  function toggleSelectAll() {
    const cards = ((state.trace || {}).cards || {}).cards || [];
    if (cards.length < 2) return;
    state.selectAll = !state.selectAll;
    renderStage3(state.trace);
    markSelected(state.selected);
    maybeAnswer();
  }

  async function loadResult(locator, opts = {}) {
    let relocate = opts.relocate !== false;
    const q = '?path=' + encodeURIComponent(locator.path) +
      '&line=' + encodeURIComponent(locator.line || 1) + '&chars=900';
    try {
      const data = await fetchJSON('/api/chapter' + q);
      state.result = Object.assign({}, data, {
        book: locator.book || '',
        book_title: locator.book_title || locator.title || '',
        chapter: locator.chapter || data.title || '',
        card: locator.card || 0,
      });
    } catch (err) {
      state.result = {
        path: locator.path, line: locator.line || 1, text: '',
        book_title: locator.book_title || '', chapter: locator.chapter || '',
        error: err.message, ms: 0,
      };
      relocate = false;
    }
    if (relocate && state.result.path && !state.result.error) {
      state.locator = {
        book: locator.book || '',
        book_title: locator.book_title || locator.title || '',
        chapter: locator.chapter || state.result.title || '',
        path: state.result.path,
        line: state.result.hit_line || state.result.line || locator.line || 1,
        card: locator.card || 0,
      };
    }
    renderStage4(state.trace);
    renderRaw(4, state.trace);
    bindSwitch();
  }

  function bindSwitch() {
    const sel = $('#book-switch');
    if (!sel) return;
    sel.addEventListener('change', () => {
      const books = ((state.trace || {}).cards || {}).next || [];
      const b = books[Number(sel.value)];
      if (!b || !b.selected || !b.selected.length) return;
      const s = b.selected[0];
      const idx = ((state.trace.cards || {}).cards || []).findIndex((c) => c.book === b.book);
      if (idx >= 0) {
        state.selected = idx;
        if (state.selectAll) {
          state.selectAll = false;
          renderStage3(state.trace);
        }
        markSelected(idx);
      }
      loadResult({ book: b.book, book_title: b.title, chapter: s.chapter_title, path: s.chapter_path, line: s.line, card: idx + 1 })
        .then(() => startAnswer(answerLocator()));
    });
  }

  async function selectCard(idx, opts = {}) {
    const cards = ((state.trace || {}).cards || {}).cards || [];
    const card = cards[idx];
    if (!card) return;
    state.selected = idx;
    if (state.selectAll) {
      state.selectAll = false;
      renderStage3(state.trace);
    }
    markSelected(idx);
    if (opts.fetch === false) {
      renderStage4(state.trace);
      return;
    }
    await loadResult({
      book: card.book, book_title: card.title, chapter: card.best_chapter_title,
      path: card.best_chapter, line: card.line, card: idx + 1,
    });
    if (opts.answer !== false) await startAnswer(answerLocator());
  }

  /* ---- events ---------------------------------------------------- */

  function bindStageToggle() {
    stages.forEach((article, i) => {
      const head = $('.stage-head', article);
      head.addEventListener('click', () => {
        if (article.dataset.state !== 'done') return;
        if (i === stages.length - 1) return;
        if (state.open.has(i)) {
          state.open.delete(i);
          article.classList.remove('is-open');
        } else {
          state.open.add(i);
          article.classList.add('is-open');
        }
      });
    });
  }

  function bindCardClick() {
    $('.stages').addEventListener('click', (ev) => {
      if (ev.target.closest('#select-all')) {
        toggleSelectAll();
        return;
      }
      const card = ev.target.closest('.book-card');
      if (card) {
        const idx = Number(card.dataset.card);
        if (Number.isFinite(idx)) selectCard(idx);
        return;
      }
      const cite = ev.target.closest('.cite');
      if (cite) {
        openCitation(cite);
        return;
      }
      if (ev.target.closest('.answer-retry')) {
        const loc = (state.answer && state.answer.locator) || answerLocator();
        if (loc) startAnswer(loc, { fresh: true });
        return;
      }
      if (ev.target.closest('.answer-fresh')) {
        const q = (state.trace && state.trace.question) || $('#question').value;
        run(q, { fresh: true });
        return;
      }
      const name = ev.target.closest('.rank-name');
      if (name) {
        const row = name.closest('.rank-row');
        if (row) row.classList.toggle('open');
      }
    });
  }

  function showError(message) {
    let err = $('#hero-error');
    if (!err) {
      err = document.createElement('p');
      err.id = 'hero-error';
      err.className = 'hero-error';
      $('.hero-card').appendChild(err);
    }
    err.textContent = '检索失败：' + message;
    err.hidden = false;
  }

  function hideError() {
    const err = $('#hero-error');
    if (err) err.hidden = true;
  }

  async function run(question, opts = {}) {
    const q = String(question || '').trim();
    if (!q || state.busy) return;
    state.busy = true;
    hideError();
    cancelAnswer();
    const fresh = !!opts.fresh;
    state.answerFresh = fresh;
    const btn = $('#ask-btn');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '检索中';
    const freshBtn = $('#fresh-btn');
    if (freshBtn) freshBtn.disabled = true;
    const thinkSel = $('#think-select');
    if (thinkSel) thinkSel.disabled = true;
    const seq = ++state.runSeq;
    enterLoading(q);
    try {
      const trace = await fetchJSON('/api/run', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question: q, thinking: state.thinking, fresh }),
      });
      if (seq !== state.runSeq) return;
      state.trace = trace;
      state.instant = !!opts.instant;
      state.expandAll = !!opts.expandAll;
      state.noScroll = !!opts.noScroll;
      state.scrollHit = !!opts.scrollHit;
      state.answerFresh = fresh;
      const cards = (trace.cards && trace.cards.cards) || [];
      const want = Number(opts.select) || 0;
      state.selected = (cards.length && want >= 1 && want <= cards.length) ? want - 1 : 0;
      state.selectAll = !!opts.all && cards.length > 1;
      $('#hero').hidden = true;
      $('#workspace').hidden = false;
      document.body.classList.toggle('instant', state.instant);
      renderAll();
      if (state.instant) {
        finishAll();
        if (state.selected > 0 && !state.selectAll) await selectCard(state.selected);
        else bindSwitch();
      } else {
        play();
      }
      const params = new URLSearchParams();
      params.set('q', q);
      if (state.instant) params.set('instant', '1');
      if (state.thinking !== THINKING_DEFAULT) params.set('thinking', state.thinking);
      if (state.selected && cards.length) params.set('select', String(state.selected + 1));
      if (state.selectAll) params.set('all', '1');
      history.replaceState(null, '', '?' + params.toString());
    } catch (err) {
      if (seq !== state.runSeq) return;
      state.answerFresh = false;
      backToHero();
      showError(err.message || String(err));
    } finally {
      state.busy = false;
      btn.disabled = false;
      btn.textContent = old;
      if (freshBtn) freshBtn.disabled = false;
      if (thinkSel) thinkSel.disabled = false;
    }
  }

  function backToHero() {
    clearTimers();
    cancelAnswer();
    state.runSeq++;
    state.trace = null;
    state.answerFresh = false;
    state.selectAll = false;
    state.result = null;
    state.locator = null;
    state.playing = false;
    $('#workspace').hidden = true;
    $('#hero').hidden = false;
    document.body.classList.remove('instant');
    history.replaceState(null, '', location.pathname);
    const input = $('#question');
    input.focus();
    input.select();
  }

  function init() {
    const params = new URLSearchParams(location.search);
    setThinking(params.get('thinking') || savedThinking());
    bindStageToggle();
    bindCardClick();
    railItems.forEach((li) => {
      li.addEventListener('click', () => {
        const target = stages[Number(li.dataset.step)];
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
    $$('.think-opt').forEach((btn) => {
      btn.addEventListener('click', () => setThinking(btn.dataset.level));
    });
    $('#think-select').addEventListener('change', (ev) => setThinking(ev.target.value));
    loadHealth();

    $('#ask-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      run($('#question').value);
    });
    $$('.sample').forEach((btn) => {
      btn.addEventListener('click', () => {
        const q = btn.dataset.q;
        $('#question').value = q;
        run(q);
      });
    });
    $('#back-btn').addEventListener('click', backToHero);
    $('#brand').addEventListener('click', (ev) => {
      ev.preventDefault();
      backToHero();
    });
    $('#replay-btn').addEventListener('click', () => {
      if (!state.trace) return;
      state.answerFresh = false;
      state.selectAll = false;
      state.selected = 0;
      markSelected(0);
      renderStage3(state.trace);
      state.result = state.trace.result;
      state.locator = locatorFromResult(state.trace.result);
      renderStage4(state.trace);
      renderRaw(4, state.trace);
      bindSwitch();
      play();
    });
    $('#skip-btn').addEventListener('click', () => {
      state.instant = false;
      finishAll();
    });
    $('#fresh-btn').addEventListener('click', () => {
      const q = (state.trace && state.trace.question) || $('#question').value;
      run(q, { fresh: true });
    });

    const q = (params.get('q') || '').trim();
    if (q) {
      $('#question').value = q;
      run(q, {
        instant: params.get('instant') === '1',
        select: Number(params.get('select')) || 0,
        all: params.get('all') === '1',
        expandAll: params.get('expand') === 'all',
        noScroll: params.get('top') === '1',
        scrollHit: params.get('hit') === '1',
      });
    } else {
      $('#question').focus();
    }
  }

  init();
})();
