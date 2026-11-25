"""Fast deterministic JS layout tests for diagram_renderer.js wide modal & spatial expand-all."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RENDERER_JS = REPO / "apps" / "reading" / "web" / "assets" / "diagram_renderer.js"

NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync(process.argv[2], 'utf8');
const payloadJson = fs.readFileSync(process.argv[3], 'utf8');
const cases = JSON.parse(payloadJson);

const calcCardH = (chunk, cls, isExp) => {
  const pills = (chunk.match(/class="blk-anchor/g) || []).length;
  const detMatch = chunk.match(/detail">([^<]*)</);
  const detLen = isExp && detMatch ? detMatch[1].length : 0;
  const baseH = cls === 'spatial-node' ? 92 : 84;
  const detH = detLen ? Math.ceil(detLen / 11) * 17 + 16 : 0;
  return baseH + Math.ceil(pills / 3) * 22 + detH;
};

const makeStageEl = () => {
  let _html = '';
  let _w = 176;
  return {
    id: 'measure-stage',
    style: { set width(v) { _w = parseInt(v, 10) || 176; }, get width() { return _w + 'px'; } },
    setAttribute() {},
    set innerHTML(v) { _html = String(v || ''); },
    get innerHTML() { return _html; },
    querySelectorAll(sel) {
      if (sel !== '.dag-node' && sel !== '.spatial-node') return [];
      const cls = sel.slice(1);
      const parts = _html.split('<div class="' + cls).slice(1);
      return parts.map((chunk) => {
        const isExp = chunk.split('>')[0].includes('expanded');
        const h = calcCardH(chunk, cls, isExp);
        return { getBoundingClientRect: () => ({ width: _w, height: h }) };
      });
    },
    get firstElementChild() {
      const m = _html.match(/>([^<]*)</);
      const txt = m ? m[1] : '';
      return { getBoundingClientRect: () => ({ width: Math.max(36, txt.length * 11 + 14), height: 20 }) };
    },
  };
};

const stageEl = makeStageEl();
const makeDummyNode = () => ({
  innerHTML: '',
  className: '',
  hidden: false,
  dataset: {},
  style: {},
  classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  setAttribute() {},
  removeAttribute() {},
  getAttribute() { return ''; },
  addEventListener() {},
  removeEventListener() {},
  appendChild() {},
  querySelector() { return makeDummyNode(); },
  querySelectorAll() { return []; },
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
});

const sandbox = {
  window: { RD: {}, CSS: { supports: () => false } },
  document: {
    body: { appendChild() {} },
    getElementById(id) { return id === 'measure-stage' ? stageEl : null; },
    createElement() { return makeDummyNode(); },
  },
  getComputedStyle: () => ({ display: 'none' }),
  console,
  Math,
  Map,
  Set,
  Array,
  Number,
  String,
  Boolean,
  Object,
  RegExp,
  JSON,
};
sandbox.window.window = sandbox.window;
vm.runInNewContext(code, sandbox, { filename: 'diagram_renderer.js' });

// Verify wide layout geometry across figure cases via public renderBlueprintCard inside a zoom-like hook,
// or evaluate renderLayeredDagHtml / renderSpatialMapHtml inside the same IIFE context.
const exposedCode = code.replace(
  'RD.diagrams = {',
  'RD.diagrams = { _renderLayeredDagHtml: renderLayeredDagHtml, _renderSpatialMapHtml: renderSpatialMapHtml,'
);
const sb2 = Object.assign({}, sandbox, { window: { RD: {}, CSS: { supports: () => false } } });
sb2.window.window = sb2.window;
vm.runInNewContext(exposedCode, sb2, { filename: 'diagram_renderer.js' });

const diagrams = sb2.window.RD.diagrams;
const parsePositionedNodes = (html, cls, expandAll) => {
  const parts = html.split('<div class="' + cls).slice(1);
  return parts
    .map((chunk) => {
      const m = chunk.match(/style="left:(\d+)px;top:(\d+)px;width:(\d+)px"/);
      if (!m) return null;
      const isExp = expandAll && chunk.split('>')[0].includes('expanded');
      return { x: +m[1], y: +m[2], w: +m[3], h: calcCardH(chunk, cls, isExp) };
    })
    .filter(Boolean);
};

const countOverlaps = (boxes, pad = 2) => {
  let n = 0;
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (!(a.x + a.w + pad <= b.x || b.x + b.w + pad <= a.x || a.y + a.h + pad <= b.y || b.y + b.h + pad <= a.y)) {
        n++;
      }
    }
  }
  return n;
};

const availW = 1044;
const availH = 580;
const results = {};
for (const [name, fig] of Object.entries(cases)) {
  const fn = fig.blueprint === 'spatial_route' ? diagrams._renderSpatialMapHtml : diagrams._renderLayeredDagHtml;
  const cls = fig.blueprint === 'spatial_route' ? 'spatial-node' : 'dag-node';
  const htmlCollapsed = fn(fig, { wide: true, expandAll: false });
  const htmlExpanded = fn(fig, { wide: true, expandAll: true });
  const dimRe = fig.blueprint === 'spatial_route'
    ? /spatial-canvas" style="width:(\d+)px;height:(\d+)px"/
    : /dag-canvas" style="width:(\d+)px;height:(\d+)px"/;
  const dm1 = htmlCollapsed.match(dimRe);
  const natW1 = dm1 ? +dm1[1] : 0;
  const natH1 = dm1 ? +dm1[2] : 0;
  const fit1 = dm1 ? Math.min(1, availW / (natW1 + 8), availH / (natH1 + 36)) : 1;
  const boxes1 = parsePositionedNodes(htmlCollapsed, cls, false);
  const boxes2 = parsePositionedNodes(htmlExpanded, cls, true);
  results[name] = {
    natW1,
    natH1,
    fitPct1: Math.round(fit1 * 100),
    overlaps1: countOverlaps(boxes1, 1),
    overlaps2: countOverlaps(boxes2, 1),
    moreCount1: (htmlCollapsed.match(/blk-anchor-more/g) || []).length,
    moreCount2: (htmlExpanded.match(/blk-anchor-more/g) || []).length,
    distinctYCount1: new Set(boxes1.map((b) => Math.round(b.y / 20))).size,
    distinctXCount1: new Set(boxes1.map((b) => Math.round(b.x / 20))).size,
  };
}
console.log(JSON.stringify(results));
"""


def _build_deep_causality_figure() -> dict:
    roles = ["structural", "trigger", "mechanism", "turning_point", "consequence"]
    nodes = [
        {
            "id": f"C{i}",
            "role": roles[min(len(roles) - 1, (i - 1) // 2)],
            "label": f"因果节点{i}",
            "detail": f"说明第{i}步的制度诱因与局势推进，用于验证宽屏展开后的排版尺寸与零重叠计算。",
            "blocks": [f"B{b:02d}" for b in range(i, min(i + 7, 35))],
            "source": "原文",
        }
        for i in range(1, 10)
    ]
    edges = [{"from": f"C{i}", "to": f"C{i + 1}", "label": f"推进{i}"} for i in range(1, 9)]
    edges.append({"from": "C3", "to": "C7", "label": "远因制约", "kind": "constraint"})
    return {"blueprint": "historical_causality", "data": {"nodes": nodes, "edges": edges}}


def _build_five_tier_power_figure() -> dict:
    specs = [
        ("P1", "faction", ["B01", "B02", "B03", "B04", "B05", "B06"]),
        ("P2", "ally", ["B04", "B07"]),
        ("P3", "faction", ["B08", "B09", "B10"]),
        ("P4", "sovereign", ["B11", "B12", "B13", "B14", "B15"]),
        ("P5", "constraint", ["B03", "B05", "B16"]),
        ("P6", "faction", ["B17", "B18", "B19", "B20", "B21"]),
        ("P7", "rival", ["B22", "B23", "B24"]),
        ("P8", "ally", ["B25", "B26"]),
        ("P9", "rival", ["B27", "B28", "B29"]),
        ("P10", "faction", ["B30", "B31"]),
        ("P11", "constraint", ["B32", "B33", "B34", "B35", "B36"]),
        ("P12", "constraint", ["B11", "B12", "B31", "B33", "B34"]),
    ]
    nodes = [
        {
            "id": nid,
            "role": role,
            "label": f"阵营节点{nid}",
            "detail": f"势力{nid}盟约义务。",
            "blocks": blks,
            "source": "原文",
        }
        for nid, role, blks in specs
    ]
    edges = [
        {"from": "P1", "to": "P3", "label": "同盟", "relation": "alliance"},
        {"from": "P2", "to": "P4", "label": "制衡", "relation": "hostile"},
        {"from": "P3", "to": "P6", "label": "统治", "relation": "command"},
        {"from": "P4", "to": "P7", "label": "主攻", "relation": "hostile"},
        {"from": "P5", "to": "P8", "label": "中立", "relation": "constraint"},
        {"from": "P6", "to": "P9", "label": "施压", "relation": "hostile"},
        {"from": "P7", "to": "P12", "label": "抗衡", "relation": "hostile"},
        {"from": "P8", "to": "P10", "label": "改组", "relation": "constraint"},
        {"from": "P9", "to": "P12", "label": "实施", "relation": "constraint"},
    ]
    return {"blueprint": "power_hierarchy", "data": {"nodes": nodes, "edges": edges}}


def _build_wide_fan_power_figure() -> dict:
    nodes = [
        {
            "id": f"F{i}",
            "role": "faction",
            "label": f"各方势力{i}",
            "detail": "制度或同盟约束说明。",
            "blocks": ["B01", "B02", "B03", "B04", "B05"],
            "source": "原文",
        }
        for i in range(1, 9)
    ] + [
        {
            "id": "M1",
            "role": "sovereign",
            "label": "总参谋部",
            "detail": "核心统筹。",
            "blocks": ["B06", "B07"],
            "source": "原文",
        },
        {"id": "T1", "role": "rival", "label": "比利时", "detail": "中立防线。", "blocks": ["B08"], "source": "原文"},
        {"id": "T2", "role": "rival", "label": "法国", "detail": "主攻目标。", "blocks": ["B09"], "source": "原文"},
    ]
    edges = [{"from": f"F{i}", "to": "M1", "label": f"约束{i}", "relation": "constraint"} for i in range(1, 9)] + [
        {"from": "M1", "to": "T1", "label": "借道", "relation": "hostile"},
        {"from": "M1", "to": "T2", "label": "主攻", "relation": "hostile"},
    ]
    return {"blueprint": "power_hierarchy", "data": {"nodes": nodes, "edges": edges}}


def _build_dense_spatial_figure() -> dict:
    coords = [
        (12, 26),
        (24, 28),
        (33, 30),
        (42, 34),
        (51, 32),
        (60, 34),
        (72, 28),
        (86, 22),
        (46, 56),
        (28, 72),
        (64, 62),
        (78, 74),
    ]
    nodes = [
        {
            "id": f"S{i}",
            "role": "corridor" if i % 2 else "stronghold",
            "label": f"战区节点{i}",
            "zone": "西线走廊",
            "x": x,
            "y": y,
            "detail": f"第{i}处战区承担主力翼侧包抄或要塞封锁任务，展开说明后需要画布重排以保持零重叠。",
            "blocks": [f"B{10 + i:02d}", f"B{20 + i:02d}", f"B{30 + i:02d}"],
            "source": "原文",
        }
        for i, (x, y) in enumerate(coords, 1)
    ]
    edges = [{"from": f"S{i}", "to": f"S{i + 1}", "kind": "advance", "label": f"推进{i}"} for i in range(1, 12)]
    return {"blueprint": "spatial_route", "data": {"mode": "campaign", "nodes": nodes, "edges": edges}}


def test_diagram_renderer_js_syntax() -> None:
    res = subprocess.run(["node", "--check", str(RENDERER_JS)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def test_wide_dag_aspect_and_spatial_expand_zero_overlap(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    harness_path = tmp_path / "harness.js"
    cases = {
        "causality": _build_deep_causality_figure(),
        "power": _build_five_tier_power_figure(),
        "power_fan": _build_wide_fan_power_figure(),
        "spatial": _build_dense_spatial_figure(),
    }
    cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    harness_path.write_text(NODE_HARNESS, encoding="utf-8")

    res = subprocess.run(
        ["node", str(harness_path), str(RENDERER_JS), str(cases_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    assert out["causality"]["overlaps1"] == 0
    assert out["causality"]["overlaps2"] == 0
    assert out["causality"]["fitPct1"] >= 65, out["causality"]
    assert out["causality"]["distinctXCount1"] >= 4
    assert out["causality"]["moreCount1"] > 0
    assert out["causality"]["moreCount2"] == 0

    assert out["power"]["overlaps1"] == 0
    assert out["power"]["overlaps2"] == 0
    assert out["power"]["fitPct1"] >= 65, out["power"]
    assert out["power"]["distinctXCount1"] >= 5
    assert out["power"]["moreCount1"] > 0
    assert out["power"]["moreCount2"] == 0

    assert out["power_fan"]["overlaps1"] == 0
    assert out["power_fan"]["overlaps2"] == 0
    assert out["power_fan"]["fitPct1"] >= 60, out["power_fan"]

    assert out["spatial"]["overlaps1"] == 0
    assert out["spatial"]["overlaps2"] == 0, out["spatial"]
    assert out["spatial"]["fitPct1"] >= 65, out["spatial"]
    assert re.search(r"manualZoom", RENDERER_JS.read_text(encoding="utf-8"))
