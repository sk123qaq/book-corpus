"""视觉读本门禁：原文逐字、单文件、图注覆盖、结构完整。"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from bookcorpus.reading.edition.content import number_warnings


class _Blocks(HTMLParser):
    """收集每个 data-block 元素内的可见文本，以及图片块是否存在。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.text: dict[str, list[str]] = {}
        self.images: set[str] = set()
        self.h1 = 0
        self.plates: set[str] = set()
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        if tag in ("link", "script") and values.get("src"):
            self.external.append(values["src"] or "")
        for key in ("src", "href"):
            value = values.get(key) or ""
            if value.startswith(("http://", "https://")):
                self.external.append(value)
        block = values.get("data-block")
        if not block and tag == "img" and self.stack:
            block = self.stack[-1][0]
        if block:
            self.stack.append((block, tag))
            self.text.setdefault(block, [])
            if tag == "img":
                self.images.add(block)
        if block and tag == "figure" and "plate" in (values.get("class") or "").split():
            self.plates.add(block)

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1][1] == tag:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        for block, _tag in self.stack:
            self.text[block].append(data)


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value)


def audit_edition(blocks: list[dict], html_text: str, content: dict) -> dict:
    """返回 {errors, warnings, stats}。errors 非空即拒绝入库。"""
    parser = _Blocks()
    parser.feed(html_text)
    errors: list[str] = []
    warnings: list[str] = []

    if parser.h1 != 1:
        errors.append("页面必须有且只有一个 h1（当前 %d 个）" % parser.h1)
    if parser.external:
        errors.append("视觉读本不允许外部资源：%s" % ", ".join(sorted(set(parser.external))[:3]))

    for block in blocks:
        block_id = block["id"]
        kind = block.get("kind")
        if kind == "footnote":
            actual = _norm("".join(parser.text.get(block_id, [])))
            if _norm(block.get("text", "")) not in actual:
                errors.append("脚注 %s 的原文没有逐字出现" % block_id)
            continue
        if kind == "image":
            if block_id not in parser.images:
                errors.append("图片块 %s 没有渲染出 img" % block_id)
            if block_id not in parser.plates:
                errors.append("图片块 %s 没有包在 figure.plate 里" % block_id)
            continue
        actual = _norm("".join(parser.text.get(block_id, [])))
        expected = _norm(block.get("text", ""))
        if expected and expected not in actual:
            errors.append("块 %s 的原文没有逐字渲染" % block_id)

    images = {block["id"] for block in blocks if block.get("kind") == "image"}
    covered = set((content.get("figures") or {}).keys())
    if images - covered:
        warnings.append("有 %d 张原文图没有编辑图注：%s" % (len(images - covered), ", ".join(sorted(images - covered))))
    for block_id in content.get("figures") or {}:
        if block_id not in parser.plates:
            errors.append("图注锚点 %s 没有对应的 figure.plate" % block_id)

    for chart in content.get("charts") or []:
        if not chart.get("title"):
            errors.append("图表 %s 缺标题" % chart.get("id"))
    for section in content.get("sections") or []:
        if section["anchor"] not in parser.text:
            errors.append("分节锚点 %s 没有渲染出正文" % section["anchor"])

    warnings.extend(number_warnings(content, blocks))
    stats = {
        "blocks": len(blocks),
        "images": len(images),
        "plates": len(parser.plates),
        "charts": len(content.get("charts") or []),
        "callouts": len(content.get("callouts") or []),
        "sections": len(content.get("sections") or []),
        "html_chars": len(html_text),
    }
    return {"errors": errors, "warnings": sorted(set(warnings)), "stats": stats}
