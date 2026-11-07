"""编辑层：章级旁批、图版、节拍与收束。

模型只产出 ``annotation.json``。人写的这一层单独放在 ``editorial.json``，
按块锚点挂载，重新生成读本时不会被覆盖。这里只做结构校验与引用校验；
真正的版式在 :mod:`bookcorpus.reading.visuals`。
"""

from __future__ import annotations

import re

EDITORIAL_SCHEMA = 1
TIERS = ("skim", "standard", "deep", "research")
DEFAULT_TIERS = ("standard", "deep", "research")
PLACEMENTS = ("after", "inside", "before")
VISUAL_KINDS = ("two_lives", "ratio", "scale", "curve", "ledger", "steps", "framing", "prediction")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class EditorialError(ValueError):
    """编辑层不合法。消息直接给作者看，带字段路径。"""


def _fail(path: str, message: str) -> None:
    raise EditorialError(f"{path} {message}")


def _require_dict(value, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "必须是对象")
    return value


def _require_list(value, path: str) -> list:
    if not isinstance(value, list):
        _fail(path, "必须是数组")
    return value


def _require_text(value, path: str, *, max_len: int = 400) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "必须是非空字符串")
    text = value.strip()
    if len(text) > max_len:
        _fail(path, f"不能超过 {max_len} 字")
    return text


def _optional_text(value, path: str, *, max_len: int = 400) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail(path, "必须是字符串")
    text = value.strip()
    if len(text) > max_len:
        _fail(path, f"不能超过 {max_len} 字")
    return text


def _tiers(value, path: str, *, default=DEFAULT_TIERS) -> list[str]:
    if value is None:
        value = list(default)
    tiers = _require_list(value, path)
    if not tiers:
        _fail(path, "不能为空")
    out = []
    for index, tier in enumerate(tiers):
        if tier not in TIERS:
            _fail(f"{path}[{index}]", f"未知档位：{tier!r}")
        if tier not in out:
            out.append(tier)
    return out


def _block_id(value, path: str, block_ids: set[str]) -> str:
    block = _require_text(value, path, max_len=40)
    if block not in block_ids:
        _fail(path, f"引用了不存在的块：{block}")
    return block


def _visual_data(kind: str, data, path: str) -> dict:
    data = _require_dict(data, path)
    if kind == "two_lives":
        if not isinstance(data.get("statistical"), int) or not 1 <= data["statistical"] <= 240:
            _fail(path + ".statistical", "必须是 1–240 的整数")
        if not isinstance(data.get("identified"), int) or not 1 <= data["identified"] <= 10:
            _fail(path + ".identified", "必须是 1–10 的整数")
    elif kind == "ratio":
        for side in ("left", "right"):
            item = _require_dict(data.get(side), f"{path}.{side}")
            _require_text(item.get("label"), f"{path}.{side}.label", max_len=40)
            _require_text(item.get("display"), f"{path}.{side}.display", max_len=40)
            if not isinstance(item.get("value"), (int, float)) or item["value"] <= 0:
                _fail(f"{path}.{side}.value", "必须是正数")
        _require_text(data.get("ratio_label"), path + ".ratio_label", max_len=40)
    elif kind == "scale":
        bars = _require_list(data.get("bars"), path + ".bars")
        if not 2 <= len(bars) <= 4:
            _fail(path + ".bars", "必须有 2–4 条")
        for index, bar in enumerate(bars):
            bar = _require_dict(bar, f"{path}.bars[{index}]")
            _require_text(bar.get("label"), f"{path}.bars[{index}].label", max_len=48)
            _require_text(bar.get("display"), f"{path}.bars[{index}].display", max_len=40)
            if not isinstance(bar.get("value"), (int, float)) or bar["value"] <= 0:
                _fail(f"{path}.bars[{index}].value", "必须是正数")
            tone = bar.get("tone")
            if tone not in (None, "", "accent", "muted", "pine"):
                _fail(f"{path}.bars[{index}].tone", "只能是 accent/muted/pine")
        _optional_text(data.get("factor"), path + ".factor", max_len=40)
        _optional_text(data.get("note"), path + ".note", max_len=240)
    elif kind == "curve":
        _require_text(data.get("x_label"), path + ".x_label", max_len=40)
        _require_text(data.get("y_label"), path + ".y_label", max_len=40)
        series = _require_list(data.get("series"), path + ".series")
        if not 1 <= len(series) <= 2:
            _fail(path + ".series", "必须有 1–2 条曲线")
        for index, item in enumerate(series):
            item = _require_dict(item, f"{path}.series[{index}]")
            _require_text(item.get("label"), f"{path}.series[{index}].label", max_len=40)
            if item.get("shape") not in ("concave", "convex", "s_curve", "loss_aversion", "linear"):
                _fail(f"{path}.series[{index}].shape", "曲线形状不合法")
            annotations = item.get("annotations") or []
            annotations = _require_list(annotations, f"{path}.series[{index}].annotations")
            if len(annotations) > 4:
                _fail(f"{path}.series[{index}].annotations", "最多 4 条")
            for note_index, note in enumerate(annotations):
                note = _require_dict(note, f"{path}.series[{index}].annotations[{note_index}]")
                _require_text(note.get("text"), f"{path}.series[{index}].annotations[{note_index}].text", max_len=60)
                for axis in ("x", "y"):
                    value = note.get(axis)
                    if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                        _fail(f"{path}.series[{index}].annotations[{note_index}].{axis}", "必须是 0–1")
        _optional_text(data.get("note"), path + ".note", max_len=240)
    elif kind == "ledger":
        rows = _require_list(data.get("rows"), path + ".rows")
        if not 1 <= len(rows) <= 6:
            _fail(path + ".rows", "必须有 1–6 行")
        for index, row in enumerate(rows):
            row = _require_dict(row, f"{path}.rows[{index}]")
            for key in ("label", "cash", "opportunity", "choice"):
                _require_text(row.get(key), f"{path}.rows[{index}].{key}", max_len=60)
    elif kind == "steps":
        steps = _require_list(data.get("steps"), path + ".steps")
        if not 1 <= len(steps) <= 4:
            _fail(path + ".steps", "必须有 1–4 步")
        for index, step in enumerate(steps):
            step = _require_dict(step, f"{path}.steps[{index}]")
            _require_text(step.get("label"), f"{path}.steps[{index}].label", max_len=40)
            _optional_text(step.get("detail"), f"{path}.steps[{index}].detail", max_len=80)
        result = _require_dict(data.get("result"), path + ".result")
        _require_text(result.get("label"), path + ".result.label", max_len=40)
        _optional_text(result.get("detail"), path + ".result.detail", max_len=80)
    elif kind == "framing":
        for key in ("base", "credit", "cash", "delta"):
            if not isinstance(data.get(key), (int, float)) or data[key] < 0:
                _fail(f"{path}.{key}", "必须是非负数")
        if data["credit"] <= data["base"] or data["cash"] != data["base"]:
            _fail(path, "需要 credit > base = cash")
    elif kind == "prediction":
        _require_text(data.get("question"), path + ".question", max_len=240)
        options = _require_list(data.get("options"), path + ".options")
        if not 2 <= len(options) <= 5:
            _fail(path + ".options", "必须有 2–5 个选项")
        for index, option in enumerate(options):
            _require_text(option, f"{path}.options[{index}]", max_len=40)
        _require_text(data.get("reveal"), path + ".reveal", max_len=240)
    return data


def validate_editorial(payload, blocks, *, uid: str | None = None, n: int | None = None) -> dict:
    """校验并归一化编辑层。``blocks`` 是当前章的源块（带 id/text/kind）。"""
    payload = _require_dict(payload, "editorial")
    if payload.get("schema") != EDITORIAL_SCHEMA:
        _fail("schema", f"必须是 {EDITORIAL_SCHEMA}")
    got_uid = _require_text(payload.get("uid"), "uid", max_len=40)
    try:
        got_n = int(payload.get("n"))
    except (TypeError, ValueError):
        _fail("n", "必须是正整数")
    if got_n <= 0:
        _fail("n", "必须是正整数")
    if uid is not None and got_uid != uid:
        _fail("uid", f"与章节不一致：{got_uid} != {uid}")
    if n is not None and got_n != int(n):
        _fail("n", f"与章节不一致：{got_n} != {n}")

    block_ids = {block["id"] for block in blocks}
    order = {block["id"]: index for index, block in enumerate(blocks)}
    text_by_id = {block["id"]: block.get("text") or "" for block in blocks}

    beats_out = []
    beats = _require_list(payload.get("beats") or [], "beats")
    if len(beats) > 8:
        _fail("beats", "最多 8 个")
    seen_beats = set()
    seen_ids = set()
    last_order = -1
    for index, beat in enumerate(beats):
        path = f"beats[{index}]"
        beat = _require_dict(beat, path)
        beat_id = _require_text(beat.get("id"), path + ".id", max_len=40)
        if not ID_RE.match(beat_id):
            _fail(path + ".id", "只能用小写字母、数字和连字符")
        if beat_id in seen_beats:
            _fail(path + ".id", f"重复：{beat_id}")
        seen_beats.add(beat_id)
        if beat_id in seen_ids:
            _fail(path + ".id", f"与图版 id 重复：{beat_id}")
        seen_ids.add(beat_id)
        before = _block_id(beat.get("before_block"), path + ".before_block", block_ids)
        if order[before] < last_order:
            _fail(path + ".before_block", "节拍顺序必须与块顺序一致")
        last_order = order[before]
        beats_out.append(
            {
                "id": beat_id,
                "title": _require_text(beat.get("title"), path + ".title", max_len=60),
                "question": _optional_text(beat.get("question"), path + ".question", max_len=120),
                "before_block": before,
                "tiers": _tiers(beat.get("tiers"), path + ".tiers"),
            }
        )

    visuals_out = []
    visuals = _require_list(payload.get("visuals") or [], "visuals")
    if len(visuals) > 12:
        _fail("visuals", "最多 12 个")
    seen_visuals = set()
    for index, visual in enumerate(visuals):
        path = f"visuals[{index}]"
        visual = _require_dict(visual, path)
        visual_id = _require_text(visual.get("id"), path + ".id", max_len=40)
        if not ID_RE.match(visual_id):
            _fail(path + ".id", "只能用小写字母、数字和连字符")
        if visual_id in seen_visuals:
            _fail(path + ".id", f"重复：{visual_id}")
        seen_visuals.add(visual_id)
        if visual_id in seen_ids:
            _fail(path + ".id", f"与节拍 id 重复：{visual_id}")
        seen_ids.add(visual_id)
        kind = _require_text(visual.get("kind"), path + ".kind", max_len=40)
        if kind not in VISUAL_KINDS:
            _fail(path + ".kind", f"未知类型：{kind}")
        placement = visual.get("placement") or "after"
        if placement not in PLACEMENTS:
            _fail(path + ".placement", f"只能是 {'/'.join(PLACEMENTS)}")
        if kind in ("two_lives", "ratio", "scale", "curve", "ledger") and placement != "after":
            _fail(path + ".placement", "图版必须放在块后")
        if placement == "before" and kind != "prediction":
            _fail(path + ".placement", "只有 prediction 可以放在块前")
        after = _block_id(visual.get("after_block"), path + ".after_block", block_ids)
        tiers = _tiers(visual.get("tiers"), path + ".tiers")
        data = _visual_data(kind, visual.get("data"), path + ".data")
        claims = []
        raw_claims = _require_list(visual.get("claims") or [], path + ".claims")
        for claim_index, claim in enumerate(raw_claims):
            claim_path = f"{path}.claims[{claim_index}]"
            claim = _require_dict(claim, claim_path)
            block = _block_id(claim.get("block"), claim_path + ".block", block_ids)
            quote = _require_text(claim.get("quote"), claim_path + ".quote", max_len=400)
            if quote not in text_by_id[block]:
                _fail(claim_path + ".quote", f"不是 {block} 的逐字原文")
            claims.append({"block": block, "quote": quote})
        if kind != "prediction" and not claims:
            _fail(path + ".claims", "数值/结构图版至少需要一条出处")
        visuals_out.append(
            {
                "id": visual_id,
                "kind": kind,
                "title": _optional_text(visual.get("title"), path + ".title", max_len=60),
                "after_block": after,
                "placement": placement,
                "tiers": tiers,
                "data": data,
                "caption": _optional_text(visual.get("caption"), path + ".caption", max_len=400),
                "claims": claims,
            }
        )

    takeaways_out = []
    takeaways = _require_list(payload.get("takeaways") or [], "takeaways")
    if len(takeaways) > 5:
        _fail("takeaways", "最多 5 条")
    for index, item in enumerate(takeaways):
        path = f"takeaways[{index}]"
        item = _require_dict(item, path)
        sources = item.get("sources") or []
        sources = _require_list(sources, path + ".sources")
        clean_sources = []
        for source_index, source in enumerate(sources):
            clean_sources.append(_block_id(source, f"{path}.sources[{source_index}]", block_ids))
        takeaways_out.append(
            {
                "text": _require_text(item.get("text"), path + ".text", max_len=240),
                "sources": clean_sources,
            }
        )

    note_tags = payload.get("note_tags") or {}
    note_tags = _require_dict(note_tags, "note_tags")
    tags_out = {}
    for block, tag in note_tags.items():
        _block_id(block, f"note_tags.{block}", block_ids)
        tags_out[block] = _require_text(tag, f"note_tags.{block}", max_len=24)

    return {
        "schema": EDITORIAL_SCHEMA,
        "uid": got_uid,
        "n": got_n,
        "takeaways_title": _optional_text(payload.get("takeaways_title"), "takeaways_title", max_len=80),
        "takeaways_note": _optional_text(payload.get("takeaways_note"), "takeaways_note", max_len=200),
        "takeaways_tiers": _tiers(payload.get("takeaways_tiers"), "takeaways_tiers"),
        "beats": beats_out,
        "visuals": visuals_out,
        "takeaways": takeaways_out,
        "note_tags": tags_out,
    }


def audit_editorial(payload, blocks, projections, *, uid: str | None = None, n: int | None = None) -> list[str]:
    """在结构校验之外，检查图版锚点是否落在它声明的每个档位里。"""
    try:
        clean = validate_editorial(payload, blocks, uid=uid, n=n)
    except EditorialError as exc:
        return [str(exc)]
    problems = []
    block_ids = {block["id"] for block in blocks}
    for tier, projection in projections.items():
        ids = {block["id"] for block in projection["blocks"]}
        for visual in clean["visuals"]:
            if tier not in visual["tiers"]:
                continue
            if visual["after_block"] not in ids:
                problems.append(f"{tier} 图版 {visual['id']} 的锚点 {visual['after_block']} 不在本档")
        for beat in clean["beats"]:
            if tier in beat["tiers"] and beat["before_block"] not in ids and beat["before_block"] in block_ids:
                # 节拍锚点不在本档时，渲染器不会输出这个节拍；这是允许的，
                # 但如果节拍声称覆盖本档，作者通常希望它真的出现，给出提示。
                problems.append(f"{tier} 节拍 {beat['id']} 的锚点 {beat['before_block']} 不在本档")
    return problems
