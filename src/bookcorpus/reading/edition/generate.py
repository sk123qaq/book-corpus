"""用模型生成视觉读本的编辑层 JSON。"""

from __future__ import annotations

import hashlib
import json

from bookcorpus.reading.edition.content import ContentError, number_warnings, validate_content

PROMPT_VERSION = "edition-xerox-v1"
SYSTEM_PROMPT = """你是书籍视觉版的编辑。你输出一份 JSON 编辑层，不是 HTML。

硬规则：
1. 原文由程序逐字插入。你不能改写、缩写、复述原文，只能给出锚点并添加编辑内容。
2. 只输出 JSON。不要输出 HTML、CSS、SVG、Markdown 或解释。
3. 所有数字必须能在原文中找到。图表数据只能抄原文数字，禁止估算、换算或编造。
4. anchor 必须是给定的块 id，例如 B07。sections 必须按原文顺序排列。
5. 没有足够结构时 sections 可以是空数组；不要为了凑数硬分节。
6. 语气克制，不要写“本章告诉我们”“作者指出”“综上所述”。
7. 图注只描述图里确实可见的内容；看不清就留空，不要猜。

JSON 结构：
{
  "schema": 1,
  "template": "xerox",
  "hero": {"kicker": "<=30字", "title_lines": ["<=32字", "..."], "lead": "<=160字", "foot_left": "<=60字"},
  "overview": [{"no": "01", "title": "<=14字", "text": "<=90字"}],   // 0-4 条
  "sections": [{"anchor": "B07", "title": "<=56字，不要写 1./2. 编号", "intro": "<=140字"}], // 0-10 节
  "figures": {"B13": {"title": "<=48字", "caption": "<=240字", "note": "<=140字"}},
  "charts": [
    {"anchor": "B12", "after": true, "kind": "bars|ratio|compare|steps",
     "title": "<=56字", "caption": "<=220字", "note": "<=140字",
     "data": {"labels": ["..."], "values": [1, 2], "unit": "", "highlight": -1}}
  ],
  "callouts": [{"anchor": "B09", "after": true, "kicker": "<=16字", "title": "<=36字", "text": "<=180字"}],
  "takeaways": {"anchor": "B30", "title": "<=36字", "items": [{"title": "<=22字", "text": "<=110字"}]}
}

图表 data 按 kind 取：
- bars: {"labels": ["标签", "..."], "values": [数字, "..."], "unit": "单位", "highlight": 高亮下标}
- ratio: {"left_label": "", "left_value": 数字, "right_label": "", "right_value": 数字, "note": ""}
- compare: {"headers": ["列1", "列2"], "rows": [["...", "..."]]}
- steps: {"steps": [{"title": "<=24字", "text": "<=90字"}]}

只加真正有帮助的内容：1-3 个图表、1-3 条旁批、3-4 条读后要点即可。没有数字材料就不要 charts。"""


def _block_lines(blocks: list[dict]) -> str:
    lines = []
    for index, block in enumerate(blocks):
        if block.get("kind") == "footnote":
            continue
        kind = block.get("kind", "paragraph")
        text = block.get("text", "").replace("\n", " ")
        if kind == "image":
            context = ""
            for nxt in blocks[index + 1 : index + 3]:
                if nxt.get("kind") in ("paragraph", "heading"):
                    context = nxt.get("text", "")[:60]
                    break
            lines.append("%s [image] %s | 下文：%s" % (block["id"], block.get("rel", ""), context))
            continue
        lines.append("%s [%s] %s" % (block["id"], kind, text))
    return "\n".join(lines)


def _messages(book: dict, chapter: dict, blocks: list[dict]) -> list[dict]:
    payload = {
        "book": book.get("title", ""),
        "author": book.get("author", ""),
        "chapter": chapter.get("title", ""),
        "han": chapter.get("body_han", 0),
        "blocks": _block_lines(blocks),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "为下面这一章生成视觉读本的编辑层 JSON。\n\n" + json.dumps(payload, ensure_ascii=False),
        },
    ]


def _merge_usage(a: dict, b: dict) -> dict:
    out = dict(a)
    for key, value in (b or {}).items():
        if isinstance(value, (int, float)) and isinstance(out.get(key), (int, float)):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


def generate_content(
    client,
    book: dict,
    chapter: dict,
    blocks: list[dict],
    *,
    model: str | None = None,
    effort: str = "low",
) -> tuple[dict, dict, list[str]]:
    """调用模型生成编辑层；最多三轮，后两轮只回喂校验错误。"""
    messages = _messages(book, chapter, blocks)
    usage: dict = {}
    last_error: Exception | None = None
    for _attempt in range(3):
        payload, one_usage = client.complete_json(messages, model=model, effort=effort)
        usage = _merge_usage(usage, one_usage)
        try:
            content = validate_content(payload, blocks)
            content["source_sha256"] = hashlib.sha256(
                "\n".join(block.get("text", "") for block in blocks).encode("utf-8")
            ).hexdigest()
            content["prompt_version"] = PROMPT_VERSION
            content["model"] = model or getattr(client, "model", "")
            return content, usage, number_warnings(content, blocks)
        except ContentError as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)})
            messages.append(
                {
                    "role": "user",
                    "content": "上面的 JSON 未通过校验：%s。只重发修正后的完整 JSON，不要解释。" % exc,
                }
            )
    raise ContentError("视觉读本编辑层生成失败：%s" % last_error)
