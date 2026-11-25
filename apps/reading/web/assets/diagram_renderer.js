/* 伴读 · 五步防重叠确定性蓝图渲染引擎与全章树形导图 */
(function () {
  const RD = (window.RD = window.RD || {});

  let supportsDisplayTransition;
  let lastZoomTrigger = null;

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

  async function closeZoomModal(modal) {
    if (!modal) return;
    if (!supportsTopLayerExit()) {
      modal.setAttribute('data-closing', '');
      const animations = modal.getAnimations ? modal.getAnimations({ subtree: true }) : [];
      if (animations.length > 0) {
        await Promise.race([
          Promise.allSettled(animations.map((a) => a.finished)),
          new Promise((r) => setTimeout(r, 400)),
        ]);
      }
      if (!modal.hasAttribute('data-closing')) return;
      modal.removeAttribute('data-closing');
    }
    if (typeof modal.close === 'function') {
      if (modal.open) modal.close();
    } else {
      modal.classList.remove('open');
      if (lastZoomTrigger && typeof lastZoomTrigger.focus === 'function') lastZoomTrigger.focus();
    }
  }

  const ROLE_ZH = {
    premise: '前提',
    axiom: '公理',
    inference: '推论',
    lemma: '引理',
    step: '步骤',
    rebuttal: '反驳/风险',
    conclusion: '结论',
    theorem: '定理',
    condition: '条件',
    // 历史·权力与阵营
    sovereign: '中枢/君主',
    faction: '核心机构/派系',
    ally: '盟友/根基',
    rival: '敌对/制衡',
    constraint: '条约/制度约束',
    // 历史·因果演进
    structural: '深层结构',
    trigger: '触发事件',
    mechanism: '催化机制',
    turning_point: '关键转折',
    consequence: '长期后果',
    // 历史·空间态势
    capital: '都城中枢',
    fortress: '要塞关隘',
    corridor: '通道平原',
    neutral: '中立缓冲',
    front: '外线战区',
  };

  const DAG_BLUEPRINTS = new Set([
    'argument_map',
    'lineage_dag',
    'power_hierarchy',
    'spatial_route',
    'historical_causality',
    'relation_graph',
    'flow_swimlane',
    'decision_tree',
    'derivation_dag',
    'system_flow',
    'causal_loop',
  ]);
  const MATRIX_BLUEPRINTS = new Set(['term_contrast', 'concept_matrix', 'element_matrix']);
  const CURVE_BLUEPRINTS = new Set(['tension_curve', 'sensitivity_chart', 'metric_chart']);

  function esc(s) {
    return RD.esc ? RD.esc(s) : String(s == null ? '' : s);
  }

  function renderAnchorPills(blocks, maxShow) {
    const list = blocks || [];
    if (!maxShow || list.length <= maxShow) {
      return list
        .map(
          (b) =>
            '<button type="button" class="blk-anchor" data-jump="' +
            esc(b) +
            '" title="点击定位原文 ' +
            esc(b) +
            '">↗ ' +
            esc(b) +
            '</button>'
        )
        .join(' ');
    }
    const head = list
      .slice(0, maxShow)
      .map(
        (b) =>
          '<button type="button" class="blk-anchor" data-jump="' +
          esc(b) +
          '" title="点击定位原文 ' +
          esc(b) +
          '">↗ ' +
          esc(b) +
          '</button>'
      )
      .join(' ');
    const rest = list.slice(maxShow);
    return (
      head +
      ' <button type="button" class="blk-anchor blk-anchor-more" data-jump="' +
      esc(rest[0]) +
      '" title="全部块号：' +
      esc(list.join(', ')) +
      '">+' +
      rest.length +
      '</button>'
    );
  }

  function renderSrcTag(source) {
    if (!source) return '';
    return source === '原文'
      ? '<span class="src-tag tag-src">[原文]</span>'
      : '<span class="src-tag tag-ext">[' + esc(source) + ']</span>';
  }

  function rectsOverlap(a, b, pad) {
    const p = pad || 0;
    return !(
      a.x + a.w + p <= b.x ||
      b.x + b.w + p <= a.x ||
      a.y + a.h + p <= b.y ||
      b.y + b.h + p <= a.y
    );
  }

  function ensureMeasureStage() {
    let stage = document.getElementById('measure-stage');
    if (!stage) {
      stage = document.createElement('div');
      stage.id = 'measure-stage';
      stage.setAttribute('aria-hidden', 'true');
      document.body.appendChild(stage);
    }
    return stage;
  }

  function measureNodeSizes(nodes, cardWidth, expandAll, maxAnchors) {
    const stage = ensureMeasureStage();
    const sizes = new Map();
    stage.style.width = cardWidth + 'px';
    stage.innerHTML = nodes
      .map(
        (n) =>
          '<div class="dag-node role-' +
          esc(n.role || 'inference') +
          (expandAll && n.detail ? ' expanded' : '') +
          '" style="position:relative;width:' +
          cardWidth +
          'px">' +
          '<div class="dag-node-role"><span>' +
          esc(ROLE_ZH[n.role] || n.role || '节点') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="dag-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.detail
            ? '<div class="dag-node-more">' +
              (expandAll ? '▴ 收起' : '▾ 查看展开') +
              '</div><div class="dag-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, maxAnchors) +
          '</div></div>'
      )
      .join('');
    const els = stage.querySelectorAll('.dag-node');
    nodes.forEach((n, idx) => {
      const rect = els[idx] ? els[idx].getBoundingClientRect() : null;
      if (rect && rect.height > 20) {
        sizes.set(n.id, { w: Math.ceil(rect.width), h: Math.ceil(rect.height) });
      } else {
        const chars = String(n.label || '').length;
        const lines = Math.max(1, Math.ceil((chars * 12.5) / Math.max(80, cardWidth - 24)));
        const detH = expandAll && n.detail ? Math.ceil(String(n.detail).length / 12) * 16 + 12 : 0;
        sizes.set(n.id, { w: cardWidth, h: 58 + lines * 18 + (n.detail ? 16 : 0) + detH });
      }
    });
    stage.innerHTML = '';
    return sizes;
  }

  function measureLabelSize(text) {
    if (!text) return { w: 0, h: 0 };
    const stage = ensureMeasureStage();
    stage.style.width = 'auto';
    stage.innerHTML = '<span class="dag-edge-label" style="position:relative">' + esc(text) + '</span>';
    const el = stage.firstElementChild;
    const r = el ? el.getBoundingClientRect() : null;
    stage.innerHTML = '';
    if (r && r.width > 4) return { w: Math.ceil(r.width) + 2, h: Math.ceil(r.height) + 2 };
    return { w: Math.max(36, text.length * 11 + 14), h: 20 };
  }

  function buildRoundedOrthogonalPath(pts, radius) {
    if (pts.length < 2) return '';
    const rad = radius || 8;
    let d = 'M ' + pts[0].x + ',' + pts[0].y;
    for (let i = 1; i < pts.length - 1; i++) {
      const p0 = pts[i - 1];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const v1x = p1.x - p0.x;
      const v1y = p1.y - p0.y;
      const v2x = p2.x - p1.x;
      const v2y = p2.y - p1.y;
      const len1 = Math.hypot(v1x, v1y);
      const len2 = Math.hypot(v2x, v2y);
      const r = Math.min(rad, len1 / 2, len2 / 2);
      if (r < 1) {
        d += ' L ' + p1.x + ',' + p1.y;
        continue;
      }
      const ax = p1.x - (v1x / len1) * r;
      const ay = p1.y - (v1y / len1) * r;
      const bx = p1.x + (v2x / len2) * r;
      const by = p1.y + (v2y / len2) * r;
      d += ' L ' + ax.toFixed(1) + ',' + ay.toFixed(1) + ' Q ' + p1.x + ',' + p1.y + ' ' + bx.toFixed(1) + ',' + by.toFixed(1);
    }
    const last = pts[pts.length - 1];
    d += ' L ' + last.x + ',' + last.y;
    return d;
  }

  let markerSeq = 0;

  const DAG_ROOT_ROLES = new Set(['premise', 'axiom', 'condition', 'sovereign', 'structural', 'trigger', 'capital']);

  function isNegativeEdgeLabel(lbl) {
    if (!lbl) return false;
    return (
      lbl.startsWith('-') ||
      lbl.includes('批') ||
      lbl.includes('拒') ||
      lbl.includes('排除') ||
      lbl.includes('切断') ||
      lbl.includes('削弱') ||
      lbl.includes('阻碍') ||
      lbl.includes('冲突') ||
      lbl.includes('敌对') ||
      lbl.includes('反噬')
    );
  }

  function prepareDagEdges(rawEdges, idMap) {
    const valid = (rawEdges || []).filter((e) => e && idMap.has(e.from) && idMap.has(e.to));
    const merged = [];
    const used = new Array(valid.length).fill(false);

    for (let i = 0; i < valid.length; i++) {
      if (used[i]) continue;
      used[i] = true;
      const e = valid[i];
      let rev = null;
      if (e.from !== e.to) {
        for (let j = i + 1; j < valid.length; j++) {
          if (!used[j] && valid[j].from === e.to && valid[j].to === e.from) {
            used[j] = true;
            rev = valid[j];
            break;
          }
        }
      }
      const l1 = String(e.label || '').trim();
      const l2 = rev ? String(rev.label || '').trim() : '';
      const label = l2 && l2 !== l1 ? (l1 ? l1 + ' / ' + l2 : l2) : l1;
      const notes = [e.note, rev && rev.note].filter(Boolean);
      const relType = String(e.relation_type || (rev && rev.relation_type) || '').trim();
      merged.push({
        idx: merged.length,
        from: e.from,
        to: e.to,
        label,
        note: notes.join('；'),
        relationType: relType,
        bidirectional: Boolean(rev) || relType === 'balance',
        isSelfLoop: e.from === e.to,
        isCycleBack: e.from === e.to,
        isNeg: relType === 'hostile' || isNegativeEdgeLabel(l1) || isNegativeEdgeLabel(l2),
      });
    }
    return merged;
  }

  function assignDagRanks(nodes, idMap, mergedEdges) {
    const rawIn = new Map(nodes.map((n) => [n.id, 0]));
    const rawOut = new Map(nodes.map((n) => [n.id, []]));
    mergedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      rawOut.get(e.from).push(e);
      rawIn.set(e.to, (rawIn.get(e.to) || 0) + 1);
    });

    const dfsOrder = nodes
      .map((raw) => idMap.get(raw.id))
      .sort((a, b) => {
        const ai0 = (rawIn.get(a.id) || 0) === 0 ? 0 : 1;
        const bi0 = (rawIn.get(b.id) || 0) === 0 ? 0 : 1;
        if (ai0 !== bi0) return ai0 - bi0;
        const ar = DAG_ROOT_ROLES.has(a.role) ? 0 : 1;
        const br = DAG_ROOT_ROLES.has(b.role) ? 0 : 1;
        if (ar !== br) return ar - br;
        const ad = (rawIn.get(a.id) || 0) - rawOut.get(a.id).length;
        const bd = (rawIn.get(b.id) || 0) - rawOut.get(b.id).length;
        if (ad !== bd) return ad - bd;
        return a.idx - b.idx;
      });

    // DFS cycle-breaking marks only genuine back-edges on the active call stack
    const visitState = new Map(nodes.map((n) => [n.id, 0]));
    function dfs(uId) {
      visitState.set(uId, 1);
      const edges = rawOut.get(uId) || [];
      for (const e of edges) {
        const st = visitState.get(e.to);
        if (st === 1) {
          e.isCycleBack = true;
        } else if (st === 0) {
          dfs(e.to);
        }
      }
      visitState.set(uId, 2);
    }
    dfsOrder.forEach((n) => {
      if (visitState.get(n.id) === 0) dfs(n.id);
    });

    const dagIn = new Map(nodes.map((n) => [n.id, 0]));
    const dagChildren = new Map(nodes.map((n) => [n.id, []]));
    const dagParents = new Map(nodes.map((n) => [n.id, []]));
    mergedEdges.forEach((e) => {
      if (e.isCycleBack || e.isSelfLoop) return;
      dagChildren.get(e.from).push(e.to);
      dagParents.get(e.to).push(e.from);
      dagIn.set(e.to, (dagIn.get(e.to) || 0) + 1);
    });

    const indeg = new Map(dagIn);
    const q = [];
    indeg.forEach((deg, id) => {
      if (deg === 0) q.push(id);
    });
    while (q.length) {
      const u = q.shift();
      const uRank = idMap.get(u).rank;
      (dagChildren.get(u) || []).forEach((v) => {
        const vn = idMap.get(v);
        if (vn.rank <= uRank) vn.rank = uRank + 1;
        indeg.set(v, indeg.get(v) - 1);
        if (indeg.get(v) === 0) q.push(v);
      });
    }

    // Pull standalone roots closer to their earliest child to prevent long top-to-bottom skips
    nodes.forEach((raw) => {
      const n = idMap.get(raw.id);
      const kids = dagChildren.get(n.id) || [];
      if ((dagIn.get(n.id) || 0) === 0 && kids.length) {
        const minChildRank = Math.min(...kids.map((v) => idMap.get(v).rank));
        n.rank = Math.max(0, minChildRank - 1);
      }
    });

    // Compress any empty rank levels while keeping relative rank order intact
    const usedRanks = Array.from(new Set(nodes.map((raw) => idMap.get(raw.id).rank))).sort((a, b) => a - b);
    const rankRemap = new Map(usedRanks.map((r, i) => [r, i]));
    idMap.forEach((n) => {
      n.rank = rankRemap.get(n.rank);
    });

    const ranks = new Map();
    idMap.forEach((n) => {
      if (!ranks.has(n.rank)) ranks.set(n.rank, []);
      ranks.get(n.rank).push(n);
    });
    const sortedRanks = Array.from(ranks.keys()).sort((a, b) => a - b);

    // 2-pass barycenter sweep across adjacent connected neighbors to untangle crossings
    const allNbrs = new Map(nodes.map((n) => [n.id, new Set()]));
    mergedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      allNbrs.get(e.from).add(e.to);
      allNbrs.get(e.to).add(e.from);
    });
    const updateOrders = () => {
      sortedRanks.forEach((r) => {
        ranks.get(r).forEach((n, i) => {
          n.order = i;
        });
      });
    };
    updateOrders();
    for (let pass = 0; pass < 2; pass++) {
      for (let ri = 1; ri < sortedRanks.length; ri++) {
        const r = sortedRanks[ri];
        const prevR = sortedRanks[ri - 1];
        const list = ranks.get(r);
        list.sort((a, b) => {
          const score = (node) => {
            const pts = [];
            allNbrs.get(node.id).forEach((nid) => {
              const nb = idMap.get(nid);
              if (nb && nb.rank === prevR) pts.push(nb.order);
            });
            return pts.length ? pts.reduce((s, x) => s + x, 0) / pts.length : node.order;
          };
          return score(a) - score(b) || a.order - b.order;
        });
        updateOrders();
      }
      for (let ri = sortedRanks.length - 2; ri >= 0; ri--) {
        const r = sortedRanks[ri];
        const nextR = sortedRanks[ri + 1];
        const list = ranks.get(r);
        list.sort((a, b) => {
          const score = (node) => {
            const pts = [];
            allNbrs.get(node.id).forEach((nid) => {
              const nb = idMap.get(nid);
              if (nb && nb.rank === nextR) pts.push(nb.order);
            });
            return pts.length ? pts.reduce((s, x) => s + x, 0) / pts.length : node.order;
          };
          return score(a) - score(b) || a.order - b.order;
        });
        updateOrders();
      }
    }
    return { ranks, sortedRanks };
  }

  function renderLayeredDagHtml(payload, options) {
    const nodes = (payload.data && payload.data.nodes) || [];
    const rawEdges = (payload.data && payload.data.edges) || [];
    if (!nodes.length) return '<div class="wb-sub">暂无图节点数据</div>';

    const wideMode = Boolean(options && options.wide);
    const expandAll = Boolean(options && options.expandAll);
    const maxAnchors = !expandAll ? 4 : 0;
    const isHistoryDag = ['historical_causality', 'power_hierarchy'].includes(payload.blueprint);
    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i, rank: 0, order: 0 })]));
    const mergedEdges = prepareDagEdges(rawEdges, idMap);
    const { ranks, sortedRanks } = assignDagRanks(nodes, idMap, mergedEdges);

    let maxInRank = 1;
    sortedRanks.forEach((r) => {
      maxInRank = Math.max(maxInRank, ranks.get(r).length);
    });
    const R = sortedRanks.length;
    const rankToIdx = new Map(sortedRanks.map((r, i) => [r, i]));
    const wantCompactLR =
      wideMode && isHistoryDag && (R === 5 || (R === 6 && maxInRank >= 3));
    const wantLR =
      (wideMode && ['flow_swimlane', 'lineage_dag', 'system_flow'].includes(payload.blueprint)) ||
      wantCompactLR;
    const useBandedLR =
      !wantLR && wideMode && isHistoryDag && (R >= 7 || (R === 6 && maxInRank <= 2));
    const splitIdx = useBandedLR ? Math.ceil(R / 2) : R;
    idMap.forEach((n) => {
      const ri = rankToIdx.get(n.rank) ?? 0;
      n.rankIdx = ri;
      n.band = useBandedLR && ri >= splitIdx ? 1 : 0;
    });

    const cardW = wantCompactLR
      ? R >= 6
        ? 162
        : 174
      : useBandedLR
        ? 176
        : wantLR
          ? 204
          : wideMode
            ? maxInRank >= 6
              ? 150
              : maxInRank >= 4
                ? 172
                : maxInRank >= 2
                  ? 190
                  : 236
            : maxInRank >= 4
              ? 142
              : maxInRank >= 2
                ? 158
                : 236;
    const sizes = measureNodeSizes(nodes, cardW, expandAll, maxAnchors);

    const colGap = wantCompactLR
      ? R >= 6
        ? 46
        : 54
      : useBandedLR
        ? 56
        : wantLR
          ? 72
          : wideMode && maxInRank >= 6
            ? 18
            : 24;
    const rowGap = wantCompactLR || useBandedLR ? 24 : wantLR ? 36 : wideMode ? 36 : 56;
    const trackStep = wideMode && (!wantLR || wantCompactLR) ? 15 : 18;
    const corridorStep = wideMode ? 32 : 26;

    // Step 1: Plan every edge's route type, face attachments, and gap/corridor slot needs
    const bandGapSegs = [new Map(), new Map()];
    const getGapList = (g, band = 0) => {
      const m = bandGapSegs[band];
      if (!m.has(g)) m.set(g, []);
      return m.get(g);
    };
    const sideA = [[], []];
    const sideB = [[], []];
    const midCrossPlans = [];
    const edgePlans = [];
    const effLR = wantLR || useBandedLR;

    mergedEdges.forEach((e) => {
      const fn = idMap.get(e.from);
      const tn = idMap.get(e.to);
      if (!fn || !tn) return;
      const ar = fn.rankIdx;
      const br = tn.rankIdx;
      const aCnt = ranks.get(fn.rank).length;
      const bCnt = ranks.get(tn.rank).length;

      if (e.isSelfLoop) {
        edgePlans.push({
          edge: e,
          kind: 'self',
          outFace: effLR ? 'bottom' : 'right',
          inFace: effLR ? 'bottom' : 'right',
        });
        return;
      }

      if (useBandedLR && fn.band !== tn.band) {
        let outFace = 'bottom';
        let exitGap = null;
        const exitBand = fn.band;
        if (fn.band === 0) {
          if (fn.order !== aCnt - 1) {
            outFace = 'right';
            exitGap = ar;
          }
        } else {
          outFace = 'top';
          if (fn.order !== 0) {
            outFace = 'left';
            exitGap = ar - 1;
          }
        }

        let inFace = 'top';
        let enterGap = null;
        const enterBand = tn.band;
        if (tn.band === 1) {
          if (tn.order !== 0) {
            inFace = 'left';
            enterGap = br - 1;
          }
        } else {
          inFace = 'bottom';
          if (tn.order !== bCnt - 1) {
            inFace = 'left';
            enterGap = br - 1;
          }
        }

        const plan = {
          edge: e,
          kind: 'midCross',
          outFace,
          inFace,
          exitGap,
          exitBand,
          enterGap,
          enterBand,
          exitTrackIdx: 0,
          enterTrackIdx: 0,
          midTrackIdx: 0,
          span: Math.abs(br - ar),
        };
        if (exitGap !== null) {
          getGapList(exitGap, exitBand).push({ plan, role: 'exit', sortKey: fn.order * 100 + 20 });
        }
        if (enterGap !== null) {
          getGapList(enterGap, enterBand).push({ plan, role: 'enter', sortKey: tn.order * 100 - 20 });
        }
        midCrossPlans.push(plan);
        edgePlans.push(plan);
        return;
      }

      const band = useBandedLR ? fn.band : 0;
      if (br === ar + 1 && !e.isCycleBack) {
        const plan = {
          edge: e,
          kind: 'adj',
          band,
          gapIdx: ar,
          outFace: effLR ? 'right' : 'bottom',
          inFace: effLR ? 'left' : 'top',
          trackIdx: 0,
        };
        getGapList(ar, band).push({ plan, role: 'adj', sortKey: fn.order * 100 + tn.order });
        edgePlans.push(plan);
        return;
      }

      // Outer corridor edge: pick side closer to endpoints, damped slightly by current load
      const bSideA = sideA[band];
      const bSideB = sideB[band];
      const aRel = (fn.order + 0.5) / aCnt;
      const bRel = (tn.order + 0.5) / bCnt;
      const avgRel = (aRel + bRel) / 2;
      const scoreA = avgRel + bSideA.length * 0.14;
      const scoreB = 1 - avgRel + bSideB.length * 0.14;
      const useA = scoreA <= scoreB;
      const corridorSide = useA ? (effLR ? 'top' : 'left') : effLR ? 'bottom' : 'right';

      const aIsOuter = useA ? fn.order === 0 : fn.order === aCnt - 1;
      const bIsOuter = useA ? tn.order === 0 : tn.order === bCnt - 1;
      const goingBack = br <= ar;

      let outFace = corridorSide;
      let exitGap = null;
      if (!aIsOuter) {
        if (effLR) {
          outFace = goingBack ? 'left' : 'right';
          exitGap = goingBack ? ar - 1 : ar;
        } else {
          outFace = goingBack ? 'top' : 'bottom';
          exitGap = goingBack ? ar - 1 : ar;
        }
      }

      let inFace = corridorSide;
      let enterGap = null;
      if (!bIsOuter) {
        inFace = effLR ? 'left' : 'top';
        enterGap = br - 1;
      }

      const plan = {
        edge: e,
        kind: 'corridor',
        band,
        useA,
        span: Math.abs(br - ar),
        minRank: Math.min(ar, br),
        corridorSide,
        corridorLane: 0,
        outFace,
        inFace,
        exitGap,
        enterGap,
        exitTrackIdx: 0,
        enterTrackIdx: 0,
      };
      if (exitGap !== null) {
        getGapList(exitGap, band).push({ plan, role: 'exit', sortKey: fn.order * 100 + (useA ? -10 : 10) });
      }
      if (enterGap !== null) {
        getGapList(enterGap, band).push({ plan, role: 'enter', sortKey: tn.order * 100 + (useA ? -10 : 10) });
      }
      (useA ? bSideA : bSideB).push(plan);
      edgePlans.push(plan);
    });

    // Order each gap's horizontal/vertical tracks and nest outer corridor lanes shortest-innermost
    const bandGapCounts = [new Map(), new Map()];
    bandGapSegs.forEach((segs, b) => {
      segs.forEach((items, g) => {
        items.sort((a, bIt) => a.sortKey - bIt.sortKey);
        items.forEach((it, idx) => {
          if (it.role === 'adj') it.plan.trackIdx = idx;
          else if (it.role === 'exit') it.plan.exitTrackIdx = idx;
          else it.plan.enterTrackIdx = idx;
        });
        bandGapCounts[b].set(g, items.length);
      });
    });

    const sortCorridors = (arr) => {
      arr.sort((a, b) => a.span - b.span || a.minRank - b.minRank || a.edge.idx - b.edge.idx);
      arr.forEach((p, idx) => {
        p.corridorLane = idx;
      });
    };
    sortCorridors(sideA[0]);
    sortCorridors(sideB[0]);
    sortCorridors(sideA[1]);
    sortCorridors(sideB[1]);
    midCrossPlans.sort((a, b) => {
      const af = idMap.get(a.edge.from);
      const bf = idMap.get(b.edge.from);
      return (af ? af.rankIdx : 0) - (bf ? bf.rankIdx : 0) || a.edge.idx - b.edge.idx;
    });
    midCrossPlans.forEach((p, idx) => {
      p.midTrackIdx = idx;
    });

    // Step 2: Compute node geometry with expanded per-gap and per-corridor gutters
    const gapSize = (g, base, band = 0) => {
      const n = bandGapCounts[band].get(g) || 0;
      return n ? Math.max(base, 26 + n * trackStep) : base;
    };

    const pos = new Map();
    const bandGapBounds = [new Map(), new Map()];
    let rawGraphLeft = 0;
    let rawGraphRight = 0;
    let rawGraphTop = 0;
    let rawGraphBottom = 0;
    let b0Top = 0;
    let b0Bottom = 0;
    let b1Top = 0;
    let b1Bottom = 0;
    let midZoneTop = 0;
    let midZoneBottom = 0;

    if (useBandedLR) {
      const b0PreCnt = bandGapCounts[0].get(-1) || 0;
      const b1PreCnt = bandGapCounts[1].get(splitIdx - 1) || 0;
      const b0Pre = b0PreCnt ? 26 + b0PreCnt * trackStep : 20;
      const b1Pre = b1PreCnt ? 26 + b1PreCnt * trackStep : 20;
      const leftPad = Math.max(b0Pre, b1Pre);

      const layoutLrBand = (bandIdx, sliceIndices, bandPreKey, bandPostKey, bandYStart) => {
        const colW = sliceIndices.map((ri) => Math.max(...ranks.get(sortedRanks[ri]).map((n) => sizes.get(n.id).w)));
        const colH = sliceIndices.map((ri) => {
          const list = ranks.get(sortedRanks[ri]);
          return list.reduce((acc, n) => acc + sizes.get(n.id).h, 0) + (list.length - 1) * rowGap;
        });
        const bandMaxH = Math.max(120, ...colH);
        const postCnt = bandGapCounts[bandIdx].get(bandPostKey) || 0;
        const postG = postCnt ? 26 + postCnt * trackStep : 22;
        let curX = leftPad;
        bandGapBounds[bandIdx].set(bandPreKey, { start: 8, end: leftPad });
        sliceIndices.forEach((ri, localIdx) => {
          const list = ranks.get(sortedRanks[ri]);
          let curY = bandYStart + (bandMaxH - colH[localIdx]) / 2;
          list.forEach((n) => {
            const sz = sizes.get(n.id);
            pos.set(n.id, { x: Math.round(curX), y: Math.round(curY), w: sz.w, h: sz.h, rank: n.rank });
            curY += sz.h + rowGap;
          });
          const layerRight = Math.round(curX + colW[localIdx]);
          const gSpan = localIdx < sliceIndices.length - 1 ? gapSize(ri, colGap, bandIdx) : postG;
          bandGapBounds[bandIdx].set(ri, { start: layerRight, end: layerRight + gSpan });
          curX = layerRight + gSpan;
        });
        return { right: curX - postG, top: bandYStart, bottom: bandYStart + bandMaxH };
      };

      const b0Slice = Array.from({ length: splitIdx }, (_, k) => k);
      const b1Slice = Array.from({ length: R - splitIdx }, (_, k) => splitIdx + k);
      b0Top = sideA[0].length ? sideA[0].length * corridorStep + 26 : 20;
      const b0PreMetrics = b0Slice.map((ri) => {
        const list = ranks.get(sortedRanks[ri]);
        return list.reduce((acc, n) => acc + sizes.get(n.id).h, 0) + (list.length - 1) * rowGap;
      });
      const b0H = Math.max(120, ...b0PreMetrics);
      b0Bottom = b0Top + b0H;
      const b0BotCorrH = sideB[0].length ? sideB[0].length * corridorStep + 24 : 16;
      const midTracksH = midCrossPlans.length ? midCrossPlans.length * trackStep + 28 : 20;
      const b1TopCorrH = sideA[1].length ? sideA[1].length * corridorStep + 24 : 16;
      midZoneTop = b0Bottom + b0BotCorrH;
      midZoneBottom = midZoneTop + midTracksH;
      b1Top = midZoneBottom + b1TopCorrH;

      const r0 = layoutLrBand(0, b0Slice, -1, splitIdx - 1, b0Top);
      const r1 = layoutLrBand(1, b1Slice, splitIdx - 1, R - 1, b1Top);
      b1Bottom = r1.bottom;
      rawGraphLeft = leftPad;
      rawGraphRight = Math.max(r0.right, r1.right);
      rawGraphTop = b0Top;
      rawGraphBottom = b1Bottom;
    } else if (!wantLR) {
      const gapTracksCount = bandGapCounts[0];
      const sideAPad = (sideA[0].length ? sideA[0].length * corridorStep + 28 : 20) + ((gapTracksCount.get(-1) || 0) * trackStep);
      const preGap = gapTracksCount.get(-1) ? 26 + gapTracksCount.get(-1) * trackStep : 22;
      const postGap = gapTracksCount.get(R - 1) ? 26 + gapTracksCount.get(R - 1) * trackStep : 24;
      const leftPad = sideAPad;
      const topPad = preGap;
      const layerHeights = sortedRanks.map((r) => Math.max(...ranks.get(r).map((n) => sizes.get(n.id).h)));
      const maxRowW = Math.max(
        ...sortedRanks.map((r) => {
          const list = ranks.get(r);
          return list.reduce((acc, n) => acc + sizes.get(n.id).w, 0) + (list.length - 1) * colGap;
        })
      );
      const innerWidth = Math.max(240, maxRowW);
      let curY = topPad;
      bandGapBounds[0].set(-1, { start: 8, end: topPad });
      sortedRanks.forEach((r, ri) => {
        const list = ranks.get(r);
        const rowW = list.reduce((acc, n) => acc + sizes.get(n.id).w, 0) + (list.length - 1) * colGap;
        let curX = leftPad + (innerWidth - rowW) / 2;
        list.forEach((n) => {
          const sz = sizes.get(n.id);
          pos.set(n.id, { x: Math.round(curX), y: Math.round(curY), w: sz.w, h: sz.h, rank: r });
          curX += sz.w + colGap;
        });
        const layerBottom = Math.round(curY + layerHeights[ri]);
        const gSpan = ri < R - 1 ? gapSize(ri, rowGap, 0) : postGap;
        bandGapBounds[0].set(ri, { start: layerBottom, end: layerBottom + gSpan });
        curY = layerBottom + gSpan;
      });
      rawGraphLeft = leftPad;
      rawGraphRight = leftPad + innerWidth;
      rawGraphTop = topPad;
      rawGraphBottom = curY - postGap;
    } else {
      const gapTracksCount = bandGapCounts[0];
      const topPad = sideA[0].length ? sideA[0].length * corridorStep + 28 : 20;
      const preGap = gapTracksCount.get(-1) ? 26 + gapTracksCount.get(-1) * trackStep : 22;
      const postGap = gapTracksCount.get(R - 1) ? 26 + gapTracksCount.get(R - 1) * trackStep : 24;
      const leftPad = preGap;
      const colWidths = sortedRanks.map((r) => Math.max(...ranks.get(r).map((n) => sizes.get(n.id).w)));
      const colHeights = sortedRanks.map((r) => {
        const list = ranks.get(r);
        return list.reduce((acc, n) => acc + sizes.get(n.id).h, 0) + (list.length - 1) * rowGap;
      });
      const maxColH = Math.max(190, ...colHeights);
      let curX = leftPad;
      bandGapBounds[0].set(-1, { start: 8, end: leftPad });
      sortedRanks.forEach((r, ri) => {
        const list = ranks.get(r);
        let curY = topPad + (maxColH - colHeights[ri]) / 2;
        list.forEach((n) => {
          const sz = sizes.get(n.id);
          pos.set(n.id, { x: Math.round(curX), y: Math.round(curY), w: sz.w, h: sz.h, rank: r });
          curY += sz.h + rowGap;
        });
        const layerRight = Math.round(curX + colWidths[ri]);
        const gSpan = ri < R - 1 ? gapSize(ri, colGap, 0) : postGap;
        bandGapBounds[0].set(ri, { start: layerRight, end: layerRight + gSpan });
        curX = layerRight + gSpan;
      });
      rawGraphLeft = leftPad;
      rawGraphRight = curX - postGap;
      rawGraphTop = topPad;
      rawGraphBottom = topPad + maxColH;
    }

    const trackCoord = (gapIdx, trackIdx, band = 0) => {
      const b = bandGapBounds[band].get(gapIdx);
      const count = bandGapCounts[band].get(gapIdx) || 1;
      if (!b) return 12;
      const mid = (b.start + b.end) / 2;
      const first = mid - ((count - 1) * trackStep) / 2;
      return Math.round(first + trackIdx * trackStep);
    };

    const midTrackCoord = (crossIdx) => {
      const mid = (midZoneTop + midZoneBottom) / 2;
      const first = mid - ((midCrossPlans.length - 1) * trackStep) / 2;
      return Math.round(first + crossIdx * trackStep);
    };

    const corridorCoord = (plan) => {
      if (useBandedLR) {
        const bTop = plan.band === 0 ? b0Top : b1Top;
        const bBot = plan.band === 0 ? b0Bottom : b1Bottom;
        return plan.useA
          ? Math.round(bTop - 24 - plan.corridorLane * corridorStep)
          : Math.round(bBot + 24 + plan.corridorLane * corridorStep);
      }
      if (wantLR) {
        return plan.useA
          ? Math.round(rawGraphTop - 26 - plan.corridorLane * corridorStep)
          : Math.round(rawGraphBottom + 26 + plan.corridorLane * corridorStep);
      }
      return plan.useA
        ? Math.round(rawGraphLeft - 28 - plan.corridorLane * corridorStep)
        : Math.round(rawGraphRight + 28 + plan.corridorLane * corridorStep);
    };

    // Step 3: Distribute ports evenly along each node face sorted by peer position
    const faceBuckets = new Map();
    const addPortReq = (nodeId, face, edgeIdx, isStart, peerId) => {
      const k = nodeId + '\0' + face;
      if (!faceBuckets.has(k)) faceBuckets.set(k, []);
      const peerBox = pos.get(peerId);
      const peerCoord =
        face === 'top' || face === 'bottom'
          ? peerBox.x + peerBox.w / 2
          : peerBox.y + peerBox.h / 2;
      faceBuckets.get(k).push({ edgeIdx, isStart, peerCoord });
    };
    edgePlans.forEach((plan) => {
      addPortReq(plan.edge.from, plan.outFace, plan.edge.idx, true, plan.edge.to);
      if (!plan.edge.isSelfLoop) {
        addPortReq(plan.edge.to, plan.inFace, plan.edge.idx, false, plan.edge.from);
      }
    });

    const portCoords = new Map();
    faceBuckets.forEach((items, key) => {
      const sep = key.indexOf('\0');
      const nodeId = key.slice(0, sep);
      const face = key.slice(sep + 1);
      const box = pos.get(nodeId);
      items.sort((a, b) => a.peerCoord - b.peerCoord || (a.isStart ? -1 : 1) || a.edgeIdx - b.edgeIdx);
      const span = face === 'top' || face === 'bottom' ? box.w : box.h;
      const pad = Math.min(24, span * 0.2);
      const usable = Math.max(8, span - 2 * pad);
      items.forEach((item, i) => {
        const offset = items.length === 1 ? span / 2 : pad + (i / (items.length - 1)) * usable;
        let x = box.x;
        let y = box.y;
        if (face === 'top') {
          x = Math.round(box.x + offset);
          y = box.y;
        } else if (face === 'bottom') {
          x = Math.round(box.x + offset);
          y = box.y + box.h;
        } else if (face === 'left') {
          x = box.x;
          y = Math.round(box.y + offset);
        } else {
          x = box.x + box.w;
          y = Math.round(box.y + offset);
        }
        portCoords.set(item.edgeIdx + ':' + (item.isStart ? 's' : 'e'), { x, y, face });
      });
    });

    const offsetFromFace = (pt, face, dist) => {
      if (face === 'top') return { x: pt.x, y: pt.y - dist };
      if (face === 'bottom') return { x: pt.x, y: pt.y + dist };
      if (face === 'left') return { x: pt.x - dist, y: pt.y };
      return { x: pt.x + dist, y: pt.y };
    };

    // Step 4: Construct strictly orthogonal polylines for every edge
    const routed = [];
    edgePlans.forEach((plan) => {
      const e = plan.edge;
      const a = pos.get(e.from);
      let pts = [];

      if (plan.kind === 'self') {
        const sy = Math.round(a.y + a.h * 0.32);
        const ey = Math.round(a.y + a.h * 0.68);
        if (!effLR) {
          const sx = a.x + a.w;
          const gx = sx + 28;
          pts = [
            { x: sx, y: sy },
            { x: gx, y: sy },
            { x: gx, y: ey },
            { x: sx + 4, y: ey },
          ];
        } else {
          const sx = Math.round(a.x + a.w * 0.35);
          const ex = Math.round(a.x + a.w * 0.65);
          const by = a.y + a.h;
          const gy = by + 26;
          pts = [
            { x: sx, y: by },
            { x: sx, y: gy },
            { x: ex, y: gy },
            { x: ex, y: by + 4 },
          ];
        }
      } else {
        const rawStart = portCoords.get(e.idx + ':s');
        const rawEnd = portCoords.get(e.idx + ':e');
        const p0 = e.bidirectional ? offsetFromFace(rawStart, plan.outFace, 4) : { x: rawStart.x, y: rawStart.y };
        const pEnd = offsetFromFace(rawEnd, plan.inFace, 4);
        const band = plan.band || 0;

        if (plan.kind === 'midCross') {
          if (plan.exitGap === null && plan.enterGap === null && Math.abs(p0.x - pEnd.x) <= 2) {
            pts = [p0, { x: p0.x, y: pEnd.y }];
          } else {
            const my = midTrackCoord(plan.midTrackIdx);
            pts = [p0];
            if (plan.exitGap !== null) {
              const exitX = trackCoord(plan.exitGap, plan.exitTrackIdx, plan.exitBand);
              pts.push({ x: exitX, y: p0.y });
              pts.push({ x: exitX, y: my });
            } else {
              pts.push({ x: p0.x, y: my });
            }
            if (plan.enterGap !== null) {
              const enterX = trackCoord(plan.enterGap, plan.enterTrackIdx, plan.enterBand);
              pts.push({ x: enterX, y: my });
              pts.push({ x: enterX, y: pEnd.y });
            } else {
              pts.push({ x: pEnd.x, y: my });
            }
            pts.push(pEnd);
          }
        } else if (plan.kind === 'adj') {
          if (!effLR) {
            if (Math.abs(p0.x - pEnd.x) <= 2) {
              pts = [p0, { x: p0.x, y: pEnd.y }];
            } else {
              const aisleY = trackCoord(plan.gapIdx, plan.trackIdx, band);
              pts = [p0, { x: p0.x, y: aisleY }, { x: pEnd.x, y: aisleY }, pEnd];
            }
          } else if (Math.abs(p0.y - pEnd.y) <= 2) {
            pts = [p0, { x: pEnd.x, y: p0.y }];
          } else {
            const aisleX = trackCoord(plan.gapIdx, plan.trackIdx, band);
            pts = [p0, { x: aisleX, y: p0.y }, { x: aisleX, y: pEnd.y }, pEnd];
          }
        } else {
          const gc = corridorCoord(plan);
          pts = [p0];
          if (!effLR) {
            let curY = p0.y;
            if (plan.exitGap !== null) {
              const exitY = trackCoord(plan.exitGap, plan.exitTrackIdx, band);
              pts.push({ x: p0.x, y: exitY });
              curY = exitY;
            }
            pts.push({ x: gc, y: curY });
            if (plan.enterGap !== null) {
              const enterY = trackCoord(plan.enterGap, plan.enterTrackIdx, band);
              pts.push({ x: gc, y: enterY });
              pts.push({ x: pEnd.x, y: enterY });
            } else {
              pts.push({ x: gc, y: pEnd.y });
            }
            pts.push(pEnd);
          } else {
            let curX = p0.x;
            if (plan.exitGap !== null) {
              const exitX = trackCoord(plan.exitGap, plan.exitTrackIdx, band);
              pts.push({ x: exitX, y: p0.y });
              curX = exitX;
            }
            pts.push({ x: curX, y: gc });
            if (plan.enterGap !== null) {
              const enterX = trackCoord(plan.enterGap, plan.enterTrackIdx, band);
              pts.push({ x: enterX, y: gc });
              pts.push({ x: enterX, y: pEnd.y });
            } else {
              pts.push({ x: pEnd.x, y: gc });
            }
            pts.push(pEnd);
          }
        }
      }
      routed.push({ edge: e, plan, pts });
    });

    // Step 5: Normalize coordinate space across ALL nodes and polyline points so nothing clips < 0
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    pos.forEach((p) => {
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + p.w);
      maxY = Math.max(maxY, p.y + p.h);
    });
    routed.forEach((r) => {
      r.pts.forEach((pt) => {
        minX = Math.min(minX, pt.x);
        minY = Math.min(minY, pt.y);
        maxX = Math.max(maxX, pt.x);
        maxY = Math.max(maxY, pt.y);
      });
    });

    const shiftX = Math.round(56 - minX);
    const shiftY = Math.round(32 - minY);
    pos.forEach((p) => {
      p.x += shiftX;
      p.y += shiftY;
    });
    routed.forEach((r) => {
      r.pts.forEach((pt) => {
        pt.x += shiftX;
        pt.y += shiftY;
      });
    });

    const maxW = Math.max(260, Math.ceil(maxX + shiftX + 58));
    const maxH = Math.max(140, Math.ceil(maxY + shiftY + 34));

    const nodeRects = Array.from(pos.values()).map((p) => ({ x: p.x, y: p.y, w: p.w, h: p.h }));
    const placedLabels = [];
    const fallbackLegend = [];

    const uidSuffix = (wideMode ? 'w' : 'n') + '-' + ++markerSeq;
    const markerGreen = 'arr-g-' + uidSuffix;
    const markerRed = 'arr-r-' + uidSuffix;
    const markerIndigo = 'arr-i-' + uidSuffix;
    const markerAmber = 'arr-a-' + uidSuffix;

    const buildLabelCandidates = (pts) => {
      const segs = [];
      for (let i = 0; i < pts.length - 1; i++) {
        const len = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
        if (len >= 24) {
          segs.push({ p1: pts[i], p2: pts[i + 1], len, idx: i });
        }
      }
      // Prefer outer corridor / middle aisle segment before terminal stubs
      segs.sort((a, b) => {
        const aMid = a.idx > 0 && a.idx < pts.length - 2 ? 1 : 0;
        const bMid = b.idx > 0 && b.idx < pts.length - 2 ? 1 : 0;
        if (aMid !== bMid) return bMid - aMid;
        return b.len - a.len;
      });
      const cands = [];
      segs.forEach((s) => {
        [0.5, 0.32, 0.68].forEach((t) => {
          cands.push({
            x: Math.round(s.p1.x + (s.p2.x - s.p1.x) * t),
            y: Math.round(s.p1.y + (s.p2.y - s.p1.y) * t),
          });
        });
      });
      if (!cands.length && pts.length) cands.push({ x: pts[0].x, y: pts[0].y });
      return cands;
    };

    const svgEdgePaths = [];
    const htmlEdgeLabels = [];

    routed.forEach(({ edge: e, plan, pts }) => {
      const d = buildRoundedOrthogonalPath(pts, 9);
      const lbl = e.label || '';
      const isBack = e.isCycleBack;
      const isSkip = !isBack && plan.kind === 'corridor';
      const isNeg = e.isNeg;

      let stroke = 'var(--pine)';
      let marker = markerGreen;
      let dash = 'none';
      let labelKindCls = 'edge-pos';

      if (e.relationType === 'hostile' || isNeg) {
        stroke = 'var(--cinnabar)';
        marker = markerRed;
        labelKindCls = 'edge-neg';
        if (isBack || isSkip) dash = '6 3';
      } else if (e.relationType === 'constraint') {
        stroke = 'var(--amber)';
        marker = markerAmber;
        dash = '5 3';
        labelKindCls = 'edge-back';
      } else if (e.relationType === 'balance') {
        stroke = 'var(--amber)';
        marker = markerAmber;
        labelKindCls = 'edge-back';
      } else if (e.relationType === 'alliance') {
        stroke = 'var(--indigo)';
        marker = markerIndigo;
        dash = '6 3';
        labelKindCls = 'edge-skip';
      } else if (isBack) {
        stroke = 'var(--amber)';
        marker = markerAmber;
        dash = '4 3';
        labelKindCls = 'edge-back';
      } else if (isSkip) {
        stroke = 'var(--indigo)';
        marker = markerIndigo;
        dash = '6 3';
        labelKindCls = 'edge-skip';
      }
      if (e.bidirectional) labelKindCls += ' edge-bi';

      const fromLabel = (idMap.get(e.from) && idMap.get(e.from).label) || e.from;
      const toLabel = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      const arrowSym = e.bidirectional ? ' ↔ ' : ' → ';
      const edgeDesc = fromLabel + arrowSym + toLabel + (lbl ? '：' + lbl : '') + (e.note ? '（' + e.note + '）' : '');

      svgEdgePaths.push(
        '<g class="dag-edge-group" data-edge-idx="' +
          e.idx +
          '" data-from="' +
          esc(e.from) +
          '" data-to="' +
          esc(e.to) +
          '" data-edge-desc="' +
          esc(edgeDesc) +
          '">' +
          '<path class="dag-edge-halo" d="' +
          d +
          '"/>' +
          '<path class="dag-edge-path" d="' +
          d +
          '" stroke="' +
          stroke +
          '" stroke-dasharray="' +
          dash +
          '"' +
          (e.bidirectional ? ' marker-start="url(#' + marker + ')"' : '') +
          ' marker-end="url(#' +
          marker +
          ')"/>' +
          '<path class="dag-edge-hit" d="' +
          d +
          '"/>' +
          '</g>'
      );

      if (lbl) {
        const lsz = measureLabelSize(lbl);
        const labelCandidates = buildLabelCandidates(pts);
        const offsets = [
          { dx: 0, dy: 0 },
          { dx: 0, dy: -13 },
          { dx: 0, dy: 13 },
          { dx: -18, dy: 0 },
          { dx: 18, dy: 0 },
          { dx: 0, dy: -24 },
          { dx: 0, dy: 24 },
        ];
        let chosenRect = null;
        for (const cand of labelCandidates) {
          for (const off of offsets) {
            const box = {
              x: Math.round(cand.x + off.dx - lsz.w / 2),
              y: Math.round(cand.y + off.dy - lsz.h / 2),
              w: lsz.w,
              h: lsz.h,
            };
            if (box.x < 4 || box.x + box.w > maxW - 4 || box.y < 4 || box.y + box.h > maxH - 4) continue;
            if (!nodeRects.some((nr) => rectsOverlap(box, nr, 5)) && !placedLabels.some((lr) => rectsOverlap(box, lr, 5))) {
              chosenRect = box;
              break;
            }
          }
          if (chosenRect) break;
        }

        if (chosenRect) {
          placedLabels.push(chosenRect);
          htmlEdgeLabels.push(
            '<span class="dag-edge-label ' +
              labelKindCls +
              '" data-edge-idx="' +
              e.idx +
              '" style="left:' +
              chosenRect.x +
              'px;top:' +
              chosenRect.y +
              'px" title="' +
              esc(edgeDesc) +
              '">' +
              esc(lbl) +
              '</span>'
          );
        } else {
          const badgeNum = fallbackLegend.length + 1;
          const circ = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'[badgeNum - 1] || String(badgeNum);
          fallbackLegend.push({
            idx: e.idx,
            text: circ + ' ' + e.from + arrowSym + e.to + '：' + (e.note || lbl),
          });
          let badgeBox = null;
          for (const cand of labelCandidates) {
            for (const off of offsets) {
              const bx = {
                x: Math.max(4, Math.min(maxW - 24, Math.round(cand.x + off.dx - 10))),
                y: Math.max(4, Math.min(maxH - 22, Math.round(cand.y + off.dy - 9))),
                w: 20,
                h: 18,
              };
              if (!nodeRects.some((nr) => rectsOverlap(bx, nr, 3)) && !placedLabels.some((lr) => rectsOverlap(bx, lr, 3))) {
                badgeBox = bx;
                break;
              }
            }
            if (badgeBox) break;
          }
          if (badgeBox) {
            placedLabels.push(badgeBox);
            htmlEdgeLabels.push(
              '<span class="dag-edge-label ' +
                labelKindCls +
                '" data-edge-idx="' +
                e.idx +
                '" style="left:' +
                badgeBox.x +
                'px;top:' +
                badgeBox.y +
                'px" title="' +
                esc(edgeDesc) +
                '">' +
                circ +
                '</span>'
            );
          }
        }
      }
    });

    const cardsHtml = nodes
      .map((n) => {
        const p = pos.get(n.id);
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        const isExp = expandAll && Boolean(n.detail);
        return (
          '<div class="dag-node role-' +
          esc(n.role || 'inference') +
          (isExp ? ' expanded' : '') +
          '" style="left:' +
          p.x +
          'px;top:' +
          p.y +
          'px;width:' +
          p.w +
          'px" data-jump="' +
          esc(firstBlk) +
          '" data-node-id="' +
          esc(n.id) +
          '" title="悬停高亮关系 · 点击节点跳转原文 ' +
          esc(firstBlk) +
          '">' +
          '<div class="dag-node-role"><span>' +
          esc(ROLE_ZH[n.role] || n.role || '节点') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="dag-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.detail
            ? '<div class="dag-node-more" data-toggle-detail>' +
              (isExp ? '▴ 收起' : '▾ 查看展开') +
              '</div><div class="dag-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, maxAnchors) +
          '</div></div>'
        );
      })
      .join('');

    const legendHtml = fallbackLegend.length
      ? '<div class="edge-legend">' +
        fallbackLegend
          .map(
            (item) =>
              '<span class="edge-legend-pill" data-edge-idx="' +
              item.idx +
              '">' +
              esc(item.text) +
              '</span>'
          )
          .join('') +
        '</div>'
      : '';

    const markerDef = (id, fill) =>
      '<marker id="' +
      id +
      '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="' +
      fill +
      '"/></marker>';

    const rawCanvasHtml =
      '<div class="dag-canvas" style="width:' +
      maxW +
      'px;height:' +
      maxH +
      'px"><svg class="dag-edges" width="' +
      maxW +
      '" height="' +
      maxH +
      '" viewBox="0 0 ' +
      maxW +
      ' ' +
      maxH +
      '"><defs>' +
      markerDef(markerGreen, 'var(--pine)') +
      markerDef(markerRed, 'var(--cinnabar)') +
      markerDef(markerIndigo, 'var(--indigo)') +
      markerDef(markerAmber, 'var(--amber)') +
      '</defs>' +
      svgEdgePaths.join('') +
      '</svg>' +
      cardsHtml +
      htmlEdgeLabels.join('') +
      '</div>';

    const narrowPreviewCap = 312;
    const fitScale = !wideMode && maxW > narrowPreviewCap ? narrowPreviewCap / maxW : 1;
    const viewportHtml =
      fitScale < 1
        ? '<div class="dag-fit-wrap" data-qa-target="dag" style="height:' +
          Math.ceil(maxH * fitScale) +
          'px"><div class="dag-fit-scale" style="width:' +
          maxW +
          'px;transform:scale(' +
          fitScale.toFixed(4) +
          ')">' +
          rawCanvasHtml +
          '</div></div>'
        : '<div class="dag-viewport" data-qa-target="dag">' + rawCanvasHtml + '</div>';

    if (!wideMode && payload.blueprint === 'power_hierarchy') {
      const roleOrder = ['sovereign', 'faction', 'ally', 'rival', 'constraint'];
      const grouped = new Map();
      nodes.forEach((n) => {
        const r = n.role || 'faction';
        if (!grouped.has(r)) grouped.set(r, []);
        grouped.get(r).push(n);
      });
      const orderedRoles = Array.from(grouped.keys()).sort((a, b) => {
        const ia = roleOrder.indexOf(a);
        const ib = roleOrder.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      const relOut = new Map();
      mergedEdges.forEach((e) => {
        const target = idMap.get(e.to);
        const tName = target ? target.label : e.to;
        if (!relOut.has(e.from)) relOut.set(e.from, []);
        relOut.get(e.from).push((e.label || '关联') + ' → ' + tName);
      });
      const groupsHtml =
        '<div class="power-group-list" data-power-panel="groups" hidden>' +
        orderedRoles
          .map(
            (r) =>
              '<div class="power-role-group"><div class="power-role-head role-' +
              esc(r) +
              '">' +
              esc(ROLE_ZH[r] || r) +
              '</div><div class="power-role-cards">' +
              grouped
                .get(r)
                .map((n) => {
                  const outs = relOut.get(n.id) || [];
                  return (
                    '<div class="power-group-card">' +
                    '<div class="power-group-top"><b>' +
                    esc(n.label) +
                    '</b>' +
                    renderSrcTag(n.source) +
                    '</div>' +
                    (n.detail ? '<div class="power-group-detail">' + esc(n.detail) + '</div>' : '') +
                    (outs.length
                      ? '<div class="power-group-rels">' +
                        outs.map((o) => '<span class="power-rel-pill">' + esc(o) + '</span>').join('') +
                        '</div>'
                      : '') +
                    '<div class="dag-node-foot">' +
                    renderAnchorPills(n.blocks) +
                    '</div></div>'
                  );
                })
                .join('') +
              '</div></div>'
          )
          .join('') +
        '</div>';

      return (
        '<div class="power-narrow-shell">' +
        '<div class="power-view-tabs" role="tablist">' +
        '<button type="button" class="active" data-power-view="graph">结构预览</button>' +
        '<button type="button" data-power-view="groups">阵营分组</button>' +
        '</div>' +
        '<div data-power-panel="graph">' +
        viewportHtml +
        '<div class="dag-trace-tip" data-trace-tip>点击「全览 ⤢」可放大查看全图；切「阵营分组」可按角色细读</div>' +
        legendHtml +
        '</div>' +
        groupsHtml +
        '</div>'
      );
    }

    return (
      viewportHtml +
      '<div class="dag-trace-tip" data-trace-tip>悬停节点或连线可高亮因果路径；点击节点跳转原文</div>' +
      legendHtml
    );
  }

  function renderQuadrantHtml(payload) {
    const qs = (payload.data && payload.data.quadrants) || [];
    const byPos = new Map(qs.map((q) => [q.pos, q]));
    const cell = (cls) => {
      const q = byPos.get(cls);
      if (!q) return '<div class="quad-cell ' + cls + '"></div>';
      return (
        '<div class="quad-cell ' +
        cls +
        '"><div class="quad-cell-title">' +
        esc(q.title) +
        '</div>' +
        (q.items || [])
          .map(
            (it) =>
              '<div class="quad-item"><div>' +
              renderSrcTag(it.source) +
              ' ' +
              esc(it.text) +
              '</div><div>' +
              renderAnchorPills(it.blocks) +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    };
    return (
      '<div class="quad-grid-shell" data-qa-target="quadrant">' +
      '<div class="quad-axis-y">↑ ' +
      esc((payload.data && payload.data.yAxis) || '纵轴（低 → 高）') +
      '</div>' +
      '<div class="quad-matrix-area">' +
      cell('q-top-left') +
      '<div class="quad-cross-v"></div>' +
      cell('q-top-right') +
      '<div class="quad-cross-h"></div>' +
      cell('q-bot-left') +
      cell('q-bot-right') +
      '</div>' +
      '<div class="quad-axis-x">→ ' +
      esc((payload.data && payload.data.xAxis) || '横轴（低 → 高）') +
      '</div></div>'
    );
  }

  const LANE_ACCENTS = ['var(--pine)', 'var(--cinnabar)', 'var(--indigo)', 'var(--amber)'];

  function renderTimelineHtml(payload, options) {
    const wideMode = Boolean(options && options.wide);
    const lanes = (payload.data && payload.data.lanes) || [];
    const rows = (payload.data && payload.data.rows) || [];
    const aftermath = (payload.data && payload.data.aftermath) || [];

    const aftermathHtml = aftermath.length
      ? '<div class="tl-aftermath"><div class="tl-aftermath-head">后世回响与史论</div><div class="tl-aftermath-list">' +
        aftermath
          .map(
            (af) =>
              '<div class="tl-aftermath-card"><div class="tl-card-head"><b>' +
              esc(af.title || '史论') +
              '</b>' +
              renderSrcTag(af.source) +
              '</div>' +
              (af.body ? '<p>' + esc(af.body) + '</p>' : '') +
              '<div>' +
              renderAnchorPills(af.blocks) +
              '</div></div>'
          )
          .join('') +
        '</div></div>'
      : '';

    if (!wideMode) {
      const filterHtml =
        lanes.length > 1
          ? '<div class="tl-filter-bar" role="group" aria-label="阵营筛选">' +
            '<button type="button" class="tl-filter-pill active" data-tl-lane="all">全部阵营</button>' +
            lanes
              .map(
                (l, idx) =>
                  '<button type="button" class="tl-filter-pill" data-tl-lane="' +
                  idx +
                  '"><i class="tl-dot" style="background:' +
                  LANE_ACCENTS[idx % LANE_ACCENTS.length] +
                  '"></i>' +
                  esc(l) +
                  '</button>'
              )
              .join('') +
            '</div>'
          : '';
      const streamHtml = rows
        .map((row) => {
          const cards = [];
          (row.cells || []).forEach((cell, idx) => {
            if (!cell) return;
            const laneName = lanes[idx] || '主线';
            const color = LANE_ACCENTS[idx % LANE_ACCENTS.length];
            cards.push(
              '<div class="tl-card" data-lane-idx="' +
                idx +
                '" style="--lane-accent:' +
                color +
                '">' +
                '<div class="tl-card-top">' +
                '<span class="tl-lane-badge">' +
                esc(laneName) +
                '</span>' +
                renderSrcTag(cell.source) +
                '</div>' +
                '<div class="tl-card-title">' +
                esc(cell.title) +
                '</div>' +
                (cell.note ? '<p class="tl-card-note">' + esc(cell.note) + '</p>' : '') +
                '<div class="tl-card-foot">' +
                renderAnchorPills(cell.blocks) +
                '</div></div>'
            );
          });
          if (!cards.length) return '';
          return (
            '<div class="tl-row-group">' +
            '<div class="tl-row-time">' +
            esc(row.time) +
            '</div>' +
            '<div class="tl-row-cards">' +
            cards.join('') +
            '</div></div>'
          );
        })
        .join('');
      return (
        '<div class="timeline-stream" data-qa-target="grid">' +
        filterHtml +
        '<div class="tl-stream-body">' +
        streamHtml +
        '</div>' +
        aftermathHtml +
        '</div>'
      );
    }

    const cols = '88px ' + lanes.map(() => 'minmax(130px, 1fr)').join(' ');
    const head =
      '<div class="lanes-head" style="grid-template-columns:' +
      cols +
      '"><div class="lane-time-col">时间</div>' +
      lanes
        .map(
          (l, idx) =>
            '<div style="border-top:3px solid ' +
            LANE_ACCENTS[idx % LANE_ACCENTS.length] +
            '">' +
            esc(l) +
            '</div>'
        )
        .join('') +
      '</div>';
    const body = rows
      .map(
        (row) =>
          '<div class="lanes-row" style="grid-template-columns:' +
          cols +
          '"><div class="lane-time">' +
          esc(row.time) +
          '</div>' +
          (row.cells || [])
            .map((cell, idx) =>
              !cell
                ? '<div class="lane-cell"></div>'
                : '<div class="lane-cell filled" style="border-top:2px solid ' +
                  LANE_ACCENTS[idx % LANE_ACCENTS.length] +
                  '"><div class="lane-cell-head"><b>' +
                  esc(cell.title) +
                  '</b>' +
                  renderSrcTag(cell.source) +
                  '</div><p>' +
                  esc(cell.note) +
                  '</p><div>' +
                  renderAnchorPills(cell.blocks) +
                  '</div></div>'
            )
            .join('') +
          '</div>'
      )
      .join('');
    return '<div class="lanes-grid" data-qa-target="grid">' + head + body + '</div>' + aftermathHtml;
  }

  const SPATIAL_EDGE_STYLES = {
    advance: { label: '主攻动线', stroke: 'var(--cinnabar)', dash: 'none' },
    flank: { label: '迂回包抄', stroke: 'var(--amber)', dash: '7 4' },
    defense: { label: '防线牵制', stroke: 'var(--indigo)', dash: 'none' },
    diplomacy: { label: '远交联结', stroke: 'var(--pine)', dash: '3 4' },
  };
  const SPATIAL_GROUP_PALETTE = ['var(--cinnabar)', 'var(--indigo)', 'var(--pine)', 'var(--amber)'];

  function measureSpatialSizes(nodes, cardW, expandAll, maxAnchors) {
    const stage = ensureMeasureStage();
    const sizes = new Map();
    stage.style.width = cardW + 'px';
    stage.innerHTML = nodes
      .map(
        (n) =>
          '<div class="spatial-node role-' +
          esc(n.role || 'corridor') +
          (expandAll && n.detail ? ' expanded' : '') +
          '" style="position:relative;width:' +
          cardW +
          'px">' +
          '<div class="spatial-node-head"><span class="spatial-role-tag">' +
          esc(ROLE_ZH[n.role] || n.role || '地点') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="spatial-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.zone ? '<div class="spatial-node-zone">' + esc(n.zone) + '</div>' : '') +
          (n.detail
            ? '<div class="dag-node-more">' +
              (expandAll ? '▴ 收起' : '▾ 态势') +
              '</div><div class="spatial-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, maxAnchors) +
          '</div></div>'
      )
      .join('');
    const els = stage.querySelectorAll('.spatial-node');
    nodes.forEach((n, idx) => {
      const r = els[idx] ? els[idx].getBoundingClientRect() : null;
      if (r && r.height > 20) {
        sizes.set(n.id, { w: Math.ceil(r.width), h: Math.ceil(r.height) });
      } else {
        const detH = expandAll && n.detail ? Math.ceil(String(n.detail).length / 10) * 16 + 10 : 0;
        sizes.set(n.id, { w: cardW, h: 66 + (n.zone ? 16 : 0) + (n.detail ? 16 : 0) + detH });
      }
    });
    stage.innerHTML = '';
    return sizes;
  }

  function resolveSpatialPositions(nodes, sizes, bounds) {
    const { padL, padR, padT, padB, W, H } = bounds;
    const clampX = (v, w) => Math.max(padL, Math.min(W - padR - w, v));
    const clampY = (v, h) => Math.max(padT, Math.min(H - padB - h, v));
    const separateAxis = (a, b, axis, dir, total) => {
      if (axis === 'x') {
        const a0 = a.x;
        const b0 = b.x;
        const a1 = clampX(a0 + dir * (total * 0.5), a.w);
        const movedA = (a1 - a0) * dir;
        const b1 = clampX(b0 - dir * (total - movedA), b.w);
        const movedB = (b0 - b1) * dir;
        a.x = movedA + movedB < total ? clampX(a1 + dir * (total - movedA - movedB), a.w) : a1;
        b.x = b1;
      } else {
        const a0 = a.y;
        const b0 = b.y;
        const a1 = clampY(a0 + dir * (total * 0.5), a.h);
        const movedA = (a1 - a0) * dir;
        const b1 = clampY(b0 - dir * (total - movedA), b.h);
        const movedB = (b0 - b1) * dir;
        a.y = movedA + movedB < total ? clampY(a1 + dir * (total - movedA - movedB), a.h) : a1;
        b.y = b1;
      }
    };

    const boxes = nodes.map((n, idx) => {
      const sz = sizes.get(n.id);
      const nx = Math.max(0, Math.min(100, Number(n.x || 0))) / 100;
      const ny = Math.max(0, Math.min(100, Number(n.y || 0))) / 100;
      const x0 = padL + nx * Math.max(10, W - padL - padR - sz.w);
      const y0 = padT + ny * Math.max(10, H - padT - padB - sz.h);
      return {
        id: n.id,
        idx,
        origX: x0,
        origY: y0,
        origCx: x0 + sz.w / 2,
        origCy: y0 + sz.h / 2,
        x: x0,
        y: y0,
        w: sz.w,
        h: sz.h,
      };
    });

    const margin = 18;
    for (let iter = 0; iter < 240; iter++) {
      let moved = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i];
          const b = boxes[j];
          const ax1 = a.x - margin / 2;
          const ay1 = a.y - margin / 2;
          const ax2 = a.x + a.w + margin / 2;
          const ay2 = a.y + a.h + margin / 2;
          const bx1 = b.x - margin / 2;
          const by1 = b.y - margin / 2;
          const bx2 = b.x + b.w + margin / 2;
          const by2 = b.y + b.h + margin / 2;
          if (ax1 < bx2 && ax2 > bx1 && ay1 < by2 && ay2 > by1) {
            moved = true;
            const ox = Math.min(ax2, bx2) - Math.max(ax1, bx1);
            const oy = Math.min(ay2, by2) - Math.max(ay1, by1);
            const dxOrig = Math.abs(a.origCx - b.origCx);
            const dyOrig = Math.abs(a.origCy - b.origCy);
            const preferX =
              iter < 90
                ? ox < oy || (dxOrig > dyOrig * 1.4 && ox < oy * 1.6)
                : iter < 150
                  ? ox <= oy
                  : ox <= oy * 0.65;
            if (preferX) {
              const dir = dxOrig > 1 ? (a.origCx < b.origCx ? -1 : 1) : a.x <= b.x ? -1 : 1;
              separateAxis(a, b, 'x', dir, ox + 3);
            } else {
              const dir = dyOrig > 1 ? (a.origCy < b.origCy ? -1 : 1) : a.y <= b.y ? -1 : 1;
              separateAxis(a, b, 'y', dir, oy + 3);
            }
          }
        }
      }
      if (iter < 40) {
        boxes.forEach((b) => {
          b.x = clampX(b.x + (b.origX - b.x) * 0.05, b.w);
          b.y = clampY(b.y + (b.origY - b.y) * 0.05, b.h);
        });
      } else if (!moved) {
        break;
      }
    }

    // Strict final pass: guarantee separation within wall bounds, alternating axes if jammed
    const minGap = 10;
    for (let pass = 0; pass < 120; pass++) {
      let anyHit = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i];
          const b = boxes[j];
          if (a.x < b.x + b.w + minGap && a.x + a.w + minGap > b.x && a.y < b.y + b.h + minGap && a.y + a.h + minGap > b.y) {
            anyHit = true;
            const ox = Math.min(a.x + a.w + minGap, b.x + b.w + minGap) - Math.max(a.x, b.x);
            const oy = Math.min(a.y + a.h + minGap, b.y + b.h + minGap) - Math.max(a.y, b.y);
            const roomX = (a.x - padL) + (W - padR - (a.x + a.w)) + (b.x - padL) + (W - padR - (b.x + b.w));
            const roomY = (a.y - padT) + (H - padB - (a.y + a.h)) + (b.y - padT) + (H - padB - (b.y + b.h));
            const useX = pass > 24 && pass % 3 === 2 ? false : ox <= oy && roomX > ox;
            if (useX || roomY < oy) {
              const dir = a.x <= b.x ? -1 : 1;
              separateAxis(a, b, 'x', dir, ox + 4);
            } else {
              const dir = a.y <= b.y ? -1 : 1;
              separateAxis(a, b, 'y', dir, oy + 4);
            }
          }
        }
      }
      if (!anyHit) break;
    }

    const pos = new Map();
    boxes.forEach((b) => {
      pos.set(b.id, { x: Math.round(b.x), y: Math.round(b.y), w: b.w, h: b.h });
    });
    return pos;
  }

  function renderSpatialMapHtml(payload, options) {
    const data = payload.data || {};
    if (data.mode === 'not_applicable') {
      return (
        '<div class="spatial-na" data-qa-target="spatial-na">' +
        '<strong>本章无显著地理空间格局</strong>' +
        '<p>' +
        esc(data.not_applicable_reason || payload.summary || '本章内容以制度或事件推演为主，未构成地理态势分布。') +
        '</p></div>'
      );
    }

    const nodes = data.nodes || [];
    if (!nodes.length) return '<div class="wb-sub">暂无空间节点数据</div>';
    const hasCoords = nodes.some((n) => Number.isFinite(Number(n.x)) && Number.isFinite(Number(n.y)));
    if (!hasCoords) {
      return renderLayeredDagHtml(payload, options);
    }

    const wideMode = Boolean(options && options.wide);
    const expandAll = Boolean(options && options.expandAll);
    const maxAnchors = !expandAll ? 4 : 0;
    const cardW = expandAll ? 174 : 140;
    const sizes = measureSpatialSizes(nodes, cardW, expandAll, maxAnchors);
    const maxNodeH = Math.max(80, ...Array.from(sizes.values()).map((s) => s.h));
    const paddedArea = Array.from(sizes.values()).reduce((s, sz) => s + (sz.w + 28) * (sz.h + 28), 0);
    let W = expandAll ? Math.max(1220, Math.ceil(Math.sqrt(paddedArea * 2.4))) : 980;
    let H = expandAll ? Math.max(820, 480 + Math.round(maxNodeH * 2.1), Math.ceil((paddedArea * 2.2) / W)) : 620;
    let pos = resolveSpatialPositions(nodes, sizes, { padL: 24, padR: 24, padT: 48, padB: 28, W, H });
    const hasPosOverlap = (pMap) => {
      const list = Array.from(pMap.values());
      for (let i = 0; i < list.length; i++) {
        for (let j = i + 1; j < list.length; j++) {
          if (rectsOverlap(list[i], list[j], 6)) return true;
        }
      }
      return false;
    };
    for (let grow = 0; grow < 6 && hasPosOverlap(pos); grow++) {
      W += 260;
      H += 220;
      pos = resolveSpatialPositions(nodes, sizes, { padL: 24, padR: 24, padT: 48, padB: 28, W, H });
    }
    const idMap = new Map(nodes.map((n) => [n.id, n]));
    const rawEdges = (data.edges || []).filter((e) => e && idMap.has(e.from) && idMap.has(e.to));

    const routeGroups = [];
    const groupColor = new Map();
    rawEdges.forEach((e) => {
      const rg = String(e.route_group || '').trim();
      if (rg && !groupColor.has(rg)) {
        groupColor.set(rg, SPATIAL_GROUP_PALETTE[routeGroups.length % SPATIAL_GROUP_PALETTE.length]);
        routeGroups.push(rg);
      }
    });

    const uidSuffix = (wideMode ? 'sw' : 'sn') + '-' + ++markerSeq;
    const markerIds = new Map();
    const markerDefs = [];
    const getMarker = (stroke) => {
      if (markerIds.has(stroke)) return markerIds.get(stroke);
      const id = 'sp-arr-' + uidSuffix + '-' + markerIds.size;
      markerIds.set(stroke, id);
      markerDefs.push(
        '<marker id="' +
          id +
          '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="' +
          stroke +
          '"/></marker>'
      );
      return id;
    };

    // Grid lines (4x4 subtle geographic graticule)
    const gridLines = [];
    for (let i = 1; i <= 4; i++) {
      const gx = Math.round((W / 5) * i);
      const gy = Math.round((H / 5) * i);
      gridLines.push('<line x1="' + gx + '" y1="36" x2="' + gx + '" y2="' + (H - 18) + '" stroke="var(--line)" stroke-dasharray="4 4" opacity="0.6"/>');
      gridLines.push('<line x1="16" y1="' + gy + '" x2="' + (W - 16) + '" y2="' + gy + '" stroke="var(--line)" stroke-dasharray="4 4" opacity="0.6"/>');
    }

    const nodeRects = Array.from(pos.values()).map((p) => ({ x: p.x, y: p.y, w: p.w, h: p.h }));
    const placedLabels = [];
    const fallbackLegend = [];
    const svgPaths = [];
    const htmlEdgeLabels = [];

    const boxAnchor = (box, tx, ty) => {
      const cx = box.x + box.w / 2;
      const cy = box.y + box.h / 2;
      const dx = tx - cx;
      const dy = ty - cy;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return { x: cx, y: cy };
      const sx = dx !== 0 ? (box.w / 2) / Math.abs(dx) : Infinity;
      const sy = dy !== 0 ? (box.h / 2) / Math.abs(dy) : Infinity;
      const s = Math.min(sx, sy);
      return { x: cx + dx * s, y: cy + dy * s };
    };

    const pairCounts = new Map();
    rawEdges.forEach((e, idx) => {
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) return;
      const acx = a.x + a.w / 2;
      const acy = a.y + a.h / 2;
      const bcx = b.x + b.w / 2;
      const bcy = b.y + b.h / 2;
      const p1 = boxAnchor(a, bcx, bcy);
      const p2 = boxAnchor(b, acx, acy);
      const key = e.from < e.to ? e.from + '::' + e.to : e.to + '::' + e.from;
      const seen = pairCounts.get(key) || 0;
      pairCounts.set(key, seen + 1);
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const dist = Math.max(1, Math.hypot(dx, dy));
      const sign = (seen % 2 === 0 ? 1 : -1) * (e.from < e.to ? 1 : -1);
      const bend = Math.min(46, Math.max(18, dist * 0.14)) * (1 + Math.floor(seen / 2) * 0.7) * sign;
      const nx = -dy / dist;
      const ny = dx / dist;
      const ctrlX = (p1.x + p2.x) / 2 + nx * bend;
      const ctrlY = (p1.y + p2.y) / 2 + ny * bend;
      const d =
        'M ' +
        p1.x.toFixed(1) +
        ',' +
        p1.y.toFixed(1) +
        ' Q ' +
        ctrlX.toFixed(1) +
        ',' +
        ctrlY.toFixed(1) +
        ' ' +
        p2.x.toFixed(1) +
        ',' +
        p2.y.toFixed(1);

      const st = SPATIAL_EDGE_STYLES[e.kind] || SPATIAL_EDGE_STYLES.advance;
      const stroke = (e.route_group && groupColor.get(e.route_group)) || st.stroke;
      const markerId = getMarker(stroke);
      const fromLabel = (idMap.get(e.from) && idMap.get(e.from).label) || e.from;
      const toLabel = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      const edgeDesc = fromLabel + ' → ' + toLabel + (e.label ? '：' + e.label : '') + (e.note ? '（' + e.note + '）' : '');

      svgPaths.push(
        '<g class="dag-edge-group" data-edge-idx="' +
          idx +
          '" data-from="' +
          esc(e.from) +
          '" data-to="' +
          esc(e.to) +
          '" data-edge-desc="' +
          esc(edgeDesc) +
          '">' +
          '<path class="dag-edge-halo" d="' +
          d +
          '"/>' +
          '<path class="dag-edge-path" d="' +
          d +
          '" stroke="' +
          stroke +
          '" stroke-dasharray="' +
          st.dash +
          '" marker-end="url(#' +
          markerId +
          ')"/>' +
          '<path class="dag-edge-hit" d="' +
          d +
          '"/>' +
          '</g>'
      );

      if (e.label) {
        const lsz = measureLabelSize(e.label);
        const curvePt = (t) => ({
          x: Math.round((1 - t) * (1 - t) * p1.x + 2 * (1 - t) * t * ctrlX + t * t * p2.x),
          y: Math.round((1 - t) * (1 - t) * p1.y + 2 * (1 - t) * t * ctrlY + t * t * p2.y),
        });
        const cands = [0.5, 0.36, 0.64].map(curvePt);
        const offs = [
          { dx: 0, dy: 0 },
          { dx: 0, dy: -14 },
          { dx: 0, dy: 14 },
          { dx: -20, dy: 0 },
          { dx: 20, dy: 0 },
        ];
        let chosen = null;
        for (const c of cands) {
          for (const off of offs) {
            const box = {
              x: Math.round(c.x + off.dx - lsz.w / 2),
              y: Math.round(c.y + off.dy - lsz.h / 2),
              w: lsz.w,
              h: lsz.h,
            };
            if (box.x < 6 || box.x + box.w > W - 6 || box.y < 38 || box.y + box.h > H - 6) continue;
            if (!nodeRects.some((nr) => rectsOverlap(box, nr, 5)) && !placedLabels.some((lr) => rectsOverlap(box, lr, 5))) {
              chosen = box;
              break;
            }
          }
          if (chosen) break;
        }
        if (chosen) {
          placedLabels.push(chosen);
          htmlEdgeLabels.push(
            '<span class="spatial-edge-label" data-edge-idx="' +
              idx +
              '" style="left:' +
              chosen.x +
              'px;top:' +
              chosen.y +
              'px;border-color:' +
              stroke +
              ';color:' +
              stroke +
              '" title="' +
              esc(edgeDesc) +
              '">' +
              esc(e.label) +
              '</span>'
          );
        } else {
          const badgeNum = fallbackLegend.length + 1;
          const circ = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'[badgeNum - 1] || String(badgeNum);
          fallbackLegend.push({ idx, text: circ + ' ' + fromLabel + ' → ' + toLabel + '：' + (e.note || e.label) });
          for (const c of cands) {
            const bx = {
              x: Math.max(6, Math.min(W - 26, Math.round(c.x - 10))),
              y: Math.max(38, Math.min(H - 24, Math.round(c.y - 9))),
              w: 20,
              h: 18,
            };
            if (!nodeRects.some((nr) => rectsOverlap(bx, nr, 3)) && !placedLabels.some((lr) => rectsOverlap(bx, lr, 3))) {
              placedLabels.push(bx);
              htmlEdgeLabels.push(
                '<span class="spatial-edge-label" data-edge-idx="' +
                  idx +
                  '" style="left:' +
                  bx.x +
                  'px;top:' +
                  bx.y +
                  'px;border-color:' +
                  stroke +
                  ';color:' +
                  stroke +
                  '" title="' +
                  esc(edgeDesc) +
                  '">' +
                  circ +
                  '</span>'
              );
              break;
            }
          }
        }
      }
    });

    const nodesHtml = nodes
      .map((n) => {
        const p = pos.get(n.id);
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        const isExp = expandAll && Boolean(n.detail);
        return (
          '<div class="spatial-node role-' +
          esc(n.role || 'corridor') +
          (isExp ? ' expanded' : '') +
          '" style="left:' +
          p.x +
          'px;top:' +
          p.y +
          'px;width:' +
          p.w +
          'px" data-jump="' +
          esc(firstBlk) +
          '" data-node-id="' +
          esc(n.id) +
          '" title="点击定位原文 ' +
          esc(firstBlk) +
          '">' +
          '<div class="spatial-node-head"><span class="spatial-role-tag">' +
          esc(ROLE_ZH[n.role] || n.role || '地点') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="spatial-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.zone ? '<div class="spatial-node-zone">' + esc(n.zone) + '</div>' : '') +
          (n.detail
            ? '<div class="dag-node-more" data-toggle-detail>' +
              (isExp ? '▴ 收起' : '▾ 态势') +
              '</div><div class="spatial-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, maxAnchors) +
          '</div></div>'
        );
      })
      .join('');

    const modeBadge = data.mode === 'campaign' ? '战役行军态势' : '地缘格局态势';
    const canvasHtml =
      '<div class="spatial-canvas" style="width:' +
      W +
      'px;height:' +
      H +
      'px">' +
      '<div class="spatial-hud-left">' +
      '<span class="spatial-mode-pill">' +
      modeBadge +
      '</span>' +
      (data.scope_note ? '<span class="spatial-scope-pill">' + esc(data.scope_note) + '</span>' : '') +
      '</div>' +
      '<div class="spatial-compass" aria-label="上北下南">N ↑</div>' +
      '<svg class="dag-edges" width="' +
      W +
      '" height="' +
      H +
      '" viewBox="0 0 ' +
      W +
      ' ' +
      H +
      '"><defs>' +
      markerDefs.join('') +
      '</defs>' +
      gridLines.join('') +
      svgPaths.join('') +
      '</svg>' +
      nodesHtml +
      htmlEdgeLabels.join('') +
      '</div>';

    const usedKinds = Array.from(new Set(rawEdges.map((e) => e.kind || 'advance')));
    const legendItems = usedKinds.map((k) => {
      const st = SPATIAL_EDGE_STYLES[k] || SPATIAL_EDGE_STYLES.advance;
      return (
        '<span class="spatial-leg-item"><i class="spatial-leg-line" style="border-top:2px ' +
        (st.dash === 'none' ? 'solid' : 'dashed') +
        ' ' +
        st.stroke +
        '"></i>' +
        esc(st.label) +
        '</span>'
      );
    });
    routeGroups.forEach((rg) => {
      legendItems.push(
        '<span class="spatial-leg-item"><i class="spatial-leg-dot" style="background:' +
          groupColor.get(rg) +
          '"></i>' +
          esc(rg) +
          '</span>'
      );
    });
    const legendBar =
      (legendItems.length ? '<div class="spatial-legend">' + legendItems.join('') + '</div>' : '') +
      (fallbackLegend.length
        ? '<div class="edge-legend">' +
          fallbackLegend
            .map((item) => '<span class="edge-legend-pill" data-edge-idx="' + item.idx + '">' + esc(item.text) + '</span>')
            .join('') +
          '</div>'
        : '');

    if (!wideMode) {
      const previewScale = 312 / W;
      const thumbList =
        '<div class="spatial-narrow-list">' +
        nodes
          .map(
            (n) =>
              '<div class="spatial-narrow-chip" data-jump="' +
              esc((n.blocks && n.blocks[0]) || '') +
              '"><b>' +
              esc(n.label) +
              '</b>' +
              (n.zone ? ' <span>(' + esc(n.zone) + ')</span>' : '') +
              ' ' +
              renderAnchorPills(n.blocks) +
              '</div>'
          )
          .join('') +
        '</div>';
      return (
        '<div class="spatial-preview-wrap" data-open-zoom title="点击展开全览空间态势图" style="height:' +
        Math.ceil(H * previewScale) +
        'px">' +
        '<div class="spatial-preview-scale" style="width:' +
        W +
        'px;transform:scale(' +
        previewScale.toFixed(4) +
        ')">' +
        canvasHtml +
        '</div>' +
        '<span class="spatial-zoom-hint">点击放大全览 ⤢</span>' +
        '</div>' +
        legendBar +
        thumbList
      );
    }

    return '<div class="spatial-viewport" data-qa-target="spatial">' + canvasHtml + '</div>' + legendBar;
  }

  function renderMatrixTableHtml(payload) {
    const cols = (payload.data && payload.data.columns) || [];
    const rows = (payload.data && payload.data.rows) || [];
    return (
      '<div style="overflow-x:auto" data-qa-target="table"><table class="bp-table"><thead><tr>' +
      cols.map((c) => '<th>' + esc(c) + '</th>').join('') +
      '</tr></thead><tbody>' +
      rows
        .map(
          (r) =>
            '<tr><td class="col-key">' +
            esc(r.key) +
            '<div style="margin-top:4px">' +
            renderSrcTag(r.source) +
            '</div></td><td>' +
            esc(r.everyday) +
            '</td><td>' +
            esc(r.author) +
            '</td><td>' +
            (r.extra ? esc(r.extra) + '</td><td>' : '') +
            renderAnchorPills(r.blocks) +
            '</td></tr>'
        )
        .join('') +
      '</tbody></table></div>'
    );
  }

  function renderSplineChartHtml(payload) {
    const pts = (payload.data && payload.data.points) || [];
    if (!pts.length) return '';
    const W = 440;
    const H = 200;
    const padL = 40;
    const padR = 24;
    const padT = 18;
    const padB = 46;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const coords = pts.map((p, i) => ({
      beat: p.beat,
      tension: Number(p.tension || 0),
      emotion: p.emotion,
      blocks: p.blocks,
      source: p.source,
      idx: i,
      x: padL + (pts.length === 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW),
      y: padT + (1 - Math.min(100, Math.max(0, Number(p.tension || 0))) / 100) * plotH,
    }));
    let pathD = 'M ' + coords[0].x + ',' + coords[0].y;
    for (let i = 0; i < coords.length - 1; i++) {
      const p0 = coords[i];
      const p1 = coords[i + 1];
      const cx = (p0.x + p1.x) / 2;
      pathD += ' C ' + cx + ',' + p0.y + ' ' + cx + ',' + p1.y + ' ' + p1.x + ',' + p1.y;
    }
    const areaD = pathD + ' L ' + coords[coords.length - 1].x + ',' + (padT + plotH) + ' L ' + coords[0].x + ',' + (padT + plotH) + ' Z';
    const gridY = [0, 25, 50, 75, 100]
      .map((v) => {
        const y = padT + (1 - v / 100) * plotH;
        return (
          '<line x1="' +
          padL +
          '" y1="' +
          y +
          '" x2="' +
          (W - padR) +
          '" y2="' +
          y +
          '" stroke="var(--rule)" stroke-dasharray="3 3"/>' +
          '<text x="' +
          (padL - 6) +
          '" y="' +
          (y + 3) +
          '" text-anchor="end" font-size="9.5" fill="var(--ink-3)">' +
          v +
          '</text>'
        );
      })
      .join('');
    const circles = coords
      .map((c) => {
        const bid = (c.blocks && c.blocks[0]) || '';
        const lblY = c.idx % 2 === 0 ? H - 22 : H - 7;
        return (
          '<g data-jump="' +
          esc(bid) +
          '" style="cursor:pointer"><circle class="chart-pt" cx="' +
          c.x +
          '" cy="' +
          c.y +
          '" r="5" fill="var(--desk-card, var(--paper))" stroke="var(--cinnabar)" stroke-width="2.2"/><text x="' +
          c.x +
          '" y="' +
          lblY +
          '" text-anchor="middle" font-size="9.5" fill="var(--ink-2)">' +
          esc(c.beat) +
          '</text></g>'
        );
      })
      .join('');
    return (
      '<svg class="chart-svg" viewBox="0 0 ' +
      W +
      ' ' +
      H +
      '" data-qa-target="chart">' +
      gridY +
      '<path d="' +
      areaD +
      '" fill="var(--cinnabar-soft)" opacity=".65"/><path d="' +
      pathD +
      '" fill="none" stroke="var(--cinnabar)" stroke-width="2.5" stroke-linecap="round"/>' +
      circles +
      '</svg><div class="chart-legend-list">' +
      coords
        .map(
          (c) =>
            '<div class="chart-legend-item" data-jump="' +
            esc((c.blocks && c.blocks[0]) || '') +
            '"><span><b>' +
            esc(c.beat) +
            '</b> · <span style="color:var(--ink-2)">' +
            esc(c.emotion) +
            '</span></span><span>' +
            renderSrcTag(c.source) +
            ' ' +
            renderAnchorPills(c.blocks) +
            '</span></div>'
        )
        .join('') +
      '</div>'
    );
  }

  function renderExtensionItemHtml(it) {
    const headTxt = it.name || it.title || it.label || '';
    const badge = it.stance || it.role_badge || '';
    return (
      '<div class="ext-item">' +
      (headTxt || badge || it.source
        ? '<div class="ext-item-head">' +
          '<div class="ext-item-title-wrap">' +
          (headTxt ? '<b class="ext-item-title">' + esc(headTxt) + '</b>' : '') +
          (badge ? '<span class="ext-role-badge">' + esc(badge) + '</span>' : '') +
          '</div>' +
          renderSrcTag(it.source) +
          '</div>'
        : '') +
      (it.title && it.name && it.title !== it.name ? '<div class="ext-item-subhead">' + esc(it.title) + '</div>' : '') +
      (it.body || it.detail ? '<div class="ext-sub">' + esc(it.body || it.detail) + '</div>' : '') +
      (it.warning ? '<div class="ext-sub" style="color:var(--ochre)">提示：' + esc(it.warning) + '</div>' : '') +
      '<div>' +
      renderAnchorPills(it.blocks) +
      '</div></div>'
    );
  }

  function renderExtensionOrFallbackHtml(payload) {
    const noticeHtml = payload.fallback_notice
      ? '<div class="bp-fallback-banner">' + esc(payload.fallback_notice) + '</div>'
      : '';
    if (Array.isArray(payload.sections) && payload.sections.length) {
      return (
        noticeHtml +
        '<div class="ext-sections">' +
        payload.sections
          .map(
            (sec) =>
              '<div class="ext-section">' +
              '<h5 class="ext-sec-title">' +
              esc(sec.title) +
              '</h5>' +
              '<div class="ext-list">' +
              (sec.items || []).map(renderExtensionItemHtml).join('') +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    }

    const items = payload.items || [];
    if (payload.blueprint === 'extension_checklist') {
      return (
        noticeHtml +
        '<div class="ext-list">' +
        items
          .map(
            (it) =>
              '<div class="ext-item"><label class="ext-check-label"><input type="checkbox"' +
              (it.checked ? ' checked' : '') +
              '><span>' +
              esc(it.text || it.title) +
              '</span></label>' +
              (it.detail || it.body ? '<div class="ext-sub">' + esc(it.detail || it.body) + '</div>' : '') +
              '<div style="display:flex;justify-content:space-between;align-items:center">' +
              renderSrcTag(it.source) +
              ' ' +
              renderAnchorPills(it.blocks) +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    }
    return noticeHtml + '<div class="ext-list">' + items.map(renderExtensionItemHtml).join('') + '</div>';
  }

  function renderBlueprintBody(payload, options) {
    if (!payload || typeof payload !== 'object') return '';
    const bp = payload.blueprint || 'flow_swimlane';
    if (bp === 'parallel_timeline') return renderTimelineHtml(payload, options);
    if (bp === 'spatial_route') return renderSpatialMapHtml(payload, options);
    if (MATRIX_BLUEPRINTS.has(bp)) return renderMatrixTableHtml(payload);
    if (bp === 'quadrant_matrix') return renderQuadrantHtml(payload);
    if (CURVE_BLUEPRINTS.has(bp)) return renderSplineChartHtml(payload);
    if (bp === 'extension_dossier' || bp === 'extension_checklist' || bp === 'fallback_list') {
      return renderExtensionOrFallbackHtml(payload);
    }
    if (DAG_BLUEPRINTS.has(bp) || (payload.data && Array.isArray(payload.data.nodes))) {
      return renderLayeredDagHtml(payload, options);
    }
    return renderExtensionOrFallbackHtml(payload);
  }

  function runCollisionAudit(stageEl) {
    const report = { overlaps: 0, edgeFallbackCount: 0, details: [] };
    if (!stageEl || typeof stageEl.querySelectorAll !== 'function') return report;
    const toBox = (r) => ({ x: r.left, y: r.top, w: r.width, h: r.height });
    const nodes = Array.from(stageEl.querySelectorAll('.dag-node, .spatial-node'));
    const labels = Array.from(stageEl.querySelectorAll('.dag-edge-label, .spatial-edge-label'));

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = toBox(nodes[i].getBoundingClientRect());
        const b = toBox(nodes[j].getBoundingClientRect());
        if (a.w > 0 && b.w > 0 && rectsOverlap(a, b, 1)) {
          report.overlaps++;
          report.details.push((nodes[i].dataset.nodeId || 'N') + '↔' + (nodes[j].dataset.nodeId || 'N'));
        }
      }
    }
    labels.forEach((lbl, idx) => {
      const lb = toBox(lbl.getBoundingClientRect());
      if (lb.w <= 0) return;
      nodes.forEach((nd) => {
        const nb = toBox(nd.getBoundingClientRect());
        if (nb.w > 0 && rectsOverlap(lb, nb, 0)) {
          report.overlaps++;
          report.details.push('线标压节点');
        }
      });
      for (let k = idx + 1; k < labels.length; k++) {
        const lb2 = toBox(labels[k].getBoundingClientRect());
        if (lb2.w > 0 && rectsOverlap(lb, lb2, 0)) {
          report.overlaps++;
          report.details.push('线标互撞');
        }
      }
    });
    const yAxis = stageEl.querySelector('.quad-axis-y');
    const xAxis = stageEl.querySelector('.quad-axis-x');
    const cells = Array.from(stageEl.querySelectorAll('.quad-cell'));
    if (yAxis) {
      const yb = toBox(yAxis.getBoundingClientRect());
      cells.forEach((c) => {
        const cb = toBox(c.getBoundingClientRect());
        if (yb.w > 0 && cb.w > 0 && rectsOverlap(yb, cb, 0)) report.overlaps++;
      });
    }
    if (xAxis) {
      const xb = toBox(xAxis.getBoundingClientRect());
      cells.forEach((c) => {
        const cb = toBox(c.getBoundingClientRect());
        if (xb.w > 0 && cb.w > 0 && rectsOverlap(xb, cb, 0)) report.overlaps++;
      });
    }
    report.edgeFallbackCount = stageEl.querySelectorAll('.edge-legend-pill').length;
    return report;
  }

  function bindStageInteractions(rootEl, onFocusBlock, onZoomRequest) {
    if (!rootEl) return;
    rootEl.querySelectorAll('[data-jump]').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target && e.target.hasAttribute('data-toggle-detail')) {
          e.stopPropagation();
          el.classList.toggle('expanded');
          const more = e.target;
          if (more) more.textContent = el.classList.contains('expanded') ? '▴ 收起' : '▾ 查看展开';
          return;
        }
        e.stopPropagation();
        const bid = el.getAttribute('data-jump');
        if (bid && onFocusBlock) onFocusBlock(bid);
      });
    });

    rootEl.querySelectorAll('[data-tl-lane]').forEach((pill) => {
      pill.addEventListener('click', () => {
        const target = pill.getAttribute('data-tl-lane');
        rootEl.querySelectorAll('[data-tl-lane]').forEach((p) => p.classList.toggle('active', p === pill));
        rootEl.querySelectorAll('.tl-row-group').forEach((grp) => {
          let visibleCount = 0;
          grp.querySelectorAll('.tl-card[data-lane-idx]').forEach((card) => {
            const show = target === 'all' || card.getAttribute('data-lane-idx') === target;
            card.hidden = !show;
            if (show) visibleCount++;
          });
          grp.hidden = visibleCount === 0;
        });
      });
    });

    rootEl.querySelectorAll('[data-power-view]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const view = btn.getAttribute('data-power-view');
        rootEl.querySelectorAll('[data-power-view]').forEach((b) => b.classList.toggle('active', b === btn));
        rootEl.querySelectorAll('[data-power-panel]').forEach((pnl) => {
          pnl.hidden = pnl.getAttribute('data-power-panel') !== view;
        });
      });
    });

    if (onZoomRequest) {
      rootEl.querySelectorAll('[data-open-zoom]').forEach((el) => {
        el.addEventListener('click', (e) => {
          if (e.target.closest('[data-jump], [data-toggle-detail]')) return;
          e.stopPropagation();
          onZoomRequest();
        });
      });
    }

    const canvas = rootEl.querySelector('.dag-canvas, .spatial-canvas');
    if (!canvas) return;
    const tipEl = rootEl.querySelector('[data-trace-tip]');
    const defaultTip = '悬停节点或连线可高亮因果路径；点击节点跳转原文';
    const nodeEls = Array.from(canvas.querySelectorAll('.dag-node[data-node-id], .spatial-node[data-node-id]'));
    const edgeGroups = Array.from(canvas.querySelectorAll('.dag-edge-group[data-edge-idx]'));
    const labelEls = Array.from(canvas.querySelectorAll('.dag-edge-label[data-edge-idx], .spatial-edge-label[data-edge-idx]'));
    const legendEls = Array.from(rootEl.querySelectorAll('.edge-legend-pill[data-edge-idx]'));

    const nodeById = new Map(nodeEls.map((el) => [el.dataset.nodeId, el]));
    const clearTrace = () => {
      canvas.classList.remove('is-tracing');
      nodeEls.forEach((n) => n.classList.remove('is-hot', 'is-lit'));
      edgeGroups.forEach((g) => g.classList.remove('is-lit'));
      labelEls.forEach((l) => l.classList.remove('is-lit'));
      legendEls.forEach((p) => p.classList.remove('is-lit'));
      if (tipEl) {
        tipEl.classList.remove('active');
        tipEl.textContent = defaultTip;
      }
    };

    const markEdgeIdx = (idx, lit) => {
      edgeGroups.forEach((g) => {
        if (g.dataset.edgeIdx === idx) g.classList.toggle('is-lit', lit);
      });
      labelEls.forEach((l) => {
        if (l.dataset.edgeIdx === idx) l.classList.toggle('is-lit', lit);
      });
      legendEls.forEach((p) => {
        if (p.dataset.edgeIdx === idx) p.classList.toggle('is-lit', lit);
      });
    };

    const traceNode = (nodeId) => {
      clearTrace();
      const target = nodeById.get(nodeId);
      if (!target) return;
      canvas.classList.add('is-tracing');
      target.classList.add('is-hot');
      let inCnt = 0;
      let outCnt = 0;
      edgeGroups.forEach((g) => {
        if (g.dataset.from === nodeId || g.dataset.to === nodeId) {
          if (g.dataset.from === nodeId) outCnt += 1;
          if (g.dataset.to === nodeId) inCnt += 1;
          markEdgeIdx(g.dataset.edgeIdx, true);
          const fEl = nodeById.get(g.dataset.from);
          const tEl = nodeById.get(g.dataset.to);
          if (fEl && g.dataset.from !== nodeId) fEl.classList.add('is-lit');
          if (tEl && g.dataset.to !== nodeId) tEl.classList.add('is-lit');
        }
      });
      if (tipEl) {
        const titleEl = target.querySelector('.dag-node-label, .spatial-node-label');
        const labelTxt = titleEl ? titleEl.textContent.trim() : nodeId;
        tipEl.classList.add('active');
        tipEl.textContent = '节点「' + labelTxt + '」· 入线 ' + inCnt + ' · 出线 ' + outCnt + '（点击跳转原文）';
      }
    };

    const traceEdge = (idx) => {
      clearTrace();
      const grp = edgeGroups.find((g) => g.dataset.edgeIdx === idx);
      if (!grp) return;
      canvas.classList.add('is-tracing');
      markEdgeIdx(idx, true);
      const fEl = nodeById.get(grp.dataset.from);
      const tEl = nodeById.get(grp.dataset.to);
      if (fEl) fEl.classList.add('is-hot');
      if (tEl) tEl.classList.add('is-hot');
      if (tipEl) {
        tipEl.classList.add('active');
        tipEl.textContent = '关系：' + (grp.dataset.edgeDesc || '');
      }
    };

    nodeEls.forEach((n) => {
      const nid = n.dataset.nodeId;
      n.addEventListener('mouseenter', () => traceNode(nid));
      n.addEventListener('focusin', () => traceNode(nid));
      n.addEventListener('mouseleave', clearTrace);
    });
    edgeGroups.forEach((g) => {
      const hit = g.querySelector('.dag-edge-hit');
      if (hit) {
        hit.addEventListener('mouseenter', () => traceEdge(g.dataset.edgeIdx));
        hit.addEventListener('mouseleave', clearTrace);
      }
    });
    labelEls.forEach((l) => {
      l.addEventListener('mouseenter', () => traceEdge(l.dataset.edgeIdx));
      l.addEventListener('mouseleave', clearTrace);
    });
    legendEls.forEach((p) => {
      p.addEventListener('mouseenter', () => traceEdge(p.dataset.edgeIdx));
      p.addEventListener('mouseleave', clearTrace);
    });
    if (rootEl._dagMouseLeave) {
      rootEl.removeEventListener('mouseleave', rootEl._dagMouseLeave);
    }
    rootEl._dagMouseLeave = clearTrace;
    rootEl.addEventListener('mouseleave', clearTrace);
  }

  function openZoomModal(figure, onFocusBlock) {
    lastZoomTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    let modal = document.getElementById('zoom-modal');
    if (!modal) {
      modal = document.createElement('dialog');
      modal.id = 'zoom-modal';
      modal.className = 'zoom-modal zoom-dialog';
      modal.setAttribute('closedby', 'any');
      modal.setAttribute('aria-labelledby', 'zoom-title');
      modal.innerHTML =
        '<div class="zoom-head"><h4 id="zoom-title">宽屏全览</h4>' +
        '<div class="zoom-actions">' +
        '<button type="button" class="wb-btn" id="btn-zoom-expand-all" aria-pressed="false" hidden>展开全部说明</button>' +
        '<div class="zoom-controls" role="group" aria-label="画布缩放">' +
        '<button type="button" class="zoom-btn" id="btn-zoom-out" title="缩小" aria-label="缩小画布">−</button>' +
        '<button type="button" class="zoom-btn zoom-pct" id="btn-zoom-fit" title="切换适应画布 / 100%">100%</button>' +
        '<button type="button" class="zoom-btn" id="btn-zoom-in" title="放大" aria-label="放大画布">+</button>' +
        '</div>' +
        '<span id="zoom-qa" class="qa-badge">✓ 宽屏质检</span>' +
        '<button type="button" class="wb-btn" id="btn-zoom-close" commandfor="zoom-modal" command="close">关闭 Esc</button>' +
        '</div></div>' +
        '<div class="zoom-stage"><div class="zoom-body" id="zoom-body"></div></div>';
      document.body.appendChild(modal);

      const closeBtn = document.getElementById('btn-zoom-close');
      if (closeBtn && !('commandForElement' in HTMLButtonElement.prototype)) {
        closeBtn.addEventListener('click', () => closeZoomModal(modal));
      }
      modal.addEventListener('cancel', (event) => {
        event.preventDefault();
        closeZoomModal(modal);
      });
      if (!('closedBy' in HTMLDialogElement.prototype)) {
        modal.addEventListener('click', (event) => {
          if (event.target !== modal) return;
          const rect = modal.getBoundingClientRect();
          const inContent =
            rect.top <= event.clientY &&
            event.clientY <= rect.top + rect.height &&
            rect.left <= event.clientX &&
            event.clientX <= rect.left + rect.width;
          if (!inContent) closeZoomModal(modal);
        });
      }
      modal.addEventListener('close', () => {
        modal.removeAttribute('data-closing');
        if (lastZoomTrigger && typeof lastZoomTrigger.focus === 'function') {
          lastZoomTrigger.focus();
        }
      });
    }
    modal.removeAttribute('data-closing');
    document.getElementById('zoom-title').textContent = (figure.caption || '图表全览') + '（宽屏画布 · 点击节点直跳原文）';
    const body = document.getElementById('zoom-body');
    const stage = modal.querySelector('.zoom-stage');
    const hasDetails = Boolean(
      figure && figure.data && Array.isArray(figure.data.nodes) && figure.data.nodes.some((n) => n && n.detail)
    );
    let expandAll = false;
    const expandBtn = document.getElementById('btn-zoom-expand-all');
    if (expandBtn) {
      expandBtn.hidden = !hasDetails;
      expandBtn.setAttribute('aria-pressed', 'false');
      expandBtn.textContent = '展开全部说明';
    }

    if (typeof modal.showModal === 'function') {
      if (!modal.open) modal.showModal();
    } else {
      modal.classList.add('open');
    }

    const fitBtn = document.getElementById('btn-zoom-fit');
    const outBtn = document.getElementById('btn-zoom-out');
    const inBtn = document.getElementById('btn-zoom-in');
    let natW = 0;
    let natH = 0;
    let fitScale = 1;
    let curScale = 1;
    let manualZoom = false;

    const applyScale = (next) => {
      curScale = Math.max(0.3, Math.min(1.5, Number(next.toFixed(2))));
      const wrap = body.querySelector('.zoom-canvas-wrap');
      const scaler = body.querySelector('.zoom-canvas-scale');
      if (wrap && scaler) {
        if (natW > 0) {
          scaler.style.width = natW + 'px';
          scaler.style.transform = curScale === 1 ? '' : 'scale(' + curScale + ')';
          wrap.style.width = Math.ceil(natW * curScale) + 'px';
          wrap.style.height = Math.ceil(natH * curScale) + 'px';
        } else {
          scaler.style.width = '';
          scaler.style.transform = curScale === 1 ? '' : 'scale(' + curScale + ')';
          wrap.style.width = '';
          wrap.style.height = '';
        }
      }
      if (fitBtn) {
        const isFit = fitScale < 0.96 && Math.abs(curScale - fitScale) < 0.03;
        fitBtn.classList.toggle('active', isFit);
        fitBtn.textContent = isFit
          ? Math.round(curScale * 100) + '% 适应'
          : Math.round(curScale * 100) + '%';
      }
    };

    const paintWide = (preserveScale) => {
      body.innerHTML =
        '<div class="zoom-canvas-wrap"><div class="zoom-canvas-scale">' +
        renderBlueprintBody(figure, { wide: true, expandAll }) +
        '</div></div>';
      const qa = runCollisionAudit(body);
      const qaBadge = document.getElementById('zoom-qa');
      qaBadge.className = 'qa-badge' + (qa.overlaps > 0 ? ' warn' : '');
      qaBadge.textContent = qa.overlaps === 0 ? '✓ 宽屏质检：0 处重叠' : '⚠ 发现 ' + qa.overlaps + ' 处重叠';
      bindStageInteractions(body, onFocusBlock);

      const scaler = body.querySelector('.zoom-canvas-scale');
      const wideCanvas = body.querySelector('.dag-canvas, .spatial-canvas, .lanes-head');
      const isTableCanvas = Boolean(wideCanvas && wideCanvas.classList.contains('lanes-head'));
      natW = 0;
      natH = 0;
      fitScale = 1;
      if (wideCanvas && stage && scaler) {
        natW = Math.max(scaler.scrollWidth, wideCanvas.scrollWidth + 8);
        natH = Math.max(scaler.scrollHeight, 120);
        const availW = Math.max(320, stage.clientWidth - 76);
        const availH = Math.max(240, stage.clientHeight - 76);
        fitScale = isTableCanvas
          ? Math.max(0.5, Math.min(1, availW / natW))
          : Math.max(0.32, Math.min(1, availW / natW, availH / natH));
        if (!preserveScale || !manualZoom) {
          curScale = fitScale < 0.96 ? Number(fitScale.toFixed(2)) : 1;
        }
      } else if (!preserveScale || !manualZoom) {
        curScale = 1;
      }
      applyScale(curScale);
    };

    paintWide(false);

    if (expandBtn) {
      expandBtn.onclick = () => {
        expandAll = !expandAll;
        expandBtn.setAttribute('aria-pressed', String(expandAll));
        expandBtn.textContent = expandAll ? '收起全部说明' : '展开全部说明';
        paintWide(true);
      };
    }
    if (outBtn) {
      outBtn.onclick = () => {
        manualZoom = true;
        applyScale(curScale - 0.15);
      };
    }
    if (inBtn) {
      inBtn.onclick = () => {
        manualZoom = true;
        applyScale(curScale + 0.15);
      };
    }
    if (fitBtn) {
      fitBtn.onclick = () => {
        if (fitScale < 0.96 && Math.abs(curScale - fitScale) >= 0.03) {
          manualZoom = false;
          applyScale(fitScale);
        } else {
          manualZoom = true;
          applyScale(1);
        }
      };
    }
  }

  function renderBlueprintCard(containerEl, figure, options) {
    if (!containerEl || !figure) return;
    const opts = options || {};
    const onFocusBlock = opts.onFocusBlock;
    const compact = Boolean(opts.compact);
    let viewMode = 'render';
    let showInspect = false;

    const paint = () => {
      const auditLogs = figure.audit_log || [];
      let inner = '';
      if (viewMode === 'json') {
        inner = '<pre class="json-box">' + esc(JSON.stringify(figure, null, 2)) + '</pre>';
      } else if (viewMode === 'audit') {
        inner =
          '<div class="pipe-step"><h5>结构审计（' +
          auditLogs.length +
          ' 条）</h5><ul style="margin:4px 0 0;padding-left:18px">' +
          (auditLogs.length
            ? auditLogs.map((l) => '<li>' + esc(l) + '</li>').join('')
            : '<li>字段与正文锚点均已校验。</li>') +
          '</ul></div>';
      } else {
        inner = '<div class="bp-stage-inner">' + renderBlueprintBody(figure, { wide: false }) + '</div>';
      }

      containerEl.innerHTML =
        '<div class="bp-card">' +
        '<div class="bp-card-head">' +
        '<h4 class="bp-title">' +
        esc(figure.caption || '结构图解') +
        '</h4>' +
        '<div class="bp-head-right">' +
        '<button type="button" class="bp-zoom-btn" data-bp-zoom title="宽屏展开查看">全览 ⤢</button>' +
        '</div></div>' +
        (figure.reading_note
          ? '<div class="bp-note">' + esc(figure.reading_note) + '</div>'
          : '') +
        (!compact && showInspect
          ? '<div class="bp-mode-row"><div class="view-mode-tabs">' +
            '<button type="button" data-bp-mode="render" class="' +
            (viewMode === 'render' ? 'active' : '') +
            '">图解</button>' +
            '<button type="button" data-bp-mode="audit" class="' +
            (viewMode === 'audit' ? 'active' : '') +
            '">审计</button>' +
            '<button type="button" data-bp-mode="json" class="' +
            (viewMode === 'json' ? 'active' : '') +
            '">原始数据</button>' +
            '</div><span class="qa-badge" data-qa-pill></span></div>'
          : '') +
        inner +
        '<div class="bp-engine-foot">' +
        '<span class="bp-foot-legend">' +
        renderSrcTag('原文') +
        ' 书中依据 · ' +
        renderSrcTag('补充') +
        ' 延伸</span>' +
        (!compact
          ? '<button type="button" class="bp-inspect-toggle" data-bp-inspect>' +
            (showInspect ? '收起数据' : '数据与审计') +
            '</button>'
          : '') +
        '</div>' +
        '</div>';

      if (viewMode === 'render') {
        const stage = containerEl.querySelector('.bp-stage-inner');
        const qa = runCollisionAudit(stage);
        const pill = containerEl.querySelector('[data-qa-pill]');
        if (pill) {
          pill.className = 'qa-badge' + (qa.overlaps > 0 ? ' warn' : '');
          pill.textContent =
            qa.overlaps === 0
              ? '排版无重叠' + (qa.edgeFallbackCount ? '（' + qa.edgeFallbackCount + '条转图例）' : '')
              : '重叠：' + qa.details.join('；');
        }
        bindStageInteractions(stage, onFocusBlock, () => openZoomModal(figure, onFocusBlock));
      }

      const zoomBtn = containerEl.querySelector('[data-bp-zoom]');
      if (zoomBtn) {
        zoomBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openZoomModal(figure, onFocusBlock);
        });
      }
      const inspectBtn = containerEl.querySelector('[data-bp-inspect]');
      if (inspectBtn) {
        inspectBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          showInspect = !showInspect;
          if (!showInspect) viewMode = 'render';
          paint();
        });
      }
      containerEl.querySelectorAll('[data-bp-mode]').forEach((btn) => {
        btn.addEventListener('click', () => {
          viewMode = btn.getAttribute('data-bp-mode');
          paint();
        });
      });
    };

    paint();
  }

  function renderChapterMapTree(cmap) {
    const sections = (cmap && cmap.sections) || [];
    if (!sections.length) return '';
    const title = (sections[0] && sections[0].title) || '全章脉络';
    return (
      '<div class="map-tree-wrap"><div class="tree-root-row">' +
      '<div class="tree-branches">' +
      sections
        .map(
          (sec, idx) =>
            '<div class="tree-branch">' +
            '<div class="tree-sec-head">' +
            '<span class="tree-sec-num">' +
            String(idx + 1).padStart(2, '0') +
            '</span>' +
            '<div class="tree-sec-title">' +
            esc(sec.title || title) +
            (sec.summary ? '<small>' + esc(sec.summary) + '</small>' : '') +
            '</div></div>' +
            '<div class="tree-leaf-list">' +
            (sec.nodes || [])
              .map((node) => {
                const target = (node.block_ids || sec.block_ids || [])[0] || '';
                return (
                  '<button type="button" class="tree-leaf" data-jump="' +
                  esc(target) +
                  '">' +
                  '<span class="tree-leaf-txt">' +
                  esc(node.summary || node.id) +
                  '</span>' +
                  (target ? '<span class="tree-leaf-ref">' + esc(target) + '</span>' : '') +
                  '</button>'
                );
              })
              .join('') +
            '</div></div>'
        )
        .join('') +
      '</div></div></div>'
    );
  }

  RD.diagrams = {
    renderBlueprintCard,
    renderChapterMapTree,
    openZoomModal,
    runCollisionAudit,
  };
})();
