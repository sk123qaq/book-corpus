"""面板层：每章一次抽取，按读书模式产出结构化面板。

面板不是第二份正文。它把本章已有的结构（时间、人物、场景、论点、步骤、
图版）压成带块锚点的条目；每条条目必须能回到原文，人名、年份、数字与图注
不能编。没有足够结构时不写空面板，直接返回空数组。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..model import ModelProviderError
from ..textnorm import normalize_verbatim
from .deep import _block_map, _block_text, _refs
from .disposition import TIER_ORDER  # noqa: F401  (keeps mode/tier separation explicit)

PANELS_SCHEMA_VERSION = 1
PANELS_PROMPT_VERSION = "reading-panels-v1"
MAX_JSON_RETRIES = 1
MAX_PANELS = 6
MAX_ITEMS = 8
MAX_TEXT = 120
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

PANEL_TYPES_BY_MODE = {
    "narrative": ("timeline", "characters", "scene_beats", "motifs"),
    "argument": ("argument_map", "definitions", "evidence_table", "counterexamples", "scope"),
    "practical": ("steps", "checklist", "decision_tree", "failure_modes", "examples"),
    "reference": ("plates", "terms", "versions", "captions"),
}
PANEL_LABELS = {
    "timeline": "时间线",
    "characters": "人物",
    "scene_beats": "场景节拍",
    "motifs": "母题",
    "argument_map": "论证地图",
    "definitions": "关键定义",
    "evidence_table": "证据表",
    "counterexamples": "反例",
    "scope": "适用范围",
    "steps": "步骤",
    "checklist": "检查清单",
    "decision_tree": "决策树",
    "failure_modes": "失败模式",
    "examples": "例证",
    "plates": "图版",
    "terms": "术语",
    "versions": "版本",
    "captions": "图注",
}
MODE_LABELS = {"narrative": "叙事", "argument": "论证", "practical": "实用", "reference": "资料·图册"}


class PanelsError(ValueError):
    """面板层不满足结构、锚点或逐字约束。"""


def empty_panels(mode: str, reason: str, source_sha256: str = "") -> dict[str, Any]:
    return {
        "schema": PANELS_SCHEMA_VERSION,
        "prompt_version": PANELS_PROMPT_VERSION,
        "mode": mode,
        "source_sha256": source_sha256,
        "panels": [],
        "warnings": [reason] if reason else [],
    }


def _text(value: Any, name: str, errors: list[str], *, allow_empty: bool = False, limit: int | None = None) -> str:
    if not isinstance(value, str):
        errors.append(f"{name} 必须是字符串")
        return ""
    text = value.strip()
    if not allow_empty and not text:
        errors.append(f"{name} 不能为空")
    if limit and len(text) > limit:
        errors.append(f"{name} 超过 {limit} 字")
    return text


def _source_ok(value: Any, name: str, errors: list[str]) -> str:
    source = _text(value, name, errors, allow_empty=True)
    if source and source != "本章" and not re.fullmatch(r"本书\s+\S.*", source):
        errors.append(f"{name} 只能是「本章」或「本书 <节或章>」")
    return source or "本章"


def _valid_block(block_id: str, block_map: dict[str, dict[str, Any]], errors: list[str], label: str) -> bool:
    if block_id not in block_map:
        errors.append(f"{label} 指向不存在的块：{block_id}")
        return False
    return True


def _valid_unit(unit_id: str, unit_map: dict[str, dict[str, Any]], errors: list[str], label: str) -> bool:
    if unit_id not in unit_map:
        errors.append(f"{label} 引用了不存在的 unit：{unit_id}")
        return False
    return True


def _check_item(
    item: Any,
    index: int,
    mode: str,
    block_map: dict[str, dict[str, Any]],
    unit_map: dict[str, dict[str, Any]],
    source_text: str,
    errors: list[str],
) -> dict[str, Any] | None:
    label = f"panels[{index}].items[]" if index else "panel.items[]"
    if not isinstance(item, dict):
        errors.append(f"{label} 必须是对象")
        return None
    text = _text(item.get("text"), f"{label}.text", errors, limit=MAX_TEXT)
    blocks = _refs(item.get("blocks"), f"{label}.blocks", errors)
    if not blocks:
        errors.append(f"{label} 至少需要一个 blocks 锚点")
    for block_id in blocks:
        _valid_block(block_id, block_map, errors, label)
    units = _refs(item.get("units"), f"{label}.units", errors)
    for unit_id in units:
        _valid_unit(unit_id, unit_map, errors, label)
    source = _source_ok(item.get("source"), f"{label}.source", errors)
    quote = _text(item.get("quote", ""), f"{label}.quote", errors, allow_empty=True)
    if quote:
        matched = False
        for block_id in blocks:
            block = block_map.get(block_id)
            if block is not None and normalize_verbatim(quote) in normalize_verbatim(_block_text(block)):
                matched = True
                break
        if not matched:
            errors.append(f"{label}.quote 不是 blocks 中任何一块的逐字原文")
    # 数字必须能在源文里找到；日期/年份同样按数字处理。
    for number in NUMBER_RE.findall(text):
        if number not in source_text:
            errors.append(f"{label}.text 中的数字不在源文：{number}")
    if "已核验" in text:
        errors.append(f"{label}.text 不能写「已核验」")
    clean = {"text": text, "blocks": blocks, "units": units, "source": source}
    if quote:
        clean["quote"] = quote
    return clean


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def validate_panels(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    mode: str,
    source_sha256: str = "",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        raise PanelsError("面板层顶层必须是对象")
    if payload.get("schema") != PANELS_SCHEMA_VERSION:
        errors.append(f"schema 必须是 {PANELS_SCHEMA_VERSION}")
    if payload.get("prompt_version") != PANELS_PROMPT_VERSION:
        errors.append(f"prompt_version 必须是 {PANELS_PROMPT_VERSION}")
    if payload.get("mode") != mode:
        errors.append(f"mode 必须是 {mode}")
    if source_sha256 and payload.get("source_sha256") not in (None, "", source_sha256):
        errors.append("source_sha256 与本章原文不一致")
    raw = payload.get("panels")
    if not isinstance(raw, list):
        errors.append("panels 必须是数组")
        raw = []
    if len(raw) > MAX_PANELS:
        errors.append(f"panels 最多 {MAX_PANELS} 个")
    block_map = _block_map(blocks)
    unit_map = {unit.get("id"): unit for unit in model.get("units") or [] if unit.get("id")}
    source_text = "\n".join(_block_text(block) for block in blocks or [])
    allowed = set(PANEL_TYPES_BY_MODE.get(mode, ()))
    clean_panels: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for index, panel in enumerate(raw):
        label = f"panels[{index}]"
        if not isinstance(panel, dict):
            errors.append(f"{label} 必须是对象")
            continue
        panel_id = _text(panel.get("id"), f"{label}.id", errors)
        if panel_id:
            if panel_id in seen_ids:
                errors.append(f"panel id 重复：{panel_id}")
            seen_ids.add(panel_id)
        kind = _text(panel.get("kind"), f"{label}.kind", errors)
        if kind and kind not in allowed:
            errors.append(f"{label}.kind 不属于 {mode}：{kind}")
        if kind:
            if kind in seen_kinds:
                warnings.append(f"同一类型面板出现多次：{kind}")
            seen_kinds.add(kind)
        title = _text(panel.get("title"), f"{label}.title", errors, allow_empty=True) or PANEL_LABELS.get(kind, "")
        summary = _text(panel.get("summary", ""), f"{label}.summary", errors, allow_empty=True, limit=MAX_TEXT)
        items_raw = panel.get("items")
        if not isinstance(items_raw, list):
            errors.append(f"{label}.items 必须是数组")
            items_raw = []
        if not items_raw:
            errors.append(f"{label}.items 不能为空；没有数据就不要写这个 panel")
        if len(items_raw) > MAX_ITEMS:
            errors.append(f"{label}.items 最多 {MAX_ITEMS} 条")
        items = []
        for item_index, item in enumerate(items_raw):
            clean = _check_item(item, item_index, mode, block_map, unit_map, source_text, errors)
            if clean is not None:
                items.append(clean)
        if items:
            clean_panels.append({"id": panel_id, "kind": kind, "title": title, "summary": summary, "items": items})
    clean = {
        "schema": PANELS_SCHEMA_VERSION,
        "prompt_version": PANELS_PROMPT_VERSION,
        "mode": mode,
        "source_sha256": source_sha256 or str(payload.get("source_sha256") or ""),
        "panels": clean_panels,
        "warnings": _dedupe(warnings + [str(w) for w in (payload.get("warnings") or []) if str(w).strip()]),
    }
    if errors:
        raise PanelsError("；".join(errors))
    return copy.deepcopy(clean)


def audit_panels(
    payload: Any,
    model: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    mode: str,
    source_sha256: str = "",
) -> dict[str, Any]:
    try:
        clean = validate_panels(payload, model, blocks, mode=mode, source_sha256=source_sha256)
    except PanelsError as exc:
        return {"status": "fail", "errors": [str(exc)], "warnings": [], "panels": 0, "items": 0}
    warnings = list(clean.get("warnings") or [])
    return {
        "status": "warn" if warnings else "pass",
        "errors": [],
        "warnings": warnings,
        "panels": len(clean.get("panels") or []),
        "items": sum(len(panel.get("items") or []) for panel in clean.get("panels") or []),
    }


def _system_prompt(mode: str) -> str:
    kinds = " / ".join(PANEL_TYPES_BY_MODE.get(mode, ()))
    label = MODE_LABELS.get(mode, mode)
    return "\n".join(
        [
            f"你是书库的面板编辑。当前是「{label}」模式。给定一章的章节模型与原文块，",
            "请做一次结构抽取，只输出一个 JSON 对象。",
            "",
            "硬约束：",
            "1. 顶层字段为 schema、prompt_version、mode、source_sha256、panels、warnings。"
            f"schema 写 {PANELS_SCHEMA_VERSION}，prompt_version 写 {PANELS_PROMPT_VERSION}，mode 写 {mode}。",
            f"2. panels 最多 {MAX_PANELS} 个；kind 只能是 {kinds}。每个 panel 写 id、kind、title、summary、items。",
            "3. 没有数据的 panel 不要写；整章没有可用结构就输出 panels: []。不要用空数组凑数。",
            f"4. 每个 panel 的 items 最多 {MAX_ITEMS} 条，每条写 text、blocks、units、source。"
            f"text 不超过 {MAX_TEXT} 字；blocks 是至少一个本章块 id，units 可省略。",
            "5. 人名、地名、年份、数字、图注必须在源文或图注里逐字可查。写不准就把原句放进 quote；"
            "quote 必须是某个 blocks 的逐字子串。不要编造。",
            "6. source 写「本章」或「本书 <节或章>」。不要引用书外材料，不要写「已核验」。",
            "7. 面板是结构摘要，不是第二份正文；不要写导读腔。不要输出 markdown 围栏或解释。",
            "",
            "输出只包含 JSON。",
        ]
    )


def _messages(book, chapter, model, blocks, mode, source_sha256):
    view = {
        "book": {
            k: book.get(k) for k in ("uid", "title", "author", "subjects") if isinstance(book, dict) and k in book
        },
        "chapter": {
            k: chapter.get(k)
            for k in ("uid", "n", "title", "source_sha256")
            if isinstance(chapter, dict) and k in chapter
        },
        "mode": mode,
        "source_sha256": source_sha256,
        "chapter_model": {
            "sections": model.get("sections") or [],
            "units": model.get("units") or [],
        },
        "blocks": [
            {
                "id": b.get("id"),
                "kind": b.get("kind"),
                "text": b.get("text") or "",
                **({"head": b.get("head")} if b.get("head") else {}),
            }
            for b in blocks or []
        ],
        "reminder": "只输出 JSON；每个 panel 至少 1 条 item；没有结构就输出空 panels。",
    }
    return [
        {"role": "system", "content": _system_prompt(mode)},
        {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
    ]


def _clip(text: Any, limit: int = 600) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "…"


def generate_panels(
    client,
    book,
    chapter,
    model,
    blocks,
    *,
    mode: str,
    source_sha256: str = "",
    model_name: str | None = None,
    effort: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成并校验面板。两次失败后返回空面板 + warning，不阻塞正文。"""
    messages = _messages(book, chapter, model, blocks, mode, source_sha256)
    attempts = 0
    provider_retried = False
    while True:
        try:
            payload, usage = client.complete_json(messages, model=model_name, effort=effort)
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("顶层不是对象", "", 0)
            payload = dict(payload)
            payload.setdefault("schema", PANELS_SCHEMA_VERSION)
            payload.setdefault("prompt_version", PANELS_PROMPT_VERSION)
            payload.setdefault("mode", mode)
            payload.setdefault("source_sha256", source_sha256)
            clean = validate_panels(payload, model, blocks, mode=mode, source_sha256=source_sha256)
            actual_model = model_name or getattr(client, "last_model", None) or getattr(client, "model", None)
            return clean, {"prompt_version": PANELS_PROMPT_VERSION, "model": actual_model, "usage": usage}
        except json.JSONDecodeError as exc:
            attempts += 1
            if attempts > MAX_JSON_RETRIES:
                reason = f"面板 JSON 连续失败：{_clip(exc)}"
                return empty_panels(mode, reason, source_sha256), {
                    "prompt_version": PANELS_PROMPT_VERSION,
                    "error": reason,
                }
            messages = [
                *messages,
                {"role": "user", "content": f"上一次输出不是合法 JSON：{exc}。请只输出完整 JSON。"},
            ]
        except PanelsError as exc:
            attempts += 1
            if attempts > MAX_JSON_RETRIES:
                reason = f"面板校验连续失败：{_clip(exc)}"
                return empty_panels(mode, reason, source_sha256), {
                    "prompt_version": PANELS_PROMPT_VERSION,
                    "error": reason,
                }
            messages = [
                *messages,
                {"role": "user", "content": f"上一次输出未通过面板校验：{_clip(exc)}。请修正后只输出 JSON。"},
            ]
        except ModelProviderError as exc:
            if provider_retried:
                reason = f"面板模型提供方连续失败：{exc}"
                return empty_panels(mode, reason, source_sha256), {
                    "prompt_version": PANELS_PROMPT_VERSION,
                    "error": reason,
                }
            provider_retried = True
            messages = [*messages, {"role": "user", "content": "上一次调用失败，请重试并只输出 JSON 对象。"}]
