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
    // 哲学·批判谱系
    predecessor: '前辈渊源',
    mediator: '转译改造',
    target: '批驳靶子',
    author: '作者立场',
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
    // 实用·SOP 流程
    input: '前置输入',
    action: '核心动作',
    gate: '检查门禁',
    pitfall: '避坑警报',
    output: '阶段产出',
    // 实用·条件决策树
    diagnosis: '场景诊断',
    strategy: '推荐动作',
    fallback: '止损/替代',
    // 文学·人物与空间
    protagonist: '核心人物',
    antagonist: '对立/施压',
    catalyst: '关键变数',
    observer: '旁观/群像',
    home: '居所起点',
    public: '公共场所',
    threshold: '门窗界限',
    transit: '通道街道',
    destination: '枢纽终点',
    prop: '核心道具',
    zone: '舞台区域',
    outside: '窗外/场外',
    // 科学·实验推理与机制流
    observation: '观测现象',
    hypothesis: '假说模型',
    experiment: '实验检验',
    finding: '科学结论',
    input_energy: '输入/激励',
    component: '部件/介质',
    process: '物理/演化过程',
    bottleneck: '阻断/约束',
    output_effect: '宏观效应',
  };

  const SOCIAL_CAUSAL_ROLE_ZH = {
    driver: '核心驱动',
    policy: '制度干预',
    mechanism: '传导机制',
    outcome: '宏观结果',
    lockin: '锁定/风险',
  };
  const SOCIAL_CAUSAL_ROLE_ORDER = ['driver', 'policy', 'mechanism', 'outcome', 'lockin'];

  function roleLabel(role, bpOrLineage, blueprint) {
    const bp = blueprint || (typeof bpOrLineage === 'string' ? bpOrLineage : '');
    if (bp === 'causal_loop' && SOCIAL_CAUSAL_ROLE_ZH[role]) return SOCIAL_CAUSAL_ROLE_ZH[role];
    if ((bpOrLineage === true || bpOrLineage === 'lineage_dag') && role === 'ally') return '同调佐证';
    if (bpOrLineage === 'decision_tree' && role === 'condition') return '判断条件';
    if (bpOrLineage === 'derivation_dag') {
      if (role === 'axiom') return '公理/假设';
      if (role === 'condition') return '边界条件';
      if (role === 'lemma') return '引理/推论';
      if (role === 'theorem') return '核心定理';
    }
    return ROLE_ZH[role] || role || '节点';
  }

  const LINEAGE_EDGE_STYLES = {
    inherit: { label: '继承', stroke: 'var(--pine)', dash: 'none', marker: 'green', cls: 'edge-pos' },
    transform: { label: '改造', stroke: 'var(--indigo)', dash: 'none', marker: 'indigo', cls: 'edge-skip' },
    critique: { label: '批驳', stroke: 'var(--cinnabar)', dash: '6 3', marker: 'red', cls: 'edge-neg' },
    contrast: { label: '对照', stroke: 'var(--amber)', dash: '5 3', marker: 'amber', cls: 'edge-back' },
  };

  const BEAT_KIND_META = {
    setup: { label: '铺垫', color: 'var(--indigo)', cls: 'bk-setup' },
    foreshadow: { label: '伏笔', color: 'var(--amber)', cls: 'bk-foreshadow' },
    relief: { label: '舒缓', color: 'var(--pine)', cls: 'bk-relief' },
    escalation: { label: '升级', color: 'var(--cinnabar)', cls: 'bk-escalation' },
    climax: { label: '顶点', color: 'var(--cinnabar)', cls: 'bk-climax' },
    aftermath: { label: '留白', color: 'var(--ink-3)', cls: 'bk-aftermath' },
  };

  const REL_KIND_META = {
    bond: { stroke: 'var(--pine)', dash: 'none', cls: 'edge-pos', marker: 'green', label: '亲情/爱恋' },
    conflict: { stroke: 'var(--cinnabar)', dash: 'none', cls: 'edge-neg', marker: 'red', label: '冲突/对抗' },
    asymmetry: { stroke: 'var(--amber)', dash: '6 3', cls: 'edge-back', marker: 'amber', label: '隐瞒/错位' },
    family: { stroke: 'var(--indigo)', dash: 'none', cls: 'edge-skip', marker: 'indigo', label: '血缘/姻亲' },
  };

  const ACTOR_PALETTE = ['var(--cinnabar)', 'var(--pine)', 'var(--indigo)', 'var(--amber)', '#7c4d2b', '#2b6a7c'];

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
  const CURVE_BLUEPRINTS = new Set(['tension_curve', 'metric_chart']);

  function esc(s) {
    return RD.esc ? RD.esc(s) : String(s == null ? '' : s);
  }

  function renderAnchorPills(blocks, maxCount) {
    const list = Array.isArray(blocks) ? blocks.filter(Boolean) : [];
    if (!list.length) return '';
    const cap = maxCount && maxCount > 0 ? maxCount : list.length;
    const pill = (b) =>
      '<button type="button" class="blk-anchor" data-jump="' +
      esc(b) +
      '" title="点击定位原文 ' +
      esc(b) +
      '">↗ ' +
      esc(b) +
      '</button>';
    if (list.length <= cap) return list.map(pill).join(' ');
    const head = list.slice(0, cap).map(pill).join(' ');
    const rest = list.slice(cap);
    return (
      head +
      ' <button type="button" class="blk-anchor blk-anchor-more" data-expand-anchors title="展开其余 ' +
      rest.length +
      ' 个块号：' +
      esc(rest.join(', ')) +
      '">+' +
      rest.length +
      '</button><span class="blk-anchor-extra" hidden>' +
      rest.map(pill).join(' ') +
      '</span>'
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

  function measureNodeSizes(nodes, cardWidth, optsOrExpand, maybeMaxAnchors) {
    const opts =
      typeof optsOrExpand === 'object' && optsOrExpand !== null
        ? optsOrExpand
        : { expandAll: Boolean(optsOrExpand), maxAnchors: maybeMaxAnchors };
    const stage = ensureMeasureStage();
    const sizes = new Map();
    const expandAll = Boolean(opts.expandAll);
    const expandedIds = opts.expandedIds || null;
    const expandedAnchorIds = opts.expandedAnchorIds || null;
    const maxAnchors = opts.maxAnchors !== undefined ? opts.maxAnchors : 3;
    const showArc = Boolean(opts.showArc);
    const showZone = Boolean(opts.showZone);
    const isLineage = Boolean(opts.isLineage);
    const roleCtx = opts.blueprint || (isLineage ? 'lineage_dag' : '');
    stage.style.width = cardWidth + 'px';
    stage.innerHTML = nodes
      .map((n) => {
        const isExp = Boolean((expandAll || (expandedIds && expandedIds.has(n.id))) && n.detail);
        const cap = expandedAnchorIds && expandedAnchorIds.has(n.id) ? 0 : maxAnchors;
        return (
          '<div class="dag-node role-' +
          esc(n.role || 'inference') +
          (isExp ? ' expanded' : '') +
          '" style="position:relative;width:' +
          cardWidth +
          'px">' +
          '<div class="dag-node-role"><span>' +
          esc(roleLabel(n.role, roleCtx)) +
          (showZone && n.zone ? ' · ' + esc(n.zone) : '') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="dag-node-label">' +
          esc(n.label) +
          '</div>' +
          (showArc && n.arc ? '<div class="dag-node-arc">' + esc(n.arc) + '</div>' : '') +
          (n.detail
            ? '<div class="dag-node-more">' +
              (isExp ? '▴ 收起说明' : '▾ 查看展开') +
              '</div><div class="dag-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, cap) +
          '</div></div>'
        );
      })
      .join('');
    const els = stage.querySelectorAll('.dag-node');
    nodes.forEach((n, idx) => {
      const rect = els[idx] ? els[idx].getBoundingClientRect() : null;
      if (rect && rect.height > 20) {
        sizes.set(n.id, { w: Math.ceil(rect.width), h: Math.ceil(rect.height) });
      } else {
        const chars = String(n.label || '').length;
        const lines = Math.max(1, Math.ceil((chars * 12.5) / Math.max(80, cardWidth - 24)));
        const isExp = Boolean((expandAll || (expandedIds && expandedIds.has(n.id))) && n.detail);
        const detLines = isExp ? Math.max(1, Math.ceil(String(n.detail || '').length / 13)) : 0;
        sizes.set(n.id, { w: cardWidth, h: 58 + lines * 18 + (n.detail ? 16 : 0) + (detLines ? detLines * 16 + 10 : 0) });
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

  const DAG_ROOT_ROLES = new Set([
    'premise',
    'axiom',
    'observation',
    'input_energy',
    'condition',
    'sovereign',
    'structural',
    'trigger',
    'capital',
    'input',
    'diagnosis',
  ]);

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
      const polRaw = String(e.polarity || (rev && rev.polarity) || '').trim();
      const polarity = polRaw === '+' || polRaw === '-' ? polRaw : '';
      merged.push({
        idx: merged.length,
        from: e.from,
        to: e.to,
        label,
        relation: String(e.relation || e.relation_type || (rev && (rev.relation || rev.relation_type)) || '').trim(),
        note: notes.join('；'),
        relationType: relType,
        rel_kind: e.rel_kind || (rev && rev.rel_kind) || '',
        kind: e.kind || '',
        actor: e.actor || '',
        polarity,
        delay: e.delay === true || (rev && rev.delay === true),
        bidirectional: Boolean(rev) || relType === 'balance',
        isSelfLoop: e.from === e.to,
        isCycleBack: e.from === e.to,
        isNeg: polarity === '-' || relType === 'hostile' || isNegativeEdgeLabel(l1) || isNegativeEdgeLabel(l2),
      });
    }
    return merged;
  }

  function assignLineageRanks(nodes, idMap, mergedEdges) {
    // 以作者立场（author）为汇聚核心的分群列排布：
    // 左侧前辈渊源/同调/转译逐级汇入 author，右侧批驳靶子与 author 张力对峙。
    const zoneOf = (role) => {
      if (role === 'target') return 'target';
      if (role === 'author') return 'author';
      if (role === 'mediator') return 'mediator';
      if (role === 'ally') return 'ally';
      return 'predecessor';
    };

    const leftNodes = [];
    const authorNodes = [];
    const targetNodes = [];
    nodes.forEach((raw) => {
      const n = idMap.get(raw.id);
      const z = zoneOf(n.role);
      if (z === 'author') authorNodes.push(n);
      else if (z === 'target') targetNodes.push(n);
      else leftNodes.push(n);
    });

    leftNodes.forEach((n) => {
      n.rank = zoneOf(n.role) === 'mediator' ? 1 : 0;
    });

    // 若左区存在同区因果推进边，适当顺推至后续子列以免同列自遮挡
    for (let pass = 0; pass < leftNodes.length; pass++) {
      let changed = false;
      mergedEdges.forEach((e) => {
        if (e.isSelfLoop) return;
        const fn = idMap.get(e.from);
        const tn = idMap.get(e.to);
        if (!fn || !tn) return;
        if (zoneOf(fn.role) !== 'author' && zoneOf(fn.role) !== 'target' && zoneOf(tn.role) !== 'author' && zoneOf(tn.role) !== 'target') {
          if (tn.rank <= fn.rank && fn.rank < 2) {
            tn.rank = fn.rank + 1;
            changed = true;
          }
        }
      });
      if (!changed) break;
    }

    const maxLeftRank = leftNodes.length ? Math.max(...leftNodes.map((n) => n.rank)) : -1;
    const authorRank = maxLeftRank + 1;
    const targetBaseRank = authorRank + (authorNodes.length || leftNodes.length ? 1 : 0);

    // 单列超过 5 张卡时自动切分为多个子列，防止宽屏下出现 8 卡竖排“烟囱”拖低适应缩放；
    // 5 张及以下保持单列，避免多出整列反而拉宽 5+ 列图谱的横向跨度
    const MAX_ZONE_COL = 5;
    const splitZoneRank = (list, baseRank) => {
      if (list.length <= MAX_ZONE_COL) {
        list.forEach((n) => {
          n.rank = baseRank * 10;
        });
        return;
      }
      const roleOrder = { predecessor: 0, ally: 1, mediator: 2 };
      const sorted = list
        .map((n, idx) => ({ n, idx, prio: roleOrder[n.role] ?? 1 }))
        .sort((a, b) => a.prio - b.prio || a.idx - b.idx);
      const subCols = Math.ceil(sorted.length / MAX_ZONE_COL);
      const chunkSize = Math.ceil(sorted.length / subCols);
      sorted.forEach((entry, i) => {
        entry.n.rank = baseRank * 10 + Math.floor(i / chunkSize);
      });
    };

    const leftGroups = new Map();
    leftNodes.forEach((n) => {
      if (!leftGroups.has(n.rank)) leftGroups.set(n.rank, []);
      leftGroups.get(n.rank).push(n);
    });
    leftGroups.forEach((grp, r) => splitZoneRank(grp, r));

    authorNodes.forEach((n) => {
      n.rank = authorRank * 10;
    });
    splitZoneRank(targetNodes, targetBaseRank);

    // 压缩空列并按列分组
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

    // 按连接邻居做重心排序，减少交叉
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
        ranks.get(r).sort((a, b) => {
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
        ranks.get(r).sort((a, b) => {
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

  function renderLineageNarrowHtml(payload, nodes, rawEdges) {
    const idMap = new Map(nodes.map((n) => [n.id, n]));
    const groupOf = (role) => {
      if (role === 'predecessor' || role === 'mediator') return 'roots';
      if (role === 'ally') return 'ally';
      if (role === 'target') return 'target';
      return 'author';
    };
    const roleOrder = { author: 0, predecessor: 1, mediator: 2, ally: 3, target: 4 };
    const orderedNodes = nodes.slice().sort((a, b) => (roleOrder[a.role] ?? 9) - (roleOrder[b.role] ?? 9));

    const rootsCnt = orderedNodes.filter((n) => groupOf(n.role) === 'roots').length;
    const allyCnt = orderedNodes.filter((n) => groupOf(n.role) === 'ally').length;
    const targetCnt = orderedNodes.filter((n) => groupOf(n.role) === 'target').length;

    const edgesByNode = new Map(nodes.map((n) => [n.id, []]));
    (rawEdges || []).forEach((e) => {
      if (!e || !idMap.has(e.from) || !idMap.has(e.to)) return;
      const relKey = e.relation || e.relation_type || 'inherit';
      const style = LINEAGE_EDGE_STYLES[relKey] || LINEAGE_EDGE_STYLES.inherit;
      const fromLbl = (idMap.get(e.from) && idMap.get(e.from).label) || e.from;
      const toLbl = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      const lbl = e.label || style.label;
      if (edgesByNode.has(e.from)) {
        edgesByNode.get(e.from).push({ dir: '→', peer: toLbl, lbl, cls: style.cls });
      }
      if (edgesByNode.has(e.to) && e.from !== e.to) {
        edgesByNode.get(e.to).push({ dir: '←', peer: fromLbl, lbl, cls: style.cls });
      }
    });

    const filterBar =
      '<div class="lineage-filter-bar" data-lineage-bar>' +
      '<div class="lineage-filter-tabs" role="tablist" aria-label="谱系关系筛选">' +
      '<button type="button" class="lineage-tab active" data-lineage-filter="all">全部 (' + orderedNodes.length + ')</button>' +
      '<button type="button" class="lineage-tab" data-lineage-filter="roots"' + (rootsCnt ? '' : ' disabled') + '>渊源与改造 (' + rootsCnt + ')</button>' +
      '<button type="button" class="lineage-tab" data-lineage-filter="ally"' + (allyCnt ? '' : ' disabled') + '>同调佐证 (' + allyCnt + ')</button>' +
      '<button type="button" class="lineage-tab" data-lineage-filter="target"' + (targetCnt ? '' : ' disabled') + '>批驳靶子 (' + targetCnt + ')</button>' +
      '</div>' +
      '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览思想谱系图">全览图谱 ⤢</button>' +
      '</div>';

    const cards = orderedNodes
      .map((n) => {
        const grp = groupOf(n.role);
        const rels = edgesByNode.get(n.id) || [];
        const relHtml = rels.length
          ? '<div class="lineage-rel-chips">' +
            rels
              .slice(0, 4)
              .map(
                (r) =>
                  '<span class="lineage-rel-chip ' +
                  esc(r.cls) +
                  '">' +
                  esc(r.dir + ' ' + r.lbl + ' · ' + r.peer) +
                  '</span>'
              )
              .join('') +
            '</div>'
          : '';
        return (
          '<div class="lineage-card role-' +
          esc(n.role || 'mediator') +
          '" data-lineage-group="' +
          esc(grp) +
          '">' +
          '<div class="lineage-card-head">' +
          '<span class="lineage-role-badge">' +
          esc(ROLE_ZH[n.role] || n.role || '立场') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="lineage-card-title">' +
          esc(n.label) +
          '</div>' +
          (n.detail ? '<div class="lineage-card-detail">' + esc(n.detail) + '</div>' : '') +
          relHtml +
          '<div class="lineage-card-foot">' +
          renderAnchorPills(n.blocks, 3) +
          '</div></div>'
        );
      })
      .join('');

    return (
      '<div class="lineage-narrow-shell" data-qa-target="lineage-cards">' +
      filterBar +
      '<div class="lineage-card-list">' +
      cards +
      '<div class="lineage-filter-empty" hidden>当前分组暂无条目。</div>' +
      '</div></div>'
    );
  }

  function renderFlowNarrowHtml(payload, nodes, rawEdges) {
    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i, rank: 0, order: 0 })]));
    const mergedEdges = prepareDagEdges(rawEdges, idMap);
    assignDagRanks(nodes, idMap, mergedEdges);
    const ordered = Array.from(idMap.values()).sort((a, b) => a.rank - b.rank || a.order - b.order || a.idx - b.idx);
    const outEdges = new Map(ordered.map((n) => [n.id, []]));
    mergedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      const dst = idMap.get(e.to);
      if (outEdges.has(e.from) && dst) {
        outEdges.get(e.from).push({ label: e.label || '流转', target: dst.label || e.to, isNeg: e.isNeg });
      }
    });

    const header =
      '<div class="sop-narrow-head">' +
      '<span class="sop-narrow-meta">共 ' + ordered.length + ' 步可执行流程</span>' +
      '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览 SOP 流程图">全览图谱 ⤢</button>' +
      '</div>';

    const cards = ordered
      .map((n, idx) => {
        const stepNo = String(idx + 1).padStart(2, '0');
        const nexts = outEdges.get(n.id) || [];
        const edgeChips = nexts.length
          ? '<div class="sop-step-edges">' +
            nexts
              .slice(0, 3)
              .map(
                (nx) =>
                  '<span class="sop-edge-chip' +
                  (nx.isNeg ? ' is-neg' : '') +
                  '">→ ' +
                  esc(nx.label) +
                  ' · ' +
                  esc(nx.target) +
                  '</span>'
              )
              .join('') +
            '</div>'
          : '';
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        return (
          '<div class="sop-step-card role-' +
          esc(n.role || 'action') +
          '" data-jump="' +
          esc(firstBlk) +
          '">' +
          '<div class="sop-step-head">' +
          '<div class="sop-step-left">' +
          '<span class="sop-step-num">' +
          stepNo +
          '</span>' +
          '<span class="sop-role-badge">' +
          esc(roleLabel(n.role, 'flow_swimlane')) +
          '</span>' +
          '</div>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="sop-step-title">' +
          esc(n.label) +
          '</div>' +
          (n.detail ? '<div class="sop-step-detail">' + esc(n.detail) + '</div>' : '') +
          edgeChips +
          '<div class="sop-step-foot">' +
          renderAnchorPills(n.blocks, 3) +
          '</div></div>'
        );
      })
      .join('');

    return (
      '<div class="sop-narrow-shell" data-qa-target="flow-cards">' +
      header +
      '<div class="sop-step-list">' +
      cards +
      '</div></div>'
    );
  }

  function renderDecisionNarrowHtml(payload, nodes, rawEdges) {
    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i, rank: 0, order: 0 })]));
    const mergedEdges = prepareDagEdges(rawEdges, idMap);
    assignDagRanks(nodes, idMap, mergedEdges);
    const ordered = Array.from(idMap.values()).sort((a, b) => a.rank - b.rank || a.order - b.order || a.idx - b.idx);

    const groupOf = (role) => {
      if (role === 'strategy') return 'strategy';
      if (role === 'fallback') return 'fallback';
      return 'condition';
    };
    const condCnt = ordered.filter((n) => groupOf(n.role) === 'condition').length;
    const stratCnt = ordered.filter((n) => groupOf(n.role) === 'strategy').length;
    const fallCnt = ordered.filter((n) => groupOf(n.role) === 'fallback').length;

    const inEdges = new Map(ordered.map((n) => [n.id, []]));
    const outEdges = new Map(ordered.map((n) => [n.id, []]));
    mergedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      const src = idMap.get(e.from);
      const dst = idMap.get(e.to);
      if (!src || !dst) return;
      inEdges.get(e.to).push({ label: e.label || '条件', peer: src.label || e.from });
      outEdges.get(e.from).push({
        label: e.label || '分支',
        peer: dst.label || e.to,
        role: dst.role || 'strategy',
      });
    });

    const filterBar =
      '<div class="decision-filter-bar" data-decision-bar>' +
      '<div class="lineage-filter-tabs" role="tablist" aria-label="决策节点筛选">' +
      '<button type="button" class="lineage-tab active" data-decision-filter="all">全部 (' + ordered.length + ')</button>' +
      '<button type="button" class="lineage-tab" data-decision-filter="condition"' + (condCnt ? '' : ' disabled') + '>诊断与条件 (' + condCnt + ')</button>' +
      '<button type="button" class="lineage-tab" data-decision-filter="strategy"' + (stratCnt ? '' : ' disabled') + '>推荐动作 (' + stratCnt + ')</button>' +
      '<button type="button" class="lineage-tab" data-decision-filter="fallback"' + (fallCnt ? '' : ' disabled') + '>止损红线 (' + fallCnt + ')</button>' +
      '</div>' +
      '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览条件决策树">全览图谱 ⤢</button>' +
      '</div>';

    const cards = ordered
      .map((n) => {
        const grp = groupOf(n.role);
        const ins = inEdges.get(n.id) || [];
        const outs = outEdges.get(n.id) || [];
        const inHtml = ins.length
          ? '<div class="decision-cond-in">' +
            ins
              .slice(0, 2)
              .map((ic) => '<span class="decision-if-pill">如果「' + esc(ic.label) + '」← ' + esc(ic.peer) + '</span>')
              .join('') +
            '</div>'
          : '';
        const outHtml = outs.length
          ? '<div class="decision-branches">' +
            outs
              .slice(0, 4)
              .map(
                (oc) =>
                  '<span class="decision-branch-pill role-' +
                  esc(oc.role) +
                  '">若「' +
                  esc(oc.label) +
                  '」→ ' +
                  esc(oc.peer) +
                  '</span>'
              )
              .join('') +
            '</div>'
          : '';
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        return (
          '<div class="decision-card role-' +
          esc(n.role || 'condition') +
          '" data-decision-group="' +
          esc(grp) +
          '" data-jump="' +
          esc(firstBlk) +
          '">' +
          '<div class="lineage-card-head">' +
          '<span class="lineage-role-badge">' +
          esc(roleLabel(n.role, 'decision_tree')) +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          inHtml +
          '<div class="lineage-card-title">' +
          esc(n.label) +
          '</div>' +
          (n.detail ? '<div class="lineage-card-detail">' + esc(n.detail) + '</div>' : '') +
          outHtml +
          '<div class="sop-step-foot">' +
          renderAnchorPills(n.blocks, 3) +
          '</div></div>'
        );
      })
      .join('');

    return (
      '<div class="decision-narrow-shell" data-qa-target="decision-cards">' +
      filterBar +
      '<div class="lineage-card-list">' +
      cards +
      '<div class="lineage-filter-empty" hidden>当前分组暂无条目。</div>' +
      '</div></div>'
    );
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

  function renderScienceNarrowHtml(payload, nodes, rawEdges, ranks, sortedRanks, idMap) {
    const bp = payload.blueprint || 'derivation_dag';
    const isFlow = bp === 'system_flow';
    const rawData = (payload && payload.data) || {};
    const expRoles = new Set(['observation', 'hypothesis', 'experiment', 'finding']);
    const isExpParadigm =
      !isFlow &&
      (String(rawData.paradigm || '').toLowerCase() === 'experimental' || nodes.some((n) => expRoles.has(n.role)));
    const badgeTxt = isFlow ? '机制流转步骤卡' : isExpParadigm ? '实验推理 · 分层推导' : '公理演绎 · 分层推导';

    const outEdges = new Map(nodes.map((n) => [n.id, []]));
    (rawEdges || []).forEach((e) => {
      if (!e || !idMap.has(e.from) || !idMap.has(e.to)) return;
      const toLbl = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      outEdges.get(e.from).push({
        lbl: String(e.label || (isFlow ? '流转' : '推出')).trim(),
        toLbl,
      });
    });

    const layersHtml = sortedRanks
      .map((r, ri) => {
        const list = ranks.get(r) || [];
        const layerTitle = isFlow ? '环节 ' + (ri + 1) : '第 ' + (ri + 1) + ' 层';
        const cards = list
          .map((n) => {
            const outs = outEdges.get(n.id) || [];
            const firstBlk = (n.blocks && n.blocks[0]) || '';
            const relsHtml = outs.length
              ? '<div class="science-edge-chips">' +
                outs
                  .slice(0, 4)
                  .map(
                    (o) =>
                      '<span class="science-edge-chip">→ ' +
                      esc(o.lbl) +
                      ' · ' +
                      esc(o.toLbl) +
                      '</span>'
                  )
                  .join('') +
                '</div>'
              : '';
            return (
              '<div class="science-card role-' +
              esc(n.role || (isFlow ? 'process' : 'lemma')) +
              '" data-jump="' +
              esc(firstBlk) +
              '">' +
              '<div class="science-card-head">' +
              '<span class="science-role-badge">' +
              esc(roleLabel(n.role, bp)) +
              '</span>' +
              renderSrcTag(n.source) +
              '</div>' +
              '<div class="science-card-title">' +
              esc(n.label) +
              '</div>' +
              (n.detail ? '<div class="science-card-detail">' + esc(n.detail) + '</div>' : '') +
              relsHtml +
              '<div class="science-card-foot">' +
              renderAnchorPills(n.blocks, 3) +
              '</div></div>'
            );
          })
          .join('');
        return (
          '<div class="science-layer-group">' +
          '<div class="science-layer-head">' +
          esc(layerTitle) +
          '</div><div class="science-layer-cards">' +
          cards +
          '</div></div>'
        );
      })
      .join('');

    return (
      '<div class="science-narrow-shell" data-qa-target="' +
      (isFlow ? 'system-flow-cards' : 'derivation-cards') +
      '">' +
      '<div class="science-narrow-bar">' +
      '<span class="science-mode-pill">' +
      esc(badgeTxt) +
      '</span>' +
      '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览图谱">全览图谱 ⤢</button>' +
      '</div>' +
      '<div class="science-layer-list">' +
      layersHtml +
      '</div></div>'
    );
  }

  function renderLayeredDagHtml(payload, options) {
    const rawData = (payload && payload.data) || {};
    const isLineage = payload.blueprint === 'lineage_dag';
    const isFlow = payload.blueprint === 'flow_swimlane';
    const isDecision = payload.blueprint === 'decision_tree';
    const isPracticalDag = isFlow || isDecision;
    const isSysFlow = payload.blueprint === 'system_flow';
    const isScienceDag = payload.blueprint === 'derivation_dag' || isSysFlow;
    if ((isLineage || isFlow || isSysFlow) && String(rawData.mode || '').toLowerCase() === 'not_applicable') {
      const defaultReason = isFlow
        ? '本章属理念阐发或原则讨论，未包含可复用的先后执行流程。'
        : isSysFlow
          ? '本章属纯公理或概念推演，未涉及实体实验装置、物理过程或演化迭代机制。'
          : '本章主要展开内部推演或文学对话，未直接涉及外部思想家或学派谱系。';
      const reason = rawData.not_applicable_reason || rawData.reason || payload.summary || defaultReason;
      return (
        '<div class="lineage-na" data-qa-target="' +
        (isFlow ? 'flow-na' : isSysFlow ? 'system-flow-na' : 'lineage-na') +
        '">' +
        '<strong>' +
        (isFlow ? '本章未展开 SOP 流程图' : isSysFlow ? '本章无实体装置或演化机制流' : '本章未展开外部思想谱系图') +
        '</strong>' +
        '<p>' +
        esc(reason) +
        '</p></div>'
      );
    }

    const nodes = rawData.nodes || [];
    const rawEdges = rawData.edges || [];
    if (!nodes.length) return '<div class="wb-sub">暂无图节点数据</div>';

    const wideMode = Boolean(options && options.wide);
    const expandAll = Boolean(options && options.expandAll);
    const expandedIds = (options && options.expandedIds) || null;
    const expandedAnchorIds = (options && options.expandedAnchorIds) || null;
    if (payload.blueprint === 'causal_loop' && !wideMode) {
      return renderCausalLoopNarrowHtml(payload, nodes, rawEdges);
    }
    if (isLineage && !wideMode) {
      return renderLineageNarrowHtml(payload, nodes, rawEdges);
    }
    if (isFlow && !wideMode) {
      return renderFlowNarrowHtml(payload, nodes, rawEdges);
    }
    if (isDecision && !wideMode) {
      return renderDecisionNarrowHtml(payload, nodes, rawEdges);
    }
    const isHistoryDag = ['historical_causality', 'power_hierarchy'].includes(payload.blueprint);
    const maxAnchors =
      options && options.maxAnchors !== undefined
        ? options.maxAnchors
        : isHistoryDag
          ? !expandAll
            ? 4
            : 0
          : 3;
    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i, rank: 0, order: 0 })]));
    const mergedEdges = prepareDagEdges(rawEdges, idMap);
    const { ranks, sortedRanks } = isLineage
      ? assignLineageRanks(nodes, idMap, mergedEdges)
      : assignDagRanks(nodes, idMap, mergedEdges);

    if (isScienceDag && !wideMode) {
      return renderScienceNarrowHtml(payload, nodes, rawEdges, ranks, sortedRanks, idMap);
    }

    let maxInRank = 1;
    sortedRanks.forEach((r) => {
      maxInRank = Math.max(maxInRank, ranks.get(r).length);
    });

    const R = sortedRanks.length;
    const rankToIdx = new Map(sortedRanks.map((r, i) => [r, i]));
    const isArgument = payload.blueprint === 'argument_map';
    const isCausal = payload.blueprint === 'causal_loop';
    const wantCompactLR =
      wideMode &&
      ((isHistoryDag && (R === 5 || (R === 6 && maxInRank >= 3))) ||
        (isArgument && (R === 6 || R === 5 || (R === 4 && maxInRank <= 2))) ||
        (isScienceDag && R >= 2) ||
        isCausal ||
        ['flow_swimlane', 'decision_tree', 'system_flow'].includes(payload.blueprint) ||
        isLineage);
    const useBandedLR =
      wideMode &&
      (((isHistoryDag || isArgument || isCausal) && (R >= 7 || (R === 6 && (maxInRank <= 2 || isCausal)))) ||
        (isFlow && R >= 5) ||
        (isDecision && R >= 6) ||
        (isScienceDag && R >= 6));
    const wantLR = !useBandedLR && wantCompactLR;
    const splitIdx = useBandedLR ? Math.ceil(R / 2) : R;
    idMap.forEach((n) => {
      const ri = rankToIdx.get(n.rank) ?? 0;
      n.rankIdx = ri;
      n.band = useBandedLR && ri >= splitIdx ? 1 : 0;
    });

    const cardW = isLineage
      ? R >= 5
        ? 172
        : 186
      : isScienceDag
        ? useBandedLR
          ? Math.ceil(R / 2) >= 6
            ? 158
            : 170
          : R >= 5
            ? 168
            : 184
        : useBandedLR
          ? isPracticalDag
            ? R >= 9
              ? 164
              : 174
            : isHistoryDag
              ? 176
              : 182
          : wantLR
            ? isPracticalDag
              ? R >= 5
                ? 168
                : 182
              : isHistoryDag
                ? R >= 6
                  ? 162
                  : 174
                : R >= 6
                  ? 166
                  : 186
            : wideMode
              ? maxInRank >= 6
                ? 150
                : maxInRank >= 4
                  ? 172
                  : maxInRank >= 2
                    ? 194
                    : 236
              : maxInRank >= 4
                ? 142
                : maxInRank >= 2
                  ? 158
                  : 236;
    const sizes = measureNodeSizes(nodes, cardW, {
      expandAll,
      expandedIds,
      expandedAnchorIds,
      maxAnchors,
      isLineage,
      blueprint: payload.blueprint,
    });

    const colGap = isLineage
      ? R >= 5
        ? 88
        : 104
      : isScienceDag
        ? useBandedLR
          ? 82
          : R >= 5
            ? 84
            : 92
        : isPracticalDag && (useBandedLR || wantLR)
          ? 88
          : useBandedLR
            ? 56
            : wantLR
              ? isHistoryDag
                ? R >= 6
                  ? 46
                  : 54
                : R >= 6
                  ? 56
                  : 74
              : wideMode && maxInRank >= 6
                ? 18
                : 24;
    const rowGap = isLineage
      ? 22
      : isScienceDag
        ? maxInRank >= 4
          ? 18
          : 22
        : useBandedLR || (wideMode && wantLR)
          ? 24
          : wideMode
            ? 36
            : 56;
    const trackStep = isLineage ? 22 : wideMode && (!wantLR || wantCompactLR) ? 15 : wideMode ? 16 : 18;
    const corridorStep = isLineage ? 36 : wideMode ? 32 : 26;

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
      return n ? Math.max(base, 28 + n * trackStep) : base;
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
      const midTracksH = midCrossPlans.length
        ? Math.max(isPracticalDag ? 46 : 0, midCrossPlans.length * trackStep + (isPracticalDag ? 34 : 28))
        : 20;
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
      maxY = Math.max(maxY, p.h + p.y);
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
        [0.5, 0.34, 0.66, 0.22, 0.78].forEach((t) => {
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

      if (e.polarity === '+') {
        stroke = 'var(--pine)';
        marker = markerGreen;
        dash = 'none';
        labelKindCls = 'edge-pos';
      } else if (e.polarity === '-') {
        stroke = 'var(--cinnabar)';
        marker = markerRed;
        dash = '5 3';
        labelKindCls = 'edge-neg';
      } else if (isLineage && e.relation && LINEAGE_EDGE_STYLES[e.relation]) {
        const st = LINEAGE_EDGE_STYLES[e.relation];
        stroke = st.stroke;
        dash = st.dash;
        labelKindCls = st.cls;
        marker =
          st.marker === 'red'
            ? markerRed
            : st.marker === 'indigo'
              ? markerIndigo
              : st.marker === 'amber'
                ? markerAmber
                : markerGreen;
      } else if (e.relationType === 'hostile' || isNeg) {
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
        const lsz = measureLabelSize(e.delay ? lbl + ' 滞后' : lbl);
        const labelCandidates = buildLabelCandidates(pts);
        const offsets = [
          { dx: 0, dy: 0 },
          { dx: 0, dy: -13 },
          { dx: 0, dy: 13 },
          { dx: -18, dy: 0 },
          { dx: 18, dy: 0 },
          { dx: -26, dy: 0 },
          { dx: 26, dy: 0 },
          { dx: 0, dy: -24 },
          { dx: 0, dy: 24 },
          { dx: 0, dy: -36 },
          { dx: 0, dy: 36 },
          { dx: 0, dy: -46 },
          { dx: 0, dy: 46 },
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
              (e.delay ? ' <i class="dag-edge-delay">滞后</i>' : '') +
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
        const isExp = Boolean((expandAll || (expandedIds && expandedIds.has(n.id))) && n.detail);
        const cap = expandedAnchorIds && expandedAnchorIds.has(n.id) ? 0 : maxAnchors;
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
          esc(roleLabel(n.role, payload.blueprint || isLineage)) +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="dag-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.detail
            ? '<div class="dag-node-more" data-toggle-detail>' +
              (isExp ? '▴ 收起说明' : '▾ 查看展开') +
              '</div><div class="dag-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks, cap) +
          '</div></div>'
        );
      })
      .join('');

    const lineageLegendHtml = isLineage
      ? '<div class="lineage-legend">' +
        Object.values(LINEAGE_EDGE_STYLES)
          .map(
            (st) =>
              '<span class="lineage-leg-item"><i class="lineage-leg-line" style="border-top:2px ' +
              (st.dash === 'none' ? 'solid' : 'dashed') +
              ' ' +
              st.stroke +
              '"></i>' +
              esc(st.label) +
              '</span>'
          )
          .join('') +
        '</div>'
      : '';

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
      lineageLegendHtml +
      '<div class="dag-trace-tip" data-trace-tip>悬停节点或连线可高亮因果路径；点击节点跳转原文</div>' +
      legendHtml
    );
  }

  const QUAD_POS_META = {
    'q-top-right': { label: '高纵 · 高横', order: 0 },
    'q-top-left': { label: '高纵 · 低横', order: 1 },
    'q-bot-right': { label: '低纵 · 高横', order: 2 },
    'q-bot-left': { label: '低纵 · 低横', order: 3 },
  };

  function renderQuadrantHtml(payload, options) {
    const data = (payload && payload.data) || {};
    const qs = data.quadrants || [];
    const xAxis = data.xAxis || '横轴（低 → 高）';
    const yAxis = data.yAxis || '纵轴（低 → 高）';
    const wideMode = Boolean(options && options.wide);
    const byPos = new Map(qs.map((q) => [q.pos, q]));

    if (!wideMode) {
      const ordered = qs
        .slice()
        .sort((a, b) => ((QUAD_POS_META[a.pos] && QUAD_POS_META[a.pos].order) ?? 9) - ((QUAD_POS_META[b.pos] && QUAD_POS_META[b.pos].order) ?? 9));
      const coordHead =
        '<div class="quad-coord-head">' +
        '<div><span class="quad-coord-k">纵轴 ↑</span><b>' + esc(yAxis) + '</b></div>' +
        '<div><span class="quad-coord-k">横轴 →</span><b>' + esc(xAxis) + '</b></div>' +
        '</div>';
      const filterBar =
        '<div class="quad-filter-bar" data-quad-bar>' +
        '<div class="lineage-filter-tabs" role="tablist" aria-label="象限切换">' +
        '<button type="button" class="lineage-tab active" data-quad-filter="all">全部 (' + ordered.length + ')</button>' +
        ordered
          .map(
            (q) =>
              '<button type="button" class="lineage-tab" data-quad-filter="' +
              esc(q.pos) +
              '">' +
              esc(q.title) +
              '</button>'
          )
          .join('') +
        '</div>' +
        '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览四象限矩阵">全览图谱 ⤢</button>' +
        '</div>';
      const cards = ordered
        .map((q) => {
          const posMeta = QUAD_POS_META[q.pos] || { label: '' };
          return (
            '<div class="quad-narrow-card ' +
            esc(q.pos || '') +
            '" data-quad-pos="' +
            esc(q.pos || '') +
            '">' +
            '<div class="quad-narrow-head">' +
            '<b class="quad-cell-title">' +
            esc(q.title) +
            '</b>' +
            (posMeta.label ? '<span class="quad-pos-tag">' + esc(posMeta.label) + '</span>' : '') +
            '</div>' +
            (q.action_hint ? '<div class="quad-action-hint">口诀：' + esc(q.action_hint) + '</div>' : '') +
            '<div class="quad-narrow-items">' +
            (q.items || [])
              .map(
                (it) =>
                  '<div class="quad-item"><div>' +
                  renderSrcTag(it.source) +
                  ' ' +
                  esc(it.text) +
                  '</div><div class="quad-item-foot">' +
                  renderAnchorPills(it.blocks, 3) +
                  '</div></div>'
              )
              .join('') +
            '</div></div>'
          );
        })
        .join('');
      return (
        '<div class="quad-narrow-shell" data-qa-target="quadrant-narrow">' +
        coordHead +
        filterBar +
        '<div class="quad-narrow-list">' +
        cards +
        '</div></div>'
      );
    }

    const cell = (cls) => {
      const q = byPos.get(cls);
      if (!q) return '<div class="quad-cell ' + cls + '"></div>';
      return (
        '<div class="quad-cell ' +
        cls +
        '"><div class="quad-cell-head"><div class="quad-cell-title">' +
        esc(q.title) +
        '</div>' +
        (q.action_hint ? '<span class="quad-action-hint">' + esc(q.action_hint) + '</span>' : '') +
        '</div>' +
        (q.items || [])
          .map(
            (it) =>
              '<div class="quad-item"><div>' +
              renderSrcTag(it.source) +
              ' ' +
              esc(it.text) +
              '</div><div class="quad-item-foot">' +
              renderAnchorPills(it.blocks, 3) +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    };
    return (
      '<div class="quad-grid-shell" data-qa-target="quadrant">' +
      '<div class="quad-axis-y">' +
      '<span class="quad-axis-y-arrow" aria-hidden="true">↑</span>' +
      '<span class="quad-axis-y-label">' +
      esc(yAxis) +
      '</span>' +
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
      esc(xAxis) +
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

  function renderMatrixTableHtml(payload, options) {
    const cols = (payload.data && payload.data.columns) || [];
    const rows = (payload.data && payload.data.rows) || [];
    const wideMode = Boolean(options && options.wide);
    const isTermContrast = payload && payload.blueprint === 'term_contrast';

    if (isTermContrast && !wideMode) {
      return (
        '<div class="term-contrast-cards" data-qa-target="term-cards">' +
        rows
          .map(
            (r) =>
              '<div class="tc-card">' +
              '<div class="tc-card-head">' +
              '<div class="tc-term-wrap">' +
              '<strong class="tc-term">' +
              esc(r.key) +
              '</strong>' +
              (r.original_term ? '<span class="tc-orig">' + esc(r.original_term) + '</span>' : '') +
              '</div>' +
              renderSrcTag(r.source) +
              '</div>' +
              '<div class="tc-card-compare">' +
              '<div class="tc-col tc-everyday"><span class="tc-col-lbl">日常直觉</span><p>' +
              esc(r.everyday) +
              '</p></div>' +
              '<div class="tc-col tc-author"><span class="tc-col-lbl">作者专义</span><p>' +
              esc(r.author) +
              '</p></div>' +
              '</div>' +
              '<div class="tc-card-foot">' +
              renderAnchorPills(r.blocks, 3) +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    }

    return (
      '<div style="overflow-x:auto" data-qa-target="table"><table class="bp-table' +
      (isTermContrast ? ' term-contrast-table' : '') +
      (payload.blueprint === 'concept_matrix' ? ' concept-matrix-table' : '') +
      '"><thead><tr>' +
      cols.map((c) => '<th>' + esc(c) + '</th>').join('') +
      '</tr></thead><tbody>' +
      rows
        .map(
          (r) =>
            '<tr><td class="col-key">' +
            '<div>' +
            esc(r.key) +
            '</div>' +
            (r.original_term ? '<div class="tc-orig-table">' + esc(r.original_term) + '</div>' : '') +
            '<div style="margin-top:4px">' +
            renderSrcTag(r.source) +
            '</div></td><td>' +
            esc(r.everyday) +
            '</td><td>' +
            esc(r.author) +
            '</td><td>' +
            (r.extra ? esc(r.extra) + '</td><td>' : '') +
            renderAnchorPills(r.blocks, 3) +
            '</td></tr>'
        )
        .join('') +
      '</tbody></table></div>'
    );
  }

  function renderSplineChartHtml(payload, options) {
    const pts = (payload.data && payload.data.points) || [];
    if (!pts.length) return '';
    const wideMode = Boolean(options && options.wide);
    const W = wideMode ? 680 : 440;
    const H = wideMode ? 210 : 200;
    const padL = 40;
    const padR = 24;
    const padT = 18;
    const padB = 46;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const coords = pts.map((p, i) => ({
      beat: p.beat,
      beat_kind: p.beat_kind || '',
      tension: Number(p.tension || 0),
      emotion: p.emotion,
      note: p.note || '',
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
        const bk = BEAT_KIND_META[c.beat_kind];
        const stroke = bk ? bk.color : 'var(--cinnabar)';
        const isClimax = c.beat_kind === 'climax';
        const isForeshadow = c.beat_kind === 'foreshadow';
        const isRelief = c.beat_kind === 'relief';
        const r = isClimax ? 6 : isForeshadow ? 5.5 : 5;
        const fill = isClimax ? 'var(--cinnabar)' : isForeshadow ? 'var(--surface)' : 'var(--desk-card, var(--paper))';
        const dashAttr = isForeshadow ? ' stroke-dasharray="2.5 1.5"' : '';
        const halo = isClimax
          ? '<circle cx="' + c.x + '" cy="' + c.y + '" r="9.5" fill="none" stroke="var(--cinnabar)" stroke-width="1.3" opacity="0.55"/>'
          : isRelief
            ? '<circle cx="' + c.x + '" cy="' + c.y + '" r="8" fill="var(--pine-soft)" opacity="0.55"/>'
            : '';
        return (
          '<g class="chart-pt-grp" tabindex="0" data-pt-idx="' +
          c.idx +
          '" data-pt-beat="' +
          esc(c.beat) +
          '" data-pt-kind="' +
          esc(bk ? bk.label : '') +
          '" data-pt-val="' +
          Math.round(c.tension) +
          '" data-pt-emotion="' +
          esc(c.emotion || '') +
          '" data-pt-note="' +
          esc(c.note || '') +
          '" data-pt-x="' +
          c.x.toFixed(1) +
          '" data-pt-y="' +
          c.y.toFixed(1) +
          '" data-pt-w="' +
          W +
          '" data-jump="' +
          esc(bid) +
          '" style="cursor:pointer">' +
          halo +
          '<circle class="chart-pt" cx="' +
          c.x +
          '" cy="' +
          c.y +
          '" r="' +
          r +
          '" fill="' +
          fill +
          '" stroke="' +
          stroke +
          '" stroke-width="2.2"' +
          dashAttr +
          '/><text x="' +
          c.x +
          '" y="' +
          lblY +
          '" text-anchor="middle" font-size="9.5" fill="var(--ink-2)">' +
          esc(c.beat) +
          '</text></g>'
        );
      })
      .join('');
    const usedKinds = Array.from(new Set(coords.map((c) => c.beat_kind).filter((k) => BEAT_KIND_META[k])));
    const kindBar = usedKinds.length
      ? '<div class="chart-kind-bar">' +
        usedKinds
          .map((k) => {
            const m = BEAT_KIND_META[k];
            return '<span class="beat-kind-pill ' + m.cls + '">' + esc(m.label) + '</span>';
          })
          .join('') +
        '</div>'
      : '';
    return (
      '<div class="chart-stage-wrap">' +
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
      '</svg>' +
      '<div class="chart-tooltip" data-chart-tip hidden></div>' +
      '</div>' +
      kindBar +
      '<div class="chart-legend-list">' +
      coords
        .map((c) => {
          const bk = BEAT_KIND_META[c.beat_kind];
          return (
            '<div class="chart-legend-item" data-jump="' +
            esc((c.blocks && c.blocks[0]) || '') +
            '">' +
            '<div class="chart-legend-top">' +
            '<span class="chart-legend-head">' +
            (bk ? '<span class="beat-kind-pill ' + bk.cls + '">' + esc(bk.label) + '</span> ' : '') +
            '<b>' +
            esc(c.beat) +
            '</b> · <span class="chart-legend-emo">' +
            esc(c.emotion) +
            '</span></span>' +
            '<span class="chart-legend-refs">' +
            renderSrcTag(c.source) +
            ' ' +
            renderAnchorPills(c.blocks, wideMode ? 5 : 3) +
            '</span></div>' +
            (c.note ? '<div class="chart-beat-note">' + esc(c.note) + '</div>' : '') +
            '</div>'
          );
        })
        .join('') +
      '</div>'
    );
  }

  function fmtNumericTick(v) {
    if (!Number.isFinite(v)) return '0';
    if (v === 0) return '0';
    const abs = Math.abs(v);
    if (abs >= 1e5 || abs < 1e-3) return v.toExponential(1).replace(/\.0e/, 'e');
    return Number(v.toPrecision(4)).toString();
  }

  function renderSensitivityHtml(payload, options) {
    const rawData = (payload && payload.data) || {};
    const wideMode = Boolean(options && options.wide);
    const mode = String(rawData.mode || '').toLowerCase();

    if (mode === 'not_applicable') {
      const reason =
        rawData.not_applicable_reason ||
        rawData.reason ||
        payload.summary ||
        '本章集中展开公理推演或概念辨析，未出现定量参数响应或临界相变阈值。';
      const highlights = Array.isArray(rawData.highlights) ? rawData.highlights : [];
      const hlHtml = highlights.length
        ? '<div class="sensitivity-hl-list">' +
          highlights
            .map(
              (h) =>
                '<div class="sensitivity-hl-card">' +
                '<div class="sensitivity-hl-head"><b>' +
                esc(h.title || '定性制约') +
                '</b>' +
                renderSrcTag(h.source) +
                '</div>' +
                (h.detail ? '<div class="sensitivity-hl-detail">' + esc(h.detail) + '</div>' : '') +
                '<div class="science-card-foot">' +
                renderAnchorPills(h.blocks, 3) +
                '</div></div>'
            )
            .join('') +
          '</div>'
        : '';
      return (
        '<div class="sensitivity-na" data-qa-target="sensitivity-na">' +
        '<strong>本章未呈现定量曲线或临界阈值</strong>' +
        '<p>' +
        esc(reason) +
        '</p>' +
        hlHtml +
        '</div>'
      );
    }

    if (mode === 'regime_threshold' || Array.isArray(rawData.regimes)) {
      const regimes = Array.isArray(rawData.regimes) ? rawData.regimes : [];
      if (!regimes.length) return '<div class="wb-sub">暂无临界相变区间数据</div>';
      const param = rawData.parameter || '控制变量';
      const toneLabel = { stable: '稳态区间', critical: '临界过渡', breakdown: '相变/失效' };
      const toneCls = (t) => (t === 'breakdown' ? 'tone-breakdown' : t === 'critical' ? 'tone-critical' : 'tone-stable');

      if (wideMode) {
        const segCols = regimes.length;
        const axisSegs = regimes
          .map((rg, idx) => {
            const cls = toneCls(rg.tone);
            const thrPin =
              idx > 0 && rg.threshold
                ? '<div class="regime-threshold-pin"><span>' + esc(rg.threshold) + '</span></div>'
                : idx === 0 && rg.threshold
                  ? '<div class="regime-threshold-pin is-start"><span>' + esc(rg.threshold) + '</span></div>'
                  : '';
            return (
              '<div class="regime-axis-seg ' +
              cls +
              '">' +
              thrPin +
              '<div class="regime-seg-range">' +
              esc(rg.range || '') +
              '</div>' +
              '<div class="regime-seg-state">' +
              esc(rg.state || '') +
              '</div></div>'
            );
          })
          .join('');

        const cards = regimes
          .map((rg) => {
            const cls = toneCls(rg.tone);
            return (
              '<div class="regime-card ' +
              cls +
              '" data-jump="' +
              esc((rg.blocks && rg.blocks[0]) || '') +
              '">' +
              '<div class="regime-card-head">' +
              '<span class="regime-tone-pill">' +
              esc(toneLabel[rg.tone] || '相态区间') +
              '</span>' +
              renderSrcTag(rg.source) +
              '</div>' +
              '<div class="regime-card-range">' +
              esc(rg.range || '') +
              '</div>' +
              (rg.threshold ? '<div class="regime-card-thr">阈值：' + esc(rg.threshold) + '</div>' : '') +
              '<div class="regime-card-state">' +
              esc(rg.state || '') +
              '</div>' +
              (rg.mechanism ? '<div class="regime-card-mech">' + esc(rg.mechanism) + '</div>' : '') +
              '<div class="science-card-foot">' +
              renderAnchorPills(rg.blocks, 3) +
              '</div></div>'
            );
          })
          .join('');

        return (
          '<div class="sensitivity-regimes-wide" data-qa-target="sensitivity-regimes-wide">' +
          '<div class="sensitivity-param-bar"><span>控制参数</span><b>' +
          esc(param) +
          '</b></div>' +
          '<div class="regime-axis-bar" style="grid-template-columns:repeat(' +
          segCols +
          ',minmax(0,1fr))">' +
          axisSegs +
          '</div>' +
          '<div class="regime-cards-grid" style="grid-template-columns:repeat(' +
          segCols +
          ',minmax(0,1fr))">' +
          cards +
          '</div></div>'
        );
      }

      const stepsHtml = regimes
        .map((rg, idx) => {
          const cls = toneCls(rg.tone);
          const divider = rg.threshold
            ? '<div class="regime-threshold-divider"><span>⚡ 临界阈值 · ' + esc(rg.threshold) + '</span></div>'
            : idx > 0
              ? '<div class="regime-step-arrow">↓ 参数跃迁</div>'
              : '';
          return (
            divider +
            '<div class="regime-step-card ' +
            cls +
            '" data-jump="' +
            esc((rg.blocks && rg.blocks[0]) || '') +
            '">' +
            '<div class="regime-card-head">' +
            '<span class="regime-range-pill">' +
            esc(rg.range || '') +
            '</span>' +
            '<span class="regime-tone-pill">' +
            esc(toneLabel[rg.tone] || '相态') +
            '</span>' +
            renderSrcTag(rg.source) +
            '</div>' +
            '<div class="regime-card-state">' +
            esc(rg.state || '') +
            '</div>' +
            (rg.mechanism ? '<div class="regime-card-mech">' + esc(rg.mechanism) + '</div>' : '') +
            '<div class="science-card-foot">' +
            renderAnchorPills(rg.blocks, 3) +
            '</div></div>'
          );
        })
        .join('');

      return (
        '<div class="sensitivity-regimes-narrow" data-qa-target="sensitivity-regimes">' +
        '<div class="sensitivity-param-bar">' +
        '<div><span>控制参数</span><b>' +
        esc(param) +
        '</b></div>' +
        '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览相变轴">全览相变轴 ⤢</button>' +
        '</div>' +
        '<div class="regime-step-list">' +
        stepsHtml +
        '</div></div>'
      );
    }

    // Real quantitative numeric_curve (no 0-100 clamping!)
    const pts = Array.isArray(rawData.points) ? rawData.points : [];
    if (!pts.length) return '<div class="wb-sub">暂无参数响应数据</div>';
    const xLabel = rawData.xLabel || '自变量';
    const yLabel = rawData.yLabel || '响应量';
    const unit = rawData.unit || '';
    const yTitle = unit ? yLabel + ' (' + unit + ')' : yLabel;
    const ys = pts.map((p) => Number(p.y));
    let minY = Math.min(...ys);
    let maxY = Math.max(...ys);
    if (minY === maxY) {
      const pad = Math.abs(minY) > 0 ? Math.abs(minY) * 0.2 : 1;
      minY -= pad;
      maxY += pad;
    } else {
      const span = maxY - minY;
      minY -= span * 0.08;
      maxY += span * 0.08;
    }
    const spanY = maxY - minY || 1;
    const W = wideMode ? 680 : 440;
    const H = wideMode ? 230 : 210;
    const padL = 54;
    const padR = 24;
    const padT = 24;
    const padB = 48;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const yOf = (val) => padT + (1 - (val - minY) / spanY) * plotH;
    const coords = pts.map((p, i) => ({
      xLbl: String(p.x != null ? p.x : p.beat || '点' + (i + 1)),
      yVal: Number(p.y),
      note: p.note || '',
      blocks: p.blocks,
      source: p.source,
      idx: i,
      cx: padL + (pts.length === 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW),
      cy: yOf(Number(p.y)),
    }));
    let pathD = 'M ' + coords[0].cx + ',' + coords[0].cy;
    for (let i = 0; i < coords.length - 1; i++) {
      const p0 = coords[i];
      const p1 = coords[i + 1];
      const mx = (p0.cx + p1.cx) / 2;
      pathD += ' C ' + mx + ',' + p0.cy + ' ' + mx + ',' + p1.cy + ' ' + p1.cx + ',' + p1.cy;
    }
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => minY + t * spanY);
    const gridY = ticks
      .map((v) => {
        const y = yOf(v);
        return (
          '<line x1="' +
          padL +
          '" y1="' +
          y.toFixed(1) +
          '" x2="' +
          (W - padR) +
          '" y2="' +
          y.toFixed(1) +
          '" stroke="var(--rule)" stroke-dasharray="3 3"/>' +
          '<text x="' +
          (padL - 6) +
          '" y="' +
          (y + 3).toFixed(1) +
          '" text-anchor="end" font-size="9.5" fill="var(--ink-3)">' +
          esc(fmtNumericTick(v)) +
          '</text>'
        );
      })
      .join('');
    const zeroLine =
      minY < 0 && maxY > 0
        ? '<line x1="' +
          padL +
          '" y1="' +
          yOf(0).toFixed(1) +
          '" x2="' +
          (W - padR) +
          '" y2="' +
          yOf(0).toFixed(1) +
          '" stroke="var(--ink-3)" stroke-width="1"/>'
        : '';
    const circles = coords
      .map((c) => {
        const bid = (c.blocks && c.blocks[0]) || '';
        const lblY = c.idx % 2 === 0 ? H - 24 : H - 9;
        const valText = fmtNumericTick(c.yVal) + (unit ? ' ' + unit : '');
        return (
          '<g class="chart-pt-grp" tabindex="0" data-pt-idx="' +
          c.idx +
          '" data-pt-beat="' +
          esc(c.xLbl) +
          '" data-pt-val="' +
          esc(valText) +
          '" data-pt-note="' +
          esc(c.note || '') +
          '" data-pt-x="' +
          c.cx.toFixed(1) +
          '" data-pt-y="' +
          c.cy.toFixed(1) +
          '" data-pt-w="' +
          W +
          '" data-jump="' +
          esc(bid) +
          '" style="cursor:pointer">' +
          '<circle class="chart-pt" cx="' +
          c.cx.toFixed(1) +
          '" cy="' +
          c.cy.toFixed(1) +
          '" r="5" fill="var(--surface)" stroke="var(--pine)" stroke-width="2.2"/>' +
          '<text x="' +
          c.cx.toFixed(1) +
          '" y="' +
          (c.cy - 9).toFixed(1) +
          '" text-anchor="middle" font-size="9" font-weight="600" fill="var(--pine)">' +
          esc(fmtNumericTick(c.yVal)) +
          '</text>' +
          '<text x="' +
          c.cx.toFixed(1) +
          '" y="' +
          lblY +
          '" text-anchor="middle" font-size="9.5" fill="var(--ink-2)">' +
          esc(c.xLbl) +
          '</text></g>'
        );
      })
      .join('');

    return (
      '<div class="chart-axis-title"><span>纵轴：' +
      esc(yTitle) +
      '</span><span>横轴：' +
      esc(xLabel) +
      '</span></div>' +
      '<div class="chart-stage-wrap">' +
      '<svg class="chart-svg" viewBox="0 0 ' +
      W +
      ' ' +
      H +
      '" data-qa-target="sensitivity-numeric">' +
      gridY +
      zeroLine +
      '<path d="' +
      pathD +
      '" fill="none" stroke="var(--pine)" stroke-width="2.4" stroke-linecap="round"/>' +
      circles +
      '</svg>' +
      '<div class="chart-tooltip" data-chart-tip hidden></div>' +
      '</div>' +
      '<div class="chart-legend-list">' +
      coords
        .map(
          (c) =>
            '<div class="chart-legend-item" data-jump="' +
            esc((c.blocks && c.blocks[0]) || '') +
            '">' +
            '<div class="chart-legend-top">' +
            '<span class="chart-legend-head"><b>' +
            esc(c.xLbl) +
            '</b> · <span class="chart-legend-emo">' +
            esc(fmtNumericTick(c.yVal) + (unit ? ' ' + unit : '')) +
            '</span></span>' +
            '<span class="chart-legend-refs">' +
            renderSrcTag(c.source) +
            ' ' +
            renderAnchorPills(c.blocks, 3) +
            '</span></div>' +
            (c.note ? '<div class="chart-beat-note">' + esc(c.note) + '</div>' : '') +
            '</div>'
        )
        .join('') +
      '</div>'
    );
  }

  function renderConceptGlossaryHtml(payload, options) {
    const items = Array.isArray(payload.items) && payload.items.length
      ? payload.items
      : (payload.sections || []).reduce((acc, s) => acc.concat((s && s.items) || []), []);
    if (!items.length) return '<div class="wb-sub">暂无科学概念词条</div>';
    const wideMode = Boolean(options && options.wide);

    if (wideMode) {
      return (
        '<div style="overflow-x:auto" data-qa-target="glossary-table">' +
        '<table class="bp-table glossary-wide-table">' +
        '<thead><tr><th>核心概念与符号</th><th>一句话直觉 / 类比</th><th>精确科学定义与本章作用</th><th>出处与锚点</th></tr></thead>' +
        '<tbody>' +
        items
          .map((it) => {
            const term = it.term || it.title || it.name || '';
            const sym = it.symbol || '';
            const intuition = it.intuition || '';
            const def = it.definition || it.body || it.detail || '';
            return (
              '<tr><td class="col-key">' +
              '<div class="glossary-term-cell">' +
              esc(term) +
              '</div>' +
              (sym ? '<code class="glossary-sym">' + esc(sym) + '</code>' : '') +
              '<div style="margin-top:4px">' +
              renderSrcTag(it.source) +
              '</div></td><td>' +
              esc(intuition) +
              '</td><td>' +
              esc(def) +
              '</td><td>' +
              renderAnchorPills(it.blocks, 3) +
              '</td></tr>'
            );
          })
          .join('') +
        '</tbody></table></div>'
      );
    }

    return (
      '<div class="glossary-cards" data-qa-target="glossary-cards">' +
      items
        .map((it) => {
          const term = it.term || it.title || it.name || '';
          const sym = it.symbol || '';
          const intuition = it.intuition || '';
          const def = it.definition || it.body || it.detail || '';
          return (
            '<div class="glossary-card">' +
            '<div class="glossary-card-head">' +
            '<div class="glossary-term-wrap">' +
            '<strong class="glossary-term">' +
            esc(term) +
            '</strong>' +
            (sym ? '<code class="glossary-sym">' + esc(sym) + '</code>' : '') +
            '</div>' +
            renderSrcTag(it.source) +
            '</div>' +
            (intuition
              ? '<div class="glossary-row glossary-intuition"><span>直觉类比</span><p>' + esc(intuition) + '</p></div>'
              : '') +
            (def
              ? '<div class="glossary-row glossary-def"><span>科学定义</span><p>' + esc(def) + '</p></div>'
              : '') +
            '<div class="glossary-card-foot">' +
            renderAnchorPills(it.blocks, 3) +
            '</div></div>'
          );
        })
        .join('') +
      '</div>'
    );
  }

  function resolveEdgeVisual(e) {
    const rk = e.rel_kind && REL_KIND_META[e.rel_kind];
    if (rk) return rk;
    if (isNegativeEdgeLabel(e.label || '')) {
      return { stroke: 'var(--cinnabar)', dash: 'none', cls: 'edge-neg', marker: 'red' };
    }
    return { stroke: 'var(--pine)', dash: 'none', cls: 'edge-pos', marker: 'green' };
  }

  function renderRelationGraphHtml(payload, options) {
    const nodes = (payload.data && payload.data.nodes) || [];
    const rawEdges = (payload.data && payload.data.edges) || [];
    if (!nodes.length) return '<div class="wb-sub">暂无人物关系数据</div>';
    const wideMode = Boolean(options && options.wide);
    const hasCamps = nodes.some((n) => String(n.camp || '').trim());

    if (!wideMode) {
      const groups = new Map();
      nodes.forEach((n) => {
        const camp = String(n.camp || '').trim() || '核心人物';
        if (!groups.has(camp)) groups.set(camp, []);
        groups.get(camp).push(n);
      });
      const nodeLabelMap = new Map(nodes.map((n) => [n.id, n.label || n.id]));
      const cardsHtml = Array.from(groups.entries())
        .map(
          ([camp, list]) =>
            '<div class="camp-group-box">' +
            '<div class="camp-group-title">' +
            esc(camp) +
            '<span>' +
            list.length +
            ' 位</span></div>' +
            '<div class="camp-char-list">' +
            list
              .map((n) => {
                const rels = rawEdges
                  .filter((e) => e && (e.from === n.id || e.to === n.id))
                  .slice(0, 4)
                  .map((e) => {
                    const peerId = e.from === n.id ? e.to : e.from;
                    const arrow = e.from === n.id ? '→' : '←';
                    const vis = resolveEdgeVisual(e);
                    const peerName = nodeLabelMap.get(peerId) || peerId;
                    return (
                      '<span class="camp-rel-chip ' +
                      vis.cls +
                      '">' +
                      arrow +
                      ' ' +
                      esc(peerName) +
                      (e.label ? '：' + esc(e.label) : '') +
                      '</span>'
                    );
                  })
                  .join('');
                const firstBlk = (n.blocks && n.blocks[0]) || '';
                return (
                  '<div class="camp-char-card role-' +
                  esc(n.role || 'observer') +
                  '" data-jump="' +
                  esc(firstBlk) +
                  '">' +
                  '<div class="dag-node-role"><span>' +
                  esc(ROLE_ZH[n.role] || n.role || '人物') +
                  '</span>' +
                  renderSrcTag(n.source) +
                  '</div>' +
                  '<div class="dag-node-label">' +
                  esc(n.label) +
                  '</div>' +
                  (n.arc ? '<div class="camp-char-arc">' + esc(n.arc) + '</div>' : '') +
                  (n.detail ? '<div class="camp-char-detail">' + esc(n.detail) + '</div>' : '') +
                  (rels ? '<div class="camp-rel-row">' + rels + '</div>' : '') +
                  '<div class="dag-node-foot">' +
                  renderAnchorPills(n.blocks, 3) +
                  '</div></div>'
                );
              })
              .join('') +
            '</div></div>'
        )
        .join('');
      return (
        '<div class="camp-groups-narrow" data-qa-target="relation-narrow">' +
        cardsHtml +
        '</div>' +
        '<div class="dag-trace-tip"><button type="button" class="inline-zoom-link" data-bp-zoom-inline>点击打开宽屏阵营连线全览 ⤢</button></div>'
      );
    }

    if (!hasCamps) {
      return renderLayeredDagHtml(payload, options);
    }

    const camps = [];
    const campMap = new Map();
    nodes.forEach((n, idx) => {
      const cName = String(n.camp || '').trim() || '其他人物';
      if (!campMap.has(cName)) {
        const obj = { name: cName, nodes: [] };
        campMap.set(cName, obj);
        camps.push(obj);
      }
      campMap.get(cName).nodes.push(Object.assign({}, n, { idx }));
    });

    const expandAll = Boolean(options && options.expandAll);
    const maxAnchors = !expandAll ? 3 : 0;
    const cardW = 208;
    const sizes = measureNodeSizes(nodes, cardW, { showArc: true, expandAll, maxAnchors });
    const innerCols = (cnt) => (cnt >= 4 ? 2 : 1);
    const innerGapX = 18;
    const innerGapY = 18;
    const campPadX = 16;
    const campHeadH = 34;
    const campPadBot = 16;

    const campBoxes = camps.map((c) => {
      const cols = innerCols(c.nodes.length);
      const rows = Math.ceil(c.nodes.length / cols);
      let maxHByRow = new Array(rows).fill(0);
      c.nodes.forEach((n, i) => {
        const r = Math.floor(i / cols);
        maxHByRow[r] = Math.max(maxHByRow[r], sizes.get(n.id).h);
      });
      const boxW = campPadX * 2 + cols * cardW + Math.max(0, cols - 1) * innerGapX;
      const boxH = campHeadH + campPadBot + maxHByRow.reduce((a, b) => a + b, 0) + Math.max(0, rows - 1) * innerGapY;
      return { name: c.name, nodes: c.nodes, cols, rows, maxHByRow, w: boxW, h: boxH, x: 0, y: 0 };
    });

    const gridCols = campBoxes.length <= 3 ? campBoxes.length : campBoxes.length <= 6 && campBoxes.every((c) => c.cols === 1) ? 3 : 2;
    const gridRows = Math.ceil(campBoxes.length / gridCols);
    const colWidths = new Array(gridCols).fill(0);
    const rowHeights = new Array(gridRows).fill(0);
    campBoxes.forEach((cb, i) => {
      const r = Math.floor(i / gridCols);
      const col = i % gridCols;
      colWidths[col] = Math.max(colWidths[col], cb.w);
      rowHeights[r] = Math.max(rowHeights[r], cb.h);
    });

    const campGapX = 104;
    const campGapY = 78;
    const outerPadX = 48;
    const outerPadY = 36;
    const pos = new Map();
    const campHeaders = [];

    let curY = outerPadY;
    for (let r = 0; r < gridRows; r++) {
      let curX = outerPadX;
      for (let col = 0; col < gridCols; col++) {
        const idx = r * gridCols + col;
        if (idx < campBoxes.length) {
          const cb = campBoxes[idx];
          cb.x = curX;
          cb.y = curY;
          campHeaders.push({ x: cb.x, y: cb.y, w: cb.w, h: campHeadH });
          let ny = cb.y + campHeadH;
          for (let nr = 0; nr < cb.rows; nr++) {
            for (let nc = 0; nc < cb.cols; nc++) {
              const ni = nr * cb.cols + nc;
              if (ni < cb.nodes.length) {
                const nd = cb.nodes[ni];
                const sz = sizes.get(nd.id);
                const nx = cb.x + campPadX + nc * (cardW + innerGapX);
                pos.set(nd.id, { x: nx, y: ny, w: sz.w, h: sz.h });
              }
            }
            ny += cb.maxHByRow[nr] + innerGapY;
          }
        }
        curX += colWidths[col] + campGapX;
      }
      curY += rowHeights[r] + campGapY;
    }

    const maxW = Math.max(320, outerPadX * 2 + colWidths.reduce((a, b) => a + b, 0) + Math.max(0, gridCols - 1) * campGapX);
    const maxH = Math.max(220, curY - campGapY + outerPadY);

    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i })]));
    const mergedEdges = prepareDagEdges(rawEdges, idMap);
    const uidSuffix = 'rg-' + ++markerSeq;
    const markers = {
      green: 'rg-g-' + uidSuffix,
      red: 'rg-r-' + uidSuffix,
      indigo: 'rg-i-' + uidSuffix,
      amber: 'rg-a-' + uidSuffix,
    };

    const nodeRects = Array.from(pos.values());
    const blockerRects = nodeRects.concat(campHeaders);
    const placedLabels = [];
    const fallbackLegend = [];
    const svgEdgePaths = [];
    const htmlEdgeLabels = [];

    const pairCounts = new Map();
    mergedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) return;
      const pk = [e.from, e.to].sort().join('\0');
      const seq = pairCounts.get(pk) || 0;
      pairCounts.set(pk, seq + 1);
      const curveOffset = seq === 0 ? 18 : (seq % 2 === 1 ? 38 : -38);
      const acx = a.x + a.w / 2;
      const acy = a.y + a.h / 2;
      const bcx = b.x + b.w / 2;
      const bcy = b.y + b.h / 2;
      const dx = bcx - acx;
      const dy = bcy - acy;
      let p0, p1;
      if (Math.abs(dx) >= Math.abs(dy)) {
        p0 = { x: dx >= 0 ? a.x + a.w : a.x, y: acy };
        p1 = { x: dx >= 0 ? b.x : b.x + b.w, y: bcy };
      } else {
        p0 = { x: acx, y: dy >= 0 ? a.y + a.h : a.y };
        p1 = { x: bcx, y: dy >= 0 ? b.y : b.y + b.h };
      }
      const len = Math.max(1, Math.hypot(p1.x - p0.x, p1.y - p0.y));
      const nx = -(p1.y - p0.y) / len;
      const ny = (p1.x - p0.x) / len;
      const cx = Math.round((p0.x + p1.x) / 2 + nx * curveOffset);
      const cy = Math.round((p0.y + p1.y) / 2 + ny * curveOffset);
      const d = 'M ' + p0.x + ',' + p0.y + ' Q ' + cx + ',' + cy + ' ' + p1.x + ',' + p1.y;
      const vis = resolveEdgeVisual(rawEdges.find((re) => re.from === e.from && re.to === e.to) || e);
      const markerId = markers[vis.marker] || markers.green;
      const fromLabel = (idMap.get(e.from) && idMap.get(e.from).label) || e.from;
      const toLabel = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      const arrowSym = e.bidirectional ? ' ↔ ' : ' → ';
      const edgeDesc = fromLabel + arrowSym + toLabel + (e.label ? '：' + e.label : '') + (e.note ? '（' + e.note + '）' : '');

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
          vis.stroke +
          '" stroke-dasharray="' +
          vis.dash +
          '"' +
          (e.bidirectional ? ' marker-start="url(#' + markerId + ')"' : '') +
          ' marker-end="url(#' +
          markerId +
          ')"/>' +
          '<path class="dag-edge-hit" d="' +
          d +
          '"/>' +
          '</g>'
      );

      if (e.label) {
        const lsz = measureLabelSize(e.label);
        const mx = Math.round(0.25 * p0.x + 0.5 * cx + 0.25 * p1.x);
        const my = Math.round(0.25 * p0.y + 0.5 * cy + 0.25 * p1.y);
        const cands = [
          { x: mx, y: my },
          { x: mx, y: my - 16 },
          { x: mx, y: my + 16 },
          { x: Math.round(0.5625 * p0.x + 0.375 * cx + 0.0625 * p1.x), y: Math.round(0.5625 * p0.y + 0.375 * cy + 0.0625 * p1.y) },
          { x: Math.round(0.0625 * p0.x + 0.375 * cx + 0.5625 * p1.x), y: Math.round(0.0625 * p0.y + 0.375 * cy + 0.5625 * p1.y) },
        ];
        let chosenRect = null;
        for (const c of cands) {
          const box = { x: Math.round(c.x - lsz.w / 2), y: Math.round(c.y - lsz.h / 2), w: lsz.w, h: lsz.h };
          if (box.x < 4 || box.x + box.w > maxW - 4 || box.y < 4 || box.y + box.h > maxH - 4) continue;
          if (!blockerRects.some((nr) => rectsOverlap(box, nr, 4)) && !placedLabels.some((lr) => rectsOverlap(box, lr, 4))) {
            chosenRect = box;
            break;
          }
        }
        if (chosenRect) {
          placedLabels.push(chosenRect);
          htmlEdgeLabels.push(
            '<span class="dag-edge-label ' +
              vis.cls +
              '" data-edge-idx="' +
              e.idx +
              '" style="left:' +
              chosenRect.x +
              'px;top:' +
              chosenRect.y +
              'px" title="' +
              esc(edgeDesc) +
              '">' +
              esc(e.label) +
              '</span>'
          );
        } else {
          const badgeNum = fallbackLegend.length + 1;
          const circ = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'[badgeNum - 1] || String(badgeNum);
          fallbackLegend.push({ idx: e.idx, text: circ + ' ' + fromLabel + arrowSym + toLabel + '：' + (e.note || e.label) });
        }
      }
    });

    const campZonesHtml = campBoxes
      .map(
        (cb) =>
          '<div class="camp-zone-box" style="left:' +
          cb.x +
          'px;top:' +
          cb.y +
          'px;width:' +
          cb.w +
          'px;height:' +
          cb.h +
          'px"><div class="camp-zone-head">' +
          esc(cb.name) +
          '</div></div>'
      )
      .join('');

    const cardsHtml = nodes
      .map((n) => {
        const p = pos.get(n.id);
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        const isExp = expandAll && Boolean(n.detail);
        return (
          '<div class="dag-node role-' +
          esc(n.role || 'observer') +
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
          '" title="悬停高亮关系 · 点击跳转原文 ' +
          esc(firstBlk) +
          '">' +
          '<div class="dag-node-role"><span>' +
          esc(ROLE_ZH[n.role] || n.role || '人物') +
          '</span>' +
          renderSrcTag(n.source) +
          '</div>' +
          '<div class="dag-node-label">' +
          esc(n.label) +
          '</div>' +
          (n.arc ? '<div class="dag-node-arc">' + esc(n.arc) + '</div>' : '') +
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

    const markerDef = (id, fill) =>
      '<marker id="' +
      id +
      '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="' +
      fill +
      '"/></marker>';

    const legendHtml = fallbackLegend.length
      ? '<div class="edge-legend">' +
        fallbackLegend
          .map((item) => '<span class="edge-legend-pill" data-edge-idx="' + item.idx + '">' + esc(item.text) + '</span>')
          .join('') +
        '</div>'
      : '';

    const relLegend =
      '<div class="rel-kind-legend">' +
      Object.values(REL_KIND_META)
        .map(
          (m) =>
            '<span class="rel-legend-chip"><i style="background:' +
            m.stroke +
            ';border-style:' +
            (m.dash === 'none' ? 'solid' : 'dashed') +
            '"></i>' +
            esc(m.label) +
            '</span>'
        )
        .join('') +
      '</div>';

    return (
      '<div class="dag-viewport" data-qa-target="dag"><div class="dag-canvas camp-zoned" style="width:' +
      maxW +
      'px;height:' +
      maxH +
      'px">' +
      campZonesHtml +
      '<svg class="dag-edges" width="' +
      maxW +
      '" height="' +
      maxH +
      '" viewBox="0 0 ' +
      maxW +
      ' ' +
      maxH +
      '"><defs>' +
      markerDef(markers.green, 'var(--pine)') +
      markerDef(markers.red, 'var(--cinnabar)') +
      markerDef(markers.indigo, 'var(--indigo)') +
      markerDef(markers.amber, 'var(--amber)') +
      '</defs>' +
      svgEdgePaths.join('') +
      '</svg>' +
      cardsHtml +
      htmlEdgeLabels.join('') +
      '</div></div>' +
      relLegend +
      '<div class="dag-trace-tip" data-trace-tip>悬停人物或连线可高亮关系；点击节点跳转原文</div>' +
      legendHtml
    );
  }

  function resolve2DOverlaps(rects, bounds, pad) {
    for (let iter = 0; iter < 64; iter++) {
      let moved = false;
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i];
          const b = rects[j];
          if (!rectsOverlap(a, b, pad)) continue;
          moved = true;
          const acx = a.x + a.w / 2;
          const acy = a.y + a.h / 2;
          const bcx = b.x + b.w / 2;
          const bcy = b.y + b.h / 2;
          const ox = a.w / 2 + b.w / 2 + pad - Math.abs(acx - bcx);
          const oy = a.h / 2 + b.h / 2 + pad - Math.abs(acy - bcy);
          if (ox <= oy) {
            const dir = acx < bcx || (acx === bcx && i < j) ? -1 : 1;
            const shift = Math.ceil(ox / 2) + 1;
            a.x += dir * shift;
            b.x -= dir * shift;
          } else {
            const dir = acy < bcy || (acy === bcy && i < j) ? -1 : 1;
            const shift = Math.ceil(oy / 2) + 1;
            a.y += dir * shift;
            b.y -= dir * shift;
          }
          a.x = Math.max(bounds.left, Math.min(bounds.right - a.w, a.x));
          a.y = Math.max(bounds.top, Math.min(bounds.bottom - a.h, a.y));
          b.x = Math.max(bounds.left, Math.min(bounds.right - b.w, b.x));
          b.y = Math.max(bounds.top, Math.min(bounds.bottom - b.h, b.y));
        }
      }
      if (!moved) break;
    }
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        if (rectsOverlap(rects[i], rects[j], 2)) return false;
      }
    }
    return true;
  }

  function renderSpatialRouteHtml(payload, options) {
    const data = (payload && payload.data) || {};
    const mode = String(data.mode || '').toLowerCase();
    if (mode !== 'route_map' && mode !== 'stage_blocking') {
      return renderSpatialMapHtml(payload, options);
    }

    const nodes = data.nodes || [];
    const rawEdges = data.edges || [];
    if (!nodes.length) return '<div class="wb-sub">暂无空间节点数据</div>';

    const wideMode = Boolean(options && options.wide);
    const isStage = mode === 'stage_blocking';
    const actors = [];
    const actorColors = new Map();
    rawEdges.forEach((e) => {
      const a = String((e && e.actor) || '').trim();
      if (a && !actorColors.has(a)) {
        actorColors.set(a, ACTOR_PALETTE[actors.length % ACTOR_PALETTE.length]);
        actors.push(a);
      }
    });
    const edgeColor = (e) => {
      if (e && e.kind === 'barrier') return 'var(--cinnabar)';
      if (e && e.actor && actorColors.has(e.actor)) return actorColors.get(e.actor);
      if (e && e.kind === 'parallel') return 'var(--indigo)';
      if (e && e.kind === 'gaze') return 'var(--amber)';
      return 'var(--pine)';
    };

    if (!wideMode) {
      const MW = 640;
      const MH = 400;
      const padX = 56;
      const padT = isStage ? 52 : 44;
      const padB = isStage ? 46 : 36;
      const miniRects = nodes.map((n, idx) => {
        const nx = Number.isFinite(Number(n.x)) ? Number(n.x) : 18 + (idx % 4) * 20;
        const ny = Number.isFinite(Number(n.y)) ? Number(n.y) : 20 + Math.floor(idx / 4) * 25;
        const bw = 96;
        const bh = 32;
        const cx = padX + ((Math.max(4, Math.min(96, nx)) - 4) / 92) * (MW - padX * 2 - bw);
        const cy = padT + ((Math.max(4, Math.min(96, ny)) - 4) / 92) * (MH - padT - padB - bh);
        return { id: n.id, idx, x: Math.round(cx), y: Math.round(cy), w: bw, h: bh, node: n };
      });
      resolve2DOverlaps(miniRects, { left: 16, right: MW - 16, top: padT - 12, bottom: MH - padB + 8 }, 8);
      const miniPos = new Map(miniRects.map((r) => [r.id, r]));
      const miniLines = rawEdges
        .map((e) => {
          const a = miniPos.get(e.from);
          const b = miniPos.get(e.to);
          if (!a || !b) return '';
          const col = edgeColor(e);
          const dash = e.kind === 'gaze' ? '4 3' : e.kind === 'barrier' ? '7 4' : e.kind === 'parallel' ? '6 3' : 'none';
          return (
            '<line x1="' +
            (a.x + a.w / 2) +
            '" y1="' +
            (a.y + a.h / 2) +
            '" x2="' +
            (b.x + b.w / 2) +
            '" y2="' +
            (b.y + b.h / 2) +
            '" stroke="' +
            col +
            '" stroke-width="2" stroke-dasharray="' +
            dash +
            '" marker-end="url(#sp-mini-arr)"/>'
          );
        })
        .join('');
      const miniNodesSvg = miniRects
        .map((r) => {
          const short = String(r.node.label || r.id).slice(0, 7);
          return (
            '<g data-jump="' +
            esc((r.node.blocks && r.node.blocks[0]) || '') +
            '" style="cursor:pointer">' +
            '<rect x="' +
            r.x +
            '" y="' +
            r.y +
            '" width="' +
            r.w +
            '" height="' +
            r.h +
            '" rx="6" fill="var(--surface)" stroke="var(--ink-3)" stroke-width="1.3"/>' +
            '<text x="' +
            (r.x + r.w / 2) +
            '" y="' +
            (r.y + 20) +
            '" text-anchor="middle" font-size="11.5" font-weight="600" fill="var(--ink)">' +
            esc((r.idx + 1) + '. ' + short) +
            '</text></g>'
          );
        })
        .join('');
      const backdropSvg = isStage
        ? '<rect x="14" y="30" width="' +
          (MW - 28) +
          '" height="' +
          (MH - 62) +
          '" rx="10" fill="none" stroke="var(--line-strong)" stroke-dasharray="6 4"/>' +
          '<text x="' +
          MW / 2 +
          '" y="20" text-anchor="middle" font-size="11" fill="var(--ink-3)">↑ 窗外 / 后景</text>' +
          '<text x="' +
          MW / 2 +
          '" y="' +
          (MH - 12) +
          '" text-anchor="middle" font-size="11" fill="var(--ink-3)">↓ 台口 / 观众席</text>'
        : '<text x="' +
          (MW - 18) +
          '" y="22" text-anchor="end" font-size="11" fill="var(--ink-3)">北 ↑ · 西←→东</text>';
      const listHtml = nodes
        .map((n, idx) => {
          const moves = rawEdges
            .filter((e) => e && (e.from === n.id || e.to === n.id))
            .slice(0, 3)
            .map((e) => {
              const actorPrefix = e.actor ? '[' + esc(e.actor) + '] ' : '';
              return '<span class="spatial-move-chip">' + actorPrefix + esc(e.label || e.kind || '动线') + '</span>';
            })
            .join('');
          return (
            '<div class="spatial-mini-item" data-jump="' +
            esc((n.blocks && n.blocks[0]) || '') +
            '">' +
            '<div class="spatial-mini-head"><b>' +
            (idx + 1) +
            '. ' +
            esc(n.label) +
            '</b><span>' +
            (n.zone ? '<span class="spatial-zone-pill">' + esc(n.zone) + '</span> ' : '') +
            renderSrcTag(n.source) +
            '</span></div>' +
            (n.detail ? '<div class="ext-sub">' + esc(n.detail) + '</div>' : '') +
            (moves ? '<div class="camp-rel-row">' + moves + '</div>' : '') +
            '<div>' +
            renderAnchorPills(n.blocks, 3) +
            '</div></div>'
          );
        })
        .join('');
      return (
        '<div class="spatial-mini-wrap" data-qa-target="spatial-mini">' +
        '<div class="spatial-mode-bar"><span>' +
        esc(isStage ? '封闭舞台调度缩略图' : '二维地点动线缩略图') +
        (data.stage_desc ? ' · ' + esc(data.stage_desc) : '') +
        '</span><button type="button" class="inline-zoom-link" data-bp-zoom-inline>放大全览 ⤢</button></div>' +
        '<svg class="spatial-mini-svg" viewBox="0 0 ' +
        MW +
        ' ' +
        MH +
        '"><defs><marker id="sp-mini-arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="var(--ink-2)"/></marker></defs>' +
        backdropSvg +
        miniLines +
        miniNodesSvg +
        '</svg>' +
        '<div class="spatial-mini-list">' +
        listHtml +
        '</div></div>'
      );
    }

    // Wide mode: 16:10 2D canvas with real DOM measurement + order-preserving overlap repulsion
    const expandAll = Boolean(options && options.expandAll);
    const maxAnchors = !expandAll ? 3 : 0;
    const cardW = expandAll ? 176 : 166;
    const sizes = measureNodeSizes(nodes, cardW, { expandAll, maxAnchors, showZone: true });
    let W = 880;
    let H = 550;
    const padX = 36;
    const padT = isStage ? 58 : 46;
    const padB = isStage ? 52 : 40;
    let rects = nodes.map((n, idx) => {
      const sz = sizes.get(n.id);
      const nx = Number.isFinite(Number(n.x)) ? Number(n.x) : 18 + (idx % 4) * 20;
      const ny = Number.isFinite(Number(n.y)) ? Number(n.y) : 20 + Math.floor(idx / 4) * 25;
      const rx = padX + ((Math.max(4, Math.min(96, nx)) - 4) / 92) * (W - padX * 2 - sz.w);
      const ry = padT + ((Math.max(4, Math.min(96, ny)) - 4) / 92) * (H - padT - padB - sz.h);
      return { id: n.id, x: Math.round(rx), y: Math.round(ry), w: sz.w, h: sz.h };
    });
    for (let grow = 0; grow < 3; grow++) {
      if (resolve2DOverlaps(rects, { left: padX, right: W - padX, top: padT, bottom: H - padB }, 14)) break;
      W = Math.round(W * 1.15);
      H = Math.round(H * 1.15);
      rects.forEach((r) => {
        r.x = Math.round(r.x * 1.15);
        r.y = Math.round(r.y * 1.15);
      });
    }
    const pos = new Map(rects.map((r) => [r.id, r]));
    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i })]));
    const directedEdges = rawEdges
      .filter((e) => e && idMap.has(e.from) && idMap.has(e.to))
      .map((e, idx) => Object.assign({}, e, { idx, isSelfLoop: e.from === e.to }));
    const uidSuffix = 'sp-' + ++markerSeq;
    const placedLabels = [];
    const fallbackLegend = [];
    const svgEdgePaths = [];
    const htmlEdgeLabels = [];
    const markerDefs = [];

    const pairCounts = new Map();
    directedEdges.forEach((e) => {
      if (e.isSelfLoop) return;
      const orig = e;
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) return;
      const pk = [e.from, e.to].sort().join('\0');
      const seq = pairCounts.get(pk) || 0;
      pairCounts.set(pk, seq + 1);
      const curveOffset = seq === 0 ? 14 : seq % 2 === 1 ? 34 : -34;
      const acx = a.x + a.w / 2;
      const acy = a.y + a.h / 2;
      const bcx = b.x + b.w / 2;
      const bcy = b.y + b.h / 2;
      const dx = bcx - acx;
      const dy = bcy - acy;
      let p0, p1;
      if (Math.abs(dx) >= Math.abs(dy)) {
        p0 = { x: dx >= 0 ? a.x + a.w : a.x, y: acy };
        p1 = { x: dx >= 0 ? b.x : b.x + b.w, y: bcy };
      } else {
        p0 = { x: acx, y: dy >= 0 ? a.y + a.h : a.y };
        p1 = { x: bcx, y: dy >= 0 ? b.y : b.y + b.h };
      }
      const len = Math.max(1, Math.hypot(p1.x - p0.x, p1.y - p0.y));
      const nx = -(p1.y - p0.y) / len;
      const ny = (p1.x - p0.x) / len;
      const cx = Math.round((p0.x + p1.x) / 2 + nx * curveOffset);
      const cy = Math.round((p0.y + p1.y) / 2 + ny * curveOffset);
      const d = 'M ' + p0.x + ',' + p0.y + ' Q ' + cx + ',' + cy + ' ' + p1.x + ',' + p1.y;
      const col = edgeColor(orig);
      const mId = 'sp-m-' + uidSuffix + '-' + e.idx;
      markerDefs.push(
        '<marker id="' +
          mId +
          '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="' +
          col +
          '"/></marker>'
      );
      const dash = orig.kind === 'gaze' ? '4 3' : orig.kind === 'barrier' ? '7 4' : orig.kind === 'parallel' ? '6 3' : 'none';
      const fromLabel = (idMap.get(e.from) && idMap.get(e.from).label) || e.from;
      const toLabel = (idMap.get(e.to) && idMap.get(e.to).label) || e.to;
      const actorStr = orig.actor ? '[' + orig.actor + '] ' : '';
      const edgeDesc = actorStr + fromLabel + ' → ' + toLabel + (e.label ? '：' + e.label : '') + (e.note ? '（' + e.note + '）' : '');

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
          col +
          '" stroke-dasharray="' +
          dash +
          '" marker-end="url(#' +
          mId +
          ')"/>' +
          '<path class="dag-edge-hit" d="' +
          d +
          '"/></g>'
      );

      const fullLbl = orig.actor && e.label ? orig.actor + '·' + e.label : e.label || orig.actor || '';
      if (fullLbl) {
        const lsz = measureLabelSize(fullLbl);
        const mx = Math.round(0.25 * p0.x + 0.5 * cx + 0.25 * p1.x);
        const my = Math.round(0.25 * p0.y + 0.5 * cy + 0.25 * p1.y);
        const cands = [
          { x: mx, y: my },
          { x: mx, y: my - 16 },
          { x: mx, y: my + 16 },
          { x: Math.round(0.5625 * p0.x + 0.375 * cx + 0.0625 * p1.x), y: Math.round(0.5625 * p0.y + 0.375 * cy + 0.0625 * p1.y) },
          { x: Math.round(0.0625 * p0.x + 0.375 * cx + 0.5625 * p1.x), y: Math.round(0.0625 * p0.y + 0.375 * cy + 0.5625 * p1.y) },
        ];
        let chosenRect = null;
        for (const c of cands) {
          const box = { x: Math.round(c.x - lsz.w / 2), y: Math.round(c.y - lsz.h / 2), w: lsz.w, h: lsz.h };
          if (box.x < 4 || box.x + box.w > W - 4 || box.y < 4 || box.y + box.h > H - 4) continue;
          if (!rects.some((nr) => rectsOverlap(box, nr, 4)) && !placedLabels.some((lr) => rectsOverlap(box, lr, 4))) {
            chosenRect = box;
            break;
          }
        }
        if (chosenRect) {
          placedLabels.push(chosenRect);
          htmlEdgeLabels.push(
            '<span class="dag-edge-label" data-edge-idx="' +
              e.idx +
              '" style="left:' +
              chosenRect.x +
              'px;top:' +
              chosenRect.y +
              'px;color:' +
              col +
              '" title="' +
              esc(edgeDesc) +
              '">' +
              esc(fullLbl) +
              '</span>'
          );
        } else {
          const badgeNum = fallbackLegend.length + 1;
          const circ = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮'[badgeNum - 1] || String(badgeNum);
          fallbackLegend.push({ idx: e.idx, text: circ + ' ' + edgeDesc });
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
          esc(n.role || 'zone') +
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
          '">' +
          '<div class="dag-node-role"><span>' +
          esc(ROLE_ZH[n.role] || n.role || '地点') +
          (n.zone ? ' · ' + esc(n.zone) : '') +
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

    const stageChrome = isStage
      ? '<div class="spatial-stage-top">↑ 窗外 / 后景' +
        (data.stage_desc ? ' · ' + esc(data.stage_desc) : '') +
        '</div><div class="spatial-stage-bot">↓ 台口 / 观众席</div>'
      : '<div class="spatial-stage-top">北 N ↑ · 上北下南 左西右东' +
        (data.stage_desc ? ' · ' + esc(data.stage_desc) : '') +
        '</div>';

    const actorLegend = actors.length
      ? '<div class="rel-kind-legend">' +
        actors
          .map(
            (a) =>
              '<span class="rel-legend-chip"><i style="background:' +
              actorColors.get(a) +
              '"></i>' +
              esc(a) +
              '</span>'
          )
          .join('') +
        '</div>'
      : '';

    const legendHtml = fallbackLegend.length
      ? '<div class="edge-legend">' +
        fallbackLegend
          .map((item) => '<span class="edge-legend-pill" data-edge-idx="' + item.idx + '">' + esc(item.text) + '</span>')
          .join('') +
        '</div>'
      : '';

    return (
      '<div class="dag-viewport" data-qa-target="spatial-2d">' +
      '<div class="dag-canvas spatial-canvas ' +
      (isStage ? 'is-stage-blocking' : 'is-route-map') +
      '" style="width:' +
      W +
      'px;height:' +
      H +
      'px">' +
      stageChrome +
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
      svgEdgePaths.join('') +
      '</svg>' +
      cardsHtml +
      htmlEdgeLabels.join('') +
      '</div></div>' +
      actorLegend +
      '<div class="dag-trace-tip" data-trace-tip>悬停地点或动线可高亮轨迹；点击节点跳转原文</div>' +
      legendHtml
    );
  }

  function renderExtensionItemHtml(it, maxAnchors) {
    const headTxt = it.name || it.title || it.label || it.text || '';
    const badge = it.stance || it.role_badge || '';
    const isPostmortem = Boolean(it.pitfall || it.case || it.root_cause || it.fix);
    const bodyHtml = isPostmortem
      ? '<div class="pm-grid">' +
        (it.pitfall ? '<div class="pm-row pm-pitfall"><span class="pm-lbl">误区行为</span><span>' + esc(it.pitfall) + '</span></div>' : '') +
        (it.case ? '<div class="pm-row pm-case"><span class="pm-lbl">反例/后果</span><span>' + esc(it.case) + '</span></div>' : '') +
        (it.root_cause ? '<div class="pm-row pm-cause"><span class="pm-lbl">失效根因</span><span>' + esc(it.root_cause) + '</span></div>' : '') +
        (it.fix ? '<div class="pm-row pm-fix"><span class="pm-lbl">纠偏动作</span><span>' + esc(it.fix) + '</span></div>' : '') +
        '</div>'
      : it.body || it.detail
        ? '<div class="ext-sub">' + esc(it.body || it.detail) + '</div>'
        : '';
    return (
      '<div class="ext-item' +
      (isPostmortem ? ' ext-postmortem-item' : '') +
      '">' +
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
      bodyHtml +
      (it.warning ? '<div class="ext-sub" style="color:var(--ochre)">提示：' + esc(it.warning) + '</div>' : '') +
      '<div class="ext-item-foot">' +
      renderAnchorPills(it.blocks, isPostmortem ? 3 : maxAnchors || 3) +
      '</div></div>'
    );
  }

  function renderChecklistItemHtml(it) {
    return (
      '<div class="ext-item ext-check-item"><label class="ext-check-label"><input type="checkbox"' +
      (it.checked ? ' checked' : '') +
      '><span>' +
      esc(it.text || it.title) +
      '</span></label>' +
      (it.detail || it.body ? '<div class="ext-sub">' + esc(it.detail || it.body) + '</div>' : '') +
      '<div class="ext-item-foot">' +
      renderSrcTag(it.source) +
      ' ' +
      renderAnchorPills(it.blocks, 3) +
      '</div></div>'
    );
  }

  function renderExtensionOrFallbackHtml(payload, options) {
    const items = payload.items || [];
    const maxAnchors = 3;
    const isFallback = payload.blueprint === 'fallback_list';
    const firstWarning = items.map((it) => it && it.warning).find(Boolean) || '';
    const noticeTxt = payload.fallback_notice || (isFallback ? firstWarning : '');
    const noticeHtml = noticeTxt ? '<div class="bp-fallback-banner">' + esc(noticeTxt) + '</div>' : '';

    if (payload.blueprint === 'extension_checklist') {
      if (Array.isArray(payload.sections) && payload.sections.length) {
        return (
          noticeHtml +
          '<div class="ext-sections">' +
          payload.sections
            .map(
              (sec) =>
                '<div class="ext-section" data-section-id="' +
                esc(sec.id || '') +
                '">' +
                '<h5 class="ext-sec-title">' +
                esc(sec.title) +
                '</h5>' +
                '<div class="ext-list">' +
                (sec.items || []).map((it) => renderChecklistItemHtml(it)).join('') +
                '</div></div>'
            )
            .join('') +
          '</div>'
        );
      }
      return noticeHtml + '<div class="ext-list">' + items.map((it) => renderChecklistItemHtml(it)).join('') + '</div>';
    }

    if (Array.isArray(payload.sections) && payload.sections.length) {
      return (
        noticeHtml +
        '<div class="ext-sections">' +
        payload.sections
          .map(
            (sec) =>
              '<div class="ext-section" data-section-id="' +
              esc(sec.id || '') +
              '">' +
              '<h5 class="ext-sec-title">' +
              esc(sec.title) +
              '</h5>' +
              '<div class="ext-list">' +
              (sec.items || []).map((it) => renderExtensionItemHtml(it, maxAnchors)).join('') +
              '</div></div>'
          )
          .join('') +
        '</div>'
      );
    }

    const hasSections = items.some((it) => String((it && it.section) || '').trim());
    if (!hasSections) {
      return (
        noticeHtml +
        '<div class="ext-list">' +
        items.map((it) => renderExtensionItemHtml(it, maxAnchors)).join('') +
        '</div>'
      );
    }

    const secMap = new Map();
    items.forEach((it) => {
      const sec = String((it && it.section) || '').trim() || '细读要点';
      if (!secMap.has(sec)) secMap.set(sec, []);
      secMap.get(sec).push(it);
    });
    return (
      noticeHtml +
      '<div class="ext-sections">' +
      Array.from(secMap.entries())
        .map(
          ([secName, list]) =>
            '<div class="ext-section"><div class="ext-section-head">' +
            esc(secName) +
            '</div><div class="ext-list">' +
            list.map((it) => renderExtensionItemHtml(it, maxAnchors)).join('') +
            '</div></div>'
        )
        .join('') +
      '</div>'
    );
  }

  function renderCausalLoopNarrowHtml(payload, nodes, rawEdges) {
    const data = (payload && payload.data) || {};
    const loops = Array.isArray(data.loops) ? data.loops.filter((l) => l && (l.label || l.id)) : [];
    const idMap = new Map(nodes.map((n) => [String(n.id), n]));
    const outMap = new Map();
    (rawEdges || []).forEach((e) => {
      if (!e || !idMap.has(String(e.from)) || !idMap.has(String(e.to))) return;
      const key = String(e.from);
      if (!outMap.has(key)) outMap.set(key, []);
      outMap.get(key).push(e);
    });
    const loopHtml = loops.length
      ? '<div class="causal-loop-strip">' +
        loops
          .slice(0, 4)
          .map(
            (lp) =>
              '<span class="causal-loop-chip"><b>' +
              esc(lp.id || '回路') +
              '</b>' +
              esc(lp.label || '') +
              '</span>'
          )
          .join('') +
        '</div>'
      : '';
    const groups = SOCIAL_CAUSAL_ROLE_ORDER.map((role) => {
      const group = nodes.filter((n) => (n.role && SOCIAL_CAUSAL_ROLE_ZH[n.role] ? n.role : 'mechanism') === role);
      if (!group.length) return '';
      const cards = group
        .map((n) => {
          const outs = (outMap.get(String(n.id)) || [])
            .map((e) => {
              const tgt = idMap.get(String(e.to));
              const pol = e.polarity === '-' ? '-' : e.polarity === '+' ? '+' : '';
              return (
                '<span class="causal-next">' +
                '<i class="causal-arrow">→</i>' +
                esc((tgt && tgt.label) || e.to) +
                (pol
                  ? '<i class="causal-pol ' + (pol === '-' ? 'pol-neg' : 'pol-pos') + '">' + pol + '</i>'
                  : '') +
                (e.delay === true ? '<i class="causal-delay">滞后</i>' : '') +
                '</span>'
              );
            })
            .join('');
          return (
            '<div class="causal-card role-' +
            esc(role) +
            '">' +
            '<div class="causal-card-head"><b>' +
            esc(n.label) +
            '</b>' +
            renderSrcTag(n.source) +
            '</div>' +
            (n.detail ? '<div class="causal-card-detail">' + esc(n.detail) + '</div>' : '') +
            (outs ? '<div class="causal-outs">' + outs + '</div>' : '') +
            '<div class="causal-card-foot">' +
            renderAnchorPills(n.blocks, 3) +
            '</div></div>'
          );
        })
        .join('');
      return (
        '<section class="causal-role-group" data-role="' +
        role +
        '"><h5 class="causal-role-head"><i class="causal-role-dot role-' +
        role +
        '"></i>' +
        SOCIAL_CAUSAL_ROLE_ZH[role] +
        '</h5>' +
        cards +
        '</section>'
      );
    }).join('');
    return (
      '<div class="causal-flow" data-qa-target="causal-flow">' +
      '<div class="causal-flow-bar"><span class="causal-flow-title">因果链路与回路</span>' +
      '<button type="button" class="lineage-zoom-inline" data-bp-zoom title="宽屏全览因果回路图">全览图谱 ⤢</button></div>' +
      loopHtml +
      groups +
      '</div>'
    );
  }

  function renderConceptMatrixCardsHtml(payload) {
    const rows = (payload.data && payload.data.rows) || [];
    const cards = rows
      .map(
        (r) =>
          '<div class="concept-card">' +
          '<div class="concept-card-head"><div class="concept-card-title"><b>' +
          esc(r.key) +
          '</b>' +
          (r.original_term ? '<span class="tc-orig">' + esc(r.original_term) + '</span>' : '') +
          '</div>' +
          renderSrcTag(r.source) +
          '</div>' +
          (r.everyday
            ? '<div class="concept-field"><span class="concept-field-lbl">核心界定</span><p>' + esc(r.everyday) + '</p></div>'
            : '') +
          (r.author
            ? '<div class="concept-field"><span class="concept-field-lbl">关键机制</span><p>' + esc(r.author) + '</p></div>'
            : '') +
          (r.extra
            ? '<div class="concept-field concept-field-warn"><span class="concept-field-lbl">盲区/反例</span><p>' +
              esc(r.extra) +
              '</p></div>'
            : '') +
          '<div class="concept-card-foot">' +
          renderAnchorPills(r.blocks, 3) +
          '</div></div>'
      )
      .join('');
    return '<div class="concept-cards" data-qa-target="concept-cards">' + cards + '</div>';
  }

  function metricValueText(value, unit) {
    const rounded = Math.abs(value) >= 1000 ? Math.round(value * 10) / 10 : Math.round(value * 100) / 100;
    return String(rounded) + (unit || '');
  }

  function renderMetricChartHtml(payload, options) {
    const data = (payload && payload.data) || {};
    const wideMode = Boolean(options && options.wide);
    const mode = String(data.mode || (Array.isArray(data.series) ? 'series' : '')).toLowerCase();
    if (mode === 'not_applicable') {
      const highlights = Array.isArray(data.highlights) ? data.highlights : [];
      return (
        '<div class="metric-na" data-qa-target="metric-na">' +
        '<div class="metric-na-card"><div class="metric-na-head"><span class="metric-na-badge">本章无可量化统计</span></div>' +
        '<p class="metric-na-reason">' +
        esc(data.reason || '本章为定性论述，未提供连续或可对比的量化统计数据。') +
        '</p></div>' +
        (highlights.length
          ? '<div class="metric-na-hl-wrap"><div class="metric-na-hl-title">定性史例与观察锚点</div><div class="metric-na-hl-list">' +
            highlights
              .slice(0, 4)
              .map(
                (h) =>
                  '<div class="metric-na-hl"><span class="metric-na-hl-text">' +
                  esc(h.text || '') +
                  '</span><span class="metric-na-hl-foot">' +
                  renderSrcTag(h.source) +
                  renderAnchorPills(h.blocks, 3) +
                  '</span></div>'
              )
              .join('') +
            '</div></div>'
          : '') +
        '</div>'
      );
    }
    const series = (Array.isArray(data.series) ? data.series : []).filter(
      (s) => s && Array.isArray(s.points) && s.points.length
    );
    if (!series.length) return '';
    const unit = String(data.unit || '');
    const yLabel = String(data.yLabel || '指标');
    const W = wideMode ? 680 : 300;
    const H = wideMode ? 268 : 238;
    const padL = wideMode ? 56 : 44;
    const padR = wideMode ? 26 : 14;
    const padT = 26;
    const padB = 48;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const values = [];
    series.forEach((s) => s.points.forEach((p) => values.push(Number(p.value))));
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    if (!isFinite(minVal) || !isFinite(maxVal)) return '';
    let lo = minVal >= 0 ? 0 : minVal;
    let hi = maxVal <= 0 ? 0 : maxVal;
    if (hi === lo) hi = lo + 1;
    const span = hi - lo;
    if (minVal < 0) lo -= span * 0.08;
    hi += span * 0.08;
    const yOf = (v) => padT + ((hi - v) / (hi - lo)) * plotH;
    const xs = series.reduce((acc, s) => (s.points.length > acc.length ? s.points : acc), []);
    const count = Math.max(1, xs.length);
    const xOf = (i) => padL + (count === 1 ? plotW / 2 : (plotW * i) / (count - 1));
    const xCenter = (i) => (mode === 'comparison' ? padL + (plotW / count) * (i + 0.5) : xOf(i));
    const palette = ['var(--cinnabar)', 'var(--pine)', 'var(--indigo)', '#7c4d2b'];
    const ticks = 4;
    const grid = [];
    for (let t = 0; t <= ticks; t++) {
      const v = lo + ((hi - lo) * t) / ticks;
      const y = yOf(v);
      grid.push(
        '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="var(--rule)" stroke-dasharray="3 3"/>' +
          '<text x="' + (padL - 6) + '" y="' + (y + 3) + '" text-anchor="end" class="metric-tick">' +
          esc(metricValueText(Math.round(v * 100) / 100, '')) +
          '</text>'
      );
    }
    const zeroY = yOf(0);
    const zeroLine =
      lo < 0 && hi > 0
        ? '<line x1="' + padL + '" y1="' + zeroY + '" x2="' + (W - padR) + '" y2="' + zeroY + '" stroke="var(--ink-3)" stroke-width="1.2"/>'
        : '';
    let plot = '';
    if (mode === 'comparison') {
      const groupW = plotW / count;
      const barW = Math.max(6, Math.min(wideMode ? 30 : 18, (groupW * 0.72) / series.length));
      series.forEach((s, si) => {
        s.points.forEach((p, i) => {
          const v = Number(p.value);
          const y = yOf(Math.max(0, v));
          const h = Math.abs(yOf(v) - yOf(0));
          const gx = padL + groupW * i + groupW / 2 - (barW * series.length) / 2 + si * barW;
          plot +=
            '<rect class="metric-bar" x="' + gx + '" y="' + Math.min(y, yOf(0)) + '" width="' + barW + '" height="' + Math.max(2, h) + '" fill="' +
            palette[si % palette.length] +
            '" rx="2"><title>' + esc(s.name + ' ' + p.x + ' ' + metricValueText(v, unit)) + '</title></rect>' +
            '<text x="' + (gx + barW / 2) + '" y="' + (Math.min(y, yOf(0)) - 4) + '" text-anchor="middle" class="metric-val">' +
            esc(metricValueText(v, '')) +
            '</text>';
        });
      });
    } else {
      series.forEach((s, si) => {
        const pts = s.points.map((p, i) => xOf(i) + ',' + yOf(Number(p.value)));
        plot +=
          '<polyline class="metric-line" points="' +
          pts.join(' ') +
          '" fill="none" stroke="' +
          palette[si % palette.length] +
          '" stroke-width="2"/>';
        s.points.forEach((p, i) => {
          const v = Number(p.value);
          const cx = xOf(i);
          const cy = yOf(v);
          plot +=
            '<circle class="metric-dot" cx="' + cx + '" cy="' + cy + '" r="3.4" fill="' + palette[si % palette.length] + '"><title>' +
            esc(s.name + ' ' + p.x + ' ' + metricValueText(v, unit)) +
            '</title></circle>' +
            '<text x="' + cx + '" y="' + (cy - (i % 2 ? 12 : 8)) + '" text-anchor="middle" class="metric-val">' +
            esc(metricValueText(v, '')) +
            '</text>';
        });
      });
    }
    const xLabels = xs
      .map(
        (p, i) =>
          '<text x="' + xCenter(i) + '" y="' + (H - padB + 18) + '" text-anchor="middle" class="metric-xlabel">' + esc(p.x || '') + '</text>'
      )
      .join('');
    const legend =
      series.length > 1
        ? '<div class="chart-legend-list metric-legend">' +
          series
            .map(
              (s, si) =>
                '<span class="chart-legend-item"><i class="metric-leg-dot" style="background:' +
                palette[si % palette.length] +
                '"></i>' +
                esc(s.name) +
                '</span>'
            )
            .join('') +
          '</div>'
        : '';
    const usedBlocks = [];
    series.forEach((s) =>
      s.points.forEach((p) => (p.blocks || []).forEach((b) => usedBlocks.includes(b) || usedBlocks.push(b)))
    );
    return (
      '<div class="metric-chart" data-qa-target="metric-chart" data-mode="' +
      esc(mode) +
      '">' +
      '<div class="metric-chart-head"><span class="metric-y-label">' +
      esc(yLabel) +
      (unit && yLabel.indexOf(unit) < 0 ? '<i class="metric-unit">（' + esc(unit) + '）</i>' : '') +
      '</span><span class="metric-mode-tag">' +
      (mode === 'comparison' ? '真实对比' : '真实序列') +
      '</span></div>' +
      '<svg class="metric-svg" viewBox="0 0 ' +
      W +
      ' ' +
      H +
      '" width="100%" role="img" aria-label="' +
      esc(yLabel) +
      '">' +
      grid.join('') +
      zeroLine +
      plot +
      xLabels +
      '</svg>' +
      legend +
      '<div class="metric-chart-foot">' +
      renderAnchorPills(usedBlocks, 3) +
      '</div></div>'
    );
  }

  function renderActorCardsHtml(payload, options) {
    const wideMode = Boolean(options && options.wide);
    const items = (payload.items || (payload.sections || []).flatMap((s) => s.items || [])).slice(0, 8);
    const fields = [
      ['incentive', '核心激励'],
      ['constraint', '硬约束'],
      ['strategy', '现实策略'],
    ];
    const cards = items
      .map((it) => {
        const rows = fields
          .filter(([k]) => it[k])
          .map(
            ([k, lbl]) =>
              '<div class="actor-field"><span class="actor-field-lbl">' +
              lbl +
              '</span><p class="actor-field-txt">' +
              esc(it[k]) +
              '</p></div>'
          )
          .join('');
        const fallbackBody = !rows && (it.body || it.detail) ? '<div class="actor-body">' + esc(it.body || it.detail) + '</div>' : '';
        return (
          '<div class="actor-card">' +
          '<div class="actor-card-head"><div class="actor-name-wrap"><b class="actor-name">' +
          esc(it.title || it.text || it.name || '行动者') +
          '</b>' +
          (it.role_tag ? '<span class="actor-role-tag">' + esc(it.role_tag) + '</span>' : '') +
          '</div>' +
          renderSrcTag(it.source) +
          '</div>' +
          rows +
          fallbackBody +
          '<div class="actor-card-foot">' +
          renderAnchorPills(it.blocks, 3) +
          '</div></div>'
        );
      })
      .join('');
    return (
      '<div class="actor-cards' +
      (wideMode ? ' wide' : '') +
      '" data-qa-target="actor-cards">' +
      cards +
      '</div>'
    );
  }

  function renderBlueprintBody(payload, options) {
    if (!payload || typeof payload !== 'object') return '';
    const bp = payload.blueprint || 'flow_swimlane';
    const wideMode = Boolean(options && options.wide);
    if (bp === 'parallel_timeline') return renderTimelineHtml(payload, options);
    if (bp === 'concept_matrix') {
      return wideMode ? renderMatrixTableHtml(payload, options) : renderConceptMatrixCardsHtml(payload);
    }
    if (MATRIX_BLUEPRINTS.has(bp)) return renderMatrixTableHtml(payload, options);
    if (bp === 'metric_chart') return renderMetricChartHtml(payload, options);
    if (bp === 'quadrant_matrix') return renderQuadrantHtml(payload, options);
    if (bp === 'sensitivity_chart') return renderSensitivityHtml(payload, options);
    if (CURVE_BLUEPRINTS.has(bp)) return renderSplineChartHtml(payload, options);
    if (bp === 'relation_graph') return renderRelationGraphHtml(payload, options);
    if (bp === 'spatial_route') {
      const spMode = String((payload.data && payload.data.mode) || '').toLowerCase();
      if (spMode === 'route_map' || spMode === 'stage_blocking') {
        return renderSpatialRouteHtml(payload, options);
      }
      return renderSpatialMapHtml(payload, options);
    }
    if (bp === 'extension_glossary') return renderConceptGlossaryHtml(payload, options);
    if (payload.variant === 'actor_incentives') return renderActorCardsHtml(payload, options);
    if (bp === 'extension_dossier' || bp === 'extension_checklist' || bp === 'fallback_list') {
      const hasActorFields = (payload.items || []).some((it) => it && (it.incentive || it.constraint || it.strategy));
      if (hasActorFields) return renderActorCardsHtml(payload, options);
      return renderExtensionOrFallbackHtml(payload, options);
    }
    if (DAG_BLUEPRINTS.has(bp) || (payload.data && Array.isArray(payload.data.nodes))) {
      return renderLayeredDagHtml(payload, options);
    }
    return renderExtensionOrFallbackHtml(payload, options);
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

  function bindStageInteractions(rootEl, onFocusBlock, optsOrZoom) {
    if (!rootEl) return;
    const opts = typeof optsOrZoom === 'function' ? { onZoomRequest: optsOrZoom } : optsOrZoom || {};
    const onZoomRequest = opts.onZoomRequest;
    const onExpandNode = opts.onExpandNode;
    const onExpandAnchors = opts.onExpandAnchors;

    rootEl.querySelectorAll('[data-expand-anchors]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const dagNode = btn.closest('.dag-node[data-node-id]');
        if (dagNode && onExpandAnchors) {
          onExpandAnchors(dagNode.getAttribute('data-node-id'));
          return;
        }
        const wrap = btn.parentElement;
        const extra = wrap && wrap.querySelector('.blk-anchor-extra');
        if (extra) extra.hidden = false;
        btn.hidden = true;
      });
    });

    const bindCardFilter = (barSel, btnAttr, cardSel, cardAttr, emptySel) => {
      const bar = rootEl.querySelector(barSel);
      if (!bar) return;
      const cards = Array.from(rootEl.querySelectorAll(cardSel));
      const emptyEl = emptySel ? rootEl.querySelector(emptySel) : null;
      bar.querySelectorAll('[' + btnAttr + ']').forEach((tab) => {
        tab.addEventListener('click', (e) => {
          e.stopPropagation();
          const filter = tab.getAttribute(btnAttr) || 'all';
          bar.querySelectorAll('[' + btnAttr + ']').forEach((b) => {
            b.classList.toggle('active', b === tab);
          });
          let shown = 0;
          cards.forEach((c) => {
            const match = filter === 'all' || c.getAttribute(cardAttr) === filter;
            c.hidden = !match;
            if (match) shown += 1;
          });
          if (emptyEl) emptyEl.hidden = shown > 0;
        });
      });
    };
    bindCardFilter('[data-lineage-bar]', 'data-lineage-filter', '.lineage-card[data-lineage-group]', 'data-lineage-group', '.lineage-filter-empty');
    bindCardFilter('[data-decision-bar]', 'data-decision-filter', '.decision-card[data-decision-group]', 'data-decision-group', '.lineage-filter-empty');
    bindCardFilter('[data-quad-bar]', 'data-quad-filter', '.quad-narrow-card[data-quad-pos]', 'data-quad-pos', '');

    rootEl.querySelectorAll('[data-jump]').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target && e.target.closest('[data-expand-anchors]')) return;
        if (e.target && e.target.hasAttribute('data-toggle-detail')) {
          e.stopPropagation();
          const nid = el.getAttribute('data-node-id');
          if (nid && onExpandNode) {
            onExpandNode(nid);
            return;
          }
          el.classList.toggle('expanded');
          const tog = el.querySelector('[data-toggle-detail]');
          if (tog) tog.textContent = el.classList.contains('expanded') ? '▴ 收起说明' : '▾ 查看展开';
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

    const chartWrap = rootEl.querySelector('.chart-stage-wrap');
    const chartTip = chartWrap && chartWrap.querySelector('[data-chart-tip]');
    if (chartWrap && chartTip) {
      const showChartTip = (grp) => {
        const beat = grp.getAttribute('data-pt-beat') || '';
        const kind = grp.getAttribute('data-pt-kind') || '';
        const val = grp.getAttribute('data-pt-val') || '';
        const emo = grp.getAttribute('data-pt-emotion') || '';
        const note = grp.getAttribute('data-pt-note') || '';
        const x = Number(grp.getAttribute('data-pt-x') || 0);
        const y = Number(grp.getAttribute('data-pt-y') || 0);
        const w = Math.max(1, Number(grp.getAttribute('data-pt-w') || 440));
        chartTip.innerHTML =
          '<div class="chart-tip-head">' +
          (kind ? '<span class="chart-tip-kind">' + esc(kind) + '</span> ' : '') +
          '<b>' +
          esc(beat) +
          '</b> <span>(' +
          esc(val) +
          ')</span></div>' +
          (emo ? '<div class="chart-tip-emo">' + esc(emo) + '</div>' : '') +
          (note ? '<div class="chart-tip-note">' + esc(note) + '</div>' : '');
        chartTip.hidden = false;
        const leftPct = Math.max(16, Math.min(84, (x / w) * 100));
        chartTip.style.left = leftPct + '%';
        chartTip.style.top = y < 85 ? '68%' : '6px';
      };
      const hideChartTip = () => {
        chartTip.hidden = true;
      };
      chartWrap.querySelectorAll('.chart-pt-grp').forEach((grp) => {
        grp.addEventListener('mouseenter', () => showChartTip(grp));
        grp.addEventListener('focusin', () => showChartTip(grp));
        grp.addEventListener('mouseleave', hideChartTip);
        grp.addEventListener('focusout', hideChartTip);
      });
    }

    const canvas = rootEl.querySelector('.dag-canvas, .spatial-canvas');
    if (!canvas) return;
    const tipEl = rootEl.querySelector('[data-trace-tip]');
    const defaultTip = (tipEl && (tipEl.dataset.defaultTip || tipEl.textContent)) || '悬停节点或连线可高亮因果路径；点击节点跳转原文';
    if (tipEl && !tipEl.dataset.defaultTip) tipEl.dataset.defaultTip = defaultTip;
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
        '<button type="button" class="wb-btn zoom-expand-btn" id="btn-zoom-expand-all" aria-pressed="false" hidden>展开全部说明</button>' +
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
    const nodesArr = (figure && figure.data && Array.isArray(figure.data.nodes) && figure.data.nodes) || [];
    const hasDetails = nodesArr.some((n) => n && n.detail);
    let expandAll = false;
    const expandedIds = new Set();
    const expandedAnchorIds = new Set();
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

    const renderModalBody = (preserveScale) => {
      const html = renderBlueprintBody(figure, {
        wide: true,
        expandAll,
        expandedIds,
        expandedAnchorIds,
      });
      body.innerHTML = '<div class="zoom-canvas-wrap"><div class="zoom-canvas-scale">' + html + '</div></div>';
      const qa = runCollisionAudit(body);
      const qaBadge = document.getElementById('zoom-qa');
      if (qaBadge) {
        qaBadge.className = 'qa-badge' + (qa.overlaps > 0 ? ' warn' : '');
        qaBadge.textContent = qa.overlaps === 0 ? '✓ 宽屏质检：0 处重叠' : '⚠ 发现 ' + qa.overlaps + ' 处重叠';
      }
      bindStageInteractions(body, onFocusBlock, {
        onExpandNode: (nid) => {
          if (expandedIds.has(nid)) expandedIds.delete(nid);
          else expandedIds.add(nid);
          if (expandBtn) {
            expandAll = nodesArr.length > 0 && nodesArr.every((n) => !n.detail || expandedIds.has(n.id));
            expandBtn.setAttribute('aria-pressed', String(expandAll));
            expandBtn.textContent = expandAll ? '收起全部说明' : '展开全部说明';
          }
          renderModalBody(true);
        },
        onExpandAnchors: (nid) => {
          expandedAnchorIds.add(nid);
          renderModalBody(true);
        },
      });

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

    if (expandBtn) {
      expandBtn.onclick = () => {
        expandAll = !expandAll;
        expandedIds.clear();
        if (expandAll) nodesArr.forEach((n) => { if (n && n.detail) expandedIds.add(n.id); });
        expandBtn.setAttribute('aria-pressed', String(expandAll));
        expandBtn.textContent = expandAll ? '收起全部说明' : '展开全部说明';
        manualZoom = false;
        renderModalBody(false);
      };
    }

    if (outBtn) outBtn.onclick = () => { manualZoom = true; applyScale(curScale - 0.15); };
    if (inBtn) inBtn.onclick = () => { manualZoom = true; applyScale(curScale + 0.15); };
    if (fitBtn) {
      fitBtn.onclick = () => {
        manualZoom = true;
        if (fitScale < 0.96 && Math.abs(curScale - fitScale) >= 0.03) applyScale(fitScale);
        else applyScale(1);
      };
    }
    renderModalBody(false);
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

      containerEl.querySelectorAll('[data-bp-zoom], [data-bp-zoom-inline]').forEach((zoomBtn) => {
        zoomBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openZoomModal(figure, onFocusBlock);
        });
      });
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
