(function () {
  const parts = location.pathname.split('/').filter(Boolean);
  const UID = decodeURIComponent(parts[1] || '');
  const N = parts[2] || '01';

  const urlParams = new URLSearchParams(location.search);
  const hashBid = (location.hash || '').startsWith('#blk-') ? location.hash.slice(5) : '';
  const blocksParam = (urlParams.get('blocks') || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  let arrival = {
    from: (urlParams.get('from') || '').trim(),
    query: (urlParams.get('query') || '').trim(),
    hl: (urlParams.get('hl') || '').trim(),
    blockIds: blocksParam.length ? blocksParam : (hashBid ? [hashBid] : []),
    dismissed: false,
  };

  let data = null;
  let showMap = false;
  let showTerms = false;
  let showModelBox = false;
  let drawerOpen = false;
  let hidePins = false;
  let currentSelection = { block_ids: [], text: '' };
  let lastResult = null;
  let abortCtrl = null;

  function buildSearchHref(queryText, bookOnly) {
    const targetParams = new URLSearchParams();
    if (queryText) targetParams.set('q', queryText);
    if (bookOnly) targetParams.set('books', UID);
    targetParams.set('from', 'reading');
    targetParams.set('mode', 'trace');
    return '/go/search?to=' + encodeURIComponent('/?' + targetParams.toString());
  }

  function clipSearchProbe(text) {
    const clean = String(text || '').replace(/\s+/g, ' ').trim();
    if (clean.length <= 120) return clean;
    const cut = clean.slice(0, 120);
    const punct = Math.max(cut.lastIndexOf('。'), cut.lastIndexOf('，'), cut.lastIndexOf('；'), cut.lastIndexOf(' '));
    return (punct >= 48 ? cut.slice(0, punct) : cut).trim();
  }

  function highlightEscaped(text, probe) {
    const raw = String(text || '');
    if (!probe || arrival.dismissed) return RD.esc(raw);
    const cleanProbe = probe.trim();
    if (!cleanProbe) return RD.esc(raw);

    const candidates = [cleanProbe];
    if (cleanProbe.length > 32) candidates.push(cleanProbe.slice(0, 32));
    if (cleanProbe.length > 16) candidates.push(cleanProbe.slice(0, 16));

    for (const cand of candidates) {
      const idx = raw.indexOf(cand);
      if (idx >= 0) {
        return (
          RD.esc(raw.slice(0, idx)) +
          '<mark class="hl-hit">' + RD.esc(raw.slice(idx, idx + cand.length)) + '</mark>' +
          RD.esc(raw.slice(idx + cand.length))
        );
      }
    }
    return RD.esc(raw);
  }

  function arrivalBlockText() {
    const blocks = (data && data.blocks) || [];
    if (!arrival.blockIds.length) return arrival.hl || '';
    const picked = blocks.filter((b) => arrival.blockIds.includes(b.id));
    return picked.map((b) => b.text || '').filter(Boolean).join('\n') || arrival.hl || '';
  }

  function arrivalActive() {
    return !arrival.dismissed && Boolean(arrival.from || arrival.query || arrival.hl || arrival.blockIds.length);
  }

  function renderArrivalBanner() {
    const el = RD.mount('arrival-banner');
    if (!el) return;
    if (!arrivalActive()) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    const sourceLabel = arrival.from === 'daily' ? '来自往期日课' : '来自书库检索';
    const bidLabel = arrival.blockIds.length ? arrival.blockIds.join(', ') : '';
    const primaryBid = arrival.blockIds[0] || '';
    el.innerHTML =
      '<div class="arrival-row">' +
        '<div class="arrival-info">' +
          '<span class="arrival-badge">' + RD.esc(sourceLabel) + '</span>' +
          (arrival.query ? '<span class="arrival-query">原问题：「' + RD.esc(arrival.query) + '」</span>' : '') +
          (bidLabel && primaryBid
            ? '<button type="button" class="wb-btn arrival-loc" id="btn-arrival-focus">定位到 ' + RD.esc(bidLabel) + '</button>'
            : '') +
        '</div>' +
        '<div class="arrival-actions">' +
          '<button type="button" class="wb-btn primary" id="btn-arrival-explain">解释这段</button>' +
          (arrival.query
            ? '<button type="button" class="wb-btn" id="btn-arrival-ask">带原问题追问</button>'
            : '') +
          (arrival.from === 'search'
            ? '<a class="wb-btn" href="' + buildSearchHref(arrival.query, false) + '" target="_blank" rel="noopener">去书库重搜 ↗</a>'
            : '') +
          '<button type="button" class="wb-btn" id="btn-arrival-close" aria-label="收起提示">×</button>' +
        '</div>' +
      '</div>';

    const focusBtn = RD.mount('btn-arrival-focus');
    if (focusBtn && primaryBid) {
      focusBtn.addEventListener('click', () => focusBlock(primaryBid));
    }
    const explainBtn = RD.mount('btn-arrival-explain');
    if (explainBtn) {
      explainBtn.addEventListener('click', () => {
        const ids = arrival.blockIds.length ? arrival.blockIds : (data.blocks && data.blocks[0] ? [data.blocks[0].id] : []);
        currentSelection = { block_ids: ids, text: arrivalBlockText() };
        runAssist('explain', '');
      });
    }
    const askBtn = RD.mount('btn-arrival-ask');
    if (askBtn) {
      askBtn.addEventListener('click', () => {
        const ids = arrival.blockIds.length ? arrival.blockIds : (data.blocks && data.blocks[0] ? [data.blocks[0].id] : []);
        currentSelection = { block_ids: ids, text: arrivalBlockText() };
        setDrawerOpen(true);
        RD.mount('drawer-empty').hidden = true;
        RD.mount('drawer-quote').hidden = false;
        RD.mount('drawer-quote').textContent = currentSelection.text || ('段落 ' + ids.join(', '));
        const qEl = RD.mount('drawer-ask-input');
        if (qEl) {
          qEl.value = arrival.query;
          qEl.focus();
        }
      });
    }
    const closeBtn = RD.mount('btn-arrival-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        arrival.dismissed = true;
        renderArrivalBanner();
        renderBlocks();
      });
    }
  }

  function renderShell() {
    const book = data.book;
    const ch = data.chapter;
    const pinCount = (data.assist_notes || []).length;
    const mapReady = Boolean(data.chapter_map && data.chapter_map.supported !== false);
    const blockCount = (data.blocks || []).length;

    RD.mount('crumb').innerHTML =
      '<a href="/">书架</a><i>/</i>' +
      '<a href="' + RD.bookHref(book.uid) + '">' + RD.esc(book.title_short || book.title) + '</a><i>/</i>' +
      '<span>第 ' + RD.esc(ch.n) + ' 章</span>';
    document.title = ch.title + ' · ' + (book.title_short || book.title);

    RD.mount('wb-root').innerHTML =
      '<section class="wb-head">' +
        '<div><div class="eyebrow">' + RD.esc(book.title) + (book.subject ? ' · ' + RD.esc(book.subject) : '') + '</div>' +
        '<h1>' + RD.esc(ch.title) + '</h1>' +
        '<div class="wb-sub">' + RD.fmt(ch.chars) + ' 字 · ' + RD.fmt(blockCount) + ' 个原文块 · 约 ' + RD.fmt(ch.minutes) + ' 分钟 · 100% 完整正文直出</div></div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
          '<a class="btn btn-ghost" href="' + buildSearchHref('', true) + '" target="_blank" rel="noopener">本书内检索 ↗</a>' +
          '<a class="btn btn-ghost" href="/records?uid=' + encodeURIComponent(UID) + '">本章日志 →</a>' +
        '</div>' +
      '</section>' +

      '<div class="wb-top-drawer arrival-banner" id="arrival-banner" hidden></div>' +

      '<div class="wb-toolbar" id="wb-toolbar">' +
        '<button type="button" class="wb-btn" id="btn-map" aria-pressed="' + showMap + '">知识地图' + (mapReady ? '（已缓存）' : '') + '</button>' +
        '<button type="button" class="wb-btn" id="btn-terms" aria-pressed="' + showTerms + '">核心术语</button>' +
        '<span class="wb-sep"></span>' +
        '<button type="button" class="wb-btn" id="btn-toggle-pins" aria-pressed="' + (!hidePins) + '">旁批 <b id="pin-count">' + pinCount + '</b></button>' +
        '<button type="button" class="wb-btn" id="btn-model-box" aria-pressed="' + showModelBox + '">模型设置</button>' +
        '<span class="wb-spacer"></span>' +
        '<span class="wb-sub" style="font-size:11.5px">提示：拖选任意正文或点段尾 ✦ 唤起伴读或反查书库</span>' +
        '<button type="button" class="wb-btn" id="btn-drawer" aria-pressed="' + drawerOpen + '">伴读抽屉</button>' +
      '</div>' +

      '<div class="wb-top-drawer" id="model-box" hidden>' + RD.modelSettingsHtml() + '</div>' +
      '<div class="wb-top-drawer" id="map-drawer" hidden></div>' +

      '<div class="wb-grid' + (drawerOpen ? ' has-drawer' : '') + '" id="wb-grid">' +
        '<article class="reader-column"><div class="reader-body" id="reader-body"></div></article>' +
        '<aside class="assist-drawer" id="assist-drawer" hidden>' +
          '<div class="drawer-head">' +
            '<h3 id="drawer-title">随文伴读</h3>' +
            '<div style="display:flex;gap:6px">' +
              '<button type="button" class="wb-btn" id="btn-pin" disabled>钉住到段落</button>' +
              '<button type="button" class="wb-btn" id="btn-drawer-close" aria-label="关闭抽屉">×</button>' +
            '</div>' +
          '</div>' +
          '<div class="drawer-body" id="drawer-body">' +
            '<div class="empty" id="drawer-empty">在左侧正文中拖选一段文字，或点段落右上角 <b>✦</b>，即可流式图解、举例、翻译、拆解前提或跨书检索。</div>' +
            '<div class="drawer-quote" id="drawer-quote" hidden></div>' +
            '<div class="drawer-diagram" id="drawer-diagram"></div>' +
            '<div class="drawer-answer" id="drawer-answer"></div>' +
            '<div class="wb-sub" id="drawer-meta" style="margin-top:10px;font-size:11.5px"></div>' +
          '</div>' +
          '<div class="drawer-foot">' +
            '<form class="drawer-ask" id="drawer-ask-form">' +
              '<input id="drawer-ask-input" placeholder="针对所选段落继续追问…" aria-label="追问内容">' +
              '<button type="submit" class="wb-btn primary">追问</button>' +
            '</form>' +
          '</div>' +
        '</aside>' +
      '</div>';

    RD.bindModelSettings();
    renderArrivalBanner();
  }

  function renderBlockHtml(blk) {
    const kind = blk.kind || 'paragraph';
    const text = blk.text || '';
    const isHit = arrivalActive() && arrival.blockIds.includes(blk.id);
    const probe = isHit ? arrival.hl : '';
    if (kind === 'heading') {
      const lvl = Math.min(4, Math.max(1, Number(blk.level || 2)));
      return '<h' + lvl + '>' + highlightEscaped(text, probe) + '</h' + lvl + '>';
    }
    if (kind === 'quote') return '<blockquote>' + highlightEscaped(text, probe) + '</blockquote>';
    if (kind === 'code') return '<pre><code>' + highlightEscaped(text, probe) + '</code></pre>';
    if (kind === 'list') {
      const tag = blk.ordered ? 'ol' : 'ul';
      const items = (blk.items || []).map((x) => '<li>' + highlightEscaped(x, probe) + '</li>').join('');
      return '<' + tag + '>' + items + '</' + tag + '>';
    }
    if (kind === 'table') {
      const head = (blk.head || []).length
        ? '<thead><tr>' + blk.head.map((c) => '<th>' + highlightEscaped(c, probe) + '</th>').join('') + '</tr></thead>' : '';
      const rows = (blk.rows || []).map((r) => '<tr>' + (r || []).map((c) => '<td>' + highlightEscaped(c, probe) + '</td>').join('') + '</tr>').join('');
      return '<table>' + head + '<tbody>' + rows + '</tbody></table>';
    }
    if (kind === 'image') {
      const rel = blk.rel || '';
      const src = rel ? '/api/asset?uid=' + encodeURIComponent(UID) + '&rel=' + encodeURIComponent(rel) : '';
      return src
        ? '<figure><img src="' + src + '" alt="' + RD.esc(text || '插图') + '">' +
          (text ? '<figcaption class="wb-sub">' + RD.esc(text) + '</figcaption>' : '') + '</figure>'
        : '<p class="wb-sub">[图片] ' + RD.esc(text) + '</p>';
    }
    if (kind === 'footnote') return '<p class="wb-sub">' + highlightEscaped(text, probe) + '</p>';
    return '<p>' + highlightEscaped(text, probe) + '</p>';
  }

  function renderBlocks() {
    const blocks = data.blocks || [];
    const notesByAnchor = new Map();
    for (const note of (data.assist_notes || [])) {
      const ids = note.block_ids || [];
      const anchor = ids[ids.length - 1] || (blocks[0] && blocks[0].id);
      if (!anchor) continue;
      if (!notesByAnchor.has(anchor)) notesByAnchor.set(anchor, []);
      notesByAnchor.get(anchor).push(note);
    }

    RD.mount('reader-body').innerHTML = blocks.map((blk) => {
      const bid = blk.id || '';
      const isHit = arrivalActive() && arrival.blockIds.includes(bid);
      const pinned = notesByAnchor.get(bid) || [];
      const pinsHtml = pinned.length
        ? '<div class="pinned-notes">' + pinned.map((note) =>
            '<div class="pin-card" data-note-id="' + RD.esc(note.id) + '">' +
              '<div class="pin-card-head">' +
                '<b>' + RD.esc(RD.actionNames[note.action] || note.action || '旁批') +
                  (note.title ? ' · ' + RD.esc(note.title) : '') + '</b>' +
                '<span>' + RD.esc((note.block_ids || []).join(', ')) +
                  (note.stale ? ' · 原文已变' : '') + '</span>' +
                '<button type="button" class="pin-del" data-del-pin="' + RD.esc(note.id) + '">移除</button>' +
              '</div>' +
              (note.diagram_html ? '<div>' + note.diagram_html + '</div>' : '') +
              (note.content ? '<div class="drawer-answer">' + RD.renderMarkdown(note.content) + '</div>' : '') +
            '</div>'
          ).join('') + '</div>'
        : '';
      return '<div class="reader-block' + (isHit ? ' hit' : '') + '" id="blk-' + RD.esc(bid) + '" data-block="' + RD.esc(bid) + '" data-kind="' + RD.esc(blk.kind || 'paragraph') + '">' +
        renderBlockHtml(blk) +
        '<button type="button" class="blk-trigger" data-trigger="' + RD.esc(bid) + '" title="对第 ' + RD.esc(bid) + ' 段伴读">✦</button>' +
        pinsHtml +
      '</div>';
    }).join('');

    const countEl = RD.mount('pin-count');
    if (countEl) countEl.textContent = String((data.assist_notes || []).length);
  }

  function renderMapDrawer() {
    const box = RD.mount('map-drawer');
    if (!showMap && !showTerms) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const cmap = data.chapter_map;
    const gate = (data.preflight && data.preflight.chapter_map) || { supported: true, reason: '' };

    if (!cmap) {
      if (!gate.supported) {
        box.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">' +
            '<div><b>无需强行展开导图</b> · <span class="wb-sub">' + RD.esc(gate.reason) + '</span></div>' +
            '<button type="button" class="wb-btn" id="btn-gen-map-force">仍要展开简图（调用模型）</button>' +
          '</div>';
      } else {
        box.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">' +
            '<div><b>按需生成全章知识地图与术语</b> · <span class="wb-sub">点击后一次性提取结构节拍与核心概念，并缓存供后续复用。</span></div>' +
            '<button type="button" class="wb-btn primary" id="btn-gen-map">生成知识地图 →</button>' +
          '</div>';
      }
      bindMapButtons();
      return;
    }

    if (cmap.supported === false) {
      box.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">' +
          '<div><b>直读建议</b> · <span class="wb-sub">' + RD.esc(cmap.reason) + '</span></div>' +
          '<button type="button" class="wb-btn" id="btn-gen-map-force">仍要展开简图</button>' +
        '</div>';
      bindMapButtons();
      return;
    }

    let html = '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<div><b>全章知识地图</b> · <span class="wb-sub">' +
        RD.fmt((cmap.sections || []).length) + ' 节 · ' +
        RD.fmt((cmap.terms || []).length) + ' 个术语' +
        (cmap.stale ? ' · 原文已更新，建议重生成' : '') +
      '</span></div>' +
      '<button type="button" class="wb-btn" id="btn-gen-map-force">重新生成</button>' +
    '</div>';

    if (showMap && (cmap.sections || []).length) {
      html += '<div class="map-sections">' + cmap.sections.map((sec) =>
        '<div class="map-card"><h4>' + RD.esc(sec.title) + '</h4>' +
        (sec.summary ? '<p>' + RD.esc(sec.summary) + '</p>' : '') +
        '<div class="map-nodes">' + (sec.nodes || []).map((node) => {
          const target = (node.block_ids || sec.block_ids || [])[0] || '';
          return '<button type="button" class="map-node" data-jump="' + RD.esc(target) + '">' +
            RD.esc(node.summary || node.id) +
            (target ? ' <span class="wb-sub">[' + RD.esc(target) + ']</span>' : '') + '</button>';
        }).join('') + '</div></div>'
      ).join('') + '</div>';
    }
    if (showTerms) {
      const terms = cmap.terms || [];
      html += terms.length
        ? '<div class="term-pills">' + terms.map((t) =>
            '<button type="button" class="term-pill" data-jump="' + RD.esc(t.source_block || '') + '">' +
              '<b>' + RD.esc(t.term) + '</b>：' + RD.esc(t.definition) +
            '</button>'
          ).join('') + '</div>'
        : '<p class="wb-sub" style="margin:10px 0 0">本章未提取出独立术语。</p>';
    }
    if (showMap && (cmap.figures || []).length) {
      html += cmap.figures.map((fig) => fig.html || '').join('');
    }
    box.innerHTML = html;
    bindMapButtons();
  }

  function bindMapButtons() {
    const genBtn = RD.mount('btn-gen-map');
    if (genBtn) genBtn.addEventListener('click', () => requestChapterMap(false));
    const forceBtn = RD.mount('btn-gen-map-force');
    if (forceBtn) forceBtn.addEventListener('click', () => requestChapterMap(true));
    RD.mount('map-drawer').querySelectorAll('[data-jump]').forEach((el) => {
      el.addEventListener('click', () => {
        const bid = el.getAttribute('data-jump');
        if (bid) focusBlock(bid);
      });
    });
  }

  async function requestChapterMap(force) {
    const settings = RD.modelSettings();
    const box = RD.mount('map-drawer');
    box.innerHTML = '<div class="wb-sub">正在生成全章知识地图与术语…</div>';
    try {
      const res = await RD.api('/api/chapter-map/' + encodeURIComponent(UID) + '/' + encodeURIComponent(N), {
        method: 'POST',
        body: { force: Boolean(force), model: settings.model, effort: settings.effort },
      });
      data.chapter_map = res;
      if (res.supported === false) {
        RD.toast(res.reason || '篇幅较短，建议直读原文');
      } else {
        showMap = true;
        RD.toast('知识地图已就绪');
      }
      renderShell();
      renderMapDrawer();
      renderBlocks();
      renderSelectionBarButtons();
      bindEvents();
    } catch (err) {
      RD.toast(err.message, true);
      renderMapDrawer();
    }
  }

  function renderSelectionBarButtons() {
    const bar = RD.mount('selection-bar');
    const toolbar = (data && data.toolbar) || [
      { action: 'diagram', label: '图解此段' },
      { action: 'explain', label: '讲人话举例' },
      { action: 'translate', label: '翻译原意' },
      { action: 'critique', label: '拆解前提' },
      { action: 'ask', label: '问这句' },
    ];
    bar.innerHTML =
      toolbar.map((item) =>
        '<button type="button" data-action="' + RD.esc(item.action) + '">' + RD.esc(item.label) + '</button>'
      ).join('') +
      '<span class="selection-bar-sep" aria-hidden="true"></span>' +
      '<button type="button" data-action="search-book" title="在当前这本书内按段落检索选中内容">本书查这段</button>' +
      '<button type="button" data-action="search-all" title="在全库范围按段落检索选中内容">全库查这段 ↗</button>';
  }

  function setDrawerOpen(open) {
    drawerOpen = Boolean(open);
    const drawer = RD.mount('assist-drawer');
    const grid = RD.mount('wb-grid');
    const btn = RD.mount('btn-drawer');
    if (drawer) drawer.hidden = !drawerOpen;
    if (grid) grid.classList.toggle('has-drawer', drawerOpen);
    if (btn) btn.setAttribute('aria-pressed', String(drawerOpen));
  }

  function focusBlock(bid) {
    const el = document.getElementById('blk-' + bid);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 2000);
  }

  function jumpToHash() {
    const h = (location.hash || '').replace(/^#/, '');
    if (h.startsWith('blk-')) focusBlock(h.slice(4));
  }

  function blockOfNode(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el ? el.closest('.reader-block[data-block]') : null;
  }

  function captureSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return null;
    const text = sel.toString().trim();
    if (!text) return null;
    const reader = RD.mount('reader-body');
    if (!reader) return null;
    const range = sel.getRangeAt(0);
    const startBlk = blockOfNode(range.startContainer);
    const endBlk = blockOfNode(range.endContainer);
    if (!startBlk && !endBlk) return null;
    const all = Array.from(reader.querySelectorAll('.reader-block[data-block]'));
    const i1 = startBlk ? all.indexOf(startBlk) : 0;
    const i2 = endBlk ? all.indexOf(endBlk) : i1;
    const lo = Math.min(i1, i2);
    const hi = Math.max(i1, i2);
    const ids = all.slice(lo, hi + 1).map((el) => el.getAttribute('data-block')).filter(Boolean);
    const rect = range.getBoundingClientRect();
    return { block_ids: ids, text, rect };
  }

  function positionSelectionBar(rect) {
    const bar = RD.mount('selection-bar');
    bar.classList.add('show');
    const top = Math.max(66, rect.top - 48);
    const left = Math.min(window.innerWidth - bar.offsetWidth - 16, Math.max(12, rect.left));
    bar.style.top = top + 'px';
    bar.style.left = left + 'px';
  }

  function hideSelectionBar() {
    RD.mount('selection-bar').classList.remove('show');
  }

  function bindEvents() {
    RD.mount('btn-map').addEventListener('click', () => {
      showMap = !showMap;
      RD.mount('btn-map').setAttribute('aria-pressed', String(showMap));
      renderMapDrawer();
    });
    RD.mount('btn-terms').addEventListener('click', () => {
      showTerms = !showTerms;
      RD.mount('btn-terms').setAttribute('aria-pressed', String(showTerms));
      if (showTerms) showMap = true;
      renderMapDrawer();
    });
    RD.mount('btn-toggle-pins').addEventListener('click', () => {
      hidePins = !hidePins;
      document.body.classList.toggle('hide-pins', hidePins);
      RD.mount('btn-toggle-pins').setAttribute('aria-pressed', String(!hidePins));
    });
    RD.mount('btn-model-box').addEventListener('click', () => {
      showModelBox = !showModelBox;
      RD.mount('model-box').hidden = !showModelBox;
      RD.mount('btn-model-box').setAttribute('aria-pressed', String(showModelBox));
    });
    RD.mount('btn-drawer').addEventListener('click', () => setDrawerOpen(!drawerOpen));
    RD.mount('btn-drawer-close').addEventListener('click', () => setDrawerOpen(false));
    RD.mount('btn-pin').addEventListener('click', pinCurrentResult);

    RD.mount('drawer-ask-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = RD.mount('drawer-ask-input');
      const q = (input.value || '').trim();
      if (!q) return;
      if (!currentSelection.block_ids.length && data.blocks && data.blocks.length) {
        currentSelection = { block_ids: [data.blocks[0].id], text: data.blocks[0].text || '' };
      }
      input.value = '';
      runAssist('ask', q);
    });

    const reader = RD.mount('reader-body');
    reader.addEventListener('mouseup', () => {
      setTimeout(() => {
        const cap = captureSelection();
        if (!cap) return;
        currentSelection = { block_ids: cap.block_ids, text: cap.text };
        positionSelectionBar(cap.rect);
      }, 10);
    });

    reader.addEventListener('click', async (e) => {
      const delBtn = e.target.closest('[data-del-pin]');
      if (delBtn) {
        e.stopPropagation();
        const noteId = delBtn.getAttribute('data-del-pin');
        await deletePin(noteId);
        return;
      }
      const trig = e.target.closest('[data-trigger]');
      if (trig) {
        e.stopPropagation();
        const bid = trig.getAttribute('data-trigger');
        const blk = (data.blocks || []).find((b) => b.id === bid);
        currentSelection = { block_ids: [bid], text: (blk && blk.text) || '' };
        reader.querySelectorAll('.reader-block.active').forEach((n) => n.classList.remove('active'));
        trig.closest('.reader-block').classList.add('active');
        positionSelectionBar(trig.getBoundingClientRect());
      }
    });

    document.addEventListener('mousedown', (e) => {
      const bar = RD.mount('selection-bar');
      if (bar.contains(e.target) || e.target.closest('[data-trigger]')) return;
      if (!reader.contains(e.target)) hideSelectionBar();
      else setTimeout(() => { if (!window.getSelection() || window.getSelection().isCollapsed) hideSelectionBar(); }, 10);
    });

    RD.mount('selection-bar').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      const action = btn.getAttribute('data-action');
      hideSelectionBar();
      if (action === 'search-book' || action === 'search-all') {
        const probe = clipSearchProbe(currentSelection.text);
        if (!probe) {
          RD.toast('请先选中一段有效正文', true);
          return;
        }
        window.open(buildSearchHref(probe, action === 'search-book'), '_blank', 'noopener');
        return;
      }
      if (action === 'ask') {
        setDrawerOpen(true);
        const qEl = RD.mount('drawer-ask-input');
        if (qEl) { qEl.focus(); qEl.placeholder = '输入想问这段话的问题…'; }
        RD.mount('drawer-empty').hidden = true;
        RD.mount('drawer-quote').hidden = false;
        RD.mount('drawer-quote').textContent = currentSelection.text || ('段落 ' + currentSelection.block_ids.join(', '));
        return;
      }
      runAssist(action, '');
    });
  }

  async function runAssist(action, question) {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const settings = RD.modelSettings();
    lastResult = null;
    setDrawerOpen(true);
    RD.mount('btn-pin').disabled = true;
    RD.mount('drawer-empty').hidden = true;
    RD.mount('drawer-title').textContent = (RD.actionNames[action] || action) +
      (currentSelection.block_ids.length ? ' · ' + currentSelection.block_ids.join('-') : '');
    RD.mount('drawer-quote').hidden = false;
    RD.mount('drawer-quote').textContent = currentSelection.text || ('段落 ' + currentSelection.block_ids.join(', '));
    RD.mount('drawer-diagram').innerHTML = '';
    RD.mount('drawer-answer').innerHTML = '<span class="wb-sub">正在思考与生成…</span>';
    RD.mount('drawer-meta').textContent = '';

    let streamBuf = '';
    try {
      const res = await fetch('/api/assist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        signal: abortCtrl.signal,
        body: JSON.stringify({
          uid: UID,
          n: N,
          action,
          block_ids: currentSelection.block_ids,
          selected_text: currentSelection.text,
          question,
          model: settings.model,
          effort: settings.effort,
          stream: true,
        }),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error || ('请求失败：' + res.status));
      }
      const ctype = res.headers.get('Content-Type') || '';
      if (!ctype.includes('text/event-stream')) {
        const json = await res.json();
        applyDoneResult(json);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let rest = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        rest += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = rest.indexOf('\n\n')) >= 0) {
          const frame = rest.slice(0, idx);
          rest = rest.slice(idx + 2);
          let evName = 'message';
          let dataLine = '';
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) evName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLine += line.slice(5).trim();
          }
          if (!dataLine) continue;
          const payload = JSON.parse(dataLine);
          if (evName === 'delta') {
            streamBuf += payload.text || '';
            RD.mount('drawer-answer').innerHTML = RD.renderMarkdown(streamBuf);
          } else if (evName === 'diagram') {
            RD.mount('drawer-diagram').innerHTML = payload.html || '';
          } else if (evName === 'done') {
            applyDoneResult(payload);
          } else if (evName === 'error') {
            throw new Error(payload.error || '伴读调用失败');
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      RD.mount('drawer-answer').innerHTML = '<p class="job-error">' + RD.esc(err.message) + '</p>';
      RD.toast(err.message, true);
    }
  }

  function applyDoneResult(payload) {
    lastResult = payload;
    if (payload.diagram_html) {
      RD.mount('drawer-diagram').innerHTML = payload.diagram_html;
    }
    if (payload.content) {
      RD.mount('drawer-answer').innerHTML = RD.renderMarkdown(payload.content);
    }
    const metaParts = [];
    if (payload.model) metaParts.push(payload.model);
    if (payload.tokens) metaParts.push(RD.fmt(payload.tokens) + ' tokens');
    if (payload.elapsed_ms) metaParts.push(RD.elapsed(payload.elapsed_ms));
    RD.mount('drawer-meta').textContent = metaParts.join(' · ');
    RD.mount('btn-pin').disabled = false;
  }

  async function pinCurrentResult() {
    if (!lastResult) return;
    const btn = RD.mount('btn-pin');
    btn.disabled = true;
    try {
      const title = (lastResult.figure && lastResult.figure.caption) || '';
      const saved = await RD.api('/api/assist/pin', {
        method: 'POST',
        body: {
          uid: UID,
          n: N,
          action: lastResult.action,
          title,
          block_ids: lastResult.block_ids && lastResult.block_ids.length ? lastResult.block_ids : currentSelection.block_ids,
          selected_text: lastResult.selected_text || currentSelection.text,
          question: lastResult.question || '',
          content: lastResult.content || '',
          diagram_html: lastResult.diagram_html || '',
        },
      });
      data.assist_notes = (data.assist_notes || []).concat([saved]);
      renderBlocks();
      RD.toast('已钉住到正文旁');
      focusBlock(saved.block_ids[saved.block_ids.length - 1]);
    } catch (err) {
      RD.toast(err.message, true);
      btn.disabled = false;
    }
  }

  async function deletePin(noteId) {
    try {
      await RD.api('/api/assist/pin/' + encodeURIComponent(UID) + '/' + encodeURIComponent(N) + '/' + encodeURIComponent(noteId), {
        method: 'DELETE',
      });
      data.assist_notes = (data.assist_notes || []).filter((n) => n.id !== noteId);
      renderBlocks();
      RD.toast('已移除旁批');
    } catch (err) {
      RD.toast(err.message, true);
    }
  }

  RD.ready(async () => {
    RD.night();
    try {
      data = await RD.api('/api/chapter/' + encodeURIComponent(UID) + '/' + encodeURIComponent(N));
    } catch (err) {
      RD.fail(RD.mount('wb-root'), err.message);
      return;
    }
    renderShell();
    renderMapDrawer();
    renderBlocks();
    renderSelectionBarButtons();
    bindEvents();
    jumpToHash();
    window.addEventListener('hashchange', jumpToHash);
  });
})();
