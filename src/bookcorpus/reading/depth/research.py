"""研究层：未检索议程。

没有检索提供方时，研究档不能编造外部事实，也不能把待查问题写成已核验结论。
本模块只产生 ``layers/research.json`` 的议程载荷：判断矩阵、分组笔记、开放问题
与建议查询。所有条目都必须能落回本章的 unit / block 锚点。

以后接入提供方时，``searched=True`` 的校验分支预留了 citation 约束，但当前
生成器固定走 no-provider 路径。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..model import ModelProviderError
from ..textnorm import normalize_ws
from .standard import _block_map

RESEARCH_SCHEMA_VERSION = 1
RESEARCH_PROMPT_VERSION = "research-agenda-v1"
MIN_NOTES = 3
MAX_NOTES = 10
MIN_MATRIX = 5
MAX_MATRIX = 10
MIN_QUESTIONS = 3
MAX_QUESTIONS = 6
NOTE_TYPES = ("context", "later", "critique", "application", "open")
VERDICTS = ("still", "changed", "caution", "verify")
NOTE_ORIGINS = ("model", "external")
CERTAINTIES = ("unverified", "verified")
GENERIC_PHRASES = ("值得思考", "有待研究", "值得关注")
CURRENT_NUMBER_RE = re.compile(r"(?:(?:19|20)\d{2}\s*年?|\d+(?:\.\d+)?\s*%|百分之[一二三四五六七八九十百千万零两\d]+)")
MAX_JSON_RETRIES = 1

SYSTEM_PROMPT = "\n".join(
    [
        "你是书库的研究编辑。给定一章的章节模型与原文块，请写一份未检索的研究议程，只输出一个 JSON 对象。",
        "",
        "硬约束：",
        "1. 顶层字段为 schema、prompt_version、searched、provider、notes、matrix、questions。"
        "schema 写 1，prompt_version 固定写 research-agenda-v1。",
        "2. 当前没有检索提供方：searched 必须写 false，provider 必须写 none。"
        "不得写外部事实，不得写「已核验」，不得写当前年份、百分比等外部数字。",
        "3. notes 是 3 到 10 条议程笔记。每条字段为 id、type、section、related_units、blocks、text、"
        "origin、certainty、citations。type 只能是 context/later/critique/application/open；"
        "origin 固定写 model，certainty 固定写 unverified，citations 固定为空数组。",
        "4. 每条 note 至少给一个 related_units 或 blocks 锚点，所有 id 必须来自本章。"
        "text 写具体要查什么、为什么值得查，不要写空泛套话。",
        "5. matrix 是 5 到 10 行判断矩阵。每行字段为 id、claim、verdict、change、needed、units、blocks。"
        "verdict 只能是 still/changed/caution/verify；claim、change、needed 都必须写具体内容。"
        "每行至少一个 unit 或 block 锚点。",
        "6. questions 是 3 到 6 个开放问题。每个字段为 id、text、why、query、units。"
        "text/why/query 必须具体；禁止「值得思考」「有待研究」「值得关注」这类空话。",
        "7. 不要输出外部链接、书名、作者、年份、统计数字或任何未给出的材料。不要输出 markdown 围栏或解释。",
        "",
        "输出只包含 JSON。",
    ]
)


class ResearchError(ValueError):
    """研究议程不满足结构、锚点或 no-provider 约束。"""


def _text(value: Any, name: str, errors: list[str], *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        errors.append(f"{name} 必须是字符串")
        return ""
    text = value.strip()
    if not allow_empty and not text:
        errors.append(f"{name} 不能为空")
    return text


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


def _check_refs(
    units: list[str],
    blocks: list[str],
    label: str,
    unit_map: dict[str, dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
    errors: list[str],
    *,
    require_anchor: bool,
) -> None:
    for unit_id in units:
        if unit_id not in unit_map:
            errors.append(f"{label}.units 引用了不存在的 unit：{unit_id}")
    for block_id in blocks:
        if block_id not in block_map:
            errors.append(f"{label}.blocks 引用了不存在的 block：{block_id}")
    if require_anchor and not units and not blocks:
        errors.append(f"{label} 至少需要一个 units 或 blocks 锚点")


def _check_citations(value: Any, label: str, errors: list[str]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label}.citations 必须是数组")
        return []
    citations: list[dict[str, str]] = []
    for index, citation in enumerate(value):
        if not isinstance(citation, dict):
            errors.append(f"{label}.citations[{index}] 必须是对象")
            continue
        url = citation.get("url")
        quote = citation.get("quote")
        clean: dict[str, str] = {}
        if isinstance(url, str) and url.strip():
            clean["url"] = url.strip()
        if isinstance(quote, str) and quote.strip():
            clean["quote"] = quote.strip()
        if not clean:
            errors.append(f"{label}.citations[{index}] 至少要有 url 或 quote")
            continue
        citations.append(clean)
    return citations


def _check_generic(text: str, label: str, errors: list[str]) -> None:
    for phrase in GENERIC_PHRASES:
        if phrase in text:
            errors.append(f"{label} 太空泛，不能写「{phrase}」")
            return


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


def _source_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(_block_text(block) for block in blocks or [])


def _check_no_provider_text(text: str, label: str, errors: list[str], source_text: str = "") -> None:
    if "已核验" in text:
        errors.append(f"{label} 不能写「已核验」")
    match = CURRENT_NUMBER_RE.search(text)
    if match and normalize_ws(match.group(0)) not in normalize_ws(source_text):
        errors.append(f"{label} 不能写当前外部数字：{match.group(0)}")


def _check_note(
    note: Any,
    index: int,
    section_map: dict[str, dict[str, Any]],
    unit_map: dict[str, dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
    searched: bool,
    errors: list[str],
    seen_ids: set[str],
    source_text: str,
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
    section_id = _identifier(note.get("section"), f"{label}.section", errors)
    if section_id and section_id not in section_map:
        errors.append(f"{label}.section 不在本章章节模型里：{section_id}")
    units = _refs(note.get("related_units"), f"{label}.related_units", errors)
    blocks = _refs(note.get("blocks"), f"{label}.blocks", errors)
    _check_refs(units, blocks, label, unit_map, block_map, errors, require_anchor=True)
    text = _text(note.get("text"), f"{label}.text", errors)
    origin = note.get("origin")
    certainty = note.get("certainty")
    if origin not in NOTE_ORIGINS:
        errors.append(f"{label}.origin 非法：{origin}")
        origin = ""
    if certainty not in CERTAINTIES:
        errors.append(f"{label}.certainty 非法：{certainty}")
        certainty = ""
    citations = _check_citations(note.get("citations"), label, errors)
    if not searched:
        if origin and origin != "model":
            errors.append(f"{label}.origin 在未检索时必须是 model")
        if certainty and certainty != "unverified":
            errors.append(f"{label}.certainty 在未检索时必须是 unverified")
        if citations:
            errors.append(f"{label}.citations 在未检索时必须为空")
        if text:
            _check_no_provider_text(text, label, errors, source_text)
    else:
        if origin == "external" and not citations:
            errors.append(f"{label} 是外部笔记，必须给 citation 的 quote 或 url")
        if certainty == "verified" and not citations:
            errors.append(f"{label} 标为 verified，必须给 citation")
    return {
        "id": nid,
        "type": type_,
        "section": section_id,
        "related_units": units,
        "blocks": blocks,
        "text": text,
        "origin": origin,
        "certainty": certainty,
        "citations": citations,
    }


def _check_matrix(
    row: Any,
    index: int,
    unit_map: dict[str, dict[str, Any]],
    block_map: dict[str, dict[str, Any]],
    searched: bool,
    errors: list[str],
    seen_ids: set[str],
    source_text: str,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        errors.append(f"matrix[{index}] 必须是对象")
        return None
    rid = _identifier(row.get("id"), f"matrix[{index}].id", errors)
    if rid:
        if rid in seen_ids:
            errors.append(f"matrix id 重复：{rid}")
        seen_ids.add(rid)
    label = rid or f"matrix[{index}]"
    claim = _text(row.get("claim"), f"{label}.claim", errors)
    verdict = row.get("verdict")
    if verdict not in VERDICTS:
        errors.append(f"{label}.verdict 非法：{verdict}")
        verdict = ""
    change = _text(row.get("change"), f"{label}.change", errors)
    needed = _text(row.get("needed"), f"{label}.needed", errors)
    units = _refs(row.get("units"), f"{label}.units", errors)
    blocks = _refs(row.get("blocks"), f"{label}.blocks", errors)
    _check_refs(units, blocks, label, unit_map, block_map, errors, require_anchor=True)
    if not searched:
        for field, value in (("claim", claim), ("change", change), ("needed", needed)):
            if value:
                _check_no_provider_text(value, f"{label}.{field}", errors, source_text)
    return {
        "id": rid,
        "claim": claim,
        "verdict": verdict,
        "change": change,
        "needed": needed,
        "units": units,
        "blocks": blocks,
    }


def _check_question(
    question: Any,
    index: int,
    unit_map: dict[str, dict[str, Any]],
    searched: bool,
    errors: list[str],
    seen_ids: set[str],
    source_text: str,
) -> dict[str, Any] | None:
    if not isinstance(question, dict):
        errors.append(f"questions[{index}] 必须是对象")
        return None
    qid = _identifier(question.get("id"), f"questions[{index}].id", errors)
    if qid:
        if qid in seen_ids:
            errors.append(f"questions id 重复：{qid}")
        seen_ids.add(qid)
    label = qid or f"questions[{index}]"
    text = _text(question.get("text"), f"{label}.text", errors)
    why = _text(question.get("why"), f"{label}.why", errors)
    query = _text(question.get("query"), f"{label}.query", errors)
    units = _refs(question.get("units"), f"{label}.units", errors)
    for unit_id in units:
        if unit_id not in unit_map:
            errors.append(f"{label}.units 引用了不存在的 unit：{unit_id}")
    for field, value in (("text", text), ("why", why), ("query", query)):
        if value:
            _check_generic(value, f"{label}.{field}", errors)
            if not searched:
                _check_no_provider_text(value, f"{label}.{field}", errors, source_text)
    return {"id": qid, "text": text, "why": why, "query": query, "units": units}


def _checks(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
    searched: bool,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return None, ["研究层顶层必须是对象"], warnings
    if payload.get("schema") != RESEARCH_SCHEMA_VERSION:
        errors.append(f"schema 必须是 {RESEARCH_SCHEMA_VERSION}")
    if payload.get("prompt_version") != RESEARCH_PROMPT_VERSION:
        errors.append(f"prompt_version 必须是 {RESEARCH_PROMPT_VERSION}")
    if not searched:
        if payload.get("searched") is not False:
            errors.append("未连接检索提供方时 searched 必须是 false")
        if payload.get("provider") != "none":
            errors.append("未连接检索提供方时 provider 必须是 none")
    else:
        if payload.get("searched") is not True:
            errors.append("searched=True 时必须声明 searched: true")
        provider = payload.get("provider")
        if not isinstance(provider, str) or not provider.strip() or provider == "none":
            errors.append("searched=True 时必须给 provider")

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
    source_text = _source_text(blocks)

    notes_raw = payload.get("notes")
    if not isinstance(notes_raw, list):
        errors.append("notes 必须是数组")
        notes_raw = []
    if not MIN_NOTES <= len(notes_raw) <= MAX_NOTES:
        errors.append(f"notes 必须有 {MIN_NOTES}–{MAX_NOTES} 条，当前 {len(notes_raw)} 条")
    notes: list[dict[str, Any]] = []
    note_ids: set[str] = set()
    for index, note in enumerate(notes_raw):
        clean = _check_note(note, index, section_map, unit_map, block_map, searched, errors, note_ids, source_text)
        if clean is not None:
            notes.append(clean)

    matrix_raw = payload.get("matrix")
    if not isinstance(matrix_raw, list):
        errors.append("matrix 必须是数组")
        matrix_raw = []
    if not MIN_MATRIX <= len(matrix_raw) <= MAX_MATRIX:
        errors.append(f"matrix 必须有 {MIN_MATRIX}–{MAX_MATRIX} 行，当前 {len(matrix_raw)} 行")
    matrix: list[dict[str, Any]] = []
    matrix_ids: set[str] = set()
    for index, row in enumerate(matrix_raw):
        clean = _check_matrix(row, index, unit_map, block_map, searched, errors, matrix_ids, source_text)
        if clean is not None:
            matrix.append(clean)

    questions_raw = payload.get("questions")
    if not isinstance(questions_raw, list):
        errors.append("questions 必须是数组")
        questions_raw = []
    if not MIN_QUESTIONS <= len(questions_raw) <= MAX_QUESTIONS:
        errors.append(f"questions 必须有 {MIN_QUESTIONS}–{MAX_QUESTIONS} 条，当前 {len(questions_raw)} 条")
    questions: list[dict[str, Any]] = []
    question_ids: set[str] = set()
    for index, question in enumerate(questions_raw):
        clean = _check_question(question, index, unit_map, searched, errors, question_ids, source_text)
        if clean is not None:
            questions.append(clean)

    clean_payload = {
        "schema": RESEARCH_SCHEMA_VERSION,
        "prompt_version": RESEARCH_PROMPT_VERSION,
        "searched": searched,
        "provider": payload.get("provider") if searched else "none",
        "notes": notes,
        "matrix": matrix,
        "questions": questions,
    }
    return clean_payload, errors, warnings


def validate_research(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    searched: bool = False,
) -> dict[str, Any]:
    """校验研究议程，返回规范化副本；不合法时抛 ``ResearchError``。"""
    clean, errors, _warnings = _checks(payload, model, blocks, searched)
    if errors:
        raise ResearchError("；".join(errors))
    return copy.deepcopy(clean)


def audit_research(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    searched: bool = False,
) -> dict[str, Any]:
    """G13 审计：origin、锚点、数量、citation 与 no-provider 约束。"""
    _clean, errors, warnings = _checks(payload, model, blocks, searched)
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
        "reminder": "searched 固定 false，provider 固定 none；notes 3–10，matrix 5–10，questions 3–6。",
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _clip(text: Any, limit: int = 600) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "…"


def generate_research(
    client: Any,
    book: dict[str, Any],
    chapter: dict[str, Any],
    chapter_model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    model: str | None = None,
    effort: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成并校验未检索研究议程；JSON 或校验失败时重试一次。"""
    messages = _messages(book, chapter, chapter_model, blocks)
    json_attempts = 0
    provider_retried = False
    while True:
        try:
            payload, usage = client.complete_json(messages, model=model, effort=effort)
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("顶层不是对象", "", 0)
            payload = dict(payload)
            payload.setdefault("schema", RESEARCH_SCHEMA_VERSION)
            payload.setdefault("prompt_version", RESEARCH_PROMPT_VERSION)
            payload.setdefault("searched", False)
            payload.setdefault("provider", "none")
            clean = validate_research(payload, chapter_model, blocks, searched=False)
            actual_model = model or getattr(client, "last_model", None) or getattr(client, "model", None)
            return clean, {"prompt_version": RESEARCH_PROMPT_VERSION, "model": actual_model, "usage": usage}
        except json.JSONDecodeError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise ResearchError(f"连续 {MAX_JSON_RETRIES + 1} 次没有得到合法 JSON：{exc}") from exc
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"上一次输出不是合法 JSON 对象：{exc}。请只输出完整 JSON，不要围栏或解释。",
                },
            ]
        except ResearchError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise ResearchError(f"连续 {MAX_JSON_RETRIES + 1} 次未通过研究校验：{_clip(exc)}") from exc
            messages = [
                *messages,
                {"role": "user", "content": f"上一次输出未通过研究校验：{_clip(exc)}。请修正后只输出完整 JSON。"},
            ]
        except ModelProviderError as exc:
            if provider_retried:
                raise ResearchError(f"模型提供方连续失败：{exc}") from exc
            provider_retried = True
            messages = [*messages, {"role": "user", "content": "上一次调用失败，请重试并只输出 JSON 对象。"}]
