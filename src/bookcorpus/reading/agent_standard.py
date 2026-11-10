"""轻量标准档：让模型直接读全文和原图，写回一页 HTML。

这里刻意不引入第二套 JSON 结构。模型拿到完整的块清单和图片，
自己决定保留哪些段落、分几个节拍、要不要图；生成后只做一次
结构/可展示性校验，失败把错误原样退回模型，最多两次。
"""

from __future__ import annotations

import html
import json
import re

from bs4 import BeautifulSoup, NavigableString

from .model import ModelClient
from .render import READER_CSS
from .textnorm import normalize_ws
from .visuals import VISUAL_CSS

MAX_ATTEMPTS = 2
MAX_FRAGMENT_CHARS = 80000
SYSTEM_PROMPT = """你是中文读本的编辑，不是摘要器。给你一章的完整原文块和原文图片，请直接写出一页“标准档”HTML 片段。
只输出 HTML 片段，不要 markdown 围栏，不要解释，不要 <html>/<head>/<body>。

目标：读者用 8–10 分钟读懂这一章，同时保留作者的语气和关键细节。你可以删掉次要段落，但不能改写被保留的原文。

硬规则：
1. 每个保留的原文段落必须写成 <p class="src" data-block="B03">原文逐字</p>。
   原文引文写成 <blockquote class="src" data-block="B05">原文逐字</blockquote>。
   原文小标题写成 <h2 class="sec" data-block="B07">原文标题</h2>。
   不要改字、不要合并两段、不要用省略号改写。
2. 原文图片写成 <figure class="fig" data-block="B13"><figcaption>一句图注</figcaption></figure>，
   不要写 <img>，不要写图片地址。外层会按块 id 注入真实图片。
3. 你可以决定不要图。只有图能解决一个具体的比较、尺度、曲线、流程或账目关系时才画图；
   纯轶事、纯观点不要配装饰图。不要重画原文已有的图片。
4. 生成的图用内联 SVG 或简单 HTML，放在
   <figure class="plate-row" data-kind="curve|scale|ratio|ledger|steps|framing|two_lives">
   里，配 <div class="plate-head"><span class="plate-no">图版 01</span><h3>标题</h3></div>
   和 <p class="plate-cap">一句说明</p>。不要引用外部资源。
5. 节拍最多 5 个，写成
   <section class="beat"><p class="beat-no">01 / 05</p><h3>标题</h3><p class="beat-q">这一节要回答的问题</p></section>
   第一个节拍放在正文之前。节拍是编辑层，不是原文，不要加 data-block。
6. 边注最多 6 条，写成
   <div class="flow-row"><div class="block">...原文...</div>
   <div class="margin"><aside class="note"><span class="tag">概念 · 两种生命</span>一句解释</aside></div></div>。
   一条边注只解释一个概念，不重复原文。
7. 结尾可以有 <section class="takeaways">...</section>，最多三条。没有把握就不写。
8. 不要编造数字、引文、页码。图上的数字必须来自原文；如果原文没有数字，就不要做定量图。

版式：正文 680px，右边 250px 边注。不要写 style 属性，不要写 class 以外的布局。
"""


class FragmentError(ValueError):
    """模型片段未通过结构/原文校验。"""


def _norm(text: str) -> str:
    """兼容旧调用；统一走共享的空白归一化。"""
    return normalize_ws(text)


def _data_url(path: str, mime: str) -> str:
    import base64
    from pathlib import Path

    raw = Path(path).read_bytes()
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def _payload_blocks(blocks):
    """给模型看的完整块清单；图片带标题提示，文字逐字给全。"""
    out = []
    for index, block in enumerate(blocks):
        item = {"id": block["id"], "kind": block["kind"]}
        if block["kind"] == "image":
            hint = ""
            for neighbor in blocks[index + 1 : index + 3]:
                if neighbor["kind"] == "heading" and (neighbor.get("text") or "").strip():
                    hint = neighbor["text"].strip()
                    break
            item.update({"rel": block.get("rel", ""), "hint": hint, "text": ""})
        else:
            item["text"] = block.get("text") or ""
        out.append(item)
    return out


def _source_prompt(book, chapter, blocks):
    return "书名：%s\n章名：%s\n\n完整原文块（JSON）：\n%s\n\n请按系统规则写出这一章的标准档 HTML 片段。" % (
        book.get("title", ""),
        chapter.get("title", ""),
        json.dumps(_payload_blocks(blocks), ensure_ascii=False),
    )


def _image_parts(blocks, image_data):
    parts = []
    for block in blocks:
        if block["kind"] != "image":
            continue
        url = (image_data or {}).get(block["id"])
        if not url:
            continue
        parts.append(
            {
                "type": "text",
                "text": "原文图片 %s，文件 %s。请先看这张图，再决定是否在页面里保留它。"
                % (block["id"], block.get("rel", "")),
            }
        )
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _messages(book, chapter, blocks, image_data):
    content = [{"type": "text", "text": _source_prompt(book, chapter, blocks)}]
    content.extend(_image_parts(blocks, image_data))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _class_list(tag) -> set[str]:
    return set(tag.get("class") or []) if tag else set()


def validate_fragment(fragment: str, blocks) -> list[str]:
    """只做结构、安全和原文一致性检查；不做文笔评分。"""
    problems: list[str] = []
    if not (fragment or "").strip():
        return ["片段为空"]
    if len(fragment) > MAX_FRAGMENT_CHARS:
        return ["片段超过 %d 字符" % MAX_FRAGMENT_CHARS]
    try:
        soup = BeautifulSoup(fragment, "html.parser")
    except Exception as exc:  # BeautifulSoup 很少抛，但保留明确错误
        return ["HTML 无法解析：%s" % exc]

    forbidden = {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "link",
        "meta",
        "form",
        "input",
        "button",
        "img",
    }
    for tag in soup.find_all(True):
        if tag.name in forbidden:
            problems.append("不允许的标签 <%s>" % tag.name)
        for attr in tag.attrs:
            low = attr.lower()
            if low.startswith("on"):
                problems.append("<%s> 不允许事件属性 %s" % (tag.name, attr))
            if low in ("href", "src", "xlink:href"):
                value = str(tag.get(attr) or "").strip().lower()
                if value and not value.startswith("#"):
                    problems.append("<%s> 不允许外部地址 %s" % (tag.name, attr))
            if low == "style":
                problems.append("<%s> 不允许 style 属性" % tag.name)

    by_id = {block["id"]: block for block in blocks}
    order = {block["id"]: index for index, block in enumerate(blocks)}
    seen: set[str] = set()
    last_order = -1
    body_tags = ("p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol")
    for cell in soup.select(".flow-row > .block"):
        for child in cell.contents:
            if isinstance(child, NavigableString):
                if str(child).strip():
                    problems.append("正文里有未标记来源的裸文本")
                continue
            if getattr(child, "name", None) in body_tags and not child.get("data-block"):
                problems.append("正文里有未标记来源的 <%s>" % child.name)

    for tag in soup.select("[data-block]"):
        block_id = str(tag.get("data-block") or "")
        block = by_id.get(block_id)
        if block is None:
            problems.append("data-block=%s 不存在" % block_id)
            continue
        if block_id in seen:
            problems.append("原文块 %s 重复出现" % block_id)
            continue
        seen.add(block_id)
        current_order = order[block_id]
        if current_order < last_order:
            problems.append("原文块顺序被打乱：%s 出现在 %s 之后" % (block_id, blocks[last_order]["id"]))
        last_order = max(last_order, current_order)
        classes = _class_list(tag)
        if block["kind"] == "image":
            if tag.name != "figure" or "fig" not in classes:
                problems.append('图片块 %s 必须是 <figure class="fig" data-block="%s">' % (block_id, block_id))
            continue
        if block["kind"] == "heading":
            if tag.name not in ("h1", "h2", "h3", "h4"):
                problems.append("标题块 %s 必须是标题标签" % block_id)
            continue
        if block["kind"] == "quote":
            if tag.name != "blockquote" or "src" not in classes:
                problems.append('引文块 %s 必须是 <blockquote class="src">' % block_id)
                continue
        elif tag.name != "p" or "src" not in classes:
            problems.append('原文块 %s 必须是 <p class="src">' % block_id)
            continue
        actual = normalize_ws(tag.get_text())
        expected = normalize_ws(block.get("text") or "")
        if actual != expected:
            problems.append(
                "原文块 %s 被改写：期望「%s」，实际「%s」"
                % (block_id, (block.get("text") or "")[:40], tag.get_text()[:40])
            )

    if len(seen) < 4:
        problems.append("保留的原文块太少（%d 块）" % len(seen))

    for figure in soup.select("figure.plate-row"):
        svg = figure.find("svg")
        if svg is not None and not svg.get("viewbox"):
            problems.append("图版 %s 的 SVG 缺少 viewBox" % (figure.get("data-kind") or "?"))

    # 去重错误，避免一次重试塞满同一句话
    return list(dict.fromkeys(problems))


def _inject_images(fragment: str, blocks, image_data) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    by_id = {block["id"]: block for block in blocks}
    for figure in soup.select("figure.fig[data-block]"):
        block = by_id.get(str(figure.get("data-block") or ""))
        if not block or block["kind"] != "image":
            continue
        if figure.find("img") is not None:
            continue
        url = (image_data or {}).get(block["id"])
        if not url:
            continue
        alt = ""
        cap = figure.find("figcaption")
        if cap:
            alt = normalize_ws(cap.get_text())[:80]
        img = soup.new_tag("img")
        img["src"] = url
        img["alt"] = alt
        figcaption = figure.find("figcaption")
        if figcaption is not None:
            figcaption.insert_before(img)
        else:
            figure.insert(0, img)
    return str(soup)


def generate_standard_html(
    client: ModelClient,
    book,
    chapter,
    blocks,
    *,
    model=None,
    effort=None,
    image_data=None,
    resolver=None,
) -> tuple[str, dict]:
    """调用模型生成标准档片段；失败把校验错误退回模型，最多两次。"""
    messages = _messages(book, chapter, blocks, image_data)
    last_error = None
    usage_total = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        content, usage = client.complete(messages, model=model, effort=effort, json_mode=False)
        usage_total = _merge_usage(usage_total, usage)
        fragment = _strip_fences(content)
        problems = validate_fragment(fragment, blocks)
        if not problems:
            fragment = _inject_images(fragment, blocks, image_data)
            return fragment, {
                "attempts": attempt,
                "model": model or client.model,
                "usage": usage_total,
                "problems": [],
            }
        last_error = "；".join(problems[:8])
        if attempt < MAX_ATTEMPTS:
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": "上面的片段没有通过展示校验：%s\n请只重发修正后的完整 HTML 片段，不要解释。"
                    % last_error,
                }
            )
    raise FragmentError("标准档片段两次未通过校验：%s" % last_error)


def _merge_usage(total, usage):
    out = dict(total or {})
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = out.get(key, 0) + value
        else:
            out.setdefault(key, value)
    return out


def render_agent_page(book, chapter, fragment, *, stats=None) -> str:
    stats = stats or {}
    stats_line = "Agent 生成 · 标准档"
    if stats.get("blocks") and stats.get("total_blocks"):
        stats_line += " · 选段 %d/%d" % (stats["blocks"], stats["total_blocks"])
    if stats.get("chars"):
        stats_line += " · %s 字" % format(stats["chars"], ",")
    if stats.get("plates"):
        stats_line += " · 图版 %d" % stats["plates"]
    if stats.get("minutes"):
        stats_line += " · 约 %d 分钟" % stats["minutes"]
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>%s · %s · Agent 标准档</title><style>%s\n%s\n%s</style></head>"
        '<body class="reader has-editorial"><main class="read"><header class="read-head">'
        '<p class="eyebrow">%s · %s · Agent 标准档</p><h1>%s</h1>'
        '<div class="read-stats">%s</div></header>%s'
        '<footer class="read-foot"><span>%s</span><span>模型生成，结构校验后展示</span></footer>'
        "</main></body></html>"
        % (
            html.escape(book.get("title_short") or book.get("title") or ""),
            html.escape(chapter.get("title") or ""),
            READER_CSS,
            VISUAL_CSS,
            _AGENT_CSS,
            html.escape(book.get("title_short") or book.get("title") or ""),
            html.escape(chapter.get("title") or ""),
            html.escape(chapter.get("title") or ""),
            html.escape(stats_line),
            fragment,
            html.escape(book.get("title") or ""),
        )
    )


_AGENT_CSS = """
.agent-badge{display:inline-block;font-family:var(--sans);font-size:9px;letter-spacing:.16em;
  color:var(--cinnabar);border:1px solid var(--cinnabar);border-radius:2px;padding:1px 5px;margin-left:8px}
body.has-editorial .read .flow-row .block > .src{margin-bottom:1.05em}
body.has-editorial .read .flow-row .block > .src:last-child{margin-bottom:0}
.read .plate-row{max-width:948px}
@media(max-width:820px){
  .read .flow-row{grid-template-columns:minmax(0,1fr)}
  .read .plate-row{padding:20px 16px}
}
"""
