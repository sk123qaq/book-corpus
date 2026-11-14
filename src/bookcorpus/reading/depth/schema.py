"""章节结构模型：类型、严格校验与规范化副本。

模型只负责提出结构，档位处置由 :mod:`disposition` 的纯函数决定。这里的
校验不碰磁盘，也不调用模型；输入保持不变，返回值是可直接落盘的深拷贝。
"""

import copy

from bookcorpus.reading.textnorm import normalize_verbatim

SCHEMA_VERSION = 1
PROMPT_VERSION = "chapter-model-v1"

KINDS = (
    "definition",
    "claim",
    "conclusion",
    "condition",
    "risk",
    "example",
    "evidence",
    "context",
    "transition",
)
IMPORTANCES = ("core", "support", "detail")
RELATION_TYPES = ("supports", "contrasts", "causes", "exemplifies", "qualifies")
FIGURE_FAMILIES = ("argument", "matrix", "tree", "flow", "timeline", "network", "quantity")
FIGURE_ORIGINS = ("source", "derived", "external")
DISPOSITIONS = ("verbatim", "condensed", "indexed", "omitted")


class ChapterModelError(ValueError):
    """chapter_model 不满足结构、引文或守恒约束。"""

    kind = "schema"

    def __init__(self, message, *, payload=None):
        super().__init__(message)
        self.payload = payload


def _fail(message):
    raise ChapterModelError(message)


def _text(value, name, *, allow_empty=False):
    if not isinstance(value, str):
        _fail(f"{name} 必须是字符串")
    if not allow_empty and not value.strip():
        _fail(f"{name} 不能为空")
    return value


def _list(value, name):
    if not isinstance(value, list):
        _fail(f"{name} 必须是数组")
    return value


def _object(value, name):
    if not isinstance(value, dict):
        _fail(f"{name} 必须是对象")
    return value


def _string_list(value, name, *, nonempty=False):
    items = _list(value, name)
    if nonempty and not items:
        _fail(f"{name} 不能为空")
    for index, item in enumerate(items):
        _text(item, f"{name}[{index}]")
    return items


def _block_map(blocks):
    result = {}
    for block in blocks or []:
        block_id = block.get("id")
        if not isinstance(block_id, str) or not block_id:
            _fail("正文块缺少 id")
        if block_id in result:
            _fail(f"正文块 id 重复：{block_id}")
        result[block_id] = block
    return result


def _quote(quote, index, block_map, source_blocks, prefix):
    quote = _object(quote, f"{prefix}.quotes[{index}]")
    block_id = quote.get("block")
    if block_id not in block_map:
        _fail(f"{prefix}.quotes[{index}] 引用了不存在的块：{block_id}")
    if block_id not in source_blocks:
        _fail(f"{prefix}.quotes[{index}] 的块 {block_id} 不在 source_blocks 里")
    text = _text(quote.get("text"), f"{prefix}.quotes[{index}].text")
    block_text = block_map[block_id].get("text") or ""
    if normalize_verbatim(text) not in normalize_verbatim(block_text):
        _fail(f"{prefix}.quotes[{index}] 不是块 {block_id} 的逐字子串")
    return {"block": block_id, "text": text}


def _hard_info(unit, prefix, quotes):
    """hard_info 只接受布尔值；可选 hard_info_note 记录精确硬事实。"""
    if "hard_info" not in unit:
        _fail(f"{prefix} 缺少 hard_info")
    value = unit.get("hard_info")
    if not isinstance(value, bool):
        _fail(f"{prefix}.hard_info 必须是布尔值")
    note = unit.get("hard_info_note")
    if note is not None:
        note = _text(note, f"{prefix}.hard_info_note")
        if not any(normalize_verbatim(note) in normalize_verbatim(quote["text"]) for quote in quotes):
            _fail(f"{prefix}.hard_info_note 不是任一 quote 的逐字子串")
    if value and not quotes:
        _fail(f"{prefix} 标了 hard_info 却没有 quotes")
    hard_texts = [note] if note is not None else [quote["text"] for quote in quotes]
    for hard_text in hard_texts:
        if not any(normalize_verbatim(hard_text) in normalize_verbatim(quote["text"]) for quote in quotes):
            _fail(f"{prefix}.hard_info 不是任一 quote 的逐字子串")
    return value, note


def _relation(relation, index, unit_ids, prefix):
    relation = _object(relation, f"{prefix}.relations[{index}]")
    kind = _text(relation.get("type"), f"{prefix}.relations[{index}].type")
    if kind not in RELATION_TYPES:
        _fail(f"{prefix}.relations[{index}] 类型非法：{kind}")
    target = relation.get("to", relation.get("target"))
    target = _text(target, f"{prefix}.relations[{index}].to")
    if target not in unit_ids:
        _fail(f"{prefix}.relations[{index}] 指向不存在的单元：{target}")
    return {"type": kind, "to": target}


def _unit(unit, index, block_map, unit_ids, section_of):
    prefix = f"units[{index}]"
    unit = _object(unit, prefix)
    unit_id = _text(unit.get("id"), prefix + ".id")
    kind = _text(unit.get("kind"), prefix + ".kind")
    if kind not in KINDS:
        _fail(f"{prefix}.kind 非法：{kind}")
    if unit.get("section") is not None:
        section = _text(unit.get("section"), prefix + ".section")
        expected = section_of.get(unit_id)
        if expected is None:
            _fail(f"{prefix} 不属于任何 section")
        if section != expected:
            _fail(f"{prefix}.section 与 section.unit_ids 不一致")
    else:
        section = section_of.get(unit_id)
        if section is None:
            _fail(f"{prefix} 不属于任何 section")
    source_blocks = _string_list(unit.get("source_blocks"), prefix + ".source_blocks", nonempty=True)
    for block_id in source_blocks:
        if block_id not in block_map:
            _fail(f"{prefix}.source_blocks 引用了不存在的块：{block_id}")
    if len(set(source_blocks)) != len(source_blocks):
        _fail(f"{prefix}.source_blocks 有重复块")
    quotes = [
        _quote(quote, quote_index, block_map, source_blocks, prefix)
        for quote_index, quote in enumerate(_list(unit.get("quotes"), prefix + ".quotes"))
    ]
    summary = _text(unit.get("summary"), prefix + ".summary")
    importance = _text(unit.get("importance"), prefix + ".importance")
    if importance not in IMPORTANCES:
        _fail(f"{prefix}.importance 非法：{importance}")
    hard_info, hard_info_note = _hard_info(unit, prefix, quotes)
    duplicate_of = unit.get("duplicate_of")
    if duplicate_of is not None:
        duplicate_of = _text(duplicate_of, prefix + ".duplicate_of")
        if duplicate_of == unit_id:
            _fail(f"{prefix} 不能 duplicate_of 自己")
        if duplicate_of not in unit_ids:
            _fail(f"{prefix}.duplicate_of 指向不存在的单元：{duplicate_of}")
        if importance == "core":
            _fail(f"{prefix} 是 core，不能标 duplicate_of")
    relations = [
        _relation(relation, relation_index, unit_ids, prefix)
        for relation_index, relation in enumerate(_list(unit.get("relations") or [], prefix + ".relations"))
    ]
    item = copy.deepcopy(unit)
    item.update(
        {
            "id": unit_id,
            "kind": kind,
            "section": section,
            "source_blocks": list(source_blocks),
            "quotes": quotes,
            "summary": summary,
            "importance": importance,
            "hard_info": hard_info,
            "relations": relations,
            "duplicate_of": duplicate_of,
        }
    )
    if hard_info_note is None:
        item.pop("hard_info_note", None)
    else:
        item["hard_info_note"] = hard_info_note
    return item


def _section(section, index):
    prefix = f"sections[{index}]"
    section = _object(section, prefix)
    section_id = _text(section.get("id"), prefix + ".id")
    title = _text(section.get("title"), prefix + ".title")
    unit_ids = _string_list(section.get("unit_ids", section.get("units")), prefix + ".unit_ids")
    if len(set(unit_ids)) != len(unit_ids):
        _fail(f"{prefix}.unit_ids 有重复单元")
    lead = section.get("lead", "")
    if lead is None:
        lead = ""
    _text(lead, prefix + ".lead", allow_empty=True)
    item = copy.deepcopy(section)
    item.update({"id": section_id, "title": title, "unit_ids": list(unit_ids), "lead": lead})
    return item


def _term(term, index, block_map):
    prefix = f"terms[{index}]"
    term = _object(term, prefix)
    name = _text(term.get("term", term.get("name")), prefix + ".term")
    definition = _text(term.get("definition"), prefix + ".definition")
    block_id = _text(term.get("source_block", term.get("block_id")), prefix + ".source_block")
    if block_id not in block_map:
        _fail(f"{prefix}.source_block 引用了不存在的块：{block_id}")
    quote = _text(term.get("quote"), prefix + ".quote")
    if normalize_verbatim(quote) not in normalize_verbatim(block_map[block_id].get("text") or ""):
        _fail(f"{prefix}.quote 不是块 {block_id} 的逐字子串")
    item = copy.deepcopy(term)
    item.update({"term": name, "definition": definition, "source_block": block_id, "quote": quote})
    item.pop("block_id", None)
    return item


def _figure(figure, index, unit_ids):
    prefix = f"figures[{index}]"
    figure = _object(figure, prefix)
    figure_id = _text(figure.get("id"), prefix + ".id")
    family = _text(figure.get("family"), prefix + ".family")
    if family not in FIGURE_FAMILIES:
        _fail(f"{prefix}.family 非法：{family}")
    question = _text(figure.get("question"), prefix + ".question")
    units = _string_list(figure.get("units"), prefix + ".units")
    for unit_id in units:
        if unit_id not in unit_ids:
            _fail(f"{prefix}.units 引用了不存在的单元：{unit_id}")
    origin = _text(figure.get("origin"), prefix + ".origin")
    if origin not in FIGURE_ORIGINS:
        _fail(f"{prefix}.origin 非法：{origin}")
    caption = _text(figure.get("caption", ""), prefix + ".caption", allow_empty=True)
    reading_note = _text(figure.get("reading_note", ""), prefix + ".reading_note", allow_empty=True)
    data = figure.get("data", {})
    if data is None:
        data = {}
    _object(data, prefix + ".data")
    fallback = figure.get("fallback", "")
    if fallback is not None and not isinstance(fallback, str):
        _fail(f"{prefix}.fallback 必须是字符串")
    item = copy.deepcopy(figure)
    item.update(
        {
            "id": figure_id,
            "family": family,
            "question": question,
            "units": list(units),
            "origin": origin,
            "caption": caption,
            "reading_note": reading_note,
            "data": data,
            "fallback": fallback or "",
        }
    )
    return item


def _non_semantic(entry, index, block_map):
    prefix = f"non_semantic[{index}]"
    entry = _object(entry, prefix)
    block_id = _text(entry.get("block"), prefix + ".block")
    if block_id not in block_map:
        _fail(f"{prefix}.block 引用了不存在的块：{block_id}")
    reason = _text(entry.get("reason"), prefix + ".reason")
    return {"block": block_id, "reason": reason}


def _fold(entry, index, block_map):
    prefix = f"folds[{index}]"
    entry = _object(entry, prefix)
    block_id = _text(entry.get("block"), prefix + ".block")
    if block_id not in block_map:
        _fail(f"{prefix}.block 引用了不存在的块：{block_id}")
    summary = _text(entry.get("summary", ""), prefix + ".summary", allow_empty=True)
    reason = _text(entry.get("reason", ""), prefix + ".reason", allow_empty=True)
    return {"block": block_id, "summary": summary, "reason": reason}


def _skim_hint(value):
    """可选的速览提示：模型可主动判定本章不适合编译。"""
    value = _object(value, "skim")
    if "supported" not in value:
        _fail("skim 缺少 supported")
    supported = value.get("supported")
    if not isinstance(supported, bool):
        _fail("skim.supported 必须是布尔值")
    reason = value.get("reason", "")
    if reason is None:
        reason = ""
    _text(reason, "skim.reason", allow_empty=supported)
    if not supported and not reason.strip():
        _fail("skim.supported=false 时必须给 reason")
    item = copy.deepcopy(value)
    item.update({"supported": supported, "reason": reason})
    return item


def validate_chapter_model(payload, blocks, *, uid=None, n=None, source_sha256=None):
    """校验章节模型并返回规范化副本；不修改 ``payload``。"""
    payload = _object(payload, "chapter_model")
    if "tiers" in payload:
        _fail("chapter_model 不允许包含 tiers；档位由确定性计划器生成")
    required = (
        "schema",
        "uid",
        "n",
        "source_sha256",
        "prompt_version",
        "units",
        "sections",
        "terms",
        "figures",
        "non_semantic",
        "folds",
    )
    for key in required:
        if key not in payload:
            _fail(f"缺少顶层字段：{key}")
    schema = payload.get("schema")
    if schema != SCHEMA_VERSION:
        _fail(f"schema 必须是 {SCHEMA_VERSION}")
    block_map = _block_map(blocks)

    for key in ("units", "sections", "terms", "figures", "non_semantic"):
        _list(payload.get(key), key)
    units_raw = payload.get("units") or []
    sections_raw = payload.get("sections") or []
    terms_raw = payload.get("terms") or []
    figures_raw = payload.get("figures") or []
    non_semantic_raw = payload.get("non_semantic") or []
    folds_raw = payload.get("folds") or []

    unit_ids = []
    for index, unit in enumerate(units_raw):
        unit_id = _text(_object(unit, f"units[{index}]").get("id"), f"units[{index}].id")
        unit_ids.append(unit_id)
    if len(set(unit_ids)) != len(unit_ids):
        _fail("unit id 重复")
    unit_id_set = set(unit_ids)

    sections = [_section(section, index) for index, section in enumerate(sections_raw)]
    section_ids = [section["id"] for section in sections]
    if len(set(section_ids)) != len(section_ids):
        _fail("section id 重复")
    section_of = {}
    for section in sections:
        for unit_id in section["unit_ids"]:
            if unit_id not in unit_id_set:
                _fail(f"section {section['id']} 引用了不存在的单元：{unit_id}")
            if unit_id in section_of:
                _fail(f"单元 {unit_id} 出现在多个 section")
            section_of[unit_id] = section["id"]
    missing = [unit_id for unit_id in unit_ids if unit_id not in section_of]
    if missing:
        _fail("section 未覆盖单元：" + "、".join(missing[:8]))

    units = [_unit(unit, index, block_map, unit_id_set, section_of) for index, unit in enumerate(units_raw)]
    for unit in units:
        duplicate_of = unit.get("duplicate_of")
        if duplicate_of and any(item["id"] == duplicate_of and item.get("duplicate_of") for item in units):
            _fail(f"duplicate_of 不能指向另一个 duplicate：{duplicate_of}")

    terms = [_term(term, index, block_map) for index, term in enumerate(terms_raw)]
    figures = [_figure(figure, index, unit_id_set) for index, figure in enumerate(figures_raw)]
    figure_ids = [figure["id"] for figure in figures]
    if len(set(figure_ids)) != len(figure_ids):
        _fail("figure id 重复")

    non_semantic = [_non_semantic(entry, index, block_map) for index, entry in enumerate(non_semantic_raw)]
    non_semantic_blocks = [entry["block"] for entry in non_semantic]
    if len(set(non_semantic_blocks)) != len(non_semantic_blocks):
        _fail("non_semantic 有重复块")
    referenced = {block_id for unit in units for block_id in unit["source_blocks"]}
    overlap = referenced.intersection(non_semantic_blocks)
    if overlap:
        _fail("non_semantic 与单元 source_blocks 重叠：" + "、".join(sorted(overlap)[:8]))

    folds = [_fold(entry, index, block_map) for index, entry in enumerate(folds_raw)]

    model_uid = payload.get("uid")
    if uid is not None and model_uid is not None and model_uid != uid:
        _fail(f"uid 不一致：{model_uid} != {uid}")
    model_n = payload.get("n")
    if n is not None and model_n is not None:
        try:
            model_n = int(model_n)
        except (TypeError, ValueError):
            _fail("n 必须是整数")
        if model_n != int(n):
            _fail(f"n 不一致：{model_n} != {n}")
    elif model_n is not None:
        try:
            model_n = int(model_n)
        except (TypeError, ValueError):
            _fail("n 必须是整数")
    model_sha = payload.get("source_sha256", payload.get("source_sha"))
    if source_sha256 is not None and model_sha is not None and model_sha != source_sha256:
        _fail("source_sha256 不一致")
    prompt_version = payload.get("prompt_version")
    _text(prompt_version, "prompt_version")
    if prompt_version != PROMPT_VERSION:
        _fail(f"prompt_version 必须是 {PROMPT_VERSION}")

    result = copy.deepcopy(payload)
    if "skim" in payload:
        result["skim"] = _skim_hint(payload["skim"])
    result.update(
        {
            "schema": SCHEMA_VERSION,
            "uid": model_uid if model_uid is not None else uid,
            "n": model_n if model_n is not None else (int(n) if n is not None else None),
            "source_sha256": model_sha if model_sha is not None else source_sha256,
            "prompt_version": prompt_version,
            "units": units,
            "sections": sections,
            "terms": terms,
            "figures": figures,
            "non_semantic": non_semantic,
            "folds": folds,
        }
    )
    return result
