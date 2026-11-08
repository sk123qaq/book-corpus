"""标准档：逐字原文之上的确定性注解层。

本模块不调用模型。它把章节模型里的节导语、术语、背景注、折叠与结构图
规整成一份固定形状，交给渲染器叠加到原文上；任何不合规的注解都降级为
审计警告，而不是丢字或改字。
"""

from .diagrams import audit_figures
from .schema import SCHEMA_VERSION

STANDARD_PROMPT_VERSION = "standard-annotations-v1"
MAX_LEAD = 80
MAX_NOTE = 120
MAX_SECTION_FIGURES = 3
AUTO_FOLD_ITEMS = 8
STRUCTURAL_FAMILIES = ("tree", "network", "argument")


def _block_map(blocks):
    return {block.get("id"): block for block in blocks or [] if block.get("id")}


def _block_order(blocks):
    return {block.get("id"): index for index, block in enumerate(blocks or [])}


def _sections(model):
    section_rows = []
    warnings = []
    for section in model.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = section.get("id") or ""
        lead = str(section.get("lead") or "").strip()
        if not lead:
            continue
        if len(lead) > MAX_LEAD:
            warnings.append(f"{section_id or '节'} 的导语超过 {MAX_LEAD} 字，已略去")
            continue
        section_rows.append({"id": section_id, "lead": lead})
    return section_rows, warnings


def _terms(model, blocks):
    block_map = _block_map(blocks)
    order = _block_order(blocks)
    warnings = []
    chosen = {}
    for index, term in enumerate(model.get("terms") or []):
        if not isinstance(term, dict):
            continue
        name = str(term.get("term") or "").strip()
        definition = str(term.get("definition") or "").strip()
        block_id = term.get("source_block") or ""
        if not name or not definition:
            continue
        if block_id not in block_map:
            warnings.append(f"术语「{name}」的出处不在本章，已略去")
            continue
        item = {
            "term": name,
            "definition": definition,
            "source_block": block_id,
            "quote": str(term.get("quote") or ""),
        }
        rank = (order.get(block_id, len(order)), index)
        if name not in chosen or rank < chosen[name][0]:
            chosen[name] = (rank, item)
    rows = [item for _rank, item in chosen.values()]
    rows.sort(key=lambda item: order.get(item["source_block"], len(order)))
    return rows, warnings


def _notes(model, blocks):
    block_map = _block_map(blocks)
    order = _block_order(blocks)
    raw_notes = model.get("standard_notes")
    if not isinstance(raw_notes, list) or not raw_notes:
        raw_notes = model.get("notes")
    warnings = []
    rows = []
    for index, note in enumerate(raw_notes or []):
        if not isinstance(note, dict):
            continue
        block_id = note.get("block") or ""
        text = str(note.get("text") or "").strip()
        origin = note.get("origin") or "editor"
        if block_id not in block_map:
            warnings.append(f"注解的出处 {block_id or '(空)'} 不在本章，已略去")
            continue
        if not text:
            warnings.append(f"{block_id} 的注解为空，已略去")
            continue
        if len(text) > MAX_NOTE:
            warnings.append(f"{block_id} 的注解超过 {MAX_NOTE} 字，已略去")
            continue
        if origin not in ("source", "editor"):
            warnings.append(f"{block_id} 的注解来源非法：{origin}")
            continue
        rows.append(
            {
                "block": block_id,
                "text": text,
                "origin": origin,
                "tag": "background",
                "_rank": (order.get(block_id, len(order)), index),
            }
        )
    rows.sort(key=lambda item: item["_rank"])
    for row in rows:
        del row["_rank"]
    return rows, warnings


def _fold_row(block, summary):
    if block.get("kind") == "list":
        count = len(block.get("items") or [])
        unit = "条"
        default = f"完整清单 · {count} 条"
    else:
        count = len(block.get("rows") or [])
        unit = "行"
        default = f"表格 · {count} 行"
    summary = str(summary or "").strip() or default
    if str(count) not in summary:
        summary = f"{summary} · {count} {unit}"
    return {"block": block["id"], "summary": summary, "reason": "长清单折叠"}


def _folds(model, blocks):
    block_map = _block_map(blocks)
    order = _block_order(blocks)
    warnings = []
    rows = {}
    for fold in model.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        block_id = fold.get("block") or ""
        block = block_map.get(block_id)
        if block is None:
            warnings.append(f"折叠指向不存在的块：{block_id}")
            continue
        if block.get("kind") not in ("list", "table"):
            warnings.append(f"折叠只作用于清单或表格：{block_id}")
            continue
        row = _fold_row(block, fold.get("summary"))
        row["reason"] = str(fold.get("reason") or row["reason"])
        rows[block_id] = row
    for block in blocks or []:
        block_id = block.get("id")
        if not block_id or block_id in rows:
            continue
        kind = block.get("kind")
        long_list = kind == "list" and len(block.get("items") or []) > AUTO_FOLD_ITEMS
        long_table = kind == "table" and len(block.get("rows") or []) > AUTO_FOLD_ITEMS
        if long_list or long_table:
            rows[block_id] = _fold_row(block, "")
    ordered = sorted(rows.values(), key=lambda row: order.get(row["block"], len(order)))
    return ordered, warnings


def _unit_sections(model):
    sections = model.get("sections") or []
    units = {}
    for section in sections:
        for unit_id in section.get("unit_ids") or []:
            units.setdefault(unit_id, []).append(section.get("id"))
    for unit in model.get("units") or []:
        unit_id = unit.get("id")
        if unit_id and unit_id not in units and unit.get("section"):
            units[unit_id] = [unit.get("section")]
    return units


def _first_block(unit, block_map):
    for block_id in (unit or {}).get("source_blocks") or []:
        if block_id in block_map:
            return block_id
    return ""


def _chapter_map(model, blocks):
    sections = model.get("sections") or []
    units = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    unit_sections = _unit_sections(model)
    if not sections:
        return {
            "id": "F00",
            "family": "tree",
            "question": "本章怎么走",
            "units": [],
            "origin": "derived",
            "caption": "本章结构",
            "reading_note": "沿节点回到原文",
            "data": {"nodes": [], "edges": []},
            "fallback": "",
        }
    block_map = _block_map(blocks)
    unit_ids = []
    nodes = []
    for section in sections:
        section_id = section.get("id") or ""
        section_units = list(section.get("unit_ids") or [])
        if not section_units:
            section_units = [unit_id for unit_id, owners in unit_sections.items() if section_id in owners]
        unit_id = section_units[0] if section_units else ""
        unit_ids.extend(section_units)
        block_id = _first_block(units.get(unit_id), block_map)
        nodes.append(
            {
                "id": section_id,
                "label": section.get("title") or section_id,
                "unit": unit_id,
                "block": block_id,
            }
        )
    edges = [{"from": nodes[index - 1]["id"], "to": nodes[index]["id"]} for index in range(1, len(nodes))]
    return {
        "id": "F00",
        "family": "tree",
        "question": "本章怎么走",
        "units": unit_ids,
        "origin": "derived",
        "caption": "本章结构",
        "reading_note": "沿节点回到原文",
        "data": {"nodes": nodes, "edges": edges},
        "fallback": "",
    }


def _figures(model, blocks):
    unit_sections = _unit_sections(model)
    figures = [figure for figure in model.get("figures") or [] if isinstance(figure, dict)]
    warnings = []

    def sections_for(figure):
        seen = []
        for unit_id in figure.get("units") or []:
            for section_id in unit_sections.get(unit_id) or []:
                if section_id not in seen:
                    seen.append(section_id)
        return seen

    def single_section(figure):
        figure_units = figure.get("units") or []
        if not figure_units:
            return None
        found = set()
        for unit_id in figure_units:
            owners = unit_sections.get(unit_id) or []
            if not owners:
                return None
            found.update(owners)
        return next(iter(found)) if len(found) == 1 else None

    chapter = None
    for figure in figures:
        if len(sections_for(figure)) >= 2:
            chapter = figure
            break
    if chapter is None:
        for figure in figures:
            if figure.get("family") in STRUCTURAL_FAMILIES and len(sections_for(figure)) != 1:
                chapter = figure
                break
    if chapter is None:
        chapter = _chapter_map(model, blocks)

    section_figures = []
    for figure in figures:
        if figure is chapter:
            continue
        if single_section(figure) is None:
            warnings.append(f"{figure.get('id') or '图'} 的单元跨节或缺失，未放进取节图")
            continue
        if len(section_figures) >= MAX_SECTION_FIGURES:
            warnings.append(f"{figure.get('id') or '图'} 超过每章三张节图的额度")
            continue
        section_figures.append(figure)
    return [chapter, *section_figures], warnings


def build_standard(model, blocks):
    """从章节模型与原文块构建标准档，不调用模型。"""
    model = model or {}
    blocks = blocks or []
    sections, section_warnings = _sections(model)
    terms, term_warnings = _terms(model, blocks)
    notes, note_warnings = _notes(model, blocks)
    folds, fold_warnings = _folds(model, blocks)
    figures, figure_warnings = _figures(model, blocks)
    chapter_id = figures[0].get("id") if figures else None
    external = [figure.get("id") for figure in figures if figure.get("origin") == "external"]
    if external:
        figures = [figure for figure in figures if figure.get("origin") != "external"]
        if not figures or chapter_id in external:
            # 标准档不承接外部图；章级图被删时补一张无数字的结构图。
            figures = [_chapter_map(model, blocks), *figures]
        figure_warnings = [
            *figure_warnings,
            *[f"图 {figure_id} 来自书外，标准档已删除" for figure_id in external],
        ]
    report = audit_figures(figures, model, blocks)
    warnings = section_warnings + term_warnings + note_warnings + fold_warnings + figure_warnings + report["warnings"]
    errors = report["errors"]
    if errors:
        # G12 失败图不能留在页面上：坏数字/坏单元先删图，再补一张
        # 无数字的章级结构图，避免标准档把错误数据画出来。
        invalid = {
            figure.get("id")
            for figure in figures
            if any(str(error).startswith(f"{figure.get('id')} ") for error in errors)
        }
        if invalid:
            dropped = [figure.get("id") for figure in figures if figure.get("id") in invalid]
            figures = [figure for figure in figures if figure.get("id") not in invalid]
            if not figures or chapter_id in invalid:
                figures = [_chapter_map(model, blocks), *figures]
            report = audit_figures(figures, model, blocks)
            errors = report["errors"]
            warnings = [
                *warnings,
                *[f"图 {figure_id} 未通过 G12，已删除" for figure_id in dropped],
                *report["warnings"],
            ]
    status = "fail" if errors else ("warn" if warnings else "pass")
    return {
        "schema": SCHEMA_VERSION,
        "prompt_version": STANDARD_PROMPT_VERSION,
        "sections": sections,
        "terms": terms,
        "notes": notes,
        "folds": folds,
        "figures": figures,
        "audit": {"status": status, "errors": errors, "warnings": warnings},
    }
