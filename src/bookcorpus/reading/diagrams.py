"""标准档结构图：图数据审计与无依赖 HTML 渲染。

图只从章节模型与逐字原文派生，不读取外部资源。数据不足时保留图元数据，
渲染成可读的降级说明，并链回原文块。
"""

import html
import re

from .map_schema import FIGURE_FAMILIES, FIGURE_ORIGINS

NODE_FAMILIES = ("tree", "network", "argument", "flow", "timeline")
LABEL_KEYS = ("label", "labels", "caption", "title", "name", "columns")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _unit_map(model):
    return {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}


def _block_map(blocks):
    return {block.get("id"): block for block in blocks or [] if block.get("id")}


def _unit_block(unit, block_map):
    for block_id in (unit or {}).get("source_blocks") or []:
        if block_id in block_map:
            return block_id
    return ""


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _label_strings(value, *, key=None):
    if isinstance(value, str):
        if key in LABEL_KEYS:
            yield value
    elif isinstance(value, dict):
        for item_key, item in value.items():
            yield from _label_strings(item, key=item_key)
    elif isinstance(value, list):
        for item in value:
            yield from _label_strings(item, key=key)


def _numbers(text):
    return NUMBER_RE.findall(text or "")


def _block_text(block):
    pieces = [str(block.get("text") or "")]
    pieces.extend(str(item) for item in block.get("items") or [])
    pieces.extend(str(cell) for cell in block.get("head") or [])
    for row in block.get("rows") or []:
        if isinstance(row, list):
            pieces.extend(str(cell) for cell in row)
    return "\n".join(pieces)


def audit_figures(figures, model, blocks):
    """G12：图元数据、单元引用、族/来源与图中数字都必须有源文依据。"""
    errors, warnings = [], []
    units = _unit_map(model)
    source_text = "\n".join(_block_text(block) for block in blocks or [])
    source_numbers = set(_numbers(source_text))
    for index, figure in enumerate(figures or []):
        if not isinstance(figure, dict):
            errors.append(f"figures[{index}] 不是对象")
            continue
        figure_id = figure.get("id") or f"figures[{index}]"
        for key in ("id", "family", "question", "units", "origin", "caption"):
            if key not in figure:
                errors.append(f"{figure_id} 缺少字段 {key}")
            elif key in ("id", "question", "caption") and not str(figure.get(key) or "").strip():
                errors.append(f"{figure_id} 的字段 {key} 为空")
        family = figure.get("family")
        if family not in FIGURE_FAMILIES:
            errors.append(f"{figure_id} 的图族非法：{family}")
        origin = figure.get("origin")
        if origin not in FIGURE_ORIGINS:
            errors.append(f"{figure_id} 的来源非法：{origin}")
        figure_units = figure.get("units")
        if not isinstance(figure_units, list):
            errors.append(f"{figure_id} 的 units 必须是数组")
            figure_units = []
        for unit_id in figure_units:
            if unit_id not in units:
                errors.append(f"{figure_id} 引用了不存在的单元：{unit_id}")
        labels = [str(figure.get("caption") or "")]
        labels.extend(_label_strings(figure.get("data") or {}))
        for label in labels:
            for number in _numbers(label):
                if number not in source_numbers:
                    errors.append(f"{figure_id} 的数字 {number} 不在源文：{label}")
        if not figure.get("data"):
            warnings.append(f"{figure_id} 缺少图数据")
        if not str(figure.get("reading_note") or "").strip():
            warnings.append(f"{figure_id} 缺少读图提示")
    status = "fail" if errors else ("warn" if warnings else "pass")
    return {"status": status, "errors": errors, "warnings": warnings}


def _link(block_id, label, block_map):
    if block_id in block_map:
        escaped_id = html.escape(str(block_id), quote=True)
        return f'<a href="#{escaped_id}">{html.escape(label)}</a>'
    return html.escape(label)


def _unit_list(figure, model, blocks):
    units = _unit_map(model)
    block_map = _block_map(blocks)
    parts = ['<ul class="fig-units">']
    for unit_id in figure.get("units") or []:
        unit = units.get(unit_id) or {}
        label = str(unit.get("summary") or unit_id or "单元")
        block_id = _unit_block(unit, block_map)
        parts.append(f"<li>{_link(block_id, label, block_map)}</li>")
    parts.append("</ul>")
    return "".join(parts)


def _fallback(figure, reason, model, blocks):
    custom = str(figure.get("fallback") or "").strip()
    note = "图已降级：" + reason
    if custom:
        note += " " + custom
    return f'<p class="fig-fallback">{html.escape(note)}</p>{_unit_list(figure, model, blocks)}'


def _node_body(figure, model, blocks):
    data = figure.get("data") if isinstance(figure.get("data"), dict) else {}
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return _fallback(figure, "缺少节点数据。", model, blocks)
    block_map = _block_map(blocks)
    units = _unit_map(model)
    labels = {}
    parts = ['<ol class="fig-nodes">']
    for node in nodes:
        if not isinstance(node, dict):
            return _fallback(figure, "节点格式不对。", model, blocks)
        node_id = node.get("id") or ""
        label = str(node.get("label") or node_id or "节点")
        labels[node_id] = label
        block_id = node.get("block") or _unit_block(units.get(node.get("unit")), block_map)
        parts.append(f"<li>{_link(block_id, label, block_map)}</li>")
    parts.append("</ol>")
    edges = data.get("edges")
    if isinstance(edges, list) and edges:
        edge_parts = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = labels.get(edge.get("from"), edge.get("from") or "")
            target = labels.get(edge.get("to"), edge.get("to") or "")
            edge_parts.append(f"{html.escape(str(source))} → {html.escape(str(target))}")
        if edge_parts:
            parts.append(f'<p class="fig-edges">{" · ".join(edge_parts)}</p>')
    return "".join(parts)


def _matrix_body(figure, model, blocks):
    data = figure.get("data") if isinstance(figure.get("data"), dict) else {}
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
        return _fallback(figure, "缺少行列数据。", model, blocks)
    head = ""
    if columns:
        cells = "".join(f"<th>{html.escape(str(cell))}</th>" for cell in columns)
        head = f"<thead><tr>{cells}</tr></thead>"
    body = "".join(
        f"<tr>{''.join(f'<td>{html.escape(str(cell))}</td>' for cell in row)}</tr>"
        for row in rows
        if isinstance(row, list)
    )
    if not body:
        body = "".join(
            f"<tr>{''.join(f'<td>{html.escape(str(row.get(column)))}</td>' for column in columns)}</tr>"
            for row in rows
            if isinstance(row, dict)
        )
    if not body:
        return _fallback(figure, "缺少行列数据。", model, blocks)
    return f'<div class="fig-table"><table>{head}<tbody>{body}</tbody></table></div>'


def _quantity_body(figure, model, blocks):
    data = figure.get("data") if isinstance(figure.get("data"), dict) else {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return _fallback(figure, "缺少数量数据。", model, blocks)
    values = []
    for item in items:
        if not isinstance(item, dict):
            return _fallback(figure, "数量项格式不对。", model, blocks)
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            return _fallback(figure, "数量值不是数字。", model, blocks)
        values.append((item, value))
    maximum = max(value for _item, value in values) or 1.0
    parts = ['<div class="fig-bars">']
    for item, value in values:
        label = str(item.get("label") or item.get("name") or "")
        width = max(0.0, min(100.0, value / maximum * 100.0))
        escaped_label = html.escape(label)
        escaped_value = html.escape(str(item.get("value")))
        parts.append(
            f'<div class="fig-bar"><span class="fig-bar-label">{escaped_label}</span>'
            f'<span class="fig-bar-track"><span class="fig-bar-fill" style="width:{width:.1f}%"></span></span>'
            f'<span class="fig-bar-value">{escaped_value}</span></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def render_figure(figure, model, blocks):
    """渲染一张标准档图；任何数据问题都退到单元清单，不抛异常。"""
    if not isinstance(figure, dict):
        return ""
    family = figure.get("family") or ""
    caption = str(figure.get("caption") or figure.get("question") or "图")
    reading_note = str(figure.get("reading_note") or "")
    if family in NODE_FAMILIES:
        body = _node_body(figure, model, blocks)
    elif family == "matrix":
        body = _matrix_body(figure, model, blocks)
    elif family == "quantity":
        body = _quantity_body(figure, model, blocks)
    else:
        body = _fallback(figure, "未知图族。", model, blocks)
    figure_id = html.escape(str(figure.get("id") or ""), quote=True)
    escaped_family = html.escape(str(family), quote=True)
    escaped_caption = html.escape(caption)
    escaped_note = html.escape(reading_note)
    return (
        f'<figure class="depth-figure" data-origin="added" data-figure="{figure_id}" data-family="{escaped_family}">'
        f"<figcaption><b>{escaped_caption}</b><span>{escaped_note}</span></figcaption>{body}</figure>"
    )


def _node_svg(figure):
    """把 nodes/edges 渲染为横向或纵折的零依赖 SVG 流程图。"""
    data = figure.get("data") if isinstance(figure.get("data"), dict) else {}
    nodes = [node for node in (data.get("nodes") or []) if isinstance(node, dict)]
    if not nodes:
        return ""
    edges = [edge for edge in (data.get("edges") or []) if isinstance(edge, dict)]
    cols = min(3, max(1, len(nodes)))
    box_w, box_h, gap_x, gap_y = 190, 62, 34, 34
    pad_x, pad_y = 16, 16
    rows = (len(nodes) + cols - 1) // cols
    width = pad_x * 2 + cols * box_w + max(0, cols - 1) * gap_x
    height = pad_y * 2 + rows * box_h + max(0, rows - 1) * gap_y
    pos = {}
    rects = []
    for idx, node in enumerate(nodes):
        nid = str(node.get("id") or f"N{idx + 1}")
        row, col = divmod(idx, cols)
        x = pad_x + col * (box_w + gap_x)
        y = pad_y + row * (box_h + gap_y)
        pos[nid] = (x, y)
        label = str(node.get("label") or nid)
        short = (label[:24] + "…") if len(label) > 25 else label
        sub = str(node.get("note") or node.get("block") or "")
        rects.append(
            f'<g class="svg-node" data-node="{html.escape(nid, quote=True)}">'
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="7" '
            f'fill="#faf7ef" stroke="#1E3A36" stroke-width="1.3"/>'
            f'<text x="{x + 12}" y="{y + 26}" font-size="13" font-weight="600" '
            f'fill="#181817">{html.escape(short)}</text>'
            + (
                f'<text x="{x + 12}" y="{y + 46}" font-size="11" fill="#6d6962">{html.escape(sub[:28])}</text>'
                if sub
                else ""
            )
            + "</g>"
        )
    arrows = []
    if edges:
        for edge in edges:
            src = pos.get(str(edge.get("from") or ""))
            dst = pos.get(str(edge.get("to") or ""))
            if not src or not dst:
                continue
            x1 = src[0] + box_w if dst[0] > src[0] else src[0] + box_w / 2
            y1 = src[1] + box_h / 2 if dst[0] > src[0] else src[1] + box_h
            x2 = dst[0] if dst[0] > src[0] else dst[0] + box_w / 2
            y2 = dst[1] + box_h / 2 if dst[0] > src[0] else dst[1]
            lbl = str(edge.get("label") or "").strip()
            arrows.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#A8442F" stroke-width="1.4" marker-end="url(#rd-arrow)"/>'
            )
            if lbl:
                arrows.append(
                    f'<text x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2 - 4:.1f}" '
                    f'text-anchor="middle" font-size="10.5" fill="#A8442F">{html.escape(lbl[:16])}</text>'
                )
    else:
        for idx in range(len(nodes) - 1):
            nid1 = str(nodes[idx].get("id") or f"N{idx + 1}")
            nid2 = str(nodes[idx + 1].get("id") or f"N{idx + 2}")
            x1, y1 = pos[nid1]
            x2, y2 = pos[nid2]
            if y1 == y2:
                arrows.append(
                    f'<line x1="{x1 + box_w}" y1="{y1 + box_h / 2}" x2="{x2}" y2="{y2 + box_h / 2}" '
                    f'stroke="#A8442F" stroke-width="1.4" marker-end="url(#rd-arrow)"/>'
                )
            else:
                arrows.append(
                    f'<path d="M {x1 + box_w / 2} {y1 + box_h} L {x1 + box_w / 2} {y1 + box_h + gap_y / 2} '
                    f'L {x2 + box_w / 2} {y2 - gap_y / 2} L {x2 + box_w / 2} {y2}" fill="none" '
                    f'stroke="#A8442F" stroke-width="1.4" marker-end="url(#rd-arrow)"/>'
                )
    return (
        f'<svg class="assist-svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(str(figure.get("caption") or "关系图"), quote=True)}">'
        '<defs><marker id="rd-arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#A8442F"/></marker></defs>' + "".join(arrows) + "".join(rects) + "</svg>"
    )


def render_assist_figure(figure):
    """将划词生成的图结构（蓝图 JSON 或旧版 family 结构）渲染为兼容 HTML/SVG 兜底卡。"""
    if not isinstance(figure, dict):
        return ""
    blueprint = str(figure.get("blueprint") or "").strip()
    bp_to_family = {
        "argument_map": "argument",
        "lineage_dag": "tree",
        "power_hierarchy": "tree",
        "spatial_route": "network",
        "historical_causality": "network",
        "relation_graph": "network",
        "flow_swimlane": "flow",
        "decision_tree": "tree",
        "derivation_dag": "tree",
        "system_flow": "flow",
        "causal_loop": "network",
        "term_contrast": "matrix",
        "concept_matrix": "matrix",
        "element_matrix": "matrix",
        "parallel_timeline": "matrix",
        "quadrant_matrix": "matrix",
        "tension_curve": "quantity",
        "sensitivity_chart": "quantity",
        "metric_chart": "quantity",
    }
    family = str(figure.get("family") or bp_to_family.get(blueprint) or "flow")
    if family not in FIGURE_FAMILIES:
        family = "flow"
    caption = str(figure.get("caption") or "段落图解")
    reading_note = str(figure.get("reading_note") or "")
    body = ""
    raw_data = figure.get("data") if isinstance(figure.get("data"), dict) else {}
    bp_mode = str(raw_data.get("mode") or "").strip().lower()
    if bp_mode == "not_applicable":
        reason = str(
            raw_data.get("not_applicable_reason")
            or raw_data.get("reason")
            or figure.get("summary")
            or "本章无显著空间动线或外部学派谱系。"
        ).strip()
        body = f'<p class="fig-fallback">{html.escape(reason)}</p>'
    if not body and family in NODE_FAMILIES and isinstance(raw_data.get("nodes"), list):
        body = _node_svg(figure)
    elif not body and (
        blueprint in {"extension_dossier", "extension_checklist"} or isinstance(figure.get("items"), list)
    ):
        items = [it for it in (figure.get("items") or []) if isinstance(it, dict)]
        if items:
            lis = []
            for it in items:
                title = str(it.get("title") or it.get("text") or it.get("label") or "").strip()
                detail = str(it.get("body") or it.get("detail") or "").strip()
                detail_html = f"：{html.escape(detail)}" if detail else ""
                lis.append(f"<li><b>{html.escape(title)}</b>{detail_html}</li>")
            body = f'<ul class="fig-units">{"".join(lis)}</ul>'
    elif not body and family == "matrix":
        if isinstance(raw_data.get("columns"), list):
            compat_rows = []
            for r in raw_data.get("rows") or []:
                if isinstance(r, dict):
                    compat_rows.append([r.get("key", ""), r.get("everyday", ""), r.get("author", "")])
                elif isinstance(r, list):
                    compat_rows.append(r)
            compat = dict(figure, data={"columns": raw_data["columns"], "rows": compat_rows})
            body = _matrix_body(compat, {}, [])
    elif not body and family == "quantity":
        if isinstance(raw_data.get("points"), list):
            compat_items = [
                {"label": p.get("beat", ""), "value": p.get("tension", 0)}
                for p in raw_data["points"]
                if isinstance(p, dict)
            ]
            compat = dict(figure, data={"items": compat_items})
            body = _quantity_body(compat, {}, [])
        else:
            body = _quantity_body(figure, {}, [])
    if not body and isinstance(figure.get("sections"), list):
        parts = ['<div class="fig-sections">']
        for sec in figure["sections"]:
            if not isinstance(sec, dict):
                continue
            parts.append(f"<h5>{html.escape(str(sec.get('title') or ''))}</h5><ul>")
            for it in sec.get("items") or []:
                if isinstance(it, dict):
                    head = str(it.get("name") or it.get("title") or it.get("text") or "").strip()
                    txt = str(it.get("body") or it.get("detail") or "").strip()
                    parts.append(f"<li><b>{html.escape(head)}</b>：{html.escape(txt)}</li>")
            parts.append("</ul>")
        parts.append("</div>")
        body = "".join(parts)
    if not body:
        body = _fallback(figure, "由前端蓝图引擎或要点清单渲染。", {}, [])
    return (
        f'<figure class="assist-figure" data-family="{html.escape(family, quote=True)}"'
        + (f' data-blueprint="{html.escape(blueprint, quote=True)}"' if blueprint else "")
        + f"><figcaption><b>{html.escape(caption)}</b>"
        + (f"<span>{html.escape(reading_note)}</span>" if reading_note else "")
        + f"</figcaption>{body}</figure>"
    )
