"""深读层：论证注与论证链。

深读不是第二份正文。它在标准档的逐字原文上叠加两类可核查的编辑层：
论证注（前提、边界、证据、反例、互文）与论证链（argument family 的结构图）。
所有锚点只能落到本章的 unit / block；互文注可以提到同一本书的其他章节，
但正文必须点名章节，且不得引入书外事实。

模块只产生 ``layers/deep.json`` 的载荷与审计结果，不写文件。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..model import ModelProviderError
from ..textnorm import normalize_verbatim
from .diagrams import audit_figures
from .standard import _block_map

DEEP_SCHEMA_VERSION = 1
DEEP_PROMPT_VERSION = "deep-annotations-v1"
MIN_NOTE = 80
MAX_NOTE = 200
MAX_NOTES = 12
MAX_NOTES_PER_SECTION = 3
NOTE_TYPES = ("assumption", "boundary", "evidence", "counter", "intertext")
NOTE_ORIGINS = ("editor", "source", "model")
MAX_JSON_RETRIES = 1
CHAPTER_RE = re.compile(r"第\s*[一二三四五六七八九十百千万零两\d]+\s*[章节]")

SYSTEM_PROMPT = "\n".join(
    [
        "你是书库的深读编辑。给定一章的章节模型与原文块，请写论证注与论证链，只输出一个 JSON 对象。",
        "",
        "硬约束：",
        "1. 顶层字段为 schema、prompt_version、notes、chains。schema 写 1，prompt_version 固定写 deep-annotations-v1。",
        "2. notes 是 1 到 12 条论证注，每节最多 3 条。每条字段为 id、type、section、units、blocks、"
        "quote（可选）、text、origin、source_scope。",
        "3. type 只能是 assumption/boundary/evidence/counter/intertext。origin 只能是 editor/source/model。"
        "source_scope 只能是「本章」或「本书 <节或章>」。",
        "4. text 写 80 到 200 字，必须是编辑判断，不要摘抄原文，不要写书外事实，不要写「已核验」。",
        "5. 每条注至少给一个 units 或 blocks 锚点；所有 id 必须来自本章章节模型或 blocks。"
        "quote 可省略；写了就必须是某个被引用 block 的逐字子串。",
        "6. intertext 注可以提到同一本书的其他章节，但 text 必须点名具体章节或小节；"
        "不能引用本书以外的材料，也不能假装读过未给到的章节内容。",
        "7. chains 每节最多 1 条，family 固定写 argument，origin 固定写 derived。字段为 id、family、section、"
        "question、units、origin、caption、reading_note、data、fallback。",
        "8. data.nodes 每项写 id、label，可选 unit、block；data.edges 每项写 from、to，可选 label。"
        "边只能连接已有节点 id；没有可靠关系时 edges 可以是空数组，但 fallback 必须写文字说明。",
        "9. 不要发明原文没有的数字、引文、章节名或外部资料。不要输出 markdown 围栏或解释。",
        "",
        "输出只包含 JSON。",
    ]
)


class DeepError(ValueError):
    """深读层不满足结构、锚点或逐字约束。"""


def _text(value: Any, name: str, errors: list[str], *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        errors.append(f"{name} 必须是字符串")
        return ""
    text = value.strip()
    if not allow_empty and not text:
        errors.append(f"{name} 不能为空")
    return text


def _optional_text(value: Any, name: str, errors: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append(f"{name} 必须是字符串")
        return ""
    return value.strip()


def _identifier(value: Any, name: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{name} 必须是非空字符串")
        return ""
    return value.strip()


def _refs(value: Any, name: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{name} 必须是数组")
        return []
    refs: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{name}[{index}] 必须是非空字符串")
            continue
        refs.append(item.strip())
    return refs


def _block_text(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "head"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    for key in ("items", "rows"):
        value = block.get(key)
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, list):
                parts.append("\n".join(str(cell) for cell in row))
            elif isinstance(row, str):
                parts.append(row)
    return "\n".join(parts)


def _quote_matches(quote: str, block: dict[str, Any]) -> bool:
    needle = normalize_verbatim(quote)
    haystack = normalize_verbatim(_block_text(block))
    return bool(needle) and needle in haystack


def _valid_scope(scope: str) -> bool:
    if scope == "本章":
        return True
    return bool(re.fullmatch(r"本书\s+\S.*", scope))


def _names_place(text: str, section_map: dict[str, dict[str, Any]]) -> bool:
    if CHAPTER_RE.search(text):
        return True
    for section_id, section in section_map.items():
        title = str(section.get("title") or "").strip()
        if section_id and section_id in text:
            return True
        if title and title in text:
            return True
    return False


def _check_note(
    note: Any,
    index: int,
    section_map: dict[str, dict[str, Any]],
    unit_map: dict[str, dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    seen_ids: set[str],
    per_section: dict[str, int],
) -> dict[str, Any] | None:
    if not isinstance(note, dict):
        errors.append(f"notes[{index}] 必须是对象")
        return None
    nid = _identifier(note.get("id"), f"notes[{index}].id", errors)
    if nid:
        if nid in seen_ids:
            errors.append(f"notes id 重复：{nid}")
        seen_ids.add(nid)
    label = nid or f"notes[{index}]"
    type_ = note.get("type")
    if type_ not in NOTE_TYPES:
        errors.append(f"{label}.type 非法：{type_}")
        type_ = ""
    origin = note.get("origin")
    if origin not in NOTE_ORIGINS:
        errors.append(f"{label}.origin 非法：{origin}")
        origin = ""
    section_id = _identifier(note.get("section"), f"{label}.section", errors)
    if section_id and section_id not in section_map:
        errors.append(f"{label}.section 不在本章章节模型里：{section_id}")
    units = _refs(note.get("units"), f"{label}.units", errors)
    block_refs = _refs(note.get("blocks"), f"{label}.blocks", errors)
    for unit_id in units:
        if unit_id not in unit_map:
            errors.append(f"{label}.units 引用了不存在的 unit：{unit_id}")
    for block_id in block_refs:
        if block_id not in block_map:
            errors.append(f"{label}.blocks 引用了不存在的 block：{block_id}")
    if not units and not block_refs:
        errors.append(f"{label} 至少需要一个 units 或 blocks 锚点")
    text = _text(note.get("text"), f"{label}.text", errors)
    if text and not MIN_NOTE <= len(text) <= MAX_NOTE:
        errors.append(f"{label}.text 长度 {len(text)} 不在 {MIN_NOTE}–{MAX_NOTE} 字")
    scope = _text(note.get("source_scope"), f"{label}.source_scope", errors)
    if scope and not _valid_scope(scope):
        errors.append(f"{label}.source_scope 只能写「本章」或「本书 <节或章>」：{scope}")
    quote = ""
    if note.get("quote") is not None:
        quote = _text(note.get("quote"), f"{label}.quote", errors)
        if quote:
            if not block_refs:
                errors.append(f"{label}.quote 需要至少一个 blocks 锚点")
            elif not any(bid in block_map and _quote_matches(quote, block_map[bid]) for bid in block_refs):
                errors.append(f"{label}.quote 不是引用块的逐字子串")
    if text and type_ == "intertext" and not _names_place(text, section_map):
        errors.append(f"{label}.text 是互文注，但没有点名章节或小节")
    if text and "已核验" in text:
        errors.append(f"{label}.text 不能写「已核验」")
    if section_id:
        per_section[section_id] = per_section.get(section_id, 0) + 1
        if per_section[section_id] > MAX_NOTES_PER_SECTION:
            errors.append(f"{section_id} 的论证注超过 {MAX_NOTES_PER_SECTION} 条")
    clean: dict[str, Any] = {
        "id": nid,
        "type": type_,
        "section": section_id,
        "units": units,
        "blocks": block_refs,
        "text": text,
        "origin": origin,
        "source_scope": scope,
    }
    if quote:
        clean["quote"] = quote
    if not units and not block_refs:
        warnings.append(f"{label} 没有有效锚点")
    return clean


def _check_chain(
    chain: Any,
    index: int,
    section_map: dict[str, dict[str, Any]],
    unit_map: dict[str, dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    seen_ids: set[str],
    per_section: dict[str, int],
) -> dict[str, Any] | None:
    if not isinstance(chain, dict):
        errors.append(f"chains[{index}] 必须是对象")
        return None
    cid = _identifier(chain.get("id"), f"chains[{index}].id", errors)
    if cid:
        if cid in seen_ids:
            errors.append(f"chains id 重复：{cid}")
        seen_ids.add(cid)
    label = cid or f"chains[{index}]"
    section_id = _identifier(chain.get("section"), f"{label}.section", errors)
    if section_id and section_id not in section_map:
        errors.append(f"{label}.section 不在本章章节模型里：{section_id}")
    family = chain.get("family")
    if family != "argument":
        errors.append(f"{label}.family 必须是 argument")
    origin = chain.get("origin")
    if origin != "derived":
        errors.append(f"{label}.origin 必须是 derived")
    question = _text(chain.get("question"), f"{label}.question", errors)
    caption = _text(chain.get("caption"), f"{label}.caption", errors)
    units = _refs(chain.get("units"), f"{label}.units", errors)
    for unit_id in units:
        if unit_id not in unit_map:
            errors.append(f"{label}.units 引用了不存在的 unit：{unit_id}")
    reading_note = _optional_text(chain.get("reading_note"), f"{label}.reading_note", errors)
    fallback = _optional_text(chain.get("fallback"), f"{label}.fallback", errors)
    data = chain.get("data")
    if not isinstance(data, dict):
        errors.append(f"{label}.data 必须是对象")
        data = {}
    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        errors.append(f"{label}.data.nodes 必须是非空数组")
        nodes_raw = []
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for node_index, node in enumerate(nodes_raw):
        if not isinstance(node, dict):
            errors.append(f"{label}.data.nodes[{node_index}] 必须是对象")
            continue
        node_id = _identifier(node.get("id"), f"{label}.data.nodes[{node_index}].id", errors)
        if node_id:
            if node_id in node_ids:
                errors.append(f"{label}.data.nodes id 重复：{node_id}")
            node_ids.add(node_id)
        node_label = _text(node.get("label"), f"{label}.data.nodes[{node_index}].label", errors)
        clean_node: dict[str, Any] = {"id": node_id, "label": node_label}
        if node.get("unit") is not None:
            unit_id = _identifier(node.get("unit"), f"{label}.data.nodes[{node_index}].unit", errors)
            if unit_id and unit_id not in unit_map:
                errors.append(f"{label}.data.nodes[{node_index}].unit 不存在：{unit_id}")
            if unit_id:
                clean_node["unit"] = unit_id
        if node.get("block") is not None:
            block_id = _identifier(node.get("block"), f"{label}.data.nodes[{node_index}].block", errors)
            if block_id and block_id not in block_map:
                errors.append(f"{label}.data.nodes[{node_index}].block 不存在：{block_id}")
            if block_id:
                clean_node["block"] = block_id
        nodes.append(clean_node)
    edges_raw = data.get("edges", [])
    if not isinstance(edges_raw, list):
        errors.append(f"{label}.data.edges 必须是数组")
        edges_raw = []
    edges: list[dict[str, str]] = []
    for edge_index, edge in enumerate(edges_raw):
        if not isinstance(edge, dict):
            errors.append(f"{label}.data.edges[{edge_index}] 必须是对象")
            continue
        from_id = _identifier(edge.get("from"), f"{label}.data.edges[{edge_index}].from", errors)
        to_id = _identifier(edge.get("to"), f"{label}.data.edges[{edge_index}].to", errors)
        if from_id and from_id not in node_ids:
            errors.append(f"{label}.data.edges[{edge_index}].from 不存在：{from_id}")
        if to_id and to_id not in node_ids:
            errors.append(f"{label}.data.edges[{edge_index}].to 不存在：{to_id}")
        clean_edge = {"from": from_id, "to": to_id}
        if edge.get("label") is not None:
            edge_label = _optional_text(edge.get("label"), f"{label}.data.edges[{edge_index}].label", errors)
            if edge_label:
                clean_edge["label"] = edge_label
        edges.append(clean_edge)
    if not edges and not fallback and not reading_note:
        warnings.append(f"{label} 没有边也没有文字说明")
    clean_chain = {
        "id": cid,
        "family": family,
        "section": section_id,
        "question": question,
        "units": units,
        "origin": origin,
        "caption": caption,
        "reading_note": reading_note,
        "data": {"nodes": nodes, "edges": edges},
        "fallback": fallback,
    }
    if section_id:
        per_section[section_id] = per_section.get(section_id, 0) + 1
        if per_section[section_id] > 1:
            errors.append(f"{section_id} 的论证链超过 1 条")
    for value in (question, caption, reading_note, fallback):
        if value and "已核验" in value:
            errors.append(f"{label} 不能写「已核验」")
    return clean_chain


def _open_questions(payload: Any, errors: list[str]) -> list[dict[str, str]]:
    raw = payload.get("open_questions") if isinstance(payload, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append("open_questions 必须是数组")
        return []
    if len(raw) > 3:
        errors.append("open_questions 最多 3 条")
    result: list[dict[str, str]] = []
    for index, item in enumerate(raw[:3]):
        if isinstance(item, str):
            text, why, anchor_id = item.strip(), "", ""
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            why = str(item.get("why") or "").strip()
            anchor_id = str(item.get("anchor") or "").strip()
        else:
            errors.append(f"open_questions[{index}] 必须是字符串或对象")
            continue
        if not text:
            errors.append(f"open_questions[{index}].text 不能为空")
            continue
        entry = {"text": text}
        if why:
            entry["why"] = why
        if anchor_id:
            entry["anchor"] = anchor_id
        result.append(entry)
    return result


def _checks(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return None, ["深读层顶层必须是对象"], warnings
    if payload.get("schema") != DEEP_SCHEMA_VERSION:
        errors.append(f"schema 必须是 {DEEP_SCHEMA_VERSION}")
    if payload.get("prompt_version") != DEEP_PROMPT_VERSION:
        errors.append(f"prompt_version 必须是 {DEEP_PROMPT_VERSION}")

    sections = model.get("sections") or []
    units = model.get("units") or []
    section_map = {
        section.get("id"): section
        for section in sections
        if isinstance(section, dict) and isinstance(section.get("id"), str) and section.get("id")
    }
    unit_map = {
        unit.get("id"): unit
        for unit in units
        if isinstance(unit, dict) and isinstance(unit.get("id"), str) and unit.get("id")
    }
    block_map = _block_map(blocks)

    notes_raw = payload.get("notes")
    if not isinstance(notes_raw, list):
        errors.append("notes 必须是数组")
        notes_raw = []
    if not 1 <= len(notes_raw) <= MAX_NOTES:
        errors.append(f"notes 必须有 1–{MAX_NOTES} 条，当前 {len(notes_raw)} 条")
    notes: list[dict[str, Any]] = []
    note_ids: set[str] = set()
    notes_per_section: dict[str, int] = {}
    for index, note in enumerate(notes_raw):
        clean = _check_note(
            note,
            index,
            section_map,
            unit_map,
            block_map,
            errors,
            warnings,
            note_ids,
            notes_per_section,
        )
        if clean is not None:
            notes.append(clean)

    chains_raw = payload.get("chains", [])
    if not isinstance(chains_raw, list):
        errors.append("chains 必须是数组")
        chains_raw = []
    chains: list[dict[str, Any]] = []
    chain_ids: set[str] = set()
    chains_per_section: dict[str, int] = {}
    for index, chain in enumerate(chains_raw):
        clean = _check_chain(
            chain,
            index,
            section_map,
            unit_map,
            block_map,
            errors,
            warnings,
            chain_ids,
            chains_per_section,
        )
        if clean is not None:
            chains.append(clean)

    clean = {"schema": DEEP_SCHEMA_VERSION, "prompt_version": DEEP_PROMPT_VERSION, "notes": notes, "chains": chains}
    questions = _open_questions(payload, errors)
    if questions:
        clean["open_questions"] = questions
    return clean, (errors), warnings


def validate_deep(payload: Any, model: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """校验深读载荷，返回规范化副本；不合法时抛 ``DeepError``。"""
    clean, errors, _warnings = _checks(payload, model, blocks)
    if errors:
        raise DeepError("；".join(errors))
    return copy.deepcopy(clean)


def audit_deep(payload: Any, model: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """G13 审计：origin、锚点、数量、书外事实与论证链数字。"""
    clean, errors, warnings = _checks(payload, model, blocks)
    if clean is not None:
        for chain in clean.get("chains") or []:
            report = audit_figures([chain], model, blocks)
            errors.extend(f"{chain.get('id') or '论证链'}: {error}" for error in report.get("errors") or [])
            warnings.extend(f"{chain.get('id') or '论证链'}: {warning}" for warning in report.get("warnings") or [])
    status = "fail" if errors else ("warn" if warnings else "pass")
    return {"status": status, "errors": errors, "warnings": warnings}


def _block_view(block: dict[str, Any]) -> dict[str, Any]:
    view = {"id": block.get("id"), "kind": block.get("kind"), "text": block.get("text") or ""}
    for key in ("level", "items", "rows", "head", "rel", "marker"):
        if key in block:
            view[key] = block[key]
    return view


def _messages(
    book: dict[str, Any],
    chapter: dict[str, Any],
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "book": {
            key: book.get(key) for key in ("uid", "title", "author", "subjects", "publisher", "year") if key in book
        },
        "chapter": {
            key: chapter.get(key) for key in ("uid", "n", "title", "source_sha256", "stem", "ordinal") if key in chapter
        },
        "chapter_model": model,
        "blocks": [_block_view(block) for block in blocks],
        "reminder": "只使用本章 blocks 与 chapter_model 的 id；notes 每条 80–200 字；chains 每节最多 1 条。",
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _clip(text: Any, limit: int = 600) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "…"


def generate_deep(
    client: Any,
    book: dict[str, Any],
    chapter: dict[str, Any],
    chapter_model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    model: str | None = None,
    effort: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成并校验深读层；JSON 或校验失败时重试一次。"""
    messages = _messages(book, chapter, chapter_model, blocks)
    json_attempts = 0
    provider_retried = False
    while True:
        try:
            payload, usage = client.complete_json(messages, model=model, effort=effort)
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("顶层不是对象", "", 0)
            payload = dict(payload)
            payload.setdefault("schema", DEEP_SCHEMA_VERSION)
            payload.setdefault("prompt_version", DEEP_PROMPT_VERSION)
            clean = validate_deep(payload, chapter_model, blocks)
            actual_model = model or getattr(client, "last_model", None) or getattr(client, "model", None)
            return clean, {"prompt_version": DEEP_PROMPT_VERSION, "model": actual_model, "usage": usage}
        except json.JSONDecodeError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise DeepError(f"连续 {MAX_JSON_RETRIES + 1} 次没有得到合法 JSON：{exc}") from exc
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"上一次输出不是合法 JSON 对象：{exc}。请只输出完整 JSON，不要围栏或解释。",
                },
            ]
        except DeepError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise DeepError(f"连续 {MAX_JSON_RETRIES + 1} 次未通过深读校验：{_clip(exc)}") from exc
            messages = [
                *messages,
                {"role": "user", "content": f"上一次输出未通过深读校验：{_clip(exc)}。请修正后只输出完整 JSON。"},
            ]
        except ModelProviderError as exc:
            if provider_retried:
                raise DeepError(f"模型提供方连续失败：{exc}") from exc
            provider_retried = True
            messages = [*messages, {"role": "user", "content": "上一次调用失败，请重试并只输出 JSON 对象。"}]
