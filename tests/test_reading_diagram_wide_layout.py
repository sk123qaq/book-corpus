"""Fast deterministic JS layout tests for diagram_renderer wide modal, spatial & philosophy 5-tool layouts."""

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

// Evaluate renderLayeredDagHtml / renderSpatialMapHtml inside the same IIFE context.
const exposedCode = code.replace(
  'RD.diagrams = {',
  'RD.diagrams = { _renderLayeredDagHtml: renderLayeredDagHtml, _renderSpatialMapHtml: renderSpatialMapHtml,'
);
vm.runInNewContext(exposedCode, sandbox, { filename: 'diagram_renderer.js' });

const diagrams = sandbox.window.RD.diagrams;
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


PHILOSOPHY_NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync(process.argv[2], 'utf8');
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

const calcCardH = (chunk, isExp) => {
  const pills = (chunk.match(/class="blk-anchor(?:"|\s)/g) || []).length;
  const detMatch = chunk.match(/detail">([^<]*)</);
  const detLen = isExp && detMatch ? detMatch[1].length : 0;
  const detH = detLen ? Math.ceil(detLen / 11) * 17 + 16 : 0;
  return 84 + Math.ceil(Math.max(1, pills) / 3) * 22 + detH;
};

const makeStageEl = () => {
  let _html = '';
  let _w = 186;
  return {
    id: 'measure-stage',
    style: { set width(v) { _w = parseInt(v, 10) || 186; }, get width() { return _w + 'px'; } },
    setAttribute() {},
    set innerHTML(v) { _html = String(v || ''); },
    get innerHTML() { return _html; },
    querySelectorAll(sel) {
      if (sel !== '.dag-node') return [];
      const parts = _html.split('<div class="dag-node').slice(1);
      return parts.map((chunk) => {
        const isExp = chunk.split('>')[0].includes('expanded');
        return { getBoundingClientRect: () => ({ width: _w, height: calcCardH(chunk, isExp) }) };
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

const exposedCode = code.replace(
  'RD.diagrams = {',
  'RD.diagrams = { _renderBlueprintBody: renderBlueprintBody,'
);
const sb = {
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
sb.window.window = sb.window;
vm.runInNewContext(exposedCode, sb, { filename: 'diagram_renderer.js' });
const renderBody = sb.window.RD.diagrams._renderBlueprintBody;

const parseNodes = (html, expandAll) => {
  const parts = html.split('<div class="dag-node').slice(1);
  return parts
    .map((chunk) => {
      const m = chunk.match(/style="left:(\d+)px;top:(\d+)px;width:(\d+)px"/);
      if (!m) return null;
      const isExp = expandAll && chunk.split('>')[0].includes('expanded');
      return { x: +m[1], y: +m[2], w: +m[3], h: calcCardH(chunk, isExp) };
    })
    .filter(Boolean);
};

const overlaps = (a, b) => !(
  a.x + a.w + 1 <= b.x || b.x + b.w + 1 <= a.x || a.y + a.h + 1 <= b.y || b.y + b.h + 1 <= a.y
);

const out = {};
for (const c of cases) {
  const html = renderBody(c.figure, c.options || {});
  const nodes = parseNodes(html, Boolean(c.options && c.options.expandAll));
  let nodeOverlaps = 0;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      if (overlaps(nodes[i], nodes[j])) nodeOverlaps++;
    }
  }
  const cm = html.match(/class="dag-canvas" style="width:(\d+)px;height:(\d+)px"/);
  const natW = cm ? +cm[1] : 0;
  const natH = cm ? +cm[2] : 0;
  const fitScale = natW && natH ? Math.min(1, 960 / natW, 680 / natH) : 1;
  out[c.name] = {
    html,
    nodeCount: nodes.length,
    nodeOverlaps,
    natW,
    natH,
    fitScale: Number(fitScale.toFixed(3)),
    fallbackPills: (html.match(/class="edge-legend-pill"/g) || []).length,
  };
}
console.log(JSON.stringify(out));
"""


def _run_harness(tmp_path: Path, cases: list[dict]) -> dict:
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(PHILOSOPHY_NODE_HARNESS, encoding="utf-8")
    payload_path = tmp_path / "cases.json"
    payload_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness_path), str(RENDERER_JS), str(payload_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_philosophy_wide_layout_and_narrow_views_regression(tmp_path: Path) -> None:
    deep_arg = {
        "blueprint": "argument_map",
        "caption": "七层哲学论证链",
        "reading_note": "测试深层论证折行",
        "data": {
            "nodes": [
                {
                    "id": f"A{i}",
                    "role": "premise"
                    if i < 3
                    else ("rebuttal" if i == 5 else ("conclusion" if i == 7 else "inference")),
                    "label": f"论证节点{i}",
                    "detail": "详细展开说明论证前提与推演步骤之间的联系。",
                    "blocks": ["B01", "B02", "B03", "B04", "B05"] if i == 1 else ["B01"],
                    "source": "原文",
                }
                for i in range(1, 8)
            ],
            "edges": [{"from": f"A{i}", "to": f"A{i + 1}", "label": "推导"} for i in range(1, 7)],
        },
    }
    lineage_fig = {
        "blueprint": "lineage_dag",
        "caption": "承认谱系图",
        "reading_note": "围绕作者立场汇聚",
        "data": {
            "mode": "lineage",
            "nodes": [
                {"id": "L1", "role": "predecessor", "label": "黑格尔", "detail": "主奴辩证法", "blocks": ["B01"]},
                {"id": "L2", "role": "predecessor", "label": "卢梭", "detail": "形而上自由", "blocks": ["B21"]},
                {"id": "L3", "role": "mediator", "label": "科耶夫", "detail": "历史终结论", "blocks": ["B04"]},
                {"id": "L4", "role": "ally", "label": "尼采", "detail": "最后的人对照", "blocks": ["B13"]},
                {"id": "L5", "role": "author", "label": "福山作者立场", "detail": "为承认而斗争", "blocks": ["B05"]},
                {"id": "L6", "role": "target", "label": "霍布斯", "detail": "自我保存优先", "blocks": ["B08"]},
                {"id": "L7", "role": "target", "label": "洛克", "detail": "契约权利观", "blocks": ["B08"]},
            ],
            "edges": [
                {"from": "L1", "to": "L3", "relation": "transform", "label": "阐释"},
                {"from": "L2", "to": "L5", "relation": "inherit", "label": "继承"},
                {"from": "L3", "to": "L5", "relation": "inherit", "label": "吸收"},
                {"from": "L4", "to": "L5", "relation": "contrast", "label": "对照"},
                {"from": "L5", "to": "L6", "relation": "critique", "label": "批驳"},
                {"from": "L5", "to": "L7", "relation": "critique", "label": "批判"},
            ],
        },
    }
    term_fig = {
        "blueprint": "term_contrast",
        "caption": "术语辨析",
        "data": {
            "columns": ["核心术语", "日常汉语直觉", "作者专属定义", "出处"],
            "rows": [
                {
                    "key": "公民",
                    "original_term": "Bourgeois",
                    "everyday": "普通市民",
                    "author": "仅顾自保的现代契约个体",
                    "blocks": ["B08", "B16"],
                    "source": "原文",
                }
            ],
        },
    }
    na_lineage = {
        "blueprint": "lineage_dag",
        "caption": "内部对话",
        "data": {"mode": "not_applicable", "reason": "本章无外部学派引用。", "nodes": [], "edges": []},
    }
    ext_fig = {
        "blueprint": "extension_dossier",
        "caption": "反例实验室",
        "sections": [
            {
                "id": "hidden_premise",
                "title": "隐含未言明前提",
                "items": [
                    {"title": "前提一", "stance": "隐含前提", "body": "内容", "blocks": ["B01"], "source": "原文"}
                ],
            },
            {
                "id": "counterexample",
                "title": "哲学反例与极端压力测试",
                "items": [
                    {"title": "反例一", "stance": "极端反例", "body": "内容", "blocks": ["B02"], "source": "补充"}
                ],
            },
        ],
        "items": [],
    }

    stacked_lineage = {
        "blueprint": "lineage_dag",
        "caption": "高频同调谱系",
        "data": {
            "nodes": [
                {"id": "S0", "role": "predecessor", "label": "亚里士多德", "detail": "德性与幸福", "blocks": ["B01"]},
                *[
                    {
                        "id": f"S{i}",
                        "role": "ally",
                        "label": f"同调学者{i}",
                        "detail": "佐证内在优先",
                        "blocks": [f"B0{i + 1}"],
                    }
                    for i in range(1, 8)
                ],
                {"id": "SA", "role": "author", "label": "叔本华", "detail": "幸福根于自身", "blocks": ["B02"]},
                {"id": "ST", "role": "target", "label": "世俗外物观", "detail": "重外轻内", "blocks": ["B25"]},
            ],
            "edges": [
                *[{"from": f"S{i}", "to": "SA", "relation": "inherit", "label": "佐证"} for i in range(0, 8)],
                {"from": "SA", "to": "ST", "relation": "critique", "label": "批驳"},
            ],
        },
    }
    cases = [
        {"name": "arg_wide_collapsed", "figure": deep_arg, "options": {"wide": True, "expandAll": False}},
        {"name": "arg_wide_expanded", "figure": deep_arg, "options": {"wide": True, "expandAll": True}},
        {"name": "lineage_wide", "figure": lineage_fig, "options": {"wide": True}},
        {"name": "lineage_stacked_wide", "figure": stacked_lineage, "options": {"wide": True}},
        {"name": "lineage_narrow", "figure": lineage_fig, "options": {"wide": False}},
        {"name": "lineage_na", "figure": na_lineage, "options": {"wide": False}},
        {"name": "term_narrow", "figure": term_fig, "options": {"wide": False}},
        {"name": "term_wide", "figure": term_fig, "options": {"wide": True}},
        {"name": "ext_sections", "figure": ext_fig, "options": {"wide": False}},
    ]
    res = _run_harness(tmp_path, cases)

    assert res["arg_wide_collapsed"]["nodeOverlaps"] == 0
    assert res["arg_wide_collapsed"]["fitScale"] >= 0.65
    assert "data-expand-anchors" in res["arg_wide_collapsed"]["html"]
    assert res["arg_wide_expanded"]["nodeOverlaps"] == 0
    assert res["arg_wide_expanded"]["fitScale"] >= 0.65

    assert res["lineage_wide"]["nodeOverlaps"] == 0
    assert res["lineage_wide"]["fallbackPills"] == 0
    assert res["lineage_wide"]["fitScale"] >= 0.70
    assert 'stroke-dasharray="6 3"' in res["lineage_wide"]["html"]
    assert "lineage-legend" in res["lineage_wide"]["html"]

    assert res["lineage_stacked_wide"]["nodeOverlaps"] == 0
    assert res["lineage_stacked_wide"]["fitScale"] >= 0.70

    assert "data-lineage-filter" in res["lineage_narrow"]["html"]
    assert "全览图谱" in res["lineage_narrow"]["html"]
    assert "lineage-na" in res["lineage_na"]["html"]

    assert "term-contrast-cards" in res["term_narrow"]["html"]
    assert "Bourgeois" in res["term_narrow"]["html"]
    assert "term-contrast-table" in res["term_wide"]["html"]
    assert "tc-orig-table" in res["term_wide"]["html"]

    assert 'data-section-id="hidden_premise"' in res["ext_sections"]["html"]
    assert 'data-section-id="counterexample"' in res["ext_sections"]["html"]


def test_practical_wide_layout_and_narrow_views_regression(tmp_path: Path) -> None:
    flow_fig = {
        "blueprint": "flow_swimlane",
        "caption": "数据清洗与缺失值处理 SOP",
        "reading_note": "先探查缺失机制再选填补策略",
        "data": {
            "mode": "flow",
            "nodes": [
                {
                    "id": "F1",
                    "role": "input",
                    "label": "导入原始数据集",
                    "detail": "读入 CSV 并核验字段类型与样本量。",
                    "blocks": ["B01", "B02", "B03", "B04"],
                },
                {
                    "id": "F2",
                    "role": "action",
                    "label": "统计各列缺失率",
                    "detail": "计算缺失占比与分布位置。",
                    "blocks": ["B05"],
                },
                {
                    "id": "F3",
                    "role": "gate",
                    "label": "判断缺失机制",
                    "detail": "区分完全随机缺失与非随机缺失。",
                    "blocks": ["B08"],
                },
                {
                    "id": "F4",
                    "role": "action",
                    "label": "分类执行填充",
                    "detail": "低缺失用均值/中位数或插值填充。",
                    "blocks": ["B12"],
                },
                {
                    "id": "F5",
                    "role": "pitfall",
                    "label": "警惕直接 dropna",
                    "detail": "盲目丢弃含缺失行会严重扭曲样本分布。",
                    "blocks": ["B15"],
                },
                {
                    "id": "F6",
                    "role": "action",
                    "label": "清理重复与异常值",
                    "detail": "按业务规则去重并处理离群点。",
                    "blocks": ["B20"],
                },
                {
                    "id": "F7",
                    "role": "gate",
                    "label": "交叉核验分布",
                    "detail": "对比清洗前后的均值与方差偏移。",
                    "blocks": ["B25"],
                },
                {
                    "id": "F8",
                    "role": "output",
                    "label": "输出可用特征表",
                    "detail": "固化清洗脚本并留存审计日志。",
                    "blocks": ["B30"],
                },
            ],
            "edges": [
                {"from": "F1", "to": "F2", "label": "统计缺失"},
                {"from": "F2", "to": "F3", "label": "判定机制"},
                {"from": "F3", "to": "F4", "label": "随机缺失"},
                {"from": "F3", "to": "F5", "label": "勿直接删"},
                {"from": "F4", "to": "F6", "label": "进入去重"},
                {"from": "F6", "to": "F7", "label": "分布校验"},
                {"from": "F7", "to": "F8", "label": "达标交付"},
            ],
        },
    }
    flow_na = {
        "blueprint": "flow_swimlane",
        "caption": "纯原则讨论",
        "data": {"mode": "not_applicable", "not_applicable_reason": "本章只讨论心法原则。", "nodes": [], "edges": []},
    }
    decision_fig = {
        "blueprint": "decision_tree",
        "caption": "破坏性技术应对决策树",
        "reading_note": "先判断技术性质与市场规模匹配度",
        "data": {
            "nodes": [
                {
                    "id": "D1",
                    "role": "diagnosis",
                    "label": "遭遇新兴技术冲击",
                    "detail": "判断技术属于延续性还是破坏性。",
                    "blocks": ["B01"],
                },
                {
                    "id": "D2",
                    "role": "condition",
                    "label": "当前主流客户是否买单",
                    "detail": "核查主流客户早期性能需求。",
                    "blocks": ["B05", "B06", "B07", "B08"],
                },
                {
                    "id": "D3",
                    "role": "strategy",
                    "label": "并入主业常规迭代",
                    "detail": "按既有价值网络持续升级性能。",
                    "blocks": ["B10"],
                },
                {
                    "id": "D4",
                    "role": "condition",
                    "label": "新市场规模是否够大",
                    "detail": "评估小市场能否支撑母公司增速。",
                    "blocks": ["B14"],
                },
                {
                    "id": "D5",
                    "role": "strategy",
                    "label": "设立独立小型机构",
                    "detail": "匹配小市场利润率与独立资源。",
                    "blocks": ["B18"],
                },
                {
                    "id": "D6",
                    "role": "fallback",
                    "label": "严禁塞回大部门考核",
                    "detail": "大部门考核会扼杀早期破坏性项目。",
                    "blocks": ["B22"],
                },
            ],
            "edges": [
                {"from": "D1", "to": "D2", "label": "评估需求"},
                {"from": "D2", "to": "D3", "label": "主流需要"},
                {"from": "D2", "to": "D4", "label": "暂不买单"},
                {"from": "D4", "to": "D5", "label": "独立孵化"},
                {"from": "D4", "to": "D6", "label": "强留主部"},
            ],
        },
    }
    quad_fig = {
        "blueprint": "quadrant_matrix",
        "caption": "体验设计杠杆四象限",
        "data": {
            "xAxis": "开发实现成本（低 → 高）",
            "yAxis": "玩家直觉响应（低 → 高）",
            "quadrants": [
                {
                    "pos": "q-top-left",
                    "title": "低成本高响应",
                    "action_hint": "优先立即落地",
                    "items": [{"text": "利用既定常识线索引导动作", "blocks": ["B01", "B02", "B03", "B04"]}],
                },
                {
                    "pos": "q-top-right",
                    "title": "高投入高回报",
                    "action_hint": "聚焦核心关卡打磨",
                    "items": [{"text": "打磨手眼同步的交互反馈", "blocks": ["B10"]}],
                },
                {
                    "pos": "q-bot-left",
                    "title": "低成本低回报",
                    "action_hint": "按需精简保留",
                    "items": [{"text": "补充基础操作文字提示", "blocks": ["B15"]}],
                },
                {
                    "pos": "q-bot-right",
                    "title": "高成本低响应",
                    "action_hint": "坚决舍弃减负",
                    "items": [{"text": "堆砌长篇开场说明书教程", "blocks": ["B20"]}],
                },
            ],
        },
    }
    chk_sections_fig = {
        "blueprint": "extension_checklist",
        "caption": "三段自检清单",
        "sections": [
            {
                "id": "before",
                "title": "启动前准备",
                "items": [
                    {
                        "text": "确认数据源",
                        "detail": "核对口径",
                        "checked": False,
                        "blocks": ["B01", "B02", "B03", "B04"],
                    }
                ],
            },
            {
                "id": "during",
                "title": "执行与核验",
                "items": [{"text": "抽样回查", "detail": "核对分布", "checked": False, "blocks": ["B05"]}],
            },
            {
                "id": "redline",
                "title": "失效边界与踩坑红线",
                "items": [{"text": "禁止全量丢弃", "detail": "防止偏差", "checked": False, "blocks": ["B09"]}],
            },
        ],
        "items": [],
    }
    chk_legacy_fig = {
        "blueprint": "extension_checklist",
        "caption": "旧版扁平清单",
        "items": [{"text": "老条文", "detail": "无分区", "checked": True, "blocks": ["B01"]}],
    }
    pm_fig = {
        "blueprint": "extension_dossier",
        "caption": "失效复盘卡",
        "items": [
            {
                "title": "希捷错失 3.5 英寸市场",
                "pitfall": "用主流大客户的利润指标否决低端新产品",
                "case": "技术已成型却被内部流程搁置，错过桌面计算机浪潮",
                "root_cause": "价值网络与财务考核机制锁定现有高毛利市场",
                "fix": "拆出独立子公司，单独考核小市场盈利与迭代",
                "blocks": ["B01", "B02", "B03", "B04"],
                "source": "原文",
            }
        ],
    }

    cases = [
        {"name": "flow_wide_collapsed", "figure": flow_fig, "options": {"wide": True, "expandAll": False}},
        {"name": "flow_wide_expanded", "figure": flow_fig, "options": {"wide": True, "expandAll": True}},
        {"name": "flow_narrow", "figure": flow_fig, "options": {"wide": False}},
        {"name": "flow_na", "figure": flow_na, "options": {"wide": False}},
        {"name": "decision_wide_collapsed", "figure": decision_fig, "options": {"wide": True, "expandAll": False}},
        {"name": "decision_wide_expanded", "figure": decision_fig, "options": {"wide": True, "expandAll": True}},
        {"name": "decision_narrow", "figure": decision_fig, "options": {"wide": False}},
        {"name": "quad_wide", "figure": quad_fig, "options": {"wide": True}},
        {"name": "quad_narrow", "figure": quad_fig, "options": {"wide": False}},
        {"name": "chk_sections", "figure": chk_sections_fig, "options": {"wide": False}},
        {"name": "chk_legacy", "figure": chk_legacy_fig, "options": {"wide": False}},
        {"name": "postmortem", "figure": pm_fig, "options": {"wide": False}},
    ]
    res = _run_harness(tmp_path, cases)

    assert res["flow_wide_collapsed"]["nodeOverlaps"] == 0
    assert res["flow_wide_collapsed"]["fitScale"] >= 0.70
    assert res["flow_wide_collapsed"]["fallbackPills"] == 0
    assert "前置输入" in res["flow_wide_collapsed"]["html"]
    assert "检查门禁" in res["flow_wide_collapsed"]["html"]
    assert "避坑警报" in res["flow_wide_collapsed"]["html"]
    assert "阶段产出" in res["flow_wide_collapsed"]["html"]
    assert "data-expand-anchors" in res["flow_wide_collapsed"]["html"]
    assert res["flow_wide_expanded"]["nodeOverlaps"] == 0
    assert res["flow_wide_expanded"]["fitScale"] >= 0.65

    assert "sop-narrow-shell" in res["flow_narrow"]["html"]
    assert "sop-step-num" in res["flow_narrow"]["html"]
    assert "全览图谱" in res["flow_narrow"]["html"]
    assert 'data-qa-target="flow-na"' in res["flow_na"]["html"]

    assert res["decision_wide_collapsed"]["nodeOverlaps"] == 0
    assert res["decision_wide_collapsed"]["fitScale"] >= 0.70
    assert res["decision_wide_collapsed"]["fallbackPills"] == 0
    assert "场景诊断" in res["decision_wide_collapsed"]["html"]
    assert "判断条件" in res["decision_wide_collapsed"]["html"]
    assert "推荐动作" in res["decision_wide_collapsed"]["html"]
    assert "止损/替代" in res["decision_wide_collapsed"]["html"]
    assert res["decision_wide_expanded"]["nodeOverlaps"] == 0
    assert res["decision_wide_expanded"]["fitScale"] >= 0.65

    assert "decision-narrow-shell" in res["decision_narrow"]["html"]
    assert "data-decision-filter" in res["decision_narrow"]["html"]
    assert "全览图谱" in res["decision_narrow"]["html"]

    assert "quad-grid-shell" in res["quad_wide"]["html"]
    assert "quad-axis-y-label" in res["quad_wide"]["html"]
    assert "优先立即落地" in res["quad_wide"]["html"]
    assert "data-expand-anchors" in res["quad_wide"]["html"]

    assert "quad-narrow-shell" in res["quad_narrow"]["html"]
    assert "quad-coord-head" in res["quad_narrow"]["html"]
    assert "data-quad-filter" in res["quad_narrow"]["html"]
    assert "高纵 · 低横" in res["quad_narrow"]["html"]

    assert 'data-section-id="before"' in res["chk_sections"]["html"]
    assert 'data-section-id="during"' in res["chk_sections"]["html"]
    assert 'data-section-id="redline"' in res["chk_sections"]["html"]
    assert 'type="checkbox"' in res["chk_sections"]["html"]
    assert "data-expand-anchors" in res["chk_sections"]["html"]
    assert 'type="checkbox"' in res["chk_legacy"]["html"]
    assert "ext-sections" not in res["chk_legacy"]["html"]

    assert "ext-postmortem-item" in res["postmortem"]["html"]
    assert "pm-pitfall" in res["postmortem"]["html"]
    assert "pm-case" in res["postmortem"]["html"]
    assert "pm-cause" in res["postmortem"]["html"]
    assert "pm-fix" in res["postmortem"]["html"]
    assert "data-expand-anchors" in res["postmortem"]["html"]


def test_science_wide_layout_and_narrow_views_regression(tmp_path: Path) -> None:
    deep_deriv = {
        "blueprint": "derivation_dag",
        "caption": "十层广义相对论推导树",
        "reading_note": "多前提汇流至奇点定理",
        "data": {
            "paradigm": "deductive",
            "nodes": [
                {
                    "id": "M1",
                    "role": "axiom",
                    "label": "爱因斯坦场方程",
                    "detail": "度规张量与能动张量耦合决定时空曲率。",
                    "blocks": ["B01", "B02", "B03", "B04", "B05"],
                    "source": "原文",
                },
                {
                    "id": "M2",
                    "role": "condition",
                    "label": "强能量条件成立",
                    "detail": "物质能量密度对光线始终表现为聚焦吸引。",
                    "blocks": ["B06"],
                    "source": "原文",
                },
                {
                    "id": "M3",
                    "role": "lemma",
                    "label": "雷查德胡里聚焦",
                    "detail": "类时测地线族膨胀率单调下降。",
                    "blocks": ["B08"],
                    "source": "原文",
                },
                {
                    "id": "M4",
                    "role": "lemma",
                    "label": "共轭点必然出现",
                    "detail": "测地线族在有限仿射参数内相交。",
                    "blocks": ["B10"],
                    "source": "原文",
                },
                {
                    "id": "M5",
                    "role": "lemma",
                    "label": "闭陷获面存在",
                    "detail": "内外向光束截面同时收缩。",
                    "blocks": ["B12"],
                    "source": "原文",
                },
                {
                    "id": "M6",
                    "role": "condition",
                    "label": "因果可测无闭线",
                    "detail": "排除违背因果律的时间闭合路径。",
                    "blocks": ["B14"],
                    "source": "原文",
                },
                {
                    "id": "M7",
                    "role": "lemma",
                    "label": "最大测地线长度",
                    "detail": "非完备曲线无法无限延伸。",
                    "blocks": ["B16"],
                    "source": "原文",
                },
                {
                    "id": "M8",
                    "role": "theorem",
                    "label": "彭罗斯-霍金奇点定理",
                    "detail": "时空必然包含类时或类光测地线不完备点。",
                    "blocks": ["B18"],
                    "source": "原文",
                },
            ],
            "edges": [
                {"from": "M1", "to": "M3", "label": "决定"},
                {"from": "M2", "to": "M3", "label": "保证"},
                {"from": "M3", "to": "M4", "label": "推出"},
                {"from": "M4", "to": "M5", "label": "蕴含"},
                {"from": "M5", "to": "M7", "label": "触发"},
                {"from": "M6", "to": "M7", "label": "限定"},
                {"from": "M7", "to": "M8", "label": "证得"},
            ],
        },
    }

    transistor_flow = {
        "blueprint": "system_flow",
        "caption": "点接触晶体管工作机制",
        "reading_note": "观察弹簧压紧金箔与空穴注入放大",
        "data": {
            "mode": "flow",
            "nodes": [
                {
                    "id": "C1",
                    "role": "input_energy",
                    "label": "微弱输入电流",
                    "detail": "来自语音电信号。",
                    "blocks": ["B01"],
                },
                {
                    "id": "C2",
                    "role": "component",
                    "label": "塑胶三角劈与金箔",
                    "detail": "刮开极窄刀缝。",
                    "blocks": ["B04"],
                },
                {
                    "id": "C3",
                    "role": "bottleneck",
                    "label": "缝隙≤0.05mm",
                    "detail": "超出则无法放大。",
                    "blocks": ["B05", "B06", "B07", "B08"],
                },
                {
                    "id": "C4",
                    "role": "component",
                    "label": "弹簧压紧N型锗",
                    "detail": "形成发射极与集电极。",
                    "blocks": ["B09"],
                },
                {
                    "id": "C5",
                    "role": "process",
                    "label": "发射极注入空穴",
                    "detail": "改变锗表面电导率。",
                    "blocks": ["B10"],
                },
                {
                    "id": "C6",
                    "role": "process",
                    "label": "调制集电极电流",
                    "detail": "载流子被反向偏压扫出。",
                    "blocks": ["B11"],
                },
                {
                    "id": "C7",
                    "role": "process",
                    "label": "跨区耦合增益",
                    "detail": "输入小变化引出大波动。",
                    "blocks": ["B12"],
                },
                {
                    "id": "C8",
                    "role": "output_effect",
                    "label": "功率放大4.5倍",
                    "detail": "首次验证固态晶体管放大。",
                    "blocks": ["B13"],
                },
            ],
            "edges": [
                {"from": "C1", "to": "C2", "label": "接入"},
                {"from": "C2", "to": "C3", "label": "受限"},
                {"from": "C3", "to": "C4", "label": "压接"},
                {"from": "C4", "to": "C5", "label": "激发"},
                {"from": "C5", "to": "C6", "label": "驱动"},
                {"from": "C6", "to": "C7", "label": "形成"},
                {"from": "C7", "to": "C8", "label": "输出"},
            ],
        },
    }

    sys_na = {
        "blueprint": "system_flow",
        "caption": "纯几何推演",
        "data": {"mode": "not_applicable", "not_applicable_reason": "本章无实体装置或动态演化机制。", "nodes": []},
    }

    sens_regime = {
        "blueprint": "sensitivity_chart",
        "caption": "电极间距与功率放大临界",
        "data": {
            "mode": "regime_threshold",
            "parameter": "金箔刀缝电极间距 d (mm)",
            "regimes": [
                {
                    "range": "≤ 0.05 mm",
                    "threshold": "",
                    "state": "功率放大 4.5×",
                    "mechanism": "空穴未复合即抵达集电极，形成强电流调制",
                    "tone": "stable",
                    "blocks": ["B05", "B06", "B07", "B08"],
                    "source": "原文",
                },
                {
                    "range": "> 0.05 mm",
                    "threshold": "0.05 mm 临界间距",
                    "state": "放大效应失效",
                    "mechanism": "空穴途中复合殆尽，无法有效调制输出",
                    "tone": "breakdown",
                    "blocks": ["B09"],
                    "source": "原文",
                },
            ],
        },
    }

    sens_num = {
        "blueprint": "sensitivity_chart",
        "caption": "温标与增益曲线",
        "data": {
            "mode": "numeric_curve",
            "xLabel": "工作温度 T",
            "yLabel": "增益系数",
            "unit": "×",
            "points": [
                {"x": "-40℃", "y": -0.5, "note": "低温反相", "blocks": ["B01"], "source": "原文"},
                {"x": "25℃", "y": 4.5, "note": "常温放大", "blocks": ["B05"], "source": "原文"},
                {"x": "300℃", "y": 150.0, "note": "击穿跃迁", "blocks": ["B09"], "source": "原文"},
            ],
        },
    }

    sens_na = {
        "blueprint": "sensitivity_chart",
        "caption": "纯公理推演",
        "data": {
            "mode": "not_applicable",
            "not_applicable_reason": "本章无连续定量参数变化曲线。",
            "highlights": [{"title": "强能量条件", "detail": "测地线必聚焦", "blocks": ["B01"], "source": "原文"}],
        },
    }

    boundary_fig = {
        "blueprint": "extension_dossier",
        "caption": "相对论黑洞边界",
        "sections": [
            {
                "id": "paradigm_shift",
                "title": "旧认知推翻与范式演进",
                "items": [
                    {
                        "title": "打破稳态恒星假设",
                        "body": "大质量恒星必然坍缩",
                        "blocks": ["B01", "B02", "B03", "B04"],
                        "source": "原文",
                    }
                ],
            },
            {
                "id": "intuition_analogy",
                "title": "直观类比与物理直觉",
                "items": [{"title": "红移钟慢", "body": "远方观测者看信号停滞", "blocks": ["B05"], "source": "原文"}],
            },
            {
                "id": "breakdown_boundary",
                "title": "极端失效条件与未解边界",
                "items": [{"title": "奇点处方程失效", "body": "需量子引力理论", "blocks": ["B08"], "source": "补充"}],
            },
        ],
        "items": [],
    }

    glossary_fig = {
        "blueprint": "extension_glossary",
        "caption": "黑洞与奇点词典",
        "items": [
            {
                "term": "钱德拉塞卡极限",
                "symbol": "M ≈ 1.44 M☉",
                "intuition": "白矮星不被压垮的质量上限",
                "definition": "电子简并压所能抵抗引力的最大恒星残骸质量。",
                "blocks": ["B01", "B02", "B03", "B04"],
                "source": "原文",
            },
            {
                "term": "事件视界",
                "symbol": "",
                "intuition": "有去无回的时空单向膜",
                "definition": "逃逸速度达到光速的边界曲面。",
                "blocks": ["B05"],
                "source": "原文",
            },
        ],
    }

    cases = [
        {"name": "deriv_wide_collapsed", "figure": deep_deriv, "options": {"wide": True, "expandAll": False}},
        {"name": "deriv_wide_expanded", "figure": deep_deriv, "options": {"wide": True, "expandAll": True}},
        {"name": "deriv_narrow", "figure": deep_deriv, "options": {"wide": False}},
        {"name": "flow_wide_collapsed", "figure": transistor_flow, "options": {"wide": True, "expandAll": False}},
        {"name": "flow_wide_expanded", "figure": transistor_flow, "options": {"wide": True, "expandAll": True}},
        {"name": "flow_narrow", "figure": transistor_flow, "options": {"wide": False}},
        {"name": "flow_na", "figure": sys_na, "options": {"wide": False}},
        {"name": "sens_regime_wide", "figure": sens_regime, "options": {"wide": True}},
        {"name": "sens_regime_narrow", "figure": sens_regime, "options": {"wide": False}},
        {"name": "sens_num_wide", "figure": sens_num, "options": {"wide": True}},
        {"name": "sens_na", "figure": sens_na, "options": {"wide": False}},
        {"name": "boundary_sec", "figure": boundary_fig, "options": {"wide": False}},
        {"name": "glossary_narrow", "figure": glossary_fig, "options": {"wide": False}},
        {"name": "glossary_wide", "figure": glossary_fig, "options": {"wide": True}},
    ]
    res = _run_harness(tmp_path, cases)

    assert res["deriv_wide_collapsed"]["nodeOverlaps"] == 0
    assert res["deriv_wide_collapsed"]["fitScale"] >= 0.70, res["deriv_wide_collapsed"]
    assert res["deriv_wide_collapsed"]["fallbackPills"] == 0
    assert "公理/假设" in res["deriv_wide_collapsed"]["html"]
    assert "边界条件" in res["deriv_wide_collapsed"]["html"]
    assert "data-expand-anchors" in res["deriv_wide_collapsed"]["html"]
    assert res["deriv_wide_expanded"]["nodeOverlaps"] == 0
    assert "derivation-cards" in res["deriv_narrow"]["html"]
    assert "全览图谱" in res["deriv_narrow"]["html"]

    assert res["flow_wide_collapsed"]["nodeOverlaps"] == 0
    assert res["flow_wide_collapsed"]["fitScale"] >= 0.70, res["flow_wide_collapsed"]
    assert res["flow_wide_collapsed"]["fallbackPills"] == 0
    assert "输入/激励" in res["flow_wide_collapsed"]["html"]
    assert "阻断/约束" in res["flow_wide_collapsed"]["html"]
    assert res["flow_wide_expanded"]["nodeOverlaps"] == 0
    assert "system-flow-cards" in res["flow_narrow"]["html"]
    assert "system-flow-na" in res["flow_na"]["html"]

    assert "sensitivity-regimes-wide" in res["sens_regime_wide"]["html"]
    assert "regime-threshold-pin" in res["sens_regime_wide"]["html"]
    assert "变化幅度" not in res["sens_regime_wide"]["html"]
    assert "sensitivity-regimes" in res["sens_regime_narrow"]["html"]
    assert "regime-threshold-divider" in res["sens_regime_narrow"]["html"]
    assert "全览相变轴" in res["sens_regime_narrow"]["html"]
    assert "data-expand-anchors" in res["sens_regime_narrow"]["html"]

    assert "sensitivity-numeric" in res["sens_num_wide"]["html"]
    assert "-0.5" in res["sens_num_wide"]["html"]
    assert "150" in res["sens_num_wide"]["html"]
    assert "sensitivity-na" in res["sens_na"]["html"]

    assert 'data-section-id="paradigm_shift"' in res["boundary_sec"]["html"]
    assert 'data-section-id="breakdown_boundary"' in res["boundary_sec"]["html"]
    assert "data-expand-anchors" in res["boundary_sec"]["html"]

    assert "glossary-cards" in res["glossary_narrow"]["html"]
    assert "M ≈ 1.44 M☉" in res["glossary_narrow"]["html"]
    assert "data-expand-anchors" in res["glossary_narrow"]["html"]
    assert "glossary-wide-table" in res["glossary_wide"]["html"]


def _social_causal_figure() -> dict:
    specs = [
        ("C1", "driver", "地方财政压力"),
        ("C2", "policy", "分税制改革"),
        ("C3", "mechanism", "预算外收费扩张"),
        ("C4", "mechanism", "土地出让收入"),
        ("C5", "outcome", "开发区与新城扩张"),
        ("C6", "lockin", "土地金融依赖"),
        ("C7", "outcome", "地方债务累积"),
        ("C8", "lockin", "风险锁定"),
        ("C9", "mechanism", "中央转移支付"),
    ]
    nodes = [
        {
            "id": nid,
            "role": role,
            "label": label,
            "detail": "这一步说明制度激励如何传导到下一环，并注明滞后与反馈方向。",
            "blocks": ["B01", "B02", "B03", "B04", "B05"] if nid == "C1" else ["B01"],
            "source": "原文" if nid != "C8" else "补充",
        }
        for nid, role, label in specs
    ]
    edges = [{"from": f"C{i}", "to": f"C{i + 1}", "label": "推动", "polarity": "+"} for i in range(1, 9)]
    edges[1] = {"from": "C2", "to": "C3", "label": "倒逼", "polarity": "+", "delay": True}
    edges.append({"from": "C6", "to": "C1", "label": "反向依赖", "polarity": "-"})
    return {
        "blueprint": "causal_loop",
        "caption": "土地财政正反馈回路",
        "reading_note": "盯住财政压力与土地金融的相互强化",
        "data": {"nodes": nodes, "edges": edges, "loops": [{"id": "R1", "label": "土地金融正反馈环"}]},
    }


def _social_concept_figure() -> dict:
    rows = [
        {
            "key": "王绍光：集权论",
            "everyday": "分权削弱国家能力",
            "author": "财政碎片化导致调控失灵",
            "extra": "低估地方信息优势",
            "blocks": ["B01", "B02", "B03", "B04"],
            "source": "原文",
        },
        {
            "key": "支出法分权派",
            "everyday": "地方支出占比高即分权",
            "author": "以支出份额衡量分权程度",
            "extra": "混淆事权下移与自主权",
            "blocks": ["B05"],
            "source": "补充",
        },
        {
            "key": "周飞舟：财权集权+事权下沉",
            "everyday": "收入上收、责任下压",
            "author": "土地财政成为地方新支柱",
            "extra": "未覆盖东部发达县差异",
            "blocks": ["B06"],
            "source": "原文",
        },
    ]
    return {
        "blueprint": "concept_matrix",
        "caption": "分权口径之争",
        "data": {
            "columns": ["学派/学者/政策立场", "核心界定", "关键机制", "盲区/反例", "出处"],
            "rows": rows,
        },
    }


def _social_metric_figure() -> dict:
    return {
        "blueprint": "metric_chart",
        "caption": "分税制前后工业化与城市化",
        "data": {
            "mode": "series",
            "yLabel": "年均增速",
            "unit": "%",
            "series": [
                {
                    "name": "工业化",
                    "points": [
                        {"x": "1994前", "value": 3.7, "blocks": ["B01"], "source": "原文"},
                        {"x": "1994后", "value": -1.1, "blocks": ["B01"], "source": "原文"},
                    ],
                },
                {
                    "name": "城市化",
                    "points": [
                        {"x": "1994前", "value": 0.6, "blocks": ["B01"], "source": "原文"},
                        {"x": "1994后", "value": 1.3, "blocks": ["B01"], "source": "原文"},
                    ],
                },
            ],
        },
    }


def test_social_five_tools_layout_cards_metric_modes_and_actor_cards(tmp_path: Path) -> None:
    causal = _social_causal_figure()
    concept = _social_concept_figure()
    metric = _social_metric_figure()
    comparison = {
        "blueprint": "metric_chart",
        "caption": "县乡两税占比",
        "data": {
            "mode": "comparison",
            "yLabel": "占县乡财政收入比重",
            "unit": "%",
            "series": [
                {
                    "name": "增值税",
                    "points": [
                        {"x": "1994", "value": 22, "blocks": ["B01"], "source": "原文"},
                        {"x": "2003", "value": 18, "blocks": ["B01"], "source": "原文"},
                    ],
                },
                {
                    "name": "营业税",
                    "points": [
                        {"x": "1994", "value": 20, "blocks": ["B01"], "source": "原文"},
                        {"x": "2003", "value": 25, "blocks": ["B01"], "source": "原文"},
                    ],
                },
            ],
        },
    }
    not_applicable = {
        "blueprint": "metric_chart",
        "caption": "群体意见的即时成因",
        "data": {
            "mode": "not_applicable",
            "reason": "本章属定性理论论述，未提供量化统计序列。",
            "highlights": [{"text": "群体由无意识动机驱动", "blocks": ["B01"], "source": "原文"}],
            "series": [],
        },
    }
    actors = {
        "blueprint": "extension_dossier",
        "variant": "actor_incentives",
        "caption": "分税制下的多方博弈",
        "reading_note": "盯住中央与地方的激励错位",
        "items": [
            {
                "title": "中央政府",
                "role_tag": "核心决策方",
                "incentive": "提高两个比重",
                "constraint": "不得触动地方既得利益过快",
                "strategy": "以基数谈判换取地方接受",
                "blocks": ["B01", "B02"],
                "source": "原文",
            },
            {
                "title": "县乡基层政府",
                "role_tag": "制度承受方",
                "incentive": "保运转保工资",
                "constraint": "自有收入锐减",
                "strategy": "转向土地出让",
                "blocks": ["B01"],
                "source": "补充",
            },
        ],
        "sections": [],
    }
    cases = [
        {"name": "causal_wide", "figure": causal, "options": {"wide": True}},
        {"name": "causal_wide_expanded", "figure": causal, "options": {"wide": True, "expandAll": True}},
        {"name": "causal_narrow", "figure": causal, "options": {"wide": False}},
        {"name": "concept_narrow", "figure": concept, "options": {"wide": False}},
        {"name": "concept_wide", "figure": concept, "options": {"wide": True}},
        {"name": "metric_series_narrow", "figure": metric, "options": {"wide": False}},
        {"name": "metric_series_wide", "figure": metric, "options": {"wide": True}},
        {"name": "metric_comparison_wide", "figure": comparison, "options": {"wide": True}},
        {"name": "metric_na_narrow", "figure": not_applicable, "options": {"wide": False}},
        {"name": "actor_narrow", "figure": actors, "options": {"wide": False}},
        {"name": "actor_wide", "figure": actors, "options": {"wide": True}},
    ]
    res = _run_harness(tmp_path, cases)

    # 宽屏因果回路：双排折行、≥65% 自适应、0 重叠、0 展开重叠
    assert res["causal_wide"]["nodeOverlaps"] == 0, res["causal_wide"]
    assert res["causal_wide"]["fitScale"] >= 0.65, res["causal_wide"]
    assert res["causal_wide_expanded"]["nodeOverlaps"] == 0
    assert res["causal_wide_expanded"]["fitScale"] >= 0.65
    assert "核心驱动" in res["causal_wide"]["html"]
    assert "锁定/风险" in res["causal_wide"]["html"]
    assert 'stroke-dasharray="5 3"' in res["causal_wide"]["html"]
    assert "dag-edge-delay" in res["causal_wide"]["html"]

    # 窄抽屉因果：卡片流而非横向裁切画布
    assert "causal-flow" in res["causal_narrow"]["html"]
    assert "全览图谱" in res["causal_narrow"]["html"]
    assert "土地金融正反馈环" in res["causal_narrow"]["html"]
    assert res["causal_narrow"]["natW"] == 0

    # 概念矩阵：窄屏卡片流、宽屏五列表
    assert "concept-cards" in res["concept_narrow"]["html"]
    assert "term-contrast-table" not in res["concept_narrow"]["html"]
    assert "核心界定" in res["concept_narrow"]["html"]
    assert "盲区/反例" in res["concept_narrow"]["html"]
    assert "concept-card" in res["concept_narrow"]["html"]
    assert "bp-table concept-matrix-table" in res["concept_wide"]["html"]
    assert "overflow-x:auto" in res["concept_wide"]["html"]

    # 实证指标：真实量纲、负值、显式数值与双系列图例
    assert "metric-chart" in res["metric_series_wide"]["html"]
    assert "年均增速" in res["metric_series_wide"]["html"]
    assert "-1.1" in res["metric_series_wide"]["html"]
    assert "3.7" in res["metric_series_wide"]["html"]
    assert "metric-legend" in res["metric_series_wide"]["html"]
    assert "metric-line" in res["metric_series_wide"]["html"]
    assert 'stroke-width="1.2"' in res["metric_series_wide"]["html"]
    assert "metric-bar" in res["metric_comparison_wide"]["html"]
    assert "22" in res["metric_comparison_wide"]["html"]
    assert 'class="metric-tick">0</text>' in res["metric_comparison_wide"]["html"]
    assert 'class="metric-tick">-' not in res["metric_comparison_wide"]["html"]
    assert (
        'x="205.5" y="238" text-anchor="middle" class="metric-xlabel">1994</text>'
        in res["metric_comparison_wide"]["html"]
    )
    assert (
        'x="504.5" y="238" text-anchor="middle" class="metric-xlabel">2003</text>'
        in res["metric_comparison_wide"]["html"]
    )
    assert "metric-na" in res["metric_na_narrow"]["html"]
    assert "未提供量化统计序列" in res["metric_na_narrow"]["html"]
    assert "metric-svg" not in res["metric_na_narrow"]["html"]

    # 行动者：结构化字段卡片
    assert "actor-cards" in res["actor_narrow"]["html"]
    assert "核心激励" in res["actor_narrow"]["html"]
    assert "制度承受方" in res["actor_narrow"]["html"]
    assert "actor-cards wide" in res["actor_wide"]["html"]
