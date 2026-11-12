"""速览档编译：把章节模型与原文压成可核查的核心命题页。

这个模块只产生 ``skim.json`` 与审计结果，不写文件。编译器必须同时满足两层
约束：结构上每个 unit 都有归属，文字上只做压缩不做补写。短章直接判为不适合
编译，不调用模型。
"""

import copy
import json
import math
import re

from .. import events
from ..model import ModelProviderError
from ..textnorm import normalize_verbatim

SKIM_SCHEMA_VERSION = 1
SKIM_PROMPT_VERSION = "skim-compiler-v2"
SKIM_PROMPT_VERSION_V1 = "skim-compiler-v1"
MIN_SOURCE_HAN = 1200
MIN_RATIO = 0.15
MAX_RATIO = 0.25
MAX_JSON_RETRIES = 1
MAX_QUALITY_REPAIRS = 1
REPAIR_PAYLOAD_LIMIT = 60_000

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
BANNED_PHRASES = (
    "作者认为",
    "作者指出",
    "作者主张",
    "笔者认为",
    "本章指出",
    "本章认为",
    "本章试图",
    "我们可以看到",
    "我们能看到",
    "值得注意的是",
    "不难看出",
    "由此可见",
)

SYSTEM_PROMPT = "\n".join(
    [
        "你是书库的速览编辑。给定一章的原文与已经校验过的章节模型，"
        "请编译一份 5 到 8 分钟读完的速览，只输出一个 JSON 对象。",
        "",
        "硬约束：",
        "1. 顶层字段固定为 schema、prompt_version、kind、supported、reason、sections、detail_index。"
        "schema 写 1，prompt_version 固定写 skim-compiler-v2，kind 固定写 claims。"
        "supported 是布尔值。",
        "2. supported=true 时，sections 必须与 chapter_model.sections 一一对应，section.id 用原 id，"
        "不得新增、删除或合并节。每节 2 到 4 条 claims；只有不超过 2 个 unit 的短节可以 1 条。lead 写一句本节要旨；"
        "turn 可省略或写一句转折/代价，每节最多一条。",
        "3. claim 字段为 text、units、anchor。units 只写本章 chapter_model 的 unit id，"
        "且必须属于该 claim 所在的 section。每条 claim 至少引用一个 unit；"
        "每个 core unit 至少要出现在一条 claim 里。",
        "4. 每个 unit 必须出现一次归属：要么进某条 claim 的 units，要么进 detail_index。"
        "detail_index 每项字段为 label、unit、anchor、quote；quote 必须是 anchor 块原文的逐字子串。",
        "5. anchor 必须是该 claim/detail 所引 unit 的 source_blocks 之一，写块 id，不写 # 或标题。",
        "6. 正文汉字数（lead、claims.text、turn）必须占源文汉字数的 "
        f"{MIN_RATIO:.0%} 到 {MAX_RATIO:.0%}。detail_index 不计入。"
        "做不到就判 supported=false，不要靠注水或删核心命题凑比例。",
        "7. claims 是编译后的陈述，不是摘抄。不要写导读腔：作者认为、作者指出、本章指出、我们可以看到、"
        "值得注意的是、不难看出、由此可见等一律禁止。",
        "8. claim 里出现的阿拉伯数字必须能在源文里逐字找到；不要推算、改写或补造数字。",
        "9. 纯叙事且没有可陈述命题链的章节，或不足 1200 汉字的短章，判 supported=false，"
        "在 reason 里写一句人话，sections 与 detail_index 都留空数组。",
        "",
        "输出只包含 JSON，不要 markdown 围栏，不要解释。",
    ]
)


SCENE_SYSTEM_PROMPT = "\n".join(
    [
        "你是书库的叙事速览编辑。给定一章的原文、章节模型和可选的面板，"
        "请编译 5 到 10 个场景节拍，只输出一个 JSON 对象。",
        "",
        "硬约束：",
        "1. 顶层字段为 schema、prompt_version、kind、supported、reason、scenes。schema 写 1，"
        "prompt_version 写 skim-compiler-v2，kind 写 scenes。",
        "2. supported=true 时，scenes 写 5 到 10 个；每个 scene 字段为 text、units、anchor、turn。"
        "anchor 必须是本章块 id；units 可省略。",
        "3. 每个 scene 至少锚定一个块；turn 写这一场的变化/代价，可省略。",
        f"4. scene 正文汉字总数不超过源文的 {MAX_RATIO:.0%}，不设下限；不要为了凑比例注水，也不要删掉关键场景。",
        "5. 场景是叙事节拍，不是命题链。不要写“作者认为/本章指出/由此可见”等导读腔。",
        "6. 人名、地名、年份、数字必须能在源文里逐字找到；不要补造。",
        "7. 源文不足 1200 汉字，或不足 3 个有效场景，判 supported=false，在 reason 里写人话，scenes 留空。",
        "8. 如果给了 panels.scene_beats，优先按它的节拍分组；但每个 scene 仍要落到 blocks。",
        "",
        "输出只包含 JSON，不要 markdown 围栏，不要解释。",
    ]
)


class SkimError(ValueError):
    """skim 不满足结构、覆盖、比例或原文约束。"""

    kind = "quality"

    def __init__(self, message, *, stats=None, payload=None, ratio_failed=False):
        super().__init__(message)
        self.stats = stats
        self.payload = payload
        # 只有比例越界值得把上一份输出整份回灌让模型扩写。锚点、导读腔这类
        # 结构错误回灌原文只会给它照抄一遍的捷径。
        self.ratio_failed = bool(ratio_failed)


def han_count(text):
    return len(HAN_RE.findall(text or ""))


def source_han(blocks):
    return sum(han_count(block.get("text") or "") for block in blocks or [])


def _ratio_bounds(source):
    """把比例翻成能写进提示和缺口表的整数区间。与校验用同一套算术。"""
    return math.ceil(source * MIN_RATIO), math.floor(source * MAX_RATIO)


def _budget(blocks, mode="argument"):
    source = source_han(blocks)
    low, high = _ratio_bounds(source)
    if mode == "narrative":
        return {
            "source_han": source,
            "body_max": high,
            "counted": ["scenes.text"],
            "excluded": [],
            "unit": "汉字",
        }
    return {
        "source_han": source,
        "body_min": low,
        "body_max": high,
        "counted": ["lead", "claims.text", "turn"],
        "excluded": ["detail_index"],
        "unit": "汉字",
    }


def unsupported_skim(reason, mode="argument", kind=None):
    kind = kind or ("scenes" if mode == "narrative" else "claims")
    return {
        "schema": SKIM_SCHEMA_VERSION,
        "prompt_version": SKIM_PROMPT_VERSION,
        "kind": kind,
        "supported": False,
        "reason": reason,
        "sections": [],
        "detail_index": [],
        "scenes": [],
    }


def _text(value, name, problems, *, allow_empty=False):
    if not isinstance(value, str):
        problems.append(f"{name} 必须是字符串")
        return None
    if not allow_empty and not value.strip():
        problems.append(f"{name} 不能为空")
        return None
    return value


def _check_scenes(skim, chapter_model, blocks):
    """叙事速览：5–10 个 scene，≤25% 源文，无 15% 下限，不要求命题链。"""
    g11 = []
    g14 = []
    warnings = []
    if not isinstance(skim, dict):
        return None, ["skim 顶层必须是对象"], [], [], {}
    required = ("schema", "prompt_version", "kind", "supported", "reason", "scenes")
    for key in required:
        if key not in skim:
            g11.append(f"缺少顶层字段：{key}")
    if skim.get("schema") != SKIM_SCHEMA_VERSION:
        g11.append(f"schema 必须是 {SKIM_SCHEMA_VERSION}")
    accepted = (SKIM_PROMPT_VERSION,)
    if skim.get("prompt_version") not in accepted:
        g11.append(f"prompt_version 必须是 {SKIM_PROMPT_VERSION}")
    if skim.get("kind") != "scenes":
        g11.append("叙事速览的 kind 必须是 scenes")
    supported = skim.get("supported")
    if not isinstance(supported, bool):
        g11.append("supported 必须是布尔值")
        supported = False
    reason = _text(skim.get("reason", ""), "reason", g11, allow_empty=True) or ""
    scenes_raw = skim.get("scenes")
    if not isinstance(scenes_raw, list):
        g11.append("scenes 必须是数组")
        scenes_raw = []
    block_map = {b.get("id"): b for b in blocks or [] if b.get("id")}
    unit_map = {u.get("id"): u for u in chapter_model.get("units") or [] if u.get("id")}
    source_text = "\n".join(b.get("text") or "" for b in blocks or [])
    source_han_count = source_han(blocks)
    clean_scenes = []
    body_parts = []
    seen_anchors = set()
    for index, scene in enumerate(scenes_raw):
        name = f"scenes[{index}]"
        if not isinstance(scene, dict):
            g11.append(f"{name} 必须是对象")
            continue
        text = _text(scene.get("text"), f"{name}.text", g11)
        anchor = _text(scene.get("anchor"), f"{name}.anchor", g11)
        units = scene.get("units") or []
        if not isinstance(units, list):
            g11.append(f"{name}.units 必须是数组")
            units = []
        units = [u for u in units if isinstance(u, str) and u]
        turn = scene.get("turn") or ""
        if not isinstance(turn, str):
            g11.append(f"{name}.turn 必须是字符串")
            turn = ""
        if text:
            body_parts.append(text)
            for phrase in BANNED_PHRASES:
                if phrase in text:
                    g11.append(f"{name}.text 含导读腔：{phrase}")
            for number in NUMBER_RE.findall(text):
                if number not in source_text:
                    g11.append(f"{name}.text 中的数字不在源文：{number}")
        if anchor:
            if anchor not in block_map:
                g11.append(f"{name}.anchor 指向不存在的块：{anchor}")
            elif anchor in seen_anchors:
                warnings.append(f"{name}.anchor 重复：{anchor}")
            seen_anchors.add(anchor)
        else:
            g11.append(f"{name}.anchor 不能为空")
        for unit_id in units:
            if unit_id not in unit_map:
                g11.append(f"{name} 引用了不存在的单元：{unit_id}")
            elif anchor and anchor not in (unit_map[unit_id].get("source_blocks") or []):
                g11.append(f"{name}.anchor 不属于单元 {unit_id}")
        if text and anchor:
            clean_scenes.append({"text": text, "units": units, "anchor": anchor, "turn": turn})
    if len(clean_scenes) > 10:
        g11.append(f"scenes 最多 10 个，当前 {len(clean_scenes)} 个")
    if supported:
        if source_han_count < MIN_SOURCE_HAN:
            g11.append(f"源文不足 {MIN_SOURCE_HAN} 汉字，不能判 supported=true")
        if len(clean_scenes) < 3:
            g11.append(f"supported=true 时至少需要 3 个有效 scene，当前 {len(clean_scenes)} 个")
        body_han = han_count("".join(body_parts))
        ratio = body_han / source_han_count if source_han_count else 0.0
        if body_han > source_han_count * MAX_RATIO:
            g14.append(f"scene 正文汉字比例 {ratio:.2%} 超过 {MAX_RATIO:.0%}")
        stats = {
            "supported": True,
            "source_han": source_han_count,
            "body_han": body_han,
            "ratio": round(ratio, 4),
            "ratio_ok": not g14,
            "kind": "scenes",
            "scenes": len(clean_scenes),
        }
    else:
        if not reason.strip():
            g11.append("supported=false 时必须给 reason")
        if clean_scenes:
            g11.append("supported=false 时 scenes 必须为空")
        stats = {
            "supported": False,
            "source_han": source_han_count,
            "body_han": 0,
            "ratio": 0.0,
            "ratio_ok": False,
            "kind": "scenes",
            "scenes": 0,
        }
    clean = {
        "schema": SKIM_SCHEMA_VERSION,
        "prompt_version": SKIM_PROMPT_VERSION,
        "kind": "scenes",
        "supported": bool(supported and not g11 and not g14),
        "reason": "" if supported and not g11 and not g14 else reason,
        "sections": [],
        "detail_index": [],
        "scenes": clean_scenes if supported and not g11 and not g14 else [],
    }
    return clean, g11, g14, warnings, stats


def _checks(skim, chapter_model, blocks):
    g11 = []
    g14 = []
    warnings = []
    clean_sections = []
    clean_details = []

    if not isinstance(skim, dict):
        g11.append("skim 顶层必须是对象")
        return None, g11, g14, warnings, {}
    if skim.get("kind") == "scenes":
        return _check_scenes(skim, chapter_model, blocks)

    block_map = {}
    for block in blocks or []:
        block_id = block.get("id")
        if isinstance(block_id, str) and block_id:
            block_map[block_id] = block

    unit_map = {}
    for unit in chapter_model.get("units") or []:
        unit_id = unit.get("id")
        if isinstance(unit_id, str) and unit_id:
            unit_map[unit_id] = unit
    section_map = {}
    for section in chapter_model.get("sections") or []:
        section_id = section.get("id")
        if isinstance(section_id, str) and section_id:
            section_map[section_id] = section

    source_text = "\n".join(block.get("text") or "" for block in blocks or [])
    source_han_count = source_han(blocks)
    core_ids = {unit_id for unit_id, unit in unit_map.items() if unit.get("importance") == "core"}

    required = ("schema", "prompt_version", "supported", "reason", "sections", "detail_index")
    for key in required:
        if key not in skim:
            g11.append(f"缺少顶层字段：{key}")
    if skim.get("schema") != SKIM_SCHEMA_VERSION:
        g11.append(f"schema 必须是 {SKIM_SCHEMA_VERSION}")
    accepted = (SKIM_PROMPT_VERSION, SKIM_PROMPT_VERSION_V1)
    if skim.get("prompt_version") not in accepted:
        g11.append(f"prompt_version 必须是 {SKIM_PROMPT_VERSION}")

    supported = skim.get("supported")
    if not isinstance(supported, bool):
        g11.append("supported 必须是布尔值")
        supported = False
    reason = _text(skim.get("reason", ""), "reason", g11, allow_empty=True) or ""
    sections_raw = skim.get("sections")
    details_raw = skim.get("detail_index")
    if not isinstance(sections_raw, list):
        g11.append("sections 必须是数组")
        sections_raw = []
    if not isinstance(details_raw, list):
        g11.append("detail_index 必须是数组")
        details_raw = []

    if not supported:
        if not reason.strip():
            g11.append("supported=false 时必须给 reason")
        if sections_raw:
            g11.append("supported=false 时 sections 必须为空")
        if details_raw:
            g11.append("supported=false 时 detail_index 必须为空")
        if source_han_count >= MIN_SOURCE_HAN:
            warnings.append("长章被标为不适合编译，请确认 reason")
        stats = {
            "supported": False,
            "source_han": source_han_count,
            "body_han": 0,
            "ratio": 0.0,
            "ratio_ok": False,
            "sections": 0,
            "claims": 0,
            "detail_count": 0,
            "unit_total": len(unit_map),
            "units_in_claims": 0,
            "units_indexed": 0,
            "units_covered": 0,
            "core_total": len(core_ids),
            "core_covered": 0,
            "core_claim_coverage": 0.0,
            "section_gaps": [],
        }
        clean = {
            "schema": SKIM_SCHEMA_VERSION,
            "prompt_version": SKIM_PROMPT_VERSION,
            "kind": "claims",
            "supported": False,
            "reason": reason,
            "sections": [],
            "detail_index": [],
            "scenes": [],
        }
        return clean, g11, g14, warnings, stats

    claim_units = set()
    detail_units = set()
    seen_sections = []
    body_parts = []
    section_body_han = {}

    for index, section in enumerate(sections_raw):
        name = f"sections[{index}]"
        if not isinstance(section, dict):
            g11.append(f"{name} 必须是对象")
            continue
        section_id = _text(section.get("id"), f"{name}.id", g11)
        if section_id is None:
            continue
        if section_id in seen_sections:
            g11.append(f"section id 重复：{section_id}")
            continue
        seen_sections.append(section_id)
        model_section = section_map.get(section_id)
        if model_section is None:
            g11.append(f"section 不在章节模型里：{section_id}")
            continue
        lead = _text(section.get("lead"), f"{name}.lead", g11)
        if lead:
            body_parts.append(lead)
            section_body_han[section_id] = section_body_han.get(section_id, 0) + han_count(lead)
            for phrase in BANNED_PHRASES:
                if phrase in lead:
                    g11.append(f"{name}.lead 含导读腔：{phrase}")
        turn = section.get("turn") or ""
        if not isinstance(turn, str):
            g11.append(f"{name}.turn 必须是字符串")
            turn = ""
        if turn.strip():
            body_parts.append(turn)
            section_body_han[section_id] = section_body_han.get(section_id, 0) + han_count(turn)
            for phrase in BANNED_PHRASES:
                if phrase in turn:
                    g11.append(f"{name}.turn 含导读腔：{phrase}")
        claims_raw = section.get("claims")
        if not isinstance(claims_raw, list):
            g11.append(f"{name}.claims 必须是数组")
            continue
        section_unit_ids = set(model_section.get("unit_ids") or [])
        min_claims = 1 if len(section_unit_ids) <= 2 else 2
        if not min_claims <= len(claims_raw) <= 4:
            g11.append(f"{name}.claims 必须有 {min_claims} 到 4 条")
        clean_claims = []
        for claim_index, claim in enumerate(claims_raw):
            claim_name = f"{name}.claims[{claim_index}]"
            if not isinstance(claim, dict):
                g11.append(f"{claim_name} 必须是对象")
                continue
            text = _text(claim.get("text"), f"{claim_name}.text", g11)
            units = claim.get("units")
            if not isinstance(units, list) or not units:
                g11.append(f"{claim_name}.units 必须是非空数组")
                units = []
            units = [unit_id for unit_id in units if isinstance(unit_id, str) and unit_id]
            if len(set(units)) != len(units):
                g11.append(f"{claim_name}.units 有重复单元")
            anchor = _text(claim.get("anchor"), f"{claim_name}.anchor", g11)
            if text:
                body_parts.append(text)
                section_body_han[section_id] = section_body_han.get(section_id, 0) + han_count(text)
                for phrase in BANNED_PHRASES:
                    if phrase in text:
                        g11.append(f"{claim_name}.text 含导读腔：{phrase}")
                for number in NUMBER_RE.findall(text):
                    if number not in source_text:
                        g11.append(f"{claim_name}.text 中的数字不在源文：{number}")
            anchor_blocks = set()
            for unit_id in units:
                unit = unit_map.get(unit_id)
                if unit is None:
                    g11.append(f"{claim_name} 引用了不存在的单元：{unit_id}")
                    continue
                if unit_id not in section_unit_ids:
                    g11.append(f"{claim_name} 的单元 {unit_id} 不属于 {section_id}")
                anchor_blocks.update(unit.get("source_blocks") or [])
                claim_units.add(unit_id)
            if anchor is not None:
                if anchor not in block_map:
                    g11.append(f"{claim_name}.anchor 指向不存在的块：{anchor}")
                elif anchor not in anchor_blocks:
                    g11.append(f"{claim_name}.anchor 不属于所引单元：{anchor}")
            clean_claims.append({"text": text or "", "units": units, "anchor": anchor or ""})
        clean_sections.append({"id": section_id, "lead": lead or "", "claims": clean_claims, "turn": turn})

    missing_sections = [section_id for section_id in section_map if section_id not in seen_sections]
    if missing_sections:
        g11.append("sections 未覆盖章节模型：" + "、".join(missing_sections[:8]))

    for index, entry in enumerate(details_raw):
        name = f"detail_index[{index}]"
        if not isinstance(entry, dict):
            g11.append(f"{name} 必须是对象")
            continue
        label = _text(entry.get("label"), f"{name}.label", g11)
        unit_id = _text(entry.get("unit"), f"{name}.unit", g11)
        anchor = _text(entry.get("anchor"), f"{name}.anchor", g11)
        quote = _text(entry.get("quote"), f"{name}.quote", g11)
        unit = unit_map.get(unit_id) if unit_id else None
        if unit_id and unit is None:
            g11.append(f"{name} 引用了不存在的单元：{unit_id}")
        if unit is not None:
            detail_units.add(unit_id)
            if anchor is not None and anchor not in (unit.get("source_blocks") or []):
                g11.append(f"{name}.anchor 不属于单元 {unit_id}：{anchor}")
        if anchor is not None and anchor not in block_map:
            g11.append(f"{name}.anchor 指向不存在的块：{anchor}")
        elif quote is not None and anchor in block_map:
            block_text = block_map[anchor].get("text") or ""
            if normalize_verbatim(quote) not in normalize_verbatim(block_text):
                g11.append(f"{name}.quote 不是 anchor 的逐字原文：{unit_id}")
        clean_details.append(
            {
                "label": label or "",
                "unit": unit_id or "",
                "anchor": anchor or "",
                "quote": quote or "",
            }
        )

    for unit_id in core_ids:
        if unit_id not in claim_units:
            g11.append(f"core unit 没有进 claim：{unit_id}")
    for unit_id in unit_map:
        if unit_id not in claim_units and unit_id not in detail_units:
            g11.append(f"unit 既不在 claim 也不在 detail_index：{unit_id}")
    both = claim_units & detail_units
    if both:
        warnings.append("单元同时出现在 claim 与 detail_index：" + "、".join(sorted(both)[:8]))

    body_han_count = han_count("".join(body_parts))
    ratio = (body_han_count / source_han_count) if source_han_count else 0.0
    if not source_han_count:
        g14.append("源文没有汉字，无法计算速览比例")
    elif body_han_count < source_han_count * MIN_RATIO or body_han_count > source_han_count * MAX_RATIO:
        # 只说比例，14.99% 会被四舍五入成 15.0%，看账的人会以为门槛自己抽了风。
        # 比例放括号里，先给可以逐字核对的字数。
        low, high = _ratio_bounds(source_han_count)
        g14.append(
            f"正文汉字 {body_han_count} 字（{ratio:.1%}）越界（{MIN_RATIO:.0%}–{MAX_RATIO:.0%}）："
            f"下限 {low} 字、上限 {high} 字"
        )

    section_gaps = []
    for section_id, model_section in section_map.items():
        section_unit_ids = set(model_section.get("unit_ids") or [])
        section_blocks = {}
        for unit_id in section_unit_ids:
            unit = unit_map.get(unit_id) or {}
            for block_id in unit.get("source_blocks") or []:
                block = block_map.get(block_id)
                if block is not None:
                    section_blocks[block_id] = block
        section_gaps.append(
            {
                "id": section_id,
                "body_han": section_body_han.get(section_id, 0),
                "units": len(section_unit_ids),
                "source_han": source_han(section_blocks.values()),
            }
        )

    stats = {
        "supported": True,
        "source_han": source_han_count,
        "body_han": body_han_count,
        "ratio": round(ratio, 4),
        "ratio_ok": not g14,
        "sections": len(seen_sections),
        "claims": sum(len(section["claims"]) for section in clean_sections),
        "detail_count": len(clean_details),
        "unit_total": len(unit_map),
        "units_in_claims": len(claim_units),
        "units_indexed": len(detail_units),
        "units_covered": len(claim_units | detail_units),
        "core_total": len(core_ids),
        "core_covered": len(core_ids & claim_units),
        "core_claim_coverage": (len(core_ids & claim_units) / len(core_ids)) if core_ids else 1.0,
        "section_gaps": section_gaps,
    }
    clean = {
        "schema": SKIM_SCHEMA_VERSION,
        "prompt_version": SKIM_PROMPT_VERSION,
        "kind": "claims",
        "supported": True,
        "reason": "",
        "sections": clean_sections,
        "detail_index": clean_details,
        "scenes": [],
    }
    return clean, g11, g14, warnings, stats


def validate_skim(payload, chapter_model, blocks, mode="argument"):
    """校验并返回规范化副本；任何结构或守恒错误都抛 :class:`SkimError`。"""
    clean, g11, g14, _, stats = _checks(payload, chapter_model, blocks)
    problems = g11 + g14
    if problems:
        raise SkimError("；".join(problems), stats=stats, payload=payload, ratio_failed=bool(g14))
    return copy.deepcopy(clean)


def audit_skim(skim, chapter_model, blocks, mode="argument"):
    """只读取证：返回错误、警告、统计与 G11/G14 gates。"""
    clean, g11, g14, warnings, stats = _checks(skim, chapter_model, blocks)
    return {
        "errors": g11 + g14,
        "warnings": warnings,
        "stats": stats,
        "gates": {
            "G11": {"status": "fail" if g11 else "pass", "problems": g11},
            "G14": {"status": "fail" if g14 else "pass", "problems": g14},
        },
        "normalized": clean,
    }


def _unit_view(unit):
    return {
        key: unit.get(key)
        for key in ("id", "kind", "importance", "source_blocks", "summary", "hard_info", "duplicate_of")
        if key in unit
    }


def _section_view(section):
    return {key: section.get(key) for key in ("id", "title", "lead", "unit_ids") if key in section}


def _block_view(block):
    view = {"id": block.get("id"), "kind": block.get("kind"), "text": block.get("text") or ""}
    for key in ("level", "items", "rows", "head", "rel", "marker"):
        if key in block:
            view[key] = block[key]
    return view


def _messages(book, chapter, chapter_model, blocks):
    book_view = {key: book.get(key) for key in ("uid", "title", "author") if isinstance(book, dict) and key in book}
    chapter_view = {
        key: chapter.get(key)
        for key in ("uid", "n", "title", "source_sha256")
        if isinstance(chapter, dict) and key in chapter
    }
    model_view = {
        "sections": [_section_view(section) for section in chapter_model.get("sections") or []],
        "units": [_unit_view(unit) for unit in chapter_model.get("units") or []],
        "core_units": [unit.get("id") for unit in chapter_model.get("units") or [] if unit.get("importance") == "core"],
    }
    budget = _budget(blocks)
    payload = {
        "book": book_view,
        "chapter": chapter_view,
        "chapter_model": model_view,
        "blocks": [_block_view(block) for block in blocks or []],
        "budget": budget,
        "reminder": (
            "只输出 JSON；每个 unit 都要有归属；anchor 用块 id；"
            f"正文（lead、claims.text、turn）须在 {budget['body_min']}–{budget['body_max']} 汉字之间，"
            f"源文 {budget['source_han']} 汉字。"
        ),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _clip(text, limit=600):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _scene_messages(book, chapter, chapter_model, blocks, panels=None):
    book_view = {key: book.get(key) for key in ("uid", "title", "author") if isinstance(book, dict) and key in book}
    chapter_view = {
        key: chapter.get(key)
        for key in ("uid", "n", "title", "source_sha256")
        if isinstance(chapter, dict) and key in chapter
    }
    budget = _budget(blocks, mode="narrative")
    payload = {
        "book": book_view,
        "chapter": chapter_view,
        "chapter_model": {
            "sections": [_section_view(section) for section in chapter_model.get("sections") or []],
            "units": [_unit_view(unit) for unit in chapter_model.get("units") or []],
        },
        "blocks": [_block_view(block) for block in blocks or []],
        "panels": panels or {},
        "budget": budget,
        "reminder": (
            "只输出 JSON；5–10 个 scene；每个 scene 至少一个 block anchor；"
            f"正文不超过源文 {budget['body_max']} 汉字（{MAX_RATIO:.0%}）。"
        ),
    }
    return [
        {"role": "system", "content": SCENE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _gap_message(stats, message, detail_dropped=False):
    source = stats.get("source_han") or 0
    body = stats.get("body_han") or 0
    low, high = _ratio_bounds(source)
    if body > high:
        # 超上限还叫它「扩写」只会把一个必然再失败的返修花掉。
        move = f"超出 {body - high} 汉字。删减 lead、claims.text、turn 的正文，先删与其它条重复的举例。"
    elif body < low:
        move = f"总缺口 {low - body} 汉字。只扩写 lead、claims.text、turn 的正文。"
    else:
        move = "正文已在区间内，不要动正文长度，只修结构与引文。"
    lines = [
        f"未通过的校验：{_clip(message)}",
        f"当前正文 {body} 汉字，目标 {low}–{high} 汉字（源文 {source} 汉字），{move}",
    ]
    lines.append(
        "sections、claims、units、anchor 的结构不要改，"
        + ("detail_index 的引文也不要改。" if not detail_dropped else "detail_index 需要按源文块重建。")
    )
    if detail_dropped:
        lines.append("上一份输出的 detail_index 因过长已省略，请按源文块重建，unit 归属规则不变。")
    gaps = stats.get("section_gaps") or []
    if gaps:
        lines.append("分节目标：")
        for item in gaps:
            section_source = item.get("source_han", 0)
            section_low, section_high = _ratio_bounds(section_source)
            section_body = item.get("body_han", 0)
            mark = "偏少" if section_body < section_low else ("偏多" if section_body > section_high else "达标")
            lines.append(
                f"- {item.get('id')}：当前正文 {section_body} 汉字（{mark}）/ 目标 {section_low}–{section_high} 汉字 / "
                f"{item.get('units', 0)} 个 unit / 源文 {section_source} 汉字"
            )
    return "\n".join(lines)


def _repair_payload_text(payload):
    """返回（可回灌的 assistant 文本，原序列化长度，省了 detail_index，仍太长）。

    文本为 None 表示整份都放不下，调用方只发目标表。省 detail_index 时不能换成
    占位对象：模型会照抄，列表变对象，下一轮必挂 schema。
    """
    try:
        original = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return None, 0, False, False
    if len(original) <= REPAIR_PAYLOAD_LIMIT:
        return original, len(original), False, False
    reduced = copy.deepcopy(payload)
    dropped = False
    if isinstance(reduced, dict) and reduced.pop("detail_index", None) is not None:
        dropped = True
        try:
            reduced_text = json.dumps(reduced, ensure_ascii=False)
        except (TypeError, ValueError):
            return None, len(original), True, True
        if len(reduced_text) <= REPAIR_PAYLOAD_LIMIT:
            return reduced_text, len(original), True, False
    return None, len(original), dropped, True


def generate_skim(
    client,
    book,
    chapter,
    chapter_model,
    blocks,
    *,
    model=None,
    effort=None,
    mode="argument",
    panels=None,
    on_usage=None,
):
    """生成并校验速览；短章直接返回 unsupported，不调用模型。"""
    source = source_han(blocks)
    if source < MIN_SOURCE_HAN:
        return unsupported_skim(f"源文只有 {source} 个汉字，短章更适合直接读原文。", mode=mode)
    hint = chapter_model.get("skim") if isinstance(chapter_model, dict) else None
    if isinstance(hint, dict) and hint.get("supported") is False:
        reason = hint.get("reason") or "章节模型判定本章不适合编译。"
        return unsupported_skim(str(reason), mode=mode)

    messages = (
        _scene_messages(book, chapter, chapter_model, blocks, panels)
        if mode == "narrative"
        else _messages(book, chapter, chapter_model, blocks)
    )
    json_attempts = 0
    quality_repairs = 0
    last_quality_error = None
    provider_retried = False
    while True:
        try:
            result = client.complete_json(messages, model=model, effort=effort)
            payload, usage = result
            if on_usage is not None and isinstance(usage, dict) and usage:
                # 用量要留给账本算钱：速览也是一次真调用，不能白记。
                on_usage(usage)
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("顶层不是对象", "", 0)
            payload = dict(payload)
            if mode == "narrative":
                payload.setdefault("kind", "scenes")
            else:
                payload.setdefault("kind", "claims")
            return validate_skim(payload, chapter_model, blocks, mode=mode)
        except json.JSONDecodeError as exc:
            json_attempts += 1
            if json_attempts > MAX_JSON_RETRIES:
                raise SkimError(f"连续 {MAX_JSON_RETRIES + 1} 次没有得到合法 JSON：{exc}") from exc
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": f"上一次输出不是合法 JSON 对象：{exc}。请只输出完整 JSON，不要围栏或解释。",
                },
            ]
        except SkimError as exc:
            if mode == "narrative" or not exc.ratio_failed:
                # 场景档没有比例下限；锚点、腔调这类结构错误也走旧路：只回错误文本。
                json_attempts += 1
                if json_attempts > MAX_JSON_RETRIES:
                    raise SkimError(
                        f"连续 {MAX_JSON_RETRIES + 1} 次未通过速览校验：{_clip(exc)}",
                        stats=exc.stats,
                        payload=exc.payload,
                        ratio_failed=exc.ratio_failed,
                    ) from exc
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": f"上一次输出未通过速览校验：{_clip(exc)}。请修正后只输出完整 JSON。",
                    },
                ]
                continue
            quality_repairs += 1
            if quality_repairs > MAX_QUALITY_REPAIRS:
                previous = (last_quality_error.stats if last_quality_error else None) or {}
                current = exc.stats or {}
                raise SkimError(
                    f"连续 {MAX_QUALITY_REPAIRS + 1} 次未通过速览校验："
                    f"上次正文 {previous.get('body_han', 0)} 字、本次 {current.get('body_han', 0)} 字；{_clip(exc)}",
                    stats=exc.stats,
                    payload=exc.payload,
                ) from exc
            last_quality_error = exc
            assistant_text, payload_chars, detail_dropped, still_too_long = _repair_payload_text(exc.payload)
            gap_text = _gap_message(exc.stats or {}, str(exc), detail_dropped=detail_dropped)
            if assistant_text is None or detail_dropped:
                events.current().emit(
                    "skim_repair_payload_omitted",
                    payload_chars=payload_chars,
                    limit=REPAIR_PAYLOAD_LIMIT,
                    detail_index_dropped=detail_dropped,
                    still_too_long=still_too_long,
                )
            if assistant_text is None:
                messages = [
                    *messages,
                    {"role": "user", "content": gap_text},
                ]
            else:
                messages = [
                    *messages,
                    {"role": "assistant", "content": assistant_text},
                    {"role": "user", "content": gap_text},
                ]
        except ModelProviderError as exc:
            if exc.retries_exhausted:
                raise
            if provider_retried:
                raise SkimError(f"模型提供方连续失败：{exc}") from exc
            provider_retried = True
            messages = [*messages, {"role": "user", "content": "上一次调用失败，请重试并只输出 JSON 对象。"}]
