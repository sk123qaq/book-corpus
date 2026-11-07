"""视觉读本的编辑层内容模型。

只描述"加在原文之间/之上"的东西：章节导语、分节标题、图注、图表、
旁批、读后要点。正文永远来自 mdparse 的块，由 render 逐字插入，
模型不能改写原文。schema 固定为 1，新增字段必须先升版本。
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA = 1
TEMPLATES = ("xerox",)
CHART_KINDS = ("bars", "ratio", "compare", "steps")
MAX_CHARS = {
    "hero.kicker": 30,
    "hero.title_line": 32,
    "hero.lead": 160,
    "hero.foot_left": 60,
    "overview.title": 14,
    "overview.text": 90,
    "section.title": 56,
    "section.intro": 140,
    "figure.title": 48,
    "figure.caption": 240,
    "figure.note": 140,
    "chart.title": 40,
    "chart.caption": 220,
    "chart.note": 140,
    "callout.kicker": 16,
    "callout.title": 36,
    "callout.text": 180,
    "takeaways.title": 36,
    "takeaway.title": 22,
    "takeaway.text": 110,
}
NUMBER_RE = re.compile(r"\d[\d,.]*")
SECTION_NUM_RE = re.compile(r"^\s*(?:\d+[.、)](?!\d)|[一二三四五六七八九十]+[、.])\s*")


class ContentError(ValueError):
    """编辑层内容不合法；消息直接回喂模型做第二轮修复。"""


def _text(value: Any, field: str, *, required: bool = True, limit: int | None = None) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ContentError("%s 必须是字符串" % field)
    value = " ".join(value.split())
    if required and not value:
        raise ContentError("%s 不能为空" % field)
    if limit is not None and len(value) > limit:
        raise ContentError("%s 超过 %d 字（当前 %d 字）" % (field, limit, len(value)))
    return value


def _list(value: Any, field: str, *, lo: int = 0, hi: int = 99) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContentError("%s 必须是数组" % field)
    if len(value) < lo or len(value) > hi:
        raise ContentError("%s 需要 %d–%d 项（当前 %d 项）" % (field, lo, hi, len(value)))
    return value


def _anchor(value: Any, positions: dict[str, int], field: str) -> str:
    anchor = _text(value, field, limit=16)
    if anchor not in positions:
        raise ContentError("%s 指向不存在的块 %s" % (field, anchor))
    return anchor


def _order(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ContentError("%s 必须是整数" % field) from None


def _num(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContentError("%s 必须是数字" % field)
    if value != value or value in (float("inf"), float("-inf")):
        raise ContentError("%s 必须是有限数字" % field)
    return float(value)


def _dict(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContentError("%s 必须是对象" % field)
    return value


def validate_content(payload: Any, blocks: list[dict], *, template: str | None = None) -> dict:
    """校验并归一化编辑层。返回只含白名单字段的新 dict。"""
    raw = _dict(payload, "content")
    if int(raw.get("schema") or SCHEMA) != SCHEMA:
        raise ContentError("schema 必须是 %d" % SCHEMA)
    name = _text(raw.get("template") or (template or "xerox"), "template", limit=20)
    if name not in TEMPLATES:
        raise ContentError("template 必须是 %s" % "/".join(TEMPLATES))
    positions = {block["id"]: i for i, block in enumerate(blocks)}
    images = {block["id"] for block in blocks if block["kind"] == "image"}

    hero_raw = _dict(raw.get("hero"), "hero")
    hero = {
        "kicker": _text(hero_raw.get("kicker"), "hero.kicker", required=False, limit=MAX_CHARS["hero.kicker"]),
        "title_lines": [
            _text(line, "hero.title_line", limit=MAX_CHARS["hero.title_line"])
            for line in _list(hero_raw.get("title_lines"), "hero.title_lines", lo=0, hi=3)
        ],
        "lead": _text(hero_raw.get("lead"), "hero.lead", required=False, limit=MAX_CHARS["hero.lead"]),
        "foot_left": _text(
            hero_raw.get("foot_left"), "hero.foot_left", required=False, limit=MAX_CHARS["hero.foot_left"]
        ),
    }

    overview = []
    for index, item_raw in enumerate(_list(raw.get("overview"), "overview", hi=4), 1):
        item = _dict(item_raw, "overview[%d]" % index)
        overview.append(
            {
                "no": _text(item.get("no") or "%02d" % index, "overview.no", limit=4),
                "title": _text(item.get("title"), "overview.title", limit=MAX_CHARS["overview.title"]),
                "text": _text(item.get("text"), "overview.text", limit=MAX_CHARS["overview.text"]),
            }
        )

    sections = []
    seen_sections: set[str] = set()
    last = -1
    for index, item_raw in enumerate(_list(raw.get("sections"), "sections", hi=10), 1):
        item = _dict(item_raw, "sections[%d]" % index)
        anchor = _anchor(item.get("anchor"), positions, "sections[%d].anchor" % index)
        if anchor in seen_sections:
            raise ContentError("sections 锚点重复：%s" % anchor)
        if positions[anchor] <= last:
            raise ContentError("sections 必须按原文顺序排列（%s 位置倒退）" % anchor)
        seen_sections.add(anchor)
        last = positions[anchor]
        sections.append(
            {
                "id": "s%02d" % index,
                "anchor": anchor,
                "title": SECTION_NUM_RE.sub(
                    "",
                    _text(item.get("title"), "sections[%d].title" % index, limit=MAX_CHARS["section.title"]),
                ),
                "intro": _text(
                    item.get("intro"), "sections[%d].intro" % index, required=False, limit=MAX_CHARS["section.intro"]
                ),
            }
        )

    figures = {}
    for anchor_raw, spec_raw in _dict(raw.get("figures"), "figures").items():
        anchor = _anchor(anchor_raw, positions, "figures 的键")
        if anchor not in images:
            raise ContentError(
                "figures 只能标注图片块，%s 是 %s" % (anchor, next(b["kind"] for b in blocks if b["id"] == anchor))
            )
        spec = _dict(spec_raw, "figures.%s" % anchor)
        figures[anchor] = {
            "title": _text(
                spec.get("title"), "figures.%s.title" % anchor, required=False, limit=MAX_CHARS["figure.title"]
            ),
            "caption": _text(
                spec.get("caption"), "figures.%s.caption" % anchor, required=False, limit=MAX_CHARS["figure.caption"]
            ),
            "note": _text(spec.get("note"), "figures.%s.note" % anchor, required=False, limit=MAX_CHARS["figure.note"]),
            "redraw": bool(spec.get("redraw", False)),
        }

    charts = []
    for index, item_raw in enumerate(_list(raw.get("charts"), "charts", hi=6), 1):
        item = _dict(item_raw, "charts[%d]" % index)
        kind = _text(item.get("kind"), "charts[%d].kind" % index, limit=12)
        if kind not in CHART_KINDS:
            raise ContentError("charts[%d].kind 必须是 %s" % (index, "/".join(CHART_KINDS)))
        data = _dict(item.get("data"), "charts[%d].data" % index)
        clean_data = _validate_chart_data(kind, data, index)
        charts.append(
            {
                "id": "c%02d" % index,
                "anchor": _anchor(item.get("anchor"), positions, "charts[%d].anchor" % index),
                "after": bool(item.get("after", True)),
                "kind": kind,
                "title": _text(item.get("title"), "charts[%d].title" % index, limit=MAX_CHARS["chart.title"]),
                "caption": _text(
                    item.get("caption"), "charts[%d].caption" % index, required=False, limit=MAX_CHARS["chart.caption"]
                ),
                "note": _text(
                    item.get("note"), "charts[%d].note" % index, required=False, limit=MAX_CHARS["chart.note"]
                ),
                "data": clean_data,
            }
        )

    callouts = []
    for index, item_raw in enumerate(_list(raw.get("callouts"), "callouts", hi=6), 1):
        item = _dict(item_raw, "callouts[%d]" % index)
        callouts.append(
            {
                "anchor": _anchor(item.get("anchor"), positions, "callouts[%d].anchor" % index),
                "after": bool(item.get("after", True)),
                "kicker": _text(
                    item.get("kicker"), "callouts[%d].kicker" % index, required=False, limit=MAX_CHARS["callout.kicker"]
                ),
                "title": _text(item.get("title"), "callouts[%d].title" % index, limit=MAX_CHARS["callout.title"]),
                "text": _text(item.get("text"), "callouts[%d].text" % index, limit=MAX_CHARS["callout.text"]),
            }
        )

    takeaways = None
    take_raw = raw.get("takeaways")
    if take_raw is not None:
        take_raw = _dict(take_raw, "takeaways")
        items = []
        for index, item_raw in enumerate(_list(take_raw.get("items"), "takeaways.items", lo=3, hi=5), 1):
            item = _dict(item_raw, "takeaways.items[%d]" % index)
            items.append(
                {
                    "title": _text(
                        item.get("title"), "takeaways.items[%d].title" % index, limit=MAX_CHARS["takeaway.title"]
                    ),
                    "text": _text(
                        item.get("text"), "takeaways.items[%d].text" % index, limit=MAX_CHARS["takeaway.text"]
                    ),
                }
            )
        takeaways = {
            "anchor": _anchor(take_raw.get("anchor"), positions, "takeaways.anchor"),
            "title": _text(take_raw.get("title"), "takeaways.title", limit=MAX_CHARS["takeaways.title"]),
            "items": items,
        }

    content = {
        "schema": SCHEMA,
        "template": name,
        "hero": hero,
        "overview": overview,
        "sections": sections,
        "figures": figures,
        "charts": charts,
        "callouts": callouts,
        "takeaways": takeaways,
    }
    for key in ("source_sha256", "prompt_version", "model", "generated_at"):
        if raw.get(key):
            content[key] = _text(raw[key], key, limit=80)
    return content


def _validate_chart_data(kind: str, data: dict, index: int) -> dict:
    prefix = "charts[%d].data" % index
    if kind == "bars":
        labels = [
            _text(x, prefix + ".labels", limit=24) for x in _list(data.get("labels"), prefix + ".labels", lo=2, hi=6)
        ]
        values = [_num(x, prefix + ".values") for x in _list(data.get("values"), prefix + ".values", lo=2, hi=6)]
        if len(labels) != len(values):
            raise ContentError("%s.labels 与 values 数量必须一致" % prefix)
        return {
            "labels": labels,
            "values": values,
            "unit": _text(data.get("unit"), prefix + ".unit", required=False, limit=12),
            "highlight": _order(data.get("highlight", -1), prefix + ".highlight"),
        }
    if kind == "ratio":
        return {
            "left_label": _text(data.get("left_label"), prefix + ".left_label", limit=24),
            "right_label": _text(data.get("right_label"), prefix + ".right_label", limit=24),
            "left_value": _num(data.get("left_value"), prefix + ".left_value"),
            "right_value": _num(data.get("right_value"), prefix + ".right_value"),
            "note": _text(data.get("note"), prefix + ".note", required=False, limit=120),
        }
    if kind == "compare":
        headers = [
            _text(x, prefix + ".headers", limit=30) for x in _list(data.get("headers"), prefix + ".headers", lo=2, hi=4)
        ]
        rows = []
        for row_index, row in enumerate(_list(data.get("rows"), prefix + ".rows", lo=2, hi=16), 1):
            cells = [
                _text(x, prefix + ".rows[%d]" % row_index, limit=80)
                for x in _list(row, prefix + ".rows[%d]" % row_index, lo=len(headers), hi=len(headers))
            ]
            rows.append(cells)
        return {"headers": headers, "rows": rows}
    steps = []
    for step_index, step in enumerate(_list(data.get("steps"), prefix + ".steps", lo=3, hi=5), 1):
        step = _dict(step, prefix + ".steps[%d]" % step_index)
        steps.append(
            {
                "title": _text(step.get("title"), prefix + ".steps[%d].title" % step_index, limit=24),
                "text": _text(step.get("text"), prefix + ".steps[%d].text" % step_index, limit=90),
            }
        )
    return {"steps": steps}


def number_warnings(content: dict, blocks: list[dict]) -> list[str]:
    """编辑层里出现的数字若不在原文，给出警告（不阻断，写进门禁报告）。"""
    source = "\n".join(block.get("text", "") for block in blocks)
    seen = set(NUMBER_RE.findall(source))
    warnings = []
    for path, value in _walk(content):
        if any(
            part
            in {
                "schema",
                "id",
                "no",
                "anchor",
                "source_sha256",
                "prompt_version",
                "model",
                "generated_at",
                "kicker",
                "foot_left",
            }
            for part in path
        ):
            continue
        if isinstance(value, str):
            for number in NUMBER_RE.findall(value):
                if number not in seen:
                    warnings.append("%s 里的数字 %s 不在原文" % (".".join(path) or "content", number))
    return sorted(set(warnings))


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))
    else:
        yield path, value
