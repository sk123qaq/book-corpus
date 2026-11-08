"""章节结构模型生成：分块、重试、缓存、合并与最终校验。

这个模块只产生 chapter_model 与调用元数据，不写文件。落盘、档位和守恒账
由 CLI 与 ``disposition`` 负责。生成失败必须整体失败：不合成兜底模型，也不
返回半截结果。
"""

import hashlib
import json
from pathlib import Path

from ..model import DEFAULT_EFFORT, DEFAULT_MODEL, ModelProviderError
from .schema import PROMPT_VERSION, ChapterModelError, validate_chapter_model

SYSTEM_PROMPT = "\n".join(
    [
        "你是书库的结构编辑。请把给定的一章正文拆成可核查的命题单元，只输出一个 JSON 对象。",
        "",
        "硬约束：",
        '1. quotes 是数组，每项写成 {"block":"B01","text":"逐字原文"}；'
        "block 必须是本次 user 消息给出的 block id，text 必须是该 block.text 的逐字子串，"
        "不能跨块拼接，不能改写。",
        "2. 每个正文块必须被某个 unit 的 source_blocks 覆盖，或者进入 non_semantic。"
        "non_semantic 必须写 reason，不能和 unit 的 source_blocks 重叠。",
        "3. sections[].unit_ids 必须不重不漏地覆盖全部 unit；unit.section 可以省略，写出时必须等于所属 section id。",
        "4. unit.kind 只能是 definition/claim/conclusion/condition/risk/example/evidence/context/transition。",
        "5. unit.importance 只能是 core/support/detail。definition、判断、条件、风险、例外、结论、数字、"
        "图题、被引证据标 hard_info=true。hard_info 必须是布尔值；hard_info=true 时必须给 hard_info_note，"
        "写一条短而不可替代的原文事实（数字、名字、定义、结论），且该文本必须能在该 unit 的 quotes 里找到。"
        "summary 必须包含 hard_info_note 的原文短语；不要把整段叙述塞进 hard_info。",
        "6. 每个 unit 必须有 relations 数组，可为空；relations 只能是 supports/contrasts/causes/exemplifies/qualifies，"
        "to 必须指向本块另一个 unit。duplicate_of 指向本块另一个 unit，"
        "且该 unit 自己不能再是 duplicate；duplicate 不能是 core。",
        "7. 不要输出 tiers。档位由后续确定性计划器生成。",
        "8. 顶层必须有 schema、uid、n、source_sha256、prompt_version、units、sections、terms、figures、"
        "non_semantic、folds。schema 写 1，prompt_version 固定写 chapter-model-v1。"
        "字段可以多，不能少。sections 用 unit_ids，不用 units。",
        "9. term 字段为 term、definition、source_block、quote；quote 必须是 source_block 的逐字子串。"
        "figure 字段为 id、family、question、units、origin、caption、reading_note、data、fallback；"
        "family 只能是 argument/matrix/tree/flow/timeline/network/quantity，"
        "origin 只能是 source/derived/external，data 是对象，phase 1 只需给出候选结构。",
        "10. 本块内的 id 用局部编号即可，例如 S01、U01、F01；user 消息给了 id_prefix 时，"
        "所有局部 id 都加这个前缀。合并器会改成全局编号。"
        "关系、duplicate_of、section.unit_ids、figure.units 都要使用同一套局部编号。",
        "11. 叙事章节按场景与转折分节，不按 H2 分节；原文没有 H2 时不要为了分节造标题。"
        "一个 unit 可以跨连续段落，只要它们属于同一场景节拍；保留 setup→turn→consequence 的因果/转折链。"
        "section.title 命名节拍（例如“跪下”“挨打”），不要照抄原文 heading。",
        "12. 表格是一个 block，但必须拆成多个 unit：表头（这张表在比较什么）、每个有意义的行/列组、"
        "结论行、读法。source_blocks 写表格 block id；quotes 必须是某个单元格文本的逐字子串，整行文本也可以。"
        "表头或结论行承载表格主张时 hard_info=true；普通数据行必须 importance=core，"
        "skim 用 condensed 保留，只有纯装饰或重复行才可 detail。合并或空白单元格归到最近的非空行/列；"
        "只有逐字表格子串才能写进 hard_info_note，"
        "否则把归属写进 summary。不要丢行，也不要把整张表压成一个 unit。",
        "13. 装饰图（封面、花纹、无图注且无相邻标题）放进 non_semantic，reason 写 decorative，"
        "不建 unit，不算硬信息。有语义的图/图表（有图注、相邻标题或承载论证）建 unit，"
        "source_blocks 同时包含图片块和图注块；quotes 用图注文本，图注为空时用 alt 文本；"
        "figure 的 origin=source、units=[unit_id]，并写 caption。"
        "语义图承载这一节的结构主张时，对应 unit 的 importance 设为 core；只作辅助证据时用 support。"
        "装饰图不得进入 unit 或 figure；不得声称图或图注里没有的数字。",
        "14. 宁可少而连贯，不要一句一个 unit；不要编造 section 标题、引文、数字或合并单元格归属。"
        "hard_info 必须是布尔值；hard_info_note 只放逐字原文短语。",
        "",
        "输出只包含 JSON，不要 markdown 围栏，不要解释。",
    ]
)

MAX_JSON_RETRIES = 1


class DepthModelError(RuntimeError):
    """结构模型生成失败。"""


def _block_view(block):
    view = {"id": block.get("id"), "kind": block.get("kind"), "text": block.get("text") or ""}
    for key in ("level", "items", "rows", "head", "rel", "marker"):
        if key in block:
            view[key] = block[key]
    return view


def _book_view(book):
    if not isinstance(book, dict):
        return {}
    return {key: book.get(key) for key in ("uid", "title", "author", "subjects", "publisher", "year") if key in book}


def _chapter_view(chapter):
    if not isinstance(chapter, dict):
        return {}
    return {
        key: chapter.get(key) for key in ("uid", "n", "title", "source_sha256", "stem", "ordinal") if key in chapter
    }


def _messages(book, chapter, chunk, index, total, *, prev_title=None, next_title=None, id_prefix=""):
    payload = {
        "book": _book_view(book),
        "chapter": _chapter_view(chapter),
        "chunk": {
            "index": index + 1,
            "total": total,
            "prev_section_title": prev_title,
            "next_section_title": next_title,
            "id_prefix": id_prefix,
        },
        "blocks": [_block_view(block) for block in chunk],
        "reminder": "只使用上面 blocks 里的 id；每个块都要被 unit 覆盖或进 non_semantic；不要输出 tiers。",
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _merge_usage(target, usage):
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, bool):
            target.setdefault(key, value)
        elif isinstance(value, (int, float)):
            target[key] = target.get(key, 0) + value
        else:
            target.setdefault(key, value)


def _split_at_limit(blocks, limit):
    chunks = []
    current = []
    current_chars = 0
    for block in blocks:
        size = len(block.get("text") or "")
        if current and current_chars + size > limit:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += size
    if current:
        chunks.append(current)
    return chunks


def _h2_groups(blocks):
    groups = []
    current = []
    for block in blocks:
        is_h2 = block.get("kind") == "heading" and block.get("level") == 2
        if is_h2 and current:
            groups.append(current)
            current = []
        current.append(block)
    if current:
        groups.append(current)
    return groups


def _chunk_plans(blocks, limit):
    """小章一次调用；超过 limit 才按 H2 与字符上限切块。"""
    if not blocks:
        plans = [{"blocks": [], "start": 0, "end": 0}]
    elif sum(len(block.get("text") or "") for block in blocks) <= limit:
        plans = [{"blocks": list(blocks), "start": 0, "end": len(blocks)}]
    else:
        plans = []
        start = 0
        for group in _h2_groups(blocks):
            for piece in _split_at_limit(group, limit):
                plans.append({"blocks": piece, "start": start, "end": start + len(piece)})
                start += len(piece)
    total = len(plans)
    for index, plan in enumerate(plans):
        plan["index"] = index
        plan["total"] = total
        plan["prev_title"] = _neighbor_h2(blocks, plan["start"], -1)
        plan["next_title"] = _neighbor_h2(blocks, plan["end"] - 1, 1)
        plan["id_prefix"] = f"C{index + 1:02d}-" if total > 1 else ""
    return plans


def _neighbor_h2(blocks, index, step):
    current = index
    while 0 <= current < len(blocks):
        block = blocks[current]
        if block.get("kind") == "heading" and block.get("level") == 2:
            if current != index:
                return block.get("text") or ""
            if step < 0:
                current -= 1
                continue
        current += step
    return None


def _chunks(blocks, limit):
    """兼容旧调用：只返回每块的正文块列表。"""
    return [plan["blocks"] for plan in _chunk_plans(blocks, limit)]


def _chunk_payload(payload, chunk, chapter):
    data = dict(payload)
    chapter_view = chapter if isinstance(chapter, dict) else {}
    data.setdefault("schema", 1)
    data.setdefault("uid", chapter_view.get("uid"))
    data.setdefault("n", chapter_view.get("n"))
    data.setdefault("source_sha256", chapter_view.get("source_sha256"))
    data.setdefault("prompt_version", PROMPT_VERSION)
    data.setdefault("folds", [])
    expected_uid = chapter_view.get("uid")
    expected_n = chapter_view.get("n")
    expected_sha = chapter_view.get("source_sha256")
    return validate_chapter_model(
        data,
        chunk,
        uid=expected_uid if expected_uid is not None else data.get("uid"),
        n=expected_n if expected_n is not None else data.get("n"),
        source_sha256=expected_sha if expected_sha is not None else data.get("source_sha256"),
    )


def _complete_json(client, messages, model, effort, *, chunk=None, chapter=None):
    json_attempts = 0
    provider_retried = False
    while True:
        try:
            result = client.complete_json(messages, model=model, effort=effort)
            payload, usage = result
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("顶层不是对象", "", 0)
            if chunk is not None:
                payload = _chunk_payload(payload, chunk, chapter)
            return payload, usage
        except json.JSONDecodeError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise DepthModelError(f"连续 {MAX_JSON_RETRIES + 1} 次没有得到合法 JSON：{exc}") from exc
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"上一次输出不是合法 JSON 对象：{exc}。请只输出完整 JSON，不要围栏或解释。",
                },
            ]
        except ChapterModelError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise DepthModelError(f"连续 {MAX_JSON_RETRIES + 1} 次未通过校验：{exc}") from exc
            messages = [
                *messages,
                {"role": "user", "content": f"上一次输出未通过结构校验：{exc}。请修正后只输出完整 JSON。"},
            ]
        except ModelProviderError as exc:
            if provider_retried:
                raise DepthModelError(f"模型提供方连续失败：{exc}") from exc
            provider_retried = True
            messages = [*messages, {"role": "user", "content": "上一次调用失败，请重试并只输出 JSON 对象。"}]


def _local_section_map(payload):
    mapping = {}
    for index, section in enumerate(payload.get("sections") or []):
        if not isinstance(section, dict):
            raise DepthModelError("sections 里有非对象")
        local_id = section.get("id") or f"S{index + 1:02d}"
        if local_id in mapping:
            raise DepthModelError(f"本块 section id 重复：{local_id}")
        mapping[local_id] = section
    return mapping


def _required_local_unit_map(payload, section_map):
    units = payload.get("units") or []
    local_ids = []
    membership = {}
    for section_id, section in section_map.items():
        for unit_id in section.get("unit_ids", section.get("units")) or []:
            membership.setdefault(unit_id, section_id)
    for unit in units:
        if not isinstance(unit, dict):
            raise DepthModelError("units 里有非对象")
        local_id = unit.get("id")
        if not local_id:
            raise DepthModelError("单元缺少局部 id")
        local_ids.append(local_id)
    if len(set(local_ids)) != len(local_ids):
        raise DepthModelError("本块 unit id 重复")
    for unit in units:
        section = unit.get("section") or membership.get(unit["id"])
        if section is None:
            if len(section_map) == 1:
                section = next(iter(section_map))
            else:
                raise DepthModelError(f"单元 {unit['id']} 没有 section，且本块有多个 section")
        if section not in section_map:
            raise DepthModelError(f"单元 {unit['id']} 的 section 不在本块：{section}")
        unit["_section"] = section
    if units and not section_map:
        raise DepthModelError("本块有单元但没有 section")
    return {unit["id"]: unit for unit in units}, membership


def _unit_section(unit, section_map):
    section = unit.get("_section")
    if section is None:
        section = next(iter(section_map))
    return section


def _merge_chunks(payloads):
    units = []
    sections = []
    terms = []
    figures = []
    non_semantic = []
    folds = []
    section_counter = 0
    figure_counter = 0
    for payload in payloads:
        section_map = _local_section_map(payload)
        units_raw = payload.get("units") or []
        _, membership = _required_local_unit_map(payload, section_map)
        unit_global = {}
        unit_counters = {}
        section_global = {}
        for local_id in section_map:
            section_counter += 1
            section_global[local_id] = f"S{section_counter:02d}"
            unit_counters[local_id] = 0
        for unit in units_raw:
            section = _unit_section(unit, section_map)
            section_id = section_global[section]
            unit_counters[section] += 1
            unit_global[unit["id"]] = f"{section_id}-U{unit_counters[section]:02d}"
        for local_id, section in section_map.items():
            section_id = section_global[local_id]
            listed = section.get("unit_ids", section.get("units"))
            if listed is None:
                listed = [
                    unit["id"]
                    for unit in units_raw
                    if _unit_section(unit, section_map) == local_id or membership.get(unit["id"]) == local_id
                ]
            mapped = []
            for local_unit_id in listed:
                if local_unit_id not in unit_global:
                    raise DepthModelError(f"section {local_id} 引用了本块不存在的单元：{local_unit_id}")
                mapped.append(unit_global[local_unit_id])
            for unit in units_raw:
                if _unit_section(unit, section_map) == local_id and unit_global[unit["id"]] not in mapped:
                    mapped.append(unit_global[unit["id"]])
            item = dict(section)
            item["id"] = section_id
            item["unit_ids"] = mapped
            item.pop("units", None)
            sections.append(item)
        for unit in units_raw:
            item = dict(unit)
            item["id"] = unit_global[unit["id"]]
            item["section"] = section_global[_unit_section(unit, section_map)]
            item.pop("_section", None)
            relations = []
            for relation in item.get("relations") or []:
                relation = dict(relation)
                target = relation.get("target", relation.get("to"))
                if target not in unit_global:
                    raise DepthModelError(f"单元 {unit['id']} 的关系指向本块不存在的单元：{target}")
                relation.pop("target", None)
                relation["to"] = unit_global[target]
                relations.append(relation)
            item["relations"] = relations
            duplicate_of = item.get("duplicate_of")
            if duplicate_of is not None:
                if duplicate_of not in unit_global:
                    raise DepthModelError(f"单元 {unit['id']} 的 duplicate_of 指向本块不存在的单元：{duplicate_of}")
                item["duplicate_of"] = unit_global[duplicate_of]
            units.append(item)
        for figure in payload.get("figures") or []:
            figure_counter += 1
            item = dict(figure)
            item["id"] = f"F{figure_counter:02d}"
            mapped = []
            for local_unit_id in item.get("units") or []:
                if local_unit_id not in unit_global:
                    raise DepthModelError(f"figure 指向本块不存在的单元：{local_unit_id}")
                mapped.append(unit_global[local_unit_id])
            item["units"] = mapped
            figures.append(item)
        terms.extend(payload.get("terms") or [])
        non_semantic.extend(payload.get("non_semantic") or [])
        folds.extend(payload.get("folds") or [])
    return {
        "schema": 1,
        "prompt_version": PROMPT_VERSION,
        "units": units,
        "sections": sections,
        "terms": terms,
        "figures": figures,
        "non_semantic": non_semantic,
        "folds": folds,
    }


def _prompt_sha256():
    return hashlib.sha256(f"{PROMPT_VERSION}\n{SYSTEM_PROMPT}".encode()).hexdigest()


def cache_key(source_sha256, prompt_sha256, model, effort):
    return hashlib.sha256(f"{source_sha256}{prompt_sha256}{model}{effort}".encode()).hexdigest()


def _read_cache(path, source_sha256, prompt_sha256, model, effort):
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("prompt_version") != PROMPT_VERSION:
        return None
    if data.get("source_sha256") != source_sha256:
        return None
    if data.get("prompt_sha256") != prompt_sha256:
        return None
    if data.get("model") != model or data.get("effort") != effort:
        return None
    cached = data.get("chapter_model")
    if not isinstance(cached, dict):
        return None
    return cached, data.get("meta") or {}


def _write_cache(path, payload):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def generate_chapter_model(
    client,
    book,
    chapter,
    blocks,
    *,
    model=None,
    effort=None,
    chunk_chars=18000,
    cache_dir=None,
):
    """生成并校验 chapter_model，返回 ``(model, meta)``；不写任何文件。

    传入 ``cache_dir`` 时按 source、prompt、模型与 effort 的哈希读写缓存。
    失败抛异常，不生成合成兜底，也不返回部分结果。
    """
    if not isinstance(chunk_chars, int) or chunk_chars <= 0:
        raise ValueError("chunk_chars 必须是正整数")
    if effort is not None and effort not in ("max", "high", "medium", "low"):
        raise ValueError("effort 必须是 max、high、medium 或 low")
    chapter_view = chapter if isinstance(chapter, dict) else {}
    requested_model = model or getattr(client, "model", None) or DEFAULT_MODEL
    requested_effort = effort or getattr(client, "effort", None) or DEFAULT_EFFORT
    source_sha256 = chapter_view.get("source_sha256")
    prompt_sha = _prompt_sha256()
    key = None
    cache_path = None
    if cache_dir is not None and source_sha256:
        key = cache_key(source_sha256, prompt_sha, requested_model, requested_effort)
        cache_path = Path(cache_dir) / f"{key}.json"
        cached = _read_cache(cache_path, source_sha256, prompt_sha, requested_model, requested_effort)
        if cached is not None:
            cached_model, cached_meta = cached
            try:
                validated = validate_chapter_model(
                    cached_model,
                    blocks or [],
                    uid=chapter_view.get("uid") or (book.get("uid") if isinstance(book, dict) else None),
                    n=chapter_view.get("n"),
                    source_sha256=source_sha256,
                )
            except ChapterModelError:
                pass
            else:
                meta = dict(cached_meta)
                meta.update(
                    {
                        "requested_model": requested_model,
                        "model": cached_model.get("model") or cached_meta.get("model") or requested_model,
                        "effort": requested_effort,
                        "cache_key": key,
                        "cache_hit": True,
                    }
                )
                return validated, meta

    plans = _chunk_plans(blocks or [], chunk_chars)
    payloads = []
    usage = {}
    actual_model = None
    for plan in plans:
        messages = _messages(
            book,
            chapter,
            plan["blocks"],
            plan["index"],
            plan["total"],
            prev_title=plan["prev_title"],
            next_title=plan["next_title"],
            id_prefix=plan["id_prefix"],
        )
        payload, chunk_usage = _complete_json(
            client,
            messages,
            requested_model,
            requested_effort,
            chunk=plan["blocks"],
            chapter=chapter,
        )
        _merge_usage(usage, chunk_usage)
        actual_model = payload.pop("_model", None) or payload.get("model") or actual_model
        payloads.append(payload)
    merged = _merge_chunks(payloads)
    first = payloads[0] if payloads else {}
    merged["uid"] = chapter_view.get("uid") or (book.get("uid") if isinstance(book, dict) else None)
    merged["n"] = chapter_view.get("n")
    merged["source_sha256"] = source_sha256
    if merged["uid"] is None:
        merged["uid"] = first.get("uid")
    if merged["n"] is None:
        merged["n"] = first.get("n")
    if merged["source_sha256"] is None:
        merged["source_sha256"] = first.get("source_sha256")
    try:
        validated = validate_chapter_model(
            merged,
            blocks or [],
            uid=merged["uid"],
            n=merged["n"],
            source_sha256=merged["source_sha256"],
        )
    except ChapterModelError as exc:
        raise DepthModelError(f"合并后的结构模型没有通过校验：{exc}") from exc
    actual_model = actual_model or requested_model
    meta = {
        "requested_model": requested_model,
        "model": actual_model,
        "effort": requested_effort,
        "usage": usage,
        "fallback": bool(actual_model and actual_model != requested_model),
        "chunks": len(plans),
        "cache_key": key,
        "cache_hit": False,
    }
    if cache_path is not None:
        _write_cache(
            cache_path,
            {
                "schema": 1,
                "prompt_version": PROMPT_VERSION,
                "source_sha256": source_sha256,
                "prompt_sha256": prompt_sha,
                "model": requested_model,
                "effort": requested_effort,
                "cache_key": key,
                "chapter_model": validated,
                "meta": meta,
            },
        )
    return validated, meta
