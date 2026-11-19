/* 分层读 · 五步防重叠确定性蓝图渲染引擎与全章树形导图 */
(function () {
  const RD = (window.RD = window.RD || {});

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
  };

  const DAG_BLUEPRINTS = new Set([
    'argument_map',
    'lineage_dag',
    'power_hierarchy',
    'spatial_route',
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

  function renderAnchorPills(blocks) {
    return (blocks || [])
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

  function measureNodeSizes(nodes, cardWidth) {
    const stage = ensureMeasureStage();
    const sizes = new Map();
    stage.style.width = cardWidth + 'px';
    stage.innerHTML = nodes
      .map(
        (n) =>
          '<div class="dag-node role-' +
          esc(n.role || 'inference') +
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
          (n.detail ? '<div class="dag-node-more">▾ 查看展开</div>' : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks) +
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
        sizes.set(n.id, { w: cardWidth, h: 58 + lines * 18 + (n.detail ? 16 : 0) });
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

  function renderLayeredDagHtml(payload, options) {
    const nodes = (payload.data && payload.data.nodes) || [];
    const edges = (payload.data && payload.data.edges) || [];
    if (!nodes.length) return '<div class="wb-sub">暂无图节点数据</div>';

    const wideMode = Boolean(options && options.wide);
    const wantLR =
      wideMode &&
      ['flow_swimlane', 'lineage_dag', 'spatial_route', 'system_flow'].includes(payload.blueprint);

    const idMap = new Map(nodes.map((n, i) => [n.id, Object.assign({}, n, { idx: i, indeg: 0, rank: 0 })]));
    const adj = new Map(nodes.map((n) => [n.id, []]));
    edges.forEach((e) => {
      if (idMap.has(e.from) && idMap.has(e.to)) {
        adj.get(e.from).push(e.to);
        idMap.get(e.to).indeg += 1;
      }
    });

    const q = [];
    idMap.forEach((n) => {
      if (n.indeg === 0) q.push(n.id);
    });
    const placed = new Set();
    let guard = 0;
    while (q.length && guard++ < 80) {
      const u = q.shift();
      placed.add(u);
      const uNode = idMap.get(u);
      (adj.get(u) || []).forEach((v) => {
        const vNode = idMap.get(v);
        if (vNode.rank <= uNode.rank) vNode.rank = uNode.rank + 1;
        vNode.indeg -= 1;
        if (vNode.indeg === 0) q.push(v);
      });
    }
    let nextR = 0;
    nodes.forEach((raw) => {
      const n = idMap.get(raw.id);
      if (!placed.has(n.id)) n.rank = nextR++;
    });

    const ranks = new Map();
    idMap.forEach((n) => {
      if (!ranks.has(n.rank)) ranks.set(n.rank, []);
      ranks.get(n.rank).push(n);
    });
    const sortedRanks = Array.from(ranks.keys()).sort((a, b) => a - b);

    let maxInRank = 1;
    sortedRanks.forEach((r) => {
      maxInRank = Math.max(maxInRank, ranks.get(r).length);
    });
    const cardW = wantLR ? 204 : maxInRank >= 2 ? 156 : 236;
    const sizes = measureNodeSizes(nodes, cardW);

    const colGap = wantLR ? 84 : 20;
    const rankGap = wantLR ? 24 : 60;

    const skipEdges = edges.filter((e) => {
      const fn = idMap.get(e.from);
      const tn = idMap.get(e.to);
      return fn && tn && (tn.rank - fn.rank > 1 || tn.rank <= fn.rank);
    });
    const leftCorridors = [];
    const rightCorridors = [];
    skipEdges.forEach((e, idx) => {
      if (idx % 2 === 0) leftCorridors.push(e);
      else rightCorridors.push(e);
    });
    const corridorStep = 54;
    const leftPad = leftCorridors.length ? leftCorridors.length * corridorStep + 24 : 14;
    const rightPad = rightCorridors.length ? rightCorridors.length * corridorStep + 24 : 14;
    const topPad = wantLR ? Math.max(24, skipEdges.length * 32 + 16) : 22;
    const botPad = wantLR ? Math.max(26, skipEdges.length * 38 + 22) : 24;

    const pos = new Map();
    const layerTop = new Map();
    const layerBottom = new Map();
    const layerLeft = new Map();
    const layerRight = new Map();
    let maxW = 0;
    let maxH = 0;

    if (!wantLR) {
      const layerHeights = sortedRanks.map((r) => Math.max(...ranks.get(r).map((n) => sizes.get(n.id).h)));
      const maxRowW = Math.max(
        ...sortedRanks.map((r) => {
          const list = ranks.get(r);
          return list.reduce((acc, n) => acc + sizes.get(n.id).w, 0) + (list.length - 1) * colGap;
        })
      );
      const innerWidth = Math.max(240, maxRowW);
      let curY = topPad;
      sortedRanks.forEach((r, ri) => {
        const list = ranks.get(r);
        const rowW = list.reduce((acc, n) => acc + sizes.get(n.id).w, 0) + (list.length - 1) * colGap;
        let curX = leftPad + (innerWidth - rowW) / 2;
        layerTop.set(r, Math.round(curY));
        layerBottom.set(r, Math.round(curY + layerHeights[ri]));
        list.forEach((n) => {
          const sz = sizes.get(n.id);
          pos.set(n.id, { x: Math.round(curX), y: Math.round(curY), w: sz.w, h: sz.h, rank: r });
          curX += sz.w + colGap;
        });
        curY += layerHeights[ri] + rankGap;
      });
      maxW = leftPad + innerWidth + rightPad;
      maxH = curY - rankGap + botPad;
    } else {
      const colWidths = sortedRanks.map((r) => Math.max(...ranks.get(r).map((n) => sizes.get(n.id).w)));
      const colHeights = sortedRanks.map((r) => {
        const list = ranks.get(r);
        return list.reduce((acc, n) => acc + sizes.get(n.id).h, 0) + (list.length - 1) * rankGap;
      });
      const maxColH = Math.max(190, ...colHeights);
      let curX = leftPad;
      sortedRanks.forEach((r, ri) => {
        const list = ranks.get(r);
        let curY = topPad + (maxColH - colHeights[ri]) / 2;
        layerLeft.set(r, Math.round(curX));
        layerRight.set(r, Math.round(curX + colWidths[ri]));
        list.forEach((n) => {
          const sz = sizes.get(n.id);
          pos.set(n.id, { x: Math.round(curX), y: Math.round(curY), w: sz.w, h: sz.h, rank: r });
          curY += sz.h + rankGap;
        });
        curX += colWidths[ri] + colGap;
      });
      maxW = curX - colGap + rightPad;
      maxH = topPad + maxColH + botPad;
    }

    const nodeRects = Array.from(pos.values()).map((p) => ({ x: p.x, y: p.y, w: p.w, h: p.h }));
    const placedLabels = [];
    const fallbackLegend = [];
    const uidSuffix = (wideMode ? 'w' : 'n') + '-' + Math.random().toString(36).slice(2, 6);
    const markerGreen = 'arr-g-' + uidSuffix;
    const markerRed = 'arr-r-' + uidSuffix;

    let leftIdx = 0;
    let rightIdx = 0;
    const svgEdgePaths = [];
    const htmlEdgeLabels = [];

    edges.forEach((e, idx) => {
      const a = pos.get(e.from);
      const b = pos.get(e.to);
      if (!a || !b) return;
      const isSkipOrBack = b.rank - a.rank > 1 || b.rank <= a.rank;
      let pts = [];
      let labelCandidates = [];

      if (!wantLR) {
        const outX = Math.round(a.x + a.w / 2);
        const outY = a.y + a.h;
        const inX = Math.round(b.x + b.w / 2);
        const inY = b.y - 3;
        if (!isSkipOrBack) {
          const aisleY = Math.round((layerBottom.get(a.rank) + layerTop.get(b.rank)) / 2) + ((idx % 3) - 1) * 4;
          pts =
            outX === inX
              ? [
                  { x: outX, y: outY },
                  { x: inX, y: inY },
                ]
              : [
                  { x: outX, y: outY },
                  { x: outX, y: aisleY },
                  { x: inX, y: aisleY },
                  { x: inX, y: inY },
                ];
          labelCandidates = [
            { x: Math.round((outX + inX) / 2), y: aisleY },
            { x: outX, y: Math.min(layerBottom.get(a.rank) + 18, aisleY) },
            { x: inX, y: Math.max(layerTop.get(b.rank) - 18, aisleY) },
          ];
        } else {
          const skipOrd = skipEdges.indexOf(e);
          const useLeft = skipOrd % 2 === 0;
          const gutterX = useLeft
            ? Math.round(leftPad - 30 - leftIdx++ * corridorStep)
            : Math.round(maxW - rightPad + 30 + rightIdx++ * corridorStep);
          const exitAisleY = layerBottom.get(a.rank) + 14 + (skipOrd % 3) * 5;
          const enterAisleY = layerTop.get(b.rank) - 16 - (skipOrd % 3) * 5;
          pts = [
            { x: outX, y: outY },
            { x: outX, y: exitAisleY },
            { x: gutterX, y: exitAisleY },
            { x: gutterX, y: enterAisleY },
            { x: inX, y: enterAisleY },
            { x: inX, y: inY },
          ];
          labelCandidates = [
            { x: gutterX, y: Math.round((exitAisleY + enterAisleY) / 2) },
            { x: gutterX, y: Math.round((a.y + b.y) / 2) },
            { x: Math.round((outX + gutterX) / 2), y: exitAisleY },
          ];
        }
      } else {
        const outX = a.x + a.w;
        const outY = a.y + Math.round(a.h / 2);
        const inX = b.x - 3;
        const inY = b.y + Math.round(b.h / 2);
        if (!isSkipOrBack) {
          const aisleX = Math.round((layerRight.get(a.rank) + layerLeft.get(b.rank)) / 2) + ((idx % 3) - 1) * 5;
          pts =
            outY === inY
              ? [
                  { x: outX, y: outY },
                  { x: inX, y: inY },
                ]
              : [
                  { x: outX, y: outY },
                  { x: aisleX, y: outY },
                  { x: aisleX, y: inY },
                  { x: inX, y: inY },
                ];
          labelCandidates = [
            { x: aisleX, y: Math.round((outY + inY) / 2) },
            { x: aisleX, y: outY - 14 },
            { x: aisleX, y: inY + 14 },
          ];
        } else {
          const skipOrd = skipEdges.indexOf(e);
          const laneY = maxH - botPad + 12 + skipOrd * 24;
          const exitAisleX = layerRight.get(a.rank) + 16;
          const enterAisleX = layerLeft.get(b.rank) - 18;
          pts = [
            { x: outX, y: outY },
            { x: exitAisleX, y: outY },
            { x: exitAisleX, y: laneY },
            { x: enterAisleX, y: laneY },
            { x: enterAisleX, y: inY },
            { x: inX, y: inY },
          ];
          labelCandidates = [{ x: Math.round((exitAisleX + enterAisleX) / 2), y: laneY }];
        }
      }

      const d = buildRoundedOrthogonalPath(pts, 9);
      const lbl = e.label || '';
      const isNeg =
        lbl.startsWith('-') ||
        lbl.includes('批') ||
        lbl.includes('拒') ||
        lbl.includes('排除') ||
        lbl.includes('切断');
      const stroke = isNeg ? 'var(--cinnabar)' : 'var(--pine)';
      const marker = isNeg ? markerRed : markerGreen;

      svgEdgePaths.push(
        '<path d="' +
          d +
          '" fill="none" stroke="' +
          stroke +
          '" stroke-width="1.7" stroke-dasharray="' +
          (isSkipOrBack ? '5 3' : 'none') +
          '" marker-end="url(#' +
          marker +
          ')" opacity=".9"/>'
      );

      if (lbl) {
        const lsz = measureLabelSize(lbl);
        let chosenRect = null;
        for (const cand of labelCandidates) {
          for (const dy of [0, -14, 14, -26, 26]) {
            const box = {
              x: Math.round(cand.x - lsz.w / 2),
              y: Math.round(cand.y + dy - lsz.h / 2),
              w: lsz.w,
              h: lsz.h,
            };
            if (box.x < 2 || box.x + box.w > maxW - 2 || box.y < 2 || box.y + box.h > maxH - 2) continue;
            if (!nodeRects.some((nr) => rectsOverlap(box, nr, 5)) && !placedLabels.some((lr) => rectsOverlap(box, lr, 4))) {
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
              (isNeg ? 'neg' : 'pos') +
              '" style="left:' +
              chosenRect.x +
              'px;top:' +
              chosenRect.y +
              'px" title="' +
              esc(e.note || lbl) +
              '">' +
              esc(lbl) +
              '</span>'
          );
        } else {
          const badgeNum = fallbackLegend.length + 1;
          const circ = '①②③④⑤⑥⑦⑧⑨⑩'[badgeNum - 1] || String(badgeNum);
          fallbackLegend.push(circ + ' ' + e.from + '→' + e.to + '：' + (e.note || lbl));
          const anchorPt = labelCandidates[0];
          const badgeBox = {
            x: Math.max(4, Math.min(maxW - 24, anchorPt.x - 10)),
            y: Math.max(4, Math.min(maxH - 22, anchorPt.y - 9)),
            w: 20,
            h: 18,
          };
          placedLabels.push(badgeBox);
          htmlEdgeLabels.push(
            '<span class="dag-edge-label ' +
              (isNeg ? 'neg' : 'pos') +
              '" style="left:' +
              badgeBox.x +
              'px;top:' +
              badgeBox.y +
              'px">' +
              circ +
              '</span>'
          );
        }
      }
    });

    const cardsHtml = nodes
      .map((n) => {
        const p = pos.get(n.id);
        const firstBlk = (n.blocks && n.blocks[0]) || '';
        return (
          '<div class="dag-node role-' +
          esc(n.role || 'inference') +
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
          '" title="点击节点跳转原文 ' +
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
            ? '<div class="dag-node-more" data-toggle-detail>▾ 查看展开</div><div class="dag-node-detail">' +
              esc(n.detail) +
              '</div>'
            : '') +
          '<div class="dag-node-foot">' +
          renderAnchorPills(n.blocks) +
          '</div></div>'
        );
      })
      .join('');

    const legendHtml = fallbackLegend.length
      ? '<div class="edge-legend">' +
        fallbackLegend.map((x) => '<span class="edge-legend-pill">' + esc(x) + '</span>').join('') +
        '</div>'
      : '';

    return (
      '<div class="dag-viewport" data-qa-target="dag"><div class="dag-canvas" style="width:' +
      maxW +
      'px;height:' +
      maxH +
      'px"><svg class="dag-edges" width="' +
      maxW +
      '" height="' +
      maxH +
      '"><defs>' +
      '<marker id="' +
      markerGreen +
      '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="var(--pine)"/></marker>' +
      '<marker id="' +
      markerRed +
      '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="var(--cinnabar)"/></marker>' +
      '</defs>' +
      svgEdgePaths.join('') +
      '</svg>' +
      cardsHtml +
      htmlEdgeLabels.join('') +
      '</div></div>' +
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

  function renderTimelineHtml(payload) {
    const lanes = (payload.data && payload.data.lanes) || [];
    const rows = (payload.data && payload.data.rows) || [];
    const cols = '76px ' + lanes.map(() => 'minmax(115px, 1fr)').join(' ');
    const head =
      '<div class="lanes-head" style="grid-template-columns:' +
      cols +
      '"><div class="lane-time-col">时间</div>' +
      lanes.map((l) => '<div>' + esc(l) + '</div>').join('') +
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
            .map((cell) =>
              !cell
                ? '<div class="lane-cell"></div>'
                : '<div class="lane-cell filled"><div style="display:flex;justify-content:space-between;gap:4px;align-items:center"><b>' +
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
    return '<div class="lanes-grid" data-qa-target="grid">' + head + body + '</div>';
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

  function renderExtensionOrFallbackHtml(payload) {
    const items = payload.items || [];
    if (payload.blueprint === 'extension_checklist') {
      return (
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
    return (
      '<div class="ext-list">' +
      items
        .map(
          (it) =>
            '<div class="ext-item">' +
            (it.title || it.label
              ? '<div style="display:flex;justify-content:space-between;gap:6px;align-items:center"><b style="font-size:12.5px;color:var(--pine)">' +
                esc(it.title || it.label) +
                '</b>' +
                renderSrcTag(it.source) +
                '</div>'
              : '') +
            (it.body || it.detail ? '<div class="ext-sub">' + esc(it.body || it.detail) + '</div>' : '') +
            (it.warning ? '<div class="ext-sub" style="color:var(--ochre)">审计：' + esc(it.warning) + '</div>' : '') +
            '<div>' +
            renderAnchorPills(it.blocks) +
            '</div></div>'
        )
        .join('') +
      '</div>'
    );
  }

  function renderBlueprintBody(payload, options) {
    if (!payload || typeof payload !== 'object') return '';
    const bp = payload.blueprint || 'flow_swimlane';
    if (bp === 'parallel_timeline') return renderTimelineHtml(payload);
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
    const nodes = Array.from(stageEl.querySelectorAll('.dag-node'));
    const labels = Array.from(stageEl.querySelectorAll('.dag-edge-label'));

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

  function bindStageInteractions(rootEl, onFocusBlock) {
    if (!rootEl) return;
    rootEl.querySelectorAll('[data-jump]').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target && e.target.hasAttribute('data-toggle-detail')) {
          e.stopPropagation();
          el.classList.toggle('expanded');
          return;
        }
        e.stopPropagation();
        const bid = el.getAttribute('data-jump');
        if (bid && onFocusBlock) onFocusBlock(bid);
      });
    });
  }

  function openZoomModal(figure, onFocusBlock) {
    let modal = document.getElementById('zoom-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'zoom-modal';
      modal.className = 'zoom-modal';
      modal.innerHTML =
        '<div class="zoom-dialog"><div class="zoom-head"><h4 id="zoom-title">宽屏全览</h4>' +
        '<div style="display:flex;gap:8px;align-items:center"><span id="zoom-qa" class="qa-badge">✓ 宽屏质检</span>' +
        '<button type="button" class="wb-btn" id="btn-zoom-close">关闭 Esc</button></div></div>' +
        '<div class="zoom-body" id="zoom-body"></div></div>';
      document.body.appendChild(modal);
      document.getElementById('btn-zoom-close').addEventListener('click', () => modal.classList.remove('open'));
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('open');
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') modal.classList.remove('open');
      });
    }
    document.getElementById('zoom-title').textContent = (figure.caption || '图表全览') + '（宽屏画布 · 点击节点直跳原文）';
    const body = document.getElementById('zoom-body');
    body.innerHTML = renderBlueprintBody(figure, { wide: true });
    modal.classList.add('open');
    const qa = runCollisionAudit(body);
    const qaBadge = document.getElementById('zoom-qa');
    qaBadge.className = 'qa-badge' + (qa.overlaps > 0 ? ' warn' : '');
    qaBadge.textContent = qa.overlaps === 0 ? '✓ 宽屏质检：0 处重叠' : '⚠ 发现 ' + qa.overlaps + ' 处重叠';
    bindStageInteractions(body, onFocusBlock);
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
        bindStageInteractions(stage, onFocusBlock);
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
