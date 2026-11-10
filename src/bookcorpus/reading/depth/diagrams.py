"""标准档结构图：图数据审计与无依赖 HTML 渲染。

图只从章节模型与逐字原文派生，不读取外部资源。数据不足时保留图元数据，
渲染成可读的降级说明，并链回原文块。
"""

import html
import re

from .schema import FIGURE_FAMILIES, FIGURE_ORIGINS

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
