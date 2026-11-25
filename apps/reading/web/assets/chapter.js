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
    fn: (urlParams.get('fn') || '').trim(),
    srcN: (urlParams.get('src_n') || '').trim(),
    blockIds: blocksParam.length ? blocksParam : (hashBid ? [hashBid] : []),
    dismissed: false,
  };

  let data = null;
  let bookChapters = [];
  let showSpine = window.innerWidth >= 1180;
  let spineTab = 'map'; // 'map' | 'terms' | 'toc'
  let showMap = true;
  let showTerms = false;
  let showModelBox = false;
  let drawerOpen = window.innerWidth >= 960;
  let hidePins = false;
  let scopeMode = 'chapter';
  let activeToolKey = '';
  let loadingToolKey = '';
  let cachedToolKeys = new Set();
  let globalBound = false;
  let currentSelection = { block_ids: [], text: '' };
  let lastResult = null;
  let abortCtrl = null;
  let mapLoading = false;
  let mapStartedAt = 0;
  let mapTimer = 0;
  let mapError = '';
  let mapAbortCtrl = null;
  let scrollRaf = 0;
  let footnoteReturn = null;
  let lastFnTrigger = null;
  const justPinnedIds = new Set();
  let supportsDisplayTransition;

  function canTransitionDisplay() {
    if (supportsDisplayTransition !== undefined) return supportsDisplayTransition;
    if (!window.CSS?.supports?.('transition-behavior', 'allow-discrete') || !document.body) {
      return false;
    }
    const probe = document.createElement('div');
    probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;';
    document.body.appendChild(probe);
    getComputedStyle(probe).display;
    probe.style.display = 'none';
    supportsDisplayTransition = getComputedStyle(probe).display === 'block';
    probe.remove();
    return supportsDisplayTransition;
  }

  function supportsTopLayerExit() {
    return Boolean(window.CSS?.supports?.('overlay', 'auto') && canTransitionDisplay());
  }

  function openPopoverSafe(pop) {
    if (!pop) return;
    pop.removeAttribute('data-closing');
    if (typeof pop.showPopover === 'function') {
      try {
        if (!pop.matches(':popover-open')) pop.showPopover();
        return;
      } catch (e) {}
    }
    pop.classList.add(':popover-open');
    pop.style.display = 'block';
  }

  async function closePopoverSafe(pop) {
    if (!pop) return;
    if (!supportsTopLayerExit()) {
      pop.setAttribute('data-closing', '');
      const animations = pop.getAnimations ? pop.getAnimations({ subtree: true }) : [];
      if (animations.length > 0) {
        await Promise.race([
          Promise.allSettled(animations.map((a) => a.finished)),
          new Promise((r) => setTimeout(r, 360)),
        ]);
      }
      if (!pop.hasAttribute('data-closing')) return;
      pop.removeAttribute('data-closing');
    }
    if (typeof pop.hidePopover === 'function') {
      try {
        if (pop.matches(':popover-open')) pop.hidePopover();
        return;
      } catch (e) {}
    }
    pop.classList.remove(':popover-open');
    pop.style.display = 'none';
  }

  const FALLBACK_PROFILES = {
    practical: {
      read_type: 'practical', name: '实用型书', adler_category: '实用型书',
      goal: '锁定「目的 → 手段 → 成立条件 → 避坑边界」，提炼可执行 SOP、条件决策与失效复盘',
      blueprints: [
        { blueprint: 'flow_swimlane', label: 'SOP 流程图' },
        { blueprint: 'decision_tree', label: '条件决策树' },
        { blueprint: 'quadrant_matrix', label: '优先级四象限' },
      ],
      extensions: [
        { action: 'checklist', label: '行动自检清单', cross_search: false },
        { action: 'failure_postmortem', label: '反例与失效复盘', cross_search: false },
      ],
    },
    literature: {
      read_type: 'literature', name: '想象文学', adler_category: '想象文学',
      goal: '重沉浸与体验：把握叙事张力、人物阵营、舞台/空间动线、伏笔视角与细读意象',
      blueprints: [
        { blueprint: 'tension_curve', label: '叙事张力曲线' },
        { blueprint: 'relation_graph', label: '人物与阵营图谱' },
        { blueprint: 'spatial_route', label: '空间与舞台动线' },
      ],
      extensions: [
        { action: 'narrative_clues', label: '叙事视角与伏笔线索', cross_search: false },
        { action: 'imagery', label: '意象、潜台词与细读', cross_search: false },
      ],
    },
    history: {
      read_type: 'history', name: '历史书', adler_category: '历史书',
      goal: '多线并行时空对照、权力阵营拆解、历史因果演进与跨史家视角比对',
      blueprints: [
        { blueprint: 'parallel_timeline', label: '多线平行时间轴' },
        { blueprint: 'power_hierarchy', label: '权力与阵营结构' },
        { blueprint: 'spatial_route', label: '地理与空间态势' },
        { blueprint: 'historical_causality', label: '历史因果演进图' },
      ],
      extensions: [
        { action: 'historical_dossier', label: '人物与名物档案', cross_search: false },
        { action: 'compare_context', label: '多史家视角对照', cross_search: true },
      ],
    },
    science: {
      read_type: 'science', name: '科学与数学', adler_category: '科学与数学',
      goal: '理清核心问题、公理前提、推导依赖与成立边界',
      blueprints: [
        { blueprint: 'derivation_dag', label: '公理-定理推导图' },
        { blueprint: 'sensitivity_chart', label: '变量敏感度曲线' },
        { blueprint: 'system_flow', label: '机制与装置流图' },
      ],
      extensions: [{ action: 'boundary_check', label: '演进与适用边界', cross_search: false }],
    },
    philosophy: {
      read_type: 'philosophy', name: '哲学书', adler_category: '哲学书',
      goal: '抠紧作者专属词义边界，剥离前提 → 推论 → 结论、批判谱系、论战交锋与反例边界',
      blueprints: [
        { blueprint: 'argument_map', label: '论证逻辑树' },
        { blueprint: 'term_contrast', label: '词义辨析双栏表' },
        { blueprint: 'lineage_dag', label: '批判谱系图' },
      ],
      extensions: [
        { action: 'sparring_dossier', label: '学术交锋档案', cross_search: false },
        { action: 'counterexample_lab', label: '反例与现实检验', cross_search: false },
      ],
    },
    social: {
      read_type: 'social', name: '社会科学', adler_category: '社会科学',
      goal: '厘清混杂术语、多变量因果反馈与跨作者对照',
      blueprints: [
        { blueprint: 'causal_loop', label: '因果反馈回路图' },
        { blueprint: 'concept_matrix', label: '跨学派概念矩阵' },
        { blueprint: 'metric_chart', label: '实证趋势图' },
      ],
      extensions: [{ action: 'method_audit', label: '方法与样本局限', cross_search: true }],
    },
    visual: {
      read_type: 'visual', name: '图册与视觉', adler_category: '图册与设定集',
      goal: '建立构图元素、工艺原型与时代流派索引',
      blueprints: [{ blueprint: 'element_matrix', label: '构图元素索引表' }],
      extensions: [{ action: 'craft_notes', label: '工艺流派考据', cross_search: false }],
    },
  };

  function getPref(key, fallback) {
    try {
      return localStorage.getItem(key) || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function setPref(key, val) {
    try {
      if (val == null || val === '') localStorage.removeItem(key);
      else localStorage.setItem(key, String(val));
    } catch (e) {}
  }

  function applyTypographyPrefs() {
    const size = getPref('reading-font-size', '18');
    const measure = getPref('reading-measure', '680');
    document.documentElement.style.setProperty('--reader-size', size + 'px');
    document.documentElement.style.setProperty('--reader-measure', measure + 'px');
    document.querySelectorAll('[data-font-size]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-font-size') === size));
    });
    document.querySelectorAll('[data-measure]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-measure') === measure));
    });
  }

  function getStoredReadType() {
    return getPref('reading-profile:' + UID, '');
  }

  function setStoredReadType(rt) {
    const def = (data && data.reading_profile && data.reading_profile.read_type) || '';
    if (!rt || rt === def) setPref('reading-profile:' + UID, '');
    else setPref('reading-profile:' + UID, rt);
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

  function findHighlightRange(raw, probe) {
    if (!probe || arrival.dismissed) return null;
    const cleanProbe = probe.trim();
    if (!cleanProbe) return null;

    const candidates = [cleanProbe];
    if (cleanProbe.length > 32) candidates.push(cleanProbe.slice(0, 32));
    if (cleanProbe.length > 16) candidates.push(cleanProbe.slice(0, 16));

    for (const cand of candidates) {
      const idx = raw.indexOf(cand);
      if (idx >= 0) return [idx, idx + cand.length];
    }
    return null;
  }

  function highlightEscaped(text, probe) {
    const raw = String(text || '');
    const range = findHighlightRange(raw, probe);
    if (!range) return RD.esc(raw);
    return (
      RD.esc(raw.slice(0, range[0])) +
      '<mark class="hl-hit">' + RD.esc(raw.slice(range[0], range[1])) + '</mark>' +
      RD.esc(raw.slice(range[1]))
    );
  }

  function buildExternalFootnoteHref(chN, blockId, marker) {
    if (blockId) {
      const targetParams = new URLSearchParams();
      targetParams.set('from', 'footnote');
      if (marker) targetParams.set('fn', marker);
      targetParams.set('src_n', String(N).padStart(2, '0'));
      return RD.chapterHref(UID, chN) + '?' + targetParams.toString() + '#blk-' + encodeURIComponent(blockId);
    }
    return RD.chapterHref(UID, chN);
  }

  function renderFnRefButton(ref) {
    const marker = String(ref.marker || '');
    const isLocal = Boolean(ref.target_block);
    const cls = 'fn-ref' + (isLocal ? '' : ' is-external');
    const ch = ref.target_chapter || null;
    const title = isLocal
      ? ('查看注释 [' + marker + ']')
      : ('注释 [' + marker + '] 不在本章正文，点击查看提示');
    return (
      '<sup class="fn-ref-wrap">' +
        '<button type="button" class="' + cls + '"' +
          ' id="' + RD.esc(ref.id || '') + '"' +
          ' data-fn-ref="' + RD.esc(ref.id || '') + '"' +
          ' data-fn-marker="' + RD.esc(marker) + '"' +
          (isLocal ? ' data-fn-target="' + RD.esc(ref.target_block) + '"' : '') +
          ' data-fn-status="' + RD.esc(ref.status || (isLocal ? 'local' : 'unresolved')) + '"' +
          (ref.target_file ? ' data-fn-target-file="' + RD.esc(ref.target_file) + '"' : '') +
          (ch ? ' data-fn-target-ch-n="' + RD.esc(ch.n) + '" data-fn-target-ch-title="' + RD.esc(ch.title || '') + '"' : '') +
          (ch && ch.block_id ? ' data-fn-target-block="' + RD.esc(ch.block_id) + '"' : '') +
          ' title="' + RD.esc(title) + '"' +
          ' aria-label="注释 ' + RD.esc(marker) + '">' +
          RD.esc(marker) +
        '</button>' +
      '</sup>'
    );
  }

  function renderRichText(text, probe, fnRefs) {
    if (!fnRefs || !fnRefs.length) return highlightEscaped(text, probe);
    const raw = String(text || '');
    const hl = findHighlightRange(raw, probe);
    const byPos = new Map();
    const pts = new Set([0, raw.length]);
    if (hl) {
      pts.add(hl[0]);
      pts.add(hl[1]);
    }
    for (const ref of fnRefs) {
      const p = Math.max(0, Math.min(raw.length, Number(ref.pos || 0)));
      pts.add(p);
      if (!byPos.has(p)) byPos.set(p, []);
      byPos.get(p).push(ref);
    }
    const sorted = Array.from(pts).sort((a, b) => a - b);
    let out = '';
    if (byPos.has(0)) {
      out += byPos.get(0).map(renderFnRefButton).join('');
    }
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i];
      const b = sorted[i + 1];
      if (b > a) {
        const chunk = RD.esc(raw.slice(a, b));
        const inHl = Boolean(hl && a >= hl[0] && b <= hl[1]);
        out += inHl ? ('<mark class="hl-hit">' + chunk + '</mark>') : chunk;
      }
      if (byPos.has(b)) {
        out += byPos.get(b).map(renderFnRefButton).join('');
      }
    }
    return out;
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
    const sourceLabel = arrival.from === 'daily'
      ? '来自往期日课'
      : (arrival.from === 'footnote' ? '来自正文注释' : '来自书库检索');
    const bidLabel = arrival.blockIds.length ? arrival.blockIds.join(', ') : '';
    const primaryBid = arrival.blockIds[0] || '';
    const queryOrFn = arrival.query
      ? '<span class="arrival-query">「' + RD.esc(arrival.query) + '」</span>'
      : (arrival.fn ? '<span class="arrival-query">注 [' + RD.esc(arrival.fn) + ']</span>' : '');
    el.innerHTML =
      '<div class="arrival-row">' +
        '<div class="arrival-info">' +
          '<span class="arrival-badge">' + RD.esc(sourceLabel) + '</span>' +
          queryOrFn +
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
          (arrival.from === 'footnote' && arrival.srcN
            ? '<a class="wb-btn" href="' + RD.chapterHref(UID, arrival.srcN) + '">回到第 ' + RD.esc(arrival.srcN) + ' 章</a>'
            : '') +
          '<button type="button" class="wb-btn arrival-close" id="btn-arrival-close" aria-label="收起提示">×</button>' +
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
        const wasFn = arrival.from === 'footnote';
        arrival.dismissed = true;
        renderArrivalBanner();
        renderBlocks();
        if (wasFn) clearActiveFootnoteDef();
      });
    }
  }

  function syncBackdrop() {
    const bd = RD.mount('desk-backdrop');
    if (!bd) return;
    const narrowSpine = window.innerWidth <= 1180 && showSpine;
    const narrowDrawer = window.innerWidth <= 920 && drawerOpen;
    bd.hidden = !(narrowSpine || narrowDrawer);
  }

  function toolCacheKey(readType, action, blueprint) {
    const keyPart = (action === 'diagram' && blueprint) ? blueprint : action;
    return String(readType || 'social') + '_' + String(action || '') + '_' + String(keyPart || '');
  }

  function renderToolButtonsHtml(prof) {
    const bps = prof.blueprints || [];
    const exts = prof.extensions || [];
    const checkSvg = '<svg class="tool-cache-ico" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.2 8.4 6.5 11.7 12.8 4.6"/></svg>';
    const bpButtons = bps.map((bp) => {
      const key = 'diagram:' + bp.blueprint;
      const ckey = toolCacheKey(prof.read_type, 'diagram', bp.blueprint);
      const isCached = cachedToolKeys.has(ckey);
      const isAct = activeToolKey === key;
      const isLoad = loadingToolKey === key;
      const cls = 'marg-tool-btn' +
        (isCached ? ' is-cached' : ' is-uncached') +
        (isAct ? ' active' : '') +
        (isLoad ? ' is-loading' : '');
      const tip = (bp.description ? bp.description + ' · ' : '') +
        (scopeMode === 'selection'
          ? (isCached ? '已有全章缓存；当前针对所选段即时生成，不覆盖缓存' : '当前针对所选段即时生成')
          : (isCached ? '已有全章缓存，点击立即查看' : '尚未生成全章缓存，点击调用模型'));
      return '<button type="button" class="' + cls + '"' +
        ' data-run-blueprint="' + RD.esc(bp.blueprint) + '"' +
        ' data-tool-key="' + RD.esc(key) + '"' +
        ' data-cache-key="' + RD.esc(ckey) + '"' +
        ' aria-pressed="' + String(isAct) + '"' +
        ' title="' + RD.esc(tip) + '">' +
        (isLoad ? '<span class="tool-spin-dot" aria-hidden="true"></span>' : (isCached ? checkSvg : '')) +
        '<span>' + RD.esc(bp.label) + '</span>' +
        '</button>';
    }).join('');
    const extButtons = exts.map((ext) => {
      const key = 'ext:' + ext.action;
      const ckey = toolCacheKey(prof.read_type, ext.action, '');
      const isCached = cachedToolKeys.has(ckey);
      const isAct = activeToolKey === key;
      const isLoad = loadingToolKey === key;
      const cls = 'marg-tool-btn' +
        (isCached ? ' is-cached' : ' is-uncached') +
        (isAct ? ' active' : '') +
        (isLoad ? ' is-loading' : '');
      const tip = (ext.hint ? ext.hint + ' · ' : '') +
        (scopeMode === 'selection'
          ? (isCached ? '已有全章缓存；当前针对所选段即时生成，不覆盖缓存' : '当前针对所选段即时生成')
          : (isCached ? '已有全章缓存，点击立即查看' : '尚未生成全章缓存，点击调用模型'));
      return '<button type="button" class="' + cls + '"' +
        ' data-run-ext="' + RD.esc(ext.action) + '"' +
        ' data-tool-key="' + RD.esc(key) + '"' +
        ' data-cache-key="' + RD.esc(ckey) + '"' +
        ' aria-pressed="' + String(isAct) + '"' +
        ' title="' + RD.esc(tip) + '">' +
        (isLoad ? '<span class="tool-spin-dot" aria-hidden="true"></span>' : (isCached ? checkSvg : '')) +
        '<span>' + RD.esc(ext.label) + '</span>' +
        '</button>' +
        (ext.cross_search ? '<button type="button" class="marg-tool-btn is-link" data-cross-book title="在书库其他书中查找相关论述">跨书对照 ↗</button>' : '');
    }).join('');
    return bpButtons + extButtons;
  }

  function syncToolButtons() {
    const strip = RD.mount('wb-profile-strip');
    if (!strip) return;
    const prof = activeProfile();
    const btnsWrap = strip.querySelector('.marg-ribbon-btns');
    if (btnsWrap) {
      btnsWrap.innerHTML = renderToolButtonsHtml(prof);
    }
    updateScopeIndicator();
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
      '<a class="crumb-book" href="' + RD.bookHref(book.uid) + '" title="' + RD.esc(book.title) + '">' +
        RD.esc(book.title_short || book.title) +
      '</a><i>/</i>' +
      '<span class="crumb-ch">第 ' + RD.esc(ch.n) + ' 章</span>';
    document.title = ch.title + ' · ' + (book.title_short || book.title);

    const profileOpts = avail.map((item) =>
      '<option value="' + RD.esc(item.read_type) + '"' + (item.read_type === prof.read_type ? ' selected' : '') + '>' +
      RD.esc(item.name) + '</option>'
    ).join('');

    const hintNote = prof.boundary_hint || prof.sub_kind_hint || '';
    const bps = prof.blueprints || [];
    const toolButtonsHtml = renderToolButtonsHtml(prof);

    const spineBtn = RD.mount('btn-spine');
    if (spineBtn) spineBtn.setAttribute('aria-pressed', String(showSpine));
    const drawerTopBtn = RD.mount('btn-drawer-top');
    if (drawerTopBtn) drawerTopBtn.setAttribute('aria-pressed', String(drawerOpen));

    const firstBp = bps[0] || null;
    const bookColor = book.color || '#1E3A36';
    const gridCls = 'wb-grid folio-grid' + (drawerOpen ? ' has-drawer' : '') + (showSpine ? '' : ' hide-spine');

    RD.mount('wb-root').innerHTML =
      '<div class="' + gridCls + '" id="wb-grid">' +
        '<aside class="left-spine" id="left-spine" aria-label="章节导览">' +
          '<div class="spine-book-head" style="--book-cloth:' + RD.esc(bookColor) + '">' +
            '<a class="spine-book-link" href="' + RD.bookHref(book.uid) + '">' +
              '<span class="spine-cloth-bar" aria-hidden="true"></span>' +
              '<span class="spine-book-meta">' +
                '<strong class="spine-book-title">' + RD.esc(book.title_short || book.title) + '</strong>' +
                '<small class="spine-book-sub" id="spine-book-sub">' +
                  RD.esc(book.author_display || book.author || '') +
                  (book.subject ? ' · ' + RD.esc(book.subject) : '') +
                '</small>' +
              '</span>' +
            '</a>' +
          '</div>' +

          '<div class="spine-tabs wb-toolbar" id="wb-toolbar" role="tablist" aria-label="导览视图">' +
            '<button type="button" class="spine-tab" id="btn-map" role="tab" aria-pressed="' + (spineTab === 'map') + '">' +
              mapTabInnerHtml(mapReady) +
            '</button>' +
            '<button type="button" class="spine-tab" id="btn-terms" role="tab" aria-pressed="' + (spineTab === 'terms') + '">核心术语</button>' +
            '<button type="button" class="spine-tab" id="btn-toc" role="tab" aria-pressed="' + (spineTab === 'toc') + '">全书目录</button>' +
          '</div>' +

          '<div class="spine-scroll" id="spine-scroll">' +
            '<div class="spine-panel" id="map-drawer"' + (spineTab === 'toc' ? ' hidden' : '') + '></div>' +
            '<div class="spine-panel" id="toc-drawer"' + (spineTab === 'toc' ? '' : ' hidden') + '>' +
              '<div class="spine-muted-empty">正在载入全书目录…</div>' +
            '</div>' +
          '</div>' +

          '<div class="spine-foot">' +
            '<div class="spine-model-wrap" id="model-box"' + (showModelBox ? '' : ' hidden') + '>' +
              '<div class="spine-model-head"><span>伴读模型配置</span></div>' +
              RD.modelSettingsHtml() +
            '</div>' +

            '<div class="spine-perspective-row">' +
              '<label class="spine-persp-lbl" for="profile-select" title="仅调整本章伴读工具视角，不改动书库人账分类">视角</label>' +
              '<div class="spine-select-wrap">' +
                '<select class="spine-select" id="profile-select" aria-label="切换阅读视角">' + profileOpts + '</select>' +
                '<svg class="spine-select-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>' +
              '</div>' +
              (prof.overridden ? '<span class="override-dot" title="已临时切换阅读视角">已切换</span>' : '') +
            '</div>' +
            (hintNote ? '<p class="spine-persp-note">' + RD.esc(hintNote) + '</p>' : '') +

            '<div class="spine-util-row">' +
              '<button type="button" class="spine-util-btn" id="btn-toggle-pins" aria-pressed="' + (!hidePins) + '" title="显示或隐藏正文内钉住的旁批">' +
                '旁批 <b id="pin-count">' + pinCount + '</b>' +
              '</button>' +
              '<button type="button" class="spine-util-btn" id="btn-model-box" aria-pressed="' + showModelBox + '" title="模型与推理深度设置">模型</button>' +
              '<button type="button" class="spine-util-btn" id="btn-drawer" aria-pressed="' + drawerOpen + '" title="展开或收起右侧伴读">伴读</button>' +
            '</div>' +

            '<div class="spine-quick-links">' +
              '<a href="' + buildSearchHref('', true) + '" target="_blank" rel="noopener">本书内检索 ↗</a>' +
              '<span aria-hidden="true">·</span>' +
              '<a href="/records?uid=' + encodeURIComponent(UID) + '">本章日志 →</a>' +
            '</div>' +
          '</div>' +
        '</aside>' +

        '<section class="center-page" id="center-page">' +
          '<div class="page-inner">' +
            '<div class="arrival-banner" id="arrival-banner" hidden></div>' +
            '<header class="book-chapter-title">' +
              '<div class="kicker">' +
                '<span>' + RD.esc(book.title) + '</span>' +
                (book.subject ? '<span class="kicker-sep">·</span><span>' + RD.esc(book.subject) + '</span>' : '') +
                '<span class="kicker-sep">·</span><span class="kicker-persp">' + RD.esc(prof.name) + '</span>' +
              '</div>' +
              '<h1>' + RD.esc(ch.title) + '</h1>' +
              '<div class="colophon">' +
                '<span class="colophon-stats">' +
                  RD.fmt(ch.chars) + ' 字' +
                  '<em>·</em>约 ' + RD.fmt(ch.minutes) + ' 分钟' +
                  '<em>·</em>' + RD.fmt(blockCount) + ' 段' +
                  '<em>·</em>100% 完整正文直出' +
                '</span>' +
                '<button type="button" class="colophon-drawer-btn" id="btn-drawer-reopen"' + (drawerOpen ? ' hidden' : '') + '>打开伴读 →</button>' +
              '</div>' +
            '</header>' +
            '<article class="reader-column"><div class="reader-body" id="reader-body"></div></article>' +
            '<nav class="chapter-pager" id="chapter-pager" aria-label="章节翻页"></nav>' +
          '</div>' +
        '</section>' +

        '<aside class="assist-drawer right-marginalia" id="assist-drawer"' + (drawerOpen ? '' : ' hidden') + ' aria-label="朱批伴读">' +
          '<div class="drawer-head">' +
            '<div class="drawer-head-left">' +
              '<span class="drawer-seal" aria-hidden="true">批</span>' +
              '<h3 id="drawer-title">伴读</h3>' +
            '</div>' +
            '<div class="drawer-head-actions">' +
              '<button type="button" class="desk-ghost-btn" id="btn-pin" disabled title="将当前回答固定到对应段落下方">钉住到段落</button>' +
              '<button type="button" class="desk-icon-btn sm" id="btn-drawer-close" aria-label="收起抽屉" title="收起伴读（]）">' +
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 6 6 18M6 6l12 12"/></svg>' +
              '</button>' +
            '</div>' +
          '</div>' +

          '<div class="marg-deck">' +
            '<div class="marg-scope-row">' +
              '<div class="marg-tabs" role="tablist">' +
                '<button type="button" id="tab-scope-chapter" data-scope-tab="chapter">全章 · ' + RD.fmt(blockCount) + '段</button>' +
                '<button type="button" id="tab-scope-selection" data-scope-tab="selection">选段精读</button>' +
              '</div>' +
              '<span class="scope-pill" id="strip-scope-pill"></span>' +
            '</div>' +
            (toolButtonsHtml
              ? '<div class="marg-tool-ribbon" id="wb-profile-strip">' +
                  '<div class="marg-ribbon-btns">' + toolButtonsHtml + '</div>' +
                '</div>'
              : '') +
          '</div>' +

          '<div class="drawer-scope-bar" id="drawer-scope-bar" hidden></div>' +

          '<div class="drawer-body" id="drawer-body">' +
            '<div class="marg-empty" id="drawer-empty">' +
              '<div class="marg-empty-kicker">' + RD.esc(prof.name) + ' · 随文伴读</div>' +
              '<p class="marg-empty-lead">在正文里划选任意句子，或点击段尾 <b>✦</b>，即可对局部精读、讲人话或拆解前提；未选段时，上方工具直接分析全章。</p>' +
              (prof.goal ? '<p class="marg-empty-goal">' + RD.esc(prof.goal) + '</p>' : '') +
              '<div class="marg-empty-actions">' +
                (firstBp ? '<button type="button" class="desk-primary-btn" id="btn-quick-first-bp">用「' + RD.esc(firstBp.label) + '」总览全章 →</button>' : '') +
              '</div>' +
            '</div>' +
            '<div class="drawer-quote" id="drawer-quote" hidden></div>' +
            '<div class="drawer-diagram" id="drawer-diagram"></div>' +
            '<div class="drawer-answer" id="drawer-answer"></div>' +
            '<div class="drawer-meta" id="drawer-meta"></div>' +
          '</div>' +

          '<div class="drawer-foot">' +
            '<form class="drawer-ask" id="drawer-ask-form">' +
              '<input id="drawer-ask-input" placeholder="对全章或所选段落提问…" aria-label="追问内容">' +
              '<button type="submit" class="drawer-send-btn">追问</button>' +
            '</form>' +
          '</div>' +
        '</aside>' +
      '</div>';

    RD.bindModelSettings();
    const profSel = RD.mount('profile-select');
    if (profSel) {
      profSel.addEventListener('change', () => {
        setStoredReadType(profSel.value);
        activeToolKey = '';
        renderShell();
        renderMapDrawer();
        renderTocDrawer();
        renderChapterPager();
        renderBlocks();
        renderSelectionBarButtons();
        bindEvents();
        RD.toast('已切换阅读视角为「' + (FALLBACK_PROFILES[profSel.value] ? FALLBACK_PROFILES[profSel.value].name : profSel.value) + '」');
      });
    }
    updateScopeIndicator();
    renderArrivalBanner();
    syncBackdrop();
  }

  function formatSelectionSpan(ids) {
    if (!ids || !ids.length) return '';
    return ids.length === 1 ? ids[0] : (ids[0] + '–' + ids[ids.length - 1] + '（' + ids.length + '段）');
  }

  function updateScopeIndicator() {
    const ids = (currentSelection && currentSelection.block_ids) || [];
    const total = (data && data.blocks && data.blocks.length) || 0;
    if (!ids.length) scopeMode = 'chapter';

    const tabCh = RD.mount('tab-scope-chapter');
    const tabSel = RD.mount('tab-scope-selection');
    if (tabCh) {
      tabCh.classList.toggle('active', scopeMode === 'chapter');
      tabCh.textContent = '全章 · ' + RD.fmt(total) + '段';
    }
    if (tabSel) {
      tabSel.classList.toggle('active', scopeMode === 'selection' && ids.length > 0);
      tabSel.textContent = ids.length ? ('选段 · ' + formatSelectionSpan(ids)) : '选段精读';
      tabSel.disabled = ids.length === 0;
    }

    const pill = RD.mount('strip-scope-pill');
    if (!pill) return;
    if (scopeMode === 'selection' && ids.length) {
      pill.hidden = false;
      pill.className = 'scope-pill is-sel';
      pill.innerHTML = '<button type="button" class="scope-reset" id="btn-clear-scope" title="清除选段并切回全章">切回全章 ×</button>';
      const clr = RD.mount('btn-clear-scope');
      if (clr) {
        clr.addEventListener('click', (e) => {
          e.stopPropagation();
          clearSelectionScope();
        });
      }
    } else {
      const prof = activeProfile();
      const allKeys = []
        .concat((prof.blueprints || []).map((bp) => toolCacheKey(prof.read_type, 'diagram', bp.blueprint)))
        .concat((prof.extensions || []).map((ext) => toolCacheKey(prof.read_type, ext.action, '')));
      const hitCount = allKeys.filter((k) => cachedToolKeys.has(k)).length;
      if (allKeys.length) {
        pill.hidden = false;
        pill.className = 'scope-pill cache-legend';
        pill.title = '实线带 ✓ 标记的工具已有全章缓存，点击立即可看；虚线框工具尚未缓存，点击将调用模型';
        pill.innerHTML =
          '<span class="cache-legend-mark" aria-hidden="true">✓</span>' +
          '<span>已缓存 ' + hitCount + '/' + allKeys.length + '</span>';
      } else {
        pill.hidden = true;
        pill.innerHTML = '';
      }
    }
  }

  function clearSelectionScope() {
    currentSelection = { block_ids: [], text: '' };
    scopeMode = 'chapter';
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
      return '<h' + lvl + '>' + renderRichText(text, probe, blk.fn_refs) + '</h' + lvl + '>';
    }
    if (kind === 'quote') return '<blockquote>' + renderRichText(text, probe, blk.fn_refs) + '</blockquote>';
    if (kind === 'code') return '<pre><code>' + highlightEscaped(text, probe) + '</code></pre>';
    if (kind === 'list') {
      const tag = blk.ordered ? 'ol' : 'ul';
      const itemRefs = blk.item_fn_refs || [];
      const items = (blk.items || []).map((x, idx) => '<li>' + renderRichText(x, probe, itemRefs[idx]) + '</li>').join('');
      return '<' + tag + '>' + items + '</' + tag + '>';
    }
    if (kind === 'table') {
      const head = (blk.head || []).length
        ? '<thead><tr>' + blk.head.map((c) => '<th>' + highlightEscaped(c, probe) + '</th>').join('') + '</tr></thead>' : '';
      const rows = (blk.rows || []).map((r) => '<tr>' + (r || []).map((c) => '<td>' + highlightEscaped(c, probe) + '</td>').join('') + '</tr>').join('');
      return '<div class="reader-table-wrap"><table>' + head + '<tbody>' + rows + '</tbody></table></div>';
    }
    if (kind === 'image') {
      const rel = blk.rel || '';
      const src = rel ? '/api/asset?uid=' + encodeURIComponent(UID) + '&rel=' + encodeURIComponent(rel) : '';
      return src
        ? '<figure><img src="' + src + '" alt="' + RD.esc(text || '插图') + '">' +
          (text ? '<figcaption class="wb-sub">' + RD.esc(text) + '</figcaption>' : '') + '</figure>'
        : '<p class="wb-sub">[图片] ' + RD.esc(text) + '</p>';
    }
    if (kind === 'footnote') {
      if (blk.marker) {
        const backRefs = blk.ref_blocks || [];
        const backBtn = backRefs.length
          ? '<button type="button" class="fn-back-inline" data-fn-back-block="' + RD.esc(backRefs[0]) + '" title="返回正文注释点">↩ 返回正文</button>'
          : '';
        return (
          '<div class="reader-footnote-item" data-fn-def-marker="' + RD.esc(blk.marker) + '">' +
            '<span class="fn-def-num">[' + RD.esc(blk.marker) + ']</span>' +
            '<p class="reader-footnote">' + renderRichText(text, probe, blk.fn_refs) + '</p>' +
            backBtn +
          '</div>'
        );
      }
      return '<p class="reader-footnote">' + renderRichText(text, probe, blk.fn_refs) + '</p>';
    }
    return '<p>' + renderRichText(text, probe, blk.fn_refs) + '</p>';
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
        ? '<div class="pinned-notes">' + pinned.map((note) => {
            const kindLabel = RD.actionNames[note.action] || note.action || '旁批';
            const rawExcerpt = String(note.title || note.selected_text || note.content || '').replace(/\s+/g, ' ').trim();
            const excerpt = rawExcerpt.length > 44 ? rawExcerpt.slice(0, 44) + '…' : rawExcerpt;
            const metaLabel = (note.block_ids || []).join(', ') + (note.stale ? ' · 原文已变' : '');
            return '<details class="pin-card pin-note" data-note-id="' + RD.esc(note.id) + '"' + (justPinnedIds.has(note.id) ? ' open' : '') + '>' +
              '<summary class="pin-note-sum">' +
                '<span class="pin-sum-tag">批注 · ' + RD.esc(kindLabel) + '</span>' +
                '<span class="pin-sum-excerpt">' + RD.esc(excerpt || '点击展开查看旁批') + '</span>' +
                (metaLabel ? '<span class="pin-sum-meta">' + RD.esc(metaLabel) + '</span>' : '') +
              '</summary>' +
              '<div class="pin-note-body">' +
                (note.figure ? '<div class="pin-figure-mount" data-pin-figure="' + RD.esc(note.id) + '"></div>' : (note.diagram_html ? '<div>' + note.diagram_html + '</div>' : '')) +
                (note.content ? '<div class="drawer-answer">' + RD.renderMarkdown(note.content) + '</div>' : '') +
                '<div class="pin-note-foot">' +
                  '<button type="button" class="pin-del" data-del-pin="' + RD.esc(note.id) + '">移除</button>' +
                '</div>' +
              '</div>' +
            '</details>';
          }).join('') + '</div>'
        : '';
      return '<div class="reader-block' + (isHit ? ' hit' : '') + '" id="blk-' + RD.esc(bid) + '" data-block="' + RD.esc(bid) + '" data-kind="' + RD.esc(blk.kind || 'paragraph') + '">' +
        renderBlockHtml(blk) +
        '<button type="button" class="blk-trigger" data-trigger="' + RD.esc(bid) + '" title="对 ' + RD.esc(bid) + ' 伴读">✦</button>' +
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

  function renderLocalHeadingsFallback() {
    const headings = ((data && data.blocks) || []).filter((b) => b.kind === 'heading' && b.text);
    if (!headings.length) return '';
    return (
      '<div class="local-headings">' +
        '<div class="local-headings-lbl">正文小节</div>' +
        headings.map((h) =>
          '<button type="button" class="tree-leaf" data-jump="' + RD.esc(h.id) + '">' +
            '<span class="tree-leaf-txt">' + RD.esc(h.text) + '</span>' +
            '<span class="tree-leaf-ref">' + RD.esc(h.id) + '</span>' +
          '</button>'
        ).join('') +
      '</div>'
    );
  }

  function mapTabInnerHtml(mapReady) {
    if (mapLoading) return '知识地图<span class="tab-loading">（生成中…）</span>';
    if (mapReady) return '知识地图<span class="tab-cached">（已缓存）</span>';
    return '知识地图';
  }

  function syncMapTabLabel() {
    const btn = RD.mount('btn-map');
    if (!btn) return;
    const mapReady = Boolean(data && data.chapter_map && data.chapter_map.supported !== false);
    btn.innerHTML = mapTabInnerHtml(mapReady);
  }

  function mapElapsedText() {
    const secs = Math.max(0, Math.floor((Date.now() - mapStartedAt) / 1000));
    return secs > 0 ? ('已用时 ' + secs + ' 秒') : '正在请求模型…';
  }

  function renderMapDrawer() {
    const box = RD.mount('map-drawer');
    const tocBox = RD.mount('toc-drawer');
    if (!box) return;
    if (spineTab === 'toc') {
      box.hidden = true;
      if (tocBox) tocBox.hidden = false;
      return;
    }
    if (tocBox) tocBox.hidden = true;
    box.hidden = false;

    if (mapLoading) {
      box.innerHTML =
        '<div class="spine-cta-card spine-loading-card" aria-busy="true">' +
          '<div class="spine-loading-head">' +
            '<span class="spine-pulse-dot" aria-hidden="true"></span>' +
            '<strong>正在提炼全章脉络与术语…</strong>' +
          '</div>' +
          '<p class="spine-loading-sub"><span id="map-elapsed">' + RD.esc(mapElapsedText()) + '</span> · 切换目录或继续阅读不会中断任务</p>' +
          '<button type="button" class="desk-ghost-btn" id="btn-cancel-map">取消生成</button>' +
        '</div>' + renderLocalHeadingsFallback();
      bindMapButtons();
      return;
    }

    const cmap = data.chapter_map;
    const gate = (data.preflight && data.preflight.chapter_map) || { supported: true, reason: '' };
    const errHtml = mapError ? '<p class="spine-error-note">' + RD.esc(mapError) + '</p>' : '';

    if (!cmap) {
      if (!gate.supported) {
        box.innerHTML =
          '<div class="spine-cta-card">' +
            '<strong>无需强行展开导图</strong>' +
            '<p>' + RD.esc(gate.reason) + '</p>' +
            errHtml +
            '<button type="button" class="desk-ghost-btn" id="btn-gen-map-force">仍要展开简图</button>' +
          '</div>' + renderLocalHeadingsFallback();
      } else {
        box.innerHTML =
          '<div class="spine-cta-card">' +
            '<strong>全章知识地图与术语</strong>' +
            '<p>按需提取本章的结构节拍与关键概念，生成后自动缓存以便随时跳转。</p>' +
            errHtml +
            '<button type="button" class="desk-primary-btn" id="btn-gen-map">生成知识地图 →</button>' +
          '</div>' + renderLocalHeadingsFallback();
      }
      bindMapButtons();
      return;
    }

    if (cmap.supported === false) {
      box.innerHTML =
        '<div class="spine-cta-card">' +
          '<strong>直读建议</strong>' +
          '<p>' + RD.esc(cmap.reason) + '</p>' +
          '<button type="button" class="desk-ghost-btn" id="btn-gen-map-force">仍要展开简图</button>' +
        '</div>' + renderLocalHeadingsFallback();
      bindMapButtons();
      return;
    }

    let html =
      '<div class="spine-map-bar">' +
        '<span class="spine-map-stat">' +
          RD.fmt((cmap.sections || []).length) + ' 节 · ' +
          RD.fmt((cmap.terms || []).length) + ' 术语' +
          (cmap.stale ? ' · 原文已变' : '') +
        '</span>' +
        '<button type="button" class="spine-text-btn" id="btn-gen-map-force" title="忽略缓存重新生成">↻ 更新</button>' +
      '</div>';

    if (spineTab === 'map') {
      if ((cmap.sections || []).length) {
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
      if ((cmap.figures || []).length) {
        html += cmap.figures.map((fig) => fig.html || '').join('');
      }
    } else if (spineTab === 'terms') {
      const terms = cmap.terms || [];
      html += terms.length
        ? '<div class="term-pills">' + terms.map((t) =>
            '<button type="button" class="term-pill" data-jump="' + RD.esc(t.source_block || '') + '">' +
              '<div class="term-pill-head"><b>' + RD.esc(t.term) + '</b>' +
              (t.source_block ? '<span class="tree-leaf-ref">' + RD.esc(t.source_block) + '</span>' : '') +
              '</div>' +
              '<div class="term-pill-def">' + RD.esc(t.definition) + '</div>' +
            '</button>'
          ).join('') + '</div>'
        : '<div class="spine-muted-empty">本章未提取出独立术语。</div>';
    }
    box.innerHTML = html;
    bindMapButtons();
  }

  function renderTocDrawer() {
    const box = RD.mount('toc-drawer');
    if (!box) return;
    if (!bookChapters.length) {
      box.innerHTML = '<div class="spine-muted-empty">暂无章节目录</div>';
      return;
    }
    const curNum = Number(data && data.chapter && data.chapter.n);
    const subEl = RD.mount('spine-book-sub');
    if (subEl && data && data.book) {
      const curIdx = bookChapters.findIndex((c) => Number(c.n) === curNum);
      const posTxt = curIdx >= 0 ? ('第 ' + (curIdx + 1) + ' / ' + bookChapters.length + ' 章') : (bookChapters.length + ' 章');
      const author = data.book.author_display || data.book.author || '';
      subEl.textContent = (author ? author + ' · ' : '') + posTxt;
    }
    box.innerHTML =
      '<div class="toc-list">' +
      bookChapters.map((c) => {
        const active = Number(c.n) === curNum;
        const marks = (c.has_map ? '<i class="toc-dot map" title="已建导图"></i>' : '') +
          (c.note_count ? '<i class="toc-dot pin" title="' + c.note_count + ' 条旁批"></i>' : '');
        return (
          '<a class="toc-item' + (active ? ' active' : '') + '" href="' + RD.chapterHref(UID, c.n) + '"' +
          (active ? ' aria-current="page"' : '') + '>' +
            '<span class="toc-n">' + RD.esc(String(c.n).padStart(2, '0')) + '</span>' +
            '<span class="toc-t">' + RD.esc(c.title) + '</span>' +
            (marks ? '<span class="toc-marks">' + marks + '</span>' : '') +
          '</a>'
        );
      }).join('') +
      '</div>';
    const actEl = box.querySelector('.toc-item.active');
    if (actEl && spineTab === 'toc') {
      actEl.scrollIntoView({ block: 'center' });
    }
  }

  function renderChapterPager() {
    const pager = RD.mount('chapter-pager');
    if (!pager || !bookChapters.length) return;
    const curNum = Number(data && data.chapter && data.chapter.n);
    const idx = bookChapters.findIndex((c) => Number(c.n) === curNum);
    if (idx < 0) return;
    const prev = idx > 0 ? bookChapters[idx - 1] : null;
    const next = idx < bookChapters.length - 1 ? bookChapters[idx + 1] : null;

    pager.innerHTML =
      '<div class="ch-pager-grid">' +
        (prev
          ? '<a class="ch-pager-card prev" href="' + RD.chapterHref(UID, prev.n) + '">' +
              '<span class="ch-pager-dir">← 上一章</span>' +
              '<strong>' + RD.esc(prev.title) + '</strong>' +
            '</a>'
          : '<div class="ch-pager-card empty-slot"></div>') +
        '<a class="ch-pager-mid" href="' + RD.bookHref(UID) + '">全部章目（' + bookChapters.length + '）</a>' +
        (next
          ? '<a class="ch-pager-card next" href="' + RD.chapterHref(UID, next.n) + '">' +
              '<span class="ch-pager-dir">下一章 →</span>' +
              '<strong>' + RD.esc(next.title) + '</strong>' +
            '</a>'
          : '<div class="ch-pager-card empty-slot"></div>') +
      '</div>';
  }

  function bindMapButtons() {
    const genBtn = RD.mount('btn-gen-map');
    if (genBtn) genBtn.addEventListener('click', () => requestChapterMap(false));
    const forceBtn = RD.mount('btn-gen-map-force');
    if (forceBtn) forceBtn.addEventListener('click', () => requestChapterMap(true));
    const cancelBtn = RD.mount('btn-cancel-map');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        if (mapAbortCtrl) mapAbortCtrl.abort();
      });
    }
    const drawer = RD.mount('map-drawer');
    if (!drawer) return;
    drawer.querySelectorAll('[data-jump]').forEach((el) => {
      el.addEventListener('click', () => {
        const bid = el.getAttribute('data-jump');
        if (bid) {
          focusBlock(bid);
          if (window.innerWidth <= 1180) setSpineOpen(false);
        }
      });
    });
  }

  function stopMapTimer() {
    if (mapTimer) {
      clearInterval(mapTimer);
      mapTimer = 0;
    }
  }

  async function requestChapterMap(force) {
    if (mapLoading) return;
    const settings = RD.modelSettings();
    mapLoading = true;
    mapError = '';
    mapStartedAt = Date.now();
    mapAbortCtrl = new AbortController();
    syncMapTabLabel();
    renderMapDrawer();
    stopMapTimer();
    mapTimer = setInterval(() => {
      const el = RD.mount('map-elapsed');
      if (el) el.textContent = mapElapsedText();
    }, 1000);

    try {
      const res = await RD.api('/api/chapter-map/' + encodeURIComponent(UID) + '/' + encodeURIComponent(N), {
        method: 'POST',
        signal: mapAbortCtrl.signal,
        body: { force: Boolean(force), model: settings.model, effort: settings.effort },
      });
      mapLoading = false;
      mapAbortCtrl = null;
      stopMapTimer();
      data.chapter_map = res;

      const curNum = Number(data && data.chapter && data.chapter.n);
      const tocEntry = bookChapters.find((c) => Number(c.n) === curNum);
      if (tocEntry && res.supported !== false) tocEntry.has_map = true;

      syncMapTabLabel();
      renderMapDrawer();
      renderTocDrawer();
      if (res.supported === false) {
        RD.toast(res.reason || '篇幅较短，建议直读原文');
      } else if (spineTab === 'toc') {
        RD.toast('知识地图已就绪，点击左侧「知识地图」查看');
      } else {
        RD.toast('知识地图已就绪');
      }
    } catch (err) {
      mapLoading = false;
      mapAbortCtrl = null;
      stopMapTimer();
      syncMapTabLabel();
      if (err && err.name === 'AbortError') {
        RD.toast('已取消生成知识地图');
      } else {
        mapError = (err && err.message) || '知识地图生成失败';
        RD.toast(mapError, true);
      }
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
    const commonRow =
      '<div class="selection-bar-row">' +
        '<span class="selection-bar-lbl">细读</span>' +
        otherTools.map((item) =>
          '<button type="button" data-action="' + RD.esc(item.action) + '">' + RD.esc(item.label) + '</button>'
        ).join('') +
        '<span class="selection-bar-sep" aria-hidden="true"></span>' +
        '<button type="button" data-action="search-related" title="在其他书中找相关内容，并按阈值选书回答">跨书找相关 ↗</button>' +
      '</div>';

    const bpRow =
      '<div class="selection-bar-row selection-bar-sub">' +
        '<span class="selection-bar-lbl">' + RD.esc(prof.name) + '</span>' +
        (defaultBp
          ? '<button type="button" class="sel-bp" data-action="diagram" data-blueprint="' + RD.esc(defaultBp.blueprint) +
              '" title="' + RD.esc(defaultBp.description || '') + '">' + RD.esc(defaultBp.label) + '</button>'
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

    bar.innerHTML = commonRow + bpRow;
  }

  function setDrawerOpen(open) {
    drawerOpen = Boolean(open);
    const drawer = RD.mount('assist-drawer');
    const grid = RD.mount('wb-grid');
    const btn = RD.mount('btn-drawer');
    const topBtn = RD.mount('btn-drawer-top');
    const reopenBtn = RD.mount('btn-drawer-reopen');
    if (drawer) drawer.hidden = !drawerOpen;
    if (grid) grid.classList.toggle('has-drawer', drawerOpen);
    if (btn) btn.setAttribute('aria-pressed', String(drawerOpen));
    if (topBtn) topBtn.setAttribute('aria-pressed', String(drawerOpen));
    if (reopenBtn) reopenBtn.hidden = drawerOpen;
    syncBackdrop();
  }

  function setSpineOpen(open) {
    showSpine = Boolean(open);
    const grid = RD.mount('wb-grid');
    const btn = RD.mount('btn-spine');
    if (grid) grid.classList.toggle('hide-spine', !showSpine);
    if (btn) btn.setAttribute('aria-pressed', String(showSpine));
    syncBackdrop();
  }

  function toggleFocusMode() {
    const isFocused = !showSpine && !drawerOpen;
    if (isFocused) {
      setSpineOpen(window.innerWidth >= 1180);
      setDrawerOpen(window.innerWidth >= 960);
    } else {
      setSpineOpen(false);
      setDrawerOpen(false);
    }
    const focused = !showSpine && !drawerOpen;
    const fBtn = RD.mount('btn-focus-mode');
    if (fBtn) {
      fBtn.setAttribute('aria-pressed', String(focused));
      fBtn.textContent = focused ? '退出专注模式（恢复双栏）' : '专注阅读（隐藏左右栏）';
    }
  }

  function setSpineTab(tab) {
    spineTab = tab;
    showMap = tab === 'map';
    showTerms = tab === 'terms';
    const bMap = RD.mount('btn-map');
    const bTerms = RD.mount('btn-terms');
    const bToc = RD.mount('btn-toc');
    if (bMap) bMap.setAttribute('aria-pressed', String(tab === 'map'));
    if (bTerms) bTerms.setAttribute('aria-pressed', String(tab === 'terms'));
    if (bToc) bToc.setAttribute('aria-pressed', String(tab === 'toc'));
    renderMapDrawer();
    if (tab === 'toc') renderTocDrawer();
  }

  function highlightActiveTool() {
    syncToolButtons();
  }

  function focusBlock(bid, instant) {
    const el = document.getElementById('blk-' + bid);
    if (!el) return;
    el.scrollIntoView({ behavior: instant ? 'auto' : 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 2000);
  }

  function renderFootnoteReturnBar() {
    const bar = RD.mount('fn-return-bar');
    if (!bar) return;
    if (!footnoteReturn) {
      bar.hidden = true;
      bar.innerHTML = '';
      return;
    }
    bar.hidden = false;
    bar.innerHTML =
      '<span class="fn-ret-label">正在查看注 <b>[' + RD.esc(footnoteReturn.marker || '') + ']</b></span>' +
      '<button type="button" class="fn-ret-btn" id="btn-fn-return">↩ 返回正文</button>' +
      '<button type="button" class="fn-ret-close" id="btn-fn-return-close" aria-label="关闭返回条" title="关闭">×</button>';
  }

  function hideFootnoteTip() {
    const pop = RD.mount('fn-tip-popover');
    if (!pop) return;
    closePopoverSafe(pop);
  }

  function clearActiveFootnoteDef() {
    const reader = RD.mount('reader-body');
    if (reader) reader.querySelectorAll('.reader-block.fn-active-def').forEach((n) => n.classList.remove('fn-active-def'));
  }

  function jumpToFootnote(refBtn) {
    hideFootnoteTip();
    const refId = refBtn.getAttribute('data-fn-ref') || '';
    const marker = refBtn.getAttribute('data-fn-marker') || '';
    const targetBid = refBtn.getAttribute('data-fn-target') || '';
    const sourceBlk = refBtn.closest('.reader-block[data-block]');
    const sourceBid = sourceBlk ? sourceBlk.getAttribute('data-block') : '';
    if (!targetBid) {
      showFootnoteTip(refBtn);
      return;
    }
    clearActiveFootnoteDef();
    const targetEl = document.getElementById('blk-' + targetBid);
    if (targetEl) targetEl.classList.add('fn-active-def');
    footnoteReturn = { refId, sourceBlockId: sourceBid, targetBlockId: targetBid, marker };
    renderFootnoteReturnBar();
    focusBlock(targetBid);
  }

  function returnFromFootnote(defBlockId) {
    let refEl = null;
    let blkEl = null;
    if (footnoteReturn && (!defBlockId || footnoteReturn.targetBlockId === defBlockId)) {
      refEl = footnoteReturn.refId ? document.getElementById(footnoteReturn.refId) : null;
      blkEl = footnoteReturn.sourceBlockId ? document.getElementById('blk-' + footnoteReturn.sourceBlockId) : null;
    } else if (defBlockId) {
      const reader = RD.mount('reader-body');
      if (reader) {
        refEl = Array.from(reader.querySelectorAll('.fn-ref[data-fn-target]'))
          .find((el) => el.getAttribute('data-fn-target') === defBlockId) || null;
      }
      const defEl = document.getElementById('blk-' + defBlockId);
      const backBtn = defEl ? defEl.querySelector('[data-fn-back-block]') : null;
      const fallbackBid = backBtn ? backBtn.getAttribute('data-fn-back-block') : '';
      blkEl = (refEl && refEl.closest('.reader-block')) || (fallbackBid ? document.getElementById('blk-' + fallbackBid) : null);
    }
    const scrollTarget = refEl || blkEl;
    if (scrollTarget) {
      scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    if (blkEl) {
      blkEl.classList.add('flash');
      setTimeout(() => blkEl.classList.remove('flash'), 2000);
    }
    if (refEl) {
      refEl.classList.add('fn-return-flash');
      setTimeout(() => refEl.classList.remove('fn-return-flash'), 2200);
    }
    clearActiveFootnoteDef();
    footnoteReturn = null;
    renderFootnoteReturnBar();
  }

  function showFootnoteTip(refBtn) {
    const pop = RD.mount('fn-tip-popover');
    if (!pop) return;
    lastFnTrigger = refBtn;
    const marker = refBtn.getAttribute('data-fn-marker') || '';
    const status = refBtn.getAttribute('data-fn-status') || 'unresolved';
    const targetFile = refBtn.getAttribute('data-fn-target-file') || '';
    const targetChN = refBtn.getAttribute('data-fn-target-ch-n') || '';
    const targetChTitle = refBtn.getAttribute('data-fn-target-ch-title') || '';
    const targetBlock = refBtn.getAttribute('data-fn-target-block') || '';
    const noteChapters = (data && data.footnotes && data.footnotes.note_chapters) || [];

    let bodyHtml = '';
    let actionsHtml = '';
    if (status === 'external_chapter' && targetChN) {
      bodyHtml = '<p>该条注释位于本书另一章节「第 ' + RD.esc(targetChN) + ' 章 · ' + RD.esc(targetChTitle || '注释') + '」。</p>';
      const href = buildExternalFootnoteHref(targetChN, targetBlock, marker);
      const label = targetBlock
        ? ('新标签直达注 [' + marker + '] ↗')
        : ('打开第 ' + targetChN + ' 章 · ' + (targetChTitle || '注释') + ' ↗');
      actionsHtml = '<a class="fn-tip-link" href="' + href + '" target="_blank" rel="noopener">' + RD.esc(label) + '</a>';
    } else if (status === 'external_candidate' && (noteChapters.length || (targetChN && targetBlock))) {
      if (targetChN && targetBlock) {
        bodyHtml = '<p>正文与注释分属不同文件，已在「第 ' + RD.esc(targetChN) + ' 章 · ' + RD.esc(targetChTitle || '注释') + '」中定位到对应注文：</p>';
        const href = buildExternalFootnoteHref(targetChN, targetBlock, marker);
        actionsHtml =
          '<a class="fn-tip-link" href="' + href + '" target="_blank" rel="noopener">' +
            RD.esc('新标签直达注 [' + marker + ']（第 ' + targetChN + ' 章）↗') +
          '</a>';
      } else {
        bodyHtml = '<p>当前章节未包含该条注文，正文与注释可能分属两个文件。可在以下注释章节中查看：</p>';
        actionsHtml = noteChapters.slice(0, 3).map((ch) =>
          '<a class="fn-tip-link" href="' + RD.chapterHref(UID, ch.n) + '" target="_blank" rel="noopener">' +
            '查看第 ' + RD.esc(ch.n) + ' 章 · ' + RD.esc(ch.title) + ' ↗' +
          '</a>'
        ).join('');
      }
    } else {
      bodyHtml =
        '<p>当前章节末尾未找到对应注释，正文与注释可能分属不同文件（例如汇总在后续小节末尾或全书注释页），或原书转档时未保留。</p>' +
        (targetFile ? '<p class="fn-tip-meta">原链接指向：<code>' + RD.esc(targetFile) + '</code></p>' : '');
      actionsHtml = '<button type="button" class="fn-tip-toc" data-fn-open-toc>查看全书目录</button>';
    }

    pop.innerHTML =
      '<div class="fn-tip-head">' +
        '<strong>注 [' + RD.esc(marker) + '] 不在本章中</strong>' +
        '<button type="button" class="fn-tip-close" id="btn-fn-tip-close" aria-label="关闭提示">×</button>' +
      '</div>' +
      '<div class="fn-tip-body">' + bodyHtml + '</div>' +
      '<div class="fn-tip-actions">' + actionsHtml + '</div>';

    openPopoverSafe(pop);
    const rect = refBtn.getBoundingClientRect();
    const popW = pop.offsetWidth || 320;
    const popH = pop.offsetHeight || 130;
    const left = Math.min(window.innerWidth - popW - 12, Math.max(12, rect.left + rect.width / 2 - popW / 2));
    const spaceBelow = window.innerHeight - rect.bottom;
    const top = spaceBelow >= popH + 16 ? (rect.bottom + 8) : Math.max(64, rect.top - popH - 8);
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  function jumpToHash(initial) {
    const h = (location.hash || '').replace(/^#/, '');
    if (!h.startsWith('blk-')) return;
    const bid = h.slice(4);
    const blocks = (data && data.blocks) || [];
    const idx = blocks.findIndex((b) => b.id === bid);
    const targetEl = document.getElementById('blk-' + bid);
    if (arrival.from === 'footnote' && !arrival.dismissed && targetEl) {
      clearActiveFootnoteDef();
      targetEl.classList.add('fn-active-def');
    }
    if (arrivalActive() && idx >= 0 && idx <= 1) {
      window.scrollTo({ top: 0, behavior: initial ? 'auto' : 'smooth' });
      if (targetEl) {
        targetEl.classList.add('flash');
        setTimeout(() => targetEl.classList.remove('flash'), 2000);
      }
      return;
    }
    focusBlock(bid, Boolean(initial));
  }

  function updateScrollProgress() {
    scrollRaf = 0;
    if (!window.CSS?.supports?.('animation-timeline', 'scroll()')) {
      const bar = RD.mount('reading-progress-bar');
      if (bar) {
        const doc = document.documentElement;
        const max = Math.max(1, doc.scrollHeight - window.innerHeight);
        const ratio = Math.min(1, Math.max(0, window.scrollY / max));
        bar.style.transform = 'scaleX(' + ratio.toFixed(4) + ')';
      }
    }
    const mapBox = RD.mount('map-drawer');
    if (!mapBox || mapBox.hidden) return;
    const leaves = mapBox.querySelectorAll('.tree-leaf[data-jump]');
    if (!leaves.length) return;
    let activeBid = '';
    const mid = window.innerHeight * 0.34;
    for (const leaf of leaves) {
      const bid = leaf.getAttribute('data-jump');
      const blk = bid ? document.getElementById('blk-' + bid) : null;
      if (!blk) continue;
      if (blk.getBoundingClientRect().top <= mid) activeBid = bid;
    }
    leaves.forEach((leaf) => {
      leaf.classList.toggle('is-reading', Boolean(activeBid && leaf.getAttribute('data-jump') === activeBid));
    });
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
    // 预渲染隐藏靠 .selection-bar { display: none }，不靠 [hidden]；
    // [hidden] { display: none !important } 会压过 .selection-bar.show，必须显式清掉。
    bar.hidden = false;
    bar.classList.add('show');
    const barH = bar.offsetHeight || 72;
    const barW = bar.offsetWidth || 380;
    const top = rect.top > barH + 72 ? (rect.top - barH - 10) : Math.min(window.innerHeight - barH - 12, rect.bottom + 10);
    const left = Math.min(window.innerWidth - barW - 14, Math.max(12, rect.left + (rect.width - barW) / 2));
    bar.style.top = Math.max(58, top) + 'px';
    bar.style.left = left + 'px';
  }

  function hideSelectionBar() {
    const bar = RD.mount('selection-bar');
    bar.classList.remove('show');
    bar.hidden = true;
  }

  function bindEvents() {
    RD.mount('btn-map').addEventListener('click', () => setSpineTab('map'));
    RD.mount('btn-terms').addEventListener('click', () => setSpineTab('terms'));
    const tocBtn = RD.mount('btn-toc');
    if (tocBtn) tocBtn.addEventListener('click', () => setSpineTab('toc'));

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
    const reopenBtn = RD.mount('btn-drawer-reopen');
    if (reopenBtn) reopenBtn.addEventListener('click', () => setDrawerOpen(true));
    RD.mount('btn-pin').addEventListener('click', pinCurrentResult);

    const quickBp = RD.mount('btn-quick-first-bp');
    if (quickBp) {
      quickBp.addEventListener('click', () => {
        const prof = activeProfile();
        const firstBp = prof.blueprints && prof.blueprints[0];
        if (firstBp) {
          scopeMode = 'chapter';
          updateScopeIndicator();
          runAssist('diagram', '', firstBp.blueprint, 'chapter', false);
        }
      });
    }

    const tabCh = RD.mount('tab-scope-chapter');
    if (tabCh) {
      tabCh.addEventListener('click', () => {
        scopeMode = 'chapter';
        syncToolButtons();
      });
    }
    const tabSel = RD.mount('tab-scope-selection');
    if (tabSel) {
      tabSel.addEventListener('click', () => {
        if (currentSelection.block_ids && currentSelection.block_ids.length) {
          scopeMode = 'selection';
          syncToolButtons();
        } else {
          RD.toast('请先在正文划选段落或点击段尾 ✦');
        }
      });
    }

    const strip = RD.mount('wb-profile-strip');
    if (strip) {
      strip.addEventListener('click', (e) => {
        const hasSel = Boolean(currentSelection.block_ids && currentSelection.block_ids.length);
        const activeScope = (scopeMode === 'selection' && hasSel) ? 'selection' : 'chapter';
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
      const hasSel = Boolean(currentSelection.block_ids && currentSelection.block_ids.length);
      const askScope = (scopeMode === 'selection' && hasSel) ? 'selection' : 'chapter';
      input.value = '';
      runAssist('ask', q, '', askScope, false);
    });

    const reader = RD.mount('reader-body');
    reader.addEventListener('mouseup', (e) => {
      if (e.target && e.target.closest('.fn-ref, .fn-back-inline')) return;
      setTimeout(() => {
        const cap = captureSelection();
        if (!cap) return;
        currentSelection = { block_ids: cap.block_ids, text: cap.text };
        scopeMode = 'selection';
        updateScopeIndicator();
        positionSelectionBar(cap.rect);
      }, 10);
    });

    reader.addEventListener('click', async (e) => {
      const fnRefBtn = e.target.closest('.fn-ref');
      if (fnRefBtn) {
        e.stopPropagation();
        e.preventDefault();
        hideSelectionBar();
        if (fnRefBtn.getAttribute('data-fn-target')) {
          jumpToFootnote(fnRefBtn);
        } else {
          showFootnoteTip(fnRefBtn);
        }
        return;
      }
      const fnBackBtn = e.target.closest('.fn-back-inline');
      if (fnBackBtn) {
        e.stopPropagation();
        const defBlk = fnBackBtn.closest('.reader-block[data-block]');
        returnFromFootnote(defBlk ? defBlk.getAttribute('data-block') : '');
        return;
      }
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
        scopeMode = 'selection';
        reader.querySelectorAll('.reader-block.active').forEach((n) => n.classList.remove('active'));
        trig.closest('.reader-block').classList.add('active');
        updateScopeIndicator();
        positionSelectionBar(trig.getBoundingClientRect());
      }
    });

    if (!globalBound) {
      globalBound = true;
      const spineBtn = RD.mount('btn-spine');
      if (spineBtn) spineBtn.addEventListener('click', () => setSpineOpen(!showSpine));

      const drawerTopBtn = RD.mount('btn-drawer-top');
      if (drawerTopBtn) drawerTopBtn.addEventListener('click', () => setDrawerOpen(!drawerOpen));

      const backdrop = RD.mount('desk-backdrop');
      if (backdrop) {
        backdrop.addEventListener('click', () => {
          if (window.innerWidth <= 1180) setSpineOpen(false);
          if (window.innerWidth <= 920) setDrawerOpen(false);
        });
      }

      const supportsPopover = Object.hasOwn(HTMLElement.prototype, 'popover');
      const typoBtn = RD.mount('btn-typo');
      const typoPop = RD.mount('typo-popover');
      if (typoBtn && typoPop) {
        if (supportsPopover) {
          typoPop.addEventListener('toggle', (e) => {
            typoBtn.setAttribute('aria-expanded', String(e.newState === 'open'));
          });
        } else {
          typoBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = typoPop.classList.contains(':popover-open');
            if (isOpen) closePopoverSafe(typoPop);
            else openPopoverSafe(typoPop);
            typoBtn.setAttribute('aria-expanded', String(!isOpen));
          });
        }
        typoPop.addEventListener('click', (e) => {
          const fsBtn = e.target.closest('[data-font-size]');
          if (fsBtn) {
            setPref('reading-font-size', fsBtn.getAttribute('data-font-size'));
            applyTypographyPrefs();
            return;
          }
          const msBtn = e.target.closest('[data-measure]');
          if (msBtn) {
            setPref('reading-measure', msBtn.getAttribute('data-measure'));
            applyTypographyPrefs();
            return;
          }
          if (e.target.closest('#btn-focus-mode')) {
            toggleFocusMode();
            closePopoverSafe(typoPop);
            typoBtn.setAttribute('aria-expanded', 'false');
          }
        });
      }

      document.addEventListener('mousedown', (e) => {
        if (!supportsPopover) {
          if (typoPop && typoPop.classList.contains(':popover-open') && !typoPop.contains(e.target) && !e.target.closest('#btn-typo')) {
            closePopoverSafe(typoPop);
            if (typoBtn) typoBtn.setAttribute('aria-expanded', 'false');
          }
          const fnPop = RD.mount('fn-tip-popover');
          if (fnPop && fnPop.classList.contains(':popover-open') && !fnPop.contains(e.target) && !e.target.closest('.fn-ref')) {
            hideFootnoteTip();
          }
        }
        const bar = RD.mount('selection-bar');
        const curReader = RD.mount('reader-body');
        if (bar.contains(e.target) || e.target.closest('[data-trigger]')) return;
        if (!curReader || !curReader.contains(e.target)) hideSelectionBar();
        else setTimeout(() => { if (!window.getSelection() || window.getSelection().isCollapsed) hideSelectionBar(); }, 10);
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          hideSelectionBar();
          if (!supportsPopover) {
            const fnPop = RD.mount('fn-tip-popover');
            const fnWasOpen = fnPop && fnPop.classList.contains(':popover-open');
            hideFootnoteTip();
            if (fnWasOpen && lastFnTrigger && typeof lastFnTrigger.focus === 'function') lastFnTrigger.focus();
            if (typoPop && typoPop.classList.contains(':popover-open')) {
              closePopoverSafe(typoPop);
              if (typoBtn) {
                typoBtn.setAttribute('aria-expanded', 'false');
                typoBtn.focus();
              }
            }
          }
          if (footnoteReturn) {
            clearActiveFootnoteDef();
            footnoteReturn = null;
            renderFootnoteReturnBar();
          }
          return;
        }
        if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === '[') {
          e.preventDefault();
          setSpineOpen(!showSpine);
        } else if (e.key === ']') {
          e.preventDefault();
          setDrawerOpen(!drawerOpen);
        } else if (e.key === 'f' || e.key === 'F') {
          e.preventDefault();
          toggleFocusMode();
        }
      });

      const fnRetBar = RD.mount('fn-return-bar');
      if (fnRetBar) {
        fnRetBar.addEventListener('click', (e) => {
          if (e.target.closest('#btn-fn-return')) {
            returnFromFootnote(footnoteReturn ? footnoteReturn.targetBlockId : '');
            return;
          }
          if (e.target.closest('#btn-fn-return-close')) {
            clearActiveFootnoteDef();
            footnoteReturn = null;
            renderFootnoteReturnBar();
          }
        });
      }

      const fnTipPop = RD.mount('fn-tip-popover');
      if (fnTipPop) {
        fnTipPop.addEventListener('click', (e) => {
          if (e.target.closest('#btn-fn-tip-close')) {
            hideFootnoteTip();
            if (lastFnTrigger && typeof lastFnTrigger.focus === 'function') lastFnTrigger.focus();
            return;
          }
          if (e.target.closest('[data-fn-open-toc]')) {
            hideFootnoteTip();
            setSpineOpen(true);
            setSpineTab('toc');
          }
        });
      }

      window.addEventListener('scroll', () => {
        if (!scrollRaf) scrollRaf = requestAnimationFrame(updateScrollProgress);
      }, { passive: true });

      window.addEventListener('resize', syncBackdrop);

      RD.mount('selection-bar').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        const bp = btn.getAttribute('data-blueprint') || '';
        hideSelectionBar();
        scopeMode = 'selection';
        updateScopeIndicator();
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

    bar.hidden = false;
    const scopeText = scope === 'chapter'
      ? ('全章 ' + RD.fmt(covered) + ' 段' + (sampled ? ' · 均匀骨架' : '') + (cached ? ' · 已缓存' : ''))
      : ('已选 ' + (formatSelectionSpan(selIds) || '片段'));

    const switchScopeBtn = scope === 'selection'
      ? '<button type="button" class="drawer-inline-act" data-switch-scope="chapter">改按全章</button>'
      : (currentSelection.block_ids && currentSelection.block_ids.length
          ? '<button type="button" class="drawer-inline-act" data-switch-scope="selection">仅看所选段</button>'
          : '');

    const regenBtn = (scope === 'chapter' || cached)
      ? '<button type="button" class="drawer-inline-act" data-regen-chapter title="忽略缓存重新生成">↻ 重生成</button>'
      : '';

    bar.innerHTML =
      '<span class="drawer-scope-txt' + (scope === 'selection' ? ' is-sel' : '') + '">' + RD.esc(scopeText) + '</span>' +
      '<span class="drawer-scope-acts">' + switchScopeBtn + regenBtn + '</span>';

    const swBtn = bar.querySelector('[data-switch-scope]');
    if (swBtn) {
      swBtn.addEventListener('click', () => {
        const nextScope = swBtn.getAttribute('data-switch-scope');
        if (nextScope === 'chapter') clearSelectionScope();
        runAssist(action, (lastResult && lastResult.question) || '', blueprint, nextScope, false);
      });
    }
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
    const scope = scopeOverride || ((scopeMode === 'selection' && currentSelection.block_ids && currentSelection.block_ids.length) ? 'selection' : 'chapter');
    const chosenBp = blueprint || (action === 'diagram' && prof.blueprints && prof.blueprints[0] ? prof.blueprints[0].blueprint : '');
    const bpMeta = (prof.blueprints || []).find((b) => b.blueprint === chosenBp);
    scopeMode = scope;
    activeToolKey = action === 'diagram' && chosenBp ? ('diagram:' + chosenBp) : ('ext:' + action);
    loadingToolKey = activeToolKey;
    const requestedCacheKey = toolCacheKey(prof.read_type, action, chosenBp);
    syncToolButtons();
    lastResult = null;
    setDrawerOpen(true);
    RD.mount('btn-pin').disabled = true;
    RD.mount('drawer-empty').hidden = true;

    const titleBase = (action === 'diagram' && bpMeta) ? bpMeta.label : (RD.actionNames[action] || action);
    RD.mount('drawer-title').textContent = titleBase;
    renderDrawerScopeBar({
      scope,
      action,
      blueprint: chosenBp,
      block_ids: scope === 'chapter' ? [] : currentSelection.block_ids,
      covered_blocks: (data && data.blocks && data.blocks.length) || 0,
      sampled: false,
      cached: false,
    });

    const quoteEl = RD.mount('drawer-quote');
    if (scope === 'chapter') {
      quoteEl.hidden = true;
      quoteEl.textContent = '';
    } else {
      quoteEl.hidden = false;
      quoteEl.textContent = currentSelection.text || ('段落 ' + currentSelection.block_ids.join(', '));
    }
    RD.mount('drawer-diagram').innerHTML = '';
    RD.mount('drawer-answer').innerHTML = '<div class="drawer-thinking">正在细读与组织回答…</div>';
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
        applyDoneResult(json, requestedCacheKey);
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
            applyDoneResult(payload, requestedCacheKey);
          } else if (evName === 'error') {
            throw new Error(payload.error || '伴读调用失败');
          }
        }
      }
      if (loadingToolKey === activeToolKey) {
        loadingToolKey = '';
        syncToolButtons();
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      loadingToolKey = '';
      syncToolButtons();
      RD.mount('drawer-answer').innerHTML = '<p class="job-error">' + RD.esc(err.message) + '</p>';
      RD.toast(err.message, true);
    }
  }

  function isRedundantSummary(summary, readingNote) {
    const clean = (s) => String(s || '')
      .replace(/\[(原文|补充|举例)\]/g, '')
      .replace(/[\s，。、；：！？“”"'‘’（）()·—\-]/g, '')
      .toLowerCase();
    const a = clean(summary);
    const b = clean(readingNote);
    if (!a) return true;
    if (!b) return false;
    if (a === b || b.includes(a)) return true;
    if (a.includes(b) && a.length <= b.length * 1.45) return true;
    if (a.length < 4 || b.length < 4) return false;
    const bigrams = (str) => {
      const counts = new Map();
      for (let i = 0; i < str.length - 1; i++) {
        const bg = str.slice(i, i + 2);
        counts.set(bg, (counts.get(bg) || 0) + 1);
      }
      return counts;
    };
    const ma = bigrams(a);
    const mb = bigrams(b);
    let hit = 0;
    ma.forEach((cnt, bg) => {
      if (mb.has(bg)) hit += Math.min(cnt, mb.get(bg));
    });
    const dice = (2 * hit) / ((a.length - 1) + (b.length - 1));
    return dice >= 0.68 && a.length <= b.length * 1.65;
  }

  function applyDoneResult(payload, requestedCacheKey) {
    lastResult = payload;
    loadingToolKey = '';
    if (
      payload &&
      payload.scope === 'chapter' &&
      !payload.question &&
      (payload.figure || payload.cached)
    ) {
      const prof = activeProfile();
      if (requestedCacheKey) cachedToolKeys.add(requestedCacheKey);
      const derivedKey = toolCacheKey(prof.read_type, payload.action, payload.blueprint);
      if (derivedKey) cachedToolKeys.add(derivedKey);
    }
    syncToolButtons();
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
    const noteText = payload.figure && payload.figure.reading_note;
    const contentText = String(payload.content || '').trim();
    if (contentText && !(payload.figure && isRedundantSummary(contentText, noteText))) {
      RD.mount('drawer-answer').innerHTML = RD.renderMarkdown(contentText);
    } else if (payload.figure) {
      RD.mount('drawer-answer').innerHTML = '';
    }
    const metaParts = [];
    if (payload.cached) metaParts.push('命中全章缓存');
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
      for (const rw of (fig.data.rows || [])) {
        const hit = pick(rw.blocks);
        if (hit) return hit;
        for (const cl of (rw.cells || [])) { if (cl) { const ch = pick(cl.blocks); if (ch) return ch; } }
      }
      for (const af of (fig.data.aftermath || [])) { const hit = pick(af.blocks); if (hit) return hit; }
      for (const qd of (fig.data.quadrants || [])) {
        for (const it of (qd.items || [])) { const hit = pick(it.blocks); if (hit) return hit; }
      }
    }
    if (fig && Array.isArray(fig.sections)) {
      for (const sec of fig.sections) {
        for (const it of (sec && sec.items) || []) { const hit = pick(it.blocks); if (hit) return hit; }
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
      justPinnedIds.add(saved.id);
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

  async function loadBookToc() {
    try {
      const res = await RD.api('/api/book/' + encodeURIComponent(UID));
      bookChapters = (res && res.chapters) || [];
      renderTocDrawer();
      renderChapterPager();
    } catch (e) {
      // Non-critical enhancement; ignore quietly if book TOC unavailable.
    }
  }

  RD.ready(async () => {
    RD.night();
    applyTypographyPrefs();
    try {
      data = await RD.api('/api/chapter/' + encodeURIComponent(UID) + '/' + encodeURIComponent(N));
    } catch (err) {
      RD.fail(RD.mount('wb-root'), err.message);
      return;
    }
    cachedToolKeys = new Set(Array.isArray(data && data.cached_tools) ? data.cached_tools : []);
    const cmap = data && data.chapter_map;
    const hasMap = Boolean(cmap && cmap.supported !== false && (cmap.sections || []).length);
    spineTab = hasMap ? 'map' : 'toc';
    showMap = spineTab === 'map';
    showTerms = false;

    renderShell();
    renderMapDrawer();
    renderBlocks();
    renderSelectionBarButtons();
    bindEvents();
    jumpToHash(true);
    updateScrollProgress();
    window.addEventListener('hashchange', () => jumpToHash(false));
    loadBookToc();
  });
})();
