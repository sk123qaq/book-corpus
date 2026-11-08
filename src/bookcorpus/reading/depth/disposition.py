"""四读档位的确定性处置计划。

模型只给结构，这里不给模型任何自由裁量：同一份 chapter_model 在四个档位下
必须得到同一套逐块处置。``skim`` 之外的档位正文逐字保留，差异留给后续标注层。

守恒审计 G10 单独放在文件末尾。传 ``expected_core`` 时，核心覆盖以外部清单
为准，因此删掉一个核心单元既会拉低核心覆盖，也会让它的原文块变成未映射块。
不传时只能按当前模型自查，删掉的单元不可见——这是本函数的已知局限。
"""

from bookcorpus.reading.textnorm import normalize_verbatim, normalize_ws

TIER_ORDER = ("skim", "standard", "deep", "research")

_DISPOSITION_RANK = {"verbatim": 3, "condensed": 2, "indexed": 1, "omitted": 0}


def _is_hard(unit):
    return unit.get("hard_info") is True


def _hard_texts(unit):
    if not _is_hard(unit):
        return []
    note = unit.get("hard_info_note")
    if note:
        return [note]
    return [quote.get("text", "") for quote in unit.get("quotes", []) if quote.get("text")]


def _skim_disposition(unit):
    if _is_hard(unit) or unit.get("importance") == "core":
        return "condensed"
    if unit.get("duplicate_of"):
        return "omitted"
    return "indexed"


def _verbatim_disposition(unit):
    return "verbatim"


# 档位 -> 处置规则。加档位只改这张表，不在主流程里堆 tier 判断。
_TIER_RULES = {
    "skim": _skim_disposition,
    "standard": _verbatim_disposition,
    "deep": _verbatim_disposition,
    "research": _verbatim_disposition,
}


def _disposition(tier, unit):
    return _TIER_RULES[tier](unit)


def _reason(tier, unit, disposition):
    if tier != "skim":
        return "正文逐字保留"
    if disposition == "condensed":
        if _is_hard(unit):
            return "硬信息必须留在正文"
        return "核心判断保留摘要"
    if disposition == "omitted":
        return f"重复表达，见 {unit.get('duplicate_of')}"
    return "细节进入索引"


def _block_map(blocks):
    return {block.get("id"): block for block in blocks or [] if block.get("id")}


def _block_chars(blocks):
    result = {}
    for block in blocks or []:
        block_id = block.get("id")
        if not block_id:
            continue
        text = block.get("text")
        if isinstance(text, str):
            result[block_id] = len(text)
        else:
            result[block_id] = int(block.get("chars") or 0)
    return result


def _derived_blocks(model):
    """没有原始块时，用引文和 non_semantic 拼一份最小块表。"""
    blocks = []
    seen = set()
    for unit in model.get("units", []):
        for quote in unit.get("quotes", []):
            block_id = quote.get("block")
            if block_id and block_id not in seen:
                seen.add(block_id)
                blocks.append({"id": block_id, "text": quote.get("text", "")})
    for entry in model.get("non_semantic", []):
        block_id = entry.get("block")
        if block_id and block_id not in seen:
            seen.add(block_id)
            blocks.append({"id": block_id, "text": ""})
    return blocks


def _block_presentation(block_id, non_semantic, folds, unit_ids, entries):
    if block_id in non_semantic:
        return "non_semantic"
    ranks = [_DISPOSITION_RANK.get(entries[unit_id]["disposition"], -1) for unit_id in unit_ids]
    if ranks:
        best = max(ranks)
        for disposition, rank in _DISPOSITION_RANK.items():
            if rank == best:
                return disposition
    if block_id in folds:
        return "folded"
    return "unmapped"


def plan_dispositions(model, tier, blocks=None):
    """返回单个档位的逐单元处置、逐块呈现与折叠表。

    ``blocks`` 是原始正文块列表；省略时只能根据模型里的引文拼最小块表，
    未映射块会算不出来，调用方应尽量传入。
    """
    if tier not in TIER_ORDER:
        raise ValueError(f"未知档位：{tier}")
    blocks = blocks if blocks is not None else _derived_blocks(model)
    block_chars = _block_chars(blocks)
    units = model.get("units", [])
    non_semantic = {entry["block"] for entry in model.get("non_semantic", [])}
    folds = model.get("folds", [])
    fold_blocks = {fold["block"] for fold in folds}
    referenced = {block_id for unit in units for block_id in unit.get("source_blocks", [])}
    unmapped = sorted(set(block_chars) - referenced - non_semantic - fold_blocks)

    block_units = {}
    entries = {}
    counts = {"units": len(units), "verbatim": 0, "condensed": 0, "indexed": 0, "omitted": 0}
    core_total = 0
    core_covered = 0
    hard_total = 0
    hard_covered = 0
    detail_total = 0
    detail_in_body = 0
    detail_indexed = 0
    omitted_duplicates = 0
    body_chars = 0

    for unit in units:
        unit_id = unit["id"]
        disposition = _disposition(tier, unit)
        counts[disposition] += 1
        entry = {
            "disposition": disposition,
            "quote": (unit.get("quotes") or [{}])[0].get("text", ""),
            "summary": unit.get("summary", ""),
            "reason": _reason(tier, unit, disposition),
        }
        if unit.get("duplicate_of"):
            entry["duplicate_of"] = unit["duplicate_of"]
        entries[unit_id] = entry
        if disposition == "condensed":
            body_chars += len(unit.get("summary", ""))
        if unit.get("importance") == "core":
            core_total += 1
            if disposition in ("verbatim", "condensed"):
                core_covered += 1
        if _is_hard(unit):
            hard_total += 1
            if disposition in ("verbatim", "condensed"):
                hard_covered += 1
        if unit.get("importance") == "detail":
            detail_total += 1
            if disposition in ("verbatim", "condensed"):
                detail_in_body += 1
            elif disposition == "indexed":
                detail_indexed += 1
        if disposition == "omitted" and unit.get("duplicate_of"):
            omitted_duplicates += 1
        for block_id in unit.get("source_blocks", []):
            block_units.setdefault(block_id, []).append(unit_id)

    block_table = {}
    for block_id in block_chars:
        block_table[block_id] = {
            "presentation": _block_presentation(
                block_id,
                non_semantic,
                fold_blocks,
                block_units.get(block_id, []),
                entries,
            ),
            "units": block_units.get(block_id, []),
        }

    fold_entries = []
    for fold in folds:
        block_id = fold.get("block")
        fold_entries.append(
            {
                "block": block_id,
                "summary": fold.get("summary", ""),
                "reason": fold.get("reason", ""),
                "presentation": block_table.get(block_id, {}).get("presentation", "unmapped"),
            }
        )

    source_chars = sum(block_chars.values())
    return {
        "tier": tier,
        "units": entries,
        "blocks": block_table,
        "folds": fold_entries,
        "counts": counts,
        "core_total": core_total,
        "core_covered": core_covered,
        "core_coverage": core_covered / core_total if core_total else 1.0,
        "hard_info_total": hard_total,
        "hard_info_covered": hard_covered,
        "hard_info_coverage": hard_covered / hard_total if hard_total else 1.0,
        "detail_total": detail_total,
        "detail_in_body": detail_in_body,
        "detail_indexed": detail_indexed,
        "omitted_duplicates": omitted_duplicates,
        "unmapped_blocks": len(unmapped),
        "unmapped_block_ids": unmapped,
        "folds_total": len(fold_entries),
        "stats": {
            "source_chars": source_chars,
            "body_chars": body_chars,
            "block_chars": source_chars,
            "non_semantic_blocks": len(non_semantic),
        },
    }


def plan_all_dispositions(model, blocks=None):
    """四个档位一起出账；CLI 与落盘 ledger 用这个入口。"""
    tiers = {tier: plan_dispositions(model, tier, blocks) for tier in TIER_ORDER}
    warnings = []
    for tier in TIER_ORDER:
        count = tiers[tier]["unmapped_blocks"]
        if count:
            warnings.append(f"{tier} 有 {count} 个未映射块")
    return {
        "schema": 1,
        "uid": model.get("uid"),
        "n": model.get("n"),
        "source_sha256": model.get("source_sha256"),
        "prompt_version": model.get("prompt_version"),
        "tiers": tiers,
        "warnings": warnings,
    }


def _verbatim_ok(unit, block_map):
    for quote in unit.get("quotes", []):
        block_id = quote.get("block")
        block = block_map.get(block_id)
        if block is None:
            return False
        if normalize_verbatim(quote.get("text", "")) not in normalize_verbatim(block.get("text", "")):
            return False
    for hard_text in _hard_texts(unit):
        if not any(normalize_ws(hard_text) in normalize_ws(quote.get("text", "")) for quote in unit.get("quotes", [])):
            return False
    return True


def _condensed_ok(unit, entry):
    summary = entry.get("summary", "")
    return all(normalize_ws(text) in normalize_ws(summary) for text in _hard_texts(unit))


def _expected_core_ids(model, expected_core):
    if expected_core is None:
        return None
    return list(dict.fromkeys(expected_core))


def audit_conservation(model, ledger, blocks, *, expected_core=None):
    """守恒审计 G10。

    不传 ``expected_core`` 时，核心总数取当前模型；若模型里已经删掉一个核心
    单元，审计看不见它。CLI ``report --gold`` 或测试应传入外部核心清单，才能
    把「删核心单元」判成核心覆盖不足 + 未映射块。
    """
    problems = []
    model = model or {}
    block_map = _block_map(blocks)
    units = model.get("units", [])
    unit_map = {unit["id"]: unit for unit in units}
    expected = _expected_core_ids(model, expected_core)
    if expected is None:
        core_ids = [unit["id"] for unit in units if unit.get("importance") == "core"]
        core_total = len(core_ids)
        core_covered = sum(
            1
            for unit_id in core_ids
            if any(
                ((ledger.get("tiers") or {}).get(tier) or {}).get("units", {}).get(unit_id, {}).get("disposition")
                in ("verbatim", "condensed")
                for tier in TIER_ORDER
            )
        )
        limitation = "未传 expected_core：核心覆盖只按当前模型自查，删掉的核心单元不可见"
    else:
        core_total = len(expected)
        core_covered = 0
        limitation = ""
        for unit_id in expected:
            unit = unit_map.get(unit_id)
            if unit is None:
                problems.append(f"核心单元 {unit_id} 不在模型里")
                continue
            if unit.get("importance") != "core":
                problems.append(f"期望核心单元 {unit_id} 在模型里不是 core")
            for tier in TIER_ORDER:
                entry = ((ledger.get("tiers") or {}).get(tier) or {}).get("units", {}).get(unit_id)
                if entry is None:
                    problems.append(f"{tier} 缺少核心单元 {unit_id} 的处置")
                elif entry.get("disposition") not in ("verbatim", "condensed"):
                    problems.append(f"{tier} 的核心单元 {unit_id} 未被正文覆盖")
            if all(
                ((ledger.get("tiers") or {}).get(tier) or {}).get("units", {}).get(unit_id, {}).get("disposition")
                in ("verbatim", "condensed")
                for tier in TIER_ORDER
            ):
                core_covered += 1

    for tier in TIER_ORDER:
        account = (ledger.get("tiers") or {}).get(tier)
        if not account:
            problems.append(f"{tier} 缺少处置账")
            continue
        entries = account.get("units") or {}
        if not isinstance(entries, dict):
            problems.append(f"{tier} 的 units 不是对象")
            continue
        extra = sorted(set(entries) - set(unit_map))
        if extra:
            problems.append(f"{tier} 有模型里不存在的单元：" + "、".join(extra[:8]))
        for unit in units:
            unit_id = unit["id"]
            entry = entries.get(unit_id)
            if entry is None:
                problems.append(f"{tier} 缺少单元 {unit_id} 的处置")
                continue
            disposition = entry.get("disposition")
            if disposition not in _DISPOSITION_RANK:
                problems.append(f"{tier} 的单元 {unit_id} 处置非法：{disposition}")
                continue
            if unit.get("importance") == "core" and disposition not in ("verbatim", "condensed"):
                problems.append(f"{tier} 的核心单元 {unit_id} 未进入正文")
            if _is_hard(unit):
                if disposition not in ("verbatim", "condensed"):
                    problems.append(f"{tier} 的硬信息单元 {unit_id} 未进入正文")
                elif disposition == "condensed" and not _condensed_ok(unit, entry):
                    problems.append(f"{tier} 的硬信息单元 {unit_id} 摘要丢了硬事实")
            if disposition == "verbatim" and not _verbatim_ok(unit, block_map):
                problems.append(f"{tier} 的单元 {unit_id} 引文或硬信息不逐字")
            if disposition == "omitted":
                duplicate_of = unit.get("duplicate_of")
                if not duplicate_of:
                    problems.append(f"{tier} 的单元 {unit_id} 不是重复却标了 omitted")
                    continue
                if entry.get("duplicate_of") != duplicate_of:
                    problems.append(f"{tier} 的单元 {unit_id} 省略时未记录 duplicate_of")
                canonical = entries.get(duplicate_of)
                if duplicate_of not in unit_map:
                    problems.append(f"{tier} 的单元 {unit_id} 指向的规范单元 {duplicate_of} 不在模型里")
                elif canonical is None:
                    problems.append(f"{tier} 的单元 {unit_id} 指向的规范单元 {duplicate_of} 不在处置账里")
                elif canonical.get("disposition") == "omitted":
                    problems.append(f"{tier} 的规范单元 {duplicate_of} 也被省略了")

        computed_unmapped = account.get("unmapped_block_ids") or []
        referenced = {block_id for unit in units for block_id in unit.get("source_blocks", [])}
        non_semantic = {entry.get("block") for entry in model.get("non_semantic") or []}
        fold_blocks = {fold.get("block") for fold in model.get("folds") or []}
        actual_unmapped = sorted(set(block_map) - referenced - non_semantic - fold_blocks)
        if computed_unmapped != actual_unmapped:
            problems.append(f"{tier} 的未映射块账不一致：账 {len(computed_unmapped)}，模型 {len(actual_unmapped)}")
        if actual_unmapped:
            problems.append(f"{tier} 有 {len(actual_unmapped)} 个未映射块：" + "、".join(actual_unmapped[:8]))
        elif computed_unmapped:
            problems.append(f"{tier} 有 {len(computed_unmapped)} 个未映射块：" + "、".join(computed_unmapped[:8]))
        account_folds = {fold.get("block") for fold in account.get("folds") or []}
        model_folds = {fold.get("block") for fold in model.get("folds") or []}
        if account_folds != model_folds:
            problems.append(f"{tier} 的折叠块与模型不一致")
        for fold in account.get("folds") or []:
            block_id = fold.get("block")
            if block_id not in block_map:
                problems.append(f"{tier} 的折叠块 {block_id} 不在正文里")
            if block_id in {entry.get("block") for entry in model.get("non_semantic") or []}:
                problems.append(f"{tier} 的折叠块 {block_id} 同时被标为 non_semantic")

    if core_covered < core_total:
        problems.append(f"核心覆盖不足：{core_covered}/{core_total}")
    hard_total = sum(1 for unit in units if _is_hard(unit))
    hard_covered = sum(
        1
        for unit in units
        if _is_hard(unit)
        and all(
            ((ledger.get("tiers") or {}).get(tier) or {}).get("units", {}).get(unit["id"], {}).get("disposition")
            in ("verbatim", "condensed")
            for tier in TIER_ORDER
        )
    )
    if hard_covered < hard_total:
        problems.append(f"硬信息覆盖不足：{hard_covered}/{hard_total}")

    status = "pass" if not problems else "fail"
    detail = f"核心 {core_covered}/{core_total}，硬信息 {hard_covered}/{hard_total}，问题 {len(problems)} 项"
    return {
        "id": "G10",
        "name": "守恒账",
        "status": status,
        "detail": detail,
        "problems": problems,
        "core_total": core_total,
        "core_covered": core_covered,
        "core_coverage": core_covered / core_total if core_total else 1.0,
        "hard_info_total": hard_total,
        "hard_info_covered": hard_covered,
        "hard_info_coverage": hard_covered / hard_total if hard_total else 1.0,
        "limitation": limitation,
    }
