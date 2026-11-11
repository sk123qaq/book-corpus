"""三档规则：把源文块与标注投影成 skim/standard/deep。

投影是确定性的、可重放的。输入只有源块与标注（人工或模型），没有模型调用。
"""

import re

from .mdparse import han_count

ROLES = {"spine", "claim", "conclusion", "evidence", "context", "transition", "card"}
TIER_SPECS = {
    "skim": {
        "label": "速览",
        "c_min": 0.40,
        "c_max": 0.55,
        "b_min": 0.30,
        "b_max": 0.65,
        "e_max": 0.10,
        "figures": 2,
        "wpm": 600,
    },
    "standard": {
        "label": "标准",
        "c_min": 0.60,
        "c_max": 0.75,
        "b_min": 0.50,
        "b_max": 0.80,
        "e_max": 0.25,
        "figures": 3,
        "wpm": 500,
    },
    "deep": {
        "label": "深读",
        "c_min": 0.80,
        "c_max": 0.90,
        "b_min": 0.70,
        "b_max": 1.00,
        "e_max": 0.40,
        "figures": 5,
        "wpm": 350,
    },
}
TIER_ORDER = ("skim", "standard", "deep")
OPTIONAL_CLAIM_TIER = {"standard": 1, "deep": 2}
CONTENT_KINDS = {"paragraph", "quote", "list", "image", "table", "code"}


def _sentence_boundary(text, quote):
    pos = text.find(quote)
    if pos < 0:
        return False
    before = text[:pos].rstrip()
    after = text[pos + len(quote) :].lstrip()
    ok_before = not before or before[-1] in "。！？；」』”’!?;“‘，,：:（("
    terminal = "。！？!?…；"
    ok_after = quote.rstrip()[-1:] in terminal or not after or after[0] in "。！？；」』”’!?;，‘“，,、"
    return ok_before and ok_after


def validate_annotation(annotation, blocks):
    """校验并补默认值。任何结构错误都抛 ``ValueError``。"""
    if not isinstance(annotation, dict):
        raise ValueError("annotation 必须是对象")
    by_id = {block["id"]: block for block in blocks}
    raw_blocks = annotation.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError("annotation.blocks 必须是非空数组")

    entries = []
    seen = set()
    for index, entry in enumerate(raw_blocks):
        if not isinstance(entry, dict):
            raise ValueError("annotation.blocks[%d] 必须是对象" % index)
        block_id = entry.get("id")
        if not isinstance(block_id, str):
            raise ValueError("annotation.blocks[%d].id 必须是字符串，得到 %r" % (index, block_id))
        if block_id not in by_id:
            raise ValueError("未知块 id：%r" % (block_id,))
        if block_id in seen:
            raise ValueError("块 id 重复：%s" % block_id)
        seen.add(block_id)
        role = entry.get("role")
        if role not in ROLES:
            raise ValueError("%s 的 role 非法：%r" % (block_id, role))
        priority = entry.get("priority", 3)
        if not isinstance(priority, int) or not 1 <= priority <= 3:
            raise ValueError("%s 的 priority 必须是 1..3" % block_id)
        deps = entry.get("deps", [])
        if not isinstance(deps, list):
            raise ValueError("%s 的 deps 必须是数组" % block_id)
        for dep in deps:
            if not isinstance(dep, str):
                raise ValueError("%s 的 deps 只能包含块 id 字符串，得到 %r" % (block_id, dep))
            if dep not in by_id:
                raise ValueError("%s 依赖不存在的块：%r" % (block_id, dep))
            if dep == block_id:
                raise ValueError("%s 依赖自身" % block_id)
        caption = str(entry.get("caption") or "").strip()[:80]
        source_block = by_id[block_id]
        if source_block.get("kind") == "image" and caption:
            rel = str(source_block.get("rel") or "")
            stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if (
                caption == str(source_block.get("text") or "").strip()
                or caption in (rel, stem)
                or re.search(r"\.(png|jpe?g|webp|gif|svg)$", caption, re.I)
            ):
                caption = ""
        entries.append(
            {
                "id": block_id,
                "role": role,
                "priority": priority,
                "spine": bool(entry.get("spine", False)),
                "deps": deps,
                "note": str(entry.get("note", "")),
                "caption": caption,
            }
        )

    # 模型常把过多块标成 spine；spine 一旦超过约 35%，速览档无论怎么删
    # 都压不到 55%。这里保留结论块，再按 priority 补足，其余降为普通块。
    content_count = sum(1 for block in blocks if block["kind"] in CONTENT_KINDS)
    spine_limit = max(2, round(content_count * 0.35))
    spine_entries = [entry for entry in entries if entry["spine"]]
    if len(spine_entries) > spine_limit:
        must_keep = [entry for entry in spine_entries if entry["role"] == "conclusion"]
        keep = {entry["id"] for entry in must_keep}
        remaining = max(0, spine_limit - len(must_keep))
        rest = [entry for entry in spine_entries if entry["id"] not in keep]
        rest.sort(key=lambda entry: (entry["priority"], entries.index(entry)))
        for entry in rest[:remaining]:
            keep.add(entry["id"])
        for entry in spine_entries:
            if entry["id"] not in keep:
                entry["spine"] = False

    # 只按个数封顶还不够：spine 的 deps 会一起被拉进速览，真正压不下去的
    # 是「spine + 一层 deps」的闭包字数。这里按「先降最不重要的、再降最大
    # 的」把闭包压到速览上限以内，剩下的内容交给填充去补。
    body_han = sum(block["chars"] for block in blocks)
    if body_han:
        skim_cap = TIER_SPECS["skim"]["c_max"] * body_han
        char_of = {block["id"]: block["chars"] for block in blocks}
        entry_map = {entry["id"]: entry for entry in entries}

        def _spine_closure_chars():
            keep = {entry["id"] for entry in entries if entry["spine"]}
            _closure(keep, entry_map)
            return sum(char_of.get(block_id, 0) for block_id in keep)

        for _ in range(len(entries)):
            spine_now = [entry for entry in entries if entry["spine"]]
            demotable = [entry for entry in spine_now if entry["role"] != "conclusion"]
            if len(spine_now) <= 2 or not demotable or _spine_closure_chars() <= skim_cap:
                break
            demotable.sort(key=lambda entry: (-entry["priority"], -char_of.get(entry["id"], 0)))
            demotable[0]["spine"] = False

    # deps 只描述骨架的前置关系；非骨架块的证据关系由 supports 表达。
    # 模型经常把整章写成一条 deps 链，保留这些边会让速览档无法取舍。
    for entry in entries:
        if not entry["spine"]:
            entry["deps"] = []

    role_by_id = {entry["id"]: entry["role"] for entry in entries}
    candidates = []
    raw_supports = annotation.get("supports", [])
    if isinstance(raw_supports, dict):
        for target, sources in raw_supports.items():
            if isinstance(sources, str):
                sources = [sources]
            if isinstance(sources, list):
                for source in sources:
                    candidates.append((source, target, False))
    elif isinstance(raw_supports, list):
        for item in raw_supports:
            if not isinstance(item, dict):
                candidates.append((None, None, True))
                continue
            if "from" in item or "to" in item:
                candidates.append((item.get("from"), item.get("to"), True))
            elif "id" in item and isinstance(item.get("supports"), list):
                for source in item["supports"]:
                    candidates.append((source, item["id"], False))
            else:
                candidates.append((None, None, True))
    else:
        raise ValueError("annotation.supports 必须是数组或对象")
    # 兼容模型把 supports 写在每个 block 里的变体：{id, supports:[支撑块]}。
    for entry in raw_blocks:
        if isinstance(entry, dict) and isinstance(entry.get("supports"), list):
            for source in entry["supports"]:
                candidates.append((source, entry.get("id"), False))

    norm_supports = []
    seen_supports = set()
    for source, target, strict in candidates:
        if not isinstance(source, str) or not isinstance(target, str):
            if strict:
                raise ValueError("supports 的 from/to 必须是块 id 字符串：%r" % (target,))
            continue
        if source not in seen:
            if strict:
                raise ValueError("supports.from 不是已标注块：%r" % (source,))
            continue
        if target not in seen:
            if strict:
                raise ValueError("supports.to 不是已标注块：%r" % (target,))
            continue
        target_role = role_by_id[target]
        if target_role not in ("claim", "conclusion"):
            if strict:
                raise ValueError("supports.to 必须是 claim 或 conclusion：%s" % target)
            continue
        if source == target:
            if strict:
                raise ValueError("supports 不能自指：%s" % source)
            continue
        key = (source, target)
        if key not in seen_supports:
            seen_supports.add(key)
            norm_supports.append({"from": source, "to": target})

    cards = annotation.get("cards", [])
    if not isinstance(cards, list):
        raise ValueError("annotation.cards 必须是数组")
    norm_cards = []
    card_ids = set()
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ValueError("cards[%d] 必须是对象" % index)
        cid = str(card.get("id") or "C%02d" % (index + 1))
        if cid in card_ids:
            raise ValueError("card id 重复：%s" % cid)
        card_ids.add(cid)
        trigger = card.get("trigger")
        if not isinstance(trigger, str):
            raise ValueError("%s 的 trigger 必须是块 id 字符串，得到 %r" % (cid, trigger))
        if trigger not in by_id:
            raise ValueError("%s 的 trigger 不存在：%r" % (cid, trigger))
        text = str(card.get("text") or card.get("body") or "").strip()
        if not text:
            raise ValueError("%s 的 text 为空" % cid)
        citations = card.get("citations", [])
        if not isinstance(citations, list) or not citations:
            raise ValueError("%s 的 citations 必须是非空数组" % cid)
        for citation in citations:
            if not isinstance(citation, str):
                raise ValueError("%s 的 citations 只能包含块 id 字符串，得到 %r" % (cid, citation))
            if citation not in by_id:
                raise ValueError("%s 引用不存在的块：%r" % (cid, citation))
        quotes = card.get("quotes", [])
        if not isinstance(quotes, list):
            raise ValueError("%s 的 quotes 必须是数组" % cid)
        norm_quotes = []
        for quote in quotes:
            if not isinstance(quote, dict):
                raise ValueError("%s 的 quote 必须是对象" % cid)
            quote_block = (
                quote.get("block")
                or quote.get("block_id")
                or quote.get("blockId")
                or quote.get("id")
                or quote.get("source")
            )
            quote_text = str(quote.get("text") or quote.get("quote") or "").strip()
            if not isinstance(quote_block, str) and quote_text:
                matches = [citation for citation in citations if quote_text in by_id[citation]["text"]]
                if len(matches) == 1:
                    quote_block = matches[0]
            if not isinstance(quote_block, str):
                raise ValueError("%s 的 quote.block 必须是块 id 字符串，得到 %r" % (cid, quote_block))
            if quote_block not in by_id:
                raise ValueError("%s 引文块不存在：%r" % (cid, quote_block))
            if not quote_text or quote_text not in by_id[quote_block]["text"]:
                shown = quote_text[:24] + ("…" if len(quote_text) > 24 else "")
                raise ValueError("%s 引文不在 %s 原文中：%s" % (cid, quote_block, shown or "（空）"))
            # 引文必须逐字来自原文；短于 8 字时才要求落在句边界，避免把
            # 模型合理的短语引文（如“把安禄山、史思明并称‘二圣’”）判死。
            if len(quote_text) < 8 and not _sentence_boundary(by_id[quote_block]["text"], quote_text):
                raise ValueError("%s 引文不是完整句：%s" % (cid, quote_text[:20]))
            norm_quotes.append({"block": quote_block, "text": quote_text})
        priority = card.get("priority", 2)
        if not isinstance(priority, int) or not 1 <= priority <= 3:
            raise ValueError("%s 的 priority 必须是 1..3" % cid)
        norm_cards.append(
            {
                "id": cid,
                "trigger": trigger,
                "title": str(card.get("title", "")).strip(),
                "text": text,
                "citations": citations,
                "quotes": norm_quotes,
                "priority": priority,
            }
        )

    tier_blocks = annotation.get("tier_blocks")
    if tier_blocks is not None:
        if not isinstance(tier_blocks, dict):
            raise ValueError("tier_blocks 必须是对象")
        cleaned = {}
        for tier, ids in tier_blocks.items():
            if tier not in TIER_ORDER:
                raise ValueError("未知档位：%s" % tier)
            if not isinstance(ids, list) or not ids:
                raise ValueError("tier_blocks.%s 必须是非空数组" % tier)
            for block_id in ids:
                if not isinstance(block_id, str):
                    raise ValueError("tier_blocks.%s 只能包含块 id 字符串，得到 %r" % (tier, block_id))
                if block_id not in by_id:
                    raise ValueError("tier_blocks.%s 含未知块：%r" % (tier, block_id))
            cleaned[tier] = list(dict.fromkeys(ids))
        tier_blocks = cleaned

    # 依赖闭包必须无环。
    graph = {entry["id"]: entry["deps"] for entry in entries}
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(graph, white)

    def visit(node, trail):
        if color[node] == grey:
            raise ValueError("依赖成环：%s" % " → ".join([*trail, node]))
        if color[node] == black:
            return
        color[node] = grey
        for dep in graph[node]:
            visit(dep, [*trail, node])
        color[node] = black

    for block_id in graph:
        visit(block_id, [])

    return {
        "schema": 1,
        "uid": annotation.get("uid"),
        "n": annotation.get("n"),
        "blocks": entries,
        "supports": norm_supports,
        "cards": norm_cards,
        "tier_blocks": tier_blocks,
    }


def _closure(selected, entry_map, depth=1):
    """补入有限层依赖。

    模型常把整章串成 ``B01 <- B02 <- ...`` 的顺序链；传递闭包会把
    任何后段块扩成整章，三档随之失去差异。只补直接前置块，既保留
    局部语境，又不让一条链吞掉所有取舍。
    """
    frontier = list(selected)
    for _ in range(max(0, depth)):
        next_frontier = []
        for node in frontier:
            for dep in entry_map.get(node, {}).get("deps", []):
                if dep not in selected:
                    selected.add(dep)
                    next_frontier.append(dep)
        frontier = next_frontier


def _draft_entries(blocks, cards):
    entries = []
    card_triggers = {card["trigger"] for card in cards}
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            role, spine, priority = "context", False, 3
        elif kind in ("image", "footnote"):
            role, spine, priority = "evidence", False, 2
        elif block["id"] in card_triggers:
            role, spine, priority = "claim", True, 1
        else:
            role, spine, priority = "context", False, 3
            if han_count(block["text"]) >= 60:
                role, spine = "claim", True
        entries.append({"id": block["id"], "role": role, "priority": priority, "spine": spine, "deps": []})
    return entries


def _counts(blocks, selected, body_han, total_content):
    content = [block for block in blocks if block["kind"] in CONTENT_KINDS]
    kept = [block for block in content if block["id"] in selected]
    kept_han = sum(block["chars"] for block in blocks if block["id"] in selected)
    c_block = len(kept) / total_content if total_content else 1.0
    c_char = kept_han / body_han if body_han else 1.0
    return {
        "c_block": round(c_block, 4),
        "c_char": round(c_char, 4),
        "chars": kept_han,
        "selected": {block["id"] for block in kept},
    }


def _band_ok(stats, spec):
    """覆盖率是否落在档位带内（与 G3 的判定一致）。"""
    return (
        spec["c_min"] - 1e-9 <= stats["c_char"] <= spec["c_max"] + 1e-9
        and spec["b_min"] - 1e-9 <= stats["c_block"] <= spec["b_max"] + 1e-9
    )


def _repair_band(blocks, entries, selected, protected, by_id, body_han, total_content, spec):
    """低于下限时用一次换入换出把覆盖率拉回带内。

    裁剪按优先级整块移除，遇到大块会一步跨过下限（例如 0.78）。这里换入
    一块未选中的块，再按优先级从低到高移出可让位的块，只要结果落在带内
    就采用；换不进去时保持原样，由 G3 如实报告。
    """
    current = _counts(blocks, selected, body_han, total_content)
    if current["c_char"] >= spec["c_min"] and current["c_block"] >= spec["b_min"]:
        return selected
    index = {block["id"]: position for position, block in enumerate(blocks)}
    required_now = {dep for item in entries if item["id"] in selected for dep in item.get("deps", [])}
    trim_pool = [
        item
        for item in entries
        if item["id"] in selected
        and item["id"] not in protected
        and item["id"] not in required_now
        and by_id[item["id"]]["kind"] in CONTENT_KINDS
    ]
    if current["c_char"] > spec["c_max"] and not trim_pool:
        # 已选的全是骨架、依赖和插图：换入任何块都只会再把上限顶出去，
        # 逐个候选试一遍纯属空转。
        return selected
    candidates = [
        entry
        for entry in entries
        if entry["id"] not in selected
        and by_id[entry["id"]]["kind"] in CONTENT_KINDS
        and all(dep in selected for dep in entry.get("deps", []))
    ]
    candidates.sort(key=lambda entry: (entry["priority"], by_id[entry["id"]]["chars"], index[entry["id"]]))
    for entry in candidates:
        trial = set(selected)
        trial.add(entry["id"])
        changed = True
        while changed:
            changed = False
            for item in entries:
                if item["id"] not in trial:
                    continue
                for dep in item.get("deps", []):
                    if dep not in trial:
                        trial.add(dep)
                        changed = True
        while True:
            stats = _counts(blocks, trial, body_han, total_content)
            if stats["c_char"] <= spec["c_max"] and stats["c_block"] <= spec["b_max"]:
                break
            required = {dep for item in entries if item["id"] in trial for dep in item.get("deps", [])}
            removable = [
                item
                for item in entries
                if item["id"] in trial
                and item["id"] != entry["id"]
                and item["id"] not in protected
                and item["id"] not in required
                and by_id[item["id"]]["kind"] in CONTENT_KINDS
            ]
            if not removable:
                break
            removable.sort(key=lambda item: (item["priority"], index[item["id"]]))
            trial.discard(removable[-1]["id"])
        if _band_ok(_counts(blocks, trial, body_han, total_content), spec):
            return trial
    return selected


def draft_annotation(blocks):
    """无模型时的确定性标注：长段与讲解触发块为脊，其余上下文。"""
    return {
        "schema": 1,
        "blocks": _draft_entries(blocks, []),
        "supports": [],
        "cards": [],
        "draft": True,
    }


def project(blocks, annotation, tier, draft=False):
    """投影一档。返回 ``{blocks, cards, stats, captions, draft}``；blocks 保持源序。"""
    if tier not in TIER_SPECS:
        raise ValueError("未知档位：%s" % tier)
    spec = TIER_SPECS[tier]
    by_id = {block["id"]: block for block in blocks}
    content = [block for block in blocks if block["kind"] in CONTENT_KINDS]
    total_content = len(content)
    body_han = sum(block["chars"] for block in blocks)
    if annotation is None:
        stats = _counts(blocks, {block["id"] for block in blocks}, body_han, total_content)
        stats["e"] = 0.0
        stats["notes"] = 0
        stats["minutes"] = max(1, round(stats["chars"] / spec["wpm"]))
        stats.pop("selected", None)
        return {
            "tier": tier,
            "blocks": list(blocks),
            "cards": [],
            "stats": stats,
            "captions": {},
            "draft": True,
        }
    entries = annotation.get("blocks") or _draft_entries(blocks, annotation.get("cards", []))
    entry_map = {entry["id"]: entry for entry in entries}
    for block in blocks:
        if block["id"] not in entry_map:
            entry = {
                "id": block["id"],
                "role": "context",
                "priority": 3,
                "spine": False,
                "deps": [],
            }
            entries.append(entry)
            entry_map[block["id"]] = entry
    cards = annotation.get("cards", [])
    override = (annotation.get("tier_blocks") or {}).get(tier)

    headings = {block["id"] for block in blocks if block["kind"] == "heading"}
    # 插图不是可删的“语境”。有 rel 的图片块在各档都保留；
    # 超限时让低优先级段落让位，而不是把图丢掉。
    figures = {block["id"] for block in blocks if block["kind"] == "image" and block.get("rel")}
    protected = {entry["id"] for entry in entries if entry["spine"]} | headings | figures

    if override:
        selected = set(override) | headings
        _closure(selected, entry_map)
    else:
        selected = {block["id"] for block in content if entry_map.get(block["id"], {}).get("spine")}
        _closure(selected, entry_map)
        selected |= headings
        selected |= figures

        supports = annotation.get("supports", [])
        claims = [entry for entry in entries if entry["role"] in ("claim", "conclusion")]
        claims.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        limit = OPTIONAL_CLAIM_TIER.get(tier, 0)
        selected_claims = [entry for entry in claims if entry["id"] in selected]
        for entry in claims:
            if limit and len(selected_claims) < limit and entry["id"] not in selected:
                selected.add(entry["id"])
                _closure(selected, entry_map)
                selected_claims.append(entry)

        # 每条主张至少带一条证据；深读/研读带全部证据。
        evidence = [entry for entry in entries if entry["role"] == "evidence"]
        evidence.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        for claim in selected_claims:
            linked = [item["from"] for item in supports if item["to"] == claim["id"]]
            if not linked:
                continue
            take = linked if tier == "deep" else linked[:1]
            for block_id in take:
                selected.add(block_id)
                _closure(selected, entry_map)

        # 上下文/转折：浏览一档只留一条，深读全留。
        context = [entry for entry in entries if entry["role"] in ("context", "transition")]
        context.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        for index, entry in enumerate(context):
            if entry["id"] in selected:
                continue
            keep = tier != "skim" or index < 1
            if tier == "standard":
                keep = entry["priority"] <= 2
            if keep:
                selected.add(entry["id"])

        # 按预算补足覆盖率：先补高优先级。
        filled = _counts(blocks, selected, body_han, total_content)
        candidates = [
            entry for entry in entries if entry["id"] not in selected and by_id[entry["id"]]["kind"] in CONTENT_KINDS
        ]
        candidates.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        while filled["c_block"] < spec["c_min"] or filled["c_char"] < spec["c_min"]:
            if not candidates:
                break
            entry = candidates.pop(0)
            size = by_id[entry["id"]]["chars"]
            remaining = spec["c_max"] * body_han - filled["chars"]
            if size > remaining:
                continue
            if any(dep not in selected for dep in entry.get("deps", [])):
                continue
            selected.add(entry["id"])
            filled = _counts(blocks, selected, body_han, total_content)

        # 超出上限就退回填充块；只保护 spine 和标题，证据/语境可让位给硬上限。
        removable = [
            entry
            for entry in entries
            if entry["id"] in selected and entry["id"] not in protected and by_id[entry["id"]]["kind"] in CONTENT_KINDS
        ]
        removable.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        while removable:
            current = _counts(blocks, selected, body_han, total_content)
            if current["c_block"] <= spec["c_max"] and current["c_char"] <= spec["c_max"]:
                break
            drop = removable.pop()
            selected.discard(drop["id"])

        # 超限回退可能把块数压到下限以下；用能装进字符预算的小块补回。
        filled = _counts(blocks, selected, body_han, total_content)
        if filled["c_block"] < spec["c_min"] or filled["c_char"] < spec["c_min"]:
            candidates = [
                entry
                for entry in entries
                if entry["id"] not in selected and by_id[entry["id"]]["kind"] in CONTENT_KINDS
            ]
            candidates.sort(
                key=lambda entry: (
                    entry["priority"],
                    by_id[entry["id"]]["chars"],
                    blocks.index(by_id[entry["id"]]),
                )
            )
            while filled["c_block"] < spec["c_min"] or filled["c_char"] < spec["c_min"]:
                if not candidates:
                    break
                entry = candidates.pop(0)
                size = by_id[entry["id"]]["chars"]
                remaining = spec["c_max"] * body_han - filled["chars"]
                if size > remaining:
                    continue
                if any(dep not in selected for dep in entry.get("deps", [])):
                    continue
                selected.add(entry["id"])
                filled = _counts(blocks, selected, body_han, total_content)

    # 依赖闭包必须在裁剪之后仍然成立：补齐缺失依赖，再在不破坏
    # 其他已选块依赖的前提下裁掉可让位的块。
    changed = True
    while changed:
        changed = False
        for entry in entries:
            if entry["id"] not in selected:
                continue
            for dep in entry.get("deps", []):
                if dep not in selected:
                    selected.add(dep)
                    changed = True

    while True:
        current = _counts(blocks, selected, body_han, total_content)
        if current["c_block"] <= spec["c_max"] and current["c_char"] <= spec["c_max"]:
            break
        required = {dep for entry in entries if entry["id"] in selected for dep in entry.get("deps", [])}
        removable = [
            entry
            for entry in entries
            if entry["id"] in selected
            and entry["id"] not in protected
            and entry["id"] not in required
            and by_id[entry["id"]]["kind"] in CONTENT_KINDS
        ]
        if not removable:
            break
        removable.sort(key=lambda entry: (entry["priority"], blocks.index(by_id[entry["id"]])))
        selected.discard(removable[-1]["id"])

    selected = _repair_band(blocks, entries, selected, protected, by_id, body_han, total_content, spec)
    stats = _counts(blocks, selected, body_han, total_content)

    kept_cards = []
    card_han = 0
    budget = int(spec["e_max"] * body_han)
    for card in sorted(cards, key=lambda item: (item["priority"], item["id"])):
        if card["trigger"] not in selected:
            continue
        size = han_count(card["text"])
        if card_han + size > budget:
            continue
        kept_cards.append(card)
        card_han += size
    note_han = card_han
    stats["e"] = round(card_han / body_han, 4) if body_han else 0.0
    stats["notes"] = len(kept_cards)
    stats["minutes"] = max(1, round((stats["chars"] + note_han) / spec["wpm"]))
    stats.pop("selected", None)

    kept_ids = {block["id"] for block in blocks if block["id"] in selected}
    kept_blocks = [block for block in blocks if block["id"] in kept_ids]
    return {
        "tier": tier,
        "blocks": kept_blocks,
        "cards": kept_cards,
        "stats": stats,
        "captions": {entry["id"]: entry["caption"] for entry in entries if entry.get("caption")},
        "draft": bool(draft),
    }
