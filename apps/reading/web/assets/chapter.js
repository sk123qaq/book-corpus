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

  const FALLBACK_PROFILES = {
    practical: {
      read_type: 'practical', name: '实用型书', adler_category: '1. 实用型书',
      goal: '锁定「目的 → 手段 → 成立条件」，并转化为可执行动作',
      blueprints: [
        { blueprint: 'flow_swimlane', label: 'SOP 流程图' },
        { blueprint: 'decision_tree', label: '条件决策树' },
        { blueprint: 'quadrant_matrix', label: '优先级四象限' },
      ],
      extensions: [{ action: 'checklist', label: '行动自检清单', cross_search: false }],
    },
    literature: {
      read_type: 'literature', name: '想象文学', adler_category: '2. 想象文学',
      goal: '重沉浸与体验：把握人物关系、情绪张力、舞台调度或意象节奏',
      blueprints: [
        { blueprint: 'tension_curve', label: '叙事张力曲线' },
        { blueprint: 'relation_graph', label: '人物阵营图谱' },
        { blueprint: 'spatial_route', label: '空间动线地图' },
      ],
      extensions: [{ action: 'imagery', label: '意象与舞台词典', cross_search: false }],
    },
    history: {
      read_type: 'history', name: '历史书', adler_category: '3. 历史书',
      goal: '多线并行时空对照、权力结构拆解，以及跨史家视角交叉比对',
      blueprints: [
        { blueprint: 'parallel_timeline', label: '多线平行时间轴' },
        { blueprint: 'power_hierarchy', label: '权力结构树' },
        { blueprint: 'spatial_route', label: '战役疆域拓扑' },
      ],
      extensions: [{ action: 'compare_context', label: '多史家视角对照', cross_search: true }],
    },
    science: {
      read_type: 'science', name: '科学与数学', adler_category: '4. 科学与数学',
      goal: '理清核心问题、公理/实验前提、推导依赖与成立边界',
      blueprints: [
        { blueprint: 'derivation_dag', label: '公理-定理推导图' },
        { blueprint: 'sensitivity_chart', label: '变量敏感度曲线' },
        { blueprint: 'system_flow', label: '机制与装置流图' },
      ],
      extensions: [{ action: 'boundary_check', label: '演进与适用边界', cross_search: false }],
    },
    philosophy: {
      read_type: 'philosophy', name: '哲学书', adler_category: '5. 哲学书',
      goal: '抠紧作者专属词义边界，剥离核心前提 → 推论 → 结论与反驳对象',
      blueprints: [
        { blueprint: 'argument_map', label: '论证逻辑树' },
        { blueprint: 'term_contrast', label: '词义辨析双栏表' },
        { blueprint: 'lineage_dag', label: '批判谱系图' },
      ],
      extensions: [{ action: 'sparring_dossier', label: '学术交锋档案', cross_search: false }],
    },
    social: {
      read_type: 'social', name: '社会科学', adler_category: '6. 社会科学',
      goal: '以议题为中心：厘清混杂术语、多变量因果反馈与跨作者对照',
      blueprints: [
        { blueprint: 'causal_loop', label: '因果反馈回路图' },
        { blueprint: 'concept_matrix', label: '跨学派概念矩阵' },
        { blueprint: 'metric_chart', label: '实证趋势图' },
      ],
      extensions: [{ action: 'method_audit', label: '方法与样本局限', cross_search: true }],
    },
    visual: {
      read_type: 'visual', name: '图册与视觉', adler_category: '补充类（图册与设定集）',
      goal: '建立构图元素、文物/工艺原型与时代流派索引',
      blueprints: [{ blueprint: 'element_matrix', label: '构图元素索引表' }],
      extensions: [{ action: 'craft_notes', label: '工艺流派考据', cross_search: false }],
    },
  };

  function getStoredReadType() {
    try {
      return localStorage.getItem('reading-profile:' + UID) || '';
    } catch (e) {
      return '';
    }
  }

  function setStoredReadType(rt) {
    try {
      const def = (data && data.reading_profile && data.reading_profile.read_type) || '';
      if (!rt || rt === def) localStorage.removeItem('reading-profile:' + UID);
      else localStorage.setItem('reading-profile:' + UID, rt);
    } catch (e) {}
  }

  function activeProfile() {
    const serverProf = (data && data.reading_profile) || FALLBACK_PROFILES.social;
    const override = getStoredReadType();
    if (override && override !== serverProf.read_type && FALLBACK_PROFILES[override]) {
      return Object.assign({}, serverProf, FALLBACK_PROFILES[override], { overridden: true });
    }
    return Object.assign({}, serverProf, { overridden: false });
  }

  function buildSearchHref(queryText, bookOnly) {
    const targetParams = new URLSearchParams();
    if (queryText) targetParams.set('q', queryText);
    if (bookOnly) targetParams.set('books', UID);
    targetParams.set('from', 'reading');
    targetParams.set('mode', 'trace');
    return '/go/search?to=' + encodeURIComponent('/?' + targetParams.toString());
  }

  function buildRelatedHref(queryText, srcPath, srcLine) {
    const targetParams = new URLSearchParams();
    if (queryText) targetParams.set('q', queryText);
    targetParams.set('from', 'reading');
    targetParams.set('mode', 'related');
    targetParams.set('exclude', UID);
    if (srcPath) targetParams.set('src_path', srcPath);
    if (srcLine) targetParams.set('src_line', String(srcLine));
    targetParams.set('threshold', '0.45');
    return '/go/search?to=' + encodeURIComponent('/?' + targetParams.toString());
  }

  function clipSearchProbe(text) {
    const clean = String(text || '').replace(/^[#>\-\*\s]+/, '').replace(/\s+/g, ' ').trim();
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
    const prof = activeProfile();
    const avail = (data.reading_profile && data.reading_profile.available_profiles) ||
      Object.values(FALLBACK_PROFILES).map((p) => ({ read_type: p.read_type, name: p.name, adler_category: p.adler_category }));

    RD.mount('crumb').innerHTML =
      '<a href="/">书架</a><i>/</i>' +
      '<a href="' + RD.bookHref(book.uid) + '">' + RD.esc(book.title_short || book.title) + '</a><i>/</i>' +
      '<span>第 ' + RD.esc(ch.n) + ' 章</span>';
    document.title = ch.title + ' · ' + (book.title_short || book.title);

    const profileOpts = avail.map((item) =>
      '<option value="' + RD.esc(item.read_type) + '"' + (item.read_type === prof.read_type ? ' selected' : '') + '>' +
      RD.esc(item.name) + '（' + RD.esc(item.adler_category) + '）</option>'
    ).join('');

    const hintNote = prof.boundary_hint || prof.sub_kind_hint || '';
    const bpButtons = (prof.blueprints || []).map((bp, idx) =>
      '<button type="button" class="wb-btn bp-rec" data-run-blueprint="' + RD.esc(bp.blueprint) + '" title="' + RD.esc(bp.description || '') + '">' +
      RD.esc(bp.label) + (idx === 0 ? ' <span class="mini-tag">书型置顶</span>' : '') + '</button>'
    ).join('');
    const extButtons = (prof.extensions || []).map((ext) =>
      '<button type="button" class="wb-btn bp-ext" data-run-ext="' + RD.esc(ext.action) + '" title="' + RD.esc(ext.hint || '') + '">' +
      RD.esc(ext.label) + '</button>' +
      (ext.cross_search ? '<button type="button" class="wb-btn" data-cross-book>跨书对照 ↗</button>' : '')
    ).join('');

    RD.mount('wb-root').innerHTML =
      '<section class="wb-head">' +
        '<div><div class="eyebrow-row">' +
          '<span class="eyebrow">' + RD.esc(book.title) + (book.subject ? ' · ' + RD.esc(book.subject) : '') + '</span>' +
          '<span class="profile-control" title="仅影响当前浏览器阅读视角，不修改底层人账">' +
            '<span>阅读视角：</span><select id="profile-select" aria-label="切换阅读视角">' + profileOpts + '</select>' +
          '</span>' +
          (prof.overridden ? '<span class="override-badge">已临时切换</span>' : '') +
        '</div>' +
        '<h1>' + RD.esc(ch.title) + '</h1>' +
        '<div class="wb-sub">' + RD.fmt(ch.chars) + ' 字 · ' + RD.fmt(blockCount) + ' 个原文块 · 约 ' + RD.fmt(ch.minutes) + ' 分钟 · 100% 完整正文直出 · ' + RD.esc(prof.goal || '') + '</div>' +
        (hintNote ? '<div class="boundary-note">' + RD.esc(hintNote) + '</div>' : '') +
        '</div>' +
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

      (bpButtons || extButtons
        ? '<div class="wb-profile-strip" id="wb-profile-strip">' +
            '<span class="wb-profile-strip-lbl">' + RD.esc(prof.name) + '工具：</span>' +
            '<span class="scope-pill" id="strip-scope-pill"></span>' +
            bpButtons + extButtons +
          '</div>'
        : '') +

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
          '<div class="drawer-scope-bar" id="drawer-scope-bar" hidden></div>' +
          '<div class="drawer-body" id="drawer-body">' +
            '<div class="empty" id="drawer-empty">在左侧正文中拖选一段或多段文字，或点段尾 <b>✦</b>；不选段落直接点上方书型工具，则默认针对<b>全章</b>分析。</div>' +
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
    const profSel = RD.mount('profile-select');
    if (profSel) {
      profSel.addEventListener('change', () => {
        setStoredReadType(profSel.value);
        renderShell();
        renderMapDrawer();
        renderBlocks();
        renderSelectionBarButtons();
        bindEvents();
        RD.toast('已切换阅读视角为「' + (FALLBACK_PROFILES[profSel.value] ? FALLBACK_PROFILES[profSel.value].name : profSel.value) + '」');
      });
    }
    updateScopeIndicator();
    renderArrivalBanner();
  }

  function formatSelectionSpan(ids) {
    if (!ids || !ids.length) return '';
    return ids.length === 1 ? ids[0] : (ids[0] + '–' + ids[ids.length - 1] + ' (' + ids.length + '段)');
  }

  function updateScopeIndicator() {
    const pill = RD.mount('strip-scope-pill');
    if (!pill) return;
    const ids = (currentSelection && currentSelection.block_ids) || [];
    const total = (data && data.blocks && data.blocks.length) || 0;
    if (ids.length) {
      pill.className = 'scope-pill is-sel';
      pill.innerHTML = '作用域：已选 ' + RD.esc(formatSelectionSpan(ids)) +
        ' <button type="button" class="scope-reset" id="btn-clear-scope" title="清除选段并切回针对全章">切回全章 ×</button>';
      const clr = RD.mount('btn-clear-scope');
      if (clr) {
        clr.addEventListener('click', (e) => {
          e.stopPropagation();
          clearSelectionScope();
        });
      }
    } else {
      pill.className = 'scope-pill';
      pill.textContent = '作用域：全章（' + RD.fmt(total) + ' 段）';
    }
  }

  function clearSelectionScope() {
    currentSelection = { block_ids: [], text: '' };
    const reader = RD.mount('reader-body');
    if (reader) reader.querySelectorAll('.reader-block.active').forEach((n) => n.classList.remove('active'));
    hideSelectionBar();
    updateScopeIndicator();
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
              (note.figure ? '<div class="pin-figure-mount" data-pin-figure="' + RD.esc(note.id) + '"></div>' : (note.diagram_html ? '<div>' + note.diagram_html + '</div>' : '')) +
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

    if (RD.diagrams) {
      const noteById = new Map((data.assist_notes || []).map((n) => [n.id, n]));
      RD.mount('reader-body').querySelectorAll('[data-pin-figure]').forEach((mountEl) => {
        const note = noteById.get(mountEl.getAttribute('data-pin-figure'));
        if (note && note.figure) {
          RD.diagrams.renderBlueprintCard(mountEl, note.figure, { onFocusBlock: focusBlock, compact: true });
        }
      });
    }

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
      if (RD.diagrams && RD.diagrams.renderChapterMapTree) {
        html += RD.diagrams.renderChapterMapTree(cmap);
      } else {
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
    const prof = activeProfile();
    const bps = prof.blueprints || [];
    const exts = prof.extensions || [];
    const defaultBp = bps[0] || null;
    const toolbar = (data && data.toolbar) || [
      { action: 'diagram', label: '图解此段' },
      { action: 'explain', label: '讲人话举例' },
      { action: 'translate', label: '翻译原意' },
      { action: 'critique', label: '拆解前提' },
      { action: 'ask', label: '问这句' },
    ];

    const otherTools = toolbar.filter((item) => item.action !== 'diagram');
    const bpRow =
      '<div class="selection-bar-row">' +
        '<span class="selection-bar-lbl">' + RD.esc(prof.name) + '：</span>' +
        (defaultBp
          ? '<button type="button" class="sel-bp" data-action="diagram" data-blueprint="' + RD.esc(defaultBp.blueprint) +
              '" title="' + RD.esc(defaultBp.description || '') + '">图解·' + RD.esc(defaultBp.label) + '</button>'
          : '<button type="button" class="sel-bp" data-action="diagram">图解此段</button>') +
        bps.slice(1).map((bp) =>
          '<button type="button" class="sel-bp" data-action="diagram" data-blueprint="' + RD.esc(bp.blueprint) +
          '" title="' + RD.esc(bp.description || '') + '">' + RD.esc(bp.label) + '</button>'
        ).join('') +
        exts.map((ext) =>
          '<button type="button" class="sel-ext" data-action="' + RD.esc(ext.action) +
          '" title="' + RD.esc(ext.hint || '') + '">' + RD.esc(ext.label) + '</button>'
        ).join('') +
      '</div>';

    const commonRow =
      '<div class="selection-bar-row">' +
        '<span class="selection-bar-lbl">细读：</span>' +
        otherTools.map((item) =>
          '<button type="button" data-action="' + RD.esc(item.action) + '">' + RD.esc(item.label) + '</button>'
        ).join('') +
        '<span class="selection-bar-sep" aria-hidden="true"></span>' +
        '<button type="button" data-action="search-related" title="在其他书中找相关内容，并按阈值选书回答">跨书找相关 ↗</button>' +
      '</div>';

    bar.innerHTML = bpRow + commonRow;
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

    const strip = RD.mount('wb-profile-strip');
    if (strip) {
      strip.addEventListener('click', (e) => {
        const activeScope = (currentSelection.block_ids && currentSelection.block_ids.length) ? 'selection' : 'chapter';
        const bpBtn = e.target.closest('[data-run-blueprint]');
        if (bpBtn) {
          runAssist('diagram', '', bpBtn.getAttribute('data-run-blueprint'), activeScope, false);
          return;
        }
        const extBtn = e.target.closest('[data-run-ext]');
        if (extBtn) {
          runAssist(extBtn.getAttribute('data-run-ext'), '', '', activeScope, false);
          return;
        }
        if (e.target.closest('[data-cross-book]')) {
          const fallbackText = (data.chapter && data.chapter.title) || ((data.blocks && data.blocks[0] && data.blocks[0].text) || '');
          const probe = clipSearchProbe((activeScope === 'selection' ? currentSelection.text : '') || fallbackText);
          const firstBid = activeScope === 'selection' ? ((currentSelection.block_ids || [])[0] || '') : '';
          const blk = ((data && data.blocks) || []).find((b) => b.id === firstBid);
          const srcPath = (blk && blk.source_file) ? ('books/' + UID + '/md/' + blk.source_file) : '';
          const srcLine = (blk && blk.start_line) ? blk.start_line : 0;
          window.open(buildRelatedHref(probe, srcPath, srcLine), '_blank', 'noopener');
        }
      });
    }

    RD.mount('drawer-ask-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = RD.mount('drawer-ask-input');
      const q = (input.value || '').trim();
      if (!q) return;
      const askScope = (currentSelection.block_ids && currentSelection.block_ids.length) ? 'selection' : 'chapter';
      input.value = '';
      runAssist('ask', q, '', askScope, false);
    });

    const reader = RD.mount('reader-body');
    reader.addEventListener('mouseup', () => {
      setTimeout(() => {
        const cap = captureSelection();
        if (!cap) return;
        currentSelection = { block_ids: cap.block_ids, text: cap.text };
        updateScopeIndicator();
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
        updateScopeIndicator();
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
      const bp = btn.getAttribute('data-blueprint') || '';
      hideSelectionBar();
      if (action === 'search-related') {
        const probe = clipSearchProbe(currentSelection.text);
        if (!probe) {
          RD.toast('请先选中一段有效正文', true);
          return;
        }
        const firstBid = (currentSelection.block_ids || [])[0] || '';
        const blk = ((data && data.blocks) || []).find((b) => b.id === firstBid);
        const srcPath = (blk && blk.source_file) ? ('books/' + UID + '/md/' + blk.source_file) : '';
        const srcLine = (blk && blk.start_line) ? blk.start_line : 0;
        window.open(buildRelatedHref(probe, srcPath, srcLine), '_blank', 'noopener');
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
      runAssist(action, '', bp, 'selection', false);
    });
  }

  function renderDrawerScopeBar(opts) {
    const bar = RD.mount('drawer-scope-bar');
    if (!bar) return;
    const scope = opts.scope || 'selection';
    const action = opts.action || 'diagram';
    const blueprint = opts.blueprint || '';
    const covered = opts.covered_blocks || ((data && data.blocks && data.blocks.length) || 0);
    const sampled = Boolean(opts.sampled);
    const cached = Boolean(opts.cached);
    const selIds = (opts.block_ids && opts.block_ids.length) ? opts.block_ids : ((currentSelection && currentSelection.block_ids) || []);
    const prof = activeProfile();

    bar.hidden = false;
    const scopeTag = scope === 'chapter'
      ? '<span class="scope-pill">作用范围：全章（' + RD.fmt(covered) + ' 段' + (sampled ? ' · 均匀骨架' : '') + '）</span>'
      : '<span class="scope-pill is-sel">作用范围：已选 ' + RD.esc(formatSelectionSpan(selIds) || '片段') + '</span>';

    const switchScopeBtn = scope === 'selection'
      ? '<button type="button" class="drawer-bp-pill" data-switch-scope="chapter">改按全章生成</button>'
      : (currentSelection.block_ids && currentSelection.block_ids.length
          ? '<button type="button" class="drawer-bp-pill" data-switch-scope="selection">仅针对所选段</button>'
          : '');

    const bpPills = (action === 'diagram' && (prof.blueprints || []).length > 1)
      ? prof.blueprints.map((item) =>
          '<button type="button" class="drawer-bp-pill' + (item.blueprint === blueprint ? ' active' : '') +
          '" data-switch-bp="' + RD.esc(item.blueprint) + '">' + RD.esc(item.label) + '</button>'
        ).join('')
      : '';

    const regenBtn = (scope === 'chapter' || cached)
      ? '<button type="button" class="drawer-bp-pill" data-regen-chapter title="忽略缓存重新调用模型">' +
        (cached ? '命中全章缓存 · ↻ 重生成' : '↻ 重新生成') + '</button>'
      : '';

    bar.innerHTML = scopeTag + switchScopeBtn + bpPills + regenBtn;

    const swBtn = bar.querySelector('[data-switch-scope]');
    if (swBtn) {
      swBtn.addEventListener('click', () => {
        const nextScope = swBtn.getAttribute('data-switch-scope');
        if (nextScope === 'chapter') clearSelectionScope();
        runAssist(action, (lastResult && lastResult.question) || '', blueprint, nextScope, false);
      });
    }
    bar.querySelectorAll('[data-switch-bp]').forEach((el) => {
      el.addEventListener('click', () => {
        const nextBp = el.getAttribute('data-switch-bp');
        if (nextBp && nextBp !== blueprint) {
          runAssist('diagram', '', nextBp, scope, false);
        }
      });
    });
    const rBtn = bar.querySelector('[data-regen-chapter]');
    if (rBtn) {
      rBtn.addEventListener('click', () => {
        runAssist(action, (lastResult && lastResult.question) || '', blueprint, scope, true);
      });
    }
  }

  async function runAssist(action, question, blueprint, scopeOverride, force) {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const settings = RD.modelSettings();
    const prof = activeProfile();
    const scope = scopeOverride || ((currentSelection.block_ids && currentSelection.block_ids.length) ? 'selection' : 'chapter');
    const chosenBp = blueprint || (action === 'diagram' && prof.blueprints && prof.blueprints[0] ? prof.blueprints[0].blueprint : '');
    const bpMeta = (prof.blueprints || []).find((b) => b.blueprint === chosenBp);
    lastResult = null;
    setDrawerOpen(true);
    RD.mount('btn-pin').disabled = true;
    RD.mount('drawer-empty').hidden = true;

    const titleBase = (action === 'diagram' && bpMeta) ? bpMeta.label : (RD.actionNames[action] || action);
    const spanText = scope === 'chapter'
      ? '全章'
      : formatSelectionSpan(currentSelection.block_ids);
    RD.mount('drawer-title').textContent = titleBase + (spanText ? ' · ' + spanText : '');
    renderDrawerScopeBar({
      scope,
      action,
      blueprint: chosenBp,
      block_ids: scope === 'chapter' ? [] : currentSelection.block_ids,
      covered_blocks: (data && data.blocks && data.blocks.length) || 0,
      sampled: false,
      cached: false,
    });

    RD.mount('drawer-quote').hidden = false;
    RD.mount('drawer-quote').textContent = scope === 'chapter'
      ? ('作用范围：全章正文（共 ' + RD.fmt((data && data.blocks && data.blocks.length) || 0) + ' 个原文块，涵盖全章脉络）')
      : (currentSelection.text || ('段落 ' + currentSelection.block_ids.join(', ')));
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
          scope,
          force: Boolean(force),
          blueprint: chosenBp,
          read_type: prof.read_type || '',
          sub_kind: prof.sub_kind || '',
          block_ids: scope === 'chapter' ? [] : currentSelection.block_ids,
          selected_text: scope === 'chapter' ? '' : currentSelection.text,
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
            if (payload.figure && RD.diagrams && RD.diagrams.renderBlueprintCard) {
              RD.diagrams.renderBlueprintCard(RD.mount('drawer-diagram'), payload.figure, { onFocusBlock: focusBlock });
            } else {
              RD.mount('drawer-diagram').innerHTML = payload.html || '';
            }
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
    renderDrawerScopeBar({
      scope: payload.scope || 'selection',
      action: payload.action || 'diagram',
      blueprint: payload.blueprint || '',
      block_ids: payload.block_ids || [],
      covered_blocks: payload.covered_blocks || ((data && data.blocks && data.blocks.length) || 0),
      sampled: Boolean(payload.sampled),
      cached: Boolean(payload.cached),
    });
    if (payload.figure && RD.diagrams && RD.diagrams.renderBlueprintCard) {
      RD.diagrams.renderBlueprintCard(RD.mount('drawer-diagram'), payload.figure, { onFocusBlock: focusBlock });
    } else if (payload.diagram_html) {
      RD.mount('drawer-diagram').innerHTML = payload.diagram_html;
    }
    if (payload.content) {
      RD.mount('drawer-answer').innerHTML = RD.renderMarkdown(payload.content);
    } else if (payload.figure) {
      RD.mount('drawer-answer').innerHTML = '';
    }
    const metaParts = [];
    if (payload.cached) metaParts.push('全章缓存');
    if (payload.model) metaParts.push(payload.model);
    if (payload.tokens) metaParts.push(RD.fmt(payload.tokens) + ' tokens');
    if (payload.elapsed_ms) metaParts.push(RD.elapsed(payload.elapsed_ms));
    RD.mount('drawer-meta').textContent = metaParts.join(' · ');
    RD.mount('btn-pin').disabled = false;
  }

  function firstValidAnchorForFigure(fig) {
    const valid = new Set(((data && data.blocks) || []).map((b) => b.id).filter(Boolean));
    const pick = (arr) => (Array.isArray(arr) ? arr.find((b) => valid.has(b)) : '');
    if (fig && fig.data) {
      for (const nd of (fig.data.nodes || [])) { const hit = pick(nd.blocks); if (hit) return hit; }
      for (const pt of (fig.data.points || [])) { const hit = pick(pt.blocks); if (hit) return hit; }
      for (const rw of (fig.data.rows || [])) { const hit = pick(rw.blocks); if (hit) return hit; }
      for (const qd of (fig.data.quadrants || [])) {
        for (const it of (qd.items || [])) { const hit = pick(it.blocks); if (hit) return hit; }
      }
    }
    if (fig && Array.isArray(fig.items)) {
      for (const it of fig.items) { const hit = pick(it.blocks); if (hit) return hit; }
    }
    return (data && data.blocks && data.blocks[0] && data.blocks[0].id) || 'B01';
  }

  async function pinCurrentResult() {
    if (!lastResult) return;
    const btn = RD.mount('btn-pin');
    btn.disabled = true;
    try {
      const rawTitle = (lastResult.figure && lastResult.figure.caption) || '';
      const isChapter = lastResult.scope === 'chapter';
      const title = isChapter && rawTitle && !rawTitle.startsWith('全章') ? ('全章 · ' + rawTitle) : rawTitle;
      const pinBlocks = (lastResult.block_ids && lastResult.block_ids.length)
        ? lastResult.block_ids
        : (isChapter ? [firstValidAnchorForFigure(lastResult.figure)] : currentSelection.block_ids);
      const saved = await RD.api('/api/assist/pin', {
        method: 'POST',
        body: {
          uid: UID,
          n: N,
          action: lastResult.action,
          title,
          block_ids: pinBlocks,
          selected_text: lastResult.selected_text || (isChapter ? '全章范围生成' : currentSelection.text),
          question: lastResult.question || '',
          content: lastResult.content || '',
          diagram_html: lastResult.diagram_html || '',
          figure: lastResult.figure || null,
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
