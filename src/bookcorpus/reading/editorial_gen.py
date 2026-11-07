"""从章节内容生成编辑层：节拍、图版、边注标签与收束。

模型只输出结构化 JSON，真正的版式仍由 :mod:`bookcorpus.reading.visuals`
渲染。图上的每个数字都必须能在 ``claims`` 引用的原文里找到；校验失败会把
错误回传给模型重试一次，仍失败则抛错，不发布半成品。
"""

from __future__ import annotations

import json

from .editorial import EditorialError, validate_editorial
from .model import ModelClient

PROMPT_VERSION = "editorial-v1"

SYSTEM_PROMPT = """你是中文书稿的图表编辑。只输出一个 JSON 对象，不要 markdown 围栏，不要解释。
任务：读一章的块和已有的角色标注，设计 3–5 个节拍、2–4 个图版、2–3 条收束，
让读者在长文中有路标、在抽象处看见结构。宁少勿多；没有合适的图就少给。

硬规则：
- beats：按原文顺序，每个 before_block 必须是已有块 id；title ≤ 24 字，question ≤ 48 字。
- visuals：每个 after_block 必须是已有块 id；tiers 用 ["standard","deep","research"]。
  claims 至少一条，quote 必须逐字来自该 block 的 text，不能改写、拼接或用省略号。
- 图上的数字、倍数、金额必须来自 claims 引用的原文；display 按原文单位。
  如果原文说“50 万美元”，display 就写“$500,000”或“50 万美元”，不要混用。
- scale 的 value 必须换算到同一单位，display 保留原文单位。例：30 英尺与 2 英寸，
  value 用 360 和 2（都按英寸），display 仍是“约30英尺”“约2英寸”。
- 不要给已经有 source image 的概念再画一张同义图；先看 image 块的 hint。
- 不要为每一段都配图。优先选择：数量级反差、同一物的两种价格、因果链、取舍账本、
  同一金额的两种说法、读者会猜错的点、一条概念曲线的形状。
- 如果一章出现同一现象的两个数字且单位或量级不同（如 1 英寸与 29.7 英尺），
  必须做一张 scale；除非 source image 已经直接呈现了这个比值。
- prediction 必须放在揭晓答案的块之前：用 "placement":"before"，
  after_block 指向提出问题的块。如果 source image 的 hint 已经提出同一个问题，
  不要重复做 prediction；改做回答该问题的 scale 或 curve。
- ledger 只用于同一决策的现金成本与机会成本并列；不要用它表示普通选择题的四个选项。
- note_tags 只给真正需要解释的块，0–6 条，每条 ≤ 12 字，例如“概念 · 机会成本”。
- takeaways 2–3 条，每条 sources 至少一个块 id；不要写口号。

可用 kind 与 data：
1) scale：2–4 条量级对比。
   {"bars":[{"label":"铁轨间的缝隙","display":"1 英寸","value":1,"tone":"muted"},
            {"label":"中间隆起","display":"29.7 英尺","value":29.7,"tone":"accent"}],
    "factor":"×356","factor_note":"横向 1 英寸，纵向 29.7 英尺","note":"..."}
2) ratio：两条同单位数值的对比，data 形如
   {"left":{"label":"愿意支付","sub":"WTP","display":"$2,000","value":2000},
    "right":{"label":"要求补偿","sub":"WTA","display":"$500,000","value":500000},
    "ratio_label":"×250","ratio_note":"..."}
3) curve：一条概念曲线，data 形如
   {"x_label":"财富","y_label":"效用",
    "series":[{"label":"财富效用","shape":"concave",
               "annotations":[{"text":"增量相同，效用更小","x":0.72,"y":0.55}]}],
    "note":"..."}
   shape 只能是 concave/convex/s_curve/loss_aversion/linear；annotation 的 x/y 取 0–1。
4) ledger：取舍账本，rows 每行有 label/cash/opportunity/choice，chosen 为 true 的行高亮。
5) steps：1–4 步因果链，steps 每步有 label/detail，result 有 label/detail。
6) framing：同一金额两种说法，base/credit/cash/delta 为数字。
7) prediction：读前猜一猜，question/options/reveal；不要泄露答案。
   必须用 "placement":"before"，after_block 指向问题所在块。
8) two_lives：只用于“可识别的生命 vs 统计意义上的生命”，statistical/identified 为整数。

版式示例（只示意字段，不要照抄内容）：
{"id":"rail-scale","kind":"scale","title":"一英寸的缝，二十九英尺的隆起",
 "after_block":"B12","placement":"after","tiers":["standard","deep","research"],
 "data":{"bars":[{"label":"缝隙","display":"1 英寸","value":1,"tone":"muted"},
                 {"label":"隆起","display":"29.7 英尺","value":29.7,"tone":"accent"}],
         "factor":"×356","note":"同一条铁轨，横向与纵向的尺度差。"},
 "caption":"...", "claims":[{"block":"B12","quote":"..."}]}

顶层 JSON：
{"schema":1,"uid":"...","n":1,
 "takeaways_title":"...","takeaways_note":"...",
 "beats":[{"id":"beat-1","title":"...","question":"...","before_block":"B01",
          "tiers":["standard","deep","research"]}],
 "visuals":[...],
 "takeaways":[{"text":"...","sources":["B01"]}],
 "note_tags":{"B01":"概念 · ..."}}
"""


def _payload(book, chapter, blocks, annotation, allowed_ids=None) -> str:
    role_by_id = {item["id"]: item for item in annotation.get("blocks", [])}
    cards = annotation.get("cards", [])
    items = []
    for index, block in enumerate(blocks):
        text = (block.get("text") or "").strip()
        if not text and block.get("kind") != "image":
            continue
        if allowed_ids and block["id"] not in allowed_ids:
            continue
        item = {
            "id": block["id"],
            "kind": block["kind"],
            "text": text,
            "role": role_by_id.get(block["id"], {}).get("role", ""),
            "spine": bool(role_by_id.get(block["id"], {}).get("spine")),
            "priority": role_by_id.get(block["id"], {}).get("priority", 3),
        }
        if block.get("kind") == "image":
            item["rel"] = block.get("rel", "")
            item["hint"] = next(
                (
                    neighbor.get("text", "")
                    for neighbor in blocks[index + 1 : index + 3]
                    if neighbor.get("kind") == "heading"
                ),
                "",
            )
        items.append(item)
    return json.dumps(
        {
            "book": book.get("title", ""),
            "chapter": chapter.get("title", ""),
            "blocks": items,
            "cards": [
                {"trigger": card.get("trigger"), "text": card.get("text", "")}
                for card in cards
                if not allowed_ids or card.get("trigger") in allowed_ids
            ],
        },
        ensure_ascii=False,
    )


def generate_editorial(
    client: ModelClient,
    book: dict,
    chapter: dict,
    blocks: list[dict],
    annotation: dict,
    *,
    model: str | None = None,
    effort: str | None = None,
    allowed_ids: set[str] | None = None,
) -> tuple[dict, dict]:
    """调用模型生成并校验编辑层；失败重试一次。"""
    if not client.available():
        raise RuntimeError("模型未配置：缺少 opencode-go key")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _payload(book, chapter, blocks, annotation, allowed_ids)},
    ]
    requested = (model or client.model).strip()
    chosen_effort = (effort or client.effort).strip().lower()
    usage_total: dict = {}
    last_error: Exception | None = None
    for _attempt in range(2):
        content, usage = client.complete_json(messages, model=requested, effort=chosen_effort)
        usage_total = _merge_usage(usage_total, usage)
        try:
            data = content if isinstance(content, dict) else json.loads(content)
            data["uid"] = book.get("uid")
            data["n"] = int(chapter.get("n"))
            clean = validate_editorial(data, blocks, uid=book.get("uid"), n=chapter.get("n"))
            for visual in clean["visuals"]:
                if visual["kind"] == "scale":
                    _normalize_scale(visual["data"])
            if allowed_ids:
                for beat in clean["beats"]:
                    if beat["before_block"] not in allowed_ids:
                        raise EditorialError("节拍 %s 的锚点 %s 不在可用块里" % (beat["id"], beat["before_block"]))
                for visual in clean["visuals"]:
                    if visual["after_block"] not in allowed_ids:
                        raise EditorialError("图版 %s 的锚点 %s 不在可用块里" % (visual["id"], visual["after_block"]))
            return clean, {"model": requested, "effort": chosen_effort, "usage": usage_total}
        except (EditorialError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": json.dumps(content, ensure_ascii=False)})
            messages.append(
                {
                    "role": "user",
                    "content": "上面的 JSON 未通过校验：%s。只重发修正后的完整 JSON，不要解释。" % exc,
                }
            )
    raise ValueError("图表编辑层解析失败：%s" % last_error)


_LENGTH_UNITS = {
    "英寸": 1.0,
    "寸": 1.0,
    "in": 1.0,
    "英尺": 12.0,
    "尺": 12.0,
    "ft": 12.0,
    "厘米": 0.393701,
    "cm": 0.393701,
    "米": 39.3701,
    "m": 39.3701,
    "毫米": 0.0393701,
    "mm": 0.0393701,
}


def _parse_length(display: str) -> float | None:
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*(毫米|厘米|米|英寸|英尺|寸|尺|mm|cm|m|in|ft)", display, re.I)
    if not match:
        return None
    unit = match.group(2).lower()
    return float(match.group(1)) * _LENGTH_UNITS.get(unit, 1.0)


def _normalize_scale(data: dict) -> None:
    bars = data.get("bars") or []
    values = [_parse_length(bar.get("display", "")) for bar in bars]
    if len(values) < 2 or any(value is None for value in values):
        return
    low, high = min(values), max(values)
    if low <= 0:
        return
    for bar, value in zip(bars, values, strict=False):
        bar["value"] = round(value, 6)
    if not data.get("factor") or "×" not in str(data.get("factor")):
        data["factor"] = "×%g" % round(high / low, 6)
        data["factor_note"] = "按同一单位换算后比较。"


def _merge_usage(left: dict, right: dict) -> dict:
    out = dict(left)
    for key, value in (right or {}).items():
        if isinstance(value, (int, float)) and isinstance(out.get(key), (int, float)):
            out[key] += value
        else:
            out[key] = value
    return out
